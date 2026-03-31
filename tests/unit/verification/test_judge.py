from ai_dev_system.verification.judge import StubVerificationLLMClient


def test_stub_returns_configured_verdict():
    stub = StubVerificationLLMClient(verdicts={
        "AC-1": ("PASS", 0.95, "looks good"),
        "AC-2": ("FAIL", 0.88, "coverage is 71%"),
    })
    verdict, conf, reasoning = stub.judge_criterion("AC-1", "User can create tasks", ["output..."])
    assert verdict == "PASS"
    assert conf == 0.95
    assert "looks good" in reasoning


def test_stub_returns_configured_fail():
    stub = StubVerificationLLMClient(verdicts={
        "AC-2": ("FAIL", 0.88, "coverage is 71%"),
    })
    verdict, conf, reasoning = stub.judge_criterion("AC-2", "Coverage ≥ 80%", ["pytest-cov: 71%"])
    assert verdict == "FAIL"
    assert conf == 0.88


def test_stub_defaults_to_pass_for_unknown_criterion():
    stub = StubVerificationLLMClient(verdicts={})
    verdict, conf, reasoning = stub.judge_criterion("AC-99", "Unknown criterion", [])
    assert verdict == "PASS"
    assert conf == 1.0


def test_stub_protocol_compliance():
    """Static check: StubVerificationLLMClient satisfies VerificationLLMClient protocol."""
    from ai_dev_system.verification.judge import VerificationLLMClient
    client: VerificationLLMClient = StubVerificationLLMClient(verdicts={})
    assert callable(client.judge_criterion)
