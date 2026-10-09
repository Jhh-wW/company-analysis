"""홈페이지 내용을 사업보고서 내용으로 바꾸는 오귀속의 공통 경계."""

from dataclasses import replace
import json
import re

import pytest

from src.features.composer.port import (
    CollectedFragment, ComposedReport, ComposedSection, ComposedSentence,
    PerformanceTable,
)
from src.features.composer.source_attribution_constants import SOURCE_ATTRIBUTION_MISMATCH
from src.features.composer.source_attribution_scope import source_attribution_problem
from src.features.composer.tests.review_evidence_fixture import review_items
from src.features.composer.verify import verify_report, verify_sentences

SOURCE = "회사는 핵심 연구시설을 중심으로 연구 역량 강화 투자를 지속하고 있습니다."
CLAIM = "사업보고서는 회사가 핵심 연구시설을 중심으로 연구 역량 강화 투자를 지속한다고 밝힌다."
WEB = CollectedFragment(
    "1", "공식웹", SOURCE, document_title="연구시설 안내",
    formal_source_kind="official_identity_verified_web_page",
)
TABLE = PerformanceTable("주요 재무", ("구분", "2024", "2025"), (("매출액", "100", "120"),), "억원")


def _reviewer(prompt):
    items = review_items(re.sub(r"(?m)^  등급: [^\n]+\n", "", prompt))
    assert items
    return json.dumps({"판정": [
        {"번호": item.number, "장": item.section, "결과": "참",
         "근거": [citation.split()[-1] for citation in item.citations]}
        for item in items
    ]}, ensure_ascii=False)


@pytest.mark.parametrize("prefix", ["사업보고서는", "사업보고서에는", "사업보고서에", "사업보고서상"])
def test_explicit_report_attribution_disagrees_with_only_website(prefix):
    assert source_attribution_problem(prefix + " 연구 투자를 지속한다고 적혀 있다.", {"1": SOURCE}, {"1": WEB}) == SOURCE_ATTRIBUTION_MISMATCH


@pytest.mark.parametrize("fragment", [
    replace(WEB, formal_source_kind="dart_business_report"),
    replace(WEB, document_title="사업보고서 (2025.12)"),
    replace(WEB, document_title="Annual Report"),
    replace(WEB, text="사업보고서는 연구 투자를 지속한다고 밝힌다."),
    replace(WEB, formal_source_kind=""),
])
def test_report_document_indirect_quote_and_unknown_legacy_are_not_false_rejected(fragment):
    assert source_attribution_problem(CLAIM, {"1": fragment.text}, {"1": fragment}) == ""


def test_unselected_business_report_does_not_rescue_website_attribution():
    report = replace(WEB, fragment_id="2", formal_source_kind="dart_business_report")
    fragments = {"1": WEB, "2": report}
    assert source_attribution_problem(CLAIM, {"1": SOURCE}, fragments) == SOURCE_ATTRIBUTION_MISMATCH
    assert source_attribution_problem(CLAIM, {"1": SOURCE, "2": SOURCE}, fragments) == ""


@pytest.mark.parametrize("claim", [
    "공식 홈페이지는 연구 투자를 지속한다고 밝힌다.",
    "회사는 사업보고서를 홈페이지에서 제공한다.",
    "회사는 연구시설 투자를 지속한다.",
])
def test_no_explicit_report_origin_claim_preserves_existing_semantic_contract(claim):
    assert source_attribution_problem(claim, {"1": SOURCE}, {"1": WEB}) == ""


@pytest.mark.parametrize("grouped", [False, True])
@pytest.mark.parametrize("table", [None, TABLE])
def test_both_review_paths_reject_wrong_origin_and_keep_correct_origin(grouped, table):
    good = CLAIM.replace("사업보고서는", "공식 홈페이지는")
    sentences = (ComposedSentence(CLAIM, ("1",), "확인"), ComposedSentence(good, ("1",), "확인"))
    draft = ComposedReport((ComposedSection("identity", sentences),))
    checked = verify_report(
        draft, (WEB,), table, _reviewer,
        allowed_fragment_ids_by_section={"identity": frozenset({"1"})} if grouped else None,
    )
    assert [s.text for s in checked.sections[0].sentences] == [good]


@pytest.mark.parametrize("table", [None, TABLE])
def test_summary_also_rejects_wrong_source_kind_even_with_shared_financial_table(table):
    kept = verify_sentences((ComposedSentence(CLAIM, ("1",), "확인"),), (WEB,), table, _reviewer)
    assert not kept
