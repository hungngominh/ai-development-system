"""
Unit tests for llm_factory.py.

No real API calls — all SDK clients are mocked.
"""

import os

import pytest
from unittest.mock import patch

from ai_dev_system.llm_factory import (
    ClaudeCodeLLMClient,
    LLMConfig,
    RealLLMClient,
    make_real_llm_client,
    make_llm_client,
    resolve_step_model_effort,
    STEP_PROFILES,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ALL_LLM_ENV_VARS = (
    "LLM_PROVIDER",
    "LLM_MODEL",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "AZURE_OPENAI_API_KEY",
    "AZURE_OPENAI_ENDPOINT",
    "AZURE_OPENAI_API_VERSION",
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

        with pytest.raises(ValueError, match="claude_code"):
            LLMConfig.from_env()

    def test_from_env_azure(self, monkeypatch):
        _clear_llm_env(monkeypatch)
        monkeypatch.setenv("LLM_PROVIDER", "azure")
        monkeypatch.setenv("LLM_MODEL", "my-gpt4o-deployment")
        monkeypatch.setenv("AZURE_OPENAI_API_KEY", "az-key")
        monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://my-resource.openai.azure.com/")
        monkeypatch.setenv("AZURE_OPENAI_API_VERSION", "2024-05-01-preview")

        config = LLMConfig.from_env()

        assert config.provider == "azure"
        assert config.model == "my-gpt4o-deployment"
        assert config.api_key == "az-key"
        assert config.azure_endpoint == "https://my-resource.openai.azure.com/"
        assert config.api_version == "2024-05-01-preview"

    def test_from_env_azure_default_api_version(self, monkeypatch):
        _clear_llm_env(monkeypatch)
        monkeypatch.setenv("LLM_PROVIDER", "azure")
        monkeypatch.setenv("LLM_MODEL", "my-gpt4o-deployment")
        monkeypatch.setenv("AZURE_OPENAI_API_KEY", "az-key")
        monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://my-resource.openai.azure.com/")

        config = LLMConfig.from_env()

        assert config.api_version == "2024-02-01"

    def test_missing_azure_key(self, monkeypatch):
        _clear_llm_env(monkeypatch)
        monkeypatch.setenv("LLM_PROVIDER", "azure")
        monkeypatch.setenv("LLM_MODEL", "my-gpt4o-deployment")
        monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://my-resource.openai.azure.com/")

        with pytest.raises(ValueError, match="AZURE_OPENAI_API_KEY"):
            LLMConfig.from_env()

    def test_missing_azure_endpoint(self, monkeypatch):
        _clear_llm_env(monkeypatch)
        monkeypatch.setenv("LLM_PROVIDER", "azure")
        monkeypatch.setenv("LLM_MODEL", "my-gpt4o-deployment")
        monkeypatch.setenv("AZURE_OPENAI_API_KEY", "az-key")

        with pytest.raises(ValueError, match="AZURE_OPENAI_ENDPOINT"):
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

    def test_complete_azure(self, mocker):
        mock_azure_cls = mocker.patch("ai_dev_system.llm_factory.openai.AzureOpenAI")
        mock_client = mock_azure_cls.return_value

        mock_response = mocker.MagicMock()
        mock_response.choices[0].message.content = "Azure says hello"
        mock_client.chat.completions.create.return_value = mock_response

        config = LLMConfig(
            provider="azure",
            model="my-gpt4o-deployment",
            api_key="az-key",
            azure_endpoint="https://my-resource.openai.azure.com/",
            api_version="2024-02-01",
        )
        llm = RealLLMClient(config)
        result = llm.complete("sys prompt", "user prompt")

        assert result == "Azure says hello"
        mock_azure_cls.assert_called_once_with(
            api_key="az-key",
            azure_endpoint="https://my-resource.openai.azure.com/",
            api_version="2024-02-01",
        )
        mock_client.chat.completions.create.assert_called_once_with(
            model="my-gpt4o-deployment",
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

    def test_make_claude_code_client(self, monkeypatch):
        _clear_llm_env(monkeypatch)
        monkeypatch.setenv("LLM_PROVIDER", "claude_code")
        monkeypatch.setenv("LLM_MODEL", "sonnet")

        client = make_real_llm_client()

        assert isinstance(client, ClaudeCodeLLMClient)
        # Default step must stay effort-free (historical behaviour).
        assert client._effort is None
        assert client._model == "sonnet"


# ---------------------------------------------------------------------------
# Per-step effort on the CLI client
# ---------------------------------------------------------------------------

_EXTRA_STEP_ENV = (
    "AI_DEV_MODEL_DEBATE", "AI_DEV_EFFORT_DEBATE",
    "AI_DEV_MODEL_INTAKE", "AI_DEV_EFFORT_INTAKE",
)


def _clear_step_env(monkeypatch):
    for var in _EXTRA_STEP_ENV:
        monkeypatch.delenv(var, raising=False)


class TestClaudeCodeEffortCmd:
    def test_effort_flag_added_when_set(self):
        client = ClaudeCodeLLMClient(model="opus", effort="high")
        with patch.object(ClaudeCodeLLMClient, "_resolve_claude_cmd", return_value="claude"):
            cmd = client._build_cmd("SYS", "USER")
        assert "--effort" in cmd and cmd[cmd.index("--effort") + 1] == "high"
        assert cmd[cmd.index("--model") + 1] == "opus"
        # prompt + system still passed
        assert "--system-prompt" in cmd and cmd[-1] == "USER"

    def test_no_effort_flag_when_unset(self):
        client = ClaudeCodeLLMClient(model="sonnet")
        with patch.object(ClaudeCodeLLMClient, "_resolve_claude_cmd", return_value="claude"):
            cmd = client._build_cmd("SYS", "USER")
        assert "--effort" not in cmd


class TestResolveStepModelEffort:
    def test_profile_values(self, monkeypatch):
        _clear_llm_env(monkeypatch)
        _clear_step_env(monkeypatch)
        assert resolve_step_model_effort("debate") == STEP_PROFILES["debate"]
        assert resolve_step_model_effort("executor") == ("opus", "xhigh")
        # Haiku step carries no effort (CLI rejects --effort on Haiku).
        assert resolve_step_model_effort("intake") == ("haiku", None)

    def test_env_override_wins(self, monkeypatch):
        _clear_llm_env(monkeypatch)
        _clear_step_env(monkeypatch)
        monkeypatch.setenv("AI_DEV_MODEL_DEBATE", "fable")
        monkeypatch.setenv("AI_DEV_EFFORT_DEBATE", "max")
        assert resolve_step_model_effort("debate") == ("fable", "max")

    def test_env_effort_none_disables(self, monkeypatch):
        _clear_llm_env(monkeypatch)
        _clear_step_env(monkeypatch)
        monkeypatch.setenv("AI_DEV_EFFORT_DEBATE", "none")
        model, effort = resolve_step_model_effort("debate")
        assert model == "opus" and effort is None

    def test_unknown_step_falls_back_to_env_model(self, monkeypatch):
        _clear_llm_env(monkeypatch)
        _clear_step_env(monkeypatch)
        monkeypatch.setenv("LLM_MODEL", "sonnet")
        assert resolve_step_model_effort("default") == ("sonnet", None)


class TestMakeLLMClientPerStep:
    def test_claude_code_step_applies_profile(self, monkeypatch):
        _clear_llm_env(monkeypatch)
        _clear_step_env(monkeypatch)
        monkeypatch.setenv("LLM_PROVIDER", "claude_code")
        client = make_llm_client("debate")
        assert isinstance(client, ClaudeCodeLLMClient)
        assert (client._model, client._effort) == STEP_PROFILES["debate"]

    def test_claude_code_default_step_no_effort(self, monkeypatch):
        _clear_llm_env(monkeypatch)
        _clear_step_env(monkeypatch)
        monkeypatch.setenv("LLM_PROVIDER", "claude_code")
        monkeypatch.setenv("LLM_MODEL", "sonnet")
        client = make_llm_client("default")
        assert isinstance(client, ClaudeCodeLLMClient)
        assert client._effort is None

    def test_api_provider_ignores_step(self, monkeypatch, mocker):
        _clear_llm_env(monkeypatch)
        _clear_step_env(monkeypatch)
        monkeypatch.setenv("LLM_PROVIDER", "anthropic")
        monkeypatch.setenv("LLM_MODEL", "claude-opus-4-8")
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
        mocker.patch("ai_dev_system.llm_factory.anthropic.Anthropic")
        client = make_llm_client("debate")
        assert isinstance(client, RealLLMClient)


# ---------------------------------------------------------------------------
# ClaudeCodeLLMClient._resolve_claude_cmd() — Windows exe-vs-cmd preference
# ---------------------------------------------------------------------------

class TestResolveClaudeCmdWindows:
    """The native claude.exe must be preferred over the claude.cmd shim.

    The .cmd shim re-expands argv through cmd.exe, which mangles prompts
    containing shell metacharacters (``<AgentKey>``, ``A|B``). The .exe is
    invoked directly and passes prompts through verbatim.
    """

    def test_prefers_native_exe_over_cmd_shim(self, mocker):
        mocker.patch("ai_dev_system.llm_factory.sys.platform", "win32")
        shim = os.path.join("C:\\", "Users", "me", "AppData", "Roaming", "npm", "claude.cmd")
        shim_dir = os.path.dirname(shim)
        derived_exe = os.path.join(
            shim_dir, "node_modules", "@anthropic-ai", "claude-code", "bin", "claude.exe"
        )

        def fake_which(name):
            return {"claude.exe": None, "claude.cmd": shim}.get(name)

        mocker.patch("ai_dev_system.llm_factory.shutil.which", side_effect=fake_which)
        # Only the derived bundled exe exists on disk.
        mocker.patch(
            "ai_dev_system.llm_factory.os.path.exists",
            side_effect=lambda p: p == derived_exe,
        )

        assert ClaudeCodeLLMClient._resolve_claude_cmd() == derived_exe

    def test_falls_back_to_cmd_shim_when_no_exe(self, mocker):
        mocker.patch("ai_dev_system.llm_factory.sys.platform", "win32")
        shim = os.path.join("C:\\", "Users", "me", "AppData", "Roaming", "npm", "claude.cmd")

        mocker.patch(
            "ai_dev_system.llm_factory.shutil.which",
            side_effect=lambda n: shim if n == "claude.cmd" else None,
        )
        # No exe anywhere on disk.
        mocker.patch("ai_dev_system.llm_factory.os.path.exists", return_value=False)

        assert ClaudeCodeLLMClient._resolve_claude_cmd() == shim

    def test_exe_on_path_wins_immediately(self, mocker):
        mocker.patch("ai_dev_system.llm_factory.sys.platform", "win32")
        path_exe = os.path.join("C:\\", "tools", "claude.exe")

        mocker.patch(
            "ai_dev_system.llm_factory.shutil.which",
            side_effect=lambda n: path_exe if n == "claude.exe" else None,
        )
        # exists() should not even be consulted, but be safe.
        mocker.patch("ai_dev_system.llm_factory.os.path.exists", return_value=False)

        assert ClaudeCodeLLMClient._resolve_claude_cmd() == path_exe


class TestStripOuterCodeFence:
    """claude -p wraps JSON answers in ```json fences; strip the outer one."""

    def test_strips_json_fence(self):
        raw = '```json\n[{"id": "Q1"}]\n```'
        assert ClaudeCodeLLMClient._strip_outer_code_fence(raw) == '[{"id": "Q1"}]'

    def test_strips_bare_fence(self):
        raw = '```\nhello world\n```'
        assert ClaudeCodeLLMClient._strip_outer_code_fence(raw) == "hello world"

    def test_leaves_plain_text_untouched(self):
        raw = "just some prose, no fences"
        assert ClaudeCodeLLMClient._strip_outer_code_fence(raw) == raw

    def test_preserves_inline_code_block_in_prose(self):
        # A fenced block embedded in prose (not wrapping the whole response)
        # must survive — only an outer wrapper is stripped.
        raw = "Here is code:\n```py\nx = 1\n```\nThanks!"
        assert ClaudeCodeLLMClient._strip_outer_code_fence(raw) == raw

    def test_strips_and_trims_surrounding_whitespace(self):
        raw = '\n\n```json\n{"k": 1}\n```\n\n'
        assert ClaudeCodeLLMClient._strip_outer_code_fence(raw) == '{"k": 1}'


class TestClaudeCodeCallEncoding:
    """`claude -p` output is UTF-8; the subprocess must decode as UTF-8.

    text=True alone uses the locale codec (cp1252 on Windows), which raises
    UnicodeDecodeError on bytes like 0x81 and leaves stdout=None — sinking
    every call whose LLM output contains an em-dash, smart quote, non-Latin
    text, or emoji.
    """

    def test_call_requests_utf8_decoding(self, mocker):
        mocker.patch.object(
            ClaudeCodeLLMClient, "_resolve_claude_cmd", return_value="claude.exe"
        )
        fake = mocker.MagicMock()
        fake.returncode = 0
        fake.stdout = 'résumé — "smart" café 🚀'
        run = mocker.patch("ai_dev_system.llm_factory.subprocess.run", return_value=fake)

        client = ClaudeCodeLLMClient(model="sonnet")
        out = client.complete("sys", "user")

        assert out == 'résumé — "smart" café 🚀'
        _, kwargs = run.call_args
        assert kwargs.get("encoding") == "utf-8"
        assert kwargs.get("errors") == "replace"
