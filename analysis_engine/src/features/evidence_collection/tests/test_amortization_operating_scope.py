"""상각 회계정책은 관측 상태를 유지하며 운영 역할 신호만 제외한다."""
import pytest

from features.evidence_collection.relevance import score_fragment_slots_with_signal
from features.evidence_collection.business_slot_scope import business_slot_quote_problem

ROLE = "operations_partners:operating_role"
POLICY = "당사는 무형자산으로 계상된 개발비를 관련 제품 등의 판매 또는 사용이 가능한 시점부터 5년 동안 정액법으로 상각하고 있으며, 그 상각액을 제조원가로 계상하고 있습니다."


@pytest.mark.parametrize("source", (
    POLICY,
    "개발활동 비용은 무형자산으로 처리한다. " + POLICY,
    "유형자산은 5년 정액법으로 상각하며 감가상각비를 제조원가로 계상한다.",
))
def test_상각정책은_무신호재분류로재진입하지않는다(source):
    scores, observed = score_fragment_slots_with_signal(source)
    assert observed
    assert not any(item.slot_id == ROLE for item in scores)
    quote = "제조원가로 계상"
    start = source.index(quote)
    assert business_slot_quote_problem(source, ROLE, start, start + len(quote))


@pytest.mark.parametrize("actual", (
    "회사는 공장에서 제품을 제조하고 있다.",
    "회사는 고객의 개발비를 5년 정액법으로 상각하며 회계 자문 서비스를 제공하고 있다.",
))
def test_혼합실제사업은운영지원과정확인용을유지한다(actual):
    source = POLICY + " " + actual
    scores, observed = score_fragment_slots_with_signal(source)
    actual_scores, _ = score_fragment_slots_with_signal(actual)
    assert observed
    # 회계서비스의 기존 고객·제공물 지원을 새 운영 슬롯으로 승격하지 않는다.
    assert {item.slot_id for item in actual_scores} <= {item.slot_id for item in scores}
    start = source.index(actual)
    assert not business_slot_quote_problem(source, ROLE, start, len(source))
