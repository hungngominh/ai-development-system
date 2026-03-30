# Start Project Skill (Phase 1a) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement `/start-project` skill và `start_project.py` CLI script — entry point Phase 1a từ raw idea đến `PAUSED_AT_GATE_1`.

**Architecture:** Skill (markdown) thu thập idea + constraints + project name từ user, sau đó gọi blocking bash command chạy `start_project.py`. Script là thin dispatcher: tính `project_id` từ project name, gọi `run_debate_pipeline()`, in progress lên stderr và JSON result lên stdout.

**Tech Stack:** Python 3.12, argparse, psycopg v3, pytest, subprocess (trong integration tests). `run_debate_pipeline()` đã có sẵn — không sửa.

**Spec:** `docs/superpowers/specs/2026-03-30-start-project-skill-design.md`
**Worktree:** `.worktrees/minimal-worker-loop/` (tất cả paths bên dưới là relative to worktree root)

---

## File Map

### New Files

| File | Responsibility |
|------|---------------|
| `src/ai_dev_system/cli/__init__.py` | Package marker |
| `src/ai_dev_system/cli/start_project.py` | CLI script: arg parsing, slug, dispatch, JSON output |
| `tests/unit/cli/__init__.py` | Package marker |
| `tests/unit/cli/test_start_project.py` | Unit tests: slug, project_id, constraints, validation |
| `tests/integration/test_start_project_cli.py` | Integration tests: subprocess invocation end-to-end |
| `skills/start-project.md` | Skill file: state machine, bash invocation, DONE display |

### Modified Files

_Không có._ `run_debate_pipeline()` đã đủ — không cần sửa.

---

## Task 1: CLI Package + `name_to_slug`

**Files:**
- Create: `src/ai_dev_system/cli/__init__.py`
- Create: `src/ai_dev_system/cli/start_project.py` (chỉ phần `name_to_slug`)
- Create: `tests/unit/cli/__init__.py`
- Create: `tests/unit/cli/test_start_project.py`

- [ ] **Step 1: Tạo package markers**

```bash
touch src/ai_dev_system/cli/__init__.py
touch tests/unit/cli/__init__.py
```

- [ ] **Step 2: Viết failing tests cho `name_to_slug`**

Tạo `tests/unit/cli/test_start_project.py`:

```python
import uuid
import pytest
from ai_dev_system.cli.start_project import name_to_slug, make_project_id


class TestNameToSlug:
    def test_basic_lowercase(self):
        assert name_to_slug("Forum Kien Thuc") == "forum-kien-thuc"

    def test_spaces_become_dashes(self):
        assert name_to_slug("my project name") == "my-project-name"

    def test_special_chars_removed(self):
        assert name_to_slug("hello! world@2026") == "hello-world-2026"

    def test_leading_trailing_dashes_stripped(self):
        assert name_to_slug("  --forum--  ") == "forum"

    def test_truncated_to_40_chars(self):
        long = "a" * 50
        assert len(name_to_slug(long)) == 40

    def test_vietnamese_diacritics_stripped(self):
        result = name_to_slug("Kiến Thức Nội Bộ")
        # unidecode hoặc ascii-ignore đều không được chứa dấu
        assert all(c in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in result)
        assert "kien" in result or "kin" in result  # tuỳ fallback

    def test_already_ascii_unchanged(self):
        assert name_to_slug("forum-kien-thuc") == "forum-kien-thuc"

    def test_consecutive_special_chars_single_dash(self):
        assert name_to_slug("hello   world") == "hello-world"


class TestMakeProjectId:
    def test_returns_string_uuid(self):
        result = make_project_id("forum-kien-thuc")
        # must be valid UUID string
        parsed = uuid.UUID(result)
        assert str(parsed) == result

    def test_deterministic_same_slug(self):
        assert make_project_id("my-project") == make_project_id("my-project")

    def test_different_slugs_different_ids(self):
        assert make_project_id("project-a") != make_project_id("project-b")
```

> **Note:** `TestCountQuestions` sẽ được thêm vào file này ở Task 3 sau khi `_count_questions` được implement.

- [ ] **Step 3: Chạy để xác nhận fail**

```bash
pytest tests/unit/cli/test_start_project.py -v
```

Expected: `ModuleNotFoundError: No module named 'ai_dev_system.cli.start_project'`

- [ ] **Step 4: Implement `name_to_slug` và `make_project_id`**

Tạo `src/ai_dev_system/cli/start_project.py`:

