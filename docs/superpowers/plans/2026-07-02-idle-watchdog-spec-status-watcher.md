# Idle-Watchdog Timeout + SpecStatusWatcher Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Claude CLI invocations are killed only when they *stall* (no NDJSON event for N seconds), not when total work exceeds a fixed wall-clock budget; and the Telegram bot proactively pushes ❌/📄 when a spec worker reaches a terminal state instead of staying silent until asked.

**Architecture:** Extend the shared `_invoke_claude` (agents/repo_branch_agent.py) with an idle watchdog over the NDJSON event stream and a `flags` parameter, switch both `_EXEC_FLAGS` and the spec agentic path to `--output-format stream-json --verbose`, and have `facets_agentic` call `_invoke_claude` instead of blocking `subprocess.run`. Add `SpecStatusWatcher` next to `ClarifyWatcher` in the gateway daemon sweep; both it and `dev_pipeline` render gate messages from a new shared `spec_messages` module.

**Tech Stack:** Python 3.12+, subprocess/threading, pytest. No new dependencies.

## Global Constraints

- Verified against claude CLI 2.1.196 in the gateway container: `-p --output-format stream-json --verbose` emits one NDJSON event per line (init event arrives immediately; `tool_use` blocks are nested inside `type=="assistant"` events at `message.content[].type=="tool_use"`).
- Env knobs (all optional): `SPEC_IDLE_TIMEOUT` (default 180 s), `SPEC_HARD_TIMEOUT` (default 3600 s), `EXEC_IDLE_TIMEOUT` (default 180 s). Existing `SPEC_MAX_TURNS` (40) and `EXEC_MAX_TURNS` (100) unchanged.
- Watcher rules (mirror ClarifyWatcher): never call an LLM on the daemon thread; one bad record never kills the sweep; at-least-once delivery (push, then flip phase).
- All user-facing chat strings are Vietnamese, matching existing dev_pipeline texts exactly (they are asserted in tests).
- Existing test seams must keep working: tests patch `ai_dev_system.agents.repo_branch_agent.subprocess.Popen` with fakes whose `wait(timeout=None)` returns immediately — the new wait loop must treat a returning `wait()` as process exit.

---

### Task 1: Idle watchdog in `_invoke_claude` + stream-json flags

**Files:**
- Modify: `src/ai_dev_system/agents/repo_branch_agent.py` (lines 24-27 `_EXEC_FLAGS`, 47-69 `_parse_ndjson_event`, 191-199 `_ClaudeRun`, 213-280 `_invoke_claude`, 365-372 and 437 callers)
- Modify: `src/ai_dev_system/agents/review_agent.py:157` (caller)
- Modify: `src/ai_dev_system/agents/test_author_agent.py:149,202` (callers)
- Test: `tests/unit/agents/test_repo_branch_agent.py`

**Interfaces:**
- Produces: `_invoke_claude(claude, cwd, prompt, max_turns, timeout_s, live_log_path=None, model=None, effort=None, flags=None, idle_timeout_s=None) -> _ClaudeRun` where `flags: Optional[list[str]]` (None → `_EXEC_FLAGS`) and `_ClaudeRun` gains `timeout_kind: str` (`""` | `"idle"` | `"hard"`). Produces `_exec_idle_timeout() -> float`. Task 2 imports both.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/agents/test_repo_branch_agent.py` (it already imports `json`, `patch`, `_max_turns`; add imports as shown):

```python
# ── idle watchdog / _invoke_claude ────────────────────────────────────────────
import subprocess as _sp

from ai_dev_system.agents.repo_branch_agent import (
    _invoke_claude, _parse_ndjson_event, _exec_idle_timeout, _EXEC_FLAGS,
)
import ai_dev_system.agents.repo_branch_agent as rba


class _HangingPopen:
    """Never exits on its own; emits no events. wait() raises TimeoutExpired
    until kill() is called (mirrors a stalled real CLI)."""
    def __init__(self, cmd, **kw):
        self.cmd = cmd
        self.returncode = None
        self.stdout = iter([])
        self.stderr = iter([])
        self._killed = False
    def wait(self, timeout=None):
        if self._killed:
            self.returncode = -9
            return
        raise _sp.TimeoutExpired(cmd="claude", timeout=timeout)
    def kill(self):
        self._killed = True


def test_invoke_claude_idle_timeout_kills_stalled_cli(monkeypatch):
    monkeypatch.setattr(rba, "_POLL_INTERVAL_S", 0.01)
    with patch("ai_dev_system.agents.repo_branch_agent.subprocess.Popen", _HangingPopen):
        run = _invoke_claude("claude", ".", "p", 10, timeout_s=9999,
                             idle_timeout_s=0.05)
    assert run.timed_out and run.timeout_kind == "idle"


def test_invoke_claude_hard_timeout_kind(monkeypatch):
    monkeypatch.setattr(rba, "_POLL_INTERVAL_S", 0.01)
    with patch("ai_dev_system.agents.repo_branch_agent.subprocess.Popen", _HangingPopen):
        run = _invoke_claude("claude", ".", "p", 10, timeout_s=0.05,
                             idle_timeout_s=None)
    assert run.timed_out and run.timeout_kind == "hard"


def test_invoke_claude_custom_flags_replace_exec_flags():
    seen = {}
    def _capture(cmd, **kw):
        seen["cmd"] = cmd
        return _HangingPopen(cmd)  # any Popen; we only need the cmd
    class _DonePopen(_HangingPopen):
        def wait(self, timeout=None):
            self.returncode = 0
    def _done(cmd, **kw):
        seen["cmd"] = cmd
        return _DonePopen(cmd)
    with patch("ai_dev_system.agents.repo_branch_agent.subprocess.Popen", side_effect=_done):
        _invoke_claude("claude", ".", "p", 7, timeout_s=5,
                       flags=["--output-format", "stream-json", "--verbose", "--x"])
    cmd = seen["cmd"]
    assert "--x" in cmd
    assert "--permission-mode" not in cmd          # _EXEC_FLAGS NOT used
    assert cmd[cmd.index("--max-turns") + 1] == "7"


