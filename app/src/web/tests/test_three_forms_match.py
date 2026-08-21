"""동일 canonical 보고서가 화면·PDF·Notion에서 같은 내용을 내는지 검증한다.

출력기는 사실을 새로 만들지 않는다. canonical 출고 게이트가 확정한 회사 사실,
요약, 의미 기반 장 순서와 출처만 각 매체의 표현 방식으로 바꾼다.
"""

from __future__ import annotations

import html as html_lib
import io
import re
from dataclasses import replace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pypdf import PdfReader

from src.core.citations import citation_number
from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.export_notion.logic import build_blocks
from src.features.export_pdf.logic import PDFGenerationError, build_pdf
from src.features.pipeline.canonical_demo import build_demo_report
from src.features.pipeline.port import (
    FactRecord,
    Grade,
    Report,
    ReportSection,
    ReportTable,
    SourceStatus,
    SummaryItem,
)
from src.features.provenance.sources import Source, SourceKind
from src.features.report_standard import CANONICAL_SCHEMA_VERSION, SECTION_BY_ID
from src.features.report_standard.publish import PublishBlockedError
from src.web import job_runtime
from src.web import main as web_main

app = web_main.app

_NOISE = re.compile(r"[\s·•\-–—\[\]()〔〕「」『』:：,，.]+")
_LEGACY_META = "LEGACY-JOB-AND-PROCESS-META"


def normalize(text: str) -> str:
    """매체별 여백·기호 차이를 걷어 내고 실제 글자만 비교한다."""

    return _NOISE.sub("", text)


def _source(number: int, owner: str) -> Source:
    return Source(
        number=number,
        kind=SourceKind.FILING,
        label=f"공식 문서 {number}",
        disclosed_at="2026-03-18",
        source_id=f"src-{number}",
        title=f"공식 문서 {number}",
        publisher="가나다 주식회사",
        host="DART",
        url=f"https://dart.example/{number}",
        document_id=f"doc-{number}",
        location=f"PDF p.{number}",
        source_type="공식 공시",
        fact_status="실제",
        used_in=[owner],
    )


def _fact(
    number: int,
    owner: str,
    claim: str,
    *,
    time_state: str = "standing",
) -> FactRecord:
    return FactRecord(
        fact_id=f"fact-{number}",
        legal_entity="가나다 주식회사",
        subject_scope=f"대상-{number}",
        relationship_or_action=f"행동-{number}",
        claim=claim,
        claim_type="공식 사실",
        section_owner=owner,
        time_state=time_state,
        as_of="2026-03-18",
        source_id=f"src-{number}",
        source_type="공식 공시",
        source_title=f"공식 문서 {number}",
        source_publisher="가나다 주식회사",
        location=f"PDF p.{number}",
        status="verified",
        state_evidence=f"원문 문단 {number}",
    )


