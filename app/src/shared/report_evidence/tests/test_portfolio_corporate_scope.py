"""법인 연혁·주주 사실은 현재 제품 역할을 대신하지 않고 실제 서비스는 보존한다."""
import hashlib

import pytest

from src.shared.report_evidence.business_slot_scope import (
    business_slot_scope, business_slot_scope_problem, business_slot_quote_problem,
    product_corporate_only_problem,
)

SLOT = "portfolio:product_role"
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
SERVICE = "당사는 온라인 가맹점의 결제 승인과 정산을 연결하는 전자결제 서비스를 제공하고 있습니다."
CORPORATE = " ".join((FOUNDING, SHAREHOLDER, MERGER))


@pytest.mark.parametrize("text", [
    FOUNDING, FOUNDING_PURPOSE, SHAREHOLDER, MERGER, CORPORATE,
    "설립 초기의 사업 목적은 전자결제 분야였다.",
    "회사는 전자결제 서비스를 제공하는 것을 목적으로 설립되었습니다.",
    "당사의 주요 제품 소개: 회사는 결제대행업을 영위할 목적으로 설립되었다.",
    "회사는 2020년에 설립되었다.",
    "회사는 A기업 인수를 완료했다.",
    "주력 제품 소개: 최대주주는 A기업이다.",
    "합병으로 A법인이 회사의 포트폴리오에 편입되었다.",
])
def test_설립목적_주주_합병만으로_제품역할을_채우지_않는다(text):
    assert business_slot_scope_problem(text, SLOT) == "business_slot_scope_unsupported"
    assert product_corporate_only_problem(text)
    assert not business_slot_scope(text, SLOT).score_text.strip()
    assert business_slot_scope(text, "identity:corporate_identity").score_text == text
    assert business_slot_scope(text, "past_changes:completed_execution").score_text == text


@pytest.mark.parametrize("text", [
    SERVICE,
    "합병 이후 회사는 전자결제 서비스를 운영하고 있습니다.",
    "합병으로 센서 제품군이 회사의 포트폴리오에 편입되었다.",
    "합병 이후 회사의 주요 제품은 센서와 펌프로 구성됩니다.",
    "회사는 A기업 인수를 완료하고 데이터 솔루션을 제품군에 편입했다.",
    "합병으로 데이터 솔루션이 회사의 포트폴리오에 편입되었다.",
    FOUNDING_PURPOSE + " " + SERVICE,
    "설립 당시의 사업 목적을 확대하여 현재 전자결제 서비스를 제공한다.",
])
def test_실제_서비스와_합병의_구체제품_역할은_남긴다(text):
    assert not business_slot_scope_problem(text, SLOT)
    assert not product_corporate_only_problem(text)


def test_혼합원문은_보존하되_법인연혁_인용이_제품기능을_빌리지_못한다():
    text = CORPORATE + " " + SERVICE
    original_hash = hashlib.sha256(text.encode()).hexdigest()
    scoped = business_slot_scope(text, SLOT)
    assert not business_slot_scope_problem(text, SLOT)
    assert SERVICE.rstrip(".") in scoped.score_text
    assert business_slot_quote_problem(text, SLOT, 0, len(CORPORATE))
    begin = text.index(SERVICE)
    assert not business_slot_quote_problem(text, SLOT, begin, len(text))
    assert tuple(text[a:b] for a, b in scoped.excluded_spans) == scoped.excluded_clauses
    assert hashlib.sha256(text.encode()).hexdigest() == original_hash
