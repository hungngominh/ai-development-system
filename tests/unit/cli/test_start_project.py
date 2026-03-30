import uuid
import pytest
from ai_dev_system.cli.start_project import name_to_slug, make_project_id


class TestNameToSlug:
    def test_basic_lowercase(self):
        assert name_to_slug("Forum Kien Thuc") == "forum-kien-thuc"

    def test_spaces_become_dashes(self):
        assert name_to_slug("my project name") == "my-project-name"

    def test_special_chars_removed(self):
        assert name_to_slug("hello! world@2026") == "hello-world-2026"

    def test_leading_trailing_dashes_stripped(self):
        assert name_to_slug("  --forum--  ") == "forum"

    def test_truncated_to_40_chars(self):
        long = "a" * 50
        assert len(name_to_slug(long)) == 40

    def test_vietnamese_diacritics_stripped(self):
        result = name_to_slug("Kiến Thức Nội Bộ")
        assert all(c in "abcdefghijklmnopqrstuvwxyz0123456789-" for c in result)
        assert "kien" in result or "kin" in result  # tuỳ fallback

    def test_already_ascii_unchanged(self):
        assert name_to_slug("forum-kien-thuc") == "forum-kien-thuc"

    def test_consecutive_special_chars_single_dash(self):
        assert name_to_slug("hello   world") == "hello-world"


class TestMakeProjectId:
    def test_returns_string_uuid(self):
        result = make_project_id("forum-kien-thuc")
        parsed = uuid.UUID(result)
        assert str(parsed) == result

    def test_deterministic_same_slug(self):
        assert make_project_id("my-project") == make_project_id("my-project")

    def test_different_slugs_different_ids(self):
        assert make_project_id("project-a") != make_project_id("project-b")
