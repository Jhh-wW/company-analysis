"""웹 공개 경계의 엔진 v2 인식을 못 박는다 (엔진 v2 소단계 3-4b).

★ 여기서 지키는 것:
  ① v2 스키마 보고서는 canonical 공개본 투영(build_published_report)을 타지
     않고, v2 3검사(validate_v2)를 통과하면 정본 그대로 공개된다.
  ② v2 3검사에 걸린 보고서는 V2ValidationError로 막힌다 (fail-closed).
  ③ v1 보고서의 공개 경로는 기존 그대로다 — build_published_report에 위임.
  ④ 자동출고 내용 검증기 선택 — v2에만 v2 3검사를 주입하고 v1은 None이다.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from src.features.composer.constants import (
    GRADE_CONFIRMED,
    NOTICE_INSUFFICIENT_EVIDENCE,
    SECTION_IDS,
)
from src.features.composer.port import (
    ComposedReport,
    ComposedSection,
    ComposedSentence,
)
from src.features.composer.render import ENGINE_V2_SCHEMA_VERSION, render_report
from src.features.composer.validate import V2ValidationError, v2_validation_problems
from src.web.routers import reports as reports_router


def _v2_report():
    """검증을 통과하는 최소 v2 보고서 — 1장 본문 + 요약 3문장, 조각 1개 인용."""
    fragments = {
        1: {"종류": "사업내용", "원문": "가나다전자는 반도체 검사 장비 전문기업이다."},
    }
    sections = []
    for section_id in SECTION_IDS:
        if section_id == "identity":
            sections.append(
                ComposedSection(
                    section_id=section_id,
                    sentences=(
                        ComposedSentence(
                            text="반도체 검사 장비를 주력으로 한다.",
                            citations=("1",),
                            grade=GRADE_CONFIRMED,
                        ),
                    ),
                )
            )
        else:
            sections.append(
                ComposedSection(
                    section_id=section_id,
                    sentences=(),
                    notice=NOTICE_INSUFFICIENT_EVIDENCE,
                )
            )
    summary = tuple(
        ComposedSentence(text=text, citations=("1",), grade=GRADE_CONFIRMED)
        for text in ("요약 하나다.", "요약 둘이다.", "요약 셋이다.")
    )
    return render_report(
        "가나다전자",
        ComposedReport(sections=tuple(sections), summary=summary),
        fragments,
        None,
    )


def test_v2_보고서는_canonical_투영_없이_그대로_공개된다(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("v2 보고서가 canonical 공개 투영을 탔습니다")

    monkeypatch.setattr(reports_router, "build_published_report", forbidden)
    report = _v2_report()

    assert reports_router._report_for_output(report) is report


def test_v2_3검사에_걸리면_공개가_막힌다():
    report = _v2_report()
    broken = replace(report, summary_items=[])  # 요약 존재 검사 위반

    with pytest.raises(V2ValidationError):
        reports_router._report_for_output(broken)


def test_v1_보고서는_기존_공개_경로_그대로다(monkeypatch):
    marker = object()
    seen = {}

    def fake_publish(target):
        seen["report"] = target
        return marker

    monkeypatch.setattr(reports_router, "build_published_report", fake_publish)
    v1_report = replace(_v2_report(), schema_version="company-report-v4-canonical")

    assert reports_router._report_for_output(v1_report) is marker
    assert seen["report"] is v1_report


def test_자동출고_내용검증기는_v2에만_주입된다():
    v2_report = _v2_report()
    v1_report = replace(v2_report, schema_version="company-report-v4-canonical")

    assert (
        reports_router._content_validator_for(v2_report) is v2_validation_problems
    )
    assert reports_router._content_validator_for(v1_report) is None
