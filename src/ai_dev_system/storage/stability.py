import os
import time

def _snapshot(folder_path: str) -> tuple[int, int, float]:
    """Returns (total_size, file_count, max_mtime)."""
    total_size = file_count = 0
    max_mtime = 0.0
    for root, _, files in os.walk(folder_path):
        for f in files:
            abs_path = os.path.join(root, f)
            stat = os.stat(abs_path)
            total_size += stat.st_size
            file_count += 1
            max_mtime = max(max_mtime, stat.st_mtime)
    return total_size, file_count, max_mtime

def wait_until_stable(
    folder_path: str,
    poll_interval_ms: int = 100,
    stable_duration_ms: int = 200,
    timeout_s: float = 60.0,
) -> None:
    poll_s = poll_interval_ms / 1000
    stable_s = stable_duration_ms / 1000
    deadline = time.time() + timeout_s
    stable_since = None
    last_snap = None

    while time.time() < deadline:
        snap = _snapshot(folder_path)
        if snap == last_snap:
            if stable_since is None:
                stable_since = time.time()
            elif time.time() - stable_since >= stable_s:
                return
        else:
            last_snap = snap
            stable_since = None
        time.sleep(poll_s)

    raise TimeoutError(f"Folder {folder_path!r} did not stabilize within {timeout_s}s")
