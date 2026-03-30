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


def test_config_defaults():
    cfg = Config(storage_root="/tmp", database_url="postgresql://x")
    assert cfg.poll_interval_s == 5.0
    assert cfg.heartbeat_interval_s == 30.0
    assert cfg.heartbeat_timeout_s == 120.0
    assert cfg.task_timeout_s == 3600.0
    assert isinstance(cfg.retry_policy, dict)
    assert cfg.retry_policy["EXECUTION_ERROR"]["max_retries"] == 2
    assert cfg.retry_policy["ENVIRONMENT_ERROR"]["retry_delay_s"] == 5.0


def test_config_retry_policy_keys():
    cfg = Config(storage_root="/tmp", database_url="postgresql://x")
    for key in ("EXECUTION_ERROR", "ENVIRONMENT_ERROR", "SPEC_AMBIGUITY",
                "SPEC_CONTRADICTION", "UNKNOWN"):
        assert key in cfg.retry_policy, f"Missing key: {key}"
