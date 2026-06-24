"""Background worker: produce a single-task TaskSpec and write a status file.

Spawned detached by the webui for repo-grounded (agentic) specs so the HTTP
request doesn't block. Writes <storage_root>/task_specs/<id>.json with a status.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from ai_dev_system.task_graph.single_task import spec_single_task
from ai_dev_system.llm_factory import make_real_llm_client


def run_worker(spec_id: str, idea: str, repo: str | None, *, storage_root: str) -> Path:
    out_dir = Path(storage_root) / "task_specs"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{spec_id}.json"
    try:
        # repo mode → agentic (llm unused); no repo → text path needs a real client.
        llm = None if repo else make_real_llm_client()
        result = spec_single_task(idea, llm, repo_path=repo)
        payload = {"status": "done", "idea": idea, "repo": repo,
                   "task": result["task"], "facets": result["facets"]}
    except Exception as exc:  # noqa: BLE001
        payload = {"status": "error", "idea": idea, "repo": repo, "error": str(exc)}
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def main(argv=None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--id", required=True)
    p.add_argument("--idea", required=True)
    p.add_argument("--repo", default=None)
    p.add_argument("--storage-root", required=True)
    args = p.parse_args(argv)
    run_worker(args.id, args.idea, args.repo or None, storage_root=args.storage_root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
