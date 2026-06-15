# kronos_vendor

Vendored copy of [Kronos](https://github.com/shiyu-coder/Kronos)（K 线基础模型，AAAI 2026，arXiv:2508.02739，MIT）。

只读离线接入 KSS：见 `kss/kronos/`。`log_mv` 实盘路径全程不动。

## 内容

- `model/` — 上游 `Kronos` / `KronosTokenizer` / `KronosPredictor`（字节一致）。
- `finetune/` — 上游微调脚本（字节一致）。
- `LICENSE` — 上游 MIT 许可。
- `UPSTREAM_COMMIT` — vendored 时的上游 commit，供 provenance / 升级对照。
- `requirements.txt` — 隔离依赖（不并入 KSS 主依赖）。

## 导入

不要直接 `import model`（会污染顶层命名空间）。统一经 `kss.kronos` 的 shim：

```python
from kss.kronos import load_vendor  # 把 kronos_vendor/ 挂上 sys.path
Kronos, KronosTokenizer, KronosPredictor = load_vendor()
```

## 升级

```bash
git clone --depth 1 https://github.com/shiyu-coder/Kronos.git /tmp/Kronos
cp -R /tmp/Kronos/model kronos_vendor/model
cp -R /tmp/Kronos/finetune kronos_vendor/finetune
git -C /tmp/Kronos rev-parse HEAD > kronos_vendor/UPSTREAM_COMMIT
```

升级后重跑 `kss/kronos/tests/` 确认契约未漂移。
