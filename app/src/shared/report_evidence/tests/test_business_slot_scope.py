"""수집·장 선택·재분류가 같은 사업 의미칸 제약을 쓴다."""
from pathlib import Path

import pytest

from src.shared.report_evidence.business_slot_scope import business_slot_scope, business_slot_scope_problem


CUSTOMER = "business_model:customer_type"
ROLE = "operations_partners:operating_role"


@pytest.mark.parametrize("slot,policy,fact", [
    (CUSTOMER, "회사는 신용등급이 높은 거래처와만 거래하고 담보를 수취한다.",
     "주요 고객사는 의료기관이다."),
    (ROLE, "제조물책임법에 따라 제조업자는 배상책임을 지므로 보험계약을 체결한다.",
     "회사는 산업장비를 제조하고 생산한다."),
    (ROLE, "회사는 공정거래위원회의 제조위탁 조사결과에 이의를 제기했다.",
     "회사는 고객사에 제품을 공급한다."),
])
def test_관리문구만_해당칸에서_제외하고_실제사업은_남긴다(slot, policy, fact):
    assert business_slot_scope_problem(policy, slot) == "business_slot_scope_unsupported"
    assert not business_slot_scope_problem(policy + " " + fact, slot)
    assert fact.rstrip(".") in business_slot_scope(policy + " " + fact, slot).score_text
    assert business_slot_scope(policy, "current_challenges:issue").score_text == policy


@pytest.mark.parametrize("slot,text", [
    (CUSTOMER, "회사는 거래처의 신용등급 평가 서비스를 제공합니다."),
    (CUSTOMER, "회사는 기업 고객에게 신용위험 관리 서비스를 제공하고 있습니다."),
    (ROLE, "회사는 제조물책임보험 상품을 고객사에 판매한다."),
    (CUSTOMER, "회사는 신용등급을 평가하며, 은행의 고객은 기업과 개인 차주이다."),
    (CUSTOMER, "은행의 고객은 기업과 개인 차주이며 신용위험 관리정책을 운영한다."),
    (ROLE, "보험사는 제조물책임보험을 설계하고 판매한다."),
    (ROLE, "보험사는 제조물책임보험 계약을 인수한다."),
])
def test_고객에게_판매하는_금융서비스는_관리상용구가_아니다(slot, text):
    assert not business_slot_scope_problem(text, slot)


@pytest.mark.parametrize("slot,text", [
    (CUSTOMER, "회사는 고객의 신용등급을 관리하고 고객에게 서비스를 제공할 계획이다."),
    (ROLE, "제조물책임보험을 가입하고 공장에서 장비를 제조하려는 계획이다."),
])
def test_계획동사가_현재사업_면제로_쓰이지_않는다(slot, text):
    assert business_slot_scope_problem(text, slot) == "business_slot_scope_unsupported"


@pytest.mark.parametrize("slot,text", [
    (CUSTOMER, "은행은 고객의 신용위험 관리정책을 운영한다."),
    (ROLE, "보험사는 보험계약에 따른 금융위험을 관리한다."),
])
def test_금융업명칭만으로_회사자체관리정책을_본업으로_면제하지_않는다(slot, text):
    assert business_slot_scope_problem(text, slot) == "business_slot_scope_unsupported"


@pytest.mark.parametrize("slot,text", [
    (CUSTOMER, "회사는 거래처의 신용등급을 결정할 목적으로 재무정보와 거래실적을 사용하고 있습니다."),
    (ROLE, "회사는 제조물의 결함으로 인한 생명, 신체 또는 재산의 피해에 대비해 보험계약을 체결한다."),
])
def test_결정사용_관리동사와_쉼표뒤_보험문맥도_사업으로_재진입하지_않는다(slot, text):
    assert business_slot_scope_problem(text, slot) == "business_slot_scope_unsupported"


def test_관리고객절과_실제고객명_정의가_같은문장이면_정의를_남긴다():
    text = "회사는 거래처의 신용등급을 평가하며, 주요 고객사는 가람설비주식회사이다."
    assert not business_slot_scope_problem(text, CUSTOMER)
    assert "가람설비주식회사" in business_slot_scope(text, CUSTOMER).score_text


def test_별도배포_수집기와_공통판정이_일치한다():
    root = Path(__file__).resolve().parents[5]
    engine = root / "analysis_engine/src/features/evidence_collection"
    app = Path(__file__).resolve().parents[1]
    assert (engine / "business_slot_scope_constants.py").read_text(encoding="utf-8") == (
        app / "business_slot_scope_constants.py").read_text(encoding="utf-8")
    engine_logic = (engine / "business_slot_scope.py").read_text(encoding="utf-8")
    app_logic = (app / "business_slot_scope.py").read_text(encoding="utf-8")
    assert engine_logic.replace("from features.evidence_collection", "from src.shared.report_evidence") == app_logic
