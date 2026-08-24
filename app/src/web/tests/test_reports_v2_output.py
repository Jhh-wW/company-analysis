"""웹 공개 경계의 엔진 v2 인식을 못 박는다 (엔진 v2 소단계 3-4b).

★ 여기서 지키는 것:
  ① v2 스키마 보고서는 canonical 공개본 투영(build_published_report)을 타지
     않고, v2 3검사(validate_v2)를 통과하면 정본 그대로 공개된다.
  ② v2 3검사에 걸린 보고서는 V2ValidationError로 막힌다 (fail-closed).
  ③ v1 보고서의 공개 경로는 기존 그대로다 — build_published_report에 위임.
  ④ 자동출고 내용 검증기 선택 — v2에만 v2 3검사를 주입하고 v1은 None이다.
  ⑤ result.html이 v2 본문(장 제목·[n] 인용·"— 해석" 표지·요약·출처 부록)을
     실제로 화면에 찍고, Notion 채널이 없는 v2에서는 「노션으로 보내기」
     버튼을 보여주지 않는다 (04장 3-4절 3·4항).
"""

from __future__ import annotations

import uuid
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.composer.constants import (
    GRADE_CONFIRMED,
    GRADE_INTERPRETED,
    NOTICE_INSUFFICIENT_EVIDENCE,
    SECTION_IDS,
)
from src.features.composer.port import (
    ComposedReport,
    ComposedSection,
    ComposedSentence,
)
from src.features.composer.render import (
    ENGINE_V2_SCHEMA_VERSION,
    INTERPRETATION_MARKER,
    render_report,
)
from src.features.composer.validate import V2ValidationError, v2_validation_problems
from src.web import job_runtime
from src.web.main import app
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
                        ComposedSentence(
                            text="검사 장비 수요는 앞으로도 이어질 것으로 보인다.",
                            citations=("1",),
                            grade=GRADE_INTERPRETED,
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


def test_v2_결과_화면은_장_제목과_인용_해석_표지를_담는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """result.html v2 분기 — 미완 목록 ③ 실측 (04장 3-4절 3항)."""

    report = _v2_report()
    job_id = f"result-v2-preview-{uuid.uuid4().hex}"
    job_runtime._JOBS.pop(job_id, None)
    monkeypatch.setattr(job_runtime, "_load_saved_report", lambda _job_id: report)
    monkeypatch.setattr(job_runtime, "_link_expired", lambda _report: False)
    # PDF 자동출고 해시 결속(release_state)은 여기서 검증하지 않는다 — 화면
    # 렌더만 격리해서 본다(①②는 test_e2e_offline.py가 프로덕션 경로로 본다).
    monkeypatch.setattr(
        reports_router, "_release_state", lambda **_kwargs: (object(), None)
    )
    monkeypatch.setattr(reports_router, "is_notion_configured", lambda: True)
    session = auth_logic.create_session("admin@example.com", True)

    with TestClient(app) as client:
        response = client.get(
            f"/result/{job_id}",
            cookies={auth_constants.SESSION_COOKIE_NAME: session.token},
        )

    assert response.status_code == 200
    body = response.text
    # v1 전용 "기준을 통과하지 못해" 경고문이 아니라 실제 본문이 나온다.
    assert "현재 보고서 기준을 통과하지 못해" not in body
    # 장 제목 — v3 정본 제목(기업 정체성)과 표시 번호.
    assert '<span class="no">1</span><span class="txt">기업 정체성</span>' in body
    # 문장 끝 [n] 인용 — render.sentence_display_text가 이미 문자열에 찍은 것.
    assert "[1]" in body
    # "— 해석" 표지 — 확인/해석을 구분하는 v2 렌더 표지.
    assert INTERPRETATION_MARKER in body
    # 핵심 요약과 출처 부록.
    assert 'id="report-summary-title">핵심 요약</h2>' in body
    assert (
        'id="report-citations-title"><span class="no">부록</span>'
        '<span class="txt">출처와 검증 상태</span>' in body
    )
    assert 'id="src1"' in body
    # v2는 fact_records 기반 「사실 검증」 열 개념이 없어 그 열을 내지 않는다.
    assert "<th scope=\"col\">사실 검증</th>" not in body
    # Notion 채널은 구조적으로 막혀 있다(항목 ④) — 실패가 확실한 버튼을 숨긴다.
    assert f'action="/notion/{job_id}"' not in body
    assert "노션으로 보내기" not in body
