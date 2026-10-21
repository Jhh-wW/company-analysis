"""실제 공시 수익원과 대금 정산이 서로 다른 의미칸 때문에 사라지지 않는다."""
import json
import hashlib
from pathlib import Path

import pytest

from features.evidence_collection.relevance import score_fragment_slots_with_signal, portfolio_revenue_source_scores
from features.evidence_collection.business_slot_scope import business_slot_scope_problem
from features.evidence_collection.business_activity_declaration import declared_business_item
from features.evidence_collection.tests.test_streaming_collection import _collect

REVENUE = "business_model:revenue_model"
EXCHANGE = "business_model:value_exchange"
PORTFOLIO_REVENUE = "portfolio:revenue_link"
FIXTURE = json.loads((Path(__file__).parent / "fixtures/payment_service_slots.json").read_text(encoding="utf-8"))


def _slots(text):
    return {item.slot_id for item in score_fragment_slots_with_signal(text)[0]}


@pytest.mark.parametrize("fragment_id,slot", (("9", REVENUE), ("40", REVENUE), ("37", EXCHANGE), ("73", EXCHANGE)))
def test_actual_service_disclosures_support_the_written_business_role(fragment_id, slot):
    assert slot in _slots(FIXTURE["fragments"][fragment_id])


@pytest.mark.parametrize("fragment_id", ("37", "73"))
def test_explicit_current_payment_business_is_available_to_identity(fragment_id):
    text = FIXTURE["fragments"][fragment_id]
    assert declared_business_item(text) == "전자지불결제대행업"
    harvest = _collect(text)
    identities = [fragment for fragment in harvest.fragments
                  if "identity:business_definition" in fragment.covered_slot_ids]
    business = [fragment for fragment in harvest.fragments if fragment.section_id == "business_model"]
    assert identities and business
    assert any(EXCHANGE in fragment.covered_slot_ids for fragment in business)
    assert any(REVENUE in fragment.covered_slot_ids for fragment in business)
    for fragment in (*identities, *business):
        start, end = map(int, fragment.location.split("-"))
        assert text[start:end] == fragment.text
        assert fragment.text_sha256 == hashlib.sha256(fragment.text.encode()).hexdigest()


@pytest.mark.parametrize("fragment_id", ("9", "40"))
def test_revenue_source_list_does_not_invent_an_industry_definition(fragment_id):
    text = FIXTURE["fragments"][fragment_id]
    assert not declared_business_item(text)
    assert "identity:business_definition" not in _slots(text)


@pytest.mark.parametrize("row", FIXTURE["writer_sentences"])
def test_actual_writer_claim_is_not_mistaken_for_pure_recognition_policy(row):
    assert not business_slot_scope_problem(row["글"], row["주장슬롯"])


@pytest.mark.parametrize("text", (
    "회사는 데이터 서비스, 운영 용역으로부터 수익을 인식합니다.",
    "회사의 수익은 데이터 서비스와 운영 용역에서 인식됩니다.",
))
def test_declared_revenue_sources_are_not_just_recognition_timing(text):
    assert REVENUE in _slots(text)
    assert not business_slot_scope_problem(text, REVENUE)


@pytest.mark.parametrize("text", (
    "회사는 서비스로부터 수익을 인식할 예정입니다.",
    "회사는 서비스로부터 수익을 인식하지 않습니다.",
    "회사는 서비스를 제공할 때 수익을 인식합니다.",
    "PG 서비스의 경우 거래승인 시점에 수행의무가 완료됩니다.",
    "회사는 이자수익과 배당금수익을 인식합니다.",
))
def test_revenue_source_signal_does_not_promote_policy_or_accounts(text):
    assert REVENUE not in _slots(text)


@pytest.mark.parametrize("text", (
    "회사는 결제대행업으로 대금을 받은 후 가맹점에 수수료를 제외하여 지급하지 않습니다.",
    "회사는 결제대행업으로 대금을 받은 후 가맹점에 수수료를 제외하여 지급할 예정입니다.",
    "회사는 결제대행업을 영위합니다. 별도 계정에서 대금을 받은 후 가맹점에 수수료를 제외하여 지급합니다.",
    "회사는 결제대행업을 통해 가맹점으로부터 수수료를 제외하여 대금을 받습니다.",
))
def test_settlement_route_requires_one_actual_directional_transaction(text):
    assert EXCHANGE not in _slots(text)


@pytest.mark.parametrize("text,slot", (
    ("경쟁사는 데이터 서비스로부터 수익을 인식합니다.", REVENUE),
    ("한빛회사는 데이터 서비스로부터 수익을 인식합니다.", REVENUE),
    ("회사는 과거 데이터 서비스로부터 수익을 인식합니다.", REVENUE),
    ("우리 협력회사는 데이터 서비스로부터 수익을 인식합니다.", REVENUE),
    ("연결회사는 데이터 서비스로부터 수익을 인식합니다.", REVENUE),
    ("회사는 2020년에 설립되었습니다. 한빛회사는 데이터 서비스로부터 수익을 인식합니다.", REVENUE),
    ("회사가 사업에 진출하는 경우 데이터 서비스로부터 수익을 인식합니다.", REVENUE),
    ("자회사는 데이터 서비스, 운영 용역으로부터 수익을 인식합니다.", REVENUE),
    ("자회사는 결제대행업을 통하여 원천사로부터 대금을 받은 후 가맹점에 수수료를 제외하여 지급합니다.", EXCHANGE),
    ("한빛회사는 결제대행업을 통하여 원천사로부터 대금을 받은 후 가맹점에 수수료를 제외하여 지급합니다.", EXCHANGE),
    ("회사가 사업에 진출하는 경우 결제대행업을 통하여 원천사로부터 대금을 받은 후 가맹점에 수수료를 제외하여 지급합니다.", EXCHANGE),
))
def test_new_business_signals_do_not_promote_foreign_or_conditional_roles(text, slot):
    assert slot not in _slots(text)
    assert all(slot not in fragment.covered_slot_ids for fragment in _collect(text).fragments)


