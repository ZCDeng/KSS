"""Tushare pro API 单例封装，含指数退避重试."""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Any, Callable

import pandas as pd
import tushare as ts

logger = logging.getLogger(__name__)

# Tushare API 调用的重试策略：3 次尝试、1s/2s 退避；最终失败 log warning 返回 None，
# 遵循 AGENTS.md 数据层契约（不在数据层抛异常）。
_MAX_ATTEMPTS: int = 3
_BACKOFF_BASE_SECONDS: float = 1.0


def _fetch_with_retry(
    fn: Callable[[], pd.DataFrame | None],
    label: str,
    max_attempts: int = _MAX_ATTEMPTS,
    backoff_base: float = _BACKOFF_BASE_SECONDS,
) -> pd.DataFrame | None:
    """对 Tushare API 调用应用指数退避重试.

    异常路径（network/timeout/429）重试；返回 None / 空 DataFrame 视为业务层
    "无数据"信号不重试（避免对真实空响应做无意义重试）。

    Args:
        fn: 无参回调，调用一次 Tushare API.
        label: 日志标签（含 ts_code 与日期窗口）.
        max_attempts: 最大尝试次数（包含首次）.
        backoff_base: 退避基数（秒）；第 n 次失败后等待 ``backoff_base * 2^(n-1)``.

    Returns:
        成功时返回 DataFrame；最终失败或空响应返回 ``None``.
    """
    for attempt in range(1, max_attempts + 1):
        try:
            df = fn()
        except Exception as exc:  # noqa: BLE001
            if attempt >= max_attempts:
                logger.warning(
                    "%s 最终失败（已重试 %d 次）: %s",
                    label,
                    max_attempts - 1,
                    exc,
                )
                return None
            wait = backoff_base * (2 ** (attempt - 1))
            logger.info(
                "%s 第 %d 次尝试失败: %s；%.1fs 后重试",
                label,
                attempt,
                exc,
                wait,
            )
            time.sleep(wait)
            continue
        if df is None or df.empty:
            logger.warning("%s 无数据返回", label)
            return None
        return df
    return None