def test_exec_flags_use_stream_json():
    assert "stream-json" in _EXEC_FLAGS and "--verbose" in _EXEC_FLAGS


def test_exec_idle_timeout_env(monkeypatch):
    monkeypatch.delenv("EXEC_IDLE_TIMEOUT", raising=False)
    assert _exec_idle_timeout() == 180.0
    monkeypatch.setenv("EXEC_IDLE_TIMEOUT", "60")
    assert _exec_idle_timeout() == 60.0
    monkeypatch.setenv("EXEC_IDLE_TIMEOUT", "junk")
    assert _exec_idle_timeout() == 180.0


def test_parse_ndjson_assistant_tool_use_unwrapped():
    line = json.dumps({"type": "assistant", "message": {"content": [
        {"type": "tool_use", "name": "Read", "input": {"file_path": "a.py"}},
        {"type": "text", "text": "reading"},
    ]}})
    assert _parse_ndjson_event(line) == "[tool] Read: a.py"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/agents/test_repo_branch_agent.py -q`
Expected: ImportError (`_exec_idle_timeout`, `_POLL_INTERVAL_S` don't exist) / assertion failures on stream-json.

- [ ] **Step 3: Implement in `repo_branch_agent.py`**

Replace `_EXEC_FLAGS` (lines 24-27):

```python
# Static claude CLI flags. --max-turns is appended at run() time so it can be
# tuned per-environment (see _max_turns). stream-json (NDJSON, one event per
# line) feeds both the live log and the idle watchdog in _invoke_claude;
# --verbose is required by the CLI for stream-json in -p mode.
_EXEC_FLAGS = [
    "--output-format", "stream-json", "--verbose",
    "--permission-mode", "bypassPermissions",
]
```

After `_max_turns()` add:

```python
# Seconds between liveness checks while waiting on the CLI. Module-level so
# tests can shrink it.
_POLL_INTERVAL_S = 1.0


def _exec_idle_timeout() -> float:
    """Idle watchdog budget: kill claude only when NO NDJSON event has arrived
    for this many seconds (EXEC_IDLE_TIMEOUT, default 180). A healthy run is
    bounded by liveness, not total wall-clock — timeout_s stays as a very high
    safety ceiling against infinite loops."""
    raw = os.environ.get("EXEC_IDLE_TIMEOUT")
    if not raw:
        return 180.0
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return 180.0
    return v if v > 0 else 180.0
```

In `_parse_ndjson_event`, after the `tool_use` branch, add unwrapping for stream-json's nested layout:

```python
    if event_type == "assistant":
        blocks = ((obj.get("message") or {}).get("content")) or []
        parts = []
        for b in blocks:
            if isinstance(b, dict) and b.get("type") == "tool_use":
                name = b.get("name", "?")
                parts.append(f"[tool] {name}{_summarize_tool_input(name, b.get('input') or {})}")
        return "\n".join(parts) if parts else None
```

In `_ClaudeRun` add field:

```python
    timed_out: bool = False
    timeout_kind: str = ""   # "" | "idle" | "hard"
```

Rewrite `_invoke_claude`:

```python
def _invoke_claude(
    claude: str,
    cwd: str,
    prompt: str,
    max_turns: int,
    timeout_s: float,
    live_log_path: Optional[Path] = None,
    model: Optional[str] = None,
    effort: Optional[str] = None,
    flags: Optional[list] = None,
    idle_timeout_s: Optional[float] = None,
) -> _ClaudeRun:
    """Run `claude -p` once, streaming NDJSON events to the live log. Shared by
    RepoBranchAgent (implement/fix), ReviewAgent (review) and the spec agentic
    path (facets_agentic, via flags=_READONLY_FLAGS).

    Liveness beats wall-clock: when idle_timeout_s is set, the process is
    killed only if no stdout line has arrived for that long; timeout_s is a
    high safety ceiling against infinite loops, not a work budget."""
    cmd = [claude, "-p", prompt, *(_EXEC_FLAGS if flags is None else flags),
           "--max-turns", str(max_turns)]
    if model:
        cmd += ["--model", model]
    if effort:
        cmd += ["--effort", effort]
    proc = subprocess.Popen(
        cmd, cwd=cwd,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
    )

    all_stdout: list[str] = []
    stderr_lines: list[str] = []
    last_event = [time.monotonic()]   # mutable holder shared with drain thread

    def _drain_stdout():
        for line in proc.stdout:
            last_event[0] = time.monotonic()
            all_stdout.append(line)
            if live_log_path:
                msg = _parse_ndjson_event(line.strip())
                if msg:
                    _append_log(live_log_path, msg)

    def _drain_stderr():
        for line in proc.stderr:
            stderr_lines.append(line)

    stdout_thread = threading.Thread(target=_drain_stdout, daemon=True)
    stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
    stdout_thread.start()
    stderr_thread.start()

    timed_out = False
    timeout_kind = ""
    deadline = time.monotonic() + float(timeout_s)
    while True:
        try:
            proc.wait(timeout=_POLL_INTERVAL_S)
            break                                   # process exited
        except subprocess.TimeoutExpired:
            now = time.monotonic()
            if now >= deadline:
                timeout_kind = "hard"
            elif idle_timeout_s and now - last_event[0] >= float(idle_timeout_s):
                timeout_kind = "idle"
            else:
                continue
            timed_out = True
            proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
            break
    stdout_thread.join(timeout=5)
    stderr_thread.join(timeout=5)

    stdout = "".join(all_stdout)
    stderr = "".join(stderr_lines)
    ev = _extract_result_event(stdout)
    rc = proc.returncode if proc.returncode is not None else -1
    return _ClaudeRun(
        returncode=rc, stdout=stdout, stderr=stderr,
        result_event=ev, subtype=(ev or {}).get("subtype") or "",
        timed_out=timed_out, timeout_kind=timeout_kind,
    )
