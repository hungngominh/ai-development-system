"""M5.C moderator parser + retry orchestrator tests.

Covers: extract_json_block (pure / fenced / prose / unrecoverable),
parse_moderator_response (4 ParseFailReason cases + happy path +
boundary confidences), run_moderator (happy path, retry success,
exhausted retries → MODERATOR_PARSE_FAILED, error-feedback prompt
content, agent_a/b/round_number pass-through, max_retries guard).
"""

import json

import pytest

from ai_dev_system.debate.moderator import (
    MAX_MODERATOR_RETRIES,
    REQUIRED_FIELDS,
    ParseFailReason,
    extract_json_block,
    parse_moderator_response,
    run_moderator,
)


# ---- helpers ----


class FakeLLM:
    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        if not self._responses:
            raise AssertionError("FakeLLM exhausted")
        return self._responses.pop(0)


def _valid_verdict(
    status: str = "RESOLVED",
    confidence: float = 0.9,
    summary: str = "Both agents agree.",
    caveat: str | None = None,
) -> str:
    payload = {"status": status, "confidence": confidence, "summary": summary}
    if caveat is not None:
        payload["caveat"] = caveat
    return json.dumps(payload)


# ---- extract_json_block ----


def test_extract_pure_json_object():
    assert extract_json_block('{"a": 1}') == {"a": 1}


def test_extract_fenced_json_block():
    text = "Sure! Here you go:\n```json\n{\"status\": \"RESOLVED\"}\n```"
    assert extract_json_block(text) == {"status": "RESOLVED"}


def test_extract_unfenced_code_block():
    text = "Result:\n```\n{\"a\": 1}\n```"
    assert extract_json_block(text) == {"a": 1}


def test_extract_json_from_prose():
    text = "Sure, here's my verdict: {\"status\": \"RESOLVED\", \"x\": 2}. Hope that helps!"
    assert extract_json_block(text) == {"status": "RESOLVED", "x": 2}


def test_extract_skips_arrays_returns_first_object():
    text = "[1, 2] {\"a\": 1}"
    assert extract_json_block(text) == {"a": 1}


def test_extract_returns_none_when_no_json():
    assert extract_json_block("no json here at all") is None


def test_extract_returns_none_on_truncated_json():
    # `{...` with no closing brace anywhere
    assert extract_json_block('reply: {"status":') is None


def test_extract_handles_nested_braces_in_strings():
    text = '{"summary": "uses {curly} braces", "status": "RESOLVED"}'
    assert extract_json_block(text) == {
        "summary": "uses {curly} braces",
        "status": "RESOLVED",
    }


# ---- parse_moderator_response: happy ----


def test_parse_valid_response():
    data, reason = parse_moderator_response(_valid_verdict())
    assert reason is None
    assert data["status"] == "RESOLVED"
    assert data["confidence"] == 0.9
    assert data["summary"] == "Both agents agree."


def test_parse_with_caveat_field():
    raw = _valid_verdict(status="RESOLVED_WITH_CAVEAT", caveat="rate-limit needed")
    data, reason = parse_moderator_response(raw)
    assert reason is None
    assert data["caveat"] == "rate-limit needed"


def test_parse_accepts_int_confidence():
    raw = json.dumps({"status": "RESOLVED", "confidence": 1, "summary": "ok"})
    data, reason = parse_moderator_response(raw)
    assert reason is None
    assert data["confidence"] == 1.0
    assert isinstance(data["confidence"], float)


def test_parse_accepts_confidence_boundary_zero():
    raw = json.dumps({"status": "NEED_MORE_EVIDENCE", "confidence": 0, "summary": "."})
    data, reason = parse_moderator_response(raw)
    assert reason is None


def test_parse_extracts_from_fenced_response():
    text = "Verdict:\n```json\n" + _valid_verdict() + "\n```"
    data, reason = parse_moderator_response(text)
    assert reason is None
    assert data["status"] == "RESOLVED"


# ---- parse_moderator_response: 4 fail reasons ----


def test_parse_fail_json_invalid_on_garbage():
    data, reason = parse_moderator_response("totally not json")
    assert data is None
    assert reason is ParseFailReason.JSON_INVALID


def test_parse_fail_json_invalid_on_empty_string():
    data, reason = parse_moderator_response("")
    assert data is None
    assert reason is ParseFailReason.JSON_INVALID


def test_parse_fail_json_invalid_on_non_object():
    data, reason = parse_moderator_response("[1, 2, 3]")
    assert data is None
    assert reason is ParseFailReason.JSON_INVALID


def test_parse_fail_missing_fields():
    raw = json.dumps({"status": "RESOLVED", "confidence": 0.9})  # no summary
    data, reason = parse_moderator_response(raw)
    assert data is None
    assert reason is ParseFailReason.MISSING_FIELDS


def test_parse_fail_invalid_status():
    raw = json.dumps({"status": "MAYBE_OK", "confidence": 0.9, "summary": "."})
    data, reason = parse_moderator_response(raw)
    assert data is None
    assert reason is ParseFailReason.INVALID_STATUS


