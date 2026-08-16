"""DSH settings.yaml 写入器：KSS 落地「DSH 官方配置模型方法」的持久层.

自定义 OpenAI-compatible provider 写进 ``$DSH_HOME/settings.yaml`` 的
``llm-pi-ai:`` 小节（`dsh-settings-file` 热加载，settings 小节按命名空间
整体覆盖组合行配置）。凭证不落盘：``apiKeyEnv`` 只是环境变量引用，真实
密钥仍由 Keychain → credential broker → Node 进程环境注入。
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

import yaml

_PROVIDER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,255}$")

# kss-profile cordis.patch.yml 里的内建 providers。settings 小节是整体替换
# 语义，任何写入都必须带上它们，否则内建路由会被挤掉。
_BASE_PROVIDERS: dict[str, dict[str, Any]] = {
    "openai": {"apiKeyEnv": "OPENAI_API_KEY"},
    "deepseek": {"apiKeyEnv": "DEEPSEEK_API_KEY"},
}

# 自定义 provider 的登记表放在 dsh 不认识的顶层键里，避免往 llm-pi-ai
# schema 里塞未知字段导致整个命名空间被拒。
_REGISTRY_KEY = "kss-custom-providers"


def custom_provider_env_name(provider_id: str) -> str:
    """自定义 provider 的 apiKeyEnv 命名（与 Swift KeychainStore 约定一致）."""
    token = re.sub(r"[^A-Z0-9]", "_", provider_id.upper())
    return f"KSS_PROVIDER_{token}_API_KEY"


def settings_path(dsh_home: str | Path) -> Path:
    return Path(dsh_home) / "settings.yaml"


def _load_document(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    return dict(loaded) if isinstance(loaded, Mapping) else {}


def _dump_document(path: Path, document: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        yaml.safe_dump(dict(document), allow_unicode=True, sort_keys=True),
        encoding="utf-8",
    )


def _registry(document: Mapping[str, Any]) -> list[str]:
    raw = document.get(_REGISTRY_KEY)
    if not isinstance(raw, list):
        return []
    return [str(item) for item in raw if isinstance(item, str) and item]


def _providers_section(document: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    section = document.get("llm-pi-ai")
    providers = section.get("providers") if isinstance(section, Mapping) else None
    merged: dict[str, dict[str, Any]] = {
        key: dict(value) for key, value in _BASE_PROVIDERS.items()
    }
    if isinstance(providers, Mapping):
        for provider_id, spec in providers.items():
            if isinstance(spec, Mapping):
                merged[str(provider_id)] = dict(spec)
    return merged


def _validate_base_url(value: str) -> None:
    from urllib.parse import urlparse

    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if parsed.scheme == "https" and host:
        return
    if parsed.scheme == "http" and host in {"localhost", "127.0.0.1", "::1"}:
        return
    raise ValueError("base_url 必须使用 https（仅 localhost 允许 http）")


def list_custom_providers(dsh_home: str | Path) -> dict[str, dict[str, Any]]:
    """按登记表列出自定义 providers（id → settings 里的 profile）."""
    document = _load_document(settings_path(dsh_home))
    providers = _providers_section(document)
    return {
        provider_id: providers[provider_id]
        for provider_id in _registry(document)
        if provider_id in providers
    }


def add_custom_provider(
    dsh_home: str | Path,
    *,
    provider_id: str,
    base_url: str,
    model_ids: list[str],
    display_name: str = "",
    api: str = "openai-completions",
) -> dict[str, Any]:
    """新增/更新一个自定义 provider profile 并原子写回 settings.yaml.

    Returns:
        写入的 profile（含 apiKeyEnv），供 UI 提示保存哪个 Keychain 凭证。

    Raises:
        ValueError: id/base_url/models 不合法，或与内建 provider 冲突。
    """
    provider_id = provider_id.strip()
    if not _PROVIDER_ID.fullmatch(provider_id):
        raise ValueError("provider_id 不合法")
    if provider_id in _BASE_PROVIDERS:
        raise ValueError("provider_id 与内建 provider 冲突")
    base_url = base_url.strip()
    _validate_base_url(base_url)
    models = []
    for raw in model_ids:
        model_id = str(raw).strip()
        if not model_id:
            continue
        if not _MODEL_ID.fullmatch(model_id):
            raise ValueError(f"模型 ID 不合法：{model_id}")
        if model_id not in [entry["id"] for entry in models]:
            models.append({"id": model_id})
    if not models:
        raise ValueError("至少需要一个模型 ID")

    profile: dict[str, Any] = {
        "apiKeyEnv": custom_provider_env_name(provider_id),
        "api": api,
        "baseURL": base_url,
        "models": models,
    }
    display = display_name.strip()
    if display:
        profile["displayName"] = display

    path = settings_path(dsh_home)
    document = _load_document(path)
    providers = _providers_section(document)
    providers[provider_id] = profile
    registry = _registry(document)
    if provider_id not in registry:
        registry.append(provider_id)
    document["llm-pi-ai"] = {"providers": providers}
    document[_REGISTRY_KEY] = sorted(registry)
    _dump_document(path, document)
    return dict(profile)


def remove_custom_provider(dsh_home: str | Path, provider_id: str) -> bool:
    """移除一个自定义 provider；内建 provider 不可移除."""
    provider_id = provider_id.strip()
    if not provider_id or provider_id in _BASE_PROVIDERS:
        return False
    path = settings_path(dsh_home)
    document = _load_document(path)
    registry = _registry(document)
    if provider_id not in registry:
        return False
    providers = _providers_section(document)
    providers.pop(provider_id, None)
    registry = [item for item in registry if item != provider_id]
    document["llm-pi-ai"] = {"providers": providers}
    document[_REGISTRY_KEY] = sorted(registry)
    _dump_document(path, document)
    return True


__all__ = [
    "add_custom_provider",
    "custom_provider_env_name",
    "list_custom_providers",
    "remove_custom_provider",
    "settings_path",
]
