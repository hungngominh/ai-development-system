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


def _strip_fenced_blocks(text: str) -> str:
    """Drop ```...``` fenced blocks so prose can be inspected on its own.

    Fenced blocks hold illustrative examples (the forum task graph, the setup
    wizard transcript) where a project-level "PostgreSQL" mention is legitimate;
    they are not claims about the system's own persistence layer.
    """
    return re.sub(r"```.*?```", "", text, flags=re.S)


# A line that *explains the removal/migration* of PostgreSQL is historical
# context, not a live claim that the system still runs on Postgres.
_PG_REMOVAL_MARKERS = re.compile(
    r"bị bỏ|đã bỏ|loại bỏ|thay bằng|removed|dropped|migrat|M0\.5|không cần\s*post",
    re.I,
)


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
# format / preservation: the rewrite must keep the diagrams intact
# (validation_rules: "the mermaid block intact (fenced code blocks closed)";
#  deliverable: preserve the ASCII pipeline diagram + mermaid sequence diagram)
# --------------------------------------------------------------------------- #
def test_readme_preserves_diagrams_and_closes_all_fences():
    readme = _read("README.md")

    # Every ``` fence must be paired — an odd count means a code block (most
    # likely the mermaid diagram) was left unclosed by the rewrite.
    assert readme.count("```") % 2 == 0, (
        "README has an unbalanced number of ``` fences; a fenced code block "
        "(likely the mermaid diagram) was left unclosed"
    )

    # The ASCII pipeline diagram in "Mô hình hoạt động" must survive intact.
    flow = _section(readme, "## Mô hình hoạt động")
    pipeline = _first_fenced_block(flow)
    for stage in ("Gate 1", "Gate 2", "Gate 3", "COMPLETED"):
        assert stage in pipeline, (
            f"README ASCII pipeline diagram lost the {stage!r} stage"
        )

    # The mermaid sequence diagram must survive as a closed ```mermaid block.
    m = re.search(r"```mermaid\n(.*?)\n```", readme, re.S)
    assert m, "README lost its ```mermaid sequence diagram block"
    assert "sequenceDiagram" in m.group(1), (
        "README mermaid block is no longer a sequenceDiagram"
    )


# --------------------------------------------------------------------------- #
# (2) persistence layer is SQLite, not PostgreSQL — no live PG claims in README
# --------------------------------------------------------------------------- #
def test_readme_has_no_postgres_claims():
    readme = _read("README.md")
    assert not re.search(r"postgres|psycopg", readme, re.I), (
        "README still references PostgreSQL/psycopg as a live claim"
    )


@pytest.mark.parametrize("rel", ["SETUP.md", "docs/workflow-v2.md"])
def test_docs_have_no_live_postgres_claims(rel):
    """Acceptance (2): grep *the docs* (not just README) for live PG claims.

    `psycopg` (the driver dropped in M0.5) must not appear anywhere. A prose
    `PostgreSQL` mention is only allowed when the line documents its removal /
    migration; fenced examples (forum task graph, wizard transcript) are
    project-level illustrations, not persistence-layer claims.
    """
    text = _read(rel)
    assert not re.search(r"psycopg", text, re.I), (
        f"{rel} references psycopg, the driver dropped in M0.5"
    )
    prose = _strip_fenced_blocks(text)
    live = [
        ln.strip()
        for ln in prose.splitlines()
        if re.search(r"postgre", ln, re.I) and not _PG_REMOVAL_MARKERS.search(ln)
    ]
    assert not live, f"{rel} has live PostgreSQL claim(s): {live}"


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


def test_setup_describes_sqlite_and_claudemax_reality():
    """Positive counterpart to the negative checks: SETUP must actually state the
    correct setup — SQLite persistence and the no-API-key Claude Max (`claude`
    CLI) execution path — not merely drop the stale strings."""
    setup = _read("SETUP.md")
    assert re.search(r"sqlite", setup, re.I), (
        "SETUP.md must describe the SQLite (stdlib sqlite3, no driver) default"
    )
    assert re.search(
        r"claude\s*max|claude\s*code\s*max|no[\s-]?api[\s-]?key|không cần\s*api",
        setup,
        re.I,
    ), (
        "SETUP.md must describe the no-API-key Claude Max (`claude` CLI) "
        "execution path"
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
    # Scope to the skills table so unrelated inline `/...` code elsewhere in the
    # README is not counted as a documented skill.
    idx = readme.find("Skills (Claude Code slash commands)")
    assert idx != -1, "README must contain the skills table"
    table = readme[idx:]
    end = re.search(r"\n---", table)
    if end:
        table = table[: end.start()]

    documented = set(re.findall(r"`/([a-z][a-z0-9-]+)`", table))
    # Exact match (not subset): catches an extra/renamed/wrong skill name in the
    # table as well as a missing one.
    assert documented == EXPECTED_SKILLS, (
        f"README skills table must document exactly the shipped skills "
        f"{sorted(EXPECTED_SKILLS)}; got {sorted(documented)}"
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
