"""Qlib Alpha158 因子库 KSS 移植版.

参考：Qlib v0.9.7 ``qlib/contrib/data/handler.py::Alpha158DL.get_feature_config()``
（https://github.com/microsoft/qlib/blob/main/qlib/contrib/data/loader.py）.

与 :class:`~kss.features.pipeline.FactorPipeline` 的关系：

- ``FactorPipeline``: KSS 自己写的 49 个因子，已通过 7 轮 bias 防御.
- ``Alpha158``: 158 个工业验证因子，须用 ``strategy_family="mined"``
  （n_trials=100+）跑 DSR 集体筛后才能上线，单个因子不可信任.

使用::

    from kss.features.alpha158 import Alpha158
    factors = Alpha158.generate(df)
    # factors 列名与 Qlib 一致 (KMID, KLEN, MA5, STD20, RANK60, VSUMP10, ...).

工程注意：

- 输入 ``df`` 应是**单股**且按 ``trade_date`` 升序的 DataFrame；
  多股请按 ``symbol`` 分组后逐股调用 :meth:`Alpha158.generate`.
- 全部因子用 pandas / numpy 实现，**不依赖 Qlib**，不引入新依赖.
- NaN 处理：所有 rolling 操作允许 NaN（rolling 窗口起始位置自然为 NaN），
  下游 ``dropna`` 由调用方负责（与 ``FactorPipeline`` 行为一致）.
- 总数：默认配置下 158 个（9 KBAR + 4 价格 + 29 rolling × 5 windows = 158）.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Qlib 默认 rolling 窗口
_WINDOWS: tuple[int, ...] = (5, 10, 20, 30, 60)

# 极小项，与 Qlib +1e-12 保持一致，避免除零
_EPS = 1e-12


class Alpha158:
    """Qlib Alpha158 因子库 KSS 移植版（pandas-only，无外部依赖）.

    每个静态方法返回 ``dict[str, pd.Series]``，键名与 Qlib Alpha158 完全一致
    （KMID, KLEN, MA5, STD10, ..., VSUMD60），便于交叉验证.

    所有 rolling 计算使用 ``min_periods=window``（即窗口未填满时返回 NaN），
    与 Qlib ``Mean/Std/Slope/...`` 默认行为对齐.
    """

    # ================================================================== #
    # 1. KBAR —— 9 个 OHLC 形态因子
    # ================================================================== #

    @staticmethod
    def kbar(df: pd.DataFrame) -> dict[str, pd.Series]:
        """KBAR 形态因子（9 个）.

        Qlib 公式::

            KMID  = ($close-$open)/$open
            KLEN  = ($high-$low)/$open
            KMID2 = ($close-$open)/($high-$low+1e-12)
            KUP   = ($high-Greater($open,$close))/$open
            KUP2  = ($high-Greater($open,$close))/($high-$low+1e-12)
            KLOW  = (Less($open,$close)-$low)/$open
            KLOW2 = (Less($open,$close)-$low)/($high-$low+1e-12)
            KSFT  = (2*$close-$high-$low)/$open
            KSFT2 = (2*$close-$high-$low)/($high-$low+1e-12)
        """
        o = df["open"].astype(float)
        h = df["high"].astype(float)
        l = df["low"].astype(float)
        c = df["close"].astype(float)
        # Greater / Less = 逐元素 max / min
        body_top = pd.concat([o, c], axis=1).max(axis=1)
        body_bot = pd.concat([o, c], axis=1).min(axis=1)
        hl = (h - l) + _EPS
        return {
            "KMID": (c - o) / o,
            "KLEN": (h - l) / o,
            "KMID2": (c - o) / hl,
            "KUP": (h - body_top) / o,
            "KUP2": (h - body_top) / hl,
            "KLOW": (body_bot - l) / o,
            "KLOW2": (body_bot - l) / hl,
            "KSFT": (2 * c - h - l) / o,
            "KSFT2": (2 * c - h - l) / hl,
        }

    # ================================================================== #
    # 2. 价格特征 —— 4 个 相对 close 的比率
    # ================================================================== #

    @staticmethod
    def price_features(df: pd.DataFrame) -> dict[str, pd.Series]:
        """价格相对位置因子（4 个）.

        默认 Alpha158 配置只用 ``windows=[0]`` 与 ``feature=[OPEN,HIGH,LOW,VWAP]``,
        因此对 d=0 简化为 ``$<field>/$close``（CLOSE0 = 1，被 Qlib 默认排除）.

        VWAP 优先用 ``df.vwap``；缺失则按 ``amount/vol`` 估算；再缺失退化为 ``close``.
        """
        c = df["close"].astype(float)
        o = df["open"].astype(float)
        h = df["high"].astype(float)
        l = df["low"].astype(float)
        if "vwap" in df.columns:
            vwap = df["vwap"].astype(float)
        elif "amount" in df.columns and "vol" in df.columns:
            v = df["vol"].astype(float)
            amt = df["amount"].astype(float)
            # Tushare amount 是千元，vol 是手；这里只关心比例稳定，直接相除即可.
            vwap = amt / (v + _EPS)
            # 若个别日 vol = 0 / amount = 0，会得到 NaN/inf —— 回填 close.
            vwap = vwap.replace([np.inf, -np.inf], np.nan).fillna(c)
        else:
            vwap = c.copy()
        return {
            "OPEN0": o / c,
            "HIGH0": h / c,
            "LOW0": l / c,
            "VWAP0": vwap / c,
        }

    # ================================================================== #
    # 3. Rolling factors —— 29 个 × 5 windows = 145
    # ================================================================== #

    # ---- 价格类 ---- #

    @staticmethod
    def rolling_roc(close: pd.Series, windows: tuple[int, ...] = _WINDOWS) -> dict[str, pd.Series]:
        """ROC{w}: ``Ref($close,w)/$close``（注意是 lag/now，不是 now/lag）."""
        return {f"ROC{w}": close.shift(w) / close for w in windows}

    @staticmethod
    def rolling_ma(close: pd.Series, windows: tuple[int, ...] = _WINDOWS) -> dict[str, pd.Series]:
        """MA{w}: ``Mean($close,w)/$close``."""
        return {f"MA{w}": close.rolling(w, min_periods=w).mean() / close for w in windows}

    @staticmethod
    def rolling_std(close: pd.Series, windows: tuple[int, ...] = _WINDOWS) -> dict[str, pd.Series]:
        """STD{w}: ``Std($close,w)/$close``."""
        return {f"STD{w}": close.rolling(w, min_periods=w).std() / close for w in windows}

    @staticmethod
    def rolling_beta(close: pd.Series, windows: tuple[int, ...] = _WINDOWS) -> dict[str, pd.Series]:
        """BETA{w}: ``Slope($close,w)/$close``（对时间索引 0..w-1 的线性回归斜率）."""
        out: dict[str, pd.Series] = {}
        for w in windows:
            out[f"BETA{w}"] = _rolling_slope(close, w) / close
        return out

    @staticmethod
    def rolling_rsqr(close: pd.Series, windows: tuple[int, ...] = _WINDOWS) -> dict[str, pd.Series]:
        """RSQR{w}: ``Rsquare($close,w)`` —— 对时间索引的线性回归 R²."""
        return {f"RSQR{w}": _rolling_rsquare(close, w) for w in windows}

    @staticmethod
    def rolling_resi(close: pd.Series, windows: tuple[int, ...] = _WINDOWS) -> dict[str, pd.Series]:
        """RESI{w}: ``Resi($close,w)/$close`` —— 当前价 vs 线性预测的残差比率."""
        return {f"RESI{w}": _rolling_resi(close, w) / close for w in windows}

    @staticmethod
    def rolling_max(high: pd.Series, close: pd.Series, windows: tuple[int, ...] = _WINDOWS) -> dict[str, pd.Series]:
        """MAX{w}: ``Max($high,w)/$close``."""
        return {f"MAX{w}": high.rolling(w, min_periods=w).max() / close for w in windows}

    @staticmethod
    def rolling_min(low: pd.Series, close: pd.Series, windows: tuple[int, ...] = _WINDOWS) -> dict[str, pd.Series]:
        """MIN{w}: ``Min($low,w)/$close``."""
        return {f"MIN{w}": low.rolling(w, min_periods=w).min() / close for w in windows}

    @staticmethod
    def rolling_qtlu(close: pd.Series, windows: tuple[int, ...] = _WINDOWS) -> dict[str, pd.Series]:
        """QTLU{w}: ``Quantile($close,w,0.8)/$close``."""
        return {
            f"QTLU{w}": close.rolling(w, min_periods=w).quantile(0.8) / close
            for w in windows
        }

    @staticmethod
    def rolling_qtld(close: pd.Series, windows: tuple[int, ...] = _WINDOWS) -> dict[str, pd.Series]:
        """QTLD{w}: ``Quantile($close,w,0.2)/$close``."""
        return {
            f"QTLD{w}": close.rolling(w, min_periods=w).quantile(0.2) / close
            for w in windows
        }

    @staticmethod
    def rolling_rank(close: pd.Series, windows: tuple[int, ...] = _WINDOWS) -> dict[str, pd.Series]:
        """RANK{w}: ``Rank($close,w)`` —— 当前价在过去 w 日的百分位排名 (0~1).

        Qlib `Rank` 实现：rank of current value within trailing window of size w,
        归一化到 [0, 1]（用 (rank-1)/(w-1)，最小=0 最大=1）.
        """
        out: dict[str, pd.Series] = {}
        for w in windows:
            # 在每个 rolling 窗口里求最后一个值的 rank（1-based, average tie-break），
            # 然后归一化到 [0, 1].
            out[f"RANK{w}"] = close.rolling(w, min_periods=w).apply(
                _last_value_rank, raw=True,
            )
        return out

    @staticmethod
    def rolling_rsv(high: pd.Series, low: pd.Series, close: pd.Series, windows: tuple[int, ...] = _WINDOWS) -> dict[str, pd.Series]:
        """RSV{w}: ``($close-Min($low,w))/(Max($high,w)-Min($low,w)+1e-12)``."""
        out: dict[str, pd.Series] = {}
        for w in windows:
            ll = low.rolling(w, min_periods=w).min()
            hh = high.rolling(w, min_periods=w).max()
            out[f"RSV{w}"] = (close - ll) / (hh - ll + _EPS)
        return out

    @staticmethod
    def rolling_imax(high: pd.Series, windows: tuple[int, ...] = _WINDOWS) -> dict[str, pd.Series]:
        """IMAX{w}: ``IdxMax($high,w)/w`` —— 过去 w 日 high 最大值距今相对位置.

        Qlib ``IdxMax`` 返回"从窗口起点到 argmax 的距离"; 这里归一化到 [0, 1].
        """
        out: dict[str, pd.Series] = {}
        for w in windows:
            out[f"IMAX{w}"] = high.rolling(w, min_periods=w).apply(
                _argmax_index, raw=True,
            ) / w
        return out

    @staticmethod
    def rolling_imin(low: pd.Series, windows: tuple[int, ...] = _WINDOWS) -> dict[str, pd.Series]:
        """IMIN{w}: ``IdxMin($low,w)/w``."""
        out: dict[str, pd.Series] = {}
        for w in windows:
            out[f"IMIN{w}"] = low.rolling(w, min_periods=w).apply(
                _argmin_index, raw=True,
            ) / w
        return out

    @staticmethod
    def rolling_imxd(high: pd.Series, low: pd.Series, windows: tuple[int, ...] = _WINDOWS) -> dict[str, pd.Series]:
        """IMXD{w}: ``(IdxMax($high,w)-IdxMin($low,w))/w``."""
        out: dict[str, pd.Series] = {}
        for w in windows:
            imax = high.rolling(w, min_periods=w).apply(_argmax_index, raw=True)
            imin = low.rolling(w, min_periods=w).apply(_argmin_index, raw=True)
            out[f"IMXD{w}"] = (imax - imin) / w
        return out

    @staticmethod
    def rolling_corr(close: pd.Series, volume: pd.Series, windows: tuple[int, ...] = _WINDOWS) -> dict[str, pd.Series]:
        """CORR{w}: ``Corr($close, Log($volume+1), w)``."""
        log_v = np.log(volume.astype(float) + 1)
        return {
            f"CORR{w}": close.rolling(w, min_periods=w).corr(log_v)
            for w in windows
        }

    @staticmethod
    def rolling_cord(close: pd.Series, volume: pd.Series, windows: tuple[int, ...] = _WINDOWS) -> dict[str, pd.Series]:
        """CORD{w}: ``Corr($close/Ref($close,1), Log($volume/Ref($volume,1)+1), w)``."""
        c_ratio = close / close.shift(1)
        v_ratio = volume.astype(float) / volume.astype(float).shift(1)
        log_vr = np.log(v_ratio + 1)
        return {
            f"CORD{w}": c_ratio.rolling(w, min_periods=w).corr(log_vr)
            for w in windows
        }

    @staticmethod
    def rolling_cntp(close: pd.Series, windows: tuple[int, ...] = _WINDOWS) -> dict[str, pd.Series]:
        """CNTP{w}: ``Mean($close>Ref($close,1), w)`` —— 过去 w 日上涨日占比."""
        up = (close > close.shift(1)).astype(float)
        return {f"CNTP{w}": up.rolling(w, min_periods=w).mean() for w in windows}

    @staticmethod
    def rolling_cntn(close: pd.Series, windows: tuple[int, ...] = _WINDOWS) -> dict[str, pd.Series]:
        """CNTN{w}: ``Mean($close<Ref($close,1), w)`` —— 过去 w 日下跌日占比."""
        dn = (close < close.shift(1)).astype(float)
        return {f"CNTN{w}": dn.rolling(w, min_periods=w).mean() for w in windows}

    @staticmethod
    def rolling_cntd(close: pd.Series, windows: tuple[int, ...] = _WINDOWS) -> dict[str, pd.Series]:
        """CNTD{w}: CNTP - CNTN."""
        up = (close > close.shift(1)).astype(float)
        dn = (close < close.shift(1)).astype(float)
        return {
            f"CNTD{w}": up.rolling(w, min_periods=w).mean() - dn.rolling(w, min_periods=w).mean()
            for w in windows
        }

    @staticmethod
    def rolling_sumpnd(close: pd.Series, windows: tuple[int, ...] = _WINDOWS) -> dict[str, pd.Series]:
        """SUMP/SUMN/SUMD: 价格涨/跌部分占比及差值."""
        diff = close - close.shift(1)
        pos = diff.clip(lower=0.0)
        neg = (-diff).clip(lower=0.0)
        abs_diff = diff.abs()
        out: dict[str, pd.Series] = {}
        for w in windows:
            sp = pos.rolling(w, min_periods=w).sum()
            sn = neg.rolling(w, min_periods=w).sum()
            denom = abs_diff.rolling(w, min_periods=w).sum() + _EPS
            out[f"SUMP{w}"] = sp / denom
            out[f"SUMN{w}"] = sn / denom
            out[f"SUMD{w}"] = (sp - sn) / denom
        return out

    @staticmethod
    def rolling_vma(volume: pd.Series, windows: tuple[int, ...] = _WINDOWS) -> dict[str, pd.Series]:
        """VMA{w}: ``Mean($volume,w)/($volume+1e-12)``."""
        v = volume.astype(float)
        return {f"VMA{w}": v.rolling(w, min_periods=w).mean() / (v + _EPS) for w in windows}

    @staticmethod
    def rolling_vstd(volume: pd.Series, windows: tuple[int, ...] = _WINDOWS) -> dict[str, pd.Series]:
        """VSTD{w}: ``Std($volume,w)/($volume+1e-12)``."""
        v = volume.astype(float)
        return {f"VSTD{w}": v.rolling(w, min_periods=w).std() / (v + _EPS) for w in windows}

    @staticmethod
    def rolling_wvma(close: pd.Series, volume: pd.Series, windows: tuple[int, ...] = _WINDOWS) -> dict[str, pd.Series]:
        """WVMA{w}: 成交量加权价格变化波动率.

        ``Std(Abs($close/Ref($close,1)-1)*$volume, w) / (Mean(...) + 1e-12)``.
        """
        v = volume.astype(float)
        x = (close / close.shift(1) - 1).abs() * v
        out: dict[str, pd.Series] = {}
        for w in windows:
            num = x.rolling(w, min_periods=w).std()
            den = x.rolling(w, min_periods=w).mean() + _EPS
            out[f"WVMA{w}"] = num / den
        return out

    @staticmethod
    def rolling_vsumpnd(volume: pd.Series, windows: tuple[int, ...] = _WINDOWS) -> dict[str, pd.Series]:
        """VSUMP/VSUMN/VSUMD: 成交量上涨/下跌部分占比及差值."""
        v = volume.astype(float)
        diff = v - v.shift(1)
        pos = diff.clip(lower=0.0)
        neg = (-diff).clip(lower=0.0)
        abs_diff = diff.abs()
        out: dict[str, pd.Series] = {}
        for w in windows:
            sp = pos.rolling(w, min_periods=w).sum()
            sn = neg.rolling(w, min_periods=w).sum()
            denom = abs_diff.rolling(w, min_periods=w).sum() + _EPS
            out[f"VSUMP{w}"] = sp / denom
            out[f"VSUMN{w}"] = sn / denom
            out[f"VSUMD{w}"] = (sp - sn) / denom
        return out

    # ================================================================== #
    # 主入口：生成全部 158 个因子
    # ================================================================== #

    @staticmethod
    def generate(df: pd.DataFrame, windows: tuple[int, ...] = _WINDOWS) -> pd.DataFrame:
        """生成 Alpha158 全部因子矩阵.

        Args:
            df: 含 ``open``/``high``/``low``/``close``/``vol`` 列的**单股** DataFrame
                （顺序按 ``trade_date`` 升序）；可选 ``amount`` / ``vwap``.
            windows: rolling 窗口集合，默认 ``(5, 10, 20, 30, 60)``.

        Returns:
            DataFrame，行序与 ``df`` 一致；列名是 Qlib 标准命名
            （KMID, KLEN, ..., MA5, STD20, RANK60, VSUMD60, ...），共 158 列.

        Raises:
            ValueError: 缺少必需列.
        """
        required = {"open", "high", "low", "close", "vol"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"Alpha158 缺少必需列: {missing}")

        c = df["close"].astype(float)
        h = df["high"].astype(float)
        l = df["low"].astype(float)
        v = df["vol"].astype(float)

        factors: dict[str, pd.Series] = {}
        factors.update(Alpha158.kbar(df))                       # 9
        factors.update(Alpha158.price_features(df))              # 4
        # rolling: 29 × len(windows) = 145 (默认 windows=5)
        factors.update(Alpha158.rolling_roc(c, windows))         # 5
        factors.update(Alpha158.rolling_ma(c, windows))          # 5
        factors.update(Alpha158.rolling_std(c, windows))         # 5
        factors.update(Alpha158.rolling_beta(c, windows))        # 5
        factors.update(Alpha158.rolling_rsqr(c, windows))        # 5
        factors.update(Alpha158.rolling_resi(c, windows))        # 5
        factors.update(Alpha158.rolling_max(h, c, windows))      # 5
        factors.update(Alpha158.rolling_min(l, c, windows))      # 5
        factors.update(Alpha158.rolling_qtlu(c, windows))        # 5
        factors.update(Alpha158.rolling_qtld(c, windows))        # 5
        factors.update(Alpha158.rolling_rank(c, windows))        # 5
        factors.update(Alpha158.rolling_rsv(h, l, c, windows))   # 5
        factors.update(Alpha158.rolling_imax(h, windows))        # 5
        factors.update(Alpha158.rolling_imin(l, windows))        # 5
        factors.update(Alpha158.rolling_imxd(h, l, windows))     # 5
        factors.update(Alpha158.rolling_corr(c, v, windows))     # 5
        factors.update(Alpha158.rolling_cord(c, v, windows))     # 5
        factors.update(Alpha158.rolling_cntp(c, windows))        # 5
        factors.update(Alpha158.rolling_cntn(c, windows))        # 5
        factors.update(Alpha158.rolling_cntd(c, windows))        # 5
        factors.update(Alpha158.rolling_sumpnd(c, windows))      # 15 (SUMP/N/D × 5)
        factors.update(Alpha158.rolling_vma(v, windows))         # 5
        factors.update(Alpha158.rolling_vstd(v, windows))        # 5
        factors.update(Alpha158.rolling_wvma(c, v, windows))     # 5
        factors.update(Alpha158.rolling_vsumpnd(v, windows))     # 15 (VSUMP/N/D × 5)

        out = pd.DataFrame(factors, index=df.index)
        # 替换 inf 为 NaN：rolling 在常数序列上的 std/0 会得到 inf，下游模型必坏.
        out = out.replace([np.inf, -np.inf], np.nan)
        return out


# ====================================================================== #
# 辅助函数：rolling 高阶算子的 numba-free 实现（够用即可，速度二级目标）
# ====================================================================== #


def _last_value_rank(arr: np.ndarray) -> float:
    """返回数组最后一个元素在数组中的百分位排名（0~1，含 ties，average tie-break）.

    Qlib ``Rank($close, w)`` 等价 implementation：scipy `rankdata(average)` 然后
    取最后一个值的 rank，归一化 `(rank - 1) / (n - 1)` 落到 [0, 1].
    """
    n = arr.shape[0]
    if n <= 1:
        return float("nan")
    last = arr[-1]
    if np.isnan(last):
        return float("nan")
    # average tie-break: rank = (#strictly_less + (#equal + 1)/2)
    less = (arr < last).sum()
    equal = (arr == last).sum()
    rank = less + (equal + 1) / 2.0
    return float((rank - 1) / (n - 1))


def _argmax_index(arr: np.ndarray) -> float:
    """返回 ``arr`` 中最大值的位置（0 = 窗口最早，n-1 = 窗口最新）.

    与 Qlib ``IdxMax``"距离"语义一致：值越大表示 max 越早出现。
    多个最大值时返回**最后一个**（与 Qlib 一致：右端优先）.
    """
    if np.isnan(arr).all():
        return float("nan")
    # numpy nanargmax 取第一个最大；这里取最后一个 max
    finite = ~np.isnan(arr)
    if not finite.any():
        return float("nan")
    max_val = np.nanmax(arr)
    # 最后一个 max 的位置（与 Qlib IdxMax 右端优先一致）
    return float(np.where(arr == max_val)[0][-1])


def _argmin_index(arr: np.ndarray) -> float:
    """返回 ``arr`` 中最小值的位置."""
    if np.isnan(arr).all():
        return float("nan")
    finite = ~np.isnan(arr)
    if not finite.any():
        return float("nan")
    min_val = np.nanmin(arr)
    return float(np.where(arr == min_val)[0][-1])


def _rolling_slope(s: pd.Series, w: int) -> pd.Series:
    """对时间索引 ``[0, 1, ..., w-1]`` 做线性回归，返回每窗口斜率序列."""
    return s.rolling(w, min_periods=w).apply(_slope_last, raw=True)


def _slope_last(arr: np.ndarray) -> float:
    """对长度为 w 的 ``arr`` 做 ``y = α + β·t`` 回归，返回 β."""
    n = arr.shape[0]
    if n < 2 or np.isnan(arr).any():
        return float("nan")
    t = np.arange(n, dtype=float)
    t_mean = t.mean()
    a_mean = arr.mean()
    cov = ((t - t_mean) * (arr - a_mean)).sum()
    var = ((t - t_mean) ** 2).sum()
    if var == 0:
        return float("nan")
    return float(cov / var)


def _rolling_rsquare(s: pd.Series, w: int) -> pd.Series:
    """对时间索引线性回归的 R²."""
    return s.rolling(w, min_periods=w).apply(_rsquare_last, raw=True)


def _rsquare_last(arr: np.ndarray) -> float:
    n = arr.shape[0]
    if n < 2 or np.isnan(arr).any():
        return float("nan")
    var_y = arr.var()
    if var_y == 0:
        return float("nan")
    t = np.arange(n, dtype=float)
    t_mean = t.mean()
    a_mean = arr.mean()
    cov = ((t - t_mean) * (arr - a_mean)).sum()
    var_t = ((t - t_mean) ** 2).sum()
    if var_t == 0:
        return float("nan")
    beta = cov / var_t
    alpha = a_mean - beta * t_mean
    pred = alpha + beta * t
    ss_res = ((arr - pred) ** 2).sum()
    ss_tot = ((arr - a_mean) ** 2).sum()
    if ss_tot == 0:
        return float("nan")
    return float(1.0 - ss_res / ss_tot)


def _rolling_resi(s: pd.Series, w: int) -> pd.Series:
    """对时间索引线性回归 ``y = α + β·t``，返回每窗口**末位点的残差**.

    Qlib ``Resi($close, w)``：用过去 w 日 close 对 ``t = 0..w-1`` 回归，
    取最后一日的 residual（实际值 - 拟合值）.
    """
    return s.rolling(w, min_periods=w).apply(_resi_last, raw=True)


def _resi_last(arr: np.ndarray) -> float:
    n = arr.shape[0]
    if n < 2 or np.isnan(arr).any():
        return float("nan")
    t = np.arange(n, dtype=float)
    t_mean = t.mean()
    a_mean = arr.mean()
    cov = ((t - t_mean) * (arr - a_mean)).sum()
    var_t = ((t - t_mean) ** 2).sum()
    if var_t == 0:
        return float("nan")
    beta = cov / var_t
    alpha = a_mean - beta * t_mean
    pred_last = alpha + beta * t[-1]
    return float(arr[-1] - pred_last)
