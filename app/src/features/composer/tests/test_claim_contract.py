from __future__ import annotations

import json
import re

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
    fragments = tuple(
        CollectedFragment(
            str(index), "공식자료", f"가상 근거 {index}",
            supported_claim_slots=(slot,),
        )
        for index, slot in enumerate((
            "business_model:revenue_model",
            "business_model:customer_type",
            "business_model:value_exchange",
        ), start=1)
    )
    prompt = build_section_prompt(
        "테스트",
        "business_model",
        fragments,
        None,
        show_supported_claim_slots=True,
    )

    assert "FULL 산문 필수 의미칸" in prompt
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

    assert re.search(r"- business_model:value_exchange: 12\[p2-\d+\]\n", prompt)
    assert re.search(r"- business_model:customer_type: 27\[p2-\d+\]\n", prompt)
    assert "- business_model:revenue_model: " not in prompt
    assert "- business_model:revenue_model\n" not in prompt
    assert "근거선택 ID가 가리키는 원문 조각" in prompt
    assert "«주장슬롯»과 «인용»은 별도로 고르지 않는다" in prompt
    assert '"근거선택": ["<지원쌍 ID>"]' in prompt
    assert '"주장슬롯": "<인용한 조각의 지원 주장슬롯 id>"' not in prompt
    assert "허용된 id 또는 빈 문자열" not in prompt
    assert "어느 자리에도 맞지 않으면 빈 문자열" not in prompt
    assert "허용된 id 또는 빈 문자열" in legacy
    assert "FULL 근거 선택표" not in legacy
    assert "- business_model:revenue_model\n" in legacy


def test_FULL전용_흐름표_스키마에도_같은_지원슬롯_계약이_적용된다() -> None:
    prompt = build_section_prompt(
        "테스트", "operations_partners", (), None,
        show_supported_claim_slots=True,
    )

    assert '"근거선택": ["<지원쌍 ID>"]' in prompt
    assert "허용된 id 또는 빈 문자열" not in prompt
    assert "경로표" in prompt


def test_FULL모든장의_스키마가_빈슬롯예시를_허용하지_않는다() -> None:
    for section_id in SECTION_IDS:
        prompt = build_section_prompt(
            "테스트", section_id, (), None,
            show_supported_claim_slots=True,
        )
        assert "허용된 id 또는 빈 문자열" not in prompt
        assert '"근거선택": ["<지원쌍 ID>"]' in prompt


def test_FULL색인은_같은조각번호를_지원슬롯마다_중복기재하지_않는다() -> None:
    fragment = CollectedFragment(
        "17", "공식자료", "가상의 고객 관계 원문",
        supported_claim_slots=("business_model:customer_type",),
    )
    prompt = build_section_prompt(
        "테스트", "business_model", (fragment, fragment), None,
        show_supported_claim_slots=True,
    )

    assert len(re.findall(r"- business_model:customer_type: 17\[p2-\d+\]\n", prompt)) == 1


def test_FULL은_미지원_선택슬롯을_작성목록에서_제외한다() -> None:
    fragment = CollectedFragment(
        "4", "공식자료", "가상의 사업 계획 원문",
        supported_claim_slots=("future_strategy:stated_plan",),
    )
    prompt = build_section_prompt(
        "테스트", "future_strategy", (fragment,), None,
        show_supported_claim_slots=True,
    )
    legacy = build_section_prompt("테스트", "future_strategy", (fragment,), None)

    assert "- future_strategy:stated_plan\n" in prompt
    assert "- future_strategy:plan_condition\n" not in prompt
    assert "- future_strategy:plan_timing\n" not in prompt
    assert "- future_strategy:plan_condition\n" in legacy
    assert "- future_strategy:plan_timing\n" in legacy
    assert "자료가 지원하지 않는 필수 칸을 추측해 채우지 않는다" in prompt


def test_FULL_4장_수치필수칸은_프로그램책임이고_표이름인용을_유도하지_않는다() -> None:
    fragment = CollectedFragment(
        "5", "공식자료", "가상의 완료된 사업 변화",
        supported_claim_slots=("past_changes:completed_execution",),
    )
    prompt = build_section_prompt(
        "테스트", "past_changes", (fragment,), None,
        show_supported_claim_slots=True,
    )
    legacy = build_section_prompt("테스트", "past_changes", (fragment,), None)

    assert re.search(r"- past_changes:completed_execution: 5\[p4-\d+\]\n", prompt)
    assert "FULL 프로그램 책임 의미칸" in prompt
    assert "past_changes:historical_performance" in prompt
    assert "- past_changes:historical_performance: " not in prompt
    assert "표 이름을 인용하거나 표의 수치를" in prompt
    assert "표 자체에서 숫자 산문을 만들지 않는다" in prompt
    assert "다시 쓰거나 표 이름을 인용하지 않는다" in prompt
    assert "그 표를 가리킨다" not in prompt
    assert "전사 3개년 실적 수치와 그 변화의 의미" not in prompt
    assert "전사 3개년 실적 수치와 그 변화의 의미" in legacy
    assert "그 표를 가리킨다" in legacy


def test_FULL_9장_주입칸을_산문필수처럼_요구하지_않는다() -> None:
    fragment = CollectedFragment(
        "8", "공식자료", "가상의 회사 자기 선언",
        supported_claim_slots=(
            "competitive_position:stated_differentiator",
            "competitive_position:limitation",
        ),
    )
    prompt = build_section_prompt(
        "테스트", "competitive_position", (fragment,), None,
        show_supported_claim_slots=True,
    )

    writer_part, injected_part = prompt.split("FULL 프로그램 책임 의미칸", 1)
    assert "competitive_position:limitation" not in writer_part
    assert "competitive_position:limitation" in injected_part
    assert "- competitive_position:limitation: 8\n" not in prompt
    assert re.search(r"- competitive_position:stated_differentiator: 8\[p9-\d+\]\n", prompt)


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
