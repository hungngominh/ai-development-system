"""M5.B AgentRegistry tests.

Synthetic registries exercise the pair_suggestion algorithm against
known agent topologies; real registry smoke test asserts the
committed sample files load and the SecuritySpecialist <-> BackendArchitect
pair is returned for typical decision domains.
"""

import pytest

from ai_dev_system.debate.agents import (
    AgentNotFoundError,
    AgentRegistry,
    AgentSpec,
    PairSuggestionError,
)


# ---- helpers ----


def _spec(
    key: str,
    domain: str,
    *,
    paired: list[str] | None = None,
    debate_role: str = "neutral",
) -> AgentSpec:
    return AgentSpec(
        key=key,
        domain=domain,
        version=1,
        aliases=[],
        debate_role=debate_role,
        typical_paired_with=paired or [],
        system_prompt=f"system prompt for {key}",
        file_path=None,
        is_fallback=False,
    )


# ---- construction ----


def test_from_specs_builds_indexes():
    reg = AgentRegistry.from_specs([
        _spec("A", "backend"),
        _spec("B", "security"),
        _spec("C", "backend"),
    ])
    assert len(reg) == 3
    assert "A" in reg
    assert reg.get("A").key == "A"
    assert {s.key for s in reg.by_domain("backend")} == {"A", "C"}
    assert {s.key for s in reg.by_domain("security")} == {"B"}


def test_from_specs_rejects_duplicate_key():
    with pytest.raises(ValueError, match="Duplicate agent key"):
        AgentRegistry.from_specs([
            _spec("A", "backend"),
            _spec("A", "security"),
        ])


def test_from_directory_loads_committed_samples():
    reg = AgentRegistry.from_directory()
    keys = {s.key for s in reg.list_all()}
    assert "SecuritySpecialist" in keys
    assert "BackendArchitect" in keys


def test_from_directory_with_custom_dir(tmp_path):
    (tmp_path / "ag_one.md").write_text(
        "---\nagent_key: One\ndomain: backend\nversion: 1\n---\nbody\n",
        encoding="utf-8",
    )
    (tmp_path / "ag_two.md").write_text(
        "---\nagent_key: Two\ndomain: security\nversion: 1\n---\nbody\n",
        encoding="utf-8",
    )
    reg = AgentRegistry.from_directory(tmp_path)
    assert len(reg) == 2


def test_from_directory_propagates_parse_failures(tmp_path):
    (tmp_path / "broken.md").write_text("no frontmatter here\n", encoding="utf-8")
    from ai_dev_system.debate.agents import AgentLoadError
    with pytest.raises(AgentLoadError):
        AgentRegistry.from_directory(tmp_path)


# ---- lookups ----


def test_get_unknown_key_raises():
    reg = AgentRegistry.from_specs([_spec("A", "backend")])
    with pytest.raises(AgentNotFoundError):
        reg.get("Missing")


def test_by_domain_missing_returns_empty_list():
    reg = AgentRegistry.from_specs([_spec("A", "backend")])
    assert reg.by_domain("legal") == []


def test_list_all_returns_all_in_insertion_order():
    specs = [_spec("A", "backend"), _spec("B", "security"), _spec("C", "data")]
    reg = AgentRegistry.from_specs(specs)
    assert [s.key for s in reg.list_all()] == ["A", "B", "C"]


# ---- pair_suggestion: 4-strategy chain ----


def test_pair_strategy_1_typical_intersect_remaining_hints():
    # Primary=A (backend); A.typical_paired_with=[B, C].
    # B is security, C is data. decision_domains=[backend, data].
    # remaining=[data] → C matches → return C
    reg = AgentRegistry.from_specs([
        _spec("A", "backend", paired=["B", "C"]),
        _spec("B", "security"),
        _spec("C", "data"),
    ])
    assert reg.pair_suggestion("A", ["backend", "data"]) == "C"


def test_pair_strategy_2_typical_with_different_domain():
    # Primary=A (backend); typical=[B]. B is security.
    # decision_domains=[backend] only → remaining=[] → fall to strategy 2
    # which picks B because B.domain != A.domain
    reg = AgentRegistry.from_specs([
        _spec("A", "backend", paired=["B"]),
        _spec("B", "security"),
    ])
    assert reg.pair_suggestion("A", ["backend"]) == "B"


def test_pair_strategy_3_any_agent_in_remaining_hints_domain():
    # Primary=A (backend); typical=[] (nothing).
    # remaining=[security] → strategy 1+2 skipped → strategy 3 finds B
    reg = AgentRegistry.from_specs([
        _spec("A", "backend", paired=[]),
        _spec("B", "security"),
    ])
    assert reg.pair_suggestion("A", ["backend", "security"]) == "B"


def test_pair_strategy_4_any_different_domain_fallback():
    # Primary=A (backend); typical=[]; decision_domains=[backend] only.
    # remaining=[] → strategies 1-3 all skip → strategy 4 picks any
    # agent in a different domain.
    reg = AgentRegistry.from_specs([
        _spec("A", "backend", paired=[]),
        _spec("B", "security"),
    ])
    assert reg.pair_suggestion("A", ["backend"]) == "B"


def test_pair_strategy_4_skips_unknown_typical_keys():
    # typical_paired_with refers to keys not in registry — should be
    # ignored cleanly, fall through to later strategies.
    reg = AgentRegistry.from_specs([
        _spec("A", "backend", paired=["GhostAgent"]),
        _spec("B", "security"),
    ])
    assert reg.pair_suggestion("A", ["backend"]) == "B"


