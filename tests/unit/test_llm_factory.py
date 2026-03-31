"""
Unit tests for llm_factory.py.

No real API calls — all SDK clients are mocked.
"""

import pytest
from unittest.mock import patch

from ai_dev_system.llm_factory import LLMConfig, RealLLMClient, make_real_llm_client


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ALL_LLM_ENV_VARS = (
    "LLM_PROVIDER",
    "LLM_MODEL",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
)


def _clear_llm_env(monkeypatch):
    """Remove all LLM-related env vars to avoid cross-test leakage."""
    for var in _ALL_LLM_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def _fake_config(provider: str = "anthropic") -> LLMConfig:
    return LLMConfig(
        provider=provider,
        model="test-model",
        api_key="test-key",
    )


# ---------------------------------------------------------------------------
# LLMConfig.from_env()
# ---------------------------------------------------------------------------

class TestLLMConfigFromEnv:
    def test_from_env_anthropic(self, monkeypatch):
        _clear_llm_env(monkeypatch)
        monkeypatch.setenv("LLM_PROVIDER", "anthropic")
        monkeypatch.setenv("LLM_MODEL", "claude-opus-4-5")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")

        config = LLMConfig.from_env()

        assert config.provider == "anthropic"
        assert config.model == "claude-opus-4-5"
        assert config.api_key == "sk-ant-test"

    def test_from_env_openai(self, monkeypatch):
        _clear_llm_env(monkeypatch)
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.setenv("LLM_MODEL", "gpt-4o")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-oai-test")

        config = LLMConfig.from_env()

        assert config.provider == "openai"
        assert config.model == "gpt-4o"
        assert config.api_key == "sk-oai-test"

    def test_missing_provider(self, monkeypatch):
        _clear_llm_env(monkeypatch)
        monkeypatch.setenv("LLM_MODEL", "any-model")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "key")

        with pytest.raises(ValueError, match="LLM_PROVIDER"):
            LLMConfig.from_env()

    def test_invalid_provider(self, monkeypatch):
        _clear_llm_env(monkeypatch)
        monkeypatch.setenv("LLM_PROVIDER", "gemini")
        monkeypatch.setenv("LLM_MODEL", "any-model")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "key")

        with pytest.raises(ValueError, match="'anthropic' or 'openai'"):
            LLMConfig.from_env()

    def test_missing_model(self, monkeypatch):
        _clear_llm_env(monkeypatch)
        monkeypatch.setenv("LLM_PROVIDER", "anthropic")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "key")

        with pytest.raises(ValueError, match="LLM_MODEL"):
            LLMConfig.from_env()

    def test_missing_anthropic_key(self, monkeypatch):
        _clear_llm_env(monkeypatch)
        monkeypatch.setenv("LLM_PROVIDER", "anthropic")
        monkeypatch.setenv("LLM_MODEL", "claude-opus-4-5")

        with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
            LLMConfig.from_env()

    def test_missing_openai_key(self, monkeypatch):
        _clear_llm_env(monkeypatch)
        monkeypatch.setenv("LLM_PROVIDER", "openai")
        monkeypatch.setenv("LLM_MODEL", "gpt-4o")

        with pytest.raises(ValueError, match="OPENAI_API_KEY"):
            LLMConfig.from_env()


# ---------------------------------------------------------------------------
# RealLLMClient.complete()
# ---------------------------------------------------------------------------

class TestRealLLMClientComplete:
    def test_complete_anthropic(self, mocker):
        mock_anthropic_cls = mocker.patch("ai_dev_system.llm_factory.anthropic.Anthropic")
        mock_client = mock_anthropic_cls.return_value

        # Set up the response structure
        mock_response = mocker.MagicMock()
        mock_response.content[0].text = "Anthropic says hello"
        mock_client.messages.create.return_value = mock_response

        llm = RealLLMClient(_fake_config(provider="anthropic"))
        result = llm.complete("sys prompt", "user prompt")

        assert result == "Anthropic says hello"
        mock_client.messages.create.assert_called_once_with(
            model="test-model",
            max_tokens=4096,
            system="sys prompt",
            messages=[{"role": "user", "content": "user prompt"}],
        )

    def test_complete_openai(self, mocker):
        mock_openai_cls = mocker.patch("ai_dev_system.llm_factory.openai.OpenAI")
        mock_client = mock_openai_cls.return_value

        # Set up the response structure
        mock_response = mocker.MagicMock()
        mock_response.choices[0].message.content = "OpenAI says hello"
        mock_client.chat.completions.create.return_value = mock_response

        llm = RealLLMClient(_fake_config(provider="openai"))
        result = llm.complete("sys prompt", "user prompt")

        assert result == "OpenAI says hello"
        mock_client.chat.completions.create.assert_called_once_with(
            model="test-model",
            messages=[
                {"role": "system", "content": "sys prompt"},
                {"role": "user", "content": "user prompt"},
            ],
        )

    def test_complete_openai_null_content(self, mocker):
        mock_openai_cls = mocker.patch("ai_dev_system.llm_factory.openai.OpenAI")
        mock_client = mock_openai_cls.return_value

        mock_response = mocker.MagicMock()
        mock_response.choices[0].message.content = None
        mock_response.choices[0].finish_reason = "content_filter"
        mock_client.chat.completions.create.return_value = mock_response

        llm = RealLLMClient(_fake_config(provider="openai"))

        with pytest.raises(ValueError, match="null content"):
            llm.complete("sys prompt", "user prompt")

    def test_complete_propagates_exception(self, mocker):
        mock_anthropic_cls = mocker.patch("ai_dev_system.llm_factory.anthropic.Anthropic")
        mock_client = mock_anthropic_cls.return_value
        mock_client.messages.create.side_effect = Exception("API exploded")

        llm = RealLLMClient(_fake_config(provider="anthropic"))

        with pytest.raises(Exception, match="API exploded"):
            llm.complete("sys", "user")


