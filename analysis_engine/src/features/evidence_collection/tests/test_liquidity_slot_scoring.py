"""유동성 상용구가 5장 필수칸을 거짓 준비시키지 않는지 확인한다."""

import hashlib

from features.evidence_collection.relevance import score_fragment_slots_with_signal
from features.evidence_collection.tests.test_streaming_collection import _collect


POLICY = "회사는 유동성 위험을 예측하고 관리하고 있습니다. 유동성 위험을 지속적으로 관리합니다."


def _slot_ids(text: str) -> tuple[set[str], bool]:
    scores, signal = score_fragment_slots_with_signal(text)
    return {item.slot_id for item in scores}, signal


def test_pure_policy_does_not_fill_required_issue_or_response() -> None:
    slots, signal = _slot_ids(POLICY)
    assert slots == set()
    assert signal  # 직접 신호는 있었으므로 무분류 AI 재판정으로 우회하지 않는다.


def test_policy_in_table_or_semicolon_rows_cannot_supply_required_slots() -> None:
    for text in (
        "유동성 위험 | 영업자금 수요를 예측하고 관리합니다",
        "유동성 위험을 예측하고 관리합니다; 자금수요를 충당합니다",
    ):
        slots, signal = _slot_ids(text)
        assert not {"current_challenges:issue", "current_challenges:response"} & slots
        assert signal


def test_pure_policy_does_not_migrate_to_past_changes_weak_signal() -> None:
    text = "유동성 위험의 관리방법은 시장 변화에 따라 정기적으로 예측하고 관리합니다."
    slots, signal = _slot_ids(text)
    assert slots == set()
    assert signal


def test_heading_next_to_pure_policy_cannot_restore_issue_or_ai_reclassification() -> None:
    for text in ("유동성 위험\n" + POLICY, POLICY + "\n유동성 위험"):
        slots, signal = _slot_ids(text)
        assert slots == set()
        assert signal
        harvest = _collect(text)
        assert not harvest.fragments
        assert not harvest.unclassified_fragments


def test_mixed_paragraph_uses_only_nonpolicy_clause_for_challenge_slots() -> None:
    text = POLICY + "\n회사는 실제 고객 납품 위험에 대응하고 계약 조건을 변경했습니다."
    slots, signal = _slot_ids(text)
    assert signal
    assert {"current_challenges:issue", "current_challenges:response"} <= slots


def test_policy_risk_word_alone_cannot_supply_issue_in_mixed_paragraph() -> None:
    text = POLICY + "\n회사는 고객 요청에 대응하여 공급 방식을 변경했습니다."
    slots, signal = _slot_ids(text)
    assert signal
    assert "current_challenges:issue" not in slots


def test_actual_money_event_and_qualitative_pressure_keep_issue() -> None:
    for text in (
        "유동성 위험에 노출된 금융부채 잔액은 1.5억원입니다.",
        "신용등급 하락으로 유동성위험이 증가했습니다.",
    ):
        slots, signal = _slot_ids(text)
        assert signal
        assert "current_challenges:issue" in slots


def test_collector_policy_is_not_retained_for_ai_reclassification() -> None:
    harvest = _collect(POLICY)
    assert not harvest.fragments
    assert not harvest.unclassified_fragments


def test_collector_mixed_actual_fact_keeps_exact_source_hash_and_location() -> None:
    fact = "실제 신용등급 하락으로 유동성위험이 증가하여 대응 조치를 완료했습니다."
    source = POLICY + "\n" + fact
    harvest = _collect(source)
    retained = [fragment for fragment in harvest.fragments if fact in fragment.text]
    assert retained
    assert any("current_challenges:issue" in fragment.covered_slot_ids for fragment in retained)
    assert all(document.content_sha256 == hashlib.sha256(source.encode()).hexdigest()
               for document in harvest.documents)
    for fragment in retained:
        start, end = map(int, fragment.location.split("-"))
        assert source[start:end] == fragment.text
        assert fragment.text_sha256 == hashlib.sha256(fragment.text.encode()).hexdigest()
