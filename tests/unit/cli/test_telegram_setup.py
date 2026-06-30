import pytest

from ai_dev_system.cli import telegram_setup as ts


def test_extract_chat_id_from_message():
    updates = [
        {"update_id": 1, "message": {"chat": {"id": 5913726934},
                                     "from": {"username": "ngomi"}, "text": "hi"}}
    ]
    assert ts.extract_chat_id(updates) == (5913726934, "ngomi")


def test_extract_chat_id_returns_none_when_no_message():
    assert ts.extract_chat_id([]) is None
    assert ts.extract_chat_id([{"update_id": 1}]) is None


def test_upsert_into_empty_bots_line():
    env = "LLM_PROVIDER=claude_code\nAI_DEV_TELEGRAM_BOTS=[]\n"
    out = ts.upsert_bot_in_env(env, "my-app", "TOK", [42])
    assert 'AI_DEV_TELEGRAM_BOTS=[{"label": "my-app", "token": "TOK", "chat_ids": [42]}]' in out
    assert "LLM_PROVIDER=claude_code" in out  # other lines preserved


def test_upsert_appends_to_existing_bot():
    env = 'AI_DEV_TELEGRAM_BOTS=[{"label": "a", "token": "T1", "chat_ids": [1]}]\n'
    out = ts.upsert_bot_in_env(env, "b", "T2", [2])
    assert '"label": "a"' in out and '"label": "b"' in out
    # still one line for the key
    assert sum(1 for ln in out.splitlines() if ln.startswith("AI_DEV_TELEGRAM_BOTS=")) == 1


def test_upsert_adds_key_when_missing():
    env = "LLM_PROVIDER=claude_code\n"
    out = ts.upsert_bot_in_env(env, "a", "T1", [1])
    assert "AI_DEV_TELEGRAM_BOTS=" in out
    assert '"label": "a"' in out


def test_upsert_duplicate_label_raises():
    env = 'AI_DEV_TELEGRAM_BOTS=[{"label": "a", "token": "T1", "chat_ids": [1]}]\n'
    with pytest.raises(ValueError, match="a"):
        ts.upsert_bot_in_env(env, "a", "T2", [2])


def test_upsert_coerces_non_list_json_to_empty():
    env = 'AI_DEV_TELEGRAM_BOTS={"oops": 1}\n'
    out = ts.upsert_bot_in_env(env, "a", "T1", [1])
    line = next(ln for ln in out.splitlines() if ln.startswith("AI_DEV_TELEGRAM_BOTS="))
    import json
    bots = json.loads(line.split("=", 1)[1])
    assert bots == [{"label": "a", "token": "T1", "chat_ids": [1]}]


import json as _json
from pathlib import Path


def _transport_with_message(chat_id=42, username="ngomi"):
    payload = {
        "ok": True,
        "result": [
            {"update_id": 1, "message": {"chat": {"id": chat_id},
                                         "from": {"username": username}, "text": "hi"}}
        ],
    }

    def _t(url, data, timeout):
        return _json.dumps(payload).encode("utf-8")

    return _t


def test_run_telegram_setup_writes_bot(tmp_path):
    env = tmp_path / ".env"
    env.write_text("LLM_PROVIDER=claude_code\nAI_DEV_TELEGRAM_BOTS=[]\n")

    inputs = iter(["123:ABC", "my-app", ""])  # token, project label, host repo (skip)

    rc = ts.run_telegram_setup(
        env,
        transport=_transport_with_message(chat_id=999),
        input_fn=lambda *_a, **_k: next(inputs),
        sleep_fn=lambda *_a, **_k: None,
    )

    assert rc == 0
    text = env.read_text()
    bots = _json.loads(
        next(ln for ln in text.splitlines() if ln.startswith("AI_DEV_TELEGRAM_BOTS="))
        .split("=", 1)[1]
    )
    assert bots == [{"label": "my-app", "token": "123:ABC", "chat_ids": [999]}]


