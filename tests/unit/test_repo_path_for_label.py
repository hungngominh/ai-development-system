from ai_dev_system.config import TelegramBotConfig, repo_path_for_label


def test_returns_repo_for_matching_label():
    bots = (
        TelegramBotConfig(label="a", token="t", repo_path="/repos/A"),
        TelegramBotConfig(label="b", token="t", repo_path="/repos/B"),
    )
    assert repo_path_for_label(bots, "b") == "/repos/B"


def test_empty_for_no_match_or_no_repo():
    bots = (TelegramBotConfig(label="a", token="t", repo_path=""),)
    assert repo_path_for_label(bots, "a") == ""      # bound but no repo
    assert repo_path_for_label(bots, "zzz") == ""    # no such label
    assert repo_path_for_label((), "a") == ""        # no bots
    assert repo_path_for_label(None, "a") == ""      # None-safe
