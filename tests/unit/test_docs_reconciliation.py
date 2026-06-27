"""Doc-reconciliation invariants (TDD — RED before docs are updated).

The task is "cập nhật lại toàn bộ tài liệu cho đúng với hiện tại" — bring the
prose docs (README.md, SETUP.md, docs/workflow*.md) back in line with the real
code/test state. There are no doc-content assertions in this repo yet, so these
tests encode the acceptance source as executable checks.

Authoritative sources read by these tests (never trusts prior doc text):
  - the actual package tree under src/ai_dev_system/*/__init__.py
  - the live `pytest --collect-only` count
  - the files under skills/ and .claude/commands/

NOTE: docs/architecture.md is already reconciled and is treated here as the
reference "good" state; the drift under test lives in README.md, SETUP.md, and
docs/workflow-v2.md.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_PKG = REPO_ROOT / "src" / "ai_dev_system"

# Canonical package list — src/ai_dev_system/<name>/__init__.py
EXPECTED_PACKAGES = {
    "agents", "beads", "cli", "db", "debate", "engine", "eval", "gate",
    "intake", "migration", "rules", "spec", "storage", "task_graph",
    "verification",
}

# The three skills shipped in skills/ and .claude/commands/.
EXPECTED_SKILLS = {"start-project", "review-debate", "review-verification"}


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _read(rel: str) -> str:
    p = REPO_ROOT / rel
    assert p.exists(), f"expected doc file missing: {rel}"
    # Guard against accidentally editing/asserting a worktree copy.
    assert ".worktrees" not in p.resolve().as_posix(), (
        f"{rel} resolved into a .worktrees copy, not the repo root"
    )
    return p.read_text(encoding="utf-8")


def _section(text: str, heading: str) -> str:
    """Return the markdown section body starting at `heading` up to the next H2."""
    idx = text.find(heading)
    assert idx != -1, f"heading not found: {heading!r}"
    rest = text[idx + len(heading):]
    nxt = re.search(r"\n##\s", rest)
    return rest[: nxt.start()] if nxt else rest


def _first_fenced_block(text: str) -> str:
    m = re.search(r"```[^\n]*\n(.*?)\n```", text, re.S)
    assert m, "no fenced code block found in section"
    return m.group(1)


def _actual_packages() -> set[str]:
    return {
        d.name
        for d in SRC_PKG.iterdir()
        if d.is_dir() and (d / "__init__.py").exists()
    }


def _relative_md_links(text: str) -> list[str]:
    """Markdown link targets that are local (not http/anchor/mailto)."""
    links = re.findall(r"\]\(([^)]+)\)", text)
    out = []
    for href in links:
        href = href.strip()
        if href.startswith(("http://", "https://", "#", "mailto:")):
            continue
        out.append(href.split("#", 1)[0])  # drop any anchor
    return [h for h in out if h]


# --------------------------------------------------------------------------- #
# sanity: the canonical package list matches reality (catches my own drift)
# --------------------------------------------------------------------------- #
def test_expected_packages_match_source_tree():
    assert _actual_packages() == EXPECTED_PACKAGES


# --------------------------------------------------------------------------- #
# (1) README test count must match the live collected count
# --------------------------------------------------------------------------- #
def test_readme_does_not_carry_stale_262_count():
    status = _section(_read("README.md"), "## Trạng thái")
    assert "262" not in status, "stale '262 tests' count still present in README status"


def test_readme_test_count_matches_collected_count():
    status = _section(_read("README.md"), "## Trạng thái")
    m = re.search(r"([\d,]+)\s*tests", status, re.I)
    assert m, "README status section must state a test count ('<N> tests')"
    claimed = int(m.group(1).replace(",", ""))

    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=300,
    )
    cm = re.search(r"(\d[\d,]*)\s+tests collected", proc.stdout)
    assert cm, f"could not parse collected count from pytest output:\n{proc.stdout[-500:]}"
    actual = int(cm.group(1).replace(",", ""))

    assert claimed == actual, (
        f"README claims {claimed} tests but pytest collects {actual}"
    )


def test_setup_does_not_carry_stale_406_count():
    setup = _read("SETUP.md")
    assert not re.search(r"406\s*passed", setup), (
        "stale '406 passed' count still present in SETUP.md"
    )


# --------------------------------------------------------------------------- #
# (2) persistence layer is SQLite, not PostgreSQL — no live PG claims in README
# --------------------------------------------------------------------------- #
def test_readme_has_no_postgres_claims():
    readme = _read("README.md")
    assert not re.search(r"postgres|psycopg", readme, re.I), (
        "README still references PostgreSQL/psycopg as a live claim"
    )


def test_readme_states_sqlite_persistence():
    readme = _read("README.md")
    assert re.search(r"sqlite", readme, re.I), (
        "README must state the persistence layer is SQLite (stdlib sqlite3)"
    )


# --------------------------------------------------------------------------- #
# (3) intake wizard is the front door — normalize is no longer the entry point
# --------------------------------------------------------------------------- #
def test_readme_presents_intake_as_front_door():
    readme = _read("README.md")
    assert re.search(r"intake", readme, re.I), (
        "README must present the intake wizard, not normalize.py, as the entry point"
    )


def test_workflow_v2_front_door_is_intake_not_normalize():
    wf = _read("docs/workflow-v2.md")
    assert "Normalize idea" not in wf, (
        "workflow-v2.md still labels the front door 'Normalize idea → initial brief'"
    )
    assert re.search(r"intake", wf, re.I), (
        "workflow-v2.md must reference the intake wizard as the front door"
    )


# --------------------------------------------------------------------------- #
# (5)/(a) README module tree matches the actual package list
# --------------------------------------------------------------------------- #
def test_readme_module_tree_lists_every_package():
    arch = _section(_read("README.md"), "## Kiến trúc")
    tree = _first_fenced_block(arch)
    listed_tokens = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", tree))
    missing = EXPECTED_PACKAGES - listed_tokens
    assert not missing, f"README module tree omits packages: {sorted(missing)}"


def test_readme_module_tree_has_no_nonexistent_packages():
    """Any '<name>/' directory entry in the tree must be a real package."""
    arch = _section(_read("README.md"), "## Kiến trúc")
    tree = _first_fenced_block(arch)
    # Only inspect child entries (lines with a tree branch glyph), not the
    # `src/ai_dev_system/` root header.
    dir_entries = set(re.findall(r"──\s*([A-Za-z_][A-Za-z0-9_]*)/", tree))
    actual = _actual_packages()
    bogus = {d for d in dir_entries if d not in actual}
    assert not bogus, f"README module tree lists nonexistent packages: {sorted(bogus)}"


# --------------------------------------------------------------------------- #
# (c) execution section: single-task = working, multi-task graph = partial
# --------------------------------------------------------------------------- #
def test_readme_marks_single_task_working_and_multitask_partial():
    readme = _read("README.md")
    assert re.search(r"single[ -]?task", readme, re.I), (
        "README must state single-task execution as the working path"
    )
    has_multi = re.search(r"multi[ -]?task|task graph|đồ thị", readme, re.I)
    has_partial = re.search(r"⚠|partial|một phần|chưa hoàn", readme, re.I)
    assert has_multi and has_partial, (
        "README must mark multi-task graph execution as partial/incomplete (⚠️)"
    )


# --------------------------------------------------------------------------- #
# SETUP no longer instructs entering a DB + API key (no-API-key ClaudeMax + sqlite)
# --------------------------------------------------------------------------- #
def test_setup_does_not_instruct_entering_db_and_apikey():
    setup = _read("SETUP.md")
    assert "nhập DB, API key" not in setup, (
        "SETUP.md still tells the user to enter a DB + API key, conflicting with "
        "the no-API-key ClaudeMax + sqlite default reality"
    )


# --------------------------------------------------------------------------- #
# (e) skills documented in README match the files in skills/ and .claude/commands/
# --------------------------------------------------------------------------- #
def test_skill_files_exist_in_both_locations():
    for name in EXPECTED_SKILLS:
        assert (REPO_ROOT / "skills" / f"{name}.md").exists(), (
            f"skills/{name}.md missing"
        )
        assert (REPO_ROOT / ".claude" / "commands" / f"{name}.md").exists(), (
            f".claude/commands/{name}.md missing"
        )


def test_readme_skills_table_matches_skill_files():
    readme = _read("README.md")
    documented = set(re.findall(r"/([a-z][a-z-]+)`", readme))
    documented &= EXPECTED_SKILLS | {"x"}  # restrict to plausible skill names
    assert EXPECTED_SKILLS <= documented, (
        f"README skills table must document all skills; missing "
        f"{sorted(EXPECTED_SKILLS - documented)}"
    )


# --------------------------------------------------------------------------- #
# (4) every relative link target in edited docs resolves
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "rel",
    ["README.md", "SETUP.md", "docs/architecture.md", "docs/workflow-v2.md"],
)
def test_relative_links_resolve(rel):
    text = _read(rel)
    base = (REPO_ROOT / rel).parent
    broken = [h for h in _relative_md_links(text) if not (base / h).exists()]
    assert not broken, f"{rel} has broken relative links: {broken}"