class TushareClient:
    """Singleton wrapper for tushare pro API.

    The token is resolved once at initialisation from (in order):
    1. ``TUSHARE_TOKEN`` environment variable
    2. ``~/.tushare/token``
    3. ``.tushare_token`` (cwd)
    4. ``tushare_token.txt`` (cwd)
    """

    _instance: TushareClient | None = None
    _pro: Any | None = None

    def __new__(cls) -> TushareClient:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self) -> None:
        if self._pro is not None:
            return

        self._bypass_system_proxy()
        token = self._resolve_token()
        if not token:
            logger.warning("Tushare token not found; API calls will likely fail.")
        else:
            ts.set_token(token)

        self._pro = ts.pro_api()

    @staticmethod
    def _bypass_system_proxy() -> None:
        """把 tushare 接口域名加入 NO_PROXY，绕过 macOS 系统代理.

        tushare 是国内直连接口，不需要代理；但 requests 默认 ``trust_env``
        会读 macOS 系统代理设置（如 Clash 127.0.0.1:7890），代理进程挂起时
        所有 cron 任务跟着 ReadTimeout。追加而非覆盖已有 NO_PROXY 条目；
        requests 对 no_proxy/NO_PROXY 大小写均认，两个都写保证不被遮蔽.
        """
        bypass_hosts = ("api.tushare.pro", "api.waditu.com")
        existing = os.environ.get("no_proxy") or os.environ.get("NO_PROXY") or ""
        hosts = [h.strip() for h in existing.split(",") if h.strip()]
        for host in bypass_hosts:
            if host not in hosts:
                hosts.append(host)
        merged = ",".join(hosts)
        os.environ["NO_PROXY"] = merged
        os.environ["no_proxy"] = merged

    @staticmethod
    def _resolve_token() -> str:
        """Find tushare token from env or known file paths.

        解析优先级（KTD4/S6 定型为 **env-first**）：
        1. ``TUSHARE_TOKEN`` 环境变量（dev 覆盖入口，**首位**；operator 须保证
           运行环境无陈旧 token）。
        2. ``$KSS_STATE_ROOT/secrets/tushare_token``（0600、git-ignored；
           bundle/launchd 部署的规范 secret 落点——U6 渲染的 plist **只**注入
           ``KSS_STATE_ROOT`` env，采集器据此 root 解析此文件，token 绝不进 plist）。
        3. 历史已知文件路径（``~/.tushare/token`` / cwd 文件）。

        选 env-first 而非 secrets-first：保持与 ``S6`` 定型一致（env 是 dev 覆盖，
        secrets 是部署默认），且 launchd plist 不带 ``TUSHARE_TOKEN``，故生产路径
        自然落到 secrets 文件，不被陈旧 env 误用。
        """
        token = os.environ.get("TUSHARE_TOKEN", "").strip()
        if token:
            return token

        state_root = os.environ.get("KSS_STATE_ROOT", "").strip()
        candidates: list[Path] = []
        if state_root:
            candidates.append(Path(state_root).expanduser() / "secrets" / "tushare_token")
        candidates += [
            Path.home() / ".tushare" / "token",
            Path(".tushare_token"),
            Path("tushare_token.txt"),
        ]
        for path in candidates:
            if path.exists():
                try:
                    token = path.read_text(encoding="utf-8").strip()
                    if token:
                        return token
                except OSError:
                    continue
        return ""

    def get_pro(self) -> Any:
        """Return the underlying ``ts.pro_api()`` instance."""
        return self._pro

    def fetch_daily(
        self,
        ts_code: str,
        start: str,
        end: str,
    ) -> pd.DataFrame | None:
        """获取日线 OHLCV 数据（含指数退避重试）.

        Args:
            ts_code: Tushare 代码，例如 ``688008.SH``.
            start: 起始日期，``YYYYMMDD`` 格式.
            end: 截止日期，``YYYYMMDD`` 格式.

        Returns:
            DataFrame；失败或空响应返回 ``None``.
        """
        return _fetch_with_retry(
            lambda: self._pro.daily(
                ts_code=ts_code, start_date=start, end_date=end
            ),
            f"fetch_daily {ts_code} ({start}~{end})",
        )

    def fetch_stk_mins(
        self,
        ts_code: str,
        *,
        freq: str,
        start: str,
        end: str,
    ) -> pd.DataFrame | None:
        """获取分钟 K（``stk_mins``，含指数退避重试）.

        Args:
            ts_code: Tushare 代码，例如 ``688017.SH``.
            freq: 周期，如 ``60min`` / ``15min``（上游无原生 120min）.
            start: 起始，``YYYY-MM-DD HH:MM:SS`` 或 ``YYYYMMDD``.
            end: 截止，同上.

        Returns:
            DataFrame；失败或空响应返回 ``None``.
        """
        return _fetch_with_retry(
            lambda: self._pro.stk_mins(
                ts_code=ts_code, freq=freq, start_date=start, end_date=end
            ),
            f"stk_mins {ts_code} {freq} ({start}~{end})",
        )

    def fetch_daily_basic(
        self,
        ts_code: str,
        start: str,
        end: str,
    ) -> pd.DataFrame | None:
        """获取每日基本面指标（换手率、PE、PB 等，含指数退避重试）.

        Args:
            ts_code: Tushare 代码，例如 ``688008.SH``.
            start: 起始日期，``YYYYMMDD`` 格式.
            end: 截止日期，``YYYYMMDD`` 格式.

        Returns:
            DataFrame；失败或空响应返回 ``None``.
        """
        return _fetch_with_retry(
            lambda: self._pro.daily_basic(
                ts_code=ts_code, start_date=start, end_date=end
            ),
            f"fetch_daily_basic {ts_code} ({start}~{end})",
        )

    # ------------------------------------------------------------------ #
    # 板块层面 API（用于收盘后板块复盘 / 资金轮动分析）
    # ------------------------------------------------------------------ #

    def fetch_moneyflow_ind_dc(self, trade_date: str) -> pd.DataFrame | None:
        """东方财富行业资金流（单日切片，含指数退避重试）.

        Tushare ``moneyflow_ind_dc`` 单日返回 1000+ 行，含 ``content_type``
        列区分「行业 / 概念 / 地域」。调用方需要按 ``content_type == '行业'``
        过滤；保留原始 17 列含 ``net_amount_rate`` / ``buy_elg_amount_rate``
        等关键资金流指标.

        Args:
            trade_date: 交易日，``YYYYMMDD`` 格式.

        Returns:
            DataFrame；失败或空响应返回 ``None``.
        """
        return _fetch_with_retry(
            lambda: self._pro.moneyflow_ind_dc(trade_date=trade_date),
            f"fetch_moneyflow_ind_dc ({trade_date})",
        )

    def fetch_moneyflow_cnt_ths(self, trade_date: str) -> pd.DataFrame | None:
        """同花顺概念板块资金流（单日切片，含指数退避重试）.

        与 ``concept_detail`` 共用同花顺概念命名空间，保证概念名能与
        ``storage/stock_names.csv`` 的 ``concept`` 字段一致.

        Args:
            trade_date: 交易日，``YYYYMMDD`` 格式.

        Returns:
            DataFrame；失败或空响应返回 ``None``.
        """
        return _fetch_with_retry(
            lambda: self._pro.moneyflow_cnt_ths(trade_date=trade_date),
            f"fetch_moneyflow_cnt_ths ({trade_date})",
        )

    def fetch_sw_daily(self, trade_date: str) -> pd.DataFrame | None:
        """申万行业指数日线（单日切片，含 L1/L2/L3 全量，含指数退避重试）.

        返回的 DataFrame 含 ``ts_code`` / ``name`` / ``pct_change`` / ``amount``
        等列。**调用方需自行按 ts_code 前缀过滤所需层级**（申万一级为
        ``801001-801999.SI`` 的特定子集）；本方法不做过滤，保持薄封装.

        Args:
            trade_date: 交易日，``YYYYMMDD`` 格式.

        Returns:
            DataFrame；失败或空响应返回 ``None``.
        """
        return _fetch_with_retry(
            lambda: self._pro.sw_daily(trade_date=trade_date),
            f"fetch_sw_daily ({trade_date})",
        )

    def fetch_moneyflow_hsgt(self, trade_date: str) -> pd.DataFrame | None:
        """沪深港通资金流向（含北向 / 南向单日累计，含指数退避重试）.

        单日返回 1 行，含 ``north_money`` / ``south_money`` 等汇总字段
        （金额单位：万元）。用于复盘报告的「外资态度」section.

        Args:
            trade_date: 交易日，``YYYYMMDD`` 格式.

        Returns:
            DataFrame；失败或空响应返回 ``None``.
        """
        return _fetch_with_retry(
            lambda: self._pro.moneyflow_hsgt(trade_date=trade_date),
            f"fetch_moneyflow_hsgt ({trade_date})",
        )

    def fetch_index_daily(
        self,
        ts_code: str,
        start: str,
        end: str,
    ) -> pd.DataFrame | None:
        """指数日线 OHLCV（含 ``pct_chg`` / ``amount``，用于大盘量价复盘）.

        Tushare ``index_daily`` 接口；支持上证 / 深证 / 创业板 / 科创 50 等
        宽基指数代码（如 ``000001.SH`` / ``399006.SZ`` / ``000688.SH``）.

        Args:
            ts_code: 指数代码（如 ``000001.SH``）.
            start: 起始日期，``YYYYMMDD`` 格式.
            end: 截止日期，``YYYYMMDD`` 格式.

        Returns:
            DataFrame；失败或空响应返回 ``None``.
        """
        return _fetch_with_retry(
            lambda: self._pro.index_daily(
                ts_code=ts_code, start_date=start, end_date=end
            ),
            f"fetch_index_daily {ts_code} ({start}~{end})",
        )

    def fetch_fund_share(
        self,
        ts_code: str,
        start: str,
        end: str,
    ) -> pd.DataFrame | None:
        """场内 ETF 日度份额（``fd_share``，单位万份；用于调仓雷达）.

        Tushare ``fund_share`` 接口。份额 T 日数据通常 T+1 早间可得——
        17:30 复盘当天取到的最新行一般是 T-1，调用方需自行标注数据日期.

        Args:
            ts_code: ETF 代码（如 ``588000.SH``）.
            start: 起始日期，``YYYYMMDD`` 格式.
            end: 截止日期，``YYYYMMDD`` 格式.

        Returns:
            DataFrame（``trade_date`` / ``fd_share``）；失败或空响应返回 ``None``.
        """
        return _fetch_with_retry(
            lambda: self._pro.fund_share(
                ts_code=ts_code, start_date=start, end_date=end
            ),
            f"fetch_fund_share {ts_code} ({start}~{end})",
        )

    def fetch_fund_nav(
        self,
        ts_code: str,
        start: str,
        end: str,
    ) -> pd.DataFrame | None:
        """场内 ETF 日度净值（``unit_nav`` / ``accum_nav``；用于主题 5 日收益）.

        Tushare ``fund_nav`` 接口。收益计算优先 ``accum_nav``（规避分红断崖,
        见 docs/solutions/etf_flow_signal_lessons.md）.

        Args:
            ts_code: ETF 代码（如 ``588000.SH``）.
            start: 起始日期，``YYYYMMDD`` 格式.
            end: 截止日期，``YYYYMMDD`` 格式.

        Returns:
            DataFrame（``nav_date`` / ``unit_nav`` / ``accum_nav``）；
            失败或空响应返回 ``None``.
        """
        return _fetch_with_retry(
            lambda: self._pro.fund_nav(
                ts_code=ts_code, start_date=start, end_date=end
            ),
            f"fetch_fund_nav {ts_code} ({start}~{end})",
        )

    # ------------------------------------------------------------------ #
    # 基本面 / 股票信息（用于风险前过滤）
    # ------------------------------------------------------------------ #

    def fetch_fina_indicator(
        self,
        ts_code: str,
        start: str | None = None,
        end: str | None = None,
        period: str | None = None,
    ) -> pd.DataFrame | None:
        """财务指标（季频，含 ``debt_to_assets`` / ``current_ratio`` 等）.

        Tushare ``fina_indicator`` 单股查询；按 ``period`` 取单季 / ``start``
        ``end`` 取窗口. 财报披露滞后约 30 天，调用方应当用 trade_date - 30d
        作为可见上限.

        Args:
            ts_code: Tushare 代码.
            start: 起始公告日期 ``YYYYMMDD``，可空.
            end: 截止公告日期 ``YYYYMMDD``，可空.
            period: 报告期 ``YYYYMMDD``（如 ``20240930`` = 2024Q3），可空.

        Returns:
            DataFrame 含 ``ts_code`` / ``end_date`` / ``debt_to_assets`` /
            ``current_ratio`` / ``n_income_attr_p`` 等；失败 ``None``.
        """
        kwargs: dict = {"ts_code": ts_code}
        if start:
            kwargs["start_date"] = start
        if end:
            kwargs["end_date"] = end
        if period:
            kwargs["period"] = period
        return _fetch_with_retry(
            lambda: self._pro.fina_indicator(**kwargs),
            f"fetch_fina_indicator {ts_code} ({start or ''}~{end or ''} period={period or ''})",
        )

    def fetch_stock_basic(
        self,
        exchange: str = "",
        list_status: str = "L",
    ) -> pd.DataFrame | None:
        """股票基础信息（``name`` / ``list_date`` / ``delist_date`` / ``market``）.

        Tushare ``stock_basic`` 返回全市场清单. ``list_status='L'`` 仅在
        市股；含 ``'D'``（退市）/ ``'P'``（暂停上市）可换参. ST 检测：
        ``name`` 含 ``'ST'`` / ``'*ST'`` 前缀.

        Args:
            exchange: ``'SSE'`` 上交 / ``'SZSE'`` 深交，空则全市场.
            list_status: ``'L'`` 上市 / ``'D'`` 退市 / ``'P'`` 暂停.

        Returns:
            DataFrame；失败 ``None``.
        """
        return _fetch_with_retry(
            lambda: self._pro.stock_basic(
                exchange=exchange, list_status=list_status,
                fields="ts_code,name,industry,market,list_date,delist_date",
            ),
            f"fetch_stock_basic (exchange={exchange or 'ALL'} status={list_status})",
        )

    def fetch_report_rc(
        self,
        ts_code: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame | None:
        """研报评级明细（``report_rc``，含机构名、评级、报告类型）.

        用于计算"最近 12 个月覆盖该股的独立券商机构数"，作为
        紫苏叶评分 ``coverage_gap_score`` 的动态数据源.

        Args:
            ts_code: 股票代码，如 ``688012.SH``.
            start_date: 起始日期 ``YYYYMMDD``.
            end_date: 截止日期 ``YYYYMMDD``.

        Returns:
            DataFrame（含 ``org_name`` / ``report_type`` / ``report_date`` 等）；
            失败 ``None``.
        """
        return _fetch_with_retry(
            lambda: self._pro.report_rc(
                ts_code=ts_code,
                start_date=start_date,
                end_date=end_date,
            ),
            f"fetch_report_rc ({ts_code} {start_date}-{end_date})",
        )

    def fetch_top10_floatholders(
        self,
        ts_code: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame | None:
        """前十大流通股东（``top10_floatholders``，含机构类型与环比变动）.

        紫苏叶机构持仓动态的主数据源：一次取数同时覆盖
        (a) 机构持仓结构（``holder_type`` 区分风投/基金/一般企业等）与环比
        增减仓（``hold_change``），以及 (b) 北向（陆股通）——北向持股以
        "香港中央结算有限公司" 名义出现在本表中，故无需单独的 ``hk_hold``
        端点（该端点本账号无访问权限，实测返回空）。

        Args:
            ts_code: 股票代码，如 ``688012.SH``.
            start_date: 起始日期 ``YYYYMMDD``.
            end_date: 截止日期 ``YYYYMMDD``.

        Returns:
            DataFrame（含 ``end_date`` / ``holder_name`` / ``hold_ratio`` /
            ``hold_float_ratio`` / ``hold_change`` / ``holder_type`` 等）；失败 ``None``.
        """
        return _fetch_with_retry(
            lambda: self._pro.top10_floatholders(
                ts_code=ts_code,
                start_date=start_date,
                end_date=end_date,
            ),
            f"fetch_top10_floatholders ({ts_code} {start_date}-{end_date})",
        )

    def fetch_daily_basic_history(
        self,
        ts_code: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame | None:
        """单票 ``daily_basic`` 历史窗口（用于 PE_TTM 历史分位）.

        与单日切片 ``fetch_daily_basic`` 不同，本方法按日期区间取一只票的
        时间序列，供紫苏叶估值层计算 PE_TTM 历史分位。

        Args:
            ts_code: 股票代码，如 ``688012.SH``.
            start_date: 起始日期 ``YYYYMMDD``.
            end_date: 截止日期 ``YYYYMMDD``.

        Returns:
            DataFrame（含 ``trade_date`` / ``pe_ttm`` / ``total_mv`` / ``circ_mv``）；失败 ``None``.
        """
        return _fetch_with_retry(
            lambda: self._pro.daily_basic(
                ts_code=ts_code,
                start_date=start_date,
                end_date=end_date,
                fields="ts_code,trade_date,pe,pe_ttm,total_mv,circ_mv",
            ),
            f"fetch_daily_basic_history ({ts_code} {start_date}-{end_date})",
        )
