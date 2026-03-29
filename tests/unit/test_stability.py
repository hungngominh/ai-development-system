import os
import time
import threading
from pathlib import Path
from ai_dev_system.storage.stability import wait_until_stable

def test_stable_folder_returns_quickly(tmp_path):
    (tmp_path / "done.txt").write_text("data")
    start = time.time()
    wait_until_stable(str(tmp_path), poll_interval_ms=50, stable_duration_ms=150)
    assert time.time() - start < 3.0

def test_unstable_folder_waits(tmp_path):
    (tmp_path / "file.txt").write_text("v1")
    writes_done = threading.Event()

    def writer():
        time.sleep(0.1)
        (tmp_path / "file.txt").write_text("v2")
        time.sleep(0.1)
        (tmp_path / "file.txt").write_text("v3")
        writes_done.set()

    t = threading.Thread(target=writer)
    t.start()
    wait_until_stable(str(tmp_path), poll_interval_ms=50, stable_duration_ms=200)
    assert writes_done.is_set()
    t.join()
