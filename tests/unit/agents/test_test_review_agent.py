from ai_dev_system.agents.test_review_agent import (
    TestReviewVerdict, _parse_test_verdict, _build_test_review_prompt,
)


def test_red_tests_no_findings_is_not_blocking():
    v = TestReviewVerdict(verdict="pass", tests_red=True, findings=[])
    assert v.is_blocking() is False


def test_green_tests_at_red_stage_is_blocking():
    # Tests passing without implementation => tautological / wrong => blocking.
    v = TestReviewVerdict(verdict="pass", tests_red=False, findings=[])
    assert v.is_blocking() is True


def test_high_severity_finding_is_blocking():
    v = TestReviewVerdict(verdict="pass", tests_red=True,
                          findings=[{"severity": "high", "issue": "AC-2 has no test"}])
    assert v.is_blocking() is True


def test_inconclusive_never_blocks():
    v = TestReviewVerdict(verdict="inconclusive", tests_red=False, findings=[])
    assert v.is_blocking() is False


def test_parse_extracts_json_from_prose():
    raw = 'here is my review\n{"verdict":"fail","tests_red":true,' \
          '"findings":[{"severity":"high","file":"t.py","line":3,"issue":"x"}]}\nthanks'
    v = _parse_test_verdict(raw)
    assert v.verdict == "fail"
    assert v.tests_red is True
    assert v.findings[0]["severity"] == "high"


def test_prompt_includes_test_spec_and_redness_instruction():
    p = _build_test_review_prompt("main", "Add login", "AC-1: returns 401 on bad creds")
    assert "AC-1: returns 401" in p
    assert "RED" in p or "fail" in p.lower()