```

Update every `_invoke_claude(...)` caller to pass the idle budget — in `repo_branch_agent.py` (two calls, at current lines ~372 and ~437), `test_author_agent.py` (two calls), `review_agent.py` (one call), append the kwarg:

```python
            idle_timeout_s=_exec_idle_timeout(),
```

(`test_author_agent.py` already imports from `repo_branch_agent` — extend that import with `_exec_idle_timeout`; `review_agent.py` likewise.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/agents/ -q`
Expected: all PASS (existing FakePopen fakes exit via a returning `wait()`, which the new loop treats as process exit).

- [ ] **Step 5: Commit**

```bash
git add src/ai_dev_system/agents/ tests/unit/agents/test_repo_branch_agent.py
git commit -m "feat(agents): idle watchdog + stream-json for _invoke_claude (EXEC_IDLE_TIMEOUT)"
```

---

### Task 2: Spec agentic path uses `_invoke_claude` with idle watchdog

**Files:**
- Modify: `src/ai_dev_system/task_graph/facets_agentic.py` (flags, resolvers, `generate_task_facets_agentic`; delete `_build_command`)
- Modify: `src/ai_dev_system/task_graph/single_task.py:33-47` (thread `live_log_path`)
- Test: `tests/unit/task_graph/test_facets_agentic.py` (rewrite fakes)

**Interfaces:**
- Consumes: `_invoke_claude`, `_ClaudeRun` from Task 1.
- Produces: `generate_task_facets_agentic(task, repo_path, *, model=None, live_log_path=None, invoke=None, log=None)` — the `run=`/`timeout=` params are REMOVED; tests inject `invoke` (a `_invoke_claude`-shaped callable). `spec_single_task(idea, llm, *, title=None, repo_path=None, log=None, live_log_path=None)`. Produces `_spec_idle_timeout() -> float`, `_spec_hard_timeout() -> float` (Task 3 imports them for the log line).

- [ ] **Step 1: Rewrite the test file's fake layer and add new tests**

In `tests/unit/task_graph/test_facets_agentic.py`: replace the imports, `_wrapper`, `_FakeRun`, `_cp` helpers with:

```python
import json
import pytest

from ai_dev_system.agents.repo_branch_agent import _ClaudeRun
from ai_dev_system.task_graph.facets_agentic import (
    generate_task_facets_agentic, _spec_max_turns, _spec_idle_timeout,
    _spec_hard_timeout, _READONLY_FLAGS,
)
from ai_dev_system.task_graph.facets import FACET_KEYS, SPEC_FACET_KEYS, EXEC_FACET_KEYS


def _ok_run(inner: str, subtype="success") -> _ClaudeRun:
    return _ClaudeRun(returncode=0, stdout="", stderr="",
                      result_event={"type": "result", "subtype": subtype, "result": inner},
                      subtype=subtype)


class _FakeInvoke:
    def __init__(self, run): self.run = run; self.calls = []
    def __call__(self, *a, **kw): self.calls.append((a, kw)); return self.run
```

Update every existing test: where it did `run = _FakeRun(_cp(stdout=_wrapper(inner)))` + `generate_task_facets_agentic(_task(), str(tmp_path), run=run)`, use `inv = _FakeInvoke(_ok_run(inner))` + `generate_task_facets_agentic(_task(), str(tmp_path), invoke=inv)`. The `test_extract_text_messages_fallback` test becomes: `_ClaudeRun(returncode=0, stdout=json.dumps({"messages": [{"role": "assistant", "content": inner}]}), stderr="", result_event=None, subtype="")` — exercises the stdout fallback. `test_build_command_includes_model_when_given` is DELETED (model now flows through `_invoke_claude`); replace with an invoke-kwargs assertion:

```python
def test_invoke_receives_flags_turns_and_timeouts(tmp_path, monkeypatch):
    monkeypatch.delenv("SPEC_MAX_TURNS", raising=False)
    monkeypatch.delenv("SPEC_IDLE_TIMEOUT", raising=False)
    monkeypatch.delenv("SPEC_HARD_TIMEOUT", raising=False)
    inv = _FakeInvoke(_ok_run(_ok_inner()))
    generate_task_facets_agentic(_task(), str(tmp_path), model="opus", invoke=inv)
    a, kw = inv.calls[0]
    assert a[3] == 40                          # max_turns positional
    assert a[4] == 3600.0                      # timeout_s (hard ceiling)
    assert kw["idle_timeout_s"] == 180.0
    assert kw["flags"] == _READONLY_FLAGS
    assert kw["model"] == "opus"
```

Replace the three `_build_command` max-turns tests (added 2026-07-02) with resolver tests:

```python
def test_spec_max_turns_env(monkeypatch):
    monkeypatch.delenv("SPEC_MAX_TURNS", raising=False)
    assert _spec_max_turns() == 40
    monkeypatch.setenv("SPEC_MAX_TURNS", "25")
    assert _spec_max_turns() == 25
    monkeypatch.setenv("SPEC_MAX_TURNS", "zero")
    assert _spec_max_turns() == 40


def test_spec_timeout_resolvers_env(monkeypatch):
    monkeypatch.delenv("SPEC_IDLE_TIMEOUT", raising=False)
    monkeypatch.delenv("SPEC_HARD_TIMEOUT", raising=False)
    assert _spec_idle_timeout() == 180.0 and _spec_hard_timeout() == 3600.0
    monkeypatch.setenv("SPEC_IDLE_TIMEOUT", "300")
    monkeypatch.setenv("SPEC_HARD_TIMEOUT", "7200")
    assert _spec_idle_timeout() == 300.0 and _spec_hard_timeout() == 7200.0
    monkeypatch.setenv("SPEC_IDLE_TIMEOUT", "-1")
    assert _spec_idle_timeout() == 180.0
```

