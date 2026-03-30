from ai_dev_system.debate.report import (
    Question, RoundResult, QuestionDebateResult, DebateReport, auto_resolve
)

def test_question_construction():
    q = Question(id="Q1", text="Use JWT?", classification="REQUIRED",
                 domain="security", agent_a="SecuritySpecialist", agent_b="BackendArchitect")
    assert q.id == "Q1"
    assert q.classification == "REQUIRED"

def test_round_result_construction():
    r = RoundResult(round_number=1, agent_a_position="Use JWT",
                    agent_b_position="Use sessions", moderator_summary="JWT preferred",
                    resolution_status="RESOLVED", confidence=0.9, caveat=None)
    assert r.confidence == 0.9

def test_auto_resolve_optional():
    q = Question(id="Q5", text="Color scheme?", classification="OPTIONAL",
                 domain="product", agent_a="ProductManager", agent_b="QAEngineer")
    result = auto_resolve(q)
    assert result.final.resolution_status == "RESOLVED"
    assert result.final.confidence == 1.0
    assert len(result.rounds) == 1

def test_debate_report_escalated_and_resolved():
    q1 = Question(id="Q1", text="Auth?", classification="REQUIRED",
                  domain="security", agent_a="SecuritySpecialist", agent_b="BackendArchitect")
    r1 = RoundResult(1, "JWT", "Sessions", "JWT wins", "ESCALATE_TO_HUMAN", 0.4, None)
    qdr1 = QuestionDebateResult(question=q1, rounds=[r1], final=r1)

    q2 = Question(id="Q2", text="DB?", classification="STRATEGIC",
                  domain="database", agent_a="DatabaseSpecialist", agent_b="BackendArchitect")
    r2 = RoundResult(1, "Postgres", "MySQL", "Postgres", "RESOLVED", 0.95, None)
    qdr2 = QuestionDebateResult(question=q2, rounds=[r2], final=r2)

    report = DebateReport(run_id="r1", brief={"raw_idea": "x"},
                          results=[qdr1, qdr2], generated_at="2026-03-30T00:00:00Z")
    escalated = [r for r in report.results if r.final.resolution_status == "ESCALATE_TO_HUMAN"]
    resolved = [r for r in report.results if r.final.resolution_status != "ESCALATE_TO_HUMAN"]
    assert len(escalated) == 1
    assert len(resolved) == 1
