"""kss/llm/openai_client.py 单元测试."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from kss.llm.openai_client import LLMClient, LLMUnavailable, _resolve_credentials


# ====================================================================== #
# 凭据解析
# ====================================================================== #


class TestResolveCredentials:
    """_resolve_credentials —— 环境变量 → (key, base_url, default_model)."""

    def test_openai_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        key, base, model = _resolve_credentials()
        assert key == "sk-test"
        assert base is None
        assert model == "gpt-4o-mini"

    def test_openai_with_custom_base_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """oneAPI 网关之类的自建 base_url 应保留."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("OPENAI_BASE_URL", "https://oneapi.local/v1")
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        key, base, _ = _resolve_credentials()
        assert key == "sk-test"
        assert base == "https://oneapi.local/v1"

    def test_falls_back_to_deepseek(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """OPENAI 缺失 + DEEPSEEK 在 → 走 deepseek."""
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-test")
        key, base, model = _resolve_credentials()
        assert key == "ds-test"
        assert base == "https://api.deepseek.com/v1"
        assert model == "deepseek-chat"

    def test_both_missing_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        with pytest.raises(LLMUnavailable, match="未配置"):
            _resolve_credentials()

    def test_empty_string_treated_as_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """空字符串 / 纯空格 key 不应被认为有效."""
        monkeypatch.setenv("OPENAI_API_KEY", "   ")
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        with pytest.raises(LLMUnavailable):
            _resolve_credentials()


# ====================================================================== #
# LLMClient.complete
# ====================================================================== #


@pytest.fixture
def with_openai_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("KSS_LLM_MODEL", raising=False)


def _make_resp(content: str | None) -> SimpleNamespace:
    """构造一个伪 chat.completions.create 返回值."""
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
    )


class TestLLMClientComplete:
    """LLMClient.complete —— happy path + 失败路径."""

    def test_happy_path_returns_stripped_text(
        self, with_openai_env: None,
    ) -> None:
        with patch("openai.OpenAI") as MockOpenAI:
            instance = MagicMock()
            instance.chat.completions.create.return_value = _make_resp(
                "  今天大盘上涨 1.5%  "
            )
            MockOpenAI.return_value = instance
            client = LLMClient()
            out = client.complete(system="你是投顾", user="今天怎么样")
            assert out == "今天大盘上涨 1.5%"

    def test_api_failure_raises_llm_unavailable(
        self, with_openai_env: None,
    ) -> None:
        with patch("openai.OpenAI") as MockOpenAI:
            instance = MagicMock()
            instance.chat.completions.create.side_effect = RuntimeError(
                "429 rate limit"
            )
            MockOpenAI.return_value = instance
            client = LLMClient()
            with pytest.raises(LLMUnavailable, match="LLM API 调用失败"):
                client.complete(system="x", user="y")

    def test_empty_content_raises(self, with_openai_env: None) -> None:
        with patch("openai.OpenAI") as MockOpenAI:
            instance = MagicMock()
            instance.chat.completions.create.return_value = _make_resp("")
            MockOpenAI.return_value = instance
            client = LLMClient()
            with pytest.raises(LLMUnavailable, match="返回内容为空"):
                client.complete(system="x", user="y")

    def test_none_content_raises(self, with_openai_env: None) -> None:
        with patch("openai.OpenAI") as MockOpenAI:
            instance = MagicMock()
            instance.chat.completions.create.return_value = _make_resp(None)
            MockOpenAI.return_value = instance
            client = LLMClient()
            with pytest.raises(LLMUnavailable, match="返回内容为空"):
                client.complete(system="x", user="y")

    def test_no_choices_raises(self, with_openai_env: None) -> None:
        with patch("openai.OpenAI") as MockOpenAI:
            instance = MagicMock()
            instance.chat.completions.create.return_value = SimpleNamespace(
                choices=[]
            )
            MockOpenAI.return_value = instance
            client = LLMClient()
            with pytest.raises(LLMUnavailable, match="choices 为空"):
                client.complete(system="x", user="y")

    def test_explicit_model_overrides_default(
        self, with_openai_env: None,
    ) -> None:
        """构造时传入 model 应覆盖环境默认."""
        with patch("openai.OpenAI") as MockOpenAI:
            instance = MagicMock()
            instance.chat.completions.create.return_value = _make_resp("ok")
            MockOpenAI.return_value = instance
            client = LLMClient(model="claude-3-5-sonnet-latest")
            client.complete(system="x", user="y")
            kwargs = instance.chat.completions.create.call_args.kwargs
            assert kwargs["model"] == "claude-3-5-sonnet-latest"

    def test_kss_llm_model_env_overrides_default(
        self, with_openai_env: None, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """KSS_LLM_MODEL env 应覆盖默认 model."""
        monkeypatch.setenv("KSS_LLM_MODEL", "gpt-4o")
        with patch("openai.OpenAI") as MockOpenAI:
            instance = MagicMock()
            instance.chat.completions.create.return_value = _make_resp("ok")
            MockOpenAI.return_value = instance
            client = LLMClient()
            client.complete(system="x", user="y")
            kwargs = instance.chat.completions.create.call_args.kwargs
            assert kwargs["model"] == "gpt-4o"
