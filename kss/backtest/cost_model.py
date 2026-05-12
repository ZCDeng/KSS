"""交易成本模型 —— 买卖双边费率 + 可选滑点."""

from __future__ import annotations

import logging
import warnings
from typing import TYPE_CHECKING, Literal

import pandas as pd

if TYPE_CHECKING:  # pragma: no cover - 仅类型注解
    from kss.data.suspension_data import SuspensionData

logger = logging.getLogger(__name__)


class CostModel:
    """交易成本模型，支持买入/卖出双边费率与对称滑点.

    默认费率参考 A 股实盘：

    - 买入：佣金 0.03% + 冲击成本 0.07% = 0.10%
    - 卖出：佣金 0.03% + 印花税 0.10% + 冲击成本 0.07% = 0.20%
    - 滑点：默认 0；现实通常 5~30 bps，可通过参数注入做敏感度扫描.
    """

    def __init__(
        self,
        buy_cost: float = 0.001,
        sell_cost: float = 0.002,
        slippage_bps: float = 0.0,
    ) -> None:
        """初始化成本模型.

        Args:
            buy_cost: 买入单边费率（小数），默认 0.001 (0.1%).
            sell_cost: 卖出单边费率（小数），默认 0.002 (0.2%).
            slippage_bps: 单边对称滑点（基点；1 bps = 0.0001 = 0.01%），默认 0.
                买入/卖出各自承担一次滑点.
        """
        self.buy_cost = buy_cost
        self.sell_cost = sell_cost
        self.slippage_bps = float(slippage_bps)

    # ------------------------------------------------------------------ #
    # 总成本拆分访问
    # ------------------------------------------------------------------ #

    @property
    def slippage_rate(self) -> float:
        """滑点小数表示 = ``slippage_bps / 10_000``."""
        return self.slippage_bps / 1e4

    @property
    def buy_total(self) -> float:
        """买入总单边成本 = 佣金费率 + 滑点."""
        return self.buy_cost + self.slippage_rate

    @property
    def sell_total(self) -> float:
        """卖出总单边成本 = 佣金/印花费率 + 滑点."""
        return self.sell_cost + self.slippage_rate

    def lateral_cost(self) -> float:
        """双边交易成本（买入 + 卖出，含滑点）.

        Returns:
            双边总成本（小数）.
        """
        return self.buy_total + self.sell_total

    def apply(self, portfolio_return: float, turnover: float) -> float:
        """根据换手率扣除交易成本后的净收益.

        Args:
            portfolio_return: 组合毛收益率（小数）.
            turnover: 当期换手率（0~1）.

        Returns:
            净收益率 = 毛收益 - 换手率 × 双边成本.
        """
        return portfolio_return - turnover * self.lateral_cost()


# --------------------------------------------------------------------------- #
# ExecutionModel —— 实盘成交摩擦（涨跌停过滤 + 部分成交 + 开盘流动性）
# --------------------------------------------------------------------------- #


