"""``result.html``의 새 ``visual.kind == 'card'`` 분기가 실제로 그려지는지
end-to-end로 확인한다.

★ 왜 이 시험이 있나 — report_standard.visualization의 카드 판정 로직과
  export_pdf의 카드 렌더는 각각 시험이 있지만, 이 템플릿 분기 자체는
  아무도 렌더해 보지 않았다. 손으로 고친 Jinja 매크로 한 곳이라 오타 하나로
  조용히 깨질 수 있다 — 실제 FastAPI 응답으로 한 번은 태워 본다.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.composer.render import ENGINE_V2_SCHEMA_VERSION
from src.features.pipeline.port import Grade, Report, ReportSection, ReportTable, SummaryItem
from src.features.provenance.sources import Source, SourceKind
from src.web import job_runtime
from src.web.tests.report_route_support import serve_legacy_report_snapshot


def _v2_card_report() -> Report:
    """카드로 판정될 흐름표(1장 정체성 칸 이름) 하나만 담은 최소 v2 보고서."""
    card_table = ReportTable(
        caption="회사가 스스로를 어떻게 규정하나",
        headers=["공식 자기정의", "사업 범위", "이 보고서의 해석"],
        rows=[["소재 가공 회사다.", "가구·가전용 시트 전체.", "B2B 소재 회사로 해석."]],
        cite="[2]",
        presentation="flow",
    )
    section = ReportSection(
        cell="identity",
        title="기업 정체성",
        tables=[card_table],
        display_number="1",
    )
    return Report(
        company="카드렌더테스트",
        job="",
        corp_type="",
        sections=[section],
        citations=[Source(number=2, kind=SourceKind.OTHER, label="테스트 출처")],
        grade=Grade.COMPLETE,
        schema_version=ENGINE_V2_SCHEMA_VERSION,
        summary_items=[SummaryItem(text=f"요약 문장 {i}입니다.") for i in range(1, 4)],
    )


def _render(report: Report) -> str:
    from src.web.main import app
    from src.web.routers import reports as reports_router

    job_id = f"card-render-{uuid.uuid4().hex}"
    job_runtime._JOBS.pop(job_id, None)

    with pytest.MonkeyPatch.context() as mp:
        mp.setenv(auth_constants.ENV_BETA_ADMIN_ONLY, "0")
        mp.setenv(auth_constants.ENV_ADMIN_EMAILS, "admin@example.com")
        job_runtime._start_job_runtime()
        serve_legacy_report_snapshot(mp, report, report_id=job_id)
        mp.setattr(job_runtime, "_link_expired", lambda _report: False)
        mp.setattr(reports_router, "_release_state", lambda **_kwargs: (object(), None))
        mp.setattr(reports_router, "is_notion_configured", lambda: True)
        session = auth_logic.create_session("admin@example.com", True)
        with TestClient(app) as client:
            response = client.get(
                f"/result/{job_id}",
                cookies={auth_constants.SESSION_COOKIE_NAME: session.token},
            )
    assert response.status_code == 200, response.text[:400]
    return response.text


def test_card_kind_renders_section_content_card_without_arrows() -> None:
    body = _render(_v2_card_report())

    assert 'class="report-visual card-chart"' in body, "카드 도식 figure가 안 그려졌습니다"
    assert 'class="card-rows"' in body
    assert 'class="section-content-card"' in body, (
        "카드가 .section-content-card(라벨:값)로 안 나왔습니다 — 새 CSS를 "
        "또 만들었거나 매크로가 다른 class를 쓰고 있을 수 있습니다"
    )
    assert "<dt>공식 자기정의</dt>" in body
    assert "<dd>소재 가공 회사다.</dd>" in body
    assert "<dt>사업 범위</dt>" in body
    assert "<dt>이 보고서의 해석</dt>" in body
    # 화살표 흐름(class="report-visual flow-chart")으로는 안 떨어져야 한다.
    assert 'class="report-visual flow-chart"' not in body


def _v2_portfolio_report() -> Report:
    """3장 — «제목 칸이 있는» 카드(제품마다 카드 1장)를 검증하는 최소 보고서."""
    card_table = ReportTable(
        caption="지금 무엇을 미는가 — 핵심 제품·서비스와 역할",
        headers=["제품·서비스명", "제품·서비스 범위", "중점 추진 근거", "사업적 역할"],
        rows=[
            ["리얼 알루미늄 합지 필름", "가전 표면재", "생산확대", "전사 수익 경로"],
            ["폐플라스틱 열분해유", "자원순환 판매 제품", "투자·증설", "전사 수익 경로"],
        ],
        cite="[1]",
        presentation="flow",
    )
    section = ReportSection(
        cell="portfolio",
        title="핵심 제품·서비스와 포트폴리오 역할",
        tables=[card_table],
        display_number="3",
    )
    return Report(
        company="카드렌더테스트",
        job="",
        corp_type="",
        sections=[section],
        citations=[Source(number=1, kind=SourceKind.OTHER, label="테스트 출처")],
        grade=Grade.COMPLETE,
        schema_version=ENGINE_V2_SCHEMA_VERSION,
        summary_items=[SummaryItem(text=f"요약 문장 {i}입니다.") for i in range(1, 4)],
    )


def test_portfolio_card_titles_render_as_h3_per_product() -> None:
    """★ 제목이 있는 카드(3장)가 실제로 <h3>로 찍히는지 — 지금까지는 모든
    카드 제목이 빈 문자열이라 result.html의 ``{% if card.title %}`` 분기가
    한 번도 실행돼 본 적이 없었다. 처음으로 그 경로를 태운다."""
    body = _render(_v2_portfolio_report())

    assert 'class="report-visual card-chart"' in body
    assert body.count('class="section-content-card"') == 2, "제품 2개 → 카드 2장이어야 합니다"
    assert "<h3>리얼 알루미늄 합지 필름</h3>" in body
    assert "<h3>폐플라스틱 열분해유</h3>" in body
    # 제목으로 빠진 칸("제품·서비스명")은 라벨:값 줄로 «또» 나오면 안 된다(중복 표시 방지).
    assert "<dt>제품·서비스명</dt>" not in body
    assert "<dt>제품·서비스 범위</dt>" in body
    assert "<dt>중점 추진 근거</dt>" in body
    assert "<dt>사업적 역할</dt>" in body
    # 「범위·한계」 — 코드가 붙이는 고정 문구(층2). 카드마다 한 번씩, 총 2번.
    assert body.count("<dt>범위·한계</dt>") == 2
    assert "<dd>공식 근거가 확인한 범위로 한정합니다</dd>" in body
