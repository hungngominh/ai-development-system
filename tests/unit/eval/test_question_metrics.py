"""Tests for question_metrics — Layer 2 of eval harness (rule-based portion)."""
from __future__ import annotations

import re

import pytest

from ai_dev_system.eval.golden_loader import DecisionPattern, GoldenIdea, load_idea
from ai_dev_system.eval.metrics.question_metrics import (
    THRESHOLDS,
    compute_avg_question_length,
    compute_binary_yes_no_ratio,
    compute_classification_distribution,
    compute_domain_balance_entropy,
    compute_duplicate_pair_count,
    compute_forbidden_decision_rate,
    compute_question_metrics,
    compute_required_decision_coverage,
    compute_scope_drift_count,
)


def _q(text, *, domain="backend", classification="REQUIRED",
       source_decision_id=None, qid="Qx"):
    return {
        "id": qid,
        "text": text,
        "domain": domain,
        "classification": classification,
        "source_decision_id": source_decision_id,
    }


def _idea(required=None, forbidden=None, intake_script=None) -> GoldenIdea:
    return GoldenIdea(
        id="test",
        raw_idea="test",
        intake_script=intake_script or {},
        profile={},
        required_decisions=required or [],
        forbidden_decisions=forbidden or [],
    )


def _dp(decision_id, *patterns) -> DecisionPattern:
    return DecisionPattern(
        decision_id=decision_id,
        why="test",
        domain_expected=[],
        patterns=[re.compile(p) for p in patterns],
    )


# ============================================================================
# required_decision_coverage
# ============================================================================

class TestRequiredCoverage:
    def test_no_required_is_vacuously_pass(self):
        rate, missed = compute_required_decision_coverage([], _idea())
        assert rate == 1.0
        assert missed == []

    def test_all_covered(self):
        idea = _idea(required=[
            _dp("search_engine", r"(?i)search.*engine"),
            _dp("moderation", r"(?i)moderat"),
        ])
        questions = [
            _q("Which search engine: PostgreSQL FTS or Meilisearch?"),
            _q("What moderation policy: manual or auto?"),
        ]
        rate, missed = compute_required_decision_coverage(questions, idea)
        assert rate == 1.0
        assert missed == []

    def test_one_missed(self):
        idea = _idea(required=[
            _dp("search_engine", r"(?i)search.*engine"),
            _dp("moderation", r"(?i)moderat"),
        ])
        questions = [_q("Which search engine?")]
        rate, missed = compute_required_decision_coverage(questions, idea)
        assert rate == 0.5
        assert missed == ["moderation"]

    def test_one_question_satisfies_multiple_patterns(self):
        """One question can cover multiple decisions if patterns overlap."""
        idea = _idea(required=[
            _dp("search", r"(?i)search"),
            _dp("engine", r"(?i)engine"),
        ])
        rate, _ = compute_required_decision_coverage(
            [_q("Which search engine to use?")], idea
        )
        assert rate == 1.0


# ============================================================================
# forbidden_decision_rate
# ============================================================================

class TestForbiddenRate:
    def test_no_forbidden(self):
        rate, hits = compute_forbidden_decision_rate(
            [_q("Anything goes")], _idea()
        )
        assert rate == 0.0
        assert hits == []

    def test_no_violation(self):
        idea = _idea(forbidden=[_dp("db_choice", r"(?i)which.*database")])
        questions = [_q("Search engine?"), _q("Moderation policy?")]
        rate, hits = compute_forbidden_decision_rate(questions, idea)
        assert rate == 0.0
        assert hits == []

    def test_one_violation(self):
        idea = _idea(forbidden=[
            _dp("db_choice", r"(?i)which.*database"),
            _dp("auth_method", r"(?i)oauth.*vs.*jwt"),
        ])
        questions = [_q("Which database to use?")]
        rate, hits = compute_forbidden_decision_rate(questions, idea)
        assert rate == 0.5
        assert len(hits) == 1
        assert hits[0]["decision_id"] == "db_choice"

    def test_multiple_violations(self):
        idea = _idea(forbidden=[_dp("db_choice", r"(?i)database")])
        questions = [
            _q("Which database?"),
            _q("How should we partition the database?"),
        ]
        rate, hits = compute_forbidden_decision_rate(questions, idea)
        # Both match db_choice but it's only 1 forbidden decision
        assert rate == 1.0
        assert len(hits) == 1  # break after first hit per decision


# ============================================================================
# duplicate_pair_count
# ============================================================================

