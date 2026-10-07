"""현재 회사사업 선언만 기존 회사정의 transport로 보관한다."""

import pytest
from dataclasses import replace

from features.evidence_collection.relevance import score_fragment_slots_with_signal
from features.evidence_collection.collect import collect_dart_evidence
from features.evidence_collection.filing_select import RawFilingRow
from features.evidence_collection.tests.test_collect import _fetcher, _NOW


def test_현사업구성의_새신호는_회사정의만이다():
    text = "당사의 사업은 정밀부품 사업과 산업장비 사업으로 구성되어 있습니다."
    scores, signal = score_fragment_slots_with_signal(text, "사업의 개요")
    assert signal is True
    assert {score.slot_id for score in scores} == {"identity:business_definition"}
    assert "direct_pattern:current_company_business_declaration" in scores[0].reason_codes


@pytest.mark.parametrize("text", (
    "미디어 사업은 광고 사업과 콘텐츠 판매 사업으로 구성되어 있습니다.",
    "당사의 사업 목적은 정밀부품 사업과 장비 사업으로 구성되어 있습니다.",
    "당사의 사업은 향후 정밀부품 사업과 장비 사업으로 구성되어 있습니다.",
    "당사의 사업은 고객사의 정밀부품 사업과 장비 사업으로 구성되어 있습니다.",
    "당사의 사업은 회계 관리 사업과 내부 관리 사업으로 구성되어 있습니다.",
    "당사의 사업은 인수하는 경우 데이터 분석 사업과 장비 임대 사업으로 구성되어 있습니다.",
    "당사의 사업은 2020년 당시 데이터 분석 사업과 장비 임대 사업으로 구성되어 있습니다.",
    "당사의 사업은 종속기업의 데이터 분석 사업과 장비 임대 사업으로 구성되어 있습니다.",
    "당사의 사업은 인수 완료를 전제로 한 데이터 분석 사업과 장비 임대 사업으로 구성되어 있습니다.",
    "당사의 사업은 회계 처리 사업과 세무 자문 사업으로 구성되어 있습니다.",
))
def test_기존기타신호를_새회사사업선언_양성으로_만들지_않는다(text):
    scores, _ = score_fragment_slots_with_signal(text)
    assert all("direct_pattern:current_company_business_declaration" not in score.reason_codes
               for score in scores)


def test_새회사정의가_자료종류의_허용칸을_넘지_않는다():
    scores, _ = score_fragment_slots_with_signal(
        "당사의 사업은 정밀부품 사업과 장비 사업으로 구성되어 있습니다.",
        allowed_slot_ids=frozenset({"current_challenges:issue"}))
    assert not scores


@pytest.mark.parametrize("text", (
    "당사의 사업은 고객 대상 회계 처리 사업과 세무 자문 사업으로 구성되어 있습니다.",
    "당사의 사업은 종속기업 대상 회계 자문 사업과 기업 교육 사업으로 구성되어 있습니다.",
    "당사의 사업은 2020년에 시작한 데이터 분석 사업과 장비 임대 사업으로 구성되어 있습니다.",
))
def test_현재_외부서비스와_과거개시_현사업은_회사정의만_보관한다(text):
    scores, _ = score_fragment_slots_with_signal(text)
    declarations = [score for score in scores
                    if "direct_pattern:current_company_business_declaration" in score.reason_codes]
    assert [score.slot_id for score in declarations] == ["identity:business_definition"]


@pytest.mark.parametrize("actor", ("(주)가온기업", "(주)다른기업"))
def test_다른회사_표제의_선언은_대상회사정의로_수집되지_않는다(actor):
    text = ("(주)가온기업\nII. 사업의 내용\n[부품사업부문]\n영업개황 " + actor + "\n\n"
            "당사의 사업은 정밀부품 사업과 산업장비 사업으로 구성되어 있습니다.")
    row = RawFilingRow("20250315000001", "사업보고서 (2025.03)", "20250315", "00126380")
    fetcher = _fetcher("A", row, text)
    fetcher.document_responses_by_rcept_no[row.rcept_no] = replace(
        fetcher.document_responses_by_rcept_no[row.rcept_no], corp_code="00126380",
        document_actor="(주)가온기업")
    harvest = collect_dart_evidence(fetcher, "00126380", now=_NOW)
    declarations = [fragment for fragment in harvest.fragments
                    if "direct_pattern:current_company_business_declaration" in fragment.reason_codes]
    assert bool(declarations) == (actor == "(주)가온기업")
    for fragment in declarations:
        assert fragment.covered_slot_ids == ("identity:business_definition",)
        start, end = map(int, fragment.location.split("-"))
        assert text[start:end] == fragment.text