```python
"""CLI entry point for Phase 1a: normalize → debate → PAUSED_AT_GATE_1."""
from __future__ import annotations

import json
import re
import sys
import uuid


def name_to_slug(name: str) -> str:
    """Convert a project name to a URL-safe slug (max 40 chars)."""
    s = name.strip().lower()
    # Strip diacritics: prefer unidecode, fall back to ascii-ignore
    try:
        from unidecode import unidecode  # type: ignore
        s = unidecode(s)
    except ImportError:
        s = s.encode("ascii", "ignore").decode()
    # Replace non-alphanumeric runs with a single dash
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:40]


def make_project_id(slug: str) -> str:
    """Deterministic UUID from slug (uuid5). Same slug → same UUID."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, slug))
```

- [ ] **Step 5: Chạy để xác nhận pass**

```bash
pytest tests/unit/cli/test_start_project.py::TestNameToSlug \
       tests/unit/cli/test_start_project.py::TestMakeProjectId -v
```

Expected: tất cả PASS.

- [ ] **Step 6: Commit**

```bash
git add src/ai_dev_system/cli/__init__.py \
        src/ai_dev_system/cli/start_project.py \
        tests/unit/cli/__init__.py \
        tests/unit/cli/test_start_project.py
git commit -m "feat(cli): add CLI package with name_to_slug and make_project_id"
```

---

## Task 2: Argument Parsing + Validation

**Files:**
- Modify: `src/ai_dev_system/cli/start_project.py`
- Modify: `tests/unit/cli/test_start_project.py`

- [ ] **Step 1: Viết failing tests cho argument parsing**

Append vào `tests/unit/cli/test_start_project.py`:

```python
import subprocess
import sys


class TestArgumentValidation:
    """Test argument validation via subprocess (giữ đúng exit code behaviour)."""

    def _run(self, args: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "ai_dev_system.cli.start_project"] + args,
            capture_output=True, text=True,
        )

    def test_missing_idea_exits_1(self):
        result = self._run(["--project-name", "my-project"])
        assert result.returncode == 1
        assert "Error: --idea must be non-empty" in result.stderr
        assert result.stdout == ""

    def test_empty_idea_exits_1(self):
        result = self._run(["--project-name", "my-project", "--idea", ""])
        assert result.returncode == 1
        assert "Error: --idea must be non-empty" in result.stderr
        assert result.stdout == ""

    def test_missing_project_name_exits_1(self):
        result = self._run(["--idea", "Build something"])
        assert result.returncode == 1
        assert "Error: --project-name must be non-empty" in result.stderr
        assert result.stdout == ""

    def test_empty_project_name_exits_1(self):
        result = self._run(["--project-name", "", "--idea", "Build something"])
        assert result.returncode == 1
        assert "Error: --project-name must be non-empty" in result.stderr
        assert result.stdout == ""

    def test_missing_both_exits_1(self):
        result = self._run([])
        assert result.returncode == 1
        assert result.stdout == ""
```

- [ ] **Step 2: Chạy để xác nhận fail**

```bash
pytest tests/unit/cli/test_start_project.py::TestArgumentValidation -v
```

Expected: FAIL (script chưa có `__main__` block).

- [ ] **Step 3: Implement argument parsing + validation**

Append vào `src/ai_dev_system/cli/start_project.py`:

```python
import argparse


def _parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Run Phase A debate pipeline.")
    parser.add_argument("--project-name", default="", dest="project_name")
    parser.add_argument("--idea", default="")
    parser.add_argument("--constraints", default="")
    return parser.parse_args(argv)


def _validate(args) -> list[str]:
    errors = []
    if not args.project_name.strip():
        errors.append("Error: --project-name must be non-empty")
    if not args.idea.strip():
        errors.append("Error: --idea must be non-empty")
    return errors


def main(argv=None) -> int:
    args = _parse_args(argv)
    errors = _validate(args)
    if errors:
        for e in errors:
            print(e, file=sys.stderr)
        return 1
    # (pipeline call will go here in Task 3)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Chạy để xác nhận pass**

```bash
pytest tests/unit/cli/test_start_project.py::TestArgumentValidation -v
```

Expected: tất cả PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ai_dev_system/cli/start_project.py \
        tests/unit/cli/test_start_project.py
git commit -m "feat(cli): add argument parsing and validation with exit code 1"
```

---

## Task 3: Happy Path — Pipeline Dispatch + JSON Output

