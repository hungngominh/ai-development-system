"""M4.1 Decision Inventory tests.

Covers: happy path, count bounds, duplicate ids, missing fields,
invalid classification, JSON parse failure, retry success, retry
exhaustion, domain alias resolve + unknown warn, blocks_what scope_in
warn-only, prompt rendering helpers.
"""

import json

import pytest

from ai_dev_system.debate.questions import inventory
from ai_dev_system.debate.questions.inventory import (
    InventoryCountError,
    InventoryError,
)


# ---- helpers ----


class FakeLLM:
    """LLM stub that serves canned responses in order."""

    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        if not self._responses:
            raise AssertionError("FakeLLM exhausted its canned responses")
        return self._responses.pop(0)


def _make_decision(id_: str, **overrides) -> dict:
    base = {
        "id": id_,
        "summary": f"Decide about {id_}",
        "classification": "REQUIRED",
        "domain_hints": ["backend"],
        "blocks_what": ["voting"],
        "has_safe_default": False,
        "brief_field_refs": ["scope_in"],
    }
    base.update(overrides)
    return base


def _make_response(n: int, **id_overrides) -> str:
    """JSON-encoded list of n minimally-valid decisions, ids d0..d{n-1}."""
    items = [_make_decision(f"d{i}") for i in range(n)]
    for idx, override in id_overrides.items():
        items[idx].update(override)
    return json.dumps(items)


_BRIEF = {
    "brief_version": 2,
    "scope_in": ["voting", "auth", "feed"],
    "scope_out": ["analytics"],
}


# ---- prompt helpers ----


def test_load_prompt_returns_template_text():
    text = inventory.load_prompt()
    assert "SYSTEM" in text
    assert "USER" in text
    assert "{brief_v2_json}" in text


def test_split_prompt_separates_system_and_user():
    system, user_template = inventory._split_prompt(inventory.load_prompt())
    assert system.startswith("You are a senior")
    assert "{brief_v2_json}" in user_template
    assert "SYSTEM" not in system


def test_split_prompt_rejects_missing_user_section():
    with pytest.raises(ValueError, match="missing USER"):
        inventory._split_prompt("SYSTEM\nonly the system block")


# ---- happy path ----


def test_run_returns_decisions_on_first_try():
    llm = FakeLLM([_make_response(8)])
    decisions = inventory.run(_BRIEF, llm)
    assert len(decisions) == 8
    assert [d.id for d in decisions] == [f"d{i}" for i in range(8)]
    assert all(d.classification == "REQUIRED" for d in decisions)
    assert llm.calls and len(llm.calls) == 1
    # brief JSON must reach the user prompt
    _, user_sent = llm.calls[0]
    assert "voting" in user_sent


def test_run_handles_max_count_boundary():
    llm = FakeLLM([_make_response(25)])
    decisions = inventory.run(_BRIEF, llm)
    assert len(decisions) == 25


# ---- count violations ----


def test_run_raises_count_error_when_too_few_after_retry():
    bad = _make_response(3)
    llm = FakeLLM([bad, bad])
    with pytest.raises(InventoryCountError, match=r"returned 3"):
        inventory.run(_BRIEF, llm)
    assert len(llm.calls) == 2


def test_run_raises_count_error_when_too_many_after_retry():
    bad = _make_response(30)
    llm = FakeLLM([bad, bad])
    with pytest.raises(InventoryCountError, match=r"returned 30"):
        inventory.run(_BRIEF, llm)


# ---- structural failures ----


def test_run_raises_on_non_array_response():
    llm = FakeLLM(['{"not": "an array"}', '{"still": "not"}'])
    with pytest.raises(InventoryError, match="JSON array"):
        inventory.run(_BRIEF, llm)


def test_run_raises_on_invalid_json():
    llm = FakeLLM(["not valid json {", "still bad"])
    with pytest.raises(InventoryError, match="not valid JSON"):
        inventory.run(_BRIEF, llm)


def test_run_raises_on_duplicate_ids():
    items = [_make_decision(f"d{i}") for i in range(8)]
    items[3]["id"] = "d0"  # duplicate
    bad = json.dumps(items)
    llm = FakeLLM([bad, bad])
    with pytest.raises(InventoryError, match="more than once"):
        inventory.run(_BRIEF, llm)


