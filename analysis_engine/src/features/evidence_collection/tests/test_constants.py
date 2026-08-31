"""수집기 필수 슬롯(COLLECTOR_SLOTS_BY_SECTION) 계약 잠금 시험.

2026-08-31 team-lead 통보 — 정본은 app/src/shared/report_evidence/policy.py
(이 워크트리 기준 커밋에는 아직 없음). 다른 담당(chapter_evidence)이 이
파일 경로·상수 이름으로 정본과의 동등성 시험을 만들 예정이라, 값이 실수로
바뀌지 않게 여기서도 리터럴로 고정해 둔다.
"""

from __future__ import annotations

from features.evidence_collection import constants as c


def test_collector_slots_by_section은_team_lead_통보값과_정확히_같다() -> None:
    assert c.COLLECTOR_SLOTS_BY_SECTION == {
        "identity": (
            "identity:corporate_identity", "identity:business_definition",
        ),
        "business_model": (
            "business_model:revenue_model", "business_model:customer_type",
            "business_model:value_exchange",
        ),
        "portfolio": ("portfolio:product_role", "portfolio:revenue_link"),
        "past_changes": ("past_changes:completed_execution",),
        "current_challenges": ("current_challenges:issue", "current_challenges:response"),
        "future_strategy": ("future_strategy:stated_plan", "future_strategy:plan_status"),
        "operations_partners": (
            "operations_partners:value_chain", "operations_partners:operating_role",
        ),
        "culture": ("culture:work_principle", "culture:verified_case"),
        "competitive_position": ("competitive_position:self_context",),
    }


def test_collector_slot_ids는_17개다() -> None:
    assert len(c.COLLECTOR_SLOT_IDS) == 17


def test_collector_slot_ids는_ALL_SLOT_IDS의_부분집합이다() -> None:
    assert c.COLLECTOR_SLOT_IDS <= c.ALL_SLOT_IDS


def test_self_context는_composer_45개_어휘에_없던_신규_슬롯이다() -> None:
    composer_slots = frozenset(
        slot_id for slots in c.CLAIM_SLOTS_BY_SECTION.values() for slot_id in slots
    )
    assert "competitive_position:self_context" not in composer_slots
    assert "competitive_position:self_context" in c.ALL_SLOT_IDS
    assert c.SLOT_SECTION_OF["competitive_position:self_context"] == "competitive_position"


def test_past_changes_historical_performance는_수집기_슬롯이_아니다() -> None:
    """Codex 구조화 실적기가 채운다 — 수집기 1차 표적에서 뺐다."""
    assert "past_changes:historical_performance" not in c.COLLECTOR_SLOT_IDS
    assert "past_changes:historical_performance" not in c.COLLECTOR_SLOTS_BY_SECTION["past_changes"]


def test_competitive_position_비교_4종은_수집기_슬롯이_아니다() -> None:
    """비교 대상·지표·근거·판단은 Codex가 채운다 — 수집기는 self_context 하나뿐."""
    excluded = {
        "competitive_position:comparison_target",
        "competitive_position:comparison_metric",
        "competitive_position:comparison_basis",
        "competitive_position:comparison_judgment",
        "competitive_position:limitation",
    }
    assert excluded.isdisjoint(c.COLLECTOR_SLOT_IDS)
    assert c.COLLECTOR_SLOTS_BY_SECTION["competitive_position"] == (
        "competitive_position:self_context",
    )


def test_source_kind_slot_scope는_전부_수집기_슬롯에서만_고른다() -> None:
    """attempts.slot_ids도 COLLECTOR_SLOT_IDS에서만 고른다(team-lead 규칙 1)."""
    for source_kind, slot_ids in c.SOURCE_KIND_SLOT_SCOPE.items():
        assert slot_ids, f"{source_kind}의 slot_ids가 비어 있습니다"
        assert set(slot_ids) <= c.COLLECTOR_SLOT_IDS, f"{source_kind}가 수집기 슬롯 밖 값을 씁니다"
