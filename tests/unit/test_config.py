import os
import pytest
from ai_dev_system.config import Config

def test_config_reads_from_env(monkeypatch):
    monkeypatch.setenv("STORAGE_ROOT", "/tmp/test-data")
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/test")
    cfg = Config.from_env()
    assert cfg.storage_root == "/tmp/test-data"
    assert cfg.database_url == "postgresql://localhost/test"

def test_config_raises_if_missing(monkeypatch):
    monkeypatch.delenv("STORAGE_ROOT", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    with pytest.raises(ValueError, match="STORAGE_ROOT"):
        Config.from_env()
