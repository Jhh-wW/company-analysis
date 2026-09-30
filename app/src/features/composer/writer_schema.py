"""FULL 작성 단계에서 서로 다른 의미칸의 선택쌍 혼합을 막는다."""

from collections.abc import Mapping, Sequence
import json
from typing import Any

from src.features.composer.constants import (
    FLOW_HEADERS_BY_SECTION, RESPONSE_EVIDENCE_PAIR_KEY, RESPONSE_FLOW_KEY,
    RESPONSE_FLOW_ROW_CELLS_KEY, RESPONSE_FLOW_ROW_CITATIONS_KEY,
    RESPONSE_GRADE_KEY, RESPONSE_SENTENCES_KEY, RESPONSE_TEXT_KEY, SECTION_IDS, VALID_GRADES,
)
from src.features.composer.evidence_pair_selection import EvidencePairMap
from src.features.composer.news_constants import NEWS_DECISIONS_KEY, NEWS_VALID_EXCLUSION_REASONS
from src.features.composer.prompt_metadata import PromptMetadata
from src.features.composer.writer_schema_constants import (
    FULL_REQUIRED_CONTENT_GUIDE, REQUIRED_CONTENT_ARRAY_DESCRIPTION, RESPONSE_REQUIRED_CONTENT_KEY,
)
from src.shared.report_evidence.policy import injected_slots_for, required_slots_for


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


def required_writer_slots(section_id: str, pairs: EvidencePairMap) -> tuple[str, ...]:
    """프로그램 책임 칸과 지원쌍이 없는 칸은 작가에게 요구하지 않는다."""
    supported = {slot for slot, _fid in pairs.values()}
    injected = set(injected_slots_for(section_id))
    return tuple(slot for slot in required_slots_for(section_id)
                 if slot in supported and slot not in injected)


def required_content_guide(section_id: str, pairs: EvidencePairMap) -> str:
    slots = required_writer_slots(section_id, pairs)
    if not slots:
        return ""
    shape = {RESPONSE_REQUIRED_CONTENT_KEY: {slot: [] for slot in slots}}
    return FULL_REQUIRED_CONTENT_GUIDE + json.dumps(shape, ensure_ascii=False) + "\n"


def writer_sentence_items(payload: Mapping, section_id: str, pairs: EvidencePairMap | None):
    """새 응답의 칸별 배열을 기존 검수 입력으로 합친다. 사실을 추가하지 않는다.

    과거 보존 응답은 원래 파서 계약으로 읽는다. 새 영역을 사용하면 필수 키와
    선택쌍의 의미칸이 모두 일치해야 하며, 어긋난 개별 문장은 제외한다.
    """
    items = payload.get(RESPONSE_SENTENCES_KEY)
    if not isinstance(items, list):
        return None
    if RESPONSE_REQUIRED_CONTENT_KEY not in payload:
        return items
    if pairs is None or section_id not in SECTION_IDS:
        return None
    required = payload[RESPONSE_REQUIRED_CONTENT_KEY]
    slots = required_writer_slots(section_id, pairs)
    if not isinstance(required, Mapping) or set(required) != set(slots):
        return None
    selected_items = []
    for slot in slots:
        rows = required[slot]
        if not isinstance(rows, list):
            return None
        for item in rows:
            if not isinstance(item, Mapping):
                continue
            ids = item.get(RESPONSE_EVIDENCE_PAIR_KEY)
            if (not isinstance(ids, list) or not ids
                    or any(type(key) is not str or key not in pairs
                           or pairs[key][0] != slot for key in ids)):
                continue
            selected_items.append(item)
    return [*selected_items, *items]


def build_full_writer_schema(section_id: str, pairs: EvidencePairMap, *, news_fragment_ids: Sequence[str] = ()) -> dict | None:
    """선택지 ID가 각 의미칸에 속하게 하며 사실의 참 판정은 기존 검수에 맡긴다."""
    if not pairs:
        return None
    by_slot: dict[str, list[str]] = {}
    for pair_id, (slot, _fid) in pairs.items():
        by_slot.setdefault(slot, []).append(pair_id)
    branches = {
        slot: _object({
            RESPONSE_TEXT_KEY: {"type": "string"},
            RESPONSE_GRADE_KEY: {"type": "string", "enum": sorted(VALID_GRADES)},
            RESPONSE_EVIDENCE_PAIR_KEY: {
                "type": "array", "minItems": 1,
                "items": {"type": "string", "enum": ids},
            },
        })
        for slot, ids in by_slot.items()
    }
    properties = {}
    required_slots = required_writer_slots(section_id, pairs)
    if required_slots:
        properties[RESPONSE_REQUIRED_CONTENT_KEY] = _object({
            slot: {"type": "array", "items": branches[slot],
                   "description": REQUIRED_CONTENT_ARRAY_DESCRIPTION}
            for slot in required_slots
        })
    properties[RESPONSE_SENTENCES_KEY] = {"type": "array", "items": {"anyOf": list(branches.values())}}
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
    required_keys = (RESPONSE_REQUIRED_CONTENT_KEY, RESPONSE_SENTENCES_KEY) if required_slots else (RESPONSE_SENTENCES_KEY,)
    return _object(properties, required=required_keys)


def with_full_writer_schema(prompt: str, section_id: str, pairs: EvidencePairMap, *, news_fragment_ids: Sequence[str] = ()) -> str:
    schema = build_full_writer_schema(section_id, pairs, news_fragment_ids=news_fragment_ids)
    return prompt if schema is None else WriterPrompt(prompt, schema)