class TestDuplicatePairs:
    def test_no_decisions(self):
        count, pairs = compute_duplicate_pair_count([_q("a"), _q("b")])
        # neither has source_decision_id → no pairs
        assert count == 0
        assert pairs == []

    def test_no_dupes(self):
        questions = [
            _q("a", source_decision_id="d1", qid="Q1"),
            _q("b", source_decision_id="d2", qid="Q2"),
        ]
        count, _ = compute_duplicate_pair_count(questions)
        assert count == 0

    def test_one_pair(self):
        questions = [
            _q("a", source_decision_id="d1", qid="Q1"),
            _q("b", source_decision_id="d1", qid="Q2"),
        ]
        count, pairs = compute_duplicate_pair_count(questions)
        assert count == 1
        assert ("Q1", "Q2") in pairs

    def test_triple_makes_three_pairs(self):
        questions = [
            _q("a", source_decision_id="d1", qid="Q1"),
            _q("b", source_decision_id="d1", qid="Q2"),
            _q("c", source_decision_id="d1", qid="Q3"),
        ]
        count, _ = compute_duplicate_pair_count(questions)
        assert count == 3  # C(3,2) = 3


# ============================================================================
# domain_balance_entropy
# ============================================================================

class TestDomainEntropy:
    def test_empty(self):
        assert compute_domain_balance_entropy([]) == 0.0

    def test_single_domain(self):
        questions = [_q("a", domain="backend"), _q("b", domain="backend")]
        assert compute_domain_balance_entropy(questions) == 0.0

    def test_two_domains_equal(self):
        import math
        questions = [_q("a", domain="backend"), _q("b", domain="security")]
        # H = -2 * (0.5 * log 0.5) = log 2 ≈ 0.693 nats
        assert compute_domain_balance_entropy(questions) == pytest.approx(math.log(2))

    def test_uniform_5_domains(self):
        import math
        questions = [_q("x", domain=d) for d in ("a", "b", "c", "d", "e")]
        # H = log 5 ≈ 1.609 nats
        assert compute_domain_balance_entropy(questions) == pytest.approx(math.log(5))


# ============================================================================
# avg_question_length
# ============================================================================

class TestAvgLength:
    def test_empty(self):
        assert compute_avg_question_length([]) == 0.0

    def test_uniform(self):
        questions = [_q("a" * 100), _q("b" * 100)]
        assert compute_avg_question_length(questions) == 100

    def test_mixed(self):
        questions = [_q("a" * 100), _q("b" * 200)]
        assert compute_avg_question_length(questions) == 150


# ============================================================================
# classification_distribution
# ============================================================================

class TestClassification:
    def test_empty(self):
        assert compute_classification_distribution([]) == {}

    def test_all_required(self):
        questions = [_q("a"), _q("b"), _q("c")]
        d = compute_classification_distribution(questions)
        assert d["REQUIRED"] == 1.0

    def test_mixed(self):
        questions = [
            _q("a", classification="REQUIRED"),
            _q("b", classification="REQUIRED"),
            _q("c", classification="STRATEGIC"),
            _q("d", classification="OPTIONAL"),
        ]
        d = compute_classification_distribution(questions)
        assert d["REQUIRED"] == 0.5
        assert d["STRATEGIC"] == 0.25
        assert d["OPTIONAL"] == 0.25


# ============================================================================
# compute_question_metrics — integration
# ============================================================================

class TestIntegration:
    def test_perfect_questions_pass(self):
        idea = _idea(
            required=[
                _dp("search", r"(?i)search"),
                _dp("moderation", r"(?i)moderat"),
                _dp("voting", r"(?i)vot"),
            ],
            forbidden=[_dp("db_choice", r"(?i)which.*database")],
        )
        questions = [
            _q("Search engine choice for full-text search: PostgreSQL FTS, Meilisearch, or Elasticsearch?", domain="backend"),
            _q("Moderation policy approach: manual review queue, automated heuristic filters, or community flag-based?", domain="product"),
            _q("Voting anti-abuse strategy with rate limit per user and IP, or trust-graph weighting?", domain="security"),
            _q("Notification delivery channel for post mentions: email digest, Slack DM, or in-app inbox?", domain="devops"),
            _q("Comment depth limit enforcement at DB schema level (CHECK constraint) or only at UI layer?", domain="database"),
        ]
        report = compute_question_metrics(questions, idea)
        assert report.pass_required_coverage
        assert report.pass_forbidden_rate
        assert report.pass_duplicate
        assert report.pass_domain_entropy  # 5 different domains
        assert report.pass_avg_length
        assert report.overall_pass()

    def test_zero_questions_fails(self):
        idea = _idea(required=[_dp("x", r"x")])
        report = compute_question_metrics([], idea)
        assert not report.pass_required_coverage
        assert report.question_count == 0

    def test_forbidden_triggers_failure(self):
        idea = _idea(forbidden=[_dp("db", r"(?i)database")])
        questions = [_q("Which database?")]
        report = compute_question_metrics(questions, idea)
        assert not report.pass_forbidden_rate
        assert len(report.forbidden_hits) == 1

    def test_report_serializable(self):
        report = compute_question_metrics([], _idea())
        d = report.to_dict()
        assert "required_decision_coverage" in d
        assert "duplicate_pairs" in d


