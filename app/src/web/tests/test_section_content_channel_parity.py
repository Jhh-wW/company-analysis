"""장별 구조 필드가 화면·PDF·Notion에서 같은 뜻으로 보이는지 검증한다."""

from __future__ import annotations

from collections import Counter
from dataclasses import replace
import io
import re
from typing import Any

import pytest
from fastapi.testclient import TestClient
from lxml import html as lxml_html
from pypdf import PdfReader

from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.export_notion.logic import build_blocks
from src.features.export_pdf.logic import build_pdf
from src.features.pipeline.canonical_demo import build_demo_report
from src.features.report_standard.section_content import (
    masthead_lines,
    section_content_blocks,
    summary_topic,
)
from src.web import job_runtime
from src.web import main as web_main
from src.web.tests.report_route_support import serve_legacy_report_snapshot


def _compact(text: str) -> str:
    return re.sub(r"\s+", "", text)


def _notion_text(blocks: list[dict[str, Any]]) -> str:
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
        text for block in blocks for text in block_text(block) if text
    )


def _render_web(report, monkeypatch: pytest.MonkeyPatch):
    report_id = "section-content-channel-parity"
    job_runtime._JOBS.pop(report_id, None)
    serve_legacy_report_snapshot(monkeypatch, report, report_id=report_id)
    monkeypatch.setattr(job_runtime, "_link_expired", lambda _report: False)
    session = auth_logic.create_session("admin@example.com", True)

    with TestClient(web_main.app) as client:
        response = client.get(
            f"/result/{report_id}",
            cookies={auth_constants.SESSION_COOKIE_NAME: session.token},
        )

    assert response.status_code == 200
    return lxml_html.fromstring(response.text)


def _channel_outputs(report, monkeypatch: pytest.MonkeyPatch):
    web_document = _render_web(report, monkeypatch)
    pdf_reader = PdfReader(io.BytesIO(build_pdf(report)))
    pdf_text = "\n".join(page.extract_text() or "" for page in pdf_reader.pages)
    notion_blocks = build_blocks(report)
    return web_document, notion_blocks, {
        "화면": web_document.text_content(),
        "PDF": pdf_text,
        "Notion": _notion_text(notion_blocks),
    }


def test_공통_장별필드_라벨은_화면_PDF_Notion에_같이_표시된다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = build_demo_report()
    blocks = [
        block
        for section in report.sections
        for block in section_content_blocks(report, section)
    ]
    expected_counts = Counter(
        field.label for block in blocks for field in block.fields
    )
    combined_labels = {
        "초기 신호·남은 문제",
        "시점·조건·현재 상태",
        "가치사슬 단계",
        "관계 유형",
        "비교군 선정 이유·동일 조건",
        "판정·비교 한계",
    }
    assert combined_labels <= set(expected_counts)
    combined_values = [
        field.value
        for block in blocks
        for field in block.fields
        if field.label in combined_labels
    ]
    assert combined_values
    assert all(value.strip() for value in combined_values)

    _web_document, _notion_blocks, outputs = _channel_outputs(report, monkeypatch)
    for medium, output in outputs.items():
        compact_output = _compact(output)
        for label, count in expected_counts.items():
            assert compact_output.count(_compact(label)) >= count, (
                f"{medium}의 장별 필드 라벨 누락: {label}"
            )
        for value in combined_values:
            assert _compact(value) in compact_output, (
                f"{medium}의 결합 필드 값 누락: {value}"
            )


def test_요약_출처검증_9장_복수출처가_세_채널에_함께_남는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    report = build_demo_report()
    web_document, _notion_blocks, outputs = _channel_outputs(report, monkeypatch)

    topics = [summary_topic(item.section_id) for item in report.summary_items]
    for medium, output in outputs.items():
        compact_output = _compact(output)
        for topic in topics:
            assert _compact(topic) in compact_output, f"{medium} 요약 제목 누락: {topic}"
        assert _compact("사실 검증") in compact_output
        assert _compact("사실 검증 완료") in compact_output

    competitive_section = web_document.xpath(
        "//section[@data-report-cell='competitive_position']"
    )
    assert len(competitive_section) == 1
    for number in (2, 6):
        links = competitive_section[0].xpath(f".//a[@href='#src{number}']")
        assert links, f"화면 9장에 출처 {number} 링크가 없음"

    competitive = next(
        section
        for section in report.sections
        if section.cell == "competitive_position"
    )
    for block in section_content_blocks(report, competitive):
        expected_caption = _compact(f"{block.title} [2] [6]")
        assert expected_caption in _compact(outputs["PDF"])
        assert expected_caption in _compact(outputs["Notion"])


def test_마스트헤드_문자열은_웹_PDF_Notion이_같다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """표지 다음 첫 본문 페이지 마스트헤드 두 줄이 세 채널에서 같은 문자열이다.

    ``as_of_date``를 ``generated_at``과 다른 날로 벌려 둔다. 마스트헤드
    둘째 줄은 표지 메타(``_cover_metadata``)의 「내용 생성」과 같은
    ``generated_at`` 필드를 읽어야 하는데, 두 필드가 우연히 같은 값이면
    잘못된 필드를 읽는 회귀가 생겨도 이 시험이 못 잡는다.
    """
    report = replace(
        build_demo_report(),
        generated_at="2026-08-19",
        as_of_date="2026-08-20",
    )
    company_line, meta_line = masthead_lines(report)
    assert "2026-08-20" not in meta_line  # 잘못된 필드를 읽지 않는지 자기 점검

    _web_document, _notion_blocks, outputs = _channel_outputs(report, monkeypatch)
    for medium, output in outputs.items():
        compact_output = _compact(output)
        assert _compact(meta_line) in compact_output, f"{medium}에 마스트헤드 메타줄 누락"
        assert _compact(company_line) in compact_output, f"{medium}에 회사명 누락"
