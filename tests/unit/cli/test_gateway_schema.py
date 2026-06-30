import sqlite3


def test_ensure_schema_creates_tables(tmp_path):
    from ai_dev_system.cli.commands.gateway import _ensure_schema

    db = tmp_path / "control.db"
    url = f"sqlite:///{db}"

    _ensure_schema(url)

    n = sqlite3.connect(db).execute(
        "SELECT count(*) FROM sqlite_master WHERE type='table'"
    ).fetchone()[0]
    assert n > 0


def test_ensure_schema_raises_on_control_layer_failure(monkeypatch, tmp_path):
    import pytest
    import ai_dev_system.db.migrator as migrator
    from ai_dev_system.db.migrator import MigrationResult
    from ai_dev_system.cli.commands.gateway import _ensure_schema

    monkeypatch.setattr(
        migrator, "apply_schema",
        lambda conn: [MigrationResult(name="control-layer-schema.sql", applied=False,
                                      skipped_reason="file not found")],
    )
    with pytest.raises(RuntimeError, match="schema"):
        _ensure_schema(f"sqlite:///{tmp_path / 'x.db'}")


def test_ensure_schema_does_not_raise_on_already_applied(monkeypatch, tmp_path):
    import ai_dev_system.db.migrator as migrator
    from ai_dev_system.db.migrator import MigrationResult
    from ai_dev_system.cli.commands.gateway import _ensure_schema

    monkeypatch.setattr(
        migrator, "apply_schema",
        lambda conn: [MigrationResult(name="control-layer-schema.sql", applied=True),
                      MigrationResult(name="v2.sql", applied=False, skipped_reason="already applied")],
    )
    _ensure_schema(f"sqlite:///{tmp_path / 'x.db'}")  # must NOT raise
