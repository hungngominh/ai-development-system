import json
from pathlib import Path

from ai_dev_system.debate_pipeline import _load_project_profile_dict


def test_load_profile_returns_dict_from_debate_report(tmp_path):
    report = {"brief": {"_project_profile": {"vertical": "couples app", "key_dimensions": ["x"]}}}
    art_dir = tmp_path / "art"
    art_dir.mkdir()
    (art_dir / "debate_report.json").write_text(json.dumps(report), encoding="utf-8")

    class _Conn:
        def execute(self, *a):
            class _C:
                def fetchone(self_):
                    return {"content_ref": str(art_dir)}
            return _C()
    profile = _load_project_profile_dict(_Conn(), {"debate_report_id": "abc"})
    assert profile["vertical"] == "couples app"


def test_load_profile_none_when_missing():
    class _Conn:
        def execute(self, *a):
            class _C:
                def fetchone(self_):
                    return None
            return _C()
    assert _load_project_profile_dict(_Conn(), {}) is None
