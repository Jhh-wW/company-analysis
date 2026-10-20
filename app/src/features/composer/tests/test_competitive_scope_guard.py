"""자기선언 장은 자기 인용의 실제 선언 절을 필요로 한다."""

import json
import re

import pytest

from src.features.composer.competitive_scope_guard import competitive_section_evidence_problem
from src.features.composer.port import CollectedFragment, ComposedReport, ComposedSection, ComposedSentence
from src.features.composer.tests.review_evidence_fixture import review_items
from src.features.composer.verify import verify_report


@pytest.mark.parametrize("claim", [
    "콘텐츠 매출은 고객이 콘텐츠를 제공받는 시점에 수익을 인식한다.",
    "회사는 회계 주석에서 대손충당금의 설정 방법을 설명한다.",
    "회사는 별도재무제표에 제품 판매 수익을 표시한다.",
])
def test_accounting_and_revenue_clauses_are_not_self_declaration(claim):
    # 같은 인용 뒤에 진짜 선언이 있어도 기댄 절이 다르면 빌리지 못한다.
    source = claim + " 회사는 모듈형 설계를 핵심 강점으로 설명한다."
    assert competitive_section_evidence_problem(claim, {"1": source}) == "competitive_section_evidence_offcontract"


@pytest.mark.parametrize("claim,source", [
    ("회사는 모듈형 설계를 핵심 강점으로 설명한다.", "회사는 모듈형 설계를 핵심 강점으로 설명한다."),
    ("회사는 자사 제품의 독자 기술을 강조한다.", "회사는 자사 제품의 독자 기술을 경쟁력으로 강조한다."),
    ("회사는 경쟁력 확보에 인증 절차가 제약이라고 설명한다.", "회사는 경쟁력 확보에 인증 절차가 제약이라고 설명한다."),
])
def test_actual_declared_strength_and_stated_limitations_survive(claim, source):
    assert competitive_section_evidence_problem(claim, {"1": source}) == ""


@pytest.mark.parametrize("claim", [
    "당사는 국내 시장점유율 1위를 유지하고 있다고 공식 자료에서 설명한다.",
    "회사는 국내 결제 서비스 부문 1위에 선정되었다고 밝혔다.",
    "회사는 고객 만족도 조사에서 1 위를 기록했다고 공시한다.",
    "회사는 고객 만족도 1위라는 평가를 받았다고 밝혔다.",
    "회사는 국내 시장점유율 1위임을 공시한다.",
    "회사의 국내 시장점유율 순위는 1위다.",
    "회사는 지난해 국내 시장점유율 1위였다고 밝혔다.",
])
def test_explicit_first_place_declaration_survives(claim):
    assert competitive_section_evidence_problem(claim, {"1": claim}) == ""


@pytest.mark.parametrize("claim", [
    "회사는 시장점유율 11위를 기록했다고 설명한다.",
    "회사는 시장점유율 21위를 기록했다고 설명한다.",
    "회사의 시장점유율 1위 여부는 확인할 수 없다고 설명한다.",
    "회사는 제1위원회를 설치해 심의를 진행한다.",
    "회사는 내부 규정의 1위험 항목을 설명한다.",
])
def test_other_rank_and_unconfirmed_rank_do_not_create_declaration(claim):
    assert competitive_section_evidence_problem(claim, {"1": claim}) == "competitive_section_evidence_offcontract"


@pytest.mark.parametrize("grade", ["확인", "해석"])
@pytest.mark.parametrize("verdict", ["참", "애매"])
def test_true_or_unclear_cannot_publish_wrong_ninth_topic(grade, verdict):
    bad = "콘텐츠 매출은 고객이 콘텐츠를 제공받는 시점에 수익을 인식한다."
    good = "회사는 모듈형 설계를 핵심 강점으로 설명한다."
    def ask(prompt):
        items = review_items(re.sub(r"(?m)^  등급: [^\n]+\n", "", prompt))
        return json.dumps({"판정": [{"번호": item.number, "결과": verdict if item.text == bad else "참",
                                    "장": item.section, "근거": list(item.citations)} for item in items]}, ensure_ascii=False)
    diagnostics = []
    result = verify_report(ComposedReport((ComposedSection("competitive_position", (
        ComposedSentence(bad, ("1",), grade), ComposedSentence(good, ("2",), "확인"),
    )),)), (CollectedFragment("1", "사업의 개요", bad), CollectedFragment("2", "공식자료", good)),
        None, ask, diagnostics=diagnostics)
    assert tuple(sentence.text for sentence in result.sections[0].sentences) == (good,)
    assert any(item["reason_code"] == "competitive_section_evidence_offcontract" for item in diagnostics)
