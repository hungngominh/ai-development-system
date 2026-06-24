"""ClaudeMaxAgent executes a task by asking Claude (Max, via the unified LLM
client) for file contents as JSON, then writing them itself — deterministic and
sandboxed to output_path. No real `claude -p` calls here: a fake LLM is injected.
"""
import json

from ai_dev_system.agents.base import PromotedOutput
from ai_dev_system.agents.claude_max_agent import ClaudeMaxAgent


class _FakeLLM:
    def __init__(self, response: str):
        self._response = response
        self.calls: list[tuple[str, str]] = []

    def complete(self, system: str, user: str) -> str:
        self.calls.append((system, user))
        return self._response


def test_writes_files_and_succeeds(tmp_path):
    resp = json.dumps({"files": {"main.py": "print('hi')\n"}, "summary": "done"})
    agent = ClaudeMaxAgent(llm=_FakeLLM(resp))
    out = str(tmp_path / "out")

    res = agent.run(
        "TASK-1", out,
        promoted_outputs=[PromotedOutput("main.py", "CODE")],
        context={"objective": "build the thing"},
    )

    assert res.success, res.error
    assert (tmp_path / "out" / "main.py").read_text(encoding="utf-8") == "print('hi')\n"


def test_writes_into_subdirectories(tmp_path):
    resp = json.dumps({"files": {"pkg/mod.py": "x = 1\n"}})
    agent = ClaudeMaxAgent(llm=_FakeLLM(resp))
    res = agent.run("T", str(tmp_path / "o"), promoted_outputs=[PromotedOutput("pkg/mod.py", "CODE")])
    assert res.success, res.error
    assert (tmp_path / "o" / "pkg" / "mod.py").read_text(encoding="utf-8") == "x = 1\n"


def test_missing_promoted_output_errors(tmp_path):
    resp = json.dumps({"files": {"other.py": "x"}})
    agent = ClaudeMaxAgent(llm=_FakeLLM(resp))
    res = agent.run("T", str(tmp_path / "o"), promoted_outputs=[PromotedOutput("main.py", "CODE")])
    assert not res.success
    assert "missing" in res.error.lower()


def test_blocks_path_traversal(tmp_path):
    resp = json.dumps({"files": {"../evil.py": "x"}})
    agent = ClaudeMaxAgent(llm=_FakeLLM(resp))
    res = agent.run("T", str(tmp_path / "o"), promoted_outputs=[])
    assert not res.success
    assert "outside" in res.error.lower()
    assert not (tmp_path / "evil.py").exists()


def test_strips_json_fence(tmp_path):
    resp = "```json\n" + json.dumps({"files": {"a.txt": "hello"}}) + "\n```"
    agent = ClaudeMaxAgent(llm=_FakeLLM(resp))
    res = agent.run("T", str(tmp_path / "o"), promoted_outputs=[PromotedOutput("a.txt", "X")])
    assert res.success, res.error
    assert (tmp_path / "o" / "a.txt").read_text(encoding="utf-8") == "hello"


def test_non_json_response_errors(tmp_path):
    agent = ClaudeMaxAgent(llm=_FakeLLM("I cannot complete this task."))
    res = agent.run("T", str(tmp_path / "o"), promoted_outputs=[PromotedOutput("a", "X")])
    assert not res.success
    assert res.error


def test_llm_exception_becomes_error(tmp_path):
    class _Boom:
        def complete(self, system, user):
            raise RuntimeError("claude exited 1")

    res = ClaudeMaxAgent(llm=_Boom()).run("T", str(tmp_path / "o"), promoted_outputs=[])
    assert not res.success
    assert "claude exited 1" in res.error


def test_prompt_includes_input_artifact_content(tmp_path):
    # A resolved input artifact path whose file content should reach the prompt.
    art = tmp_path / "art"
    art.mkdir()
    (art / "spec.md").write_text("THE-SPEC-MARKER", encoding="utf-8")
    fake = _FakeLLM(json.dumps({"files": {"out.py": "ok"}}))
    agent = ClaudeMaxAgent(llm=fake)
    agent.run(
        "T", str(tmp_path / "o"),
        promoted_outputs=[PromotedOutput("out.py", "CODE")],
        context={"required_inputs": [{"name": "spec", "path": str(art)}]},
    )
    _, user = fake.calls[0]
    assert "THE-SPEC-MARKER" in user
