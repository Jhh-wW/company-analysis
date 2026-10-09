"""지역 미확인 관찰의 공개 표시·출처·저장 계약을 확인한다."""

from dataclasses import replace

import pytest

from src.features.composer.quality_projection import build_generation_quality_candidate
from src.features.composer.render import render_report
from src.features.composer.tests.test_official_industry_context import _official_materials, _sha
from src.features.composer.tests.test_industry_context import _materials
from src.features.report_standard.public_projection import build_public_projection
from src.features.storage.reports import report_from_dict, report_to_dict
from src.shared.business_challenge_context import (
    IndustryContextDisplay, industry_context_from_dict, industry_context_to_dict,
)
from src.shared.report_generation.public_projection import (
    public_report_projection_from_dict, public_report_projection_to_dict,
)


def _render_unspecified():
    company, fragments, anchor, problem, composed = _official_materials()
    text = problem.exact_text.replace("국내 ", "")
    document_hash = _sha(fragments[0].text + text)
    fragments = (
        replace(fragments[0], document_content_sha256=document_hash),
        replace(fragments[1], text=text, document_content_sha256=document_hash),
    )
    anchor = replace(anchor, document_content_sha256=document_hash)
    problem = replace(
        problem, geography="unspecified", geography_detail="", geography_evidence="",
        exact_text=text, text_sha256=_sha(text), document_content_sha256=document_hash,
        assessment_quote=problem.assessment_quote.replace("국내 ", ""),
    )
    report = render_report(
        company, composed, fragments, None, company_id=anchor.company_id,
        as_of_date="2026-09-30", industry_anchors=(anchor,), industry_problems=(problem,),
    )
    return report, composed


def test_미확인_표시와_공식기간_두출처_해석_한계를_함께_싣는다():
    report, composed = _render_unspecified()
    section = report.sections[4]
    context, = section.industry_contexts
    display = IndustryContextDisplay(context, 11, 12)
    assert display.lines[0] == "회사 공식 자료의 기준: 2025 · 2026-09-01 공표"
    assert display.lines[1].startswith("산업 관찰(지역 범위 미확인):")
    assert "[11] [12] — 해석" in display.lines[-2]
    assert "직접 피해" in display.lines[-1]
    assert "국내·세계 산업 문제로 단정하지 않는다" in display.lines[-1]
    assert not section.is_filled and not section.fact_ids and not report.fact_records
    quality = build_generation_quality_candidate(report, composed)
    assert all(not source.counts_toward_document_floor for source in quality.sources)
    assert industry_context_from_dict(industry_context_to_dict(context)) == context
    restored = report_from_dict(report_to_dict(report))
    assert restored.sections[4].industry_contexts == section.industry_contexts
    projection = build_public_projection(report)
    assert public_report_projection_from_dict(public_report_projection_to_dict(projection)) == projection


def test_뉴스_자료에는_지역미확인_우회를_허용하지_않는다():
    _, _, _, news, _ = _materials()
    with pytest.raises(ValueError, match="지역 범위"):
        replace(news, geography="unspecified", geography_detail="", geography_evidence="")


@pytest.mark.parametrize("changes", [
    {"source_kind": ""}, {"identity_binding": ""},
    {"observation_period": ""}, {"geography_evidence": "국내"},
])
def test_미확인_자료도_공식_검수와_원문_결속을_요구한다(changes):
    report, _ = _render_unspecified()
    problem = report.sections[4].industry_contexts[0].problem
    with pytest.raises(ValueError):
        replace(problem, **changes)