def _legacy_incomplete_report() -> Report:
    claims = {
        1: "공식 문서가 회사를 기술 중심 기업으로 규정한다",
        2: "고객 계약과 유지보수가 수익 구조를 이룬다",
        3: "주력 플랫폼이 고객 접점의 중심 역할을 맡는다",
        4: "핵심 서비스 | 수익화 연결",
        5: "제품 중심 구조에서 서비스 결합 구조로 바뀌었다",
    }
    owners = {
        1: "identity",
        2: "business_model",
        3: "portfolio",
        4: "portfolio",
        5: "past_changes",
    }
    facts = [
        _fact(
            number,
            owners[number],
            claim,
            time_state="completed" if owners[number] == "past_changes" else "standing",
        )
        for number, claim in claims.items()
    ]
    sections = [
        ReportSection(
            cell="past_changes",
            title="임시 과거 제목",
            display_number="99",
            tag="#틀림",
            lines=[("과거 원문 반복 금지", "[5]")],
            prose_lines=[(claims[5], "[5]")],
            fact_ids=["fact-5"],
        ),
        ReportSection(
            cell="portfolio",
            title="임시 포트폴리오 제목",
            display_number="98",
            lines=[("포트폴리오 원문 반복 금지", "[3]")],
            prose_lines=[(claims[3], "[3]")],
            tables=[
                ReportTable(
                    caption="포트폴리오 역할",
                    headers=["구분", "사업적 역할"],
                    rows=[["핵심 서비스", "수익화 연결"]],
                    cite="[4]",
                )
            ],
            fact_ids=["fact-3", "fact-4"],
        ),
        ReportSection(
            cell="identity",
            title="임시 정체성 제목",
            display_number="97",
            lines=[("정체성 원문 반복 금지", "[1]")],
            prose_lines=[(claims[1], "[1]")],
            fact_ids=["fact-1"],
        ),
        ReportSection(
            cell="business_model",
            title="임시 사업 제목",
            display_number="96",
            lines=[("사업 구조 원문 반복 금지", "[2]")],
            prose_lines=[(claims[2], "[2]")],
            fact_ids=["fact-2"],
        ),
    ]
    return Report(
        company="가나다",
        job=_LEGACY_META,
        corp_type="상장사",
        grade=Grade.PARTIAL,
        sections=sections,
        requirements=[_LEGACY_META],
        sources=[SourceStatus("내부 수집 현황", "failed", _LEGACY_META)],
        citations=[_source(number, owners[number]) for number in claims],
        cells={"5": True},
        shortfall_reasons=[_LEGACY_META],
        generated_at="2026-08-19",
        schema_version=CANONICAL_SCHEMA_VERSION,
        summary_items=[
            SummaryItem("공식 정체성이 사업 구조와 연결된다", "identity"),
            SummaryItem("계약과 유지보수가 수익의 두 축이다", "business_model"),
            SummaryItem("주력 플랫폼이 고객 접점을 묶는다", "portfolio"),
        ],
        fact_records=facts,
        as_of_date="2026-03-18",
        analysis_period="2023~2025 완료 회계연도",
        latest_performance_period="2026년 1분기 확정",
    )


def canonical_report() -> Report:
    """실제 공개 게이트를 통과한 1~9장 보고서에 내부 원문만 구분해 둔다."""

    report = build_demo_report()
    sections = [
        replace(
            section,
            lines=[(f"내부 감사 원문 {section.cell}", "")],
        )
        for section in report.sections
    ]
    return replace(report, sections=sections)


def _pdf_text(report: Report) -> str:
    reader = PdfReader(io.BytesIO(build_pdf(report)))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


def _notion_text(report: Report) -> str:
    def rich_text_text(items: list[dict[str, Any]]) -> str:
        return "".join(item["text"]["content"] for item in items)

    def block_text(block: dict[str, Any]) -> list[str]:
        payload = block[block["type"]]
        parts: list[str] = []
        if "rich_text" in payload:
            parts.append(rich_text_text(payload["rich_text"]))
        for cell in payload.get("cells", []):
            parts.append(rich_text_text(cell))
        for child in payload.get("children", []):
            parts.extend(block_text(child))
        return parts

    return "\n".join(
        text
        for block in build_blocks(report)
        for text in block_text(block)
        if text
    )


