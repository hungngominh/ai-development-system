"""CLI entry point for Phase 1a: normalize → debate → PAUSED_AT_GATE_1."""
from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
import os

from ai_dev_system.config import Config
from ai_dev_system.db.connection import get_connection
from ai_dev_system.debate.progress import DebateProgress
from ai_dev_system.debate_pipeline import run_debate_pipeline


class _StderrProgress(DebateProgress):
    """Render debate progress to stderr (one line per round).

    Keeps stdout reserved for the final JSON; flushes each line so it
    shows up live even when the run is backgrounded into a log file.
    """

    def on_questions(self, total, required, strategic, optional):
        print(
            f"[debate] questions: {total}  "
            f"({required} required / {strategic} strategic / {optional} optional)",
            file=sys.stderr, flush=True,
        )

    def on_question_start(self, index, total, question):
        print(
            f"[debate] [{index:>2}/{total}] {question.id}  "
            f"{question.agent_a} vs {question.agent_b}",
            file=sys.stderr, flush=True,
        )

    def on_round(self, index, total, round_num, max_rounds, result, *, is_final):
        a = "ok" if result.agent_a_position else "--"
        b = "ok" if result.agent_b_position else "--"
        mod = "ok" if result.moderator_summary else "--"
        tail = f"  {result.resolution_status}" if is_final else ""
        print(
            f"           round {round_num}/{max_rounds}:  "
            f"A {a}  B {b}  mod {mod}   -> conf {result.confidence:.2f}{tail}",
            file=sys.stderr, flush=True,
        )



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


def _make_llm_client():
    """Return StubDebateLLMClient nếu AI_DEV_STUB_LLM=1, else real client."""
    if os.environ.get("AI_DEV_STUB_LLM") == "1":
        from ai_dev_system.debate.llm import StubDebateLLMClient
        return StubDebateLLMClient()
    from ai_dev_system.llm_factory import make_real_llm_client
    return make_real_llm_client()


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

    # Config guard — before progress print
    try:
        llm_client = _make_llm_client()
    except RuntimeError as exc:
        print(f"LLM configuration error: {exc}", file=sys.stderr)
        return 1

    # Progress
    print("[Phase 1a/1b] Running debate pipeline (normalize -> questions -> debate)...", file=sys.stderr)
    print("             This may take 2-5 minutes.", file=sys.stderr)

    try:
        result = run_debate_pipeline(
            raw_idea=full_idea,
            config=config,
            conn=conn,
            project_id=project_id,
            llm_client=llm_client,
            progress=_StderrProgress(),
        )
        # get_connection() opens with autocommit OFF, and the pipeline never
        # commits — without this the run + artifact rows are rolled back on
        # conn.close(), leaving the run invisible to `ai-dev info` and Gate 1
        # review (only the on-disk artifacts survive). Commit at the command
        # boundary, matching intake/phase-b/migrate.
        conn.commit()
        total, escalated, resolved, optional = _count_questions(result.debate_report.results)
        print("[Done]     DEBATE_REPORT promoted. Status: PAUSED_AT_GATE_1", file=sys.stderr)
    except Exception as exc:
        print(f"Pipeline error: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()

    # JSON output to stdout
    output = {
        "run_id": result.run_id,
        "project_id": project_id,
        "project_slug": slug,
        "status": "PAUSED_AT_GATE_1",
        "questions_count": total,
        "escalated_count": escalated,
        "resolved_count": resolved,
        "optional_count": optional,
    }
    print(json.dumps(output))
    return 0


if __name__ == "__main__":
    sys.exit(main())
