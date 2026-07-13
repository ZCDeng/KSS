"""kss/llm/openai_client.py 单元测试."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from kss.llm.openai_client import (
    LLMClient,
    LLMUnavailable,
    _resolve_credential_candidates,
    _resolve_credentials,
    probe_credential_candidate,
)


def _clear_new_byok_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "KSS_LLM_PRIMARY_KEY", "KSS_LLM_PRIMARY_BASE_URL", "KSS_LLM_PRIMARY_MODEL",
        "KSS_LLM_FALLBACK_KEY", "KSS_LLM_FALLBACK_BASE_URL", "KSS_LLM_FALLBACK_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)


# ====================================================================== #
# 凭据解析
# ====================================================================== #


class TestResolveCredentials:
    """_resolve_credentials —— 环境变量 → (key, base_url, default_model)."""

    def test_deepseek_primary_even_when_openai_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """DEEPSEEK 在 → 主路径，即便 OPENAI 也在."""
        monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-test")
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        key, base, model = _resolve_credentials()
        assert key == "ds-test"
        assert base == "https://api.deepseek.com/v1"
        assert model == "deepseek-v4-flash"

    def test_falls_back_to_openai(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """DEEPSEEK 缺失 + OPENAI 在 → fallback OpenAI."""
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        key, base, model = _resolve_credentials()
        assert key == "sk-test"
        assert base is None
        assert model == "gpt-4o-mini"

    def test_openai_fallback_with_custom_base_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """oneAPI 网关之类的自建 base_url 应保留."""
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.setenv("OPENAI_BASE_URL", "https://oneapi.local/v1")
        key, base, _ = _resolve_credentials()
        assert key == "sk-test"
        assert base == "https://oneapi.local/v1"

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

    def test_deepseek_base_prefers_deepseek_key(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """双 key + OPENAI_BASE_URL=deepseek 时仍用 DEEPSEEK_API_KEY."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai-dead")
        monkeypatch.setenv("OPENAI_BASE_URL", "https://api.deepseek.com")
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-deepseek-live")
        key, base, model = _resolve_credentials()
        assert key == "sk-deepseek-live"
        assert base == "https://api.deepseek.com/v1"
        assert model == "deepseek-v4-flash"

    def test_deepseek_base_normalizes_v1_openai_key_only(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """仅 OPENAI key 但 base 指 deepseek：走 deepseek 网关 + 该 key."""
        monkeypatch.setenv("OPENAI_API_KEY", "sk-only")
        monkeypatch.setenv("OPENAI_BASE_URL", "https://api.deepseek.com")
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        key, base, model = _resolve_credentials()
        assert key == "sk-only"
        assert base == "https://api.deepseek.com/v1"
        assert model == "deepseek-v4-flash"


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

    def test_kss_llm_timeout_env_overrides_default(
        self, with_openai_env: None, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """KSS_LLM_TIMEOUT env 应被传给 OpenAI SDK 的 timeout."""
        monkeypatch.setenv("KSS_LLM_TIMEOUT", "120")
        with patch("openai.OpenAI") as MockOpenAI:
            instance = MagicMock()
            instance.chat.completions.create.return_value = _make_resp("ok")
            MockOpenAI.return_value = instance
            LLMClient()
            kwargs = MockOpenAI.call_args.kwargs
            assert kwargs["timeout"] == 120.0

    def test_constructor_timeout_overrides_env(
        self, with_openai_env: None, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """构造时显式传 timeout 优先于 env."""
        monkeypatch.setenv("KSS_LLM_TIMEOUT", "120")
        with patch("openai.OpenAI") as MockOpenAI:
            instance = MagicMock()
            MockOpenAI.return_value = instance
            LLMClient(timeout=45.0)
            kwargs = MockOpenAI.call_args.kwargs
            assert kwargs["timeout"] == 45.0

    def test_kss_llm_timeout_invalid_falls_back_to_default(
        self, with_openai_env: None, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """env 是垃圾字符串 → 用默认值，不抛."""
        monkeypatch.setenv("KSS_LLM_TIMEOUT", "abc")
        with patch("openai.OpenAI") as MockOpenAI:
            instance = MagicMock()
            MockOpenAI.return_value = instance
            LLMClient()
            kwargs = MockOpenAI.call_args.kwargs
            assert kwargs["timeout"] == 90.0  # _DEFAULT_TIMEOUT_SEC


# ====================================================================== #
# U3: BYOK 端点泛化 —— 有序候选解析 + https 校验 + 主备运行期降级
# ====================================================================== #


class TestResolveCredentialCandidates:
    """_resolve_credential_candidates —— 新六键优先，全缺时兼容映射旧键."""

    def test_new_keys_take_priority_over_legacy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """新键在场 → 优先新键，即便旧键也配置了."""
        _clear_new_byok_keys(monkeypatch)
        monkeypatch.setenv("KSS_LLM_PRIMARY_KEY", "primary-key")
        monkeypatch.setenv("KSS_LLM_PRIMARY_BASE_URL", "https://gateway-a.example/v1")
        monkeypatch.setenv("KSS_LLM_PRIMARY_MODEL", "model-a")
        monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-should-be-ignored")
        candidates = _resolve_credential_candidates()
        assert candidates[0] == ("primary-key", "https://gateway-a.example/v1", "model-a")

    def test_new_keys_primary_and_fallback_both_present(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _clear_new_byok_keys(monkeypatch)
        monkeypatch.setenv("KSS_LLM_PRIMARY_KEY", "primary-key")
        monkeypatch.setenv("KSS_LLM_FALLBACK_KEY", "fallback-key")
        monkeypatch.setenv("KSS_LLM_FALLBACK_BASE_URL", "https://gateway-b.example/v1")
        candidates = _resolve_credential_candidates()
        assert len(candidates) == 2
        assert candidates[1][0] == "fallback-key"
        assert candidates[1][1] == "https://gateway-b.example/v1"

    def test_legacy_compat_when_new_keys_all_absent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """旧配置零操作可用：仅旧键在场时行为与 U3 之前逐字段一致."""
        _clear_new_byok_keys(monkeypatch)
        monkeypatch.setenv("DEEPSEEK_API_KEY", "ds-test")
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        candidates = _resolve_credential_candidates()
        assert candidates[0] == ("ds-test", "https://api.deepseek.com/v1", "deepseek-v4-flash")

    def test_rejects_insecure_http_base_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """http 非 localhost 的 base_url 被拒并给出原因."""
        _clear_new_byok_keys(monkeypatch)
        monkeypatch.setenv("KSS_LLM_PRIMARY_KEY", "primary-key")
        monkeypatch.setenv("KSS_LLM_PRIMARY_BASE_URL", "http://insecure.example/v1")
        with pytest.raises(LLMUnavailable, match="https"):
            _resolve_credential_candidates()

    def test_allows_http_localhost(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """本地推理端点 http://localhost 例外放行."""
        _clear_new_byok_keys(monkeypatch)
        monkeypatch.setenv("KSS_LLM_PRIMARY_KEY", "primary-key")
        monkeypatch.setenv("KSS_LLM_PRIMARY_BASE_URL", "http://localhost:11434/v1")
        candidates = _resolve_credential_candidates()
        assert candidates[0][1] == "http://localhost:11434/v1"

    def test_no_credentials_at_all_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _clear_new_byok_keys(monkeypatch)
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        with pytest.raises(LLMUnavailable, match="未配置"):
            _resolve_credential_candidates()


class TestLLMClientFailover:
    """LLMClient.complete —— 主候选失败时降级备用候选（U3 新增行为）."""

    def test_primary_failure_falls_back_to_secondary(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _clear_new_byok_keys(monkeypatch)
        monkeypatch.setenv("KSS_LLM_PRIMARY_KEY", "primary-key")
        monkeypatch.setenv("KSS_LLM_FALLBACK_KEY", "fallback-key")
        monkeypatch.delenv("KSS_LLM_MODEL", raising=False)

        primary_instance = MagicMock()
        primary_instance.chat.completions.create.side_effect = RuntimeError("401 unauthorized")
        fallback_instance = MagicMock()
        fallback_instance.chat.completions.create.return_value = _make_resp("来自备用候选的回答")

        with patch("openai.OpenAI", side_effect=[primary_instance, fallback_instance]):
            client = LLMClient()
            out = client.complete(system="x", user="y")
            assert out == "来自备用候选的回答"
        fallback_instance.chat.completions.create.assert_called_once()

    def test_both_candidates_fail_raises_with_both_errors(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _clear_new_byok_keys(monkeypatch)
        monkeypatch.setenv("KSS_LLM_PRIMARY_KEY", "primary-key")
        monkeypatch.setenv("KSS_LLM_FALLBACK_KEY", "fallback-key")

        primary_instance = MagicMock()
        primary_instance.chat.completions.create.side_effect = RuntimeError("primary down")
        fallback_instance = MagicMock()
        fallback_instance.chat.completions.create.side_effect = RuntimeError("fallback down")

        with patch("openai.OpenAI", side_effect=[primary_instance, fallback_instance]):
            client = LLMClient()
            with pytest.raises(LLMUnavailable, match="主备均不可用"):
                client.complete(system="x", user="y")

    def test_single_candidate_failure_does_not_attempt_fallback_build(
        self, with_openai_env: None,
    ) -> None:
        """只有一个候选时失败直接抛出，不构造第二个 SDK client（旧行为逐字段保留）."""
        with patch("openai.OpenAI") as MockOpenAI:
            instance = MagicMock()
            instance.chat.completions.create.side_effect = RuntimeError("429 rate limit")
            MockOpenAI.return_value = instance
            client = LLMClient()
            with pytest.raises(LLMUnavailable, match="LLM API 调用失败"):
                client.complete(system="x", user="y")
        assert MockOpenAI.call_count == 1


class TestCredentialCandidateProbe:
    """probe_credential_candidate —— U4 datasource-test 复用的单候选连通性探测."""

    def test_success_reports_ok_with_latency(self) -> None:
        with patch("openai.OpenAI") as MockOpenAI:
            instance = MagicMock()
            instance.chat.completions.create.return_value = _make_resp("pong")
            MockOpenAI.return_value = instance
            result = probe_credential_candidate(("key", "https://gateway.example/v1", "model-x"))
            assert result["ok"] is True
            assert result["error"] is None
            assert result["latency_ms"] is not None

    def test_failure_reports_error_and_hint(self) -> None:
        with patch("openai.OpenAI") as MockOpenAI:
            instance = MagicMock()
            instance.chat.completions.create.side_effect = RuntimeError("401 unauthorized")
            MockOpenAI.return_value = instance
            result = probe_credential_candidate(("bad-key", None, "model-x"))
            assert result["ok"] is False
            assert result["error"] == "RuntimeError"
            assert "401" in result["hint"]