Add timeout/readonly/streaming behavior tests:

```python
def test_idle_timeout_raises_with_knob_name(tmp_path):
    timed = _ClaudeRun(returncode=-1, stdout="", stderr="", result_event=None,
                       subtype="", timed_out=True, timeout_kind="idle")
    with pytest.raises(RuntimeError, match="SPEC_IDLE_TIMEOUT"):
        generate_task_facets_agentic(_task(), str(tmp_path), invoke=_FakeInvoke(timed))


def test_hard_timeout_raises_with_knob_name(tmp_path):
    timed = _ClaudeRun(returncode=-1, stdout="", stderr="", result_event=None,
                       subtype="", timed_out=True, timeout_kind="hard")
    with pytest.raises(RuntimeError, match="SPEC_HARD_TIMEOUT"):
        generate_task_facets_agentic(_task(), str(tmp_path), invoke=_FakeInvoke(timed))


def test_readonly_flags_are_stream_json_and_no_write_tools():
    assert "stream-json" in _READONLY_FLAGS and "--verbose" in _READONLY_FLAGS
    assert "Edit" in _READONLY_FLAGS and "Write" in _READONLY_FLAGS  # still disallowed
```

Keep the existing failure-path test that asserts rc != 0 raises (adapt to `_ClaudeRun(returncode=1, stdout="", stderr="boom", result_event=None, subtype="")`).

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/task_graph/test_facets_agentic.py -q`
Expected: ImportError (`_spec_idle_timeout` etc. don't exist).

- [ ] **Step 3: Implement `facets_agentic.py`**

Replace `_READONLY_FLAGS` + `_spec_max_turns` block with:

```python
# Read-only, non-interactive flags. stream-json (one NDJSON event per line)
# feeds the idle watchdog in _invoke_claude — a stalled CLI dies after
# SPEC_IDLE_TIMEOUT of silence instead of a fixed total budget; --verbose is
# required by the CLI for stream-json in -p mode. --max-turns is appended by
# _invoke_claude (see SPEC_MAX_TURNS).
_READONLY_FLAGS = [
    "--output-format", "stream-json", "--verbose",
    "--permission-mode", "bypassPermissions",
    "--disallowedTools", "Edit", "Write", "Bash", "PowerShell", "WebFetch", "WebSearch",
]

_DEFAULT_SPEC_MAX_TURNS = 40
_DEFAULT_SPEC_IDLE_TIMEOUT = 180.0
_DEFAULT_SPEC_HARD_TIMEOUT = 3600.0


def _spec_max_turns() -> int:
    """Resolve the claude --max-turns budget from SPEC_MAX_TURNS (fallback 40)."""
    raw = os.environ.get("SPEC_MAX_TURNS")
    if raw is None:
        return _DEFAULT_SPEC_MAX_TURNS
    try:
        n = int(raw)
    except ValueError:
        return _DEFAULT_SPEC_MAX_TURNS
    return n if n > 0 else _DEFAULT_SPEC_MAX_TURNS


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if not raw:
        return default
    try:
        v = float(raw)
    except (TypeError, ValueError):
        return default
    return v if v > 0 else default


def _spec_idle_timeout() -> float:
    """Kill claude only after this many seconds WITHOUT a new NDJSON event
    (SPEC_IDLE_TIMEOUT, default 180). Liveness, not total work, is the bound —
    a large repo may legitimately take 15+ minutes of active reading."""
    return _float_env("SPEC_IDLE_TIMEOUT", _DEFAULT_SPEC_IDLE_TIMEOUT)


def _spec_hard_timeout() -> float:
    """Safety ceiling against infinite loops (SPEC_HARD_TIMEOUT, default 3600)."""
    return _float_env("SPEC_HARD_TIMEOUT", _DEFAULT_SPEC_HARD_TIMEOUT)
```

Delete `_build_command`. Rewrite `generate_task_facets_agentic` (keep `_build_prompt`, `_find_json_block`, `_extract_text`, and the facet-coercion tail exactly as they are):

```python
def generate_task_facets_agentic(
    task: dict,
    repo_path: str,
    *,
    model: str | None = None,
    live_log_path=None,
    invoke=None,
    log=None,
) -> dict[str, dict]:
    """20 facets grounded in the repo at `repo_path`, via read-only `claude -p`
    (streamed through the shared _invoke_claude with an idle watchdog).

    Raises on failure so callers can surface the error. Use _all_needs_human()
    at the call site when a silent fallback is wanted.
    live_log_path: NDJSON tool events are appended here (the spec .log file).
    invoke: test seam — an _invoke_claude-shaped callable.
    log: optional callable(str) for progress/diagnostic lines.
    """
    def _log(msg):
        if log:
            log(msg)

    if not repo_path or not os.path.isdir(repo_path):
        raise ValueError(f"repo_path không hợp lệ hoặc không tồn tại: {repo_path!r}")
    claude = ClaudeCodeLLMClient._resolve_claude_cmd()
    if invoke is None:
        from ai_dev_system.agents.repo_branch_agent import _invoke_claude as invoke
    idle_s, hard_s = _spec_idle_timeout(), _spec_hard_timeout()
    run = invoke(
        claude, repo_path, _build_prompt(task), _spec_max_turns(), hard_s,
        live_log_path=live_log_path, model=model,
        flags=_READONLY_FLAGS, idle_timeout_s=idle_s,
    )
    if run.timed_out:
        if run.timeout_kind == "idle":
            raise RuntimeError(
                f"claude CLI treo: không có event mới trong {int(idle_s)}s "
                f"(SPEC_IDLE_TIMEOUT)")
        raise RuntimeError(
            f"claude CLI vượt trần an toàn {int(hard_s)}s (SPEC_HARD_TIMEOUT)")
    _log(f"claude CLI xong: rc={run.returncode} "
         f"stdout={len(run.stdout)}B stderr={len(run.stderr)}B")
    if run.returncode != 0:
        kind = f" ({run.subtype})" if run.subtype else ""
        raise RuntimeError(
            f"claude CLI trả về code {run.returncode}{kind}. "
            f"stderr: {run.stderr[:400]!r}. stdout: {run.stdout[:200]!r}"
        )
    raw = (run.result_event or {}).get("result") or ""
    if not raw:
        stdout = run.stdout or run.stderr
        try:
            raw = _extract_text(stdout)
        except (json.JSONDecodeError, ValueError):
            raw = stdout
    text = ClaudeCodeLLMClient._strip_outer_code_fence(raw)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        text = _find_json_block(raw)
        ...  # keep the existing fallback/parse/coercion code from here on, unchanged
