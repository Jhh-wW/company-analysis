"""원조각 하나의 복수 사업도 법인·원문 결속과 기존 조사 상한을 지킨다."""
from dataclasses import replace
import json
import pytest

from src.features.pipeline.business_activity_anchors import build_business_activity_anchors
from src.features.pipeline.tests.test_business_activity_anchors import PROFILE, _evidence, _sha


TEXT = (
    "당기 중 당사가 제공한 서비스 내역입니다. ① 당기\n\n"
    "서비스종류 | 당기매출액 ; 회계 자문 서비스 | 30 ; 교육 서비스 | 20"
)


def test_multiple_items_keep_one_exact_source_and_distinct_stable_ids():
    evidence = _evidence((TEXT,))
    original = evidence.candidates[2].fragments[0]
    first, second = build_business_activity_anchors(evidence, profile=PROFILE)
    assert [first.business_item, second.business_item] == ["회계 자문 서비스", "교육 서비스"]
    assert first.anchor_id == original.fragment_id
    assert second.anchor_id != first.anchor_id
    assert (first, second) == build_business_activity_anchors(evidence, profile=PROFILE)
    for anchor in (first, second):
        assert anchor.exact_text == original.text
        assert anchor.location == original.location
        assert anchor.text_sha256 == original.text_sha256
        assert anchor.document_id == original.document_id
        assert anchor.company_id == original.company_id
        assert anchor.source_id == ""
    assert replace(second, anchor_id=first.anchor_id, business_item=first.business_item) == first


def test_additional_id_changes_when_its_original_source_changes():
    original = build_business_activity_anchors(_evidence((TEXT,)), profile=PROFILE)
    changed = build_business_activity_anchors(_evidence((TEXT.replace("| 20", "| 21"),)), profile=PROFILE)
    assert original[0].anchor_id == changed[0].anchor_id
    assert original[1].anchor_id != changed[1].anchor_id


def test_multiple_rows_keep_three_anchor_cap_and_deduplicate_items():
    text = TEXT + " ; 번역 서비스 | 10 ; 분석 서비스 | 20 ; 교육 서비스 | 5"
    anchors = build_business_activity_anchors(_evidence((text, TEXT)), profile=PROFILE)
    assert [anchor.business_item for anchor in anchors] == ["회계 자문 서비스", "교육 서비스", "번역 서비스"]
    assert len({anchor.anchor_id for anchor in anchors}) == 3


def test_explicit_existing_business_stays_ahead_of_additional_table_rows():
    evidence = _evidence((TEXT, "당사는 정밀부품을 제조합니다."))
    anchors = build_business_activity_anchors(evidence, profile=PROFILE)
    assert [anchor.business_item for anchor in anchors] == ["정밀부품", "회계 자문 서비스", "교육 서비스"]


@pytest.mark.parametrize("actor,owner,status,count", (
    ("가온기업", "가온기업", "", 2),
    ("다른기업", "가온기업", "", 0),
    ("가온기업", "다른기업", "", 0),
    ("가온기업", "가온기업", "제공 예정", 0),
))
def test_multiple_table_items_keep_existing_actor_context_gate(actor, owner, status, count):
    evidence = _evidence((TEXT,))
    candidate = evidence.candidates[2]
    row = " | ".join(filter(None, (actor, "회계 자문 서비스", status)))
    fragment = replace(candidate.fragments[0], source_context_json=json.dumps({
        "version": "source-context-v1", "origin": "table_row", "text": row,
        "location": f"0-{len(row)}", "text_sha256": _sha(row),
        "actor": actor, "document_actor": owner, "status": status,
        "document_actor_location": f"0-{len(owner)}", "document_actor_sha256": _sha(owner),
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
    changed = replace(evidence, candidates=tuple(
        replace(value, fragments=(fragment,)) if value.section_id == candidate.section_id else value
        for value in evidence.candidates
    ))
    assert len(build_business_activity_anchors(changed, profile=PROFILE)) == count
