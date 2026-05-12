"""CLI 命令 smoke 测试 —— 验证错误路径与命令注册.

集中覆盖以下行为，避免 P1 #18 / #19 类型的"echo ✅ 完成 实际崩溃"再次发生：

- 主入口 ``kss`` 注册了所有子命令，没有 import 错误.
- ``kss report`` 显式抛 ClickException 而非假装成功.
- ``kss analyze --model XXX`` 在模型不存在时给出明确错误.
- ``kss scan`` / ``kss predict`` 在股票池不可加载时给出明确错误且非零退出.
- ``kss train`` 在股票池不可加载时给出明确错误且非零退出.

happy-path 用真实模型测 ``kss analyze``，验证训练→分析的端到端契约。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from click.testing import CliRunner

from kss.cli.commands import analyze, predict, report, scan, train
from kss.cli.main import main
from kss.models.lightgbm_model import LightGBMModel
from kss.models.registry import ModelRegistry


class TestCliWiring:
    """CLI 模块连接 smoke 测试."""

    def test_all_commands_registered(self) -> None:
        """主命令应注册全部 8 个子命令（update/train/backtest/predict/scan/report/notify/analyze）."""
        expected = {
            "update", "train", "backtest", "predict",
            "scan", "report", "notify", "analyze",
        }
        runner = CliRunner()
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        # 通过 --help 输出验证命令在帮助文本中
        for cmd_name in expected:
            assert cmd_name in result.output, f"主命令缺少 {cmd_name}"

    def test_commands_have_help(self) -> None:
        """每个新接线的子命令 --help 应返回 0，证明 click decorator 与函数签名匹配."""
        runner = CliRunner()
        for sub_cmd in (train.cmd, scan.cmd, predict.cmd, analyze.cmd, report.cmd):
            result = runner.invoke(sub_cmd, ["--help"])
            assert result.exit_code == 0, \
                f"命令 {sub_cmd.name} --help 失败: {result.output}"


class TestReportNotImplemented:
    """`kss report` 必须 loud-fail 而非假装成功（P1 #19 修复）."""

    def test_report_raises_click_exception(self) -> None:
        runner = CliRunner()
        result = runner.invoke(report.cmd, obj={"config": {}})
        assert result.exit_code != 0, "report 必须非零退出，不可以假装成功"
        assert "尚未实现" in result.output

    def test_report_weekly_also_fails(self) -> None:
        runner = CliRunner()
        result = runner.invoke(report.cmd, ["--type", "weekly"], obj={"config": {}})
        assert result.exit_code != 0
        assert "尚未实现" in result.output


class TestAnalyzeMissingModel:
    """analyze 加载不存在的模型时应明确报错（P1 #19 修复）."""

    def test_analyze_missing_model_fails(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(
                analyze.cmd,
                ["--model", "nonexistent_model_xyz"],
                obj={"config": {}},
            )
            assert result.exit_code != 0
            # 错误消息应可读地指出模型缺失
            assert "❌" in result.output or "尚未保存" in result.output


class TestAnalyzeHappyPath:
    """analyze 完整链路：训练一个真实模型 → analyze 应能输出 feature_importance Markdown."""

    def test_analyze_produces_markdown_report(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            rng = np.random.default_rng(0)
            n = 200
            X = pd.DataFrame({
                "feat_a": rng.standard_normal(n),
                "feat_b": rng.standard_normal(n),
                "feat_c": rng.standard_normal(n),
            })
            # 让 feat_a 与 y 强相关，确保 feature_importance 非空
            y = X["feat_a"] * 2.0 + rng.standard_normal(n) * 0.1

            # 使用 ModelRegistry 默认路径，确保 analyze 子命令内部新建的 registry
            # 与本测试指向同一存储位置（CliRunner.isolated_filesystem 已隔离 cwd）.
            registry = ModelRegistry()
            model = LightGBMModel()
            model.fit(X, y)
            model_name = "lgb_test_5d"
            registry.save(model, name=model_name)

            output_path = "./out_analysis.md"
            result = runner.invoke(
                analyze.cmd,
                ["--model", model_name, "--top", "3", "--output", output_path],
                obj={"config": {}},
            )
            assert result.exit_code == 0, f"analyze 失败: {result.output}"
            assert Path(output_path).exists()

            content = Path(output_path).read_text(encoding="utf-8")
            assert model_name in content
            assert "特征重要性" in content
            # 三个特征都应出现（top=3）
            for feat in ("feat_a", "feat_b", "feat_c"):
                assert feat in content


class TestScanMissingPool:
    """scan 加载不存在的股票池应非零退出（P1 #18 修复）."""

    def test_scan_missing_pool_exits_nonzero(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(
                scan.cmd,
                ["--pool", "definitely_nonexistent_pool_zzz"],
                obj={"config": {}},
            )
            assert result.exit_code != 0
            assert "❌" in result.output


class TestPredictMissingPool:
    """predict 加载不存在的股票池应非零退出."""

    def test_predict_missing_pool_exits_nonzero(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(
                predict.cmd,
                ["--pool", "definitely_nonexistent_pool_zzz"],
                obj={"config": {}},
            )
            assert result.exit_code != 0
            assert "❌" in result.output


class TestTrainMissingPool:
    """train 加载不存在的股票池应非零退出且不产生未训练模型."""

    def test_train_missing_pool_exits_nonzero(self) -> None:
        runner = CliRunner()
        with runner.isolated_filesystem():
            result = runner.invoke(
                train.cmd,
                ["--pool", "definitely_nonexistent_pool_zzz"],
                obj={"config": {}},
            )
            assert result.exit_code != 0
            # 旧 stub 行为是 echo "✅ 完成" → 现在必须显式失败
            assert "❌" in result.output
            assert "✅ 已注册" not in result.output, \
                "train 在数据加载失败后不可以再保存（伪造的）模型"
