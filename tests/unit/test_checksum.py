import os
import hashlib
import pytest
from pathlib import Path
from ai_dev_system.storage.checksum import checksum_file, checksum_folder, checksum_artifact

def test_checksum_file(tmp_path):
    f = tmp_path / "test.txt"
    f.write_bytes(b"hello world")
    result = checksum_file(str(f))
    expected = hashlib.sha256(b"hello world").hexdigest()
    assert result == expected

def test_checksum_folder_deterministic(tmp_path):
    (tmp_path / "a.txt").write_text("aaa")
    (tmp_path / "b.txt").write_text("bbb")
    c1 = checksum_folder(str(tmp_path))
    c2 = checksum_folder(str(tmp_path))
    assert c1 == c2

def test_checksum_folder_changes_when_file_changes(tmp_path):
    (tmp_path / "a.txt").write_text("aaa")
    c1 = checksum_folder(str(tmp_path))
    (tmp_path / "a.txt").write_text("bbb")
    c2 = checksum_folder(str(tmp_path))
    assert c1 != c2

def test_checksum_folder_order_independent(tmp_path):
    """Same files = same checksum regardless of creation order."""
    d1 = tmp_path / "d1"
    d2 = tmp_path / "d2"
    d1.mkdir(); d2.mkdir()
    (d1 / "x.txt").write_text("x"); (d1 / "y.txt").write_text("y")
    (d2 / "y.txt").write_text("y"); (d2 / "x.txt").write_text("x")
    assert checksum_folder(str(d1)) == checksum_folder(str(d2))

def test_checksum_artifact_file(tmp_path):
    f = tmp_path / "f.txt"
    f.write_bytes(b"data")
    checksum, size = checksum_artifact(str(f))
    assert checksum == hashlib.sha256(b"data").hexdigest()
    assert size == 4

def test_checksum_artifact_folder(tmp_path):
    (tmp_path / "a.txt").write_bytes(b"abc")
    checksum, size = checksum_artifact(str(tmp_path))
    assert isinstance(checksum, str) and len(checksum) == 64
    assert size == 3
