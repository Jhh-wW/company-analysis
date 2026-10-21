"""실제 문제 PDF의 3장 법인 사실을 제품 설명으로 승인하지 않는 회귀."""
import json

import pytest

from src.features.composer.business_relation_scope import business_relation_scope_problem
from src.features.composer.verify import _apply_grounding

FOUNDING = "토스페이먼츠 주식회사는 전자지급결제대행업을 영위할 목적으로 설립된 회사다."
FOUNDING_PURPOSE = (
    "토스페이먼츠 주식회사는 전자지급결제대행업을 영위할 목적으로 설립된 회사로, "
    "설립 당시의 사업 목적이 전자결제 분야에 있음이 공시 문서에 명시되어 있다."
)
SHAREHOLDER = (
    "ANT INTERNATIONAL TECHNOLOGIES(SINGAPORE) HOLDING PTE. LTD. 역시 "
    "상당한 지분을 보유한 주요 주주로 이름을 올리고 있다."
)
MERGER = (
    "회사는 2023년 10월 31일을 간주취득일로 하여 블리츠패스트 주식회사와의 합병을 완료하였으며, "
    "이 합병은 지배회사 비바리퍼블리카의 또 다른 종속기업이었던 블리츠패스트를 흡수하는 방식으로 이루어졌다."
)
CORPORATE = " ".join((FOUNDING, SHAREHOLDER, MERGER))
# 2026-10-20 공식웹 추출 스냅샷의 실제 문장. 기존 PDF의 생성 입력이었다는 뜻은 아니다.
BRANDPAY = (
    "브랜드페이는 누구나 간편결제를 쉽게 만들 수 있도록 돕는 솔루션이에요. "
    "고객의 첫 결제수단 등록부터 결제까지 구현할 수 있고, 브랜드에 맞는 맞춤형 UI를 적용할 수 있어요."
)
PAYOUT = (
    "지급대행 서비스 오픈마켓 형태의 쇼핑몰을 대상으로 전자금융사업자인 토스페이먼츠가 "
    "쇼핑몰 입점 판매자에게 대금지급을 대행해주는 서비스입니다."
)


def _verdict(text, source, slot):
    problems = {}
    raw = json.dumps({"판정": [{"번호": 1, "결과": "참", "근거": ["1"]}]}, ensure_ascii=False)
    result = _apply_grounding(
        raw, {1: "참"}, {1: (text, {"1": source})},
        diagnostic_contexts={1: ("portfolio", "본문", text)},
        claim_slots_by_number={1: slot}, grounding_problems=problems,
    )
    return result[1], problems


@pytest.mark.parametrize("slot", ["", "portfolio:product_role"])
@pytest.mark.parametrize("text", [FOUNDING, SHAREHOLDER, MERGER, CORPORATE])
def test_법인사실은_정확한_자기인용이_있어도_제품본문이_아니다(text, slot):
    verdict, problems = _verdict(text, CORPORATE, slot)
    assert verdict != "참"
    assert problems[1] == "scope_condition_unbound"


@pytest.mark.parametrize("slot", ["", "portfolio:product_role"])
def test_법인연혁_원문으로_새_제품기능을_만들지_못한다(slot):
    verdict, problems = _verdict("회사는 가맹점의 결제와 정산을 지원하는 서비스를 제공한다.", CORPORATE, slot)
    assert verdict != "참"
    assert problems[1] == "scope_condition_unbound"


@pytest.mark.parametrize("slot", ["", "portfolio:product_role"])
def test_설립목적의_공시확인_덧말도_제품본문으로_승인하지_않는다(slot):
    verdict, problems = _verdict(FOUNDING_PURPOSE, FOUNDING_PURPOSE, slot)
    assert verdict != "참"
    assert problems[1] == "scope_condition_unbound"
    assert not business_relation_scope_problem(
        FOUNDING_PURPOSE, {"1": FOUNDING_PURPOSE}, section_id="identity",
        claim_slot="identity:corporate_identity",
    )


@pytest.mark.parametrize("text", [BRANDPAY, PAYOUT])
@pytest.mark.parametrize("slot", ["", "portfolio:product_role"])
def test_현재웹에서_확인한_구어체_서비스기능은_법인사실과_혼동하지_않는다(text, slot):
    verdict, problems = _verdict(text, CORPORATE + " " + text, slot)
    assert verdict == "참"
    assert not problems


def test_합병사실의_과거변화장_소유권은_바꾸지_않는다():
    assert not business_relation_scope_problem(
        MERGER, {"1": CORPORATE}, section_id="past_changes",
        claim_slot="past_changes:completed_execution",
    )
