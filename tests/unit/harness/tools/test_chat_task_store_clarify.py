from ai_dev_system.harness.tools.chat_task_store import ChatTaskStore


def test_set_pending_stores_new_fields(tmp_path):
    s = ChatTaskStore(str(tmp_path))
    s.set_pending("Sigo", "5913", spec_id="ab", repo="/r", base_branch="main",
                  idea="add OwnerId")
    rec = s.get_pending("Sigo", "5913")
    assert rec["idea"] == "add OwnerId"
    assert rec["phase"] == "generating"
    assert rec["round"] == 0
    assert rec["surface"] == "Sigo" and rec["chat_id"] == "5913"


def test_update_partial(tmp_path):
    s = ChatTaskStore(str(tmp_path))
    s.set_pending("Sigo", "5913", spec_id="ab", repo="/r", base_branch="main", idea="x")
    s.update("Sigo", "5913", phase="awaiting_clarify", clarify_questions=["Q?"])
    rec = s.get_pending("Sigo", "5913")
    assert rec["phase"] == "awaiting_clarify"
    assert rec["clarify_questions"] == ["Q?"]
    assert rec["spec_id"] == "ab"                 # untouched fields preserved


def test_update_missing_is_noop(tmp_path):
    s = ChatTaskStore(str(tmp_path))
    s.update("nope", "0", phase="x")              # must not raise
    assert s.get_pending("nope", "0") is None


def test_list_pending_returns_all_with_routing(tmp_path):
    s = ChatTaskStore(str(tmp_path))
    s.set_pending("A", "1", spec_id="a", repo="/r", base_branch="m", idea="i1")
    s.set_pending("B", "2", spec_id="b", repo="/r", base_branch="m", idea="i2")
    recs = {(r["surface"], r["chat_id"]) for r in s.list_pending()}
    assert recs == {("A", "1"), ("B", "2")}


def test_list_pending_skips_corrupt(tmp_path):
    s = ChatTaskStore(str(tmp_path))
    s.set_pending("A", "1", spec_id="a", repo="/r", base_branch="m", idea="i")
    (tmp_path / "chat_tasks" / "broken__x.json").write_text("{not json", encoding="utf-8")
    recs = s.list_pending()
    assert len(recs) == 1 and recs[0]["surface"] == "A"
