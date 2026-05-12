"""深度学习预测模型 —— LSTM / Transformer 回归头.

设计取舍：

- **回归头而非分类**：与 LightGBM 回归一致，便于 :class:`BackendEnsemble`
  做 z-score 融合，避免分类概率与回归打分尺度混用.
- **二维 fit 接口**：上层把 ``(N, n_features)`` 喂进来时，类内按 ``seq_len`` 把它视为
  一段时序序列；上层若已经构造好 3D 张量，``fit`` 也接受 ``X.ndim == 3``.
- **可选依赖 torch**：通过 try/except 延迟导入；缺失时构造期抛 ``ImportError``，
  把"DL 未安装"的失败前置到训练入口而非预测阶段.
- **MPS / CUDA / CPU 自动选择**：M-series Mac 默认走 MPS.

仅作为 :class:`~kss.strategies.ensemble.BackendEnsemble` 的可选成员；
不接入 ``walk_forward`` 滚动重训（单股票训练成本过高）.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from kss.models.base import ModelBase

logger = logging.getLogger(__name__)


try:
    import torch
    import torch.nn as nn
    from torch.utils.data import DataLoader, TensorDataset
    _HAS_TORCH = True
except ImportError:  # pragma: no cover
    _HAS_TORCH = False
    torch = None  # type: ignore[assignment]
    nn = None  # type: ignore[assignment]


def _select_device() -> "torch.device":  # type: ignore[name-defined]
    """自动选择设备：MPS（Apple Silicon）→ CUDA → CPU."""
    if not _HAS_TORCH:
        raise ImportError("torch 未安装；请运行 `pip install -e .[deep]` 启用 DL 后端")
    if torch.backends.mps.is_available():
        return torch.device("mps")
    if torch.cuda.is_available():
        return torch.device("cuda")
    return torch.device("cpu")


# ====================================================================== #
# 网络结构（与根目录 deep_learning_models.py 对齐，但回归头）
# ====================================================================== #


if _HAS_TORCH:  # pragma: no branch

    class _LSTMRegressorNet(nn.Module):  # type: ignore[misc]
        """LSTM 回归网络：取最后时刻隐藏状态 → FC → scalar score."""

        def __init__(
            self,
            input_dim: int,
            hidden_dim: int = 64,
            num_layers: int = 2,
            dropout: float = 0.3,
        ) -> None:
            super().__init__()
            self.lstm = nn.LSTM(
                input_dim, hidden_dim, num_layers,
                batch_first=True,
                dropout=dropout if num_layers > 1 else 0.0,
            )
            self.dropout = nn.Dropout(dropout)
            self.fc1 = nn.Linear(hidden_dim, 32)
            self.fc2 = nn.Linear(32, 1)
            self.relu = nn.ReLU()

        def forward(self, x: "torch.Tensor") -> "torch.Tensor":  # type: ignore[name-defined]
            _, (h_n, _) = self.lstm(x)
            out = self.dropout(h_n[-1])
            out = self.relu(self.fc1(out))
            return self.fc2(out).squeeze(-1)


    class _PositionalEncoding(nn.Module):  # type: ignore[misc]
        def __init__(self, d_model: int, max_len: int = 500) -> None:
            super().__init__()
            pe = torch.zeros(max_len, d_model)
            position = torch.arange(0, max_len, dtype=torch.float).unsqueeze(1)
            div_term = torch.exp(
                torch.arange(0, d_model, 2).float() * (-np.log(10000.0) / d_model)
            )
            pe[:, 0::2] = torch.sin(position * div_term)
            pe[:, 1::2] = torch.cos(position * div_term)
            self.register_buffer("pe", pe.unsqueeze(0))

        def forward(self, x: "torch.Tensor") -> "torch.Tensor":  # type: ignore[name-defined]
            return x + self.pe[:, : x.size(1)]


    class _TransformerRegressorNet(nn.Module):  # type: ignore[misc]
        """Transformer 回归网络：mean-pool 后输出 scalar."""

        def __init__(
            self,
            input_dim: int,
            d_model: int = 64,
            nhead: int = 4,
            num_layers: int = 2,
            dropout: float = 0.3,
        ) -> None:
            super().__init__()
            self.input_fc = nn.Linear(input_dim, d_model)
            self.pos_encoder = _PositionalEncoding(d_model)
            encoder_layer = nn.TransformerEncoderLayer(
                d_model=d_model, nhead=nhead, dim_feedforward=d_model * 4,
                dropout=dropout, batch_first=True,
            )
            self.transformer = nn.TransformerEncoder(encoder_layer, num_layers=num_layers)
            self.dropout = nn.Dropout(dropout)
            self.fc1 = nn.Linear(d_model, 32)
            self.fc2 = nn.Linear(32, 1)
            self.relu = nn.ReLU()

        def forward(self, x: "torch.Tensor") -> "torch.Tensor":  # type: ignore[name-defined]
            x = self.input_fc(x)
            x = self.pos_encoder(x)
            x = self.transformer(x)
            x = x.mean(dim=1)
            x = self.dropout(x)
            x = self.relu(self.fc1(x))
            return self.fc2(x).squeeze(-1)

else:  # pragma: no cover
    _LSTMRegressorNet = None  # type: ignore[assignment]
    _TransformerRegressorNet = None  # type: ignore[assignment]


# ====================================================================== #
# ModelBase 子类
# ====================================================================== #


class _DLBase(ModelBase):
    """LSTM / Transformer 共享的训练 / 序列化逻辑."""

    backend: str = "dl"

    def __init__(
        self,
        seq_len: int = 20,
        epochs: int = 30,
        batch_size: int = 64,
        learning_rate: float = 1e-3,
        weight_decay: float = 1e-5,
        patience: int = 8,
        device: str | None = None,
        **net_kwargs: Any,
    ) -> None:
        """初始化.

        Args:
            seq_len: 时序窗口长度；当 ``fit`` 接到 2D X 时按 ``seq_len`` 切窗.
            epochs: 训练轮数.
            batch_size: 批大小.
            learning_rate: Adam 学习率.
            weight_decay: L2 正则系数.
            patience: 早停耐心轮数.
            device: ``"cpu" / "mps" / "cuda" / None``；``None`` 自动选择.
            **net_kwargs: 透传给具体网络（hidden_dim、nhead 等）.

        Raises:
            ImportError: torch 未安装.
        """
        if not _HAS_TORCH:
            raise ImportError(
                "torch 未安装；请运行 `pip install -e .[deep]` 启用 DL 后端"
            )
        self.seq_len = int(seq_len)
        self.epochs = int(epochs)
        self.batch_size = int(batch_size)
        self.learning_rate = float(learning_rate)
        self.weight_decay = float(weight_decay)
        self.patience = int(patience)
        self.device = torch.device(device) if device else _select_device()
        self.net_kwargs = net_kwargs
        self._net: "nn.Module | None" = None  # type: ignore[name-defined]
        self.input_dim: int | None = None

    # ------------------------------------------------------------------ #
    # 子类接口
    # ------------------------------------------------------------------ #

    def _build_net(self, input_dim: int) -> "nn.Module":  # type: ignore[name-defined]
        raise NotImplementedError

    # ------------------------------------------------------------------ #
    # 数据预处理
    # ------------------------------------------------------------------ #

    def _prepare_tensor(self, X: Any) -> "torch.Tensor":  # type: ignore[name-defined]
        """把 2D 或 3D X 标准化为 ``(N, seq_len, input_dim)`` 张量.

        2D 输入：每行被视为单一时刻的特征，构造长度 ``seq_len`` 的滑窗序列.
        其中前 ``seq_len-1`` 行无完整历史，会被前向填充（重复首行）做边界处理.
        """
        arr = np.asarray(X, dtype=np.float32)
        if arr.ndim == 3:
            return torch.from_numpy(arr)
        if arr.ndim != 2:
            raise ValueError(f"X 必须为 2D 或 3D，收到 ndim={arr.ndim}")

        n, d = arr.shape
        seq_len = self.seq_len
        # padded: 前缀重复首行
        padded = np.vstack([np.repeat(arr[:1], seq_len - 1, axis=0), arr])
        windows = np.lib.stride_tricks.sliding_window_view(
            padded, window_shape=(seq_len, d)
        ).reshape(n, seq_len, d)
        return torch.from_numpy(windows.astype(np.float32))

    # ------------------------------------------------------------------ #
    # ModelBase 实现
    # ------------------------------------------------------------------ #

    def fit(self, X: Any, y: Any, **kwargs: Any) -> None:
        """训练.

        Args:
            X: 特征矩阵，2D ``(N, n_features)`` 或已分窗 3D ``(N, seq_len, n_features)``.
            y: 目标变量，1D ``(N,)``.
            **kwargs: ``valid_X`` / ``valid_y`` 可选验证集；其它忽略.
        """
        X_tensor = self._prepare_tensor(X)
        y_tensor = torch.from_numpy(np.asarray(y, dtype=np.float32))
        if len(y_tensor) != len(X_tensor):
            raise ValueError(
                f"X/y 样本数不一致：X={len(X_tensor)}, y={len(y_tensor)}"
            )

        self.input_dim = X_tensor.shape[-1]
        self._net = self._build_net(self.input_dim).to(self.device)
        criterion = nn.MSELoss()
        optimizer = torch.optim.Adam(
            self._net.parameters(),
            lr=self.learning_rate,
            weight_decay=self.weight_decay,
        )
        scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
            optimizer, patience=3, factor=0.5
        )

        train_ds = TensorDataset(X_tensor, y_tensor)
        train_loader = DataLoader(train_ds, batch_size=self.batch_size, shuffle=True)

        valid_X = kwargs.get("valid_X")
        valid_y = kwargs.get("valid_y")
        valid_loader: "DataLoader | None" = None
        if valid_X is not None and valid_y is not None:
            vX = self._prepare_tensor(valid_X)
            vy = torch.from_numpy(np.asarray(valid_y, dtype=np.float32))
            valid_loader = DataLoader(
                TensorDataset(vX, vy), batch_size=self.batch_size, shuffle=False
            )

        best_val = float("inf")
        best_state: dict[str, Any] | None = None
        no_improve = 0
        for epoch in range(self.epochs):
            self._net.train()
            for xb, yb in train_loader:
                xb = xb.to(self.device)
                yb = yb.to(self.device)
                optimizer.zero_grad()
                pred = self._net(xb)
                loss = criterion(pred, yb)
                loss.backward()
                optimizer.step()

            # 验证 / 早停
            if valid_loader is not None:
                self._net.eval()
                val_loss = 0.0
                with torch.no_grad():
                    for xb, yb in valid_loader:
                        xb = xb.to(self.device)
                        yb = yb.to(self.device)
                        pred = self._net(xb)
                        val_loss += criterion(pred, yb).item()
                val_loss /= max(1, len(valid_loader))
                scheduler.step(val_loss)
                if val_loss < best_val - 1e-6:
                    best_val = val_loss
                    best_state = {k: v.detach().cpu().clone() for k, v in self._net.state_dict().items()}
                    no_improve = 0
                else:
                    no_improve += 1
                    if no_improve >= self.patience:
                        logger.info("DL 早停 @ epoch %d (val_loss=%.6f)", epoch + 1, best_val)
                        break

        if best_state is not None:
            self._net.load_state_dict(best_state)

    def predict(self, X: Any) -> np.ndarray:
        if self._net is None:
            raise RuntimeError("模型未训练；先调用 fit() 或 load()")
        X_tensor = self._prepare_tensor(X).to(self.device)
        self._net.eval()
        with torch.no_grad():
            scores = self._net(X_tensor)
        return scores.detach().cpu().numpy().astype(np.float64)

    def save(self, path: str) -> None:
        if self._net is None:
            raise RuntimeError("模型未训练；无可保存内容")
        out = {
            "state_dict": self._net.state_dict(),
            "input_dim": self.input_dim,
            "seq_len": self.seq_len,
            "net_kwargs": self.net_kwargs,
            "backend": self.backend,
        }
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        torch.save(out, path)

    def load(self, path: str) -> None:
        ckpt = torch.load(path, map_location=self.device, weights_only=False)
        self.seq_len = int(ckpt.get("seq_len", self.seq_len))
        self.input_dim = int(ckpt["input_dim"])
        self.net_kwargs = dict(ckpt.get("net_kwargs", {}))
        self._net = self._build_net(self.input_dim).to(self.device)
        self._net.load_state_dict(ckpt["state_dict"])
        self._net.eval()


class LSTMRegressor(_DLBase):
    """LSTM 回归模型."""

    backend = "lstm"

    def _build_net(self, input_dim: int) -> "nn.Module":  # type: ignore[name-defined]
        return _LSTMRegressorNet(input_dim=input_dim, **self.net_kwargs)


class TransformerRegressor(_DLBase):
    """Transformer 回归模型."""

    backend = "transformer"

    def _build_net(self, input_dim: int) -> "nn.Module":  # type: ignore[name-defined]
        return _TransformerRegressorNet(input_dim=input_dim, **self.net_kwargs)