**Files:**
- Modify: `src/ai_dev_system/cli/start_project.py`
- Create: `tests/integration/test_start_project_cli.py`

Cần: `DATABASE_URL` và `STORAGE_ROOT` env vars, DB đã apply migrations.

- [ ] **Step 1: Viết failing integration test**

Tạo `tests/integration/test_start_project_cli.py`:

```python
"""Integration tests: invoke start_project.py via subprocess."""
import json
import os
import subprocess
import sys

import pytest


def _run_cli(idea: str, project_name: str, constraints: str = "", env_override: dict = None):
    env = os.environ.copy()
    env["AI_DEV_STUB_LLM"] = "1"   # dùng StubDebateLLMClient
    if env_override:
        env.update(env_override)
    return subprocess.run(
        [
            sys.executable, "-m", "ai_dev_system.cli.start_project",
            "--project-name", project_name,
            "--idea", idea,
            "--constraints", constraints,
        ],
        capture_output=True, text=True, env=env,
    )


@pytest.mark.integration
def test_happy_path_exit_0(config):
    result = _run_cli("Build a forum for knowledge sharing", "forum-test")
    assert result.returncode == 0, f"stderr: {result.stderr}"


@pytest.mark.integration
def test_stdout_is_valid_json(config):
    result = _run_cli("Build a forum for knowledge sharing", "forum-test")
    assert result.returncode == 0
    data = json.loads(result.stdout)
    assert data["status"] == "PAUSED_AT_GATE_1"
    assert "run_id" in data
    assert "project_id" in data


@pytest.mark.integration
def test_count_invariant(config):
    """questions_count == escalated + resolved + optional."""
    result = _run_cli("Build a forum for knowledge sharing", "forum-test")
    data = json.loads(result.stdout)
    assert (
        data["questions_count"]
        == data["escalated_count"] + data["resolved_count"] + data["optional_count"]
    )


@pytest.mark.integration
def test_stderr_has_progress_stdout_only_json(config):
    result = _run_cli("Build a task manager", "task-mgr-test")
    assert result.returncode == 0
    # stderr phải có progress lines
    assert "[Phase" in result.stderr
    # stdout phải chỉ có đúng 1 JSON object (không có trailing garbage)
    data = json.loads(result.stdout.strip())
    assert isinstance(data, dict)


@pytest.mark.integration
def test_constraints_appended_to_idea(config):
    """Chạy với constraints — chỉ cần không crash và trả về valid JSON."""
    result = _run_cli(
        "Build a forum",
        "forum-constraint-test",
        constraints="Python only, no cloud",
    )
    assert result.returncode == 0
    assert json.loads(result.stdout)["status"] == "PAUSED_AT_GATE_1"


@pytest.mark.integration
def test_idempotent_project_id(config):
    """Cùng project name → cùng project_id trong cả 2 lần chạy."""
    r1 = _run_cli("Build a forum", "same-project-name")
    r2 = _run_cli("Build something else", "same-project-name")
    assert r1.returncode == 0 and r2.returncode == 0
    id1 = json.loads(r1.stdout)["project_id"]
    id2 = json.loads(r2.stdout)["project_id"]
    assert id1 == id2
```

- [ ] **Step 1b: Viết failing unit tests cho `_count_questions`**

Append vào `tests/unit/cli/test_start_project.py`:

```python
from unittest.mock import MagicMock
from ai_dev_system.cli.start_project import _count_questions


def _make_qdr(classification: str, resolution_status: str):
    """Helper: build a minimal QuestionDebateResult-like mock."""
    qdr = MagicMock()
    qdr.question.classification = classification
    qdr.final.resolution_status = resolution_status
    return qdr


class TestCountQuestions:
    def test_all_resolved(self):
        results = [
            _make_qdr("REQUIRED", "RESOLVED"),
            _make_qdr("STRATEGIC", "RESOLVED_WITH_CAVEAT"),
        ]
        total, esc, res, opt = _count_questions(results)
        assert total == 2 and esc == 0 and res == 2 and opt == 0

    def test_escalate_to_human(self):
        results = [_make_qdr("REQUIRED", "ESCALATE_TO_HUMAN")]
        total, esc, res, opt = _count_questions(results)
        assert esc == 1 and res == 0 and opt == 0

    def test_need_more_evidence_counts_as_escalated(self):
        results = [_make_qdr("STRATEGIC", "NEED_MORE_EVIDENCE")]
        total, esc, res, opt = _count_questions(results)
        assert esc == 1 and res == 0 and opt == 0

    def test_optional_not_debated(self):
        results = [_make_qdr("OPTIONAL", "RESOLVED")]  # status irrelevant for OPTIONAL
        total, esc, res, opt = _count_questions(results)
        assert opt == 1 and esc == 0 and res == 0

    def test_invariant_holds_mixed(self):
        results = [
            _make_qdr("REQUIRED", "RESOLVED"),
            _make_qdr("STRATEGIC", "ESCALATE_TO_HUMAN"),
            _make_qdr("OPTIONAL", "RESOLVED"),
            _make_qdr("REQUIRED", "NEED_MORE_EVIDENCE"),
            _make_qdr("STRATEGIC", "RESOLVED_WITH_CAVEAT"),
        ]
        total, esc, res, opt = _count_questions(results)
        assert total == esc + res + opt
        assert total == 5 and esc == 2 and res == 2 and opt == 1
```