```

(The `...` above means: the existing body from the inner `try` onward is kept verbatim — only the invocation + raw-text acquisition changed. `subprocess` import can be dropped if now unused.)

In `single_task.py` update `spec_single_task`:

```python
def spec_single_task(idea: str, llm, *, title: str | None = None,
                     repo_path: str | None = None, log=None,
                     live_log_path=None) -> dict:
```

and the call: `facets = generate_task_facets_agentic(task, repo_path, log=log, live_log_path=live_log_path)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/task_graph/ -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ai_dev_system/task_graph/facets_agentic.py src/ai_dev_system/task_graph/single_task.py tests/unit/task_graph/test_facets_agentic.py
git commit -m "feat(spec): agentic facets via shared _invoke_claude — idle watchdog replaces 300s total timeout"
```

---

### Task 3: Worker streams tool events into the spec log

**Files:**
- Modify: `src/ai_dev_system/task_graph/single_task_worker.py:97-108` (agentic branch + log line)
- Test: `tests/unit/task_graph/test_single_task_worker.py`

**Interfaces:**
- Consumes: `spec_single_task(..., live_log_path=...)` from Task 2; `_spec_idle_timeout`, `_spec_hard_timeout` from `facets_agentic`.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/task_graph/test_single_task_worker.py`:

```python
def test_run_worker_passes_live_log_path_and_logs_timeouts(tmp_path, monkeypatch, file_db_url):
    seen = {}
    def _fake_spec(idea, llm, *, repo_path=None, log=None, live_log_path=None):
        seen["live_log_path"] = live_log_path
        return {"task": {"title": "T"}, "facets": {}}
    monkeypatch.setattr(w, "spec_single_task", _fake_spec)
    monkeypatch.setattr(w, "publish_doc", lambda *a, **k: "https://x/blob/b/s.md")
    w.run_worker("speclog1", "idea", "/some/repo",
                 storage_root=str(tmp_path), database_url=file_db_url)
    assert seen["live_log_path"] == tmp_path / "task_specs" / "speclog1.log"
    log_text = (tmp_path / "task_specs" / "speclog1.log").read_text(encoding="utf-8")
    assert "300s" not in log_text          # stale fixed-budget message removed
    assert "idle" in log_text              # announces the idle-based budget
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/task_graph/test_single_task_worker.py -q`
Expected: FAIL — `_fake_spec` never receives `live_log_path`; log still says "tối đa 300s".

- [ ] **Step 3: Implement in `single_task_worker.py`**

Add import: `from ai_dev_system.task_graph.facets_agentic import _spec_idle_timeout, _spec_hard_timeout`.

Replace the agentic branch of `run_worker` (currently lines 97-99 + the `spec_single_task` call):

```python
        if repo:
            _spec_log(log_path, f"Chế độ: agentic — đọc repo tại {repo}")
            _spec_log(log_path,
                      f"Đang chạy claude CLI (đọc code + sinh {len(_SPEC_KEYS)} spec facets; "
                      f"idle timeout {int(_spec_idle_timeout())}s, "
                      f"trần an toàn {int(_spec_hard_timeout())}s)…")
            llm = None
        else:
            ...
        result = spec_single_task(idea, llm, repo_path=repo,
                                  log=lambda msg: _spec_log(log_path, msg),
                                  live_log_path=log_path)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/task_graph/ -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ai_dev_system/task_graph/single_task_worker.py tests/unit/task_graph/test_single_task_worker.py
git commit -m "feat(spec): stream claude tool events into the spec log; announce idle budget"
```

---

### Task 4: Shared spec gate/error message helpers

**Files:**
- Create: `src/ai_dev_system/task_graph/spec_messages.py`
- Modify: `src/ai_dev_system/harness/tools/dev_pipeline.py` (spec-error + spec-gate blocks in `dev_run_status`/progress path, ~lines 250-283, and the error guard in `dev_answer_gate`, ~line 409)
- Test: `tests/unit/task_graph/test_spec_messages.py` (new)

**Interfaces:**
- Produces: `spec_error_message(spec: dict) -> str` and `spec_gate_message(spec: dict) -> str` — the exact strings currently inlined in dev_pipeline. Task 5's watcher consumes both.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/task_graph/test_spec_messages.py`:

```python
from ai_dev_system.task_graph.spec_messages import spec_error_message, spec_gate_message


def test_error_message_truncates_and_prompts_retry():
    msg = spec_error_message({"status": "error", "error": "boom " * 200})
    assert msg.startswith("❌ Tạo spec thất bại: ")
    assert "Nhắn lại nội dung task để thử lại." in msg
    assert len(msg) < 400


def test_gate_message_with_doc_url():
    msg = spec_gate_message({"status": "done", "spec_doc_url": "https://x/blob/b/s.md"})
    assert "📄 Spec sẵn sàng." in msg and "https://x/blob/b/s.md" in msg
    assert "Nhắn 'duyệt' để tạo plan." in msg


def test_gate_message_publish_failed_warns():
    msg = spec_gate_message({"status": "done", "doc_publish_failed": True})
    assert "⚠️" in msg and "git credentials" in msg


def test_gate_message_plain():
    msg = spec_gate_message({"status": "done"})
    assert msg == "📄 Spec sẵn sàng.\nNhắn 'duyệt' để tạo plan."
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/task_graph/test_spec_messages.py -q`
Expected: ModuleNotFoundError.

