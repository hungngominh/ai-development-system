"""M5.A loader tests.

Covers: snake_case conversion, parse happy path, frontmatter
validation errors (missing markers, missing keys, invalid
debate_role, invalid YAML), file-based load with fallback when .md
missing, real sample files in references/agency-agents/ load
without error.
"""

import pytest

from ai_dev_system.debate.agents import (
    AGENCY_AGENTS_DIR,
    AGENT_PROMPTS,
    AgentLoadError,
    AgentSpec,
    load_agent_prompt,
    parse_agent_md,
    snake_case,
)


# ---- snake_case ----


def test_snake_case_pascal():
    assert snake_case("SecuritySpecialist") == "security_specialist"


def test_snake_case_acronym_run():
    assert snake_case("APIGateway") == "api_gateway"


def test_snake_case_single_word():
    assert snake_case("Backend") == "backend"


def test_snake_case_already_snake():
    assert snake_case("backend_architect") == "backend_architect"


# ---- parse_agent_md happy path ----


VALID_MD = """---
agent_key: TestAgent
domain: backend
version: 1
aliases: [ta, tester]
debate_role: critic_first
typical_paired_with: [Other]
---

# Identity
Body content here.

# Mission
More body.
"""


def test_parse_minimal_valid_returns_spec():
    spec = parse_agent_md(VALID_MD)
    assert isinstance(spec, AgentSpec)
    assert spec.key == "TestAgent"
    assert spec.domain == "backend"
    assert spec.version == 1
    assert spec.aliases == ["ta", "tester"]
    assert spec.debate_role == "critic_first"
    assert spec.typical_paired_with == ["Other"]
    assert spec.system_prompt.startswith("# Identity")
    assert "# Mission" in spec.system_prompt
    assert spec.is_fallback is False


def test_parse_omits_optional_fields_with_defaults():
    md = """---
agent_key: Minimal
domain: backend
version: 1
---

# Identity
Body.
"""
    spec = parse_agent_md(md)
    assert spec.aliases == []
    assert spec.debate_role == "neutral"
    assert spec.typical_paired_with == []


def test_parse_strips_trailing_whitespace_from_body():
    md = """---
agent_key: A
domain: backend
version: 1
---

# Identity
Body.

"""
    spec = parse_agent_md(md)
    assert not spec.system_prompt.endswith("\n\n")


# ---- parse_agent_md failure modes ----


def test_parse_rejects_missing_opening_marker():
    with pytest.raises(AgentLoadError, match="must start with YAML frontmatter"):
        parse_agent_md("# Identity\nbody only")


def test_parse_rejects_missing_closing_marker():
    md = "---\nagent_key: A\ndomain: backend\nversion: 1\n# Identity\nbody"
    with pytest.raises(AgentLoadError, match="missing closing"):
        parse_agent_md(md)


def test_parse_rejects_missing_required_keys():
    md = """---
agent_key: A
domain: backend
---

body
"""
    with pytest.raises(AgentLoadError, match="missing required key 'version'"):
        parse_agent_md(md)


def test_parse_rejects_invalid_debate_role():
    md = """---
agent_key: A
domain: backend
version: 1
debate_role: bystander
---

body
"""
    with pytest.raises(AgentLoadError, match="Invalid debate_role"):
        parse_agent_md(md)


def test_parse_rejects_invalid_yaml():
    md = """---
agent_key: [unclosed
---

body
"""
    with pytest.raises(AgentLoadError, match="Frontmatter YAML invalid"):
        parse_agent_md(md)


def test_parse_rejects_non_mapping_frontmatter():
    md = """---
- just
- a
- list
---

body
"""
    with pytest.raises(AgentLoadError, match="must be a YAML mapping"):
        parse_agent_md(md)


# ---- load_agent_prompt with fallback ----


def test_load_existing_file_returns_full_spec(tmp_path):
    agent_file = tmp_path / "test_agent.md"
    agent_file.write_text(VALID_MD, encoding="utf-8")
    spec = load_agent_prompt("TestAgent", agents_dir=tmp_path)
    assert spec.is_fallback is False
    assert spec.file_path == agent_file


def test_load_missing_file_warns_and_returns_fallback(tmp_path):
    with pytest.warns(UserWarning, match="missing for 'SecuritySpecialist'"):
        spec = load_agent_prompt("SecuritySpecialist", agents_dir=tmp_path)
    assert spec.is_fallback is True
    assert spec.key == "SecuritySpecialist"
    # legacy prompt content present
    assert spec.system_prompt == AGENT_PROMPTS["SecuritySpecialist"]
    assert spec.file_path is None


def test_load_missing_file_for_unknown_key_uses_generic_fallback(tmp_path):
    with pytest.warns(UserWarning, match="missing for 'NewAgent'"):
        spec = load_agent_prompt("NewAgent", agents_dir=tmp_path)
    assert spec.is_fallback is True
    assert spec.system_prompt  # non-empty generic
    assert spec.key == "NewAgent"


def test_load_uses_snake_case_filename(tmp_path):
    # File is snake_case, key is PascalCase
    (tmp_path / "some_pascal_case.md").write_text(
        VALID_MD.replace("agent_key: TestAgent", "agent_key: SomePascalCase"),
        encoding="utf-8",
    )
    spec = load_agent_prompt("SomePascalCase", agents_dir=tmp_path)
    assert spec.is_fallback is False
    assert spec.key == "SomePascalCase"


# ---- real sample files ----


def test_security_specialist_sample_loads_cleanly():
    spec = load_agent_prompt("SecuritySpecialist")
    assert spec.is_fallback is False, "Expected real .md, got fallback"
    assert spec.key == "SecuritySpecialist"
    assert spec.domain == "security"
    assert spec.debate_role == "critic_first"
    assert "BackendArchitect" in spec.typical_paired_with
    # Dense prompt: should be >= 500 chars per spec quality bar
    assert len(spec.system_prompt) > 500


def test_backend_architect_sample_loads_cleanly():
    spec = load_agent_prompt("BackendArchitect")
    assert spec.is_fallback is False
    assert spec.key == "BackendArchitect"
    assert spec.domain == "backend"
    assert spec.debate_role == "advocate_first"
    assert "SecuritySpecialist" in spec.typical_paired_with
    assert len(spec.system_prompt) > 500


def test_agency_agents_dir_resolves_to_repo_path():
    # Sanity: AGENCY_AGENTS_DIR should point at an existing directory
    assert AGENCY_AGENTS_DIR.exists()
    assert AGENCY_AGENTS_DIR.is_dir()