- [ ] **Step 1c: Chạy `TestCountQuestions` để xác nhận fail**

```bash
pytest tests/unit/cli/test_start_project.py::TestCountQuestions -v
```

Expected: `ImportError` — `_count_questions` chưa được implement.

- [ ] **Step 2: Chạy integration tests để xác nhận fail**

```bash
pytest tests/integration/test_start_project_cli.py -v -m integration
```

Expected: FAIL — `main()` hiện chỉ return 0 mà chưa gọi pipeline.

- [ ] **Step 3: Implement pipeline dispatch trong `main()`**

Thêm `import os` vào `src/ai_dev_system/cli/start_project.py` (sau dòng `import uuid` đã có), thêm 3 import package sau dòng `import argparse` đã có, rồi thêm các hàm mới và replace `main()`:

```python
# Thêm sau "import uuid":
import os

# Thêm sau "import argparse":
from ai_dev_system.config import Config
from ai_dev_system.db.connection import get_connection
from ai_dev_system.debate_pipeline import run_debate_pipeline
```

Tiếp theo thêm các hàm sau `_validate()` và replace `main()`:

```python
def _make_llm_client():
    """Return StubDebateLLMClient nếu AI_DEV_STUB_LLM=1, else real client."""
    if os.environ.get("AI_DEV_STUB_LLM") == "1":
        from ai_dev_system.debate.llm import StubDebateLLMClient
        return StubDebateLLMClient()
    # Real client: yêu cầu ANTHROPIC_API_KEY
    # TODO: implement RealDebateLLMClient khi cần production usage
    raise RuntimeError(
        "Real LLM client not yet implemented. Set AI_DEV_STUB_LLM=1 for testing."
    )


def _count_questions(results: list) -> tuple[int, int, int, int]:
    """Return (total, escalated, resolved, optional)."""
    escalated = resolved = optional = 0
    for qdr in results:
        if qdr.question.classification == "OPTIONAL":
            optional += 1
        elif qdr.final.resolution_status in ("ESCALATE_TO_HUMAN", "NEED_MORE_EVIDENCE"):
            escalated += 1
        else:
            resolved += 1
    total = escalated + resolved + optional
    return total, escalated, resolved, optional


def main(argv=None) -> int:
    args = _parse_args(argv)
    errors = _validate(args)
    if errors:
        for e in errors:
            print(e, file=sys.stderr)
        return 1

    # Build full_idea
    full_idea = args.idea.strip()
    if args.constraints.strip():
        full_idea = full_idea + "\n\nConstraints: " + args.constraints.strip()

    # Compute project_id
    slug = name_to_slug(args.project_name)
    project_id = make_project_id(slug)

    # Load config + DB connection
    try:
        config = Config.from_env()
        conn = get_connection(config.database_url)
    except Exception as exc:
        print(f"DB connection failed: {exc}", file=sys.stderr)
        return 1

    # Progress
    print("[Phase 1a/1b] Running debate pipeline (normalize → questions → debate)...", file=sys.stderr)
    print("             This may take 2-5 minutes.", file=sys.stderr)

    try:
        llm_client = _make_llm_client()
        result = run_debate_pipeline(
            raw_idea=full_idea,
            config=config,
            conn=conn,
            project_id=project_id,
            llm_client=llm_client,
        )
    except Exception as exc:
        print(f"LLM API error: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()

    # Count question outcomes
    total, escalated, resolved, optional = _count_questions(result.debate_report.results)

    print(
        f"[Done]     DEBATE_REPORT promoted. Status: PAUSED_AT_GATE_1",
        file=sys.stderr,
    )

    # JSON output to stdout
    output = {
        "run_id": result.run_id,
        "project_id": project_id,
        "project_name": slug,
        "status": "PAUSED_AT_GATE_1",
        "questions_count": total,
        "escalated_count": escalated,
        "resolved_count": resolved,
        "optional_count": optional,
    }
    print(json.dumps(output))
    return 0
```

