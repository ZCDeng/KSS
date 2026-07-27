"""Non-secret provider routes and in-memory pi-ai credential inputs.

The route records are safe to persist or expose to the desktop UI. API keys
are deliberately modeled separately and their representation is redacted.
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_MODEL_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,255}$")
_THINKING_LEVELS = {"off", "minimal", "low", "medium", "high", "xhigh", "max"}


@dataclass(frozen=True)
class ProviderCredential:
    """One provider-scoped API-key credential kept only in memory."""

    provider_id: str
    api_key: str = field(repr=False)
    env: Mapping[str, str] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        _validate_identifier(self.provider_id, "provider_id")
        if not self.api_key.strip() and not self.env:
            raise ValueError("credential requires api_key or provider env")
        if any(not isinstance(key, str) or not isinstance(value, str) for key, value in self.env.items()):
            raise TypeError("credential env must be string-to-string")

    def as_helper_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"type": "api_key"}
        if self.api_key:
            result["key"] = self.api_key
        if self.env:
            result["env"] = dict(self.env)
        return result


@dataclass(frozen=True)
class ProviderRoute:
    """A non-secret model route understood by the pi-ai helper."""

    provider_id: str
    model_id: str
    base_url: str | None = None
    thinking_level: str = "off"
    context_window: int = 32_000
    max_output_tokens: int = 8_000
    supports_images: bool = False
    supports_tools: bool = True
    supports_thinking: bool = False

    def __post_init__(self) -> None:
        _validate_identifier(self.provider_id, "provider_id")
        if not _MODEL_IDENTIFIER.fullmatch(self.model_id):
            raise ValueError("invalid model_id")
        if self.thinking_level not in _THINKING_LEVELS:
            raise ValueError(f"unsupported thinking level: {self.thinking_level}")
        if self.context_window <= 0 or self.max_output_tokens <= 0:
            raise ValueError("token limits must be positive")
        _validate_base_url(self.base_url)

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider_id": self.provider_id,
            "model_id": self.model_id,
            "base_url": self.base_url,
            "thinking_level": self.thinking_level,
            "context_window": self.context_window,
            "max_output_tokens": self.max_output_tokens,
            "supports_images": self.supports_images,
            "supports_tools": self.supports_tools,
            "supports_thinking": self.supports_thinking,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ProviderRoute":
        return cls(
            provider_id=str(value["provider_id"]),
            model_id=str(value["model_id"]),
            base_url=_optional_string(value.get("base_url")),
            thinking_level=str(value.get("thinking_level") or "off"),
            context_window=_positive_int(value.get("context_window"), 32_000),
            max_output_tokens=_positive_int(value.get("max_output_tokens"), 8_000),
            supports_images=bool(value.get("supports_images", False)),
            supports_tools=bool(value.get("supports_tools", True)),
            supports_thinking=bool(value.get("supports_thinking", False)),
        )


@dataclass(frozen=True)
class ProviderRouteSet:
    """Ordered routes; fallback is safe only before any model output."""

    primary: ProviderRoute
    fallback: ProviderRoute | None = None

    def ordered(self) -> tuple[ProviderRoute, ...]:
        if self.fallback is None:
            return (self.primary,)
        return (self.primary, self.fallback)

    def as_dict(self) -> dict[str, Any]:
        return {
            "primary": self.primary.as_dict(),
            "fallback": self.fallback.as_dict() if self.fallback else None,
        }


@dataclass(frozen=True)
class ProviderModel:
    """Sanitized model catalog record returned by the helper."""

    provider_id: str
    model_id: str
    name: str
    api: str
    context_window: int
    max_output_tokens: int
    supports_images: bool
    supports_tools: bool
    supports_thinking: bool

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ProviderModel":
        return cls(
            provider_id=str(value["provider_id"]),
            model_id=str(value["model_id"]),
            name=str(value.get("name") or value["model_id"]),
            api=str(value.get("api") or "unknown"),
            context_window=_positive_int(value.get("context_window"), 32_000),
            max_output_tokens=_positive_int(value.get("max_output_tokens"), 8_000),
            supports_images=bool(value.get("supports_images", False)),
            supports_tools=bool(value.get("supports_tools", True)),
            supports_thinking=bool(value.get("supports_thinking", False)),
        )


class ProviderRouteStore:
    """Atomic non-secret route persistence under the writable state root."""

    def __init__(self, state_root: str | Path) -> None:
        self.path = (
            Path(state_root)
            / "storage"
            / "agent"
            / "provider_routes.json"
        )

    def load(self) -> ProviderRouteSet:
        if self.path.exists():
            try:
                value = json.loads(self.path.read_text(encoding="utf-8"))
                if not isinstance(value, Mapping):
                    raise ValueError("provider route root must be an object")
                primary = value.get("primary")
                fallback = value.get("fallback")
                if not isinstance(primary, Mapping):
                    raise ValueError("provider primary route is missing")
                return ProviderRouteSet(
                    primary=ProviderRoute.from_dict(primary),
                    fallback=(
                        ProviderRoute.from_dict(fallback)
                        if isinstance(fallback, Mapping)
                        else None
                    ),
                )
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                # A corrupt non-secret config must not expose credentials or
                # prevent the legacy compatibility route from starting.
                pass
        return routes_from_legacy_environment()

    def save(self, routes: ProviderRouteSet) -> ProviderRouteSet:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        encoded = json.dumps(
            routes.as_dict(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        fd, temporary = tempfile.mkstemp(
            prefix=".provider-routes-",
            dir=str(self.path.parent),
        )
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
            directory_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        finally:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
        return routes


def routes_from_legacy_environment() -> ProviderRouteSet:
    """Build only non-secret route metadata from the legacy environment."""

    primary_key_present = _env_present(
        "KSS_LLM_PRIMARY_KEY",
        "KSS_LLM_PRIMARY_CREDENTIAL_PRESENT",
    )
    fallback_key_present = _env_present(
        "KSS_LLM_FALLBACK_KEY",
        "KSS_LLM_FALLBACK_CREDENTIAL_PRESENT",
    )
    if primary_key_present or fallback_key_present:
        primary_slot = "primary" if primary_key_present else "fallback"
        primary = _route_from_slot(primary_slot, default_model="gpt-4o-mini")
        fallback = (
            _route_from_slot("fallback", default_model="gpt-4o-mini")
            if primary_key_present and fallback_key_present
            else None
        )
        return ProviderRouteSet(primary, fallback)
    if _env_present("DEEPSEEK_API_KEY", "DEEPSEEK_API_KEY_PRESENT"):
        return ProviderRouteSet(
            ProviderRoute(
                provider_id="deepseek",
                model_id="deepseek-chat",
                base_url="https://api.deepseek.com/v1",
            ),
            ProviderRoute(
                provider_id="openai",
                model_id=os.getenv("KSS_LLM_MODEL", "").strip()
                or "gpt-4o-mini",
                base_url=_optional_string(os.getenv("OPENAI_BASE_URL")),
            )
            if _env_present("OPENAI_API_KEY", "OPENAI_API_KEY_PRESENT")
            else None,
        )
    return ProviderRouteSet(
        ProviderRoute(
            provider_id="openai",
            model_id=os.getenv("KSS_LLM_MODEL", "").strip()
            or "gpt-4o-mini",
            base_url=_optional_string(os.getenv("OPENAI_BASE_URL")),
        )
    )


def legacy_routes_from_environment() -> tuple[ProviderRouteSet, dict[str, ProviderCredential]]:
    """Translate existing flat Keychain environment keys without persisting them."""

    primary_key = os.getenv("KSS_LLM_PRIMARY_KEY", "").strip()
    fallback_key = os.getenv("KSS_LLM_FALLBACK_KEY", "").strip()
    primary_present = _env_present("KSS_LLM_PRIMARY_KEY", "KSS_LLM_PRIMARY_CREDENTIAL_PRESENT")
    fallback_present = _env_present("KSS_LLM_FALLBACK_KEY", "KSS_LLM_FALLBACK_CREDENTIAL_PRESENT")
    if primary_present or fallback_present:
        primary_slot = "primary" if primary_present else "fallback"
        primary = _route_from_slot(primary_slot, default_model="gpt-4o-mini")
        secondary = (
            _route_from_slot("fallback", default_model="gpt-4o-mini")
            if primary_present and fallback_present
            else None
        )
        routes = ProviderRouteSet(
            primary=primary,
            fallback=secondary,
        )
        credentials: dict[str, ProviderCredential] = {}
        if primary_key:
            credentials[primary.provider_id] = ProviderCredential(
                provider_id=primary.provider_id,
                api_key=primary_key,
            )
        if fallback_key:
            fallback_route = routes.fallback or routes.primary
            credentials[fallback_route.provider_id] = ProviderCredential(
                provider_id=fallback_route.provider_id,
                api_key=fallback_key,
            )
        return routes, credentials

    deepseek_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
    openai_key = os.getenv("OPENAI_API_KEY", "").strip()
    openai_base = _optional_string(os.getenv("OPENAI_BASE_URL"))
    deepseek_present = _env_present("DEEPSEEK_API_KEY", "DEEPSEEK_API_KEY_PRESENT")
    openai_present = _env_present("OPENAI_API_KEY", "OPENAI_API_KEY_PRESENT")
    if deepseek_present:
        primary = ProviderRoute(
            provider_id="deepseek",
            model_id="deepseek-chat",
            base_url="https://api.deepseek.com/v1",
        )
        fallback = (
            ProviderRoute(
                provider_id="openai",
                model_id=os.getenv("KSS_LLM_MODEL", "").strip() or "gpt-4o-mini",
                base_url=openai_base,
            )
            if openai_present
            else None
        )
        credentials = {}
        if deepseek_key:
            credentials["deepseek"] = ProviderCredential("deepseek", deepseek_key)
        if fallback is not None and openai_key:
            credentials["openai"] = ProviderCredential("openai", openai_key)
        return ProviderRouteSet(primary, fallback), credentials
    if openai_present:
        route = ProviderRoute(
            provider_id="openai",
            model_id=os.getenv("KSS_LLM_MODEL", "").strip() or "gpt-4o-mini",
            base_url=openai_base,
        )
        credentials = {
            "openai": ProviderCredential("openai", openai_key),
        } if openai_key else {}
        return ProviderRouteSet(route), credentials
    raise ValueError("no configured LLM provider routes")


def _env_present(secret_key: str, presence_key: str) -> bool:
    return bool(
        os.getenv(secret_key, "").strip()
        or os.getenv(presence_key, "").strip() in {"1", "true", "yes", "configured"}
    )


def _route_from_slot(slot: str, *, default_model: str) -> ProviderRoute:
    prefix = f"KSS_LLM_{slot.upper()}"
    return ProviderRoute(
        provider_id=f"kss-{slot}",
        model_id=os.getenv(f"{prefix}_MODEL", "").strip() or default_model,
        base_url=_optional_string(os.getenv(f"{prefix}_BASE_URL")),
    )


def _validate_identifier(value: str, label: str) -> None:
    if not _IDENTIFIER.fullmatch(value):
        raise ValueError(f"invalid {label}")


def _validate_base_url(value: str | None) -> None:
    if value is None:
        return
    parsed = urlparse(value)
    host = (parsed.hostname or "").lower()
    if parsed.scheme == "https" and host:
        return
    if parsed.scheme == "http" and host in {"localhost", "127.0.0.1", "::1"}:
        return
    raise ValueError("base_url must use https (localhost http is allowed)")


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _optional_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


__all__ = [
    "ProviderCredential",
    "ProviderModel",
    "ProviderRoute",
    "ProviderRouteSet",
    "legacy_routes_from_environment",
]
