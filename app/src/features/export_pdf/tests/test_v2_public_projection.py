"""엔진 v2의 웹·PDF 공개 본문과 내부 감사 장부를 분리한다."""

from __future__ import annotations

import io
from dataclasses import replace

import pdfplumber

from src.features.composer.render import ENGINE_V2_SCHEMA_VERSION
from src.features.export_pdf import logic as pdf_logic
from src.features.pipeline.port import (
    FactRecord,
    Grade,
    Report,
    ReportSection,
    SummaryItem,
)
from src.shared.report_generation.canonical import public_content_digests


_PUBLIC_SENTENCE = "공식 자료로 확인한 공개 본문 문장이다."
_PRIVATE_SCOPE = "PDF에 나오면 안 되는 내부 감사 범위"


def _report(*, private_scope: str = _PRIVATE_SCOPE) -> Report:
    fact = FactRecord(
        fact_id="audit-fact-1",
        subject_scope=private_scope,
        relationship_or_action="PDF에 나오면 안 되는 내부 감사 관계",
        claim=_PUBLIC_SENTENCE,
        claim_type="identity_summary",
        section_owner="identity",
    )
    section = ReportSection(
        cell="identity",
        title="기업 정체성",
        lines=[(_PUBLIC_SENTENCE, "")],
        prose_lines=[(_PUBLIC_SENTENCE, "")],
        prose_paragraphs=[_PUBLIC_SENTENCE],
        display_number="1",
        fact_ids=[fact.fact_id],
    )
    return Report(
        company="가나다전자",
        job="",
        corp_type="비상장 외감",
        grade=Grade.PARTIAL,
        sections=[section],
        summary_items=[
            SummaryItem(text=f"핵심 요약 {number}이다.", section_id="identity")
            for number in range(1, 4)
        ],
        fact_records=[fact],
        generated_at="2026-09-01",
        schema_version=ENGINE_V2_SCHEMA_VERSION,
    )


def _visible_text(pdf_bytes: bytes) -> str:
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as document:
        return "\n".join(page.extract_text() or "" for page in document.pages)


def test_v2_PDF는_감사용_사실카드_생성기를_호출하지_않는다(monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("v2 PDF가 내부 감사용 FactRecord 카드를 읽었습니다")

    monkeypatch.setattr(pdf_logic, "section_content_blocks", forbidden)

    text = _visible_text(pdf_logic.build_pdf(_report()))

    assert _PUBLIC_SENTENCE in text
    assert _PRIVATE_SCOPE not in text


def test_v2_감사장부와_fact_id가_바뀌어도_공개PDF_글자는_같다():
    original = _report()
    section = original.sections[0]
    changed = replace(
        original,
        sections=[replace(section, fact_ids=["different-private-id"])],
        fact_records=[
            replace(
                original.fact_records[0],
                fact_id="different-private-id",
                subject_scope="또 다른 비공개 감사 문자열",
            )
        ],
    )

    original_digest, _ = public_content_digests(original)
    changed_digest, _ = public_content_digests(changed)
    original_text = _visible_text(pdf_logic.build_pdf(original))
    changed_text = _visible_text(pdf_logic.build_pdf(changed))

    assert original_digest == changed_digest
    assert original_text == changed_text
    assert _PRIVATE_SCOPE not in original_text
    assert "또 다른 비공개 감사 문자열" not in changed_text
