# tests/unit/gateway/test_project_registry_stores.py
from ai_dev_system.gateway.project_registry import ProjectRegistry


def test_resources_carry_per_project_stores(tmp_path):
    reg = ProjectRegistry()
    try:
        res = reg.get(str(tmp_path / "repo"))
        # link_store usable on the project DB: link then read back
        res.link_store.link("run-1", "tg", "42")
        assert res.link_store.latest_for_chat("tg", "42") == "run-1"
        # session + budget present and bound to this project's conn
        assert res.session_store is not None
        assert res.budget is not None
    finally:
        reg.close_all()


def test_two_repos_have_independent_link_stores(tmp_path):
    reg = ProjectRegistry()
    try:
        a = reg.get(str(tmp_path / "a"))
        b = reg.get(str(tmp_path / "b"))
        a.link_store.link("run-a", "tg", "1")
        # b's DB must not see a's link
        assert b.link_store.latest_for_chat("tg", "1") is None
    finally:
        reg.close_all()
