from ai_dev_system.harness.tools.chat_task_store import ChatTaskStore


def test_set_get_clear(tmp_path):
    s = ChatTaskStore(str(tmp_path))
    assert s.get_pending("tg", "1") is None
    s.set_pending("tg", "1", spec_id="abc", repo="/repos/x", base_branch="main")
    p = s.get_pending("tg", "1")
    assert p["spec_id"] == "abc" and p["repo"] == "/repos/x" and p["base_branch"] == "main"
    assert p["pr_url"] in (None, "")
    s.set_pr_url("tg", "1", "https://github.com/o/r/pull/1")
    assert s.get_pending("tg", "1")["pr_url"].endswith("/pull/1")
    s.clear("tg", "1")
    assert s.get_pending("tg", "1") is None


def test_key_isolation(tmp_path):
    s = ChatTaskStore(str(tmp_path))
    s.set_pending("tg", "1", spec_id="a", repo="/r", base_branch="main")
    assert s.get_pending("tg", "2") is None
