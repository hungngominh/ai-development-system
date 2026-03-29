from ai_dev_system.normalize import normalize_idea, validate_brief, SCOPE_TYPES, COMPLEXITY_HINTS


def test_normalize_produces_valid_brief():
    brief = normalize_idea("Build a forum for sharing knowledge")
    assert brief["raw_idea"] == "Build a forum for sharing knowledge"
    assert brief["id"]  # non-empty UUID
    assert brief["version"] == 1
    assert brief["source_hash"]  # non-empty sha256
    assert brief["problem"] == ""
    assert brief["target_users"] == ""
    assert brief["goal"] == ""
    assert brief["constraints"] == {"hard": [], "soft": []}
    assert brief["assumptions"] == []
    assert brief["scope"] == {"type": "unknown", "complexity_hint": "unknown"}
    assert brief["success_signals"] == []
    errors = validate_brief(brief)
    assert errors == []


def test_normalize_strips_whitespace():
    brief = normalize_idea("  Build a forum  ")
    assert brief["raw_idea"] == "Build a forum"


def test_normalize_rejects_empty():
    import pytest
    with pytest.raises(ValueError, match="non-empty"):
        normalize_idea("")
    with pytest.raises(ValueError, match="non-empty"):
        normalize_idea("   ")


def test_normalize_source_hash_deterministic():
    b1 = normalize_idea("same idea")
    b2 = normalize_idea("same idea")
    assert b1["source_hash"] == b2["source_hash"]
    assert b1["id"] != b2["id"]  # UUID is unique each time


def test_validate_missing_id():
    brief = normalize_idea("test")
    brief["id"] = ""
    assert "id is required" in validate_brief(brief)


def test_validate_bad_version():
    brief = normalize_idea("test")
    brief["version"] = 0
    assert any("version" in e for e in validate_brief(brief))


def test_validate_bad_scope_type():
    brief = normalize_idea("test")
    brief["scope"]["type"] = "invalid"
    assert any("scope.type" in e for e in validate_brief(brief))


def test_validate_extra_top_level_key():
    brief = normalize_idea("test")
    brief["extra"] = "nope"
    assert any("Extra keys" in e for e in validate_brief(brief))


def test_validate_extra_nested_key():
    brief = normalize_idea("test")
    brief["constraints"]["priority"] = "high"
    assert any("constraints" in e for e in validate_brief(brief))
