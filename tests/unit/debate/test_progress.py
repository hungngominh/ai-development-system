"""run_debate must emit progress events (on_questions / on_question_start /
on_round) so the CLI can render live progress; the default no-progress path
stays a silent no-op and OPTIONAL questions are excluded from the index.
"""

from ai_dev_system.debate.engine import run_debate
from ai_dev_system.debate.llm import StubDebateLLMClient
from ai_dev_system.debate.progress import DebateProgress
from ai_dev_system.debate.report import Question

REQUIRED_Q = Question(
    id="Q1", text="Which language?", classification="REQUIRED", domain="backend",
    agent_a="BackendArchitect", agent_b="ProductManager",
)
OPTIONAL_Q = Question(
    id="Q2", text="Which colour?", classification="OPTIONAL", domain="product",
    agent_a="ProductManager", agent_b="BackendArchitect",
)


class _Recorder(DebateProgress):
    def __init__(self):
        self.questions = None
        self.starts = []
        self.rounds = []

    def on_questions(self, total, required, strategic, optional):
        self.questions = (total, required, strategic, optional)

    def on_question_start(self, index, total, question):
        self.starts.append((index, total, question.id))

    def on_round(self, index, total, round_num, max_rounds, result, *, is_final):
        self.rounds.append((index, total, round_num, is_final))


def test_progress_events_emitted():
    rec = _Recorder()
    run_debate(
        [REQUIRED_Q, OPTIONAL_Q], StubDebateLLMClient(),
        run_id="r", brief={}, progress=rec,
    )
    # 2 questions total; OPTIONAL excluded from the debated index/total.
    assert rec.questions == (2, 1, 0, 1)
    assert rec.starts == [(1, 1, "Q1")]
    assert rec.rounds, "expected at least one round event"
    assert all(total == 1 for (_, total, _, _) in rec.rounds)
    assert rec.rounds[-1][3] is True, "the last round must be flagged is_final"


def test_no_progress_is_noop():
    # Omitting progress must not raise and must not change behaviour.
    report = run_debate([REQUIRED_Q], StubDebateLLMClient(), run_id="r", brief={})
    assert len(report.results) == 1
