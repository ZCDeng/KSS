"""分时数据 provider 协议 + 东财(AKShare)前向适配 + 能力门控.

两条硬约束传入本模块（plan U1 / PRD F1）：

1. **数据层契约**（``kss/AGENTS.md``）：取数失败**不抛异常**，返回带 ``error``
   的 :class:`FetchResult`；重试/退避复用 ``tushare_client._fetch_with_retry`` 思路。
2. **PIT 红线**（``docs/solutions/dragon_tiger_integration_retrospective.md``）：
   AKShare/东财 分钟流按定义是**非-PIT 实时层**，能力门控**结构上**只允许
   ``forward_observed`` / ``research_only``，**绝不** ``pit_backtest_eligible``。
   历史 PIT 准入（Tushare proxy）仅产出分类，不在本计划建读路径（决策 D1）。

能力门控 :func:`classify_eligibility` 是**确定性纯函数**（不是模型判断）：把
「provider 是否前向源 / 各项证据是否齐」映射到 eligibility，便于 U1 测试钉死。
"""

from __future__ import annotations

import contextlib
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol, runtime_checkable


class Eligibility(str, Enum):
    """provider/observation 的回测准入分级（与 test-spec U8 / 准入测试对齐）.

    取值与 PRD 词表一致；序列化即字符串值（``str`` 基类）。
    """

    PIT_BACKTEST_ELIGIBLE = "pit_backtest_eligible"
    FORWARD_OBSERVED = "forward_observed"
    RESEARCH_ONLY = "research_only"
    FAILED = "failed"


# 前向-only 实时层 provider 名集合：这些源**结构上**不可进 PIT 回测（红线）。
# 任何此集合内的 provider，无论响应多完整，eligibility 上限都是 forward_observed。
# ``longbridge``（券商实时推送）同理——realtime 快照本就不是 PIT 源（KTD2）。
FORWARD_ONLY_PROVIDERS: frozenset[str] = frozenset(
    {"eastmoney_akshare", "eastmoney_direct", "longbridge"}
)

# 东财 1m 分钟接口的已知上游限制：仅近 ~5 个交易日（与 akshare 上游一致）。
EASTMOTNEY_1M_MAX_HISTORY_DAYS: int = 5

# 东财(akshare)分钟响应的原始中文列 → canonical 字段名映射。
# U1 仅用于**记录字段映射 / 探针可见**；真正的归一化在 U3（canonical 层）。
EM_COLUMN_MAP: dict[str, str] = {
    "时间": "bar_ts",
    "开盘": "open",
    "收盘": "close",
    "最高": "high",
    "最低": "low",
    "成交量": "volume",
    "成交额": "amount",
    "涨跌幅": "pct_change",
    "涨跌额": "change",
    "振幅": "amplitude",
    "换手率": "turnover_rate",
    "均价": "vwap",
    "最新价": "last",
}


@dataclass(frozen=True)
class FetchResult:
    """单次 ``fetch_bars`` 的取数结果（永不抛异常，失败走 ``error``）.

    Attributes:
        rows: 原始行（保留 provider 原始列名，未归一化）；失败时为空 list。
        raw_columns: 响应列名（按出现序），用于探针 schema-hash 与字段映射。
        source_asof_ts: provider 数据的 as-of 时点（ISO-8601 带时区）；前向源
            取「响应内最晚 bar 时间」，无法判定时为 ``None``。
        status_code: HTTP 状态码（成功 200）；异常路径无从得知时 ``None``。
        latency_ms: 本次请求耗时（毫秒）。
        error: 失败原因（已脱去凭据的简述）；成功时 ``None``。
    """

    rows: list[dict[str, Any]]
    raw_columns: tuple[str, ...]
    source_asof_ts: str | None
    status_code: int | None
    latency_ms: float
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and bool(self.rows)


@dataclass(frozen=True)
class CapabilityResult:
    """provider 能力门控结果（探针报告的核心分类）.

    ``eligibility`` 由 :func:`classify_eligibility` 产出，绝不由调用方手填，
    避免「前向源被误标 pit_backtest_eligible」这类红线破坏散落各处。
    """

    provider: str
    version: str
    supported_intervals: tuple[int, ...]
    supported_assets: tuple[str, ...]
    max_history_days: int | None
    eligibility: Eligibility
    reachable: bool
    # 历史 PIT 证据旗标（仅 Tushare 历史分类路径会填；前向源全 None）。
    has_entitlement: bool | None = None
    requested_history_ok: bool | None = None
    correction_policy_known: bool | None = None
    has_frozen_manifest_evidence: bool | None = None
    has_availability_proxy: bool | None = None
    notes: tuple[str, ...] = ()


