"""작가가 주장슬롯의 «장 ID:» 앞부분을 떼고 적어도 그 장의 칸으로만 복원한다.

실측(2026-09-23): 빈 장 복구 재요청 답의 주장슬롯이 전부 장 ID를 뗀 칸 이름이었다.
정식 이름 목록에 없어 빈 칸이 되었고, 운영(FULL) 경로는 칸 없는 문장을 거르므로
맞는 문장이 통째로 사라졌다. 복원은 «그 장의 허용 목록에 있는 이름»일 때만 한다 —
다른 장의 칸 이름을 받아 주면 문장이 엉뚱한 장의 사실로 장부에 오른다.
"""
from __future__ import annotations

import json

import pytest

from src.features.composer.constants import GRADE_CONFIRMED
from src.features.composer.logic import _sentence_from_item, parse_section_response
from src.shared.report_claim_policy import CLAIM_SLOTS_BY_SECTION


def _item(claim_slot: str) -> dict[str, object]:
    return {"글": "가나다전자는 산업용 검사 장비를 만든다.", "인용": ["1"],
            "등급": GRADE_CONFIRMED, "주장슬롯": claim_slot}


def _planned(claim_slot: str, section_id: str = "") -> str:
    sentence = _sentence_from_item(_item(claim_slot), section_id)
    assert sentence is not None, "형식이 맞는 항목은 문장으로 읽혀야 한다"
    return sentence.planned_claim_slot


@pytest.mark.parametrize(("section_id", "claim_slot"), [
    ("identity", "identity:business_definition"),
    ("business_model", "business_model:revenue_model"),
])
def test_정식_이름은_그대로_받는다(section_id, claim_slot):
    assert _planned(claim_slot, section_id) == claim_slot


@pytest.mark.parametrize(("section_id", "raw", "expected"), [
    ("identity", "business_definition", "identity:business_definition"),
    ("identity", "corporate_identity", "identity:corporate_identity"),
    ("identity", " corporate_identity ", "identity:corporate_identity"),
    ("business_model", "revenue_model", "business_model:revenue_model"),
    ("business_model", "value_exchange", "business_model:value_exchange"),
])
def test_장_ID를_뗀_이름은_그_장의_정식_이름으로_복원한다(section_id, raw, expected):
    assert _planned(raw, section_id) == expected


def test_모든_장의_모든_칸이_장_ID_없이도_제_이름으로_돌아온다():
    """허용 목록 전체에 같은 규칙이 걸리는지 본다 — 장마다 따로 새지 않게."""
    restored = {
        (section_id, slot): _planned(slot.split(":", 1)[1], section_id)
        for section_id, slots in CLAIM_SLOTS_BY_SECTION.items()
        for slot in slots
    }
    assert restored and all(value == slot for (_, slot), value in restored.items())


@pytest.mark.parametrize(("section_id", "raw"), [
    ("identity", "revenue_model"),
    ("identity", "value_exchange"),
    ("identity", "business_model:revenue_model"),
    ("business_model", "corporate_identity"),
    ("business_model", "identity:corporate_identity"),
])
def test_다른_장의_칸_이름은_받지_않는다(section_id, raw):
    assert _planned(raw, section_id) == ""


@pytest.mark.parametrize("raw", [
    "invented_slot", "identity:invented", "", "   ", ":business_definition",
    "identity:", "identity:identity:business_definition", "Business_Definition",
])
def test_목록에_없는_값은_빈_칸이다(raw):
    assert _planned(raw, "identity") == ""


@pytest.mark.parametrize("raw", ["business_definition", "identity:business_definition"])
def test_장_ID가_없으면_예전처럼_빈_칸이다(raw):
    assert _planned(raw) == ""
    parsed = parse_section_response(json.dumps({"문장들": [_item(raw)]}, ensure_ascii=False))
    assert parsed is not None and parsed[0].planned_claim_slot == ""


def test_공개_파서도_같은_복원_규칙을_쓴다():
    """장 작성 응답 파서(parse_section_response)도 같은 항목 해석을 거친다."""
    raw = json.dumps({"문장들": [_item("revenue_model"), _item("corporate_identity")]},
                     ensure_ascii=False)
    parsed = parse_section_response(raw, "business_model")
    assert parsed is not None
    assert [sentence.planned_claim_slot for sentence in parsed] == ["business_model:revenue_model", ""]
