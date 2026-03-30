import os
import subprocess
import sys
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
        assert all(c in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in result)
        assert "kien" in result or "kin" in result  # tuỳ fallback

    def test_already_ascii_unchanged(self):
        assert name_to_slug("forum-kien-thuc") == "forum-kien-thuc"

    def test_consecutive_special_chars_single_dash(self):
        assert name_to_slug("hello   world") == "hello-world"


class TestMakeProjectId:
    def test_returns_string_uuid(self):
        result = make_project_id("forum-kien-thuc")
        parsed = uuid.UUID(result)
        assert str(parsed) == result

    def test_deterministic_same_slug(self):
        assert make_project_id("my-project") == make_project_id("my-project")

    def test_different_slugs_different_ids(self):
        assert make_project_id("project-a") != make_project_id("project-b")


class TestArgumentValidation:
    """Test argument validation via subprocess (giữ đúng exit code behaviour)."""

    def _run(self, args: list[str]) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        env["AI_DEV_STUB_LLM"] = "1"
        return subprocess.run(
            [sys.executable, "-m", "ai_dev_system.cli.start_project"] + args,
            capture_output=True, text=True,
            env=env,
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
        assert "Error: --project-name must be non-empty" in result.stderr
        assert "Error: --idea must be non-empty" in result.stderr


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

    def test_empty_results(self):
        total, esc, res, opt = _count_questions([])
        assert total == 0 and esc == 0 and res == 0 and opt == 0
