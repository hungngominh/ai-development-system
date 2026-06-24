"""M4.5 Pipeline orchestrator tests.

Stages are monkeypatched so these tests verify ORDER, DATA FLOW, and
RECOVERY (C1 retrigger, C4 gate, ID collision suffixing) — not the
internal behaviour of each stage (covered by their own test files).
"""

import pytest

from ai_dev_system.debate.questions import (
    coverage,
    critic,
    inventory,
    materializer,
    pipeline,
)
from ai_dev_system.debate.questions.coverage import CoverageError
from ai_dev_system.debate.questions.models import (
    CoverageCheck,
    CoverageReport,
    Decision,
)
from ai_dev_system.debate.report import Question


# ---- helpers ----


def _decision(id_: str, classification: str = "REQUIRED") -> Decision:
    return Decision(
        id=id_,
        summary=f"Decide {id_}",
        classification=classification,
        domain_hints=["backend"],
        blocks_what=["voting"] if classification != "OPTIONAL" else [],
        has_safe_default=False,
    )


def _question(qid: str, source: str | None = None) -> Question:
    return Question(
        id=qid,
        text=f"Q text {qid}",
        classification="REQUIRED",
        domain="backend",
        agent_a="BackendArchitect",
        agent_b="ProductManager",
        source_decision_id=source,
    )


def _coverage_report(*, c1_status="pass", c4_status="pass", missing_ids=None):
    return CoverageReport(
        checks=[
            CoverageCheck(
                name="C1_decision_coverage",
                status=c1_status,
                detail={"missing_decision_ids": missing_ids or []},
            ),
            CoverageCheck(name="C2_domain_balance", status="pass", detail={}),
            CoverageCheck(name="C3_classification_sanity", status="pass", detail={}),
            CoverageCheck(name="C4_question_count", status=c4_status, detail={}),
        ],
        covered_decision_ids=[],
        missing_decision_ids=missing_ids or [],
        domain_distribution={},
        classification_distribution={},
        total_questions=0,
    )


class _Spy:
    """Records call args and serves canned return values in order."""

    def __init__(self, returns):
        self.returns = list(returns)
        self.calls: list[dict] = []

    def __call__(self, *args, **kwargs):
        self.calls.append({"args": args, "kwargs": kwargs})
        if not self.returns:
            raise AssertionError("_Spy exhausted")
        return self.returns.pop(0)


@pytest.fixture
def stub_llm():
    return object()


def _patch_stages(
    monkeypatch,
    *,
    inventory_return,
    materializer_returns,  # list — one per call (fresh + maybe retrigger)
    critic_return,
    coverage_returns,  # list — one per call (first + maybe re-check)
):
    inv_spy = _Spy([inventory_return])
    mat_spy = _Spy(materializer_returns)
    crit_spy = _Spy([critic_return])
    cov_spy = _Spy(coverage_returns)
    monkeypatch.setattr(inventory, "run", inv_spy)
    monkeypatch.setattr(materializer, "run", mat_spy)
    monkeypatch.setattr(critic, "run", crit_spy)
    monkeypatch.setattr(coverage, "run", cov_spy)
    return inv_spy, mat_spy, crit_spy, cov_spy


# ---- happy path ----


def test_happy_path_runs_all_stages_in_order(monkeypatch, stub_llm):
    decisions = [_decision(f"d{i}") for i in range(8)]
    draft = [_question(f"Q{i}", source=f"d{i}") for i in range(8)]
    refined = list(draft)
    report = _coverage_report()

    inv, mat, crit, cov = _patch_stages(
        monkeypatch,
        inventory_return=decisions,
        materializer_returns=[draft],
        critic_return=(refined, 1),
        coverage_returns=[report],
    )

    result = pipeline.run_pipeline({"brief": "v2"}, "digest", stub_llm)

    assert result.decisions == decisions
    assert result.questions_final == refined
    assert result.coverage_report is report
    assert result.critic_iterations == 1

    # ordering + arg propagation
    assert inv.calls[0]["args"] == ({"brief": "v2"}, stub_llm)
    assert mat.calls[0]["args"] == (decisions, "digest", stub_llm)
    assert mat.calls[0]["kwargs"] == {"mode": "fresh", "profile": None}
    assert crit.calls[0]["args"] == (draft, "digest", stub_llm)
    assert cov.calls[0]["args"] == (refined, decisions, {"brief": "v2"})
    # exactly one call per stage when C1 passes
    assert len(mat.calls) == 1
    assert len(cov.calls) == 1


# ---- C1 retrigger ----