def test_run_telegram_setup_bad_token(tmp_path):
    env = tmp_path / ".env"
    env.write_text("AI_DEV_TELEGRAM_BOTS=[]\n")

    def _t(url, data, timeout):
        return _json.dumps({"ok": False, "description": "Unauthorized"}).encode("utf-8")

    rc = ts.run_telegram_setup(
        env, transport=_t,
        input_fn=lambda *_a, **_k: "BADTOKEN",
        sleep_fn=lambda *_a, **_k: None,
    )
    assert rc == 1
    assert ts.BOTS_KEY in env.read_text()  # file untouched-ish, no bot added
    assert '"label"' not in env.read_text()


def test_run_telegram_setup_timeout_no_message(tmp_path):
    env = tmp_path / ".env"
    env.write_text("AI_DEV_TELEGRAM_BOTS=[]\n")

    def _t(url, data, timeout):
        return _json.dumps({"ok": True, "result": []}).encode("utf-8")  # no messages ever

    # clock advances past deadline on the 2nd reading so the loop exits fast
    ticks = iter([0.0, 0.0, 999.0, 999.0, 999.0])
    rc = ts.run_telegram_setup(
        env, transport=_t,
        input_fn=lambda *_a, **_k: "123:ABC",
        sleep_fn=lambda *_a, **_k: None,
        clock=lambda: next(ticks),
        max_wait_s=60.0,
    )
    assert rc == 1
    assert '"label"' not in env.read_text()


def test_container_repo_path():
    assert ts.container_repo_path("my-app") == "/repos/my-app"


def test_upsert_writes_repo_fields():
    env = "AI_DEV_TELEGRAM_BOTS=[]\n"
    out = ts.upsert_bot_in_env(env, "my-app", "T", [1],
                               repo_path="/repos/my-app", base_branch="main")
    import json
    line = next(l for l in out.splitlines() if l.startswith("AI_DEV_TELEGRAM_BOTS="))
    bot = json.loads(line.split("=", 1)[1])[0]
    assert bot["repo_path"] == "/repos/my-app"
    assert bot["base_branch"] == "main"


def test_upsert_omits_repo_fields_when_empty():
    env = "AI_DEV_TELEGRAM_BOTS=[]\n"
    out = ts.upsert_bot_in_env(env, "x", "T", [1])
    import json
    bot = json.loads(
        next(l for l in out.splitlines() if l.startswith("AI_DEV_TELEGRAM_BOTS="))
        .split("=", 1)[1]
    )[0]
    assert "repo_path" not in bot and "base_branch" not in bot


def test_add_bot_mount_fresh():
    out = ts.add_bot_mount("", "my-app", "E:/Work/my-app")
    assert "services:" in out and "gateway:" in out and "volumes:" in out
    assert '"E:/Work/my-app:/repos/my-app:rw"' in out


def test_add_bot_mount_idempotent():
    once = ts.add_bot_mount("", "my-app", "E:/Work/my-app")
    twice = ts.add_bot_mount(once, "my-app", "E:/Work/my-app")
    assert twice.count("/repos/my-app:rw") == 1


def test_run_setup_binds_repo(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    repo = tmp_path / "myrepo"
    (repo / ".git").mkdir(parents=True)
    env = tmp_path / ".env"
    env.write_text("AI_DEV_TELEGRAM_BOTS=[]\n")
    inputs = iter(["123:ABC", "my-app", str(repo)])  # token, label, host repo
    import json
    rc = ts.run_telegram_setup(
        env, transport=_transport_with_message(chat_id=7),
        input_fn=lambda *_a, **_k: next(inputs),
        sleep_fn=lambda *_a, **_k: None,
    )
    assert rc == 0
    bot = json.loads(
        next(l for l in env.read_text().splitlines() if l.startswith("AI_DEV_TELEGRAM_BOTS="))
        .split("=", 1)[1]
    )[0]
    assert bot["repo_path"] == "/repos/my-app"
    assert (tmp_path / "docker-compose.override.yml").exists()
