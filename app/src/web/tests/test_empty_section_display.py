"""서버·DB 없이 실제 결과 템플릿의 빈 장 표시를 확인한다."""

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader

from src.core.citations import citation_number, split_citation_markers, split_interpretation_marker
from src.core.report_display import empty_section_notice
from src.features.pipeline.port import Grade, Report, ReportSection
from src.features.report_standard.cover_metrics import cover_metrics
from src.features.report_standard.empty_section_constants import EMPTY_SECTION_NOTICE
from src.features.report_standard.section_content import masthead_lines, section_content_blocks, source_verification_label, summary_topic
from src.features.report_standard.visualization import table_visualization
from src.shared.report_generation.constants import ENGINE_V2_SCHEMA_VERSION


def render_result(report, *, template_source=None, notice_fn=empty_section_notice):
    """실제 result.html을 렌더하되 서비스 바깥 머리말만 빈 틀로 둔다."""
    from src.core.constants import section_display_parts
    from src.features.report_standard.period_summary import PeriodSummaryItem, period_summary_from_table
    from src.features.report_standard.visualization import composition_tone

    directory = Path(__file__).parents[1] / "templates"
    overrides = {"base.html": "{% block content %}{% endblock %}"}
    if template_source is not None:
        overrides["result.html"] = template_source
    env = Environment(autoescape=True, loader=ChoiceLoader([
        DictLoader(overrides), FileSystemLoader(directory),
    ]))
    return env.get_template("result.html").render(
        report=report, job=SimpleNamespace(job_id="offline-empty-section"),
        engine_v2_schema_version=ENGINE_V2_SCHEMA_VERSION, legacy_readonly=True,
        public_citations=report.citations, empty_section_notice=notice_fn,
        citation_number=citation_number, split_citation_markers=split_citation_markers,
        split_interpretation_marker=split_interpretation_marker, interpretation_label="해석",
        cover_metrics=cover_metrics, masthead_lines=masthead_lines,
        section_content_blocks=section_content_blocks, source_verification_label=source_verification_label,
        summary_topic=summary_topic, section_display_parts=section_display_parts,
        table_visualization=table_visualization, period_summary_from_table=period_summary_from_table,
        composition_tone=composition_tone,
        sealed_period_basis_text=lambda item: PeriodSummaryItem(*item).basis_text,
    )


def _report():
    return Report(company="회귀용 회사", job="", corp_type="", grade=Grade.PARTIAL,
                  schema_version=ENGINE_V2_SCHEMA_VERSION,
                  sections=[ReportSection("future_strategy", "성장 전략", display_number="6")])


def test_displays_empty_section_notice_once_without_adding_to_existing_notice_or_body():
    report = _report()
    assert render_result(report).count(EMPTY_SECTION_NOTICE) == 1
    for text in ["정상 본문", "검사를 완료하지 못했습니다."]:
        section = replace(report.sections[0], lines=[(text, "")], prose_lines=[(text, "")], prose_paragraphs=[text])
        html = render_result(replace(report, sections=[section]))
        assert EMPTY_SECTION_NOTICE not in html
        assert html.count(text) == 1


def test_unsealed_full_web_report_does_not_add_notice():
    assert EMPTY_SECTION_NOTICE not in render_result(replace(_report(), release_mode="FULL"))


def test_displays_explicit_legacy_empty_section_limits_unchanged_once():
    report = _report()
    section = replace(report.sections[0], empty_reason="기존 한계", guidance_lines=["기존 한계", "추가 한계"])
    html = render_result(replace(report, sections=[section]))
    assert html.count("기존 한계") == 1 and html.count("추가 한계") == 1
    assert EMPTY_SECTION_NOTICE not in html


def test_sealed_full_and_v1_web_do_not_call_empty_section_notice():
    from src.features.export_pdf.tests.test_v2_public_projection import _v2_full_report
    from src.features.pipeline.canonical_demo import build_demo_report

    def forbidden(*args):
        raise AssertionError("봉인 또는 v1에서 새 안내를 만들었습니다")

    for report in [_v2_full_report(), build_demo_report()]:
        assert EMPTY_SECTION_NOTICE not in render_result(report, notice_fn=forbidden)


def test_numeric_filter_render_storage_web_preserves_notice_without_increasing_fact_count():
    from src.features.composer.constants import NOTICE_NUMERIC_BODY_WITHHELD
    from src.features.composer.port import ComposedReport, ComposedSection, ComposedSentence
    from src.features.composer.quality_projection import build_generation_quality_candidate
    from src.features.composer.render import render_report
    from src.features.composer.structured_claims import enforce_public_numeric_safety
    from src.features.storage.reports import report_from_json, report_to_json

    unsafe = ComposedSentence("2027년 매출이 25% 증가할 것으로 해석됩니다.", ("1",), "해석")
    draft = ComposedReport((ComposedSection("future_strategy", (unsafe,)),))
    safe, filtering = enforce_public_numeric_safety(draft)
    rendered = render_report("회귀용 회사", safe, (), None)
    restored = report_from_json(report_to_json(rendered))
    section = restored.sections[0]
    assert filtering.removed_section_counts == (("future_strategy", 1),)
    assert safe.sections[0].sentences == ()
    assert section.prose_paragraphs == [NOTICE_NUMERIC_BODY_WITHHELD]
    assert section.prose_lines == [(NOTICE_NUMERIC_BODY_WITHHELD, "")]
    assert section.is_filled
    assert not section.fact_ids and not restored.fact_records
    candidate = build_generation_quality_candidate(rendered, safe)
    assert candidate.sections[0].public_sentence_count == 0
    assert candidate.sections[0].notice_only
    html = render_result(restored)
    assert html.count(NOTICE_NUMERIC_BODY_WITHHELD) == 1
    assert EMPTY_SECTION_NOTICE not in html
