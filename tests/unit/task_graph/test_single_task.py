import json

from ai_dev_system.task_graph.single_task import build_single_task, spec_single_task
from ai_dev_system.task_graph.facets import FACET_KEYS, SPEC_FACET_KEYS, EXEC_FACET_KEYS, is_implementation_task
from ai_dev_system.debate.llm import StubDebateLLMClient


class _FakeLLM:
    def __init__(self, response): self.response = response
    def complete(self, system, user): return self.response


def _all_filled():
    return json.dumps({k: {"status": "filled", "content": f"{k} c", "reason": ""} for k in SPEC_FACET_KEYS})


def test_build_single_task_is_minimal_coding_task():
    t = build_single_task("build a CSV importer")
    assert t["type"] == "coding" and t["execution_type"] == "atomic"
    assert t["objective"] == "build a CSV importer"
    assert is_implementation_task(t) is True


def test_build_single_task_title_derives_from_idea_when_absent():
    # Short idea (≤60 chars): title equals idea verbatim, no ellipsis
    assert build_single_task("build a CSV importer")["title"] == "build a CSV importer"
    # Long idea (>60 chars): first 60 chars (rstripped) + Unicode ellipsis U+2026
    long = "x" * 65
    assert build_single_task(long)["title"] == "x" * 60 + "…"
    # Explicit title overrides derivation
    assert build_single_task("x", title="My Task")["title"] == "My Task"


def test_spec_single_task_returns_task_and_eight_facets():
    result = spec_single_task("build a CSV importer", _FakeLLM(_all_filled()))
    assert set(result["facets"].keys()) == set(FACET_KEYS)
    assert result["task"]["facets"]["database"]["status"] == "filled"
    assert result["task"]["objective"] == "build a CSV importer"


def test_spec_single_task_stub_yields_all_needs_human():
    result = spec_single_task("build a CSV importer", StubDebateLLMClient())
    assert all(result["facets"][k]["status"] == "needs_human" for k in SPEC_FACET_KEYS)
    assert all(result["facets"][k]["status"] == "na" for k in EXEC_FACET_KEYS)


def test_spec_single_task_uses_agentic_when_repo_given(monkeypatch):
    # Pin critic OFF so this hermetic test never spawns a real `claude` CLI call.
    # The agentic path (llm=None) would otherwise try make_llm_client("critic") which
    # shells out via the project .env ClaudeCodeLLMClient when AI_DEV_SPEC_SELF_REVIEW=1.
    monkeypatch.setenv("AI_DEV_SPEC_SELF_REVIEW", "0")
    import ai_dev_system.task_graph.single_task as st
    called = {}
    def _fake_agentic(task, repo_path, **kw):
        called["repo"] = repo_path
        from ai_dev_system.task_graph.facets import FACET_KEYS
        return {k: {"status": "filled", "content": "x", "reason": ""} for k in FACET_KEYS}
    monkeypatch.setattr(st, "generate_task_facets_agentic", _fake_agentic)
    result = st.spec_single_task("add CSV import", None, repo_path="/some/repo")
    assert called["repo"] == "/some/repo"
    assert result["task"]["facets"]["input"]["status"] == "filled"


def test_spec_single_task_uses_text_path_when_no_repo():
    from ai_dev_system.debate.llm import StubDebateLLMClient
    from ai_dev_system.task_graph.facets import SPEC_FACET_KEYS, EXEC_FACET_KEYS
    result = spec_single_task("add CSV import", StubDebateLLMClient())  # no repo_path
    assert all(result["facets"][k]["status"] == "needs_human" for k in SPEC_FACET_KEYS)  # stub Mode A
    assert all(result["facets"][k]["status"] == "na" for k in EXEC_FACET_KEYS)
