"""조건부 회계 배부가 운영 신호와 무신호 재판정 후보로 되살아나지 않는다."""
import pytest

from features.evidence_collection.relevance import score_fragment_slots_with_signal
from features.evidence_collection.business_slot_scope import business_slot_quote_problem

POLICY = "실제조업도가 정상조업도에 미달하는 경우 생산단위당 고정제조간접원가는 정상조업도를 기초로 배부되며, 배부되지 않은 고정제조간접원가는 발생 기간의 비용으로 인식한다."
ROLE = "operations_partners:operating_role"


def test_conditional_cost_allocation_and_inventory_mixed_source_have_no_role_signal():
    for source in (POLICY, "당사는 정기 재고자산 실사를 실시한다. " + POLICY):
        scores, observed = score_fragment_slots_with_signal(source)
        assert observed
        assert not any(x.slot_id == ROLE for x in scores)
        quote = "배부되지 않은 고정제조간접원가는 발생 기간의 비용으로 인식한다"
        start = source.index(quote)
        assert business_slot_quote_problem(source, ROLE, start, start + len(quote))


def test_real_production_sentence_keeps_its_role_and_quote():
    current = "회사는 공장에서 제품을 제조하고 있다."
    source = POLICY + " " + current
    scores, observed = score_fragment_slots_with_signal(source)
    assert observed and any(x.slot_id == ROLE for x in scores)
    start = source.index(current)
    assert not business_slot_quote_problem(source, ROLE, start, len(source))


@pytest.mark.parametrize("actual", (
    "올해 실제조업도가 정상조업도에 미달했다",
    "올해 실제조업도는 정상조업도의 40%에 그쳤다",
))
@pytest.mark.parametrize("separator", (", ", " "))
def test_정책과같은절의_실제저조업도인용을_막지않는다(actual, separator):
    from features.evidence_collection.overhead_allocation_scope import overhead_allocation_scope
    from features.evidence_collection.business_slot_scope import business_slot_scope
    source = POLICY.removesuffix("인식한다.") + "인식하며" + separator + actual + "."
    assert actual in overhead_allocation_scope(source).score_text
    assert actual in business_slot_scope(source, ROLE).score_text
    start = source.index(actual)
    assert not business_slot_quote_problem(source, ROLE, start, start + len(actual))
    policy_quote = "배부되지 않은 고정제조간접원가는 발생 기간의 비용으로 인식하며"
    start = source.index(policy_quote)
    assert business_slot_quote_problem(source, ROLE, start, start + len(policy_quote))


@pytest.mark.parametrize("prediction", (
    "실제조업도가 정상조업도에 미달할 것으로 예상한다",
    "실제조업도는 정상조업도의 40%에 그칠 전망이다",
))
def test_예상저조업도만있으면_정책의운영역할신호가_되살아나지않는다(prediction):
    source = POLICY.removesuffix("인식한다.") + "인식하며, " + prediction + "."
    scores, observed = score_fragment_slots_with_signal(source)
    assert observed and not any(item.slot_id == ROLE for item in scores)