- [ ] **Step 4: Chạy để xác nhận pass**

```bash
pytest tests/unit/cli/test_start_project.py::TestCountQuestions \
       tests/integration/test_start_project_cli.py -v -m integration
```

Expected: tất cả PASS.

- [ ] **Step 5: Commit**

```bash
git add src/ai_dev_system/cli/start_project.py \
        tests/integration/test_start_project_cli.py
git commit -m "feat(cli): implement start_project happy path — pipeline dispatch + JSON output"
```

---

## Task 4: Error Paths

**Files:**
- Modify: `tests/integration/test_start_project_cli.py`

- [ ] **Step 1: Viết failing tests cho error paths**

Append vào `tests/integration/test_start_project_cli.py`:

```python
@pytest.mark.integration
def test_empty_idea_exits_1(config):
    result = _run_cli("", "my-project")
    assert result.returncode == 1
    assert "Error: --idea must be non-empty" in result.stderr
    assert result.stdout.strip() == ""


@pytest.mark.integration
def test_empty_project_name_exits_1(config):
    result = _run_cli("Build something", "")
    assert result.returncode == 1
    assert "Error: --project-name must be non-empty" in result.stderr
    assert result.stdout.strip() == ""


@pytest.mark.integration
def test_llm_error_exits_1_stdout_empty(config):
    """Khi LLM_STUB không set và không có real client → exit 1, stdout trống."""
    env = os.environ.copy()
    env.pop("AI_DEV_STUB_LLM", None)   # force real client path
    result = subprocess.run(
        [
            sys.executable, "-m", "ai_dev_system.cli.start_project",
            "--project-name", "err-test",
            "--idea", "Build something",
        ],
        capture_output=True, text=True, env=env,
    )
    assert result.returncode == 1
    assert result.stdout.strip() == ""
    assert "error" in result.stderr.lower() or "Error" in result.stderr


@pytest.mark.integration
def test_bad_database_url_exits_1(config):
    env = os.environ.copy()
    env["DATABASE_URL"] = "postgresql://invalid:invalid@localhost:9999/noexist"
    env["AI_DEV_STUB_LLM"] = "1"
    result = subprocess.run(
        [
            sys.executable, "-m", "ai_dev_system.cli.start_project",
            "--project-name", "db-err-test",
            "--idea", "Build something",
        ],
        capture_output=True, text=True, env=env,
    )
    assert result.returncode == 1
    assert "DB connection failed" in result.stderr
    assert result.stdout.strip() == ""
```

- [ ] **Step 2: Chạy để xác nhận fail**

```bash
pytest tests/integration/test_start_project_cli.py::test_llm_error_exits_1_stdout_empty \
       tests/integration/test_start_project_cli.py::test_bad_database_url_exits_1 -v
```

Expected: các test liên quan error paths FAIL hoặc PASS tuỳ implementation hiện tại.

- [ ] **Step 3: Chạy toàn bộ integration tests**

```bash
pytest tests/integration/test_start_project_cli.py -v -m integration
```

Expected: tất cả PASS (error handling đã có trong `main()` từ Task 3).

- [ ] **Step 4: Chạy toàn bộ unit tests để đảm bảo không regression**

```bash
pytest tests/unit/cli/ -v
```

Expected: tất cả PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/integration/test_start_project_cli.py
git commit -m "test(cli): add error path integration tests for start_project CLI"
```

---

## Task 5: Skill File

**Files:**
- Create: `skills/start-project.md`

Không có unit test cho skill (markdown file — test bằng manual walkthrough).

- [ ] **Step 1: Tạo thư mục nếu chưa có**

```bash
mkdir -p skills
```

- [ ] **Step 2: Tạo `skills/start-project.md`**

> **Prerequisite:** Package phải được install ở editable mode trước khi skill có thể gọi `python -m`. Từ worktree root: `pip install -e .`
> Hoặc set `PYTHONPATH=src` nếu không dùng editable install.

```markdown
---
name: start-project
description: >
  Phase 1a entry point. Thu thập ý tưởng từ user, chạy debate pipeline
  (normalize → question gen → AI debate), và hướng dẫn user sang /review-debate.
