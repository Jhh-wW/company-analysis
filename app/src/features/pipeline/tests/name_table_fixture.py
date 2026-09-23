"""이름 표 통합 시험의 요약 하한을 채우는 원문 결속 본문 세 문장."""

from __future__ import annotations

import re

from src.features.composer.tests.test_pipeline import section_id_in_prompt


_BODY_FACTS = {
    "identity": (
        "가나다회사는 사업부문 하나를 운영한다.",
        "identity:business_definition",
    ),
    "business_model": (
        "회사는 고객에게 사업부문 운영 서비스를 제공한다.",
        "business_model:value_exchange",
    ),
    "culture": (
        "회사는 임직원 교육훈련 과정을 운영한다.",
        "culture:work_principle",
    ),
}
_FRAGMENT_RE = re.compile(r"\[조각 ([^\]]+)\].*?(?=\n\[조각 |\Z)", re.DOTALL)


def with_supported_body_fragments(
    fragments: dict[int, dict[str, object]],
) -> dict[int, dict[str, object]]:
    """이름 원문은 그대로 두고 주변 본문에 쓸 독립 가공 근거를 붙인다."""
    result = dict(fragments)
    first_id = max(result, default=0) + 1
    for index, (section_id, (text, _slot)) in enumerate(_BODY_FACTS.items(), first_id):
        result[index] = {
            "종류": "공식 IR",
            "원문": text,
            "출처": f"https://fixture.example/name-table/{section_id}",
            "문서명": "이름 표 시험의 본문 자료",
            "문서일": "2026-08-01",
        }
    return result


def supported_body_sentences(prompt: str) -> list[dict[str, object]]:
    """현재 장의 실제 원문 조각과 주장 슬롯을 함께 반환한다."""
    fact = _BODY_FACTS.get(section_id_in_prompt(prompt))
    if fact is None:
        return []
    text, slot = fact
    matches = [block for block in _FRAGMENT_RE.finditer(prompt) if text in block.group(0)]
    assert matches, "이름 표 시험의 본문 근거가 작성 프롬프트에 없습니다"
    return [{"글": text, "인용": [matches[-1].group(1)], "등급": "확인", "주장슬롯": slot}]
