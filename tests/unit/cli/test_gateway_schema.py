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
