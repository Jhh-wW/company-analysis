"""실제 문체 표시 뒤에도 보완조사 검사가 검증된 문장과 출처를 보존한다."""

from dataclasses import replace

import pytest

from src.core import news_intake_switch
from src.core.citations import split_citation_markers
from src.features.composer.constants import CITATION_STYLE_INLINE
from src.features.composer.port import ComposedReport, ComposedSection, ComposedSentence
from src.features.composer.render import render_report
from src.features.composer.news_usage import attribution_prefix
from src.features.composer.validate import v2_validation_problems
from src.features.pipeline.port import SummaryItem
from src.features.pipeline.tests import test_supplementary_research_producer_independent as producer
from src.features.pipeline.tests.test_supplementary_research_filter import _enforce, _section
from src.features.storage.reports import report_from_json, report_to_json
from src.shared.report_claim_policy import CLAIM_SLOTS_BY_SECTION
from src.shared.report_evidence.constants import SOURCE_KIND_DART_BUSINESS_REPORT, SOURCE_KIND_NEWS


@pytest.fixture(autouse=True)
def _news_enabled(monkeypatch):
    monkeypatch.setenv(news_intake_switch.NEWS_INTAKE_ENV_NAME, "1")
    news_intake_switch._reset_process_news_intake_switch_for_tests()
    yield
    news_intake_switch._reset_process_news_intake_switch_for_tests()


def _styled_report():
    specs = (
        ("1", "identity", "가나다전자는 산업용 센서를 제조하고 있습니다."),
        ("2", "business_model", "가나다전자는 기업 고객에게 센서를 판매합니다."),
        ("3", "portfolio", "가나다전자의 핵심 제품은 산업용 센서입니다."),
    )
    fragments, sections, source_map = [], [], {}
    for fragment_id, section_id, claim in specs:
        fragment = producer._fragment(fragment_id, section_id, claim, kind=SOURCE_KIND_DART_BUSINESS_REPORT)
        source = producer._sealed_source(fragment, section_id=section_id)
        fragments.append(replace(fragment, bound_source=source))
        sections.append(ComposedSection(section_id, (ComposedSentence(
            text=claim, citations=(fragment_id,), grade="확인",
            planned_claim_slot=CLAIM_SLOTS_BY_SECTION[section_id][0],
            verification_state="verified",
        ),)))
        source_map[section_id] = source, claim
    report = render_report(
        producer._COMPANY, ComposedReport(tuple(sections)), tuple(fragments), None,
        corp_type="주식회사", as_of_date=producer._AS_OF, company_id=producer._COMPANY_ID,
        citation_style=CITATION_STYLE_INLINE,
    )
    report = replace(report, summary_items=[
        SummaryItem(
            text="".join(part.text for part in split_citation_markers(section.prose_lines[0][0])).strip(),
            section_id=section.cell, fact_ids=list(section.fact_ids),
        )
        for section in report.sections if section.fact_ids
    ])
    return report, producer._official_evidence(source_map)


def test_styled_producer_preserves_all_verified_claims_and_citations_after_final_filter():
    report, evidence = _styled_report()
    original_facts, original_sources = report.fact_records, report.citations
    assert "제조하고 있다." in _section(report, "identity").prose_lines[0][0]
    assert "제조하고 있습니다." in original_facts[0].claim
    assert not v2_validation_problems(report)

    decision = producer._assess(report, evidence)
    assert decision.qualified_section_ids == ("identity", "business_model", "portfolio")
    result = _enforce(report, evidence)
    assert result.report.sections == report.sections
    assert result.report.fact_records == original_facts
    assert result.report.citations == original_sources
    restored = report_from_json(report_to_json(result.report))
    assert restored.sections == report.sections
    assert not v2_validation_problems(restored)


