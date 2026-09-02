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
from src.features.export_notion.notion import NotionExportResult
from src.features.report_standard.public_projection import build_public_projection
from src.web import job_runtime, report_delivery_adapter
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


def _use_stored_legacy_snapshot(
    monkeypatch: pytest.MonkeyPatch,
    report,
) -> None:
    """화면 단위 시험에도 공개 GET의 영속 snapshot 계약을 그대로 적용한다."""

    snapshot = report_delivery_adapter.LegacyPublicReport(
        report=report,
        payload_json="{}",
        generated_at=str(report.generated_at or ""),
        stored_at="2026-08-28T00:00:00+09:00",
    )
    monkeypatch.setattr(
        report_delivery_adapter,
        "load_legacy_public_report",
        lambda _job_id: snapshot,
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
    _use_stored_legacy_snapshot(monkeypatch, report)
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
    # 문장 끝 인용은 «작은 위첨자 링크»로 나간다.
    # ★ 예전에는 render.sentence_display_text가 찍은 「[1]」이 본문 문자열
    #   그대로 인쇄돼, 본문과 «같은 크기»의 대괄호 숫자가 문장마다 박혔다
    #   (사용자 신고 — v1은 이미 .ref 위첨자를 쓰는데 v2만 평문이었다).
    #   번호를 없애거나 새로 매기지 않고 «모양만» 바꾼다.
    assert '<a class="ref" href="#src1" title="출처 1번">1</a>' in body
    본문_시작 = body.index('<p class="prose">')
    본문 = body[본문_시작 : body.index("</p>", 본문_시작)]
    assert "[1]" not in 본문, "본문에 평문 대괄호 번호가 남아 있습니다"
    assert 'class="ref"' in 본문
    # 해석 표지 — 하이픈 붙은 글이 아니라 «둥근 배지»로 나간다.
    # ★ 표지를 없앤 게 아니다. 확인(공시에 그대로 적힌 사실)과 해석(우리가
    #   읽어 낸 것)을 구분하는 것은 이 제품의 핵심 약속이라 «모양»만 바꿨다.
    assert '<span class="grade-tag">해석</span>' in body
    본문2_시작 = body.index('<p class="prose">')
    본문2 = body[본문2_시작 : body.index("</p>", 본문2_시작)]
    assert INTERPRETATION_MARKER not in 본문2, "본문에 하이픈 표지가 남아 있습니다"
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


def test_v2_Notion_직접POST는_더_이상_미지원_409가_아니다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """엔진 v2 Notion 409를 푼다 — 설계 017 §03 결정표 D-6.

    ★ 이 시험은 원래 「v2 Notion POST는 명시적 409다」를 고정하고 있었다.
      근거는 «노션용 변환기가 없다»였고, 설계 §02-6도 그 409를 「슬라이스 6
      전까지 유지」로 못 박았다. 조각 S6이 그 변환기를 만들었으므로(v2는
      공개 봉인 블록만 읽어 화면·PDF와 같은 글자를 낸다) 전제가 사라졌다.
      그래서 «지운» 것이 아니라 «뒤집었다» — 지금은 같은 자리에서
      「미지원 화면이 나오지 않는다」를 지킨다.
    ★ 어댑터까지 실제로 나가는지는 ``test_notion_export_route.py``의
      ``test_v2_Notion_POST는_409가_아니라_블록으로_전송한다``가 본다. 여기서는
      옛 미지원 응답이 되살아나지 않는 것만 본다.
    ★ 공개 블록이 «없던» 시절의 v2 저장본은 여전히 닫힌다 — 그 갈래는
      ``test_notion_export_route.py``의 옛 저장본 시험이 지킨다.
    """

    base = _v2_report()
    report = replace(base, public_projection=build_public_projection(base))
    job_id = f"result-v2-notion-supported-{uuid.uuid4().hex}"
    job_runtime._JOBS.pop(job_id, None)
    monkeypatch.setattr(job_runtime, "_load_saved_report", lambda _job_id: report)
    monkeypatch.setattr(job_runtime, "_link_expired", lambda _report: False)
    monkeypatch.setattr(
        reports_router,
        "send_report_to_notion",
        lambda *_args, **_kwargs: NotionExportResult(
            success=True,
            page_id="v2-supported-page",
            page_url="https://notion.example/v2-supported-page",
        ),
    )

    session = auth_logic.create_session("admin@example.com", True)
    csrf = auth_logic.csrf_token_for_session(session.token)
    with TestClient(app) as client:
        response = client.post(
            f"/notion/{job_id}",
            cookies={auth_constants.SESSION_COOKIE_NAME: session.token},
            data={"csrf_token": csrf},
        )

    assert response.status_code == 200
    assert "노션 내보내기를 지원하지 않습니다" not in response.text
    assert "노션용 변환기가 준비될 때까지" not in response.text
    assert response.headers.get("X-Notion-Export-Status") != "unsupported-engine-v2"


def test_v2_Notion_직접POST도_CSRF와_만료를_미지원보다_먼저_확인한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = _v2_report()
    job_id = f"result-v2-notion-boundary-{uuid.uuid4().hex}"
    job_runtime._JOBS.pop(job_id, None)
    monkeypatch.setattr(job_runtime, "_load_saved_report", lambda _job_id: report)
    expiry_checks: list[bool] = []

    def expired(_report):
        expiry_checks.append(True)
        return True

    monkeypatch.setattr(job_runtime, "_link_expired", expired)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("만료된 v2 보고서가 PDF나 Notion 외부 경계를 호출했습니다")

    monkeypatch.setattr(reports_router, "_release_state", forbidden)
    monkeypatch.setattr(reports_router, "send_report_to_notion", forbidden)

    session = auth_logic.create_session("admin@example.com", True)
    csrf = auth_logic.csrf_token_for_session(session.token)
    cookies = {auth_constants.SESSION_COOKIE_NAME: session.token}
    member = auth_logic.create_session("member@example.com", False)
    member_cookies = {auth_constants.SESSION_COOKIE_NAME: member.token}
    with TestClient(app) as client:
        permission_blocked = client.post(
            f"/notion/{job_id}",
            cookies=member_cookies,
            data={"csrf_token": auth_logic.csrf_token_for_session(member.token)},
            follow_redirects=False,
        )
        csrf_blocked = client.post(f"/notion/{job_id}", cookies=cookies, data={})
        expired_response = client.post(
            f"/notion/{job_id}",
            cookies=cookies,
            data={"csrf_token": csrf},
        )

    assert permission_blocked.status_code == 303
    assert csrf_blocked.status_code == 403
    assert expiry_checks == [True], "권한·CSRF 차단 요청은 보고서 만료 여부도 읽지 않아야 한다"
    assert expired_response.status_code == 410
    assert "노션 내보내기를 지원하지 않습니다" not in expired_response.text


def test_v2_화면이_구성표를_도식으로_그린다(monkeypatch: pytest.MonkeyPatch) -> None:
    """★ 실측 결함 — v2 분기가 도식을 아예 안 그렸다.

    `table_visualization()`을 부르는 곳이 v1 분기 한 곳뿐이라, v2가 표에
    `presentation="composition"`을 붙여도 화면이 그 값을 보지 않았다.
    그래서 매출 구성이 100% 누적 막대가 아니라 «평범한 표»로 나갔다.
    지금은 v1·v2가 같은 매크로를 쓴다.
    """
    from src.features.pipeline.port import ReportTable

    report = _v2_report()
    # 도식 판정기가 받아 주는 모양: 정확히 2열 · 3~5행 · 합계 행 없음 · 합 100%
    구성표 = ReportTable(
        caption="어디서 번 돈인가 — 지역별 매출 비중",
        headers=["구분", "비중"],
        rows=[["한국", "89.29%"], ["중국", "4.52%"], ["인도", "4.99%"], ["기타", "1.19%"]],
        cite="[1]",
        presentation="composition",
    )
    for section in report.sections:
        if section.cell == "business_model":
            section.tables.append(구성표)

    job_id = f"result-v2-visual-{uuid.uuid4().hex}"
    job_runtime._JOBS.pop(job_id, None)
    _use_stored_legacy_snapshot(monkeypatch, report)
    monkeypatch.setattr(job_runtime, "_link_expired", lambda _report: False)
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
    assert 'class="report-visual composition-chart"' in body, "구성 도식이 안 그려졌습니다"
    assert 'class="composition-track"' in body
    assert 'class="chart-legend"' in body
    # 도식으로 그렸으면 같은 표를 «평범한 표»로 또 내지 않는다.
    # (캡션 문구는 figcaption과 aria-label 두 곳에 나오는 것이 정상이라
    #  개수로 세지 않고 «표 마크업이 없는가»로 본다.)
    assert 'class="numtable"' not in body


def test_도식으로_못_그리는_표는_그냥_표로_나간다(monkeypatch: pytest.MonkeyPatch) -> None:
    """빈 자리를 남기지 않는다 — 조건에 못 맞추면 원표를 그대로 보여 준다."""
    from src.features.pipeline.port import ReportTable

    report = _v2_report()
    표 = ReportTable(
        caption="합계가 섞인 표",
        headers=["구분", "금액", "비중"],
        rows=[["한국", "100", "90%"], ["기타", "10", "10%"], ["합계", "110", "100%"]],
        cite="[1]",
        presentation="composition",
    )
    for section in report.sections:
        if section.cell == "business_model":
            section.tables.append(표)

    job_id = f"result-v2-plain-{uuid.uuid4().hex}"
    job_runtime._JOBS.pop(job_id, None)
    _use_stored_legacy_snapshot(monkeypatch, report)
    monkeypatch.setattr(job_runtime, "_link_expired", lambda _report: False)
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

    body = response.text
    assert "합계가 섞인 표" in body
    assert 'class="composition-track"' not in body
    assert 'class="numtable"' in body
