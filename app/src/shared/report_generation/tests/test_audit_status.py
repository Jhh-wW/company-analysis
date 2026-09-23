"""미감사 비교연도 문구 정본과 feature 경계."""

from pathlib import Path
from types import SimpleNamespace

from src.shared.report_generation.audit_status import (
    analysis_period_with_audit_status,
    performance_caption_with_audit_status,
)

_APP_SRC = Path(__file__).resolve().parents[3]


def test_캡션과_표지_기간에_미감사_연도를_한_번만_붙인다():
    caption = performance_caption_with_audit_status("최근 두 사업연도 실적", ("2024",))
    assert caption == "최근 두 사업연도 실적 · 2024년은 감사받지 않은 비교 재무제표"
    assert performance_caption_with_audit_status(caption, ("2024",)) == caption
    assert performance_caption_with_audit_status("그대로", ()) == "그대로"

    period = analysis_period_with_audit_status(
        "2024~2025 완료 회계연도", SimpleNamespace(unaudited_years=("2024",))
    )
    assert period == "2024~2025 완료 회계연도 (2024년은 감사받지 않은 비교 재무제표)"
    assert analysis_period_with_audit_status(period, SimpleNamespace(unaudited_years=("2024",))) == period
    assert analysis_period_with_audit_status("그대로", SimpleNamespace()) == "그대로"


def test_표_생산자와_파이프라인은_report_standard를_거꾸로_import하지_않는다():
    """2026-09-23 총괄 경계 검토 — 미감사 도우미는 shared 정본만 쓴다."""

    for relative in ("features/audit_financials/logic.py", "features/pipeline/real.py"):
        source = (_APP_SRC / relative).read_text(encoding="utf-8")
        assert "report_standard.period_summary" not in source, relative
        assert "shared.report_generation.audit_status" in source, relative
