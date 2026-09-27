from __future__ import annotations

import json

from src.features.composer.constants import GRADE_CONFIRMED, SECTION_IDS
from src.features.composer.logic import build_section_prompt, parse_section_response
from src.features.composer.port import CollectedFragment
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


def test_FULL프롬프트는_임의두종류가아니라_필수의미칸을_모두요구한다() -> None:
    prompt = build_section_prompt(
        "테스트",
        "business_model",
        (),
        None,
        show_supported_claim_slots=True,
    )

    assert "FULL 필수 의미칸" in prompt
    assert "business_model:revenue_model" in prompt
    assert "business_model:customer_type" in prompt
    assert "business_model:value_exchange" in prompt


def test_FULL프롬프트는_근거번호와_지원슬롯을_먼저_짝짓고_빈슬롯예시를_내지_않는다() -> None:
    fragments = (
        CollectedFragment(
            "12", "공식자료", "가상의 서비스 제공 원문",
            supported_claim_slots=("business_model:value_exchange",),
        ),
        CollectedFragment(
            "27", "공식자료", "가상의 고객 유형 원문",
            supported_claim_slots=("business_model:customer_type",),
        ),
    )
    prompt = build_section_prompt(
        "테스트", "business_model", fragments, None,
        show_supported_claim_slots=True,
    )
    legacy = build_section_prompt("테스트", "business_model", fragments, None)

    assert "- business_model:value_exchange: 12\n" in prompt
    assert "- business_model:customer_type: 27\n" in prompt
    assert "- business_model:revenue_model: " not in prompt
    assert "조각 id를 문자열로 정확히 복사한다" in prompt
    assert "대괄호나 '조각 ' 접두어를 붙이거나" in prompt
    assert '"주장슬롯": "<인용한 조각의 지원 주장슬롯 id>"' in prompt
    assert "허용된 id 또는 빈 문자열" not in prompt
    assert "어느 자리에도 맞지 않으면 빈 문자열" not in prompt
    assert "허용된 id 또는 빈 문자열" in legacy
    assert "FULL 근거 선택표" not in legacy


def test_FULL전용_흐름표_스키마에도_같은_지원슬롯_계약이_적용된다() -> None:
    prompt = build_section_prompt(
        "테스트", "operations_partners", (), None,
        show_supported_claim_slots=True,
    )

    assert '"주장슬롯": "<인용한 조각의 지원 주장슬롯 id>"' in prompt
    assert "허용된 id 또는 빈 문자열" not in prompt
    assert "경로표" in prompt


def test_FULL모든장의_스키마가_빈슬롯예시를_허용하지_않는다() -> None:
    for section_id in SECTION_IDS:
        prompt = build_section_prompt(
            "테스트", section_id, (), None,
            show_supported_claim_slots=True,
        )
        assert "허용된 id 또는 빈 문자열" not in prompt
        assert "<인용한 조각의 지원 주장슬롯 id>" in prompt


def test_FULL색인은_같은조각번호를_지원슬롯마다_중복기재하지_않는다() -> None:
    fragment = CollectedFragment(
        "17", "공식자료", "가상의 고객 관계 원문",
        supported_claim_slots=("business_model:customer_type",),
    )
    prompt = build_section_prompt(
        "테스트", "business_model", (fragment, fragment), None,
        show_supported_claim_slots=True,
    )

    assert prompt.count("- business_model:customer_type: 17\n") == 1


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