# ============================================================================
# Golden idea round-trip — make sure on-disk files load and patterns compile
# ============================================================================

class TestGoldenRoundTrip:
    def test_load_internal_forum(self):
        idea = load_idea("01_internal_forum")
        assert idea.id == "01_internal_forum"
        assert len(idea.required_decisions) >= 5
        assert len(idea.forbidden_decisions) >= 5
        # Check at least one pattern compiled
        assert all(len(d.patterns) >= 1 for d in idea.required_decisions)

    def test_load_cli_devtool(self):
        idea = load_idea("05_cli_devtool")
        assert idea.id == "05_cli_devtool"
        assert len(idea.required_decisions) >= 4
        assert len(idea.forbidden_decisions) >= 5

    def test_golden_patterns_against_synthetic_questions(self):
        """Smoke test: hand-crafted questions should match the golden patterns."""
        idea = load_idea("01_internal_forum")
        # Question that should hit search_engine_choice
        questions = [
            _q("Which search engine to use — PostgreSQL FTS, Meilisearch, or Elasticsearch?"),
            _q("Moderation policy: manual review or automated?"),
        ]
        rate, missed = compute_required_decision_coverage(questions, idea)
        # At least 2 of N covered
        assert rate >= 2 / len(idea.required_decisions)
        assert "search_engine_choice" not in missed
        assert "moderation_policy" not in missed

    def test_all_6_new_golden_ideas_load(self):
        """All M3.1 golden ideas must load without error."""
        new_ids = [
            "02_data_pipeline",
            "03_mobile_b2c_app",
            "04_ml_inference_service",
            "06_saas_b2b",
            "07_legacy_migration",
            "08_security_audit_tool",
        ]
        for idea_id in new_ids:
            idea = load_idea(idea_id)
            assert idea.id == idea_id, f"{idea_id} id mismatch"
            assert len(idea.required_decisions) >= 4, f"{idea_id} needs ≥4 required decisions"
            assert len(idea.forbidden_decisions) >= 2, f"{idea_id} needs ≥2 forbidden decisions"
            assert idea.raw_idea.strip(), f"{idea_id} raw_idea is empty"
            assert idea.intake_script, f"{idea_id} intake_script is empty"
            # All patterns compile (loader already compiles, just check non-empty)
            for dp in idea.required_decisions:
                assert len(dp.patterns) >= 1, f"{idea_id}/{dp.decision_id}: no patterns"


# ============================================================================
# Q6: binary_yes_no_ratio — LLM-based, stub mode
# ============================================================================

class TestBinaryYesNoRatio:
    def test_stub_returns_neutral(self):
        """Without LLM client, ratio is 0.5 (neutral)."""
        questions = [_q("Should we use Kafka?"), _q("Which database to choose?")]
        ratio, offenders = compute_binary_yes_no_ratio(questions, llm_client=None)
        assert ratio == 0.5
        assert offenders == []

    def test_empty_questions_stub(self):
        ratio, offenders = compute_binary_yes_no_ratio([], llm_client=None)
        assert ratio == 0.5
        assert offenders == []

    def test_stub_passes_threshold(self):
        """Stub 0.5 should NOT pass the ≤0.15 threshold — intentionally neutral."""
        assert 0.5 > THRESHOLDS["binary_yes_no_ratio_max"]

    def test_real_llm_client(self):
        """Real LLM client path: mock that marks Q1 as binary, Q2 as open."""
        import json as _json

        class _MockLLM:
            def complete(self, system, user):
                return _json.dumps({"ratings": [
                    {"id": "Q1", "binary": True},
                    {"id": "Q2", "binary": False},
                ]})

        questions = [
            _q("Should we use Kafka?", qid="Q1"),
            _q("How should we handle schema evolution across microservices?", qid="Q2"),
        ]
        ratio, offenders = compute_binary_yes_no_ratio(questions, llm_client=_MockLLM())
        assert ratio == 0.5   # 1 of 2
        assert len(offenders) == 1
        assert "Kafka" in offenders[0]

    def test_llm_parse_error_falls_back(self):
        """If LLM returns invalid JSON, fall back to neutral (0.5, [])."""
        class _BrokenLLM:
            def complete(self, system, user):
                return "not valid json"

        ratio, offenders = compute_binary_yes_no_ratio(
            [_q("Test?", qid="Q1")], llm_client=_BrokenLLM()
        )
        assert ratio == 0.5
        assert offenders == []