def test_c1_fail_triggers_retrigger_and_recovers(monkeypatch, stub_llm):
    decisions = [_decision(f"d{i}") for i in range(8)]
    draft = [_question(f"Q{i}", source=f"d{i}") for i in range(6)]
    refined = list(draft)
    extra = [
        _question("Q-new1", source="d6"),
        _question("Q-new2", source="d7"),
    ]
    first_report = _coverage_report(c1_status="fail", missing_ids=["d6", "d7"])
    second_report = _coverage_report(c1_status="pass")

    inv, mat, crit, cov = _patch_stages(
        monkeypatch,
        inventory_return=decisions,
        materializer_returns=[draft, extra],
        critic_return=(refined, 2),
        coverage_returns=[first_report, second_report],
    )

    result = pipeline.run_pipeline({}, "digest", stub_llm)

    assert result.coverage_report is second_report
    assert len(result.questions_final) == 8
    # second materializer call with mode=retrigger, only the missing decisions
    assert len(mat.calls) == 2
    assert mat.calls[1]["kwargs"] == {"mode": "retrigger", "profile": None}
    retrigger_decisions = mat.calls[1]["args"][0]
    assert [d.id for d in retrigger_decisions] == ["d6", "d7"]
    # two coverage calls
    assert len(cov.calls) == 2


def test_c1_fail_persists_after_retrigger_ships_anyway(monkeypatch, stub_llm):
    decisions = [_decision(f"d{i}") for i in range(8)]
    draft = [_question(f"Q{i}", source=f"d{i}") for i in range(6)]
    refined = list(draft)
    extra = []  # retrigger LLM emitted nothing
    first_report = _coverage_report(c1_status="fail", missing_ids=["d6", "d7"])
    second_report = _coverage_report(c1_status="fail", missing_ids=["d6", "d7"])

    _, mat, _, _ = _patch_stages(
        monkeypatch,
        inventory_return=decisions,
        materializer_returns=[draft, extra],
        critic_return=(refined, 1),
        coverage_returns=[first_report, second_report],
    )

    result = pipeline.run_pipeline({}, "digest", stub_llm)
    # Persistent C1 fail does NOT raise — caller surfaces it
    c1 = next(c for c in result.coverage_report.checks if c.name == "C1_decision_coverage")
    assert c1.status == "fail"
    assert "d6" in result.coverage_report.missing_decision_ids
    assert len(mat.calls) == 2


def test_c1_fail_with_empty_missing_ids_skips_retrigger(monkeypatch, stub_llm):
    decisions = [_decision(f"d{i}") for i in range(8)]
    refined = [_question(f"Q{i}", source=f"d{i}") for i in range(8)]
    # weird: C1 status=fail but empty list — defensive path
    report = _coverage_report(c1_status="fail", missing_ids=[])

    _, mat, _, cov = _patch_stages(
        monkeypatch,
        inventory_return=decisions,
        materializer_returns=[refined],
        critic_return=(refined, 0),
        coverage_returns=[report],
    )

    pipeline.run_pipeline({}, "digest", stub_llm)
    # only the fresh materializer call; no retrigger; no re-check
    assert len(mat.calls) == 1
    assert len(cov.calls) == 1


# ---- ID collision on retrigger ----


def test_retrigger_id_collision_gets_suffixed(monkeypatch, stub_llm):
    decisions = [_decision(f"d{i}") for i in range(8)]
    refined = [_question("Q1", source="d0"), _question("Q2", source="d1")]
    # retrigger emits a question that collides with existing Q1
    extra = [_question("Q1", source="d6")]
    first_report = _coverage_report(c1_status="fail", missing_ids=["d6"])
    second_report = _coverage_report()

    _patch_stages(
        monkeypatch,
        inventory_return=decisions,
        materializer_returns=[refined, extra],
        critic_return=(refined, 0),
        coverage_returns=[first_report, second_report],
    )

    result = pipeline.run_pipeline({}, "digest", stub_llm)
    ids = [q.id for q in result.questions_final]
    assert ids == ["Q1", "Q2", "Q1-r1"]


def test_retrigger_no_collision_keeps_original_id(monkeypatch, stub_llm):
    decisions = [_decision(f"d{i}") for i in range(8)]
    refined = [_question("Q1", source="d0")]
    extra = [_question("Q-fresh", source="d6")]
    first_report = _coverage_report(c1_status="fail", missing_ids=["d6"])
    second_report = _coverage_report()

    _patch_stages(
        monkeypatch,
        inventory_return=decisions,
        materializer_returns=[refined, extra],
        critic_return=(refined, 0),
        coverage_returns=[first_report, second_report],
    )

    result = pipeline.run_pipeline({}, "digest", stub_llm)
    assert [q.id for q in result.questions_final] == ["Q1", "Q-fresh"]


