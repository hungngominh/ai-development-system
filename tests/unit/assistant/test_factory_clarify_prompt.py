# tests/unit/assistant/test_factory_clarify_prompt.py
from ai_dev_system.assistant.factory import build_clarify_prompt_suffix
from ai_dev_system.config import TelegramBotConfig


def test_suffix_names_repo_and_routing_when_bound():
    bots = (TelegramBotConfig(label="Sigo", token="t", repo_path="/repos/Sigo",
                              base_branch="main"),)
    s = build_clarify_prompt_suffix("Sigo", bots)
    assert "Sigo" in s
    assert "dev_task_start" in s
    assert "dev_answer_clarify" in s
    assert "dev_run_status" in s
    assert "dev_answer_gate" in s


def test_suffix_generic_when_not_bound():
    s = build_clarify_prompt_suffix("Sigo", ())
    assert "dev_answer_clarify" in s              # routing rule still present
    assert "/repos" not in s                       # no bound-repo claim