# ============================================================================
# Q7: scope_drift_count — LLM-based, stub mode
# ============================================================================

class TestScopeDriftCount:
    def test_stub_returns_zero(self):
        """Without LLM client, drift count is 0 (neutral)."""
        questions = [_q("Is Flutter better than React Native?")]
        count, offenders = compute_scope_drift_count(
            questions, scope_in=["iOS app", "Android app"], llm_client=None
        )
        assert count == 0
        assert offenders == []

    def test_empty_questions_stub(self):
        count, offenders = compute_scope_drift_count([], scope_in=["x"], llm_client=None)
        assert count == 0
        assert offenders == []

    def test_real_llm_marks_drift(self):
        """Mock LLM marks Q2 as out-of-scope."""
        import json as _json

        class _MockLLM:
            def complete(self, system, user):
                return _json.dumps({"ratings": [
                    {"id": "Q1", "in_scope": True},
                    {"id": "Q2", "in_scope": False},
                ]})

        questions = [
            _q("Which map provider: Goong or Google Maps?", qid="Q1"),
            _q("Should we build a desktop web app?", qid="Q2"),
        ]
        count, offenders = compute_scope_drift_count(
            questions,
            scope_in=["Browse restaurants on iOS and Android"],
            llm_client=_MockLLM(),
        )
        assert count == 1
        assert "desktop" in offenders[0].lower()

    def test_llm_parse_error_falls_back(self):
        class _BrokenLLM:
            def complete(self, system, user):
                return "garbage"

        count, _ = compute_scope_drift_count(
            [_q("Test?", qid="Q1")],
            scope_in=["x"],
            llm_client=_BrokenLLM(),
        )
        assert count == 0


# ============================================================================
# compute_question_metrics with LLM metrics wired in
# ============================================================================

class TestQMetricsWithLLM:
    def test_stub_mode_defaults(self):
        """Stub mode: Q6=0.5, Q7=0, llm_metrics_mode='stub'."""
        idea = _idea()
        report = compute_question_metrics([_q("x" * 80, qid="Q1")], idea, llm_client=None)
        assert report.binary_yes_no_ratio == 0.5
        assert report.scope_drift_count == 0
        assert report.llm_metrics_mode == "stub"
        assert report.pass_scope_drift is True   # 0 ≤ 0 threshold → pass

    def test_real_mode_wires_llm(self):
        """Real LLM mode: Q6/Q7 computed from mock."""
        import json as _json

        class _MockLLM:
            def complete(self, system, user):
                if "binary" in system.lower() or "binary" in user.lower() or "yes/no" in system.lower():
                    return _json.dumps({"ratings": [{"id": "Q1", "binary": False}]})
                return _json.dumps({"ratings": [{"id": "Q1", "in_scope": True}]})

        idea = _idea(intake_script={"scope_in": ["authentication", "user login"]})
        report = compute_question_metrics(
            [_q("What authentication strategy (JWT vs session) should we use?", qid="Q1")],
            idea,
            llm_client=_MockLLM(),
        )
        assert report.llm_metrics_mode == "real"
        assert report.binary_yes_no_ratio == 0.0   # LLM says not binary
        assert report.scope_drift_count == 0        # LLM says in scope

    def test_new_fields_in_dict(self):
        """to_dict() includes Q6/Q7 fields."""
        report = compute_question_metrics([], _idea())
        d = report.to_dict()
        assert "binary_yes_no_ratio" in d
        assert "scope_drift_count" in d
        assert "llm_metrics_mode" in d
        assert "binary_yes_no_questions" in d
        assert "scope_drift_questions" in d