---

# Start Project Skill

Invoke this skill with `/start-project` (optionally followed by the idea inline).

## State Machine

```
COLLECT_IDEA → COLLECT_CONSTRAINTS → COLLECT_PROJECT_NAME → RUNNING → DONE / ERROR
```

## Instructions

### COLLECT_IDEA

Check if the user provided an idea after the slash command (e.g., `/start-project "xây forum..."`).

- **Idea provided inline:** skip to COLLECT_CONSTRAINTS.
- **No idea:** Ask: *"Bạn muốn xây dựng gì?"* Wait for response.

### COLLECT_CONSTRAINTS

Always ask this, even if idea was inline:

> *"Có constraint nào cần biết trước không? (vd: tech stack bắt buộc, deadline, budget) — Enter để bỏ qua."*

Accept empty reply, "không", "skip", "bỏ qua" → treat as empty string, continue.

### COLLECT_PROJECT_NAME

Ask:

> *"Tên project? (dùng để nhóm các run liên quan, vd: 'forum-kien-thuc')"*

Wait for name. Do not generate the slug yourself — the CLI script handles it.

### RUNNING

Before running the command, escape all user-supplied values: replace any single-quote `'`
in the value with `'\''` (the standard POSIX shell single-quote escape).

Print:

> *"Đang chạy Phase A (normalize → debate)... Quá trình này mất 2-5 phút."*

Then run the following bash command (blocking — wait for it to finish):

```bash
python -m ai_dev_system.cli.start_project \
  --project-name '<project_name_escaped>' \
  --idea '<idea_escaped>' \
  --constraints '<constraints_escaped>'
```

Stderr from the script will appear in the terminal in real-time. Do not suppress it.

### DONE (exit code = 0)

Parse the single JSON line from stdout. Display:

```
✅ Phase A hoàn tất.
   Run ID    : <run_id>
   Questions : <questions_count> tổng
               (<escalated_count> ESCALATE_TO_HUMAN/NEED_MORE_EVIDENCE,
                <resolved_count> RESOLVED,
                <optional_count> OPTIONAL tự giải)

→ Chạy /review-debate --run-id <run_id> để bắt đầu Gate 1.
```

Substitute actual values from the JSON. Show the exact `/review-debate --run-id <run_id>` command with the real run_id so the user can copy-paste it.

### ERROR (exit code ≠ 0)

Display:

```
❌ Phase A thất bại: <last line of stderr>

Không có DB record nào được tạo. Bạn có thể chạy lại /start-project với cùng tên project.
```

## Shell Safety

Single-quote escape rule: if value contains `'`, replace each `'` with `'\''` before embedding in the command.

Example — idea containing a single quote `"it's a forum"`:
```bash
--idea 'it'\''s a forum'
```
```

- [ ] **Step 3: Kiểm tra file tồn tại**

```bash
cat skills/start-project.md | head -5
```

Expected: thấy `name: start-project` ở đầu file.

- [ ] **Step 4: Manual walkthrough**

Chạy `/start-project` trong Claude Code session (cần DB + `AI_DEV_STUB_LLM=1`), xác nhận:
- [ ] COLLECT_IDEA hiển thị câu hỏi khi không có inline idea
- [ ] COLLECT_CONSTRAINTS hỏi đúng
- [ ] COLLECT_PROJECT_NAME hỏi đúng
- [ ] RUNNING print "Đang chạy..." trước khi bash chạy
- [ ] DONE hiển thị đúng format với run_id thực
- [ ] Command `/review-debate --run-id <actual-id>` hiển thị ở cuối

- [ ] **Step 5: Commit**

```bash
git add skills/start-project.md
git commit -m "feat(skill): add start-project skill — Phase 1a entry point"
```

---

## Task 6: Final Check

- [ ] **Step 1: Chạy toàn bộ unit tests**

```bash
pytest tests/unit/ -v
```

Expected: tất cả PASS, không có regression.

- [ ] **Step 2: Chạy toàn bộ integration tests**

```bash
pytest tests/integration/ -v -m integration
```

Expected: tất cả PASS bao gồm `test_start_project_cli.py`.

- [ ] **Step 3: Commit tổng kết nếu cần**

```bash
git log --oneline -5
```

Verify 4 commits từ plan này xuất hiện đúng thứ tự.