# ---- C4 fatal gate ----


def test_c4_fail_raises_coverage_error(monkeypatch, stub_llm):
    decisions = [_decision(f"d{i}") for i in range(8)]
    refined = [_question(f"Q{i}", source=f"d{i}") for i in range(3)]
    report = _coverage_report(c4_status="fail")

    _patch_stages(
        monkeypatch,
        inventory_return=decisions,
        materializer_returns=[refined],
        critic_return=(refined, 0),
        coverage_returns=[report],
    )

    with pytest.raises(CoverageError, match="C4_question_count failed"):
        pipeline.run_pipeline({}, "digest", stub_llm)


def test_c4_fail_after_retrigger_still_raises(monkeypatch, stub_llm):
    decisions = [_decision(f"d{i}") for i in range(8)]
    refined = [_question(f"Q{i}", source=f"d{i}") for i in range(3)]
    extra = [_question("Q-new", source="d7")]
    first_report = _coverage_report(c1_status="fail", missing_ids=["d7"], c4_status="fail")
    second_report = _coverage_report(c1_status="pass", c4_status="fail")

    _patch_stages(
        monkeypatch,
        inventory_return=decisions,
        materializer_returns=[refined, extra],
        critic_return=(refined, 0),
        coverage_returns=[first_report, second_report],
    )

    with pytest.raises(CoverageError):
        pipeline.run_pipeline({}, "digest", stub_llm)


# ---- propagation from sub-stages ----


def test_inventory_error_propagates(monkeypatch, stub_llm):
    from ai_dev_system.debate.questions.inventory import InventoryCountError

    def boom(*args, **kwargs):
        raise InventoryCountError("bad count")

    monkeypatch.setattr(inventory, "run", boom)
    with pytest.raises(InventoryCountError):
        pipeline.run_pipeline({}, "digest", stub_llm)


def test_retrigger_materializer_error_propagates(monkeypatch, stub_llm):
    from ai_dev_system.debate.questions.materializer import MaterializerError

    decisions = [_decision(f"d{i}") for i in range(8)]
    refined = [_question(f"Q{i}", source=f"d{i}") for i in range(6)]

    def materialize(decs, digest, llm, *, mode, profile=None):
        if mode == "fresh":
            return refined
        raise MaterializerError("retrigger boom")

    monkeypatch.setattr(inventory, "run", lambda *a, **k: decisions)
    monkeypatch.setattr(materializer, "run", materialize)
    monkeypatch.setattr(critic, "run", lambda *a, **k: (refined, 1))
    monkeypatch.setattr(
        coverage,
        "run",
        lambda *a, **k: _coverage_report(c1_status="fail", missing_ids=["d7"]),
    )

    with pytest.raises(MaterializerError, match="retrigger boom"):
        pipeline.run_pipeline({}, "digest", stub_llm)


def test_critic_iterations_propagate_to_result(monkeypatch, stub_llm):
    decisions = [_decision(f"d{i}") for i in range(8)]
    refined = [_question(f"Q{i}", source=f"d{i}") for i in range(8)]
    _patch_stages(
        monkeypatch,
        inventory_return=decisions,
        materializer_returns=[refined],
        critic_return=(refined, 2),
        coverage_returns=[_coverage_report()],
    )
    result = pipeline.run_pipeline({}, "digest", stub_llm)
    assert result.critic_iterations == 2


def test_decision_9_critic_receives_same_llm_client(monkeypatch, stub_llm):
    """Locked decision #9: critic uses the same model as materializer.

    Verify the pipeline forwards the exact llm_client object to critic
    rather than constructing a separate client.
    """
    decisions = [_decision(f"d{i}") for i in range(8)]
    refined = [_question(f"Q{i}", source=f"d{i}") for i in range(8)]
    inv, mat, crit, cov = _patch_stages(
        monkeypatch,
        inventory_return=decisions,
        materializer_returns=[refined],
        critic_return=(refined, 0),
        coverage_returns=[_coverage_report()],
    )
    pipeline.run_pipeline({}, "digest", stub_llm)
    # critic must receive the same object (not a copy / wrapper)
    materializer_client = mat.calls[0]["args"][2]
    critic_client = crit.calls[0]["args"][2]
    assert critic_client is materializer_client is stub_llm