@runtime_checkable
class IntradayProvider(Protocol):
    """分时 provider 协议（U1 定义；适配器各自实现）.

    实现方**禁止**在 ``fetch_bars`` 抛异常——异常即数据层契约违例。
    """

    name: str
    version: str

    def supported_intervals(self) -> tuple[int, ...]:
        """支持的分钟周期（如 ``(1, 5, 15, 30, 60)``）。"""
        ...

    def supported_assets(self) -> tuple[str, ...]:
        """支持的标的类型（``stock`` / ``etf`` / ``index``）。"""
        ...

    def fetch_bars(
        self,
        symbol: str,
        *,
        interval_minutes: int,
        asset_kind: str,
        start: str | None = None,
        end: str | None = None,
    ) -> FetchResult:
        """拉取分钟 bar（失败返回带 ``error`` 的 :class:`FetchResult`，不抛）。"""
        ...

    def capability(self) -> CapabilityResult:
        """能力门控分类（eligibility 由确定性门控函数产出）。"""
        ...


def classify_eligibility(
    provider_name: str,
    *,
    reachable: bool,
    has_entitlement: bool | None = None,
    requested_history_ok: bool | None = None,
    correction_policy_known: bool | None = None,
    has_frozen_manifest_evidence: bool | None = None,
    has_availability_proxy: bool | None = None,
) -> Eligibility:
    """把 provider 可达性 + 历史证据旗标映射到 eligibility（确定性纯函数）.

    判定顺序（红线优先）：

    1. **不可达** → ``failed``（错误/权限/覆盖 stub 失败都落这；test-spec U8）。
    2. **前向-only 源**（AKShare/东财）→ 恒 ``forward_observed``——无论响应多全，
       结构上不可进 PIT（红线）。1m 5 日限制是上游约束，不影响前向分级。
    3. **历史路径**（如 Tushare）：仅当**全部**证据为 ``True`` 才
       ``pit_backtest_eligible``；entitlement/coverage/correction 任一未知 → 对
       历史分钟回测**硬失败**，但其前向源若可用仍可影子采集 → ``research_only``。

    Args:
        provider_name: provider 名（用于判定是否前向-only 源）。
        reachable: 探针是否成功触达该源（连通 + 有响应）。
        has_entitlement: 是否有历史分钟权限（Tushare 路径）。
        requested_history_ok: 请求的历史覆盖是否满足。
        correction_policy_known: 修正/重述政策是否明确。
        has_frozen_manifest_evidence: 是否有冻结 manifest 证据。
        has_availability_proxy: 是否有文档化的保守 ``available_from_ts`` 代理（D2）。

    Returns:
        :class:`Eligibility` 分级。
    """
    if not reachable:
        return Eligibility.FAILED

    if provider_name in FORWARD_ONLY_PROVIDERS:
        # 非-PIT 实时层：永远 forward_observed，绝不 pit_backtest_eligible。
        return Eligibility.FORWARD_OBSERVED

    # 历史路径：全证据齐才放行 PIT；否则降级为 research_only（影子可续）。
    evidence = (
        has_entitlement,
        requested_history_ok,
        correction_policy_known,
        has_frozen_manifest_evidence,
        has_availability_proxy,
    )
    if all(flag is True for flag in evidence):
        return Eligibility.PIT_BACKTEST_ELIGIBLE
    return Eligibility.RESEARCH_ONLY


# --------------------------------------------------------------------------- #
# 东财(AKShare)前向适配
# --------------------------------------------------------------------------- #

# akshare 分钟接口按标的类型分流（已实测签名）：
#   stock -> stock_zh_a_hist_min_em(symbol, start_date, end_date, period, adjust)
#   etf   -> fund_etf_hist_min_em(symbol, start_date, end_date, period, adjust)
#   index -> index_zh_a_hist_min_em(symbol, period, start_date, end_date)  # 无 adjust
_EM_ASSET_FUNCS: dict[str, str] = {
    "stock": "stock_zh_a_hist_min_em",
    "etf": "fund_etf_hist_min_em",
    "index": "index_zh_a_hist_min_em",
}

