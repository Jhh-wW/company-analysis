"""검수 JSON 파싱 실패의 기존 재요청에만 붙이는 네이티브 출력 계약.

최초 검수에는 스키마 입력 비용을 더하지 않는다. 재요청이 없는 묶음 검수도
적용 대상이 아니며, 이 모듈 때문에 호출이나 재시도가 생기지 않는다.
스키마는 응답의 모양만 제한한다. 근거 필요 여부·원문 결속·번호 소유권은
기존 검증기가 판정하며, 거짓·애매에 없는 증거를 만들어 넣도록 강제하지 않는다.
선택 필드를 null로 채우지 않아 원문참조와 기존 생략 규칙도 그대로 유지한다.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from src.features.composer.body_review_constants import BODY_REVIEW_COMPARISON_KEY
from src.features.composer.diagram_review_constants import DIAGRAM_REASON_KEY
from src.features.composer.direct_support_constants import RELATION_TYPES
from src.features.composer.future_plan_constants import (
    FUTURE_FIELD_KEYS,
    FUTURE_KEY,
    FUTURE_MODE_KEY,
    FUTURE_MODES,
)
from src.features.composer.grounding_constants import (
    GROUNDING_KEY,
    NUMERIC_KEY,
    REVIEW_ENTRIES_KEY,
    REVIEW_NUMBER_KEY,
    REVIEW_REJECTED,
    REVIEW_RESULT_KEY,
    REVIEW_SUPPORT_CANDIDATE_VERDICTS,
    TIME_KEY,
    TREND_DIRECTIONS,
    TREND_KEY,
)
from src.features.composer.numeric_quote_refs import NUMERIC_QUOTE_REF_KEY
from src.features.composer.prompt_metadata import PromptMetadata
from src.features.composer.role_binding_constants import RELATION_KEY


class ReviewPrompt(PromptMetadata):
    """기존 문자열과 메타데이터를 함께 전달하는 재요청 프롬프트."""

    response_schema: Mapping[str, Any]

    def __new__(
        cls, value: str, response_schema: Mapping[str, Any],
        *, cache_prefix_chars: int | None = None,
    ) -> ReviewPrompt:
        prompt = super().__new__(cls, value, cache_prefix_chars=cache_prefix_chars)
        prompt.response_schema = response_schema
        return prompt

    def _with_text(self, value: str, *, cache_prefix_chars: int) -> ReviewPrompt:
        return ReviewPrompt(
            value, self.response_schema, cache_prefix_chars=cache_prefix_chars,
        )


def _object(
    properties: dict[str, Any], *, required: tuple[str, ...] | None = None
) -> dict[str, Any]:
    """네이티브 출력에서 필요한 닫힌 객체를 만든다."""
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties) if required is None else list(required),
        "additionalProperties": False,
    }


def _strings(*names: str) -> dict[str, Any]:
    return {name: {"type": "string"} for name in names}


def _array(items: dict[str, Any]) -> dict[str, Any]:
    return {"type": "array", "items": items}


def _grounding_schema() -> dict[str, Any]:
    # 원문/원문참조는 키가 동시에 존재해도 실패한다. 두 닫힌 객체로 나누고
    # 후보값은 단일 수치에서 추론 가능하므로 생략을 허용한다.
    numeric_common = _strings("표현", "항목", "근거", "원문항목", "원문값")
    numeric = {"anyOf": [
        _object(
            {**numeric_common, "원문": {"type": "string"},
             "후보값": {"type": "string"}},
            required=(*numeric_common, "원문"),
        ),
        _object(
            {**numeric_common, NUMERIC_QUOTE_REF_KEY: {"type": "integer"},
             "후보값": {"type": "string"}},
            required=(*numeric_common, NUMERIC_QUOTE_REF_KEY),
        ),
    ]}
    observation = _object(
        _strings("근거", "원문", "항목", "기간", "원문값", "원문항목"),
        required=("근거", "원문", "항목", "기간", "원문값"),
    )
    trend = _object({
        **_strings("표현", "항목"),
        "방향": {"type": "string", "enum": sorted(TREND_DIRECTIONS)},
        "관측": _array(observation),
    })
    # 관계 유형에 따라 원인·결과 또는 대상·역할값을 읽는다. 선택 필드를
    # 모두 채우게 하지 않으며, 해당 유형의 필수 증명은 기존 가드에 맡긴다.
    relation = _object({
        **_strings("근거", "원문"),
        "유형": {"type": "string", "enum": list(RELATION_TYPES)},
        **_strings("원인", "결과", "대상", "역할값"),
    }, required=("근거", "원문", "유형"))
    future = _object({
        **_strings(*FUTURE_FIELD_KEYS),
        FUTURE_MODE_KEY: {"type": "string", "enum": list(FUTURE_MODES)},
    })
    return _object({
        NUMERIC_KEY: _array(numeric),
        TREND_KEY: _array(trend),
        TIME_KEY: _array(_object(_strings("표현", "근거", "원문", "기간"))),
        RELATION_KEY: _array(relation),
        FUTURE_KEY: _array(future),
    }, required=())


def _review_schema(*, diagram: bool = False) -> dict[str, Any]:
    reason_key = DIAGRAM_REASON_KEY if diagram else BODY_REVIEW_COMPARISON_KEY
    verdicts = sorted({REVIEW_REJECTED, *REVIEW_SUPPORT_CANDIDATE_VERDICTS})
    # 생성은 기존 지시대로 번호=정수, 인용=문자열로 고정한다. 구형 응답의
    # 숫자 문자열 번호를 받아 온 파서는 그대로 두고, 생성 문법만 단순화한다.
    identity = {REVIEW_NUMBER_KEY: {"type": "integer"}}
    ownership = {
        "장": {"type": "string"},
        "근거": _array({"type": "string"}),
    }
    # 대조 설명 유무를 두 행으로 나누면 공통 근거까지 문법에서 확장되어 실제
    # API가 grammar too large로 거절한다. 기존 지시가 요구한 설명을 생성의
    # 필수 필드로 두어 «번호 → 대조 설명 → 결과» 순서와 단일 행을 유지한다.
    # 설명 없는 구형 응답을 받는 파서의 호환 범위는 바꾸지 않는다.
    properties = {**identity, reason_key: {"type": "string"},
                  REVIEW_RESULT_KEY: {"type": "string", "enum": verdicts}}
    required = tuple(properties)
    properties.update(ownership)
    properties[GROUNDING_KEY] = {"$ref": "#/$defs/grounding"}
    return {
        **_object({REVIEW_ENTRIES_KEY: _array(_object(properties, required=required))}),
        # 후보 내용이나 인용 목록을 넣지 않는 고정 스키마다.
        "$defs": {"grounding": _grounding_schema()},
    }


FLAT_REVIEW_SCHEMA = _review_schema()
DIAGRAM_REVIEW_SCHEMA = _review_schema(diagram=True)