def test_parse_rejects_moderator_emitting_parse_failed_status():
    # MODERATOR_PARSE_FAILED is reserved for the orchestrator only.
    # If the moderator itself returns it, treat as invalid.
    raw = json.dumps({
        "status": "MODERATOR_PARSE_FAILED",
        "confidence": 0.0,
        "summary": ".",
    })
    data, reason = parse_moderator_response(raw)
    assert data is None
    assert reason is ParseFailReason.INVALID_STATUS


def test_parse_fail_invalid_confidence_out_of_range_high():
    raw = json.dumps({"status": "RESOLVED", "confidence": 1.5, "summary": "."})
    data, reason = parse_moderator_response(raw)
    assert reason is ParseFailReason.INVALID_CONFIDENCE


def test_parse_fail_invalid_confidence_out_of_range_low():
    raw = json.dumps({"status": "RESOLVED", "confidence": -0.1, "summary": "."})
    data, reason = parse_moderator_response(raw)
    assert reason is ParseFailReason.INVALID_CONFIDENCE


def test_parse_fail_invalid_confidence_non_numeric():
    raw = json.dumps({"status": "RESOLVED", "confidence": "high", "summary": "."})
    data, reason = parse_moderator_response(raw)
    assert reason is ParseFailReason.INVALID_CONFIDENCE


# ---- run_moderator: happy + retry ----


def test_run_moderator_happy_path_one_call():
    llm = FakeLLM([_valid_verdict()])
    result = run_moderator(
        llm,
        system_prompt="sys",
        user_context="ctx",
        round_number=1,
        agent_a_position="A says foo",
        agent_b_position="B says bar",
    )
    assert result.resolution_status == "RESOLVED"
    assert result.confidence == 0.9
    assert result.moderator_summary == "Both agents agree."
    assert result.round_number == 1
    assert result.agent_a_position == "A says foo"
    assert result.agent_b_position == "B says bar"
    assert len(llm.calls) == 1


def test_run_moderator_recovers_on_retry():
    llm = FakeLLM(["totally bad", _valid_verdict()])
    result = run_moderator(
        llm,
        system_prompt="sys",
        user_context="ctx",
        round_number=2,
        agent_a_position="a",
        agent_b_position="b",
    )
    assert result.resolution_status == "RESOLVED"
    assert len(llm.calls) == 2
    _, retry_user = llm.calls[1]
    assert "Previous response failed parsing: json_invalid" in retry_user
    assert "STRICT JSON" in retry_user
    # required field hint must appear
    for field in REQUIRED_FIELDS:
        assert field in retry_user


def test_run_moderator_exhausted_returns_parse_failed_status():
    llm = FakeLLM(["bad", "still bad"])
    result = run_moderator(
        llm,
        system_prompt="sys",
        user_context="ctx",
        round_number=3,
        agent_a_position="a",
        agent_b_position="b",
    )
    assert result.resolution_status == "MODERATOR_PARSE_FAILED"
    assert result.confidence == 0.0
    assert "still bad" in result.moderator_summary
    assert "json_invalid" in result.caveat
    assert str(MAX_MODERATOR_RETRIES) in result.caveat


def test_run_moderator_preserves_raw_truncated_to_500():
    long_raw = "x" * 1000
    llm = FakeLLM([long_raw, long_raw])
    result = run_moderator(
        llm,
        system_prompt="sys",
        user_context="ctx",
        round_number=1,
        agent_a_position="a",
        agent_b_position="b",
    )
    assert len(result.moderator_summary) == 500


def test_run_moderator_carries_specific_fail_reason_in_caveat():
    # retry 1 sends missing-fields, retry 2 sends invalid-status
    missing = json.dumps({"status": "RESOLVED", "confidence": 0.5})
    bad_status = json.dumps({"status": "MAYBE", "confidence": 0.5, "summary": "."})
    llm = FakeLLM([missing, bad_status])
    result = run_moderator(
        llm,
        system_prompt="sys",
        user_context="ctx",
        round_number=1,
        agent_a_position="a",
        agent_b_position="b",
    )
    assert result.resolution_status == "MODERATOR_PARSE_FAILED"
    # caveat reflects the LAST reason
    assert "invalid_status" in result.caveat


def test_run_moderator_uses_fenced_response_without_retry():
    fenced = "Here:\n```json\n" + _valid_verdict() + "\n```"
    llm = FakeLLM([fenced])
    result = run_moderator(
        llm,
        system_prompt="sys",
        user_context="ctx",
        round_number=1,
        agent_a_position="a",
        agent_b_position="b",
    )
    assert result.resolution_status == "RESOLVED"
    assert len(llm.calls) == 1


def test_run_moderator_rejects_max_retries_zero():
    llm = FakeLLM([])
    with pytest.raises(ValueError, match="max_retries must be >= 1"):
        run_moderator(
            llm,
            system_prompt="sys",
            user_context="ctx",
            round_number=1,
            agent_a_position="a",
            agent_b_position="b",
            max_retries=0,
        )


def test_run_moderator_custom_max_retries():
    llm = FakeLLM(["bad", "bad", "bad", _valid_verdict()])
    result = run_moderator(
        llm,
        system_prompt="sys",
        user_context="ctx",
        round_number=1,
        agent_a_position="a",
        agent_b_position="b",
        max_retries=4,
    )
    assert result.resolution_status == "RESOLVED"
    assert len(llm.calls) == 4
