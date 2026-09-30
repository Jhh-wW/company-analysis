"""FULL 작성 단계에서 서로 다른 의미칸의 선택쌍 혼합을 막는다."""

from collections.abc import Mapping, Sequence
from typing import Any

from src.features.composer.constants import (
    FLOW_HEADERS_BY_SECTION, RESPONSE_EVIDENCE_PAIR_KEY, RESPONSE_FLOW_KEY,
    RESPONSE_FLOW_ROW_CELLS_KEY, RESPONSE_FLOW_ROW_CITATIONS_KEY,
    RESPONSE_GRADE_KEY, RESPONSE_SENTENCES_KEY, RESPONSE_TEXT_KEY, VALID_GRADES,
)
from src.features.composer.evidence_pair_selection import EvidencePairMap
from src.features.composer.news_constants import NEWS_DECISIONS_KEY, NEWS_VALID_EXCLUSION_REASONS
from src.features.composer.prompt_metadata import PromptMetadata


class WriterPrompt(PromptMetadata):
    """문자열·재요청·캐시 경계를 유지하는 작성 응답 스키마 표식."""

    response_schema: Mapping[str, Any]

    def __new__(cls, value: str, response_schema: Mapping[str, Any], *, cache_prefix_chars: int | None = None):
        prompt = super().__new__(cls, value, cache_prefix_chars=cache_prefix_chars)
        prompt.response_schema = response_schema
        return prompt

    def _with_text(self, value: str, *, cache_prefix_chars: int):
        return WriterPrompt(value, self.response_schema, cache_prefix_chars=cache_prefix_chars)


def _object(properties, *, required=None):
    return {"type": "object", "properties": properties,
            "required": list(properties) if required is None else list(required),
            "additionalProperties": False}


def build_full_writer_schema(section_id: str, pairs: EvidencePairMap, *, news_fragment_ids: Sequence[str] = ()) -> dict | None:
    """선택지 ID가 각 의미칸에 속하게 하며 사실의 참 판정은 기존 검수에 맡긴다."""
    if not pairs:
        return None
    by_slot: dict[str, list[str]] = {}
    for pair_id, (slot, _fid) in pairs.items():
        by_slot.setdefault(slot, []).append(pair_id)
    branches = [
        _object({
            RESPONSE_TEXT_KEY: {"type": "string"},
            RESPONSE_GRADE_KEY: {"type": "string", "enum": sorted(VALID_GRADES)},
            RESPONSE_EVIDENCE_PAIR_KEY: {
                "type": "array", "minItems": 1,
                "items": {"type": "string", "enum": ids},
            },
        })
        for ids in by_slot.values()
    ]
    properties = {RESPONSE_SENTENCES_KEY: {"type": "array", "items": {"anyOf": branches}}}
    if section_id in FLOW_HEADERS_BY_SECTION:
        count = len(FLOW_HEADERS_BY_SECTION[section_id])
        properties[RESPONSE_FLOW_KEY] = {"type": "array", "items": _object({
            RESPONSE_FLOW_ROW_CELLS_KEY: {"type": "array", "items": {"type": "string"}, "minItems": count, "maxItems": count},
            RESPONSE_FLOW_ROW_CITATIONS_KEY: {
                "type": "array", "minItems": 1,
                "items": {"type": "string", "enum": list(dict.fromkeys(fid for _slot, fid in pairs.values()))},
            },
        })}
    if news_fragment_ids:
        properties[NEWS_DECISIONS_KEY] = {"type": "array", "items": _object({
            "조각": {"type": "string", "enum": list(dict.fromkeys(news_fragment_ids))},
            "사유": {"type": "string", "enum": sorted(NEWS_VALID_EXCLUSION_REASONS)},
            "설명": {"type": "string"},
        })}
    return _object(properties, required=(RESPONSE_SENTENCES_KEY,))


def with_full_writer_schema(prompt: str, section_id: str, pairs: EvidencePairMap, *, news_fragment_ids: Sequence[str] = ()) -> str:
    schema = build_full_writer_schema(section_id, pairs, news_fragment_ids=news_fragment_ids)
    return prompt if schema is None else WriterPrompt(prompt, schema)
