"""统一凭据脱敏（评审 S3）——分时数据层的**唯一** canonical 脱敏真源.

为什么单独成模块：U2 的「响应体扫描 / 请求序列化」与 U6 的「plist 渲染守卫」若各自
定义凭据正则，必然漂移留缝（一处放过另一处没放过）。本模块把 key-name pattern 与
token-value pattern 定为单一常量，两边共用、用同 fixture 双路径测试拒绝。

零依赖叶子模块（只 import 标准库），可被数据层 / 脚本 / 渲染器安全 import。
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

# 凭据「键名」pattern：请求参数 / header / 字段名命中即脱敏其值（大小写不敏感）。
# 覆盖 plan 列举的 token|key|secret|auth|credential，外加 password 常见变体。
CREDENTIAL_KEY_RE = re.compile(
    r"(?i)(token|api[_-]?key|secret|auth|credential|password|passwd)"
)

# 凭据「值」pattern：长十六进制串（Tushare token = 40 hex）。保守锚定 token 形态，
# 避免误伤价格 JSON 里的普通数字 / 短哈希。响应体扫描的主用 pattern。
CREDENTIAL_VALUE_RE = re.compile(r"\b[0-9a-fA-F]{32,}\b")

REDACTED = "***REDACTED***"


def redact_text(text: str | None, *, known_secrets: tuple[str, ...] = ()) -> str | None:
    """脱去文本里的 token 形态串 + 任意已知明文 secret（error_summary 等共用）。"""
    if text is None:
        return None
    out = CREDENTIAL_VALUE_RE.sub(REDACTED, text)
    for s in known_secrets:
        if s:
            out = out.replace(s, REDACTED)
    return out


def contains_credential(
    text: str | None, *, known_secrets: tuple[str, ...] = ()
) -> bool:
    """blob 落盘前扫描：响应体是否含 token 形态串 或 已知 secret 明文。

    命中 → 调用方应终止 run（``credential_in_payload``），不持久化该 blob。
    """
    if not text:
        return False
    if CREDENTIAL_VALUE_RE.search(text):
        return True
    return any(s and s in text for s in known_secrets)


def _redact_value_by_key(key: str, value: Any) -> Any:
    """键名命中凭据 pattern → 值整体替 REDACTED；否则原样。"""
    if isinstance(key, str) and CREDENTIAL_KEY_RE.search(key):
        return REDACTED
    return value


def redact_mapping(
    mapping: dict[str, Any], *, known_secrets: tuple[str, ...] = ()
) -> dict[str, Any]:
    """按键名脱敏 dict 值（递归）：键名命中 → 值 REDACTED；字符串值再过文本脱敏。"""
    out: dict[str, Any] = {}
    for k, v in mapping.items():
        if isinstance(k, str) and CREDENTIAL_KEY_RE.search(k):
            out[k] = REDACTED
            continue
        if isinstance(v, dict):
            out[k] = redact_mapping(v, known_secrets=known_secrets)
        elif isinstance(v, str):
            out[k] = redact_text(v, known_secrets=known_secrets)
        else:
            out[k] = v
    return out


def _redact_url(url: str, *, known_secrets: tuple[str, ...] = ()) -> str:
    """脱敏 URL query 里键名命中凭据的参数值。"""
    parts = urlsplit(url)
    if not parts.query:
        return redact_text(url, known_secrets=known_secrets) or url
    pairs = parse_qsl(parts.query, keep_blank_values=True)
    redacted_pairs = [(k, _redact_value_by_key(k, v)) for k, v in pairs]
    new_query = urlencode(redacted_pairs)
    rebuilt = urlunsplit(
        (parts.scheme, parts.netloc, parts.path, new_query, parts.fragment)
    )
    return redact_text(rebuilt, known_secrets=known_secrets) or rebuilt


def redact_request(
    method: str,
    url: str,
    *,
    headers: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
    body: Any = None,
    known_secrets: tuple[str, ...] = (),
) -> dict[str, Any]:
    """请求序列化的**唯一**脱敏写入点 → 返回可安全持久化的 dict.

    供 :data:`payload_observations.redacted_request_json` 唯一来源使用：URL query /
    headers / params 里凭据键的值脱敏，body 文本里 token 形态串脱敏。
    """
    redacted: dict[str, Any] = {
        "method": method,
        "url": _redact_url(url, known_secrets=known_secrets),
    }
    if headers is not None:
        redacted["headers"] = redact_mapping(headers, known_secrets=known_secrets)
    if params is not None:
        redacted["params"] = redact_mapping(params, known_secrets=known_secrets)
    if body is not None:
        if isinstance(body, dict):
            redacted["body"] = redact_mapping(body, known_secrets=known_secrets)
        else:
            redacted["body"] = redact_text(str(body), known_secrets=known_secrets)
    return redacted


__all__ = [
    "CREDENTIAL_KEY_RE",
    "CREDENTIAL_VALUE_RE",
    "REDACTED",
    "contains_credential",
    "redact_mapping",
    "redact_request",
    "redact_text",
]
