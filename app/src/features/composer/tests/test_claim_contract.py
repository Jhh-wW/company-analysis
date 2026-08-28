from __future__ import annotations

import json

from src.features.composer.constants import GRADE_CONFIRMED
from src.features.composer.logic import build_section_prompt, parse_section_response
from src.features.composer.verify import VERDICT_TRUE, verify_sentences


def _response(claim_slot: str | None) -> str:
    item: dict[str, object] = {
        "글": "회사는 공식 원문에서 주력 사업을 밝혔다.",
        "인용": ["1"],
        "등급": GRADE_CONFIRMED,
    }
    if claim_slot is not None:
        item["주장슬롯"] = claim_slot
    return json.dumps({"문장들": [item]}, ensure_ascii=False)


def test_작가는_계획된_claim_slot만_선택할수있다() -> None:
    valid = parse_section_response(
        _response("identity:business_definition"), "identity"
    )
    unknown = parse_section_response(_response("identity:invented"), "identity")
    missing = parse_section_response(_response(None), "identity")

    assert valid is not None and valid[0].planned_claim_slot == "identity:business_definition"
    assert unknown is not None and unknown[0].planned_claim_slot == ""
    assert missing is not None and missing[0].planned_claim_slot == ""
    assert valid[0].verification_state == "unverified"


def test_프롬프트가_장별_claim_slot목록과_누락규칙을_함께_준다() -> None:
    prompt = build_section_prompt("테스트", "identity", (), None)

    assert "원자 주장 계획" in prompt
    assert "identity:business_definition" in prompt
    assert '"주장슬롯"' in prompt
    assert "맞지 않으면 빈 문자열" in prompt


def test_작가의_확인은_독립검수전까지_verified가_아니다() -> None:
    parsed = parse_section_response(
        _response("identity:business_definition"), "identity"
    )
    assert parsed is not None

    verified = verify_sentences(
        parsed,
        {1: {"종류": "사업내용", "원문": "회사는 공식 원문에서 주력 사업을 밝혔다."}},
        None,
        lambda _prompt: json.dumps(
            {"판정": [{"번호": 1, "결과": VERDICT_TRUE}]},
            ensure_ascii=False,
        ),
    )

    assert verified[0].verification_state == "verified"
    assert verified[0].planned_claim_slot == "identity:business_definition"
