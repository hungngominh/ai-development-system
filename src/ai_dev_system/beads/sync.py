# src/ai_dev_system/beads/sync.py
import logging
import subprocess
from ai_dev_system.db.repos.events import EventRepo

logger = logging.getLogger(__name__)


def _topological_sort(tasks: list[dict]) -> list[dict]:
    """Simple Kahn's algorithm topological sort."""
    id_to_task = {t["id"]: t for t in tasks}
    in_degree = {t["id"]: 0 for t in tasks}
    for t in tasks:
        for dep in t.get("deps", []):
            if dep in in_degree:
                in_degree[t["id"]] += 1
    queue = [tid for tid, deg in in_degree.items() if deg == 0]
    result = []
    while queue:
        tid = queue.pop(0)
        result.append(id_to_task[tid])
        for t in tasks:
            if tid in t.get("deps", []):
                in_degree[t["id"]] -= 1
                if in_degree[t["id"]] == 0:
                    queue.append(t["id"])
    return result


def beads_sync(run_id: str, graph: dict, conn) -> None:
    """Sync task graph to Beads (bd CLI). Non-blocking: errors are logged, never raised."""
    tasks = _topological_sort(graph.get("tasks", []))

    def _run(cmd: list[str]) -> subprocess.CompletedProcess | None:
        try:
            return subprocess.run(cmd, capture_output=True)
        except FileNotFoundError:
            logger.warning("beads_sync: bd not found in PATH, skipping sync")
            return None

    for task in tasks:
        result = _run(["bd", "create", task["id"], "--title", task["objective"], "--status", "pending"])
        if result is None:
            return  # bd not available — skip all
        if result.returncode != 0:
            stderr = result.stderr.decode(errors="replace")
            if "already exists" not in stderr:
                logger.warning("beads_sync: bd create failed for %s: %s", task["id"], stderr)
                if conn is not None:
                    try:
                        event_repo = EventRepo(conn)
                        event_repo.insert(run_id, "BEADS_SYNC_WARNING", "system",
                                          payload={"task_id": task["id"], "stderr": stderr})
                    except Exception as e:
                        logger.warning("beads_sync: failed to log event: %s", e)

    for task in graph.get("tasks", []):
        for dep in task.get("deps", []):
            _run(["bd", "dep", "add", task["id"], dep])