class ExecutionModel:
    """实盘成交摩擦模型 —— 涨跌停过滤 + 部分成交 + 开盘滑点.

    与 :class:`CostModel` 的关系：

    - ``CostModel`` 处理"手续费 + 印花税 + 固定滑点"（已存在，不动）；
    - ``ExecutionModel`` 处理"实盘买不到 / 卖不掉 / 部分成交 / 开盘冲击成本"，
      这些与流动性 / 涨跌停相关，本质上是"过滤可成交股票"和"调整可成交比例".

    用例::

        execution = ExecutionModel(
            limit_up_pct=0.10,      # A 股主板涨停 ±10%（科创板 ±20% 时改为 0.20）
            limit_down_pct=0.10,
            max_volume_pct=0.05,    # 单日最多吃成交量的 5%（避免推价）
            open_slippage_bps=10,   # 开盘冲击成本 10 bps
        )

        # 过滤可成交股票
        tradable = execution.filter_tradable(
            day_df,   # 含 open, pre_close, vol, amount, pct_chg 等列
            side="buy",
        )

        # 加在 CostModel 之上的额外成本
        extra_cost = execution.slippage_cost(turnover_rate=0.5)
    """

    # 涨跌停容差：避免 9.99% / 19.99% 漏检（实盘报价精度 0.01 元，1 元价格下
    # 0.01 元 ≈ 1%，但大多数股票 5 元以上单分误差 ≤ 0.2%；0.5% 是稳健折中）.
    _LIMIT_TOLERANCE: float = 0.005

    def __init__(
        self,
        limit_up_pct: float = 0.10,
        limit_down_pct: float = 0.10,
        max_volume_pct: float = 0.05,
        open_slippage_bps: float = 10.0,
        kcb_limit_pct: float = 0.20,
        *,
        suspension_data: "SuspensionData | None" = None,
        exclude_st: bool = True,
        exclude_suspended: bool = True,
        exclude_zero_volume: bool = True,
    ) -> None:
        """初始化实盘成交模型.

        Args:
            limit_up_pct: 主板涨停比例（默认 10%）.
            limit_down_pct: 主板跌停比例（默认 10%）.
            max_volume_pct: 单股最多吃当日成交额比例（默认 5%）.
            open_slippage_bps: 开盘冲击成本（基点，默认 10 bps = 0.1%）.
            kcb_limit_pct: 科创板/创业板（688/689/300/301/302）涨跌停比例（默认 20%）.
            suspension_data: 可选 :class:`~kss.data.suspension_data.SuspensionData`
                提供停牌 / ST 名单. ``None`` 时退化为只做涨跌停过滤（向后兼容）.
            exclude_st: 启用 ST 名单时，``True`` 表示从可成交集合剔除 ST 股.
            exclude_suspended: 启用停牌名单时，``True`` 表示剔除停牌股.
            exclude_zero_volume: ``True`` 时把当日 ``amount`` 为 0 / NaN 的行
                视作停牌（Tushare 停牌日通常 amount=0），与显式停牌名单互补.
        """
        if not 0 < limit_up_pct < 1:
            raise ValueError(f"limit_up_pct 必须在 (0, 1) 之间，收到: {limit_up_pct}")
        if not 0 < limit_down_pct < 1:
            raise ValueError(f"limit_down_pct 必须在 (0, 1) 之间，收到: {limit_down_pct}")
        if not 0 < max_volume_pct <= 1:
            raise ValueError(
                f"max_volume_pct 必须在 (0, 1] 之间，收到: {max_volume_pct}"
            )
        if open_slippage_bps < 0:
            raise ValueError(
                f"open_slippage_bps 必须 >= 0，收到: {open_slippage_bps}"
            )
        if not 0 < kcb_limit_pct < 1:
            raise ValueError(f"kcb_limit_pct 必须在 (0, 1) 之间，收到: {kcb_limit_pct}")

        self.limit_up_pct = float(limit_up_pct)
        self.limit_down_pct = float(limit_down_pct)
        self.max_volume_pct = float(max_volume_pct)
        self.open_slippage_bps = float(open_slippage_bps)
        self.kcb_limit_pct = float(kcb_limit_pct)

        # 停牌 / ST 配置（向后兼容：默认 None → 跳过对应过滤）
        self.suspension_data = suspension_data
        self.exclude_st = bool(exclude_st)
        self.exclude_suspended = bool(exclude_suspended)
        self.exclude_zero_volume = bool(exclude_zero_volume)
        # 一次性 warn：若开启了零成交过滤但传入面板不含 amount 列
        self._warned_missing_amount = False

    # ------------------------------------------------------------------ #
    # 内部工具
    # ------------------------------------------------------------------ #

    @staticmethod
    def _symbol_to_str(value: object) -> str:
        """提取 symbol 字符串前缀，兼容 "688017.SH" / "300017" / 整数 etc."""
        if value is None:
            return ""
        s = str(value).strip()
        if "." in s:
            s = s.split(".", 1)[0]
        return s

    def _resolve_limit_pct(self, symbol: object) -> float:
        """根据 symbol 前缀返回涨跌停比例.

        - 688xxx / 689xxx → 科创板
        - 300xxx / 301xxx / 302xxx → 创业板（注册制后同 20%）
        - 其他 → 主板默认值
        """
        code = self._symbol_to_str(symbol)
        if not code:
            return self.limit_up_pct
        prefix3 = code[:3]
        if prefix3 in {"688", "689", "300", "301", "302"}:
            return self.kcb_limit_pct
        return self.limit_up_pct

    @staticmethod
    def _row_get(row: pd.Series, key: str) -> object | None:
        """安全取 row[key]，不存在返回 None."""
        try:
            return row[key]
        except (KeyError, IndexError):
            return None

    # ------------------------------------------------------------------ #
    # 涨跌停判定
    # ------------------------------------------------------------------ #

    def is_limit_up(
        self,
        row: pd.Series,
        symbol_col: str = "symbol",
        open_col: str = "open",
        pre_close_col: str = "pre_close",
    ) -> bool:
        """判定该股是否开盘涨停（无法买入）.

        逻辑：T+1 open 相对 pre_close 的涨幅 ≥ limit_pct - 容差（0.5%）.
        对 688xxx / 689xxx / 300xxx / 301xxx / 302xxx 自动用 ``kcb_limit_pct``.

        Args:
            row: 单股单日行情 Series.
            symbol_col / open_col / pre_close_col: 列名.

        Returns:
            True 表示开盘已涨停（买不到）.
        """
        symbol = self._row_get(row, symbol_col)
        open_px = self._row_get(row, open_col)
        pre_close = self._row_get(row, pre_close_col)
        if open_px is None or pre_close is None:
            return False
        try:
            open_f = float(open_px)
            pre_f = float(pre_close)
        except (TypeError, ValueError):
            return False
        if pd.isna(open_f) or pd.isna(pre_f) or pre_f <= 0:
            return False
        limit_pct = self._resolve_limit_pct(symbol)
        change = open_f / pre_f - 1.0
        return change >= (limit_pct - self._LIMIT_TOLERANCE)

    def is_limit_down(
        self,
        row: pd.Series,
        symbol_col: str = "symbol",
        open_col: str = "open",
        pre_close_col: str = "pre_close",
    ) -> bool:
        """判定开盘跌停（无法卖出）."""
        symbol = self._row_get(row, symbol_col)
        open_px = self._row_get(row, open_col)
        pre_close = self._row_get(row, pre_close_col)
        if open_px is None or pre_close is None:
            return False
        try:
            open_f = float(open_px)
            pre_f = float(pre_close)
        except (TypeError, ValueError):
            return False
        if pd.isna(open_f) or pd.isna(pre_f) or pre_f <= 0:
            return False
        limit_pct = self._resolve_limit_pct(symbol)
        change = open_f / pre_f - 1.0
        return change <= -(limit_pct - self._LIMIT_TOLERANCE)

    # ------------------------------------------------------------------ #
    # 可成交过滤
    # ------------------------------------------------------------------ #

    def filter_tradable(
        self,
        day_df: pd.DataFrame,
        side: Literal["buy", "sell"],
        symbol_col: str = "symbol",
        open_col: str = "open",
        pre_close_col: str = "pre_close",
        date_col: str = "trade_date",
        amount_col: str = "amount",
    ) -> pd.DataFrame:
        """过滤当日可成交股票.

        在原始涨跌停过滤基础上，**叠加**：

        - 停牌过滤（``self.exclude_suspended`` + 名单查 ``SuspensionData``）；
        - ST 过滤（``self.exclude_st``，仅 ``side='buy'`` 时启用 —— 已持有的 ST 仍可卖）；
        - 零成交过滤（``self.exclude_zero_volume`` + ``amount`` 列；
          tushare 停牌日 amount=0，等价于隐式停牌名单）.

        向后兼容：``suspension_data is None`` + 缺 ``amount`` 列 → 行为完全
        等同于改造前（只做涨跌停 / 缺列警告）.

        Args:
            day_df: 单日行情 DataFrame.
            side: ``"buy"`` 过滤涨停（无法买入），``"sell"`` 过滤跌停.
            symbol_col / open_col / pre_close_col: 列名.
            date_col: 日期列（用于查停牌 / ST 名单；缺则跳过名单查询）.
            amount_col: 成交额列（用于零成交过滤；缺则跳过该项 + 一次性 warn）.

        Returns:
            可成交股票子集（按原顺序保留 index）.
        """
        if day_df is None or len(day_df) == 0:
            return day_df

        # 缺列容错：缺 pre_close / open 直接放行 + 警告（沿用原逻辑）
        if pre_close_col not in day_df.columns or open_col not in day_df.columns:
            msg = (
                f"ExecutionModel.filter_tradable: 缺少列 "
                f"{pre_close_col!r} 或 {open_col!r}, 跳过涨跌停过滤"
            )
            logger.warning(msg)
            warnings.warn(msg, RuntimeWarning, stacklevel=2)
            return day_df

        if side == "buy":
            mask = ~day_df.apply(
                lambda r: self.is_limit_up(r, symbol_col, open_col, pre_close_col),
                axis=1,
            )
        elif side == "sell":
            mask = ~day_df.apply(
                lambda r: self.is_limit_down(r, symbol_col, open_col, pre_close_col),
                axis=1,
            )
        else:
            raise ValueError(f"side 必须是 'buy' 或 'sell'，收到: {side!r}")

        filtered = day_df.loc[mask]

        # —— 叠加停牌 / ST / 零成交过滤 —— #
        filtered = self._apply_suspension_filters(
            filtered,
            side=side,
            symbol_col=symbol_col,
            date_col=date_col,
            amount_col=amount_col,
        )
        return filtered

    # ------------------------------------------------------------------ #
    # 停牌 / ST / 零成交过滤（4.2 新增）
    # ------------------------------------------------------------------ #

    def _apply_suspension_filters(
        self,
        day_df: pd.DataFrame,
        side: Literal["buy", "sell"],
        symbol_col: str,
        date_col: str,
        amount_col: str,
    ) -> pd.DataFrame:
        """叠加停牌 / ST / 零成交过滤；缺 :class:`SuspensionData` 或缺列时 graceful 退化."""
        if day_df is None or len(day_df) == 0:
            return day_df

        keep = pd.Series(True, index=day_df.index)

        # 1) 零成交（amount=0 / NaN 视作停牌）
        if self.exclude_zero_volume:
            if amount_col in day_df.columns:
                amt = pd.to_numeric(day_df[amount_col], errors="coerce")
                keep &= amt.fillna(0.0) > 0
            elif not self._warned_missing_amount:
                msg = (
                    f"ExecutionModel.filter_tradable: 缺少 {amount_col!r} 列, "
                    f"跳过零成交过滤"
                )
                logger.warning(msg)
                warnings.warn(msg, RuntimeWarning, stacklevel=3)
                self._warned_missing_amount = True

        # 2) 停牌名单 / ST 名单（依赖 SuspensionData）
        sus = self.suspension_data
        if sus is not None and date_col in day_df.columns:
            norm_dates = pd.to_datetime(
                day_df[date_col], errors="coerce"
            ).dt.normalize()
            symbols = day_df[symbol_col].astype(str)

            if self.exclude_suspended and getattr(sus, "suspension", None):
                sus_mask = [
                    (d is not pd.NaT) and (d in sus.suspension.get(s, set()))
                    for s, d in zip(symbols, norm_dates)
                ]
                keep &= ~pd.Series(sus_mask, index=day_df.index)

            # ST 仅在买入侧剔除；卖出侧已持有的 ST 仍允许减仓
            if self.exclude_st and side == "buy" and getattr(sus, "st", None):
                st_mask = [
                    (d is not pd.NaT) and (d in sus.st.get(s, set()))
                    for s, d in zip(symbols, norm_dates)
                ]
                keep &= ~pd.Series(st_mask, index=day_df.index)

        return day_df.loc[keep]

    def is_tradable(
        self,
        row: pd.Series,
        date: object | None = None,
        side: Literal["buy", "sell"] = "buy",
        symbol_col: str = "symbol",
        open_col: str = "open",
        pre_close_col: str = "pre_close",
        date_col: str = "trade_date",
        amount_col: str = "amount",
    ) -> bool:
        """综合行级判定：单股某日是否可成交.

        判定链（任一为真即不可成交）：

        1. 涨停（side=buy）/ 跌停（side=sell）；
        2. ``exclude_zero_volume`` + ``amount`` ≤ 0 / NaN；
        3. ``exclude_suspended`` + 在停牌名单内；
        4. ``exclude_st`` + 在 ST 名单内（仅 buy 侧）.

        Args:
            row: 单股单日行情 Series.
            date: 可选交易日；为 ``None`` 时从 ``row[date_col]`` 读取.
            side: ``"buy"`` / ``"sell"``.

        Returns:
            True 表示可成交.
        """
        # 1) 涨跌停
        if side == "buy" and self.is_limit_up(row, symbol_col, open_col, pre_close_col):
            return False
        if side == "sell" and self.is_limit_down(row, symbol_col, open_col, pre_close_col):
            return False

        # 2) 零成交
        if self.exclude_zero_volume:
            amt = self._row_get(row, amount_col)
            if amt is not None:
                try:
                    amt_f = float(amt)
                    if pd.isna(amt_f) or amt_f <= 0:
                        return False
                except (TypeError, ValueError):
                    pass

        # 3) / 4) 停牌 / ST
        sus = self.suspension_data
        if sus is not None:
            symbol = self._row_get(row, symbol_col)
            if symbol is None:
                return True
            d = date if date is not None else self._row_get(row, date_col)
            if d is None:
                return True
            sym_str = str(symbol)
            if self.exclude_suspended and sus.is_suspended(sym_str, d):
                return False
            if self.exclude_st and side == "buy" and sus.is_st(sym_str, d):
                return False
        return True

    # ------------------------------------------------------------------ #
    # 部分成交 & 滑点
    # ------------------------------------------------------------------ #

    def max_tradable_ratio(
        self,
        row: pd.Series,
        position_value: float,
        amount_col: str = "amount",
    ) -> float:
        """根据日成交额限制单股最大可成交比例（防自我推价）.

        amount 列单位约定与 KSS 一致：tushare 默认 **千元**（``cs_data_*.csv``
        来自 ``daily`` 接口，amount 字段单位为千元）.``position_value`` 用同单位
        即可——本方法只关心比例.

        Args:
            row: 单股单日行情 Series（需含 ``amount`` 列）.
            position_value: 计划下单金额（与 ``amount`` 同单位）.
            amount_col: 成交额列名.

        Returns:
            0.0 ~ 1.0，1.0 表示可以全额成交，缺列/NaN 时也返回 1.0（保守不限）.
        """
        if position_value <= 0:
            return 1.0
        amt = self._row_get(row, amount_col)
        if amt is None:
            return 1.0
        try:
            amt_f = float(amt)
        except (TypeError, ValueError):
            return 1.0
        if pd.isna(amt_f) or amt_f <= 0:
            return 1.0
        cap = self.max_volume_pct * amt_f
        if position_value <= cap:
            return 1.0
        return max(0.0, cap / position_value)

    def slippage_cost(self, turnover_rate: float) -> float:
        """开盘冲击成本（额外小数，叠加在 CostModel 之上）.

        Args:
            turnover_rate: 当期换手率（0~1，可为单边 buy_turnover + sell_turnover
                的折中量；调用方决定）.

        Returns:
            额外成本（小数） = turnover_rate × open_slippage_bps / 10_000.
        """
        if turnover_rate <= 0:
            return 0.0
        return turnover_rate * self.open_slippage_bps / 1e4