- [ ] **Step 3: Create `spec_messages.py` and use it in dev_pipeline**

```python
# src/ai_dev_system/task_graph/spec_messages.py
"""User-facing chat strings for the spec gate — shared by the gateway's
dev_pipeline tools (pull: user asks for progress) and SpecStatusWatcher
(push: daemon announces terminal states). One source of truth so both
surfaces always say the same thing."""
from __future__ import annotations


def spec_error_message(spec: dict) -> str:
    err = str(spec.get("error") or "")[:300]
    return f"❌ Tạo spec thất bại: {err}\nNhắn lại nội dung task để thử lại."


def spec_gate_message(spec: dict) -> str:
    url = spec.get("spec_doc_url")
    link = f"\n📄 Spec: {url}" if url else ""
    if spec.get("doc_publish_failed"):
        link = ("\n⚠️ Không push được spec doc lên repo (kiểm tra "
                "git credentials trong container) — file chỉ có ở bản clone local.")
    return f"📄 Spec sẵn sàng.{link}\nNhắn 'duyệt' để tạo plan."
```

In `dev_pipeline.py` add `from ai_dev_system.task_graph.spec_messages import spec_error_message, spec_gate_message` and replace the three inlined blocks:

- progress error branch → `return {"content": [{"type": "text", "text": spec_error_message(spec)}]}` (keep the `chat_task_store.clear` above it);
- progress spec-gate branch → delete the `url`/`link` lines, keep `chat_task_store.update(...)`, return `spec_gate_message(spec)`;
- `dev_answer_gate` error guard → keep clear, return `"❌ Spec thất bại, không thể duyệt: ...` — replace with `spec_error_message(spec)` too (the previous wording difference carries no information; tests only assert "❌"/"thất bại").

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/task_graph/test_spec_messages.py tests/unit/harness/test_dev_task_tools.py -q`
Expected: PASS (dev_task_tools asserts ❌/⚠️/duyệt substrings, all preserved).

- [ ] **Step 5: Commit**

```bash
git add src/ai_dev_system/task_graph/spec_messages.py src/ai_dev_system/harness/tools/dev_pipeline.py tests/unit/task_graph/test_spec_messages.py
git commit -m "refactor(spec): extract shared spec gate/error chat strings"
```

---

### Task 5: SpecStatusWatcher — proactive terminal-state push

**Files:**
- Create: `src/ai_dev_system/gateway/spec_status_watcher.py`
- Test: `tests/unit/gateway/test_spec_status_watcher.py` (new; mirrors test_clarify_watcher.py fakes)

**Interfaces:**
- Consumes: `ChatTaskStore` (phases: `"generating"` initial, `"awaiting_spec_approval"` after push), `spec_error_message`/`spec_gate_message` from Task 4.
- Produces: `SpecStatusWatcher(chat_task_store, platforms_by_name, session_store, storage_root)` with `check_once() -> int`. Task 6 wires it.

- [ ] **Step 1: Write the failing tests**

Create `tests/unit/gateway/test_spec_status_watcher.py`:

```python
import json
from pathlib import Path

from ai_dev_system.gateway.spec_status_watcher import SpecStatusWatcher
from ai_dev_system.harness.tools.chat_task_store import ChatTaskStore


class FakePlatform:
    def __init__(self): self.sent = []
    def reply(self, chat_id, text): self.sent.append((chat_id, text))


class FakeSessions:
    def __init__(self): self.appended = []
    def load_or_create(self, surface, chat_id): return f"sid-{surface}-{chat_id}"
    def append(self, sid, role, content): self.appended.append((sid, role, content))


def _write_spec(root, spec_id, payload):
    d = Path(root) / "task_specs"; d.mkdir(parents=True, exist_ok=True)
    (d / f"{spec_id}.json").write_text(json.dumps(payload), encoding="utf-8")


def _store(tmp_path, **over):
    s = ChatTaskStore(str(tmp_path))
    s.set_pending("Sigo", "5913", spec_id="ab", repo="/r", base_branch="main", idea="add X")
    if over:
        s.update("Sigo", "5913", **over)
    return s


def _watcher(tmp_path, store, plat, sess=None):
    return SpecStatusWatcher(store, {"Sigo": plat}, sess or FakeSessions(),
                             str(tmp_path))


def test_pushes_error_and_clears_pending(tmp_path):
    s = _store(tmp_path)
    _write_spec(tmp_path, "ab", {"status": "error", "error": "TimeoutExpired: 300s"})
    plat = FakePlatform(); sess = FakeSessions()
    w = _watcher(tmp_path, s, plat, sess)
    assert w.check_once() == 1
    assert "❌" in plat.sent[0][1] and "TimeoutExpired" in plat.sent[0][1]
    assert sess.appended and sess.appended[0][1] == "assistant"
    assert s.get_pending("Sigo", "5913") is None          # cleared → retry possible
    assert w.check_once() == 0                            # no re-push


def test_pushes_spec_ready_once_and_flips_phase(tmp_path):
    s = _store(tmp_path)
    _write_spec(tmp_path, "ab", {"status": "done",
                                 "spec_doc_url": "https://x/blob/b/s.md",
                                 "clarify": {"needed": False, "questions": []}})
    plat = FakePlatform()
    w = _watcher(tmp_path, s, plat)
    assert w.check_once() == 1
    assert "📄 Spec sẵn sàng." in plat.sent[0][1] and "https://x" in plat.sent[0][1]
    assert s.get_pending("Sigo", "5913")["phase"] == "awaiting_spec_approval"
    assert w.check_once() == 0                            # dedup: phase moved on
    assert len(plat.sent) == 1