# ---------------------------------------------------------------------------
# RealLLMClient.judge_criterion()
# ---------------------------------------------------------------------------

class TestRealLLMClientJudgeCriterion:
    def _make_llm_with_complete(self, mocker, response_text: str) -> RealLLMClient:
        """Create a RealLLMClient with complete() mocked to return response_text."""
        mocker.patch("ai_dev_system.llm_factory.anthropic.Anthropic")
        llm = RealLLMClient(_fake_config())
        mocker.patch.object(llm, "complete", return_value=response_text)
        return llm

    def test_judge_pass(self, mocker):
        llm = self._make_llm_with_complete(
            mocker, '{"verdict":"PASS","confidence":0.9,"reasoning":"all good"}'
        )
        verdict, confidence, reasoning = llm.judge_criterion("AC-1", "Some criterion", ["ev1"])
        assert verdict == "PASS"
        assert confidence == 0.9
        assert reasoning == "all good"

    def test_judge_fail(self, mocker):
        llm = self._make_llm_with_complete(
            mocker, '{"verdict":"FAIL","confidence":0.3,"reasoning":"missing evidence"}'
        )
        verdict, confidence, reasoning = llm.judge_criterion("AC-2", "Another criterion", [])
        assert verdict == "FAIL"
        assert confidence == 0.3
        assert reasoning == "missing evidence"

    def test_confidence_clamp_high(self, mocker):
        llm = self._make_llm_with_complete(
            mocker, '{"verdict":"PASS","confidence":1.5,"reasoning":"over-confident"}'
        )
        _, confidence, _ = llm.judge_criterion("AC-3", "criterion", ["ev"])
        assert confidence == 1.0

    def test_confidence_clamp_low(self, mocker):
        llm = self._make_llm_with_complete(
            mocker, '{"verdict":"FAIL","confidence":-0.1,"reasoning":"negative"}'
        )
        _, confidence, _ = llm.judge_criterion("AC-4", "criterion", ["ev"])
        assert confidence == 0.0

    def test_code_fences_stripped(self, mocker):
        raw = '```json\n{"verdict":"PASS","confidence":0.8,"reasoning":"clean parse"}\n```'
        llm = self._make_llm_with_complete(mocker, raw)
        verdict, confidence, reasoning = llm.judge_criterion("AC-5", "criterion", ["ev"])
        assert verdict == "PASS"
        assert confidence == 0.8
        assert reasoning == "clean parse"

    def test_invalid_json_raises(self, mocker):
        llm = self._make_llm_with_complete(mocker, "not json at all")
        with pytest.raises(ValueError, match="AC-6"):
            llm.judge_criterion("AC-6", "criterion", ["ev"])

    def test_invalid_verdict_raises(self, mocker):
        llm = self._make_llm_with_complete(
            mocker, '{"verdict":"MAYBE","confidence":0.5,"reasoning":"unsure"}'
        )
        with pytest.raises(ValueError, match="MAYBE"):
            llm.judge_criterion("AC-7", "criterion", ["ev"])

    def test_judge_propagates_backend_exception(self, mocker):
        mocker.patch("ai_dev_system.llm_factory.anthropic.Anthropic")
        llm = RealLLMClient(_fake_config())
        mocker.patch.object(llm, "complete", side_effect=Exception("backend down"))

        with pytest.raises(Exception, match="backend down"):
            llm.judge_criterion("AC-8", "criterion", ["ev"])

    def test_missing_confidence_raises(self):
        """LLM response missing 'confidence' key → ValueError."""
        client = RealLLMClient(LLMConfig(provider="anthropic", model="test", api_key="key"))
        with patch.object(client, "complete", return_value='{"verdict":"PASS","reasoning":"ok"}'):
            with pytest.raises(ValueError, match="missing required fields"):
                client.judge_criterion("AC-1", "criterion", ["evidence"])


# ---------------------------------------------------------------------------
# make_real_llm_client()
# ---------------------------------------------------------------------------

class TestMakeRealLLMClient:
    def test_make_real_llm_client_success(self, monkeypatch, mocker):
        _clear_llm_env(monkeypatch)
        monkeypatch.setenv("LLM_PROVIDER", "anthropic")
        monkeypatch.setenv("LLM_MODEL", "claude-opus-4-5")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        mocker.patch("ai_dev_system.llm_factory.anthropic.Anthropic")

        client = make_real_llm_client()

        assert isinstance(client, RealLLMClient)

    def test_make_real_llm_client_missing_env(self, monkeypatch):
        _clear_llm_env(monkeypatch)

        with pytest.raises(RuntimeError, match="LLM_PROVIDER"):
            make_real_llm_client()
