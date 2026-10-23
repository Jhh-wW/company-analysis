"""기업 정체성 첫 사업 설명의 독립 문단·인용과 사전 봉인 일치를 검증한다."""

from dataclasses import replace

import pytest

from src.features.composer.constants import SECTION_IDS
from src.features.composer.port import (
    CollectedFragment, ComposedReport, ComposedSection, ComposedSentence,
)
from src.features.composer.public_manifest import build_public_structure_seal
from src.features.composer.render import render_report
from src.shared.report_generation.canonical import public_content_digests
from src.shared.report_quality.source_identity import document_identity_from_parts


COMPANY = "가나다전자"
BUSINESS = "가나다전자는 반도체 검사장비를 제조하는 기업이다."
FOLLOWUP = "회사는 검사장비를 기업 고객에게 공급한다."
LAST = "회사는 검사장비 유지보수 서비스를 제공한다."


def _sentence(text=BUSINESS, *, slot="identity:business_definition"):
    return ComposedSentence(
        text=text, citations=("1",), grade="확인",
        planned_claim_slot=slot, verification_state="verified",
    )


def _render_and_seal(sentences, *, section_id="identity", citation_style="merged"):
    composed = ComposedReport(tuple(
        ComposedSection(sid, tuple(sentences) if sid == section_id else ())
        for sid in SECTION_IDS
    ))
    url = "https://identity.example/company"
    fragments = (CollectedFragment(
        fragment_id="1", kind="회사 공식 자료",
        text=" ".join(sentence.text for sentence in sentences), source_url=url,
        document_identity=document_identity_from_parts(url=url),
    ),)
    metadata = {
        "company_id": "00123456", "corp_type": "기업",
        "generated_at": "2026-10-23T00:00:00+09:00",
        "as_of_date": "2026-10-23", "analysis_period": "2025~2026",
        "latest_performance_period": "2025", "citation_style": citation_style,
    }
    seal = build_public_structure_seal(
        composed, fragments, None,
        filing_meta=None, composition_tables=(), table_presentation="table",
        evidence_generation_sha256="a" * 64,
        evidence_packet_sha256s=tuple((sid, "b" * 64) for sid in SECTION_IDS),
        company_name=COMPANY, **metadata,
    )
    rendered = render_report(
        COMPANY, composed, fragments, None, public_structure_seal=seal, **metadata,
    )
    # section digest는 문장·문단·인용·번호 전체를 포함한다. 독립 canonicalizer와
    # 실제 renderer를 각각 거쳐 만들어진 결과를 비교한다.
    assert public_content_digests(rendered)[1] == seal.section_sha256s
    section = next(section for section in rendered.sections if section.cell == section_id)
    return section


@pytest.mark.parametrize("citation_style", ("merged", "inline"))
def test_첫사업설명은_같은출처의_후속확인문장과_독립된_한항목이다(citation_style):
    section = _render_and_seal(
        (_sentence(), _sentence(FOLLOWUP), _sentence(LAST)),
        citation_style=citation_style,
    )

    assert len(section.prose_lines) == 3
    assert len(section.prose_paragraphs) == 2
    assert section.prose_paragraphs[0] == f"{BUSINESS} [1]"
    assert section.prose_paragraphs[1] == " ".join(text for text, _cite in section.prose_lines[1:])
    assert section.prose_lines[-1][0] == f"{LAST} [1]"
    if citation_style == "merged":
        assert section.prose_lines[1][0] == FOLLOWUP


def test_첫사업설명은_뒤에_해석이있어도_자체문단과_인용을_유지한다():
    interpretation = replace(_sentence(FOLLOWUP), grade="해석")
    section = _render_and_seal((_sentence(), interpretation, _sentence(LAST)))

    assert section.prose_paragraphs[0] == f"{BUSINESS} [1]"
    assert len(section.prose_paragraphs) == 2
    assert FOLLOWUP in section.prose_paragraphs[1]
    assert "해석" in section.prose_paragraphs[1]


@pytest.mark.parametrize("section_id,slot", (
    ("business_model", "business_model:sales_channel"),
    ("portfolio", "portfolio:product_role"),
))
def test_다른장은_같은출처의_확인문장을_기존대로_한묶음으로_유지한다(section_id, slot):
    section = _render_and_seal(
        (_sentence(slot=slot), _sentence(FOLLOWUP, slot=slot)), section_id=section_id,
    )

    assert section.prose_lines == [(BUSINESS, ""), (f"{FOLLOWUP} [1]", "")]
    assert section.prose_paragraphs == [f"{BUSINESS} {FOLLOWUP} [1]"]


@pytest.mark.parametrize("first", (
    replace(_sentence(), verification_state="unverified"),
    _sentence(slot="identity:corporate_identity"),
    _sentence("회사는 반도체 검사장비 제조를 목적으로 설립된 회사다."),
    _sentence("이후 회사는 반도체 검사장비를 제조한다."),
    _sentence("이는 반도체 검사장비를 제조하는 기업이다."),
    _sentence("회사의 본점은 서울특별시 강남구 테헤란로 131에 있다."),
))
def test_첫문장이_사업요약에_부적합하면_문단과_인용_묶음을_유지한다(first):
    section = _render_and_seal((first, _sentence(FOLLOWUP)))

    assert section.prose_lines == [(first.text, ""), (f"{FOLLOWUP} [1]", "")]
    assert section.prose_paragraphs == [f"{first.text} {FOLLOWUP} [1]"]


def test_뒤의_사업설명이_첫문장처럼_독립항목으로_잘못_분리되지않는다():
    first = _sentence("회사는 반도체 검사장비 사업부문을 합병했다.", slot="identity:corporate_identity")
    section = _render_and_seal((first, _sentence(), _sentence(FOLLOWUP)))

    assert len(section.prose_paragraphs) == 1
    assert section.prose_lines[0][0] == first.text
    assert section.prose_lines[1][0] == BUSINESS
    assert section.prose_lines[2][0] == f"{FOLLOWUP} [1]"