def test_leaves_clarify_needed_to_clarify_watcher(tmp_path):
    s = _store(tmp_path)
    _write_spec(tmp_path, "ab", {"status": "done",
                                 "clarify": {"needed": True, "questions": ["Q?"]}})
    plat = FakePlatform()
    assert _watcher(tmp_path, s, plat).check_once() == 0 and plat.sent == []


def test_silent_while_worker_running_or_phase_advanced(tmp_path):
    s = _store(tmp_path)                                   # no spec json yet
    plat = FakePlatform()
    w = _watcher(tmp_path, s, plat)
    assert w.check_once() == 0 and plat.sent == []
    _write_spec(tmp_path, "ab", {"status": "done", "clarify": {"needed": False}})
    s.update("Sigo", "5913", phase="awaiting_spec_approval")   # progress tool got there first
    assert w.check_once() == 0 and plat.sent == []


def test_unregistered_surface_is_skipped(tmp_path):
    s = _store(tmp_path)
    _write_spec(tmp_path, "ab", {"status": "error", "error": "x"})
    w = SpecStatusWatcher(s, {}, FakeSessions(), str(tmp_path))
    assert w.check_once() == 0
    assert s.get_pending("Sigo", "5913") is not None       # NOT cleared without delivery
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/gateway/test_spec_status_watcher.py -q`
Expected: ModuleNotFoundError.

- [ ] **Step 3: Create `spec_status_watcher.py`**

```python
# src/ai_dev_system/gateway/spec_status_watcher.py
"""SpecStatusWatcher — proactive push when a spec worker reaches a terminal state.

Swept once per daemon poll loop, alongside RunStatusWatcher and ClarifyWatcher.
For each pending chat record still in phase='generating' whose spec JSON now
exists:
- status=='error'                → push ❌ (real error) and CLEAR the record
- status=='done', clarify needed → skip (ClarifyWatcher owns that push)
- status=='done' otherwise       → push the spec-gate message and flip the
  record to phase='awaiting_spec_approval' (same transition dev_task_progress
  makes when the user asks — whichever runs first wins; the other is a no-op).

Reads JSON + sends only — never calls an LLM (single-threaded daemon loop).
One bad record never kills the sweep. Delivery is at-least-once: push happens
before the phase flip / clear, so a crash in between re-sends next sweep."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from ai_dev_system.task_graph.spec_messages import spec_error_message, spec_gate_message

logger = logging.getLogger(__name__)


class SpecStatusWatcher:
    def __init__(self, chat_task_store, platforms_by_name: dict, session_store,
                 storage_root: str) -> None:
        self._store = chat_task_store
        self._platforms = platforms_by_name
        self._sessions = session_store
        self._specs_dir = Path(storage_root) / "task_specs"

    def check_once(self) -> int:
        pushed = 0
        for rec in self._store.list_pending():
            try:
                pushed += self._check(rec)
            except Exception:  # noqa: BLE001 — one bad record never kills the sweep
                logger.exception("spec-status: error on record %s", rec.get("spec_id"))
        return pushed

    def _check(self, rec: dict) -> int:
        if rec.get("phase") != "generating":
            return 0
        spec_path = self._specs_dir / f"{rec.get('spec_id')}.json"
        if not spec_path.exists():
            return 0
        try:
            spec = json.loads(spec_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return 0
        surface, chat_id = rec.get("surface"), rec.get("chat_id")
        platform = self._platforms.get(surface)
        if platform is None:
            return 0
        status = spec.get("status")
        if status == "error":
            msg = spec_error_message(spec)
            platform.reply(int(chat_id), msg)
            self._append(surface, chat_id, msg)
            self._store.clear(surface, chat_id)
            return 1
        if status == "done":
            if (spec.get("clarify") or {}).get("needed"):
                return 0  # ClarifyWatcher pushes the questions
            msg = spec_gate_message(spec)
            platform.reply(int(chat_id), msg)
            self._append(surface, chat_id, msg)
            self._store.update(surface, chat_id, phase="awaiting_spec_approval")
            return 1
        return 0

    def _append(self, surface, chat_id, msg: str) -> None:
        sid = self._sessions.load_or_create(surface, chat_id)
        self._sessions.append(sid, "assistant", msg)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/gateway/test_spec_status_watcher.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ai_dev_system/gateway/spec_status_watcher.py tests/unit/gateway/test_spec_status_watcher.py
git commit -m "feat(gateway): SpecStatusWatcher — proactive push on spec done/error"
```

---

### Task 6: Wire SpecStatusWatcher into the gateway daemon

**Files:**
- Modify: `src/ai_dev_system/cli/commands/gateway.py:64-89` (`build_gateway` watcher wiring)
- Test: `tests/unit/gateway/test_build_gateway_per_project.py`

**Interfaces:**
- Consumes: `SpecStatusWatcher` from Task 5.

- [ ] **Step 1: Write the failing test**

In `tests/unit/gateway/test_build_gateway_per_project.py`, extend the existing watcher-count test (it patches `cw.ClarifyWatcher` where `cw` is the gateway command module): add a parallel patch + assertion for the new watcher:

```python
    spec_watchers = []
    monkeypatch.setattr(cw, "SpecStatusWatcher",
                        lambda *a, **k: spec_watchers.append((a, k)) or MagicMock(check_once=lambda: 0))
```

(placed with the other patches) and after the existing asserts:

```python
    assert len(spec_watchers) == 2
    # same ChatTaskStore-rooted storage as the ClarifyWatcher of that project
    for (ca, _), (sa, _) in zip(clarify_watchers, spec_watchers):
        assert ca[3] == sa[3]      # storage_root argument matches
```

NOTE: patch target is the *gateway command module* attribute — for that to work, Step 2's implementation must import the class at module level of `gateway.py` (`from ai_dev_system.gateway.spec_status_watcher import SpecStatusWatcher`) OR the test patches the defining module; follow whichever pattern the existing ClarifyWatcher patch in this test file uses (line 23-25 comment says "patch where it is defined" — mirror exactly that mechanism: `monkeypatch.setattr(<clarify module>, ...)` → use `ai_dev_system.gateway.spec_status_watcher.SpecStatusWatcher` analogously; adjust the two lines above to match the file's existing import alias for the clarify module).

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/gateway/test_build_gateway_per_project.py -q`
Expected: FAIL (`SpecStatusWatcher` not constructed / attribute missing).

- [ ] **Step 3: Implement wiring in `gateway.py`**

Inside `build_gateway`, next to the ClarifyWatcher import: `from ai_dev_system.gateway.spec_status_watcher import SpecStatusWatcher`.

Change the per-repo loop:

```python
    watchers = []            # (RunStatusWatcher, ClarifyWatcher, SpecStatusWatcher)

    for rp in repos:
        res = project_registry.get(rp)
        store = ChatTaskStore(res.paths.storage_root)
        rw = RunStatusWatcher(res.conn_factory, res.link_store, platforms_by_name)
        cwt = ClarifyWatcher(store, platforms_by_name,
                             res.session_store, res.paths.storage_root)
        swt = SpecStatusWatcher(store, platforms_by_name,
                                res.session_store, res.paths.storage_root)
        watchers.append((rw, cwt, swt))
        resume_stores.append(res.session_store)

    if has_non_repo or not repos:
        store = ChatTaskStore(cfg.storage_root)
        rw = RunStatusWatcher(global_conn_factory, global_link_store, platforms_by_name)
        cwt = ClarifyWatcher(store, platforms_by_name,
                             global_session_store, str(cfg.storage_root))
        swt = SpecStatusWatcher(store, platforms_by_name,
                                global_session_store, str(cfg.storage_root))
        watchers.append((rw, cwt, swt))
        resume_stores.append(global_session_store)

    def _post_poll():
        for rw, cwt, swt in watchers:
            rw.check_once()
            cwt.check_once()
            swt.check_once()
```

(ClarifyWatcher runs before SpecStatusWatcher in the sweep, but ordering is not load-bearing: SpecStatusWatcher skips clarify-needed specs entirely.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/unit/gateway/ -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ai_dev_system/cli/commands/gateway.py tests/unit/gateway/test_build_gateway_per_project.py
git commit -m "feat(gateway): sweep SpecStatusWatcher per project in the daemon loop"
```

---

### Task 7: Docs, full suite, deploy

**Files:**
- Modify: `.env.example` (timeout knob comments)
- Modify: `README.md` (test count line ~151)

- [ ] **Step 1: Update `.env.example`**

Replace the turn-budget comment block (added 2026-07-02) with:

```
# Ngân sách cho claude CLI (tuỳ chọn — mặc định thường đủ).
# Nguyên tắc: giới hạn THỜI GIAN IM LẶNG (idle), không giới hạn tổng thời gian —
# repo lớn đọc 15-20 phút là bình thường, chỉ giết khi CLI thật sự treo.
# SPEC_MAX_TURNS:    số lượt đọc repo khi sinh spec agentic (mặc định 40).
# SPEC_IDLE_TIMEOUT: giây im lặng tối đa khi sinh spec (mặc định 180).
# SPEC_HARD_TIMEOUT: trần an toàn chống loop vô hạn (mặc định 3600).
# EXEC_MAX_TURNS:    số lượt khi executor sửa code (mặc định 100).
# EXEC_IDLE_TIMEOUT: giây im lặng tối đa cho executor/review (mặc định 180).
# SPEC_MAX_TURNS=40
# SPEC_IDLE_TIMEOUT=180
# SPEC_HARD_TIMEOUT=3600
# EXEC_MAX_TURNS=100
# EXEC_IDLE_TIMEOUT=180
```

- [ ] **Step 2: Full suite + README count**

Run: `python -m pytest tests -q`
Expected: 0 failed except possibly `test_readme_test_count_matches_collected_count` — update the `**NNNN tests**` line in README.md to the newly collected count, re-run that test, confirm PASS.

- [ ] **Step 3: Commit, merge, deploy**

```bash
git add .env.example README.md
git commit -m "docs(env): idle/hard timeout knobs; bump test count"
git checkout master
git merge --no-ff <branch> -m "Merge <branch>: idle-watchdog CLI timeouts + SpecStatusWatcher proactive push"
docker compose up -d --build
```

- [ ] **Step 4: Live smoke (manual, with user)**

Ask the user to re-send the Sigo-Backend investigation task in Telegram. Expected: tool events appear incrementally in `<repo>/.ai-dev/state/storage/task_specs/<id>.log`; on completion the bot pushes 📄/❌ without being asked. Watch specifically for the ⚠️ publish warning — push-from-container is still unproven.

---

## Self-Review Notes

- **Spec coverage:** idle watchdog shared fn (T1), spec path switch + env knobs (T2), spec log streaming + honest budget line (T3), shared strings (T4), proactive push (T5), daemon wiring (T6), docs/deploy (T7). Item "executor/review hưởng lợi cùng lúc" → T1 updates all five `_invoke_claude` callers.
- **Type consistency:** `_ClaudeRun.timeout_kind: str`; `_invoke_claude` positional order `(claude, cwd, prompt, max_turns, timeout_s)` preserved — Task 2's `test_invoke_receives_flags_turns_and_timeouts` asserts `a[3]`/`a[4]` accordingly; `SpecStatusWatcher.__init__` mirrors `ClarifyWatcher.__init__` exactly.
- **Known risk:** switching `_EXEC_FLAGS` to stream-json changes real executor output parsing — `_extract_result_event` already scans NDJSON line-wise, and the CLI behavior was smoke-verified in the container (CLI 2.1.196). Existing FakePopen-based tests remain valid because the new wait loop treats a returning `wait()` as exit.