def _screen_text(
    report: Report,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[str, str]:
    job_id = "canonical-output-parity"
    job_runtime._JOBS.pop(job_id, None)
    monkeypatch.setattr(job_runtime, "_load_saved_report", lambda _report_id: report)
    monkeypatch.setattr(job_runtime, "_link_expired", lambda _report: False)
    session = auth_logic.create_session("admin@example.com", True)
    with TestClient(app) as client:
        html = client.get(
            f"/result/{job_id}",
            cookies={auth_constants.SESSION_COOKIE_NAME: session.token},
        ).text
    body = re.sub(r"(?s)<(script|style).*?</\1>", " ", html)
    visible = html_lib.unescape(re.sub(r"<[^>]+>", "\n", body))
    return html, visible


def _all_outputs(
    report: Report,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[str, dict[str, str]]:
    html, screen = _screen_text(report, monkeypatch)
    return html, {
        "화면": screen,
        "PDF": _pdf_text(report),
        "Notion": _notion_text(report),
    }


def test_세_출력은_표지_요약_의미장_출처를_같이_낸다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = canonical_report()
    _html, outputs = _all_outputs(report, monkeypatch)

    expected = [
        report.company,
        "분석 보고서",
        "핵심 요약",
        *[item.text for item in report.summary_items],
        *[
            f"{SECTION_BY_ID[section.cell].display_number}. "
            f"{SECTION_BY_ID[section.cell].title}"
            for section in report.sections
        ],
        "부록. 출처와 검증 상태",
        "본문의 번호가 아래 원문을 가리킵니다.",
        "원문 위치",
        "본문 사용 장",
        "완료 사업연도 연결 실적 (단위: 억원)",
        "프리미엄 아크릴 시트",
    ]
    for medium, rendered in outputs.items():
        for text in expected:
            assert normalize(text) in normalize(rendered), f"{medium}에 누락: {text}"


def test_네_출력은_canonical_순서와_prose우선_계약을_지킨다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = canonical_report()
    _html, outputs = _all_outputs(report, monkeypatch)
    headings = [
        f"{SECTION_BY_ID[section_id].display_number}. {SECTION_BY_ID[section_id].title}"
        for section_id in SECTION_BY_ID
    ]
    raw_lines = [text for section in report.sections for text, _cite in section.lines]

    for medium, rendered in outputs.items():
        normalized = normalize(rendered)
        positions = [normalized.index(normalize(heading)) for heading in headings]
        assert positions == sorted(positions), f"{medium}의 장 순서가 canonical과 다름"
        for raw in raw_lines:
            assert normalize(raw) not in normalized, f"{medium}가 근거 원문을 반복함: {raw}"
        for section in report.sections:
            for prose, _cite in section.prose_lines:
                assert normalize(prose) in normalized, f"{medium}에 prose 누락: {prose}"


def test_레거시_직무_완성도_AI수집_메타가_있는_부분본은_출력하지_않는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = _legacy_incomplete_report()

    with pytest.raises(PDFGenerationError):
        build_pdf(report)
    with pytest.raises(PublishBlockedError):
        build_blocks(report)

    _html, visible = _screen_text(report, monkeypatch)
    assert "현재 보고서 기준을 통과한 근거가 충분하지 않아" in visible
    assert _LEGACY_META not in visible


def test_화면_출처번호는_목록으로_이동하는_링크다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    html, _visible = _screen_text(canonical_report(), monkeypatch)

    report = canonical_report()
    used_numbers = {
        int(number)
        for section in report.sections
        for _text, cite in section.prose_lines
        if (number := citation_number(cite)) is not None
    }
    used_numbers.update(
        int(number)
        for section in report.sections
        for table in section.tables
        if (number := citation_number(table.cite)) is not None
    )
    for number in used_numbers:
        assert f'href="#src{number}"' in html
        assert f'title="출처 {number}번"' in html
        assert html.count(f'id="src{number}"') == 1


def test_세_출력의_출처명은_검증할_원문_URL을_보존한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = canonical_report()
    expected = {source.url for source in report.citations}

    html, _visible = _screen_text(report, monkeypatch)
    assert all(
        f'href="{url}"' in html and 'rel="noopener noreferrer"' in html
        for url in expected
    )

    reader = PdfReader(io.BytesIO(build_pdf(report)))
    pdf_urls: set[str] = set()
    for page in reader.pages:
        for reference in page.get("/Annots", []):
            annotation = reference.get_object()
            action = annotation.get("/A")
            if action is not None:
                uri = action.get_object().get("/URI")
                if uri:
                    pdf_urls.add(str(uri))
    assert pdf_urls == expected

    notion_urls: set[str] = set()

    def collect_links(block: dict[str, Any]) -> None:
        payload = block[block["type"]]
        rich_text_groups = [payload.get("rich_text", [])]
        rich_text_groups.extend(payload.get("cells", []))
        for rich_text in rich_text_groups:
            for item in rich_text:
                link = item["text"].get("link")
                if link:
                    notion_urls.add(link["url"])
        for child in payload.get("children", []):
            collect_links(child)

    for block in build_blocks(report):
        collect_links(block)
    assert notion_urls == expected
