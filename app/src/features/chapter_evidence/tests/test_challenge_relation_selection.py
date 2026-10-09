"""같은 입력 예산에서 직접 사업 관계와 취소·변경을 보관하는 회귀."""
from dataclasses import replace

import pytest

from src.features.chapter_evidence.constants import (
    CHALLENGE_DIRECT_RELATION_REASONS_BY_SLOT,
    SELECTION_CHANGE_CONTEXT,
    SELECTION_RECENT_CONTEXT,
)
from src.features.chapter_evidence.select import _selection_priority, select_section_fragments
from src.features.chapter_evidence.tests.test_select import _document, _fragment

ISSUE = "current_challenges:issue"
RESPONSE = "current_challenges:response"


def _candidate(name, *, slot=ISSUE, reasons=(), score=250, text=None):
    return replace(_fragment(fragment_id=name, section_id="current_challenges",
        slot_id=slot, score_millis=score,
        text=text or "당사는 제품 공급 지연으로 납품에 어려움을 겪고 있습니다."),
        reason_codes=reasons or ("keyword_hit:" + slot,), location=name)


def _select(fragments, budget, **kwargs):
    document = _document(exact_evidence_hashes=tuple(dict.fromkeys(item.text_sha256 for item in fragments)))
    return select_section_fragments(section_id="current_challenges", company_id="corp-1",
        documents=(document,), fragments=tuple(fragments), max_chars=budget, **kwargs)


def _representatives():
    return (
        replace(_candidate("change-issue", reasons=(SELECTION_CHANGE_CONTEXT,),
            text="제품 판매 조건을 변경했습니다."), location="900-915"),
        replace(_candidate("change-response", slot=RESPONSE, reasons=(SELECTION_CHANGE_CONTEXT,),
            text="관련 대응 계획을 취소했습니다."), location="900-915"),
    )


@pytest.mark.parametrize("slot,reason", [(slot, reason)
    for slot, reasons in CHALLENGE_DIRECT_RELATION_REASONS_BY_SLOT.items() for reason in reasons])
def test_direct_relation_precedes_equal_recent_candidate_within_budget(slot, reason):
    representatives = _representatives()
    direct = _candidate("direct", slot=slot, reasons=(reason,))
    recent = _candidate("recent", slot=slot, reasons=(SELECTION_RECENT_CONTEXT,),
        text="회사의 위험회피 정책에 대한 일반적인 설명을 기록합니다.")
    budget = sum(len(item.text) for item in representatives) + max(len(direct.text), len(recent.text))
    result = _select((*representatives, recent, direct), budget)
    assert {item.fragment_id for item in result.fragments} == {"change-issue", "change-response", "direct"}
    assert sum(len(item.text) for item in result.fragments) <= budget
    assert next(item for item in result.fragments if item.fragment_id == "direct") == direct


def test_higher_score_in_recent_group_stays_before_direct_relation():
    representatives = _representatives()
    direct = _candidate("direct", reasons=("direct_pattern:business_growth_constraint",))
    recent = _candidate("recent", reasons=(SELECTION_RECENT_CONTEXT,), score=500)
    result = _select((*representatives, direct, recent), sum(len(item.text) for item in representatives) + len(direct.text))
    assert {item.fragment_id for item in result.fragments} == {"change-issue", "change-response", "recent"}


def test_latest_cancellation_is_preserved_before_old_direct_relation():
    representatives = _representatives()
    cancelled = replace(_candidate("cancelled", reasons=(SELECTION_CHANGE_CONTEXT,),
        text="이 제품의 증설 계획은 취소했습니다."), location="800-820")
    old = _candidate("old", reasons=("direct_pattern:business_growth_constraint",),
        text="이 제품의 생산 부족에 대응하여 설비 증설을 추진하고 있습니다.")
    result = _select((*representatives, old, cancelled), sum(len(item.text) for item in representatives) + max(len(old.text), len(cancelled.text)))
    assert "cancelled" in {item.fragment_id for item in result.fragments}
    assert "old" not in {item.fragment_id for item in result.fragments}


@pytest.mark.parametrize("slot,reason", [(ISSUE, "direct_pattern:business_constraint_response"),
    (RESPONSE, "direct_pattern:business_growth_constraint"), (ISSUE, "direct_pattern:unknown")])
def test_unmatched_reason_and_slot_do_not_gain_priority(slot, reason):
    representatives = _representatives()
    mismatch = _candidate("mismatch", slot=slot, reasons=(reason,))
    recent = _candidate("recent", slot=slot, reasons=(SELECTION_RECENT_CONTEXT,))
    result = _select((*representatives, mismatch, recent), sum(len(item.text) for item in representatives) + len(recent.text))
    assert "mismatch" not in {item.fragment_id for item in result.fragments}


def test_deduped_reason_does_not_borrow_another_slot_relation():
    representatives = _representatives()
    wrong_issue = _candidate("wrong-issue", reasons=("direct_pattern:business_constraint_response",))
    wrong_response = replace(wrong_issue, fragment_id="wrong-response", slot_id=RESPONSE,
        covered_slot_ids=(RESPONSE,), reason_codes=("direct_pattern:business_growth_constraint",))
    recent = _candidate("recent", reasons=(SELECTION_RECENT_CONTEXT,))
    result = _select((*representatives, wrong_issue, wrong_response, recent),
        sum(len(item.text) for item in representatives) + len(recent.text))
    assert {item.fragment_id for item in result.fragments} == {"change-issue", "change-response", "recent"}
    assert "duplicate_fragments_removed:1" in result.reason_codes


def test_no_relation_preserves_legacy_priority_and_other_sections():
    change = replace(_candidate("change", reasons=(SELECTION_CHANGE_CONTEXT,)), location="80-100")
    recent = _candidate("recent", reasons=(SELECTION_RECENT_CONTEXT,), score=250)
    high = _candidate("high", score=900)
    fragments = (high, recent, change)
    assert sorted(fragments, key=lambda item: _selection_priority(item, {})) == [change, recent, high]
    unrelated = replace(high, section_id="business_model", slot_id="business_model:revenue_model",
        covered_slot_ids=("business_model:revenue_model",),
        reason_codes=("direct_pattern:business_growth_constraint",))
    assert _selection_priority(unrelated, {}) == _selection_priority(unrelated, {}, frozenset())


def test_direct_candidate_cannot_exceed_token_or_character_budget():
    direct = _candidate("direct", reasons=("direct_pattern:business_growth_constraint",))
    assert not _select((direct,), len(direct.text) - 1).fragments
    assert not _select((direct,), len(direct.text), max_estimated_tokens=1).fragments


def test_official_web_reason_does_not_borrow_dart_relation_priority():
    representatives = _representatives()
    direct = _candidate("direct", reasons=("direct_pattern:business_growth_constraint",))
    recent = _candidate("recent", reasons=(SELECTION_RECENT_CONTEXT,))
    fragments = (*representatives, direct, recent)
    document = replace(_document(exact_evidence_hashes=tuple(dict.fromkeys(item.text_sha256 for item in fragments))),
        source_kind="official_homepage")
    result = select_section_fragments(section_id="current_challenges", company_id="corp-1",
        documents=(document,), fragments=fragments,
        max_chars=sum(len(item.text) for item in representatives) + len(recent.text))
    assert "direct" not in {item.fragment_id for item in result.fragments}
