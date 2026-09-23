"""검수 JSON의 네이티브 출력 계약 — 최초 본문 검수의 첫 요청·파싱 재요청·누락 후속에 붙인다.

최초 본문 검수는 첫 요청의 JSON 형식 오류와 재전송을 줄이기 위해 스키마를 싣는다.
후속 재검수·요약 검수는 파싱 재요청에만 붙인다.
묶음(packet) 검수는 스위치 ``PACKET_REVIEW_SCHEMA_ENABLED``(기본 꺼짐, 이 파일 끝)가
켜졌을 때만 첫 요청·형식 재요청·누락 후속에 FLAT_REVIEW_SCHEMA 를 싣는다. 꺼져 있으면
세 호출 모두 스키마 없는 문자열이다(재요청·누락 후속 자체는 스위치와 무관하게 있다).
이 스키마에서 «장»·«근거» 칸은 선택 칸이라, packet 행의 장 소유권·근거 id 일치는
스키마가 아니라 packet 파서가 검사한다.
이 모듈 때문에 호출이나 재시도가 추가되지는 않는다.
스키마는 응답의 모양만 제한한다. 근거 필요 여부·원문 결속·번호 소유권은
기존 검증기가 판정하며, 거짓·애매에 없는 증거를 만들어 넣도록 강제하지 않는다.
선택 필드를 null로 채우지 않아 원문참조와 기존 생략 규칙도 그대로 유지한다.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from src.features.composer.body_review_constants import BODY_REVIEW_COMPARISON_KEY
from src.features.composer.combined_relation_constants import (
    COMBINED_RELATION_WORD_KEY,
    COMBINED_SCOPE_KEY,
)
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
    RECOGNITION_KEY,
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
    """기존 문자열과 응답 스키마·캐시 경계 메타데이터를 함께 전달하는 검수 프롬프트.

    첫 요청·재요청·누락 후속 어디에 쓰여도 문자열 바이트는 감싸기 전과 같다.
    """

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
    # 관계 유형에 따라 원인·결과, 대상·역할값, 또는 결합의 범위·관계를 읽는다.
    # 선택 필드를 모두 채우게 하지 않으며, 해당 유형의 필수 증명은 기존 가드에
    # 맡긴다. «결합»은 유형 enum에만 있고 두 칸이 빠져 있어, 스키마가 붙는 호출에서
    # 안내문(combined_relation_constants)대로 쓴 결합 항목이 거절됐다.
    relation = _object({
        **_strings("근거", "원문"),
        "유형": {"type": "string", "enum": list(RELATION_TYPES)},
        **_strings("원인", "결과", "대상", "역할값",
                   COMBINED_SCOPE_KEY, COMBINED_RELATION_WORD_KEY),
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
        # 정성 수익 인식 기준 단정의 정확 인용(4차 채택안). 최소 세 칸만 두고
        # 배열 자체는 선택이다 — 요구가 없는 후보에 빈 근거를 만들게 하지 않는다.
        RECOGNITION_KEY: _array(_object(_strings("표현", "근거", "원문"))),
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

#: 묶음(packet) 검수 요청에 네이티브 JSON 스키마(FLAT_REVIEW_SCHEMA)를 실을지 — 기본 꺼짐.
#:
#: ★ 왜 꺼 두나 (2026-09-23 총괄 확인·보관 기록 재집계)
#:   · 보관된 실제 공급자 호출 24건(4차 13건·5차 11건, 작성 19·검수 5) 전부
#:     ``response_schema`` 가 비어 있다. 즉 ``output_config`` json_schema 요청은 실제
#:     공급자로 한 번도 검증된 적이 없다.
#:   · 공급자가 스키마 요청을 거절하면 pipeline/real.py `_v2_ask_via_provider` 가
#:     ``gateway.ProviderCallFailed`` 를 ``AskFatalError`` 로 올린다. 검수의 `_safe_ask`
#:     는 이 예외를 삼키지 않으므로(요청 전역 장애 계약) 보고서 요청 «전체»가 멈춘다.
#:   · 4차 packet 검수는 스키마 없이도 코드 펜스를 잘라(braces_sliced) 42/42 정상
#:     판독됐다. 형식 오류는 행 단위 구제와 형식 재요청·누락 후속이 스키마 없이 받는다.
#: → 이번 배포는 «검증된 요청 형식(스키마 없음) + 행 단위 구제 + 재요청/누락 후속»이다.
#: ⚠️ 켜기 전에 유료 실측에서 저위험 호출로 공급자가 이 스키마를 받아들이는지 먼저
#:   확인한다. 켜면 packet 의 첫 요청(최초 본문 검수 ``initial_ask`` 가 있을 때)·형식
#:   재요청·누락 후속 세 호출에 스키마가 실린다(verify `_packet_review_prompt`).
#: ⚠️ 평문(flat) 경로 `_ask_verdicts` 의 스키마 부착은 이 스위치와 무관하다.
PACKET_REVIEW_SCHEMA_ENABLED: Final[bool] = False