@pytest.mark.parametrize("text", (
    "교육을 위한 가상의 회사 사례입니다.\n\n회사는 결제대행업을 통하여 원천사로부터 대금을 받은 후 가맹점에 지급하고 있습니다.",
    "교육을 위한 가상의 회사 사례입니다.\n\n회사는 통신장비 제조업을 통하여 고객에게 장비를 공급합니다.",
    "(3) 한빛회사는 통신장비 제조업을 통하여 고객에게 장비를 공급합니다.",
))
def test_current_business_identity_cannot_borrow_fiction_or_another_company_heading(text):
    assert all("identity:business_definition" not in fragment.covered_slot_ids
               for fragment in _collect(text).fragments)


def test_separate_foreign_revenue_sentence_does_not_erase_current_own_source():
    text = "경쟁사는 데이터 서비스로부터 수익을 인식합니다. 회사는 유지보수 서비스로부터 수익을 인식합니다."
    assert REVENUE in _slots(text)
    assert not business_slot_scope_problem(text, REVENUE)
    assert any(REVENUE in fragment.covered_slot_ids for fragment in _collect(text).fragments)


def test_subsidiary_as_customer_does_not_change_the_companys_revenue_subject():
    text = "회사는 자회사에 제공하는 데이터 서비스로부터 수익을 인식합니다."
    assert REVENUE in _slots(text)


@pytest.mark.parametrize("fragment_id", ("9", "40"))
def test_current_named_service_sources_also_reach_portfolio_revenue_link(fragment_id):
    text = FIXTURE["fragments"][fragment_id]
    harvest = _collect(text)
    business = [fragment for fragment in harvest.fragments if fragment.section_id == "business_model"]
    portfolio = [fragment for fragment in harvest.fragments if fragment.section_id == "portfolio"]
    assert business and portfolio
    assert any({REVENUE, EXCHANGE} <= set(fragment.covered_slot_ids) for fragment in business)
    assert all(fragment.covered_slot_ids == (PORTFOLIO_REVENUE,) for fragment in portfolio)
    for fragment in (*business, *portfolio):
        start, end = map(int, fragment.location.split("-"))
        assert text[start:end] == fragment.text
        assert fragment.text_sha256 == hashlib.sha256(fragment.text.encode()).hexdigest()


@pytest.mark.parametrize("text", (
    "회사는 서비스로부터 수익을 인식합니다.",
    "회사는 기타 서비스 및 일반 용역으로부터 수익을 인식합니다.",
    "회사는 데이터 서비스를 제공한 시점에 수익을 인식합니다.",
    "회사는 과거 데이터 서비스로부터 수익을 인식합니다.",
    "자회사는 데이터 서비스로부터 수익을 인식합니다.",
    "한빛회사는 데이터 서비스로부터 수익을 인식합니다.",
    "우리 협력회사는 데이터 서비스로부터 수익을 인식합니다.",
    "연결회사는 데이터 서비스로부터 수익을 인식합니다.",
    "회사가 사업에 진출하는 경우 데이터 서비스로부터 수익을 인식합니다.",
    "교육을 위한 가상의 회사 사례입니다.\n\n회사는 데이터 서비스로부터 수익을 인식합니다.",
    FIXTURE["fragments"]["37"],
    FIXTURE["fragments"]["73"],
))
def test_portfolio_revenue_link_does_not_invent_a_product_from_policy_or_settlement(text):
    assert all(PORTFOLIO_REVENUE not in fragment.covered_slot_ids for fragment in _collect(text).fragments)


def test_named_current_service_source_is_a_link_signal_without_product_role():
    scores = portfolio_revenue_source_scores("회사는 데이터 서비스로부터 수익을 인식합니다.")
    assert {score.slot_id for score in scores} == {PORTFOLIO_REVENUE}


def test_hypothetical_paragraph_does_not_receive_the_new_revenue_source_signal():
    text = "교육을 위한 가상의 회사 사례입니다.\n\n회사는 데이터 서비스로부터 수익을 인식합니다."
    assert all(REVENUE not in fragment.covered_slot_ids for fragment in _collect(text).fragments)


def test_hypothetical_settlement_drops_only_new_signals_and_keeps_existing_business_signal():
    text = "교육을 위한 가상의 회사 사례입니다.\n\n회사는 결제대행업을 통하여 원천사로부터 대금을 받은 후 가맹점에 수수료를 제외하여 지급합니다."
    slots = {slot for fragment in _collect(text).fragments for slot in fragment.covered_slot_ids}
    assert EXCHANGE not in slots and "identity:business_definition" not in slots
    assert REVENUE in slots