def test_pair_raises_when_only_same_domain_agents():
    # Anti-echo guarantee: every other agent is in the same domain
    reg = AgentRegistry.from_specs([
        _spec("A", "backend"),
        _spec("C", "backend"),
    ])
    with pytest.raises(PairSuggestionError, match="No counter-agent"):
        reg.pair_suggestion("A", ["backend"])


def test_pair_raises_when_only_primary_exists():
    reg = AgentRegistry.from_specs([_spec("A", "backend")])
    with pytest.raises(PairSuggestionError):
        reg.pair_suggestion("A", ["backend"])


def test_pair_unknown_primary_raises():
    reg = AgentRegistry.from_specs([_spec("A", "backend"), _spec("B", "security")])
    with pytest.raises(AgentNotFoundError):
        reg.pair_suggestion("Missing", ["backend"])


def test_pair_prefers_typical_over_random_domain_match():
    # Strategy 1 (paired ∩ remaining) wins over strategy 3 (any in domain)
    reg = AgentRegistry.from_specs([
        _spec("A", "backend", paired=["B"]),
        _spec("B", "security"),
        _spec("C", "security"),  # also security, but not in A.typical
    ])
    # remaining=[security] — both B and C match domain, but B is
    # in typical → return B
    assert reg.pair_suggestion("A", ["backend", "security"]) == "B"


# ---- real-sample integration ----


def test_real_registry_pairs_security_with_backend():
    reg = AgentRegistry.from_directory()
    # SecuritySpecialist.typical_paired_with includes BackendArchitect;
    # decision spans both domains → BackendArchitect wins via strategy 1.
    assert reg.pair_suggestion("SecuritySpecialist", ["security", "backend"]) == "BackendArchitect"


def test_real_registry_pairs_backend_with_security():
    reg = AgentRegistry.from_directory()
    # Symmetric: BackendArchitect.typical_paired_with includes SecuritySpecialist
    assert reg.pair_suggestion("BackendArchitect", ["backend", "security"]) == "SecuritySpecialist"


def test_real_registry_lookups():
    reg = AgentRegistry.from_directory()
    sec = reg.get("SecuritySpecialist")
    assert sec.domain == "security"
    assert reg.by_domain("backend")[0].key == "BackendArchitect"


# ---- M5.F.1: 12-domain coverage ----


def test_real_registry_covers_all_12_canonical_domains():
    """Spec Appendix A: every canonical domain must have ≥1 agent.

    Without this, pair_suggestion strategy 3 (decision_domains lookup)
    falls back to strategy 4 (any other domain) for any decision that
    spans a domain with no resident agent — silently weakening lens
    diversity.
    """
    from ai_dev_system.debate.domains import DOMAINS

    reg = AgentRegistry.from_directory()
    covered = {spec.domain for spec in reg.list_all()}
    missing = set(DOMAINS) - covered
    assert not missing, f"canonical domains without agent: {sorted(missing)}"


def test_real_registry_has_exactly_one_agent_per_canonical_domain():
    """Per locked decision #23 the agent set is capped at 12 (one per
    canonical domain). Multiple agents in a single domain is allowed by
    the data model but should not happen with the shipped set."""
    from ai_dev_system.debate.domains import DOMAINS

    reg = AgentRegistry.from_directory()
    counts: dict[str, int] = {}
    for spec in reg.list_all():
        counts[spec.domain] = counts.get(spec.domain, 0) + 1
    duplicates = {d: n for d, n in counts.items() if n > 1}
    assert not duplicates, f"domains with >1 agent: {duplicates}"
    assert len(reg) == len(DOMAINS)


def test_real_registry_typical_paired_with_resolves():
    """Every agent's typical_paired_with hints should point at real
    registry keys — broken hints downgrade pair_suggestion strategy 1
    to a silent miss."""
    reg = AgentRegistry.from_directory()
    broken: dict[str, list[str]] = {}
    for spec in reg.list_all():
        missing = [k for k in spec.typical_paired_with if k not in reg]
        if missing:
            broken[spec.key] = missing
    assert not broken, f"unresolvable typical_paired_with hints: {broken}"


def test_real_registry_no_self_pair_hints():
    """An agent should never list itself as typical_paired_with."""
    reg = AgentRegistry.from_directory()
    self_pairs = [
        spec.key for spec in reg.list_all()
        if spec.key in spec.typical_paired_with
    ]
    assert not self_pairs


def test_real_registry_pair_suggestion_works_for_every_agent():
    """For every agent, asking for a counterparty with an empty
    decision_domains list should succeed via strategy 2 (typical_paired_with
    with different domain) or fall through to strategy 4. PairSuggestionError
    here means a corner of the registry has no valid counterparty."""
    reg = AgentRegistry.from_directory()
    failures: list[str] = []
    for spec in reg.list_all():
        try:
            partner = reg.pair_suggestion(spec.key, [])
        except PairSuggestionError as e:
            failures.append(f"{spec.key}: {e}")
            continue
        partner_spec = reg.get(partner)
        if partner_spec.domain == spec.domain:
            failures.append(
                f"{spec.key} -> {partner} but both are in domain {spec.domain!r}"
            )
    assert not failures, "\n".join(failures)


def test_real_registry_debate_role_valid_for_every_agent():
    """Every agent must declare critic_first | advocate_first | neutral.
    parse_agent_md rejects unknown values, but a regression in the file
    set would surface here as a load failure → keep this as a smoke
    barrier for future contributors."""
    from ai_dev_system.debate.agents.loader import VALID_DEBATE_ROLES

    reg = AgentRegistry.from_directory()
    bad = [
        (spec.key, spec.debate_role)
        for spec in reg.list_all()
        if spec.debate_role not in VALID_DEBATE_ROLES
    ]
    assert not bad