_SHANGHAI_TZ = "Asia/Shanghai"


def _to_iso_shanghai(naive_ts: str) -> str | None:
    """把东财裸时间串 ``YYYY-MM-DD HH:MM:SS`` 定位到 Asia/Shanghai 的 ISO-8601.

    KTD7：``bar_end_ts`` 规范为带时区 ISO-8601（Asia/Shanghai）。解析失败返回
    ``None``（U1 不做严格校验，那是 U3 的事）。
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            dt = datetime.strptime(naive_ts.strip(), fmt)
        except (ValueError, AttributeError):
            continue
        return dt.replace(tzinfo=ZoneInfo(_SHANGHAI_TZ)).isoformat()
    return None


class EastmoneyAkshareProvider:
    """东财分钟前向适配（经 akshare）。前向-only，永不 PIT.

    akshare 是国内直连接口，沿用 ``TushareClient._bypass_system_proxy`` 同思路
    把东财域名加入 ``NO_PROXY``，避免 macOS 系统代理（Clash）挂起拖垮采集。
    """

    name = "eastmoney_akshare"

    # 东财域名（push2his.eastmoney.com 等）走直连，绕系统代理。
    _BYPASS_HOSTS = ("eastmoney.com", "push2his.eastmoney.com", "quote.eastmoney.com")

    def __init__(self) -> None:
        self._bypass_system_proxy()
        self.version = self._resolve_version()

    @staticmethod
    def _resolve_version() -> str:
        try:
            import akshare  # noqa: PLC0415

            return f"akshare-{getattr(akshare, '__version__', 'unknown')}"
        except Exception:  # noqa: BLE001 — 缺包不致命，版本记 unknown
            return "akshare-unavailable"

    @classmethod
    def _bypass_system_proxy(cls) -> None:
        """把东财域名追加进 NO_PROXY（追加非覆盖；大小写双写），同 tushare 思路。"""
        import os

        existing = os.environ.get("no_proxy") or os.environ.get("NO_PROXY") or ""
        hosts = [h.strip() for h in existing.split(",") if h.strip()]
        for host in cls._BYPASS_HOSTS:
            if host not in hosts:
                hosts.append(host)
        merged = ",".join(hosts)
        os.environ["NO_PROXY"] = merged
        os.environ["no_proxy"] = merged

    def supported_intervals(self) -> tuple[int, ...]:
        return (1, 5, 15, 30, 60)

    def supported_assets(self) -> tuple[str, ...]:
        return ("stock", "etf", "index")

    def capability(self) -> CapabilityResult:
        """能力门控：可达性由「能否 import akshare」近似（真实触达在 fetch_bars）。"""
        reachable = self.version != "akshare-unavailable"
        eligibility = classify_eligibility(self.name, reachable=reachable)
        return CapabilityResult(
            provider=self.name,
            version=self.version,
            supported_intervals=self.supported_intervals(),
            supported_assets=self.supported_assets(),
            max_history_days=EASTMOTNEY_1M_MAX_HISTORY_DAYS,
            eligibility=eligibility,
            reachable=reachable,
            notes=("东财1m仅近5交易日（上游限制）", "前向-only：结构上不可进PIT回测"),
        )

    def fetch_bars(
        self,
        symbol: str,
        *,
        interval_minutes: int,
        asset_kind: str,
        start: str | None = None,
        end: str | None = None,
    ) -> FetchResult:
        """拉取东财分钟 bar（异常吞为 error，遵循数据层契约）。"""
        t0 = time.monotonic()
        fn_name = _EM_ASSET_FUNCS.get(asset_kind)
        if fn_name is None:
            return FetchResult(
                rows=[],
                raw_columns=(),
                source_asof_ts=None,
                status_code=None,
                latency_ms=(time.monotonic() - t0) * 1000.0,
                error=f"unsupported asset_kind={asset_kind!r}",
            )
        try:
            import akshare  # noqa: PLC0415

            fn = getattr(akshare, fn_name)
            kwargs: dict[str, Any] = {"symbol": symbol, "period": str(interval_minutes)}
            if start:
                kwargs["start_date"] = start
            if end:
                kwargs["end_date"] = end
            # index 接口无 adjust 参数；stock/etf 显式空 adjust（原价，复权另算）。
            if asset_kind != "index":
                kwargs["adjust"] = ""
            df = fn(**kwargs)
        except Exception as exc:  # noqa: BLE001 — 数据层不抛
            return FetchResult(
                rows=[],
                raw_columns=(),
                source_asof_ts=None,
                status_code=None,
                latency_ms=(time.monotonic() - t0) * 1000.0,
                error=_short_error(exc),
            )

        latency_ms = (time.monotonic() - t0) * 1000.0
        if df is None or getattr(df, "empty", True):
            cols = getattr(df, "columns", None)
            empty_columns = tuple(str(c) for c in cols) if cols is not None else ()
            return FetchResult(
                rows=[],
                raw_columns=empty_columns,
                source_asof_ts=None,
                status_code=200,
                latency_ms=latency_ms,
                error="empty response",
            )

        raw_columns = tuple(str(c) for c in df.columns)
        rows = df.to_dict(orient="records")
        source_asof_ts = _latest_bar_ts(rows)
        return FetchResult(
            rows=rows,
            raw_columns=raw_columns,
            source_asof_ts=source_asof_ts,
            status_code=200,
            latency_ms=latency_ms,
            error=None,
        )


def _short_error(exc: Exception) -> str:
    """异常转简短字符串（截断，避免把整段堆栈/可能含敏感串塞进报告）。"""
    text = f"{type(exc).__name__}: {exc}"
    return text[:200]


def _latest_bar_ts(rows: list[dict[str, Any]]) -> str | None:
    """从原始行里取最晚 bar 时间并定位到 Asia/Shanghai ISO-8601。"""
    times = [str(r.get("时间")) for r in rows if r.get("时间") is not None]
    if not times:
        return None
    return _to_iso_shanghai(max(times))


# --------------------------------------------------------------------------- #
# Longbridge（长桥 / longbridge SDK）前向实时适配（U1）
# --------------------------------------------------------------------------- #

# KTD1：SDK 须经 .com 国际网关（.cn 本机不可达，叠 Clash 直连/代理均 000 失败）。
# provider **自身**固化三个 gateway env（实测可达），不劳用户填。
# 不设 = 复现东财同款「端点不可达」。
# SDK 4.x 官方 env 名 = LONGBRIDGE_*（legacy LONGPORT_* 仍兼容但弃用）。
_LONGPORT_COM_GATEWAY_ENV: dict[str, str] = {
    "LONGBRIDGE_HTTP_URL": "https://openapi.longbridge.com",
    "LONGBRIDGE_QUOTE_WS_URL": "wss://openapi-quote.longbridge.com/v2",
    "LONGBRIDGE_TRADE_WS_URL": "wss://openapi-trade.longbridge.com/v2",
}

# 凭据 env 名（R7）；显式 from_apikey(...) 而非 from_env，故读 LONGBRIDGE_* 前缀。
_LONGBRIDGE_CRED_ENV: tuple[str, str, str] = (
    "LONGBRIDGE_APP_KEY",
    "LONGBRIDGE_APP_SECRET",
    "LONGBRIDGE_ACCESS_TOKEN",
)

# interval_minutes → SDK Period 名（延迟解析，避免 import 期依赖 SDK）。
_LONGPORT_PERIOD_NAMES: dict[int, str] = {
    1: "Min_1",
    5: "Min_5",
    15: "Min_15",
    30: "Min_30",
    60: "Min_60",
}


def _classify_longbridge_error(exc: Exception, *, category: str | None = None) -> str:
    """把 SDK 异常归一为**安全类目串**（security-lens P2：绝不回显凭据）.

    SDK 认证异常/签名 URL 常回显 token/secret，原样落 ``error`` 会经
    observability 与 Seesaw loop 进 LLM 上下文（发到本机外）。故**只**返回固定
    类目标签，绝不返回 ``str(exc)``。类目本身也用于区分 auth-过期 vs 瞬态可达性
    （token 过期告警信号，见 Risk 表）。

    Args:
        exc: 原始异常（仅用于关键字分类，**不**进返回值）。
        category: 显式类目（如缺凭据），给定则直接用。
    """
    if category is not None:
        return category
    blob = f"{type(exc).__name__} {exc}".lower()
    auth_kw = (
        "auth", "token", "signature", "unauthor", "401", "403",
        "forbidden", "expired", "invalid_grant", "permission", "entitlement",
    )
    net_kw = (
        "connect", "timeout", "timed out", "unreachable", "refused",
        "dns", "resolve", "network", "reset", "ssl", "000",
    )
    if any(k in blob for k in auth_kw):
        return "auth_failed"
    if any(k in blob for k in net_kw):
        return "unreachable"
    return "fetch_failed"


def _to_iso_shanghai_any(value: Any) -> str | None:
    """把 SDK 时间戳（datetime / str / None）定位到 Asia/Shanghai ISO-8601。"""
    from datetime import datetime
    from zoneinfo import ZoneInfo

    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=ZoneInfo(_SHANGHAI_TZ))
        return dt.astimezone(ZoneInfo(_SHANGHAI_TZ)).isoformat()
    # 字符串：复用东财裸串解析，失败原样返回（best-effort）。
    text = str(value)
    return _to_iso_shanghai(text) or text


def _resolve_longbridge_symbol(symbol: str) -> str:
    """归一为 Longbridge ``ticker.REGION`` 格式（如 ``688008.SH``）.

    委托给 :func:`longbridge_coverage.normalize_symbol`（同规则单点维护）。
    """
    from kss.data.longbridge_coverage import normalize_symbol  # noqa: PLC0415

    return normalize_symbol(symbol)


# 保留本地原始实现（注释，便于审阅）：


class LongbridgeProvider:
    """长桥前向实时适配（经官方 ``longbridge`` SDK）。前向-only，永不 PIT.

    权限口径 = **ChinaConnect LV1 Real-time**（陆股通池，实测科创/创业/沪深主板/
    ETF/指数全通；北交所不覆盖，由路由层拦回东财）。强制 ``.com`` 国际网关
    （KTD1）。数据层契约照旧：**失败不抛**，返回带 ``error`` 的 :class:`FetchResult`；
    凭据绝不进 ``error``（归一为安全类目串）。

    构造可注入 ``quote_context``（测试路径，免装 SDK）；否则从 env 凭据延迟构建。
    """

    name = "longbridge"

    def __init__(self, *, quote_context: Any = None) -> None:
        # KTD1：无条件固化 .com 三网关（强制覆盖，防 SDK 版本漂移回退 .cn）。
        self._force_com_gateways()
        self.version = self._resolve_version()
        self._injected_ctx = quote_context
        self._ctx: Any = quote_context
        self._ctx_error: str | None = None
        self._ctx_built = quote_context is not None

    @staticmethod
    def _force_com_gateways() -> None:
        import os

        for key, url in _LONGPORT_COM_GATEWAY_ENV.items():
            os.environ[key] = url

    @staticmethod
    def _resolve_version() -> str:
        try:
            import longbridge  # noqa: PLC0415

            return f"longbridge-{getattr(longbridge, '__version__', 'unknown')}"
        except Exception:  # noqa: BLE001 — 缺包不致命，版本记 unavailable
            return "longbridge-unavailable"

    @staticmethod
    def _read_credentials() -> tuple[str, str, str] | None:
        """从 env 读三凭据；任一缺失返回 ``None``（走 auth_failed）。"""
        import os

        vals = tuple(os.environ.get(k, "").strip() for k in _LONGBRIDGE_CRED_ENV)
        if all(vals):
            return vals  # type: ignore[return-value]
        return None

    def _ensure_context(self) -> tuple[Any, str | None]:
        """延迟构建 ``QuoteContext``（缓存首次结果）。失败归一为安全类目串。"""
        if self._ctx_built:
            return self._ctx, self._ctx_error
        self._ctx_built = True
        creds = self._read_credentials()
        if creds is None:
            self._ctx_error = "auth_failed"  # 缺凭据（不回显是哪个）
            return None, self._ctx_error
        try:
            from longbridge.openapi import Config, QuoteContext  # noqa: PLC0415

            # SDK 建连时往 stdout 打权限表，污染 bridge JSON → App 解码失败。
            with _suppress_stdio():
                config = Config.from_apikey(*creds)
                self._ctx = QuoteContext(config)
        except Exception as exc:  # noqa: BLE001 — 建连失败走 error，不抛
            self._ctx = None
            self._ctx_error = _classify_longbridge_error(exc)
        return self._ctx, self._ctx_error

    def supported_intervals(self) -> tuple[int, ...]:
        return (1, 5, 15, 30, 60)

    def supported_assets(self) -> tuple[str, ...]:
        return ("stock", "etf", "index")

    def capability(self) -> CapabilityResult:
        """能力门控：可达性近似 = SDK 可 import 且凭据齐（真实触达在 fetch）。"""
        reachable = (
            self.version != "longbridge-unavailable"
            and self._read_credentials() is not None
        )
        eligibility = classify_eligibility(self.name, reachable=reachable)
        return CapabilityResult(
            provider=self.name,
            version=self.version,
            supported_intervals=self.supported_intervals(),
            supported_assets=self.supported_assets(),
            max_history_days=None,  # 券商实时非历史回填源
            eligibility=eligibility,
            reachable=reachable,
            notes=(
                "ChinaConnect LV1 实时（陆股通池，北交所不覆盖）",
                "强制 .com 国际网关",
                "前向-only：结构上不可进 PIT 回测",
            ),
        )

    def _resolve_period(self, interval_minutes: int) -> Any:
        """interval → SDK Period 枚举；缺 SDK 时回退原值（fake ctx 忽略之）。"""
        name = _LONGPORT_PERIOD_NAMES.get(interval_minutes)
        if name is None:
            return None
        try:
            from longbridge.openapi import Period  # noqa: PLC0415

            return getattr(Period, name)
        except Exception:  # noqa: BLE001
            return interval_minutes

    @staticmethod
    def _no_adjust() -> Any:
        try:
            from longbridge.openapi import AdjustType  # noqa: PLC0415

            return AdjustType.NoAdjust
        except Exception:  # noqa: BLE001
            return 0

    def fetch_bars(
        self,
        symbol: str,
        *,
        interval_minutes: int,
        asset_kind: str,
        start: str | None = None,
        end: str | None = None,
    ) -> FetchResult:
        """拉长桥分钟 bar（``ctx.candlesticks``）。异常吞为安全类目 error，不抛。"""
        t0 = time.monotonic()
        period = self._resolve_period(interval_minutes)
        if period is None:
            return FetchResult(
                rows=[], raw_columns=(), source_asof_ts=None, status_code=None,
                latency_ms=(time.monotonic() - t0) * 1000.0,
                error=f"unsupported interval_minutes={interval_minutes!r}",
            )
        ctx, ctx_err = self._ensure_context()
        if ctx is None:
            return FetchResult(
                rows=[], raw_columns=(), source_asof_ts=None, status_code=None,
                latency_ms=(time.monotonic() - t0) * 1000.0,
                error=ctx_err or "auth_failed",
            )
        lb_symbol = _resolve_longbridge_symbol(symbol)
        count = 1000 if (start or end) else 240  # 近端窗口；PIT 回填非本源职责
        try:
            with _suppress_stdio():
                bars = ctx.candlesticks(lb_symbol, period, count, self._no_adjust())
        except Exception as exc:  # noqa: BLE001 — 数据层不抛
            return FetchResult(
                rows=[], raw_columns=(), source_asof_ts=None, status_code=None,
                latency_ms=(time.monotonic() - t0) * 1000.0,
                error=_classify_longbridge_error(exc),
            )
        latency_ms = (time.monotonic() - t0) * 1000.0
        rows = [_longbridge_bar_to_dict(b) for b in (bars or [])]
        if not rows:
            return FetchResult(
                rows=[], raw_columns=_LONGBRIDGE_BAR_COLUMNS, source_asof_ts=None,
                status_code=200, latency_ms=latency_ms, error="empty response",
            )
        source_asof_ts = _to_iso_shanghai_any(
            max((r.get("timestamp") for r in rows if r.get("timestamp")), default=None)
        )
        return FetchResult(
            rows=rows, raw_columns=_LONGBRIDGE_BAR_COLUMNS,
            source_asof_ts=source_asof_ts, status_code=200,
            latency_ms=latency_ms, error=None,
        )

    def fetch_quote(self, symbol: str) -> FetchResult:
        """拉实时快照（``ctx.quote``）。异常吞为安全类目 error，不抛。"""
        t0 = time.monotonic()
        ctx, ctx_err = self._ensure_context()
        if ctx is None:
            return FetchResult(
                rows=[], raw_columns=(), source_asof_ts=None, status_code=None,
                latency_ms=(time.monotonic() - t0) * 1000.0,
                error=ctx_err or "auth_failed",
            )
        lb_symbol = _resolve_longbridge_symbol(symbol)
        try:
            with _suppress_stdio():
                quotes = ctx.quote([lb_symbol])
        except Exception as exc:  # noqa: BLE001
            return FetchResult(
                rows=[], raw_columns=(), source_asof_ts=None, status_code=None,
                latency_ms=(time.monotonic() - t0) * 1000.0,
                error=_classify_longbridge_error(exc),
            )
        latency_ms = (time.monotonic() - t0) * 1000.0
        rows = [_longbridge_quote_to_dict(q) for q in (quotes or [])]
        if not rows:
            return FetchResult(
                rows=[], raw_columns=_LONGBRIDGE_QUOTE_COLUMNS, source_asof_ts=None,
                status_code=200, latency_ms=latency_ms, error="empty response",
            )
        source_asof_ts = _to_iso_shanghai_any(rows[0].get("timestamp"))
        return FetchResult(
            rows=rows, raw_columns=_LONGBRIDGE_QUOTE_COLUMNS,
            source_asof_ts=source_asof_ts, status_code=200,
            latency_ms=latency_ms, error=None,
        )

    def fetch_quotes(self, symbols: list[str]) -> FetchResult:
        """批量实时快照：一次 ``ctx.quote(list)`` 覆盖全部标的（SDK 上限 500）。

        rows 顺序与 SDK 返回一致，每行 ``symbol`` 为 Longbridge 归一码；调用方
        自持请求码↔展示码映射。异常吞为安全类目 error，不抛（数据层契约）。"""
        t0 = time.monotonic()
        if not symbols:
            return FetchResult(
                rows=[], raw_columns=_LONGBRIDGE_QUOTE_COLUMNS, source_asof_ts=None,
                status_code=None, latency_ms=0.0, error="empty symbols",
            )
        ctx, ctx_err = self._ensure_context()
        if ctx is None:
            return FetchResult(
                rows=[], raw_columns=(), source_asof_ts=None, status_code=None,
                latency_ms=(time.monotonic() - t0) * 1000.0,
                error=ctx_err or "auth_failed",
            )
        lb_symbols = [_resolve_longbridge_symbol(s) for s in symbols]
        try:
            with _suppress_stdio():
                quotes = ctx.quote(lb_symbols)
        except Exception as exc:  # noqa: BLE001
            return FetchResult(
                rows=[], raw_columns=(), source_asof_ts=None, status_code=None,
                latency_ms=(time.monotonic() - t0) * 1000.0,
                error=_classify_longbridge_error(exc),
            )
        latency_ms = (time.monotonic() - t0) * 1000.0
        rows = [_longbridge_quote_to_dict(q) for q in (quotes or [])]
        if not rows:
            return FetchResult(
                rows=[], raw_columns=_LONGBRIDGE_QUOTE_COLUMNS, source_asof_ts=None,
                status_code=200, latency_ms=latency_ms, error="empty response",
            )
        newest = max((r.get("timestamp") for r in rows if r.get("timestamp")), default=None)
        return FetchResult(
            rows=rows, raw_columns=_LONGBRIDGE_QUOTE_COLUMNS,
            source_asof_ts=_to_iso_shanghai_any(newest), status_code=200,
            latency_ms=latency_ms, error=None,
        )


_LONGBRIDGE_BAR_COLUMNS: tuple[str, ...] = (
    "timestamp", "open", "high", "low", "close", "volume", "turnover",
)
_LONGBRIDGE_QUOTE_COLUMNS: tuple[str, ...] = (
    "symbol", "last_done", "prev_close", "open", "high", "low",
    "volume", "turnover", "timestamp", "trade_status",
)


def _lb_num(value: Any) -> float | None:
    """SDK 数值 → float（Decimal/int/str 可 JSON 序列化；None 保留）。"""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


@contextlib.contextmanager
def _suppress_stdio():
    """吞掉 longbridge SDK 写到 stdout 的权限表等杂讯（否则污染 bridge JSON）。

    必须 dup2 到 OS fd=1：SDK 原生扩展不走 sys.stdout，仅重定向 Python 层无效。
    """
    import os
    import sys

    devnull = os.open(os.devnull, os.O_WRONLY)
    saved_fd = os.dup(1)
    old_stdout = sys.stdout
    try:
        sys.stdout.flush()
        os.dup2(devnull, 1)
        sys.stdout = open(os.devnull, "w")  # noqa: SIM115 — 与 fd 对齐的 Python 层
        yield
    finally:
        try:
            sys.stdout.flush()
            sys.stdout.close()
        except Exception:  # noqa: BLE001
            pass
        os.dup2(saved_fd, 1)
        os.close(saved_fd)
        os.close(devnull)
        sys.stdout = old_stdout


def _longbridge_bar_to_dict(bar: Any) -> dict[str, Any]:
    """SDK Candlestick → canonical dict（getattr 兜底，容忍字段缺失）。

    timestamp 必须转 ISO 字符串：SDK 给的是 datetime 对象，直接透传会在 bridge
    ``json.dumps``（无 default=）序列化时整链炸掉（quote 侧数值转 float 防 Decimal
    同理；plan 2026-07-14-001 真机实测坑——详情页分钟档 TypeError）。"""
    return {
        "timestamp": _to_iso_shanghai_any(getattr(bar, "timestamp", None)),
        "open": _lb_num(getattr(bar, "open", None)),
        "high": _lb_num(getattr(bar, "high", None)),
        "low": _lb_num(getattr(bar, "low", None)),
        "close": _lb_num(getattr(bar, "close", None)),
        "volume": _lb_num(getattr(bar, "volume", None)),
        "turnover": _lb_num(getattr(bar, "turnover", None)),
    }


def _longbridge_quote_to_dict(quote: Any) -> dict[str, Any]:
    """SDK SecurityQuote → canonical dict（getattr 兜底）。

    真 SDK 字段为 ``prev_close``（Decimal）；历史 fake/旧文档用 ``prev_close_price``。
    数值统一 float，避免 bridge ``json.dumps`` 遇 Decimal 直接炸（实时整链失败）。
    """
    prev = getattr(quote, "prev_close", None)
    if prev is None:
        prev = getattr(quote, "prev_close_price", None)
    return {
        "symbol": getattr(quote, "symbol", None),
        "last_done": _lb_num(getattr(quote, "last_done", None)),
        "prev_close": _lb_num(prev),
        "open": _lb_num(getattr(quote, "open", None)),
        "high": _lb_num(getattr(quote, "high", None)),
        "low": _lb_num(getattr(quote, "low", None)),
        "volume": _lb_num(getattr(quote, "volume", None)),
        "turnover": _lb_num(getattr(quote, "turnover", None)),
        # datetime 在数据层即转 ISO：批量路径 rows 直接进 bridge json.dumps（无 default=），
        # 透传 datetime 整链炸（与 _longbridge_bar_to_dict 同坑同修）。
        "timestamp": _to_iso_shanghai_any(getattr(quote, "timestamp", None)),
        "trade_status": str(getattr(quote, "trade_status", "") or ""),
    }


def schema_hash(raw_columns: tuple[str, ...]) -> str:
    """对响应列名集合算 SHA-256（排序后），用于 U3 schema 漂移冻结对比。"""
    import hashlib

    joined = "|".join(sorted(raw_columns))
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RepresentativeSymbol:
    """探针代表性标的（A6：含至少一个稀薄标的以免 delay/零成交模式失真）。"""

    symbol: str
    asset_kind: str
    label: str
    liquidity_tier: str = "normal"  # normal | thin


# 10 个代表性标的：科创/创业个股 + ETF + 指数，含 1 个稀薄标的（A6）。
DEFAULT_PROBE_UNIVERSE: tuple[RepresentativeSymbol, ...] = (
    RepresentativeSymbol("688008", "stock", "科创-澜起科技"),
    RepresentativeSymbol("688009", "stock", "科创-中国通号"),
    RepresentativeSymbol("688012", "stock", "科创-中微公司"),
    RepresentativeSymbol("300750", "stock", "创业-宁德时代"),
    RepresentativeSymbol("300059", "stock", "创业-东方财富"),
    RepresentativeSymbol("688041", "stock", "科创-海光信息", liquidity_tier="thin"),
    RepresentativeSymbol("588000", "etf", "科创50ETF"),
    RepresentativeSymbol("159915", "etf", "创业板ETF"),
    RepresentativeSymbol("000688", "index", "科创50指数"),
    RepresentativeSymbol("399006", "index", "创业板指"),
)


__all__ = [
    "DEFAULT_PROBE_UNIVERSE",
    "EASTMOTNEY_1M_MAX_HISTORY_DAYS",
    "EM_COLUMN_MAP",
    "EastmoneyAkshareProvider",
    "Eligibility",
    "FORWARD_ONLY_PROVIDERS",
    "CapabilityResult",
    "FetchResult",
    "IntradayProvider",
    "LongbridgeProvider",
    "RepresentativeSymbol",
    "classify_eligibility",
    "schema_hash",
]