def test_run_raises_on_missing_required_field():
    items = [_make_decision(f"d{i}") for i in range(8)]
    del items[2]["summary"]
    bad = json.dumps(items)
    llm = FakeLLM([bad, bad])
    with pytest.raises(InventoryError, match="missing required field"):
        inventory.run(_BRIEF, llm)


def test_run_raises_on_invalid_classification():
    items = [_make_decision(f"d{i}") for i in range(8)]
    items[1]["classification"] = "MAYBE"
    bad = json.dumps(items)
    llm = FakeLLM([bad, bad])
    with pytest.raises(InventoryError, match="invalid classification"):
        inventory.run(_BRIEF, llm)


# ---- retry ----


def test_run_recovers_on_retry_after_first_failure():
    llm = FakeLLM(["not json at all", _make_response(8)])
    decisions = inventory.run(_BRIEF, llm)
    assert len(decisions) == 8
    assert len(llm.calls) == 2
    # The retry user prompt must contain the failure feedback hint
    _, second_user = llm.calls[1]
    assert "PREVIOUS ATTEMPT FAILED" in second_user


# ---- domain normalisation ----


def test_run_resolves_domain_aliases():
    items = [_make_decision(f"d{i}") for i in range(8)]
    items[0]["domain_hints"] = ["react", "k8s"]  # both aliases
    response = json.dumps(items)
    llm = FakeLLM([response])
    decisions = inventory.run(_BRIEF, llm)
    assert decisions[0].domain_hints == ["frontend", "infra"]


def test_run_warns_on_unknown_domain():
    items = [_make_decision(f"d{i}") for i in range(8)]
    items[0]["domain_hints"] = ["blockchain"]  # unknown
    response = json.dumps(items)
    llm = FakeLLM([response])
    with pytest.warns(UserWarning, match="DOMAIN_UNRECOGNIZED"):
        decisions = inventory.run(_BRIEF, llm)
    assert decisions[0].domain_hints == ["backend"]


def test_run_dedupes_resolved_hints():
    items = [_make_decision(f"d{i}") for i in range(8)]
    items[0]["domain_hints"] = ["api", "server", "backend"]  # all → backend
    response = json.dumps(items)
    llm = FakeLLM([response])
    decisions = inventory.run(_BRIEF, llm)
    assert decisions[0].domain_hints == ["backend"]


# ---- blocks_what scope check ----


def test_run_warns_when_blocks_what_not_in_scope_in():
    items = [_make_decision(f"d{i}") for i in range(8)]
    items[0]["blocks_what"] = ["nonexistent_feature"]
    response = json.dumps(items)
    llm = FakeLLM([response])
    with pytest.warns(UserWarning, match="not in brief.scope_in"):
        inventory.run(_BRIEF, llm)


def test_run_skips_scope_check_when_brief_has_no_scope_in():
    items = [_make_decision(f"d{i}") for i in range(8)]
    items[0]["blocks_what"] = ["anything_goes"]
    response = json.dumps(items)
    llm = FakeLLM([response])
    brief_no_scope = {"brief_version": 2}
    # no warning expected on blocks_what (other warnings irrelevant)
    decisions = inventory.run(brief_no_scope, llm)
    assert decisions[0].blocks_what == ["anything_goes"]


# ---- brief_field_refs preserved ----


def test_run_preserves_brief_field_refs():
    items = [_make_decision(f"d{i}") for i in range(8)]
    items[2]["brief_field_refs"] = ["scope_in", "nfr_priority", "constraints"]
    response = json.dumps(items)
    llm = FakeLLM([response])
    decisions = inventory.run(_BRIEF, llm)
    assert decisions[2].brief_field_refs == [
        "scope_in",
        "nfr_priority",
        "constraints",
    ]


def test_run_defaults_brief_field_refs_to_empty_list_when_missing():
    items = [_make_decision(f"d{i}") for i in range(8)]
    items[0].pop("brief_field_refs")
    response = json.dumps(items)
    llm = FakeLLM([response])
    decisions = inventory.run(_BRIEF, llm)
    assert decisions[0].brief_field_refs == []
