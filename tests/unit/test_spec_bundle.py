import os
from pathlib import Path
from ai_dev_system.normalize import normalize_idea
from ai_dev_system.spec_bundle import generate_spec_bundle, validate_spec_bundle, REQUIRED_FILES


def test_generate_creates_all_files(tmp_path):
    brief = normalize_idea("Build a forum")
    brief["problem"] = "No knowledge sharing"
    brief["goal"] = "Share knowledge internally"
    brief["constraints"]["hard"] = ["Must use PostgreSQL"]
    brief["success_signals"] = ["Search < 5s"]
    bundle = generate_spec_bundle(brief, tmp_path / "specs")
    assert bundle.version == 1
    assert bundle.root_dir == tmp_path / "specs"
    for filename in REQUIRED_FILES:
        assert filename in bundle.files
        assert bundle.files[filename].exists()
        assert bundle.files[filename].stat().st_size > 0


def test_generate_problem_md_content(tmp_path):
    brief = normalize_idea("Build a forum")
    brief["problem"] = "No knowledge sharing"
    brief["target_users"] = "Internal devs"
    bundle = generate_spec_bundle(brief, tmp_path / "specs")
    content = bundle.files["problem.md"].read_text()
    assert "Build a forum" in content
    assert "No knowledge sharing" in content
    assert "Internal devs" in content
    assert "Do Not Interpret" in content


def test_generate_constraints_with_prefixes(tmp_path):
    brief = normalize_idea("test")
    brief["constraints"]["hard"] = ["Must use PostgreSQL"]
    brief["constraints"]["soft"] = ["Prefer Python"]
    bundle = generate_spec_bundle(brief, tmp_path / "specs")
    content = bundle.files["constraints.md"].read_text()
    assert "[HARD]" in content
    assert "[SOFT]" in content
    assert "PostgreSQL" in content
    assert "Python" in content


def test_generate_empty_fields_show_placeholder(tmp_path):
    brief = normalize_idea("test")
    bundle = generate_spec_bundle(brief, tmp_path / "specs")
    content = bundle.files["problem.md"].read_text()
    assert "(not specified" in content


def test_validate_spec_bundle_clean(tmp_path):
    brief = normalize_idea("test")
    bundle = generate_spec_bundle(brief, tmp_path / "specs")
    warnings = validate_spec_bundle(bundle.root_dir)
    assert warnings == []


def test_validate_spec_bundle_missing_file(tmp_path):
    tmp_path.mkdir(exist_ok=True)
    (tmp_path / "problem.md").write_text("x")
    warnings = validate_spec_bundle(tmp_path)
    assert len(warnings) == 4  # 4 missing files
