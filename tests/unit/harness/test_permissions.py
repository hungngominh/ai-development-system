import asyncio
from types import SimpleNamespace

from ai_dev_system.harness.permissions import make_permission_callback


def _decide(cb, name, inp=None):
    return asyncio.run(cb(name, inp or {}, SimpleNamespace()))


def test_allows_our_mcp_tools():
    cb = make_permission_callback()
    res = _decide(cb, "mcp__ai_dev__now")
    assert res.behavior == "allow"


def test_allows_read_only_builtins():
    cb = make_permission_callback()
    assert _decide(cb, "Read", {"file_path": "/x"}).behavior == "allow"


def test_denies_unlisted_tool():
    cb = make_permission_callback()
    res = _decide(cb, "Bash", {"command": "rm -rf /"})
    assert res.behavior == "deny"
    assert res.message


def test_extra_allowed_is_honored():
    cb = make_permission_callback(extra_allowed={"Bash"})
    assert _decide(cb, "Bash", {"command": "ls"}).behavior == "allow"
