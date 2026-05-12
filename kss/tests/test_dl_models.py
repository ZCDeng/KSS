"""DL 模型单元测试（torch 缺失时整组跳过）.

注：整个模块标记为 ``pytest.mark.dl``，默认不在完整套件中收集——
在与 LightGBM / matplotlib 同进程混跑时，MPS 多次 device 切换偶发死锁.
单独跑：``pytest kss/tests/test_dl_models.py`` 或 ``pytest -m dl``.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

torch = pytest.importorskip("torch")

from kss.models.dl_models import LSTMRegressor, TransformerRegressor

pytestmark = pytest.mark.dl


@pytest.fixture
def synthetic_data() -> tuple[np.ndarray, np.ndarray]:
    """N=80 × d=4 dummy 数据；label 依赖 X[:, 0]."""
    rng = np.random.default_rng(0)
    N, d = 80, 4
    X = rng.standard_normal((N, d)).astype("float32")
    y = X[:, 0] * 0.3 + rng.standard_normal(N) * 0.5
    return X, y.astype("float32")


class TestLSTMRegressor:

    def test_fit_predict_shape(self, synthetic_data) -> None:
        X, y = synthetic_data
        m = LSTMRegressor(seq_len=8, epochs=2, batch_size=16, hidden_dim=8)
        m.fit(X[:60], y[:60])
        pred = m.predict(X[60:])
        assert pred.shape == (20,)
        assert pred.dtype == np.float64

    def test_save_load_roundtrip(self, synthetic_data, tmp_path) -> None:
        X, y = synthetic_data
        m = LSTMRegressor(seq_len=8, epochs=2, batch_size=16, hidden_dim=8)
        m.fit(X[:60], y[:60])
        pred_before = m.predict(X[60:])

        path = tmp_path / "lstm.pt"
        m.save(str(path))

        m_new = LSTMRegressor(seq_len=8, hidden_dim=8)
        m_new.load(str(path))
        pred_after = m_new.predict(X[60:])

        assert np.abs(pred_before - pred_after).max() < 1e-6

    def test_predict_without_fit_raises(self) -> None:
        m = LSTMRegressor(seq_len=8)
        with pytest.raises(RuntimeError, match="未训练"):
            m.predict(np.zeros((5, 3), dtype="float32"))

    def test_mismatched_xy_raises(self) -> None:
        m = LSTMRegressor(seq_len=8, epochs=1)
        X = np.zeros((20, 3), dtype="float32")
        y = np.zeros(10, dtype="float32")
        with pytest.raises(ValueError, match="样本数不一致"):
            m.fit(X, y)


class TestTransformerRegressor:

    def test_fit_predict_shape(self, synthetic_data) -> None:
        X, y = synthetic_data
        m = TransformerRegressor(
            seq_len=8, epochs=2, batch_size=16,
            d_model=16, nhead=2, num_layers=1,
        )
        m.fit(X[:60], y[:60])
        pred = m.predict(X[60:])
        assert pred.shape == (20,)

    def test_3d_input_accepted(self, synthetic_data) -> None:
        """已分窗的 3D X 也应被接受."""
        X, y = synthetic_data
        N, d = X.shape
        seq_len = 8
        X_3d = np.lib.stride_tricks.sliding_window_view(
            np.vstack([np.repeat(X[:1], seq_len - 1, axis=0), X]),
            window_shape=(seq_len, d),
        ).reshape(N, seq_len, d).astype("float32")

        m = TransformerRegressor(
            seq_len=seq_len, epochs=1, batch_size=16,
            d_model=16, nhead=2, num_layers=1,
        )
        m.fit(X_3d[:60], y[:60])
        pred = m.predict(X_3d[60:])
        assert pred.shape == (20,)
