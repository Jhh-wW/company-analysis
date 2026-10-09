"""현재 사업 관계의 동점 보관 순위만 우선한다."""
import pytest

from features.evidence_collection.retention import CandidateRetention
from features.evidence_collection.relevance import SlotScore
from features.evidence_collection.segment import FragmentCandidate


def _offer(retention, index, slot, score, reason, *, rank=None):
    text = f"원문 후보 {index}의 사업 내용과 변경 내용을 그대로 보존합니다."
    retention.offer_scored(index, FragmentCandidate(index * 100, index * 100 + len(text), text, ""),
        (SlotScore(slot.partition(":")[0], slot, score, (reason,)),), rank_index=rank)


@pytest.mark.parametrize("slot,reason", (
    ("current_challenges:issue", "direct_pattern:business_growth_constraint"),
    ("current_challenges:issue", "direct_pattern:business_incident_row"),
    ("current_challenges:response", "direct_pattern:business_constraint_response"),
    ("current_challenges:response", "direct_pattern:business_incident_response"),
))
def test_relation_wins_only_equal_score_top_lane(slot, reason):
    retention = CandidateRetention(frozenset((slot,)))
    _offer(retention, 0, slot, 500, "direct_keyword:일반")
    _offer(retention, 1, slot, 250, "direct_keyword:일반")
    _offer(retention, 2, slot, 250, reason)
    _offer(retention, 3, slot, 250, reason)
    assert [item.index for item in retention.pools[(slot, "top")]] == [0, 2, 3, 1]
    assert [item.index for item in retention.pools[(slot, "recent")]] == [3, 2, 1]
    assert [item.index for item in retention.pools[(slot, "change")]] == [3, 2, 1]


@pytest.mark.parametrize("slot,reason", (
    ("business_model:revenue_model", "direct_pattern:business_incident_row"),
    ("current_challenges:risk_limit", "direct_pattern:business_incident_row"),
    ("current_challenges:issue", "direct_pattern:business_incident_response"),
    ("current_challenges:response", "direct_pattern:business_growth_constraint"),
    ("current_challenges:issue", "direct_pattern:unknown_relation"),
))
def test_other_slots_and_unknown_or_wrong_reason_keep_original_order(slot, reason):
    retention = CandidateRetention(frozenset((slot,)))
    _offer(retention, 0, slot, 250, "direct_keyword:일반")
    _offer(retention, 1, slot, 250, reason)
    assert [item.index for item in retention.pools[(slot, "top")]] == [0, 1]


def test_relation_ties_use_original_rank_and_do_not_raise_scores_or_quotas():
    slot = "current_challenges:issue"
    retention = CandidateRetention(frozenset((slot,)))
    for index in range(20):
        _offer(retention, index, slot, 250, "direct_keyword:일반")
    _offer(retention, 10000, slot, 250, "direct_pattern:business_growth_constraint", rank=2)
    _offer(retention, 10001, slot, 250, "direct_pattern:business_incident_row", rank=1)
    top = retention.pools[(slot, "top")]
    assert [item.index for item in top[:2]] == [10001, 10000]
    assert len(top) <= retention.slot_count_limit
    assert all(item.scores[0].score_millis == 250 for item in top)
    assert all(item.index < 10000 for item in retention.pools[(slot, "recent")])
