"""한 원조각의 두 사업 항목도 공개 출처·봉인 경계를 각각 유지한다."""
from dataclasses import replace

import pytest

from src.features.composer.industry_context import select_industry_context_for_fragments
from src.features.composer.render import render_report
from src.features.composer.tests.test_industry_context import _materials, _sha
from src.features.storage.reports import report_from_dict, report_to_dict


def _multiple_materials():
    company, fragment, anchor, problem, composed = _materials()
    text = ("당기 중 당사가 판매한 제품의 내역입니다. ① 당기\n\n"
            "제품종류 | 당기매출액 ; 산업용 센서 | 30 ; 분석 장비 | 20")
    fragment = replace(fragment, text=text, document_content_sha256=_sha(text))
    first = replace(anchor, exact_text=text, text_sha256=_sha(text), document_content_sha256=_sha(text))
    second = replace(first, anchor_id="additional-table-item", business_item="분석 장비")
    article = "세계 분석 장비 산업에서는 운송 지연이 문제로 남아 있다."
    other = replace(problem, evidence_id="problem-2", business_anchor_id=second.anchor_id,
                    document_id="article-2", source_url="https://news.example/article-2",
                    exact_text=article, text_sha256=_sha(article), industry="분석 장비",
                    problem="운송 지연", geography="global", geography_detail="세계",
                    geography_evidence="세계", applicability_quote="분석 장비 산업",
                    document_content_sha256=_sha(article))
    return company, fragment, (first, second), (problem, other), composed


def test_two_business_items_use_the_same_verified_original_and_keep_two_contexts():
    company, fragment, anchors, problems, composed = _multiple_materials()
    selected = select_industry_context_for_fragments(
        anchors=anchors, problems=problems, original_fragments=(fragment,),
        selected_fragments=(fragment,), company_id="00000001",
    )
    assert selected == (anchors, problems, 0)
    report = render_report(company, composed, (fragment,), None, company_id="00000001",
                           as_of_date="2026-09-30", industry_anchors=anchors, industry_problems=problems)
    contexts = report.sections[4].industry_contexts
    assert len(contexts) == 2
    assert len({value.anchor.source_id for value in contexts}) == 1
    assert len({value.problem.source_id for value in contexts}) == 2
    assert not report.fact_records
    assert report_from_dict(report_to_dict(report)).sections[4].industry_contexts == contexts


def test_missing_selected_original_excludes_both_contexts_without_inventing_facts():
    _, fragment, anchors, problems, _ = _multiple_materials()
    assert select_industry_context_for_fragments(
        anchors=anchors, problems=problems, original_fragments=(fragment,),
        selected_fragments=(), company_id="00000001",
    ) == ((), (), 2)


def test_second_item_cannot_borrow_a_changed_original_source():
    _, fragment, anchors, problems, _ = _multiple_materials()
    changed = replace(fragment, text=fragment.text.replace("| 20", "| 21"))
    with pytest.raises(ValueError):
        select_industry_context_for_fragments(
            anchors=anchors, problems=problems, original_fragments=(fragment,),
            selected_fragments=(changed,), company_id="00000001",
        )
