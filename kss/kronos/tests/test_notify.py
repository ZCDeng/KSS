"""U8 交付与监控接线测试（R16/R17）."""

from __future__ import annotations

import pytest

from kss.kronos.notify import (
    NotifyProbeError,
    dry_run_probe,
    render_shadow_card,
    render_stress_card,
    send_card,
)
from kss.kronos.shadow import ShadowVerdict
from kss.kronos.stress_diagnostic import StressDiagnosticReport
from kss.prediction.cross_sectional_forecast import _md_v1_escape


class TestDryRunProbe:
    def test_covers_r16_reserved_chars_escaped_pass(self) -> None:
        """Covers R16: *ST / 5G_概念 等保留字经转义后探针通过（不会 400 静默丢）."""
        name1 = _md_v1_escape("*ST航图")
        name2 = _md_v1_escape("5G_概念")
        msg = f"*标题*\n\n{name1} 属于 {name2}"
        dry_run_probe(msg)  # 不抛即通过

    def test_unescaped_reserved_char_fails_loud(self) -> None:
        """未转义的奇数 * → 探针 fail loud（非静默返回 False）."""
        msg = "*标题*\n\n*ST航图 未转义"  # 标题 2 个 + 漏转义 1 个 = 3（奇数）
        with pytest.raises(NotifyProbeError, match="不平衡"):
            dry_run_probe(msg)

    def test_unbalanced_bracket_fails(self) -> None:
        with pytest.raises(NotifyProbeError, match="括号"):
            dry_run_probe("正文 [未闭合链接")


class TestRenderCards:
    def test_shadow_card_vertical_no_table(self) -> None:
        v = ShadowVerdict(
            n_obs=60, window_full=True, gate_passed=True, graduated=True,
            sharpe=1.23, dsr=0.45, p_value=0.01,
        )
        m = {"dir_rate": 0.58, "brier": 0.21, "coverage": 0.79, "n": 50}
        card = render_shadow_card(v, m)
        # V1 不渲染表格：无表格分隔行（---|---）、无以 | 起始的表格行。
        assert "---" not in card
        assert not any(ln.strip().startswith("|") for ln in card.splitlines())
        assert "可毕业" in card
        dry_run_probe(card)  # 渲染结果自洽

    def test_shadow_card_handles_nan(self) -> None:
        v = ShadowVerdict(
            n_obs=10, window_full=False, gate_passed=False, graduated=False,
            sharpe=float("nan"), dsr=float("nan"), p_value=1.0,
        )
        m = {"dir_rate": float("nan"), "brier": float("nan"), "coverage": float("nan"), "n": 0}
        card = render_shadow_card(v, m)
        assert "—" in card  # NaN 渲染为占位符
        assert "只读" in card
        dry_run_probe(card)

    def test_stress_card_diagnostic_not_gate(self) -> None:
        rep = StressDiagnosticReport(
            strategy_family="mined", n_trials=12, n_deployable=1,
            false_positive_rate=1 / 12, note="x",
        )
        card = render_stress_card(rep)
        assert "非阻断闸门" in card
        assert "假阳性率" in card
        dry_run_probe(card)

    def test_stress_card_no_valid_samples(self) -> None:
        rep = StressDiagnosticReport(
            strategy_family="mined", n_trials=0, n_deployable=0,
            false_positive_rate=None, note="无有效样本",
        )
        card = render_stress_card(rep)
        assert "无有效样本" in card
        dry_run_probe(card)


class TestSendCard:
    def test_happy_console_renders(self, capsys) -> None:
        v = ShadowVerdict(60, True, True, True, 1.2, 0.5, 0.01)
        m = {"dir_rate": 0.6, "brier": 0.2, "coverage": 0.8, "n": 40}
        ok = send_card(render_shadow_card(v, m), channel="console")
        assert ok is True
        assert "影子" in capsys.readouterr().out

    def test_error_probe_failure_fails_loud(self) -> None:
        """error: 探针失败 → 抛错 fail loud，不静默发送 / 返回 False."""
        bad = "*未闭合标题\n\n正文"  # 单个 *
        with pytest.raises(NotifyProbeError):
            send_card(bad, channel="telegram")