@pytest.mark.parametrize("replacement", [
    "가나다전자의 자회사는 산업용 센서를 제조하고 있다.",
    "가나다전자는 산업용 센서를 제조하지 않는다.",
    "가나다전자는 산업용 센서를 제조할 계획이다.",
    "가나다전자는 산업용 센서를 2개 제조하고 있다.",
])
def test_changed_subject_negation_plan_or_number_is_not_treated_as_style(replacement):
    report, evidence = _styled_report()
    section = _section(report, "identity")
    changed = replacement + " [1]"
    tampered = replace(report, sections=[
        replace(section, lines=[(changed, "")], prose_lines=[(changed, "")], prose_paragraphs=[changed])
        if item.cell == section.cell else item for item in report.sections
    ])
    assert "identity" not in producer._assess(tampered, evidence).qualified_section_ids
    result = _enforce(tampered, evidence)
    assert not _section(result.report, "identity").prose_lines


def test_styled_claim_still_requires_its_own_citation():
    report, evidence = _styled_report()
    section = _section(report, "identity")
    changed = section.prose_lines[0][0].replace("[1]", "[2]")
    tampered = replace(report, sections=[
        replace(section, lines=[(changed, "")], prose_lines=[(changed, "")], prose_paragraphs=[changed])
        if item.cell == section.cell else item for item in report.sections
    ])
    result = _enforce(tampered, evidence)
    assert not _section(result.report, "identity").prose_lines
    assert _section(result.report, "business_model").prose_lines == _section(report, "business_model").prose_lines


@pytest.mark.parametrize("verbatim", [True, False])
def test_news_display_keeps_exact_quotes_and_normalizes_verified_paraphrases(verbatim):
    report, evidence = _styled_report()
    raw = "가나다전자의 핵심 제품은 산업용 센서입니다."
    claim = raw if verbatim else "가나다전자의 핵심 제품은 센서입니다."
    fragment = producer._fragment("4", "portfolio", raw, kind=SOURCE_KIND_NEWS)
    fragment = replace(fragment, bound_source=producer._sealed_source(fragment, section_id="portfolio"))
    news_report = render_report(
        producer._COMPANY,
        ComposedReport((ComposedSection("portfolio", (ComposedSentence(
            text=attribution_prefix(fragment) + claim, citations=("4",), grade="확인",
            verification_state="verified", planned_claim_slot=CLAIM_SLOTS_BY_SECTION["portfolio"][0],
        ),)),)), (fragment,), None, corp_type="주식회사",
        company_id=producer._COMPANY_ID, as_of_date=producer._AS_OF,
    )
    news_section = news_report.sections[0]
    assert ("센서입니다." if verbatim else "센서이다.") in news_section.prose_lines[0][0]
    combined = replace(report, sections=[
        replace(section, lines=section.lines + news_section.lines,
                prose_lines=section.prose_lines + news_section.prose_lines,
                prose_paragraphs=section.prose_paragraphs + news_section.prose_paragraphs,
                fact_ids=section.fact_ids + news_section.fact_ids)
        if section.cell == "portfolio" else section for section in report.sections
    ], fact_records=report.fact_records + news_report.fact_records,
       citations=report.citations + news_report.citations)
    result = _enforce(combined, evidence)
    assert result.report.sections == combined.sections
    assert result.report.fact_records == combined.fact_records
    assert not v2_validation_problems(result.report)

    if verbatim:
        original = news_section.prose_lines[0][0]
        changed = original.replace("센서입니다.", "센서이다.")
        tampered = replace(combined, sections=[
            replace(section,
                    lines=[(text.replace(original, changed), cite) for text, cite in section.lines],
                    prose_lines=[(text.replace(original, changed), cite) for text, cite in section.prose_lines],
                    prose_paragraphs=[text.replace(original, changed) for text in section.prose_paragraphs])
            if section.cell == "portfolio" else section for section in combined.sections
        ])
        filtered = _enforce(tampered, evidence)
        assert not any(text == changed for text, _cite in _section(filtered.report, "portfolio").prose_lines)
        # 변조 표시를 없애도 원래 사실·부록의 참조를 임의로 버려 출고를 허용하지 않는다.
        assert v2_validation_problems(filtered.report)
