"""허용된 출처의 표시 ID만 복원한다. 원 응답과 인용 원문은 수정하지 않는다."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
import json

from src.features.composer.direct_support_constants import RELATION_KEY
from src.features.composer.future_plan_constants import FUTURE_KEY
from src.features.composer.grounding_constants import (
    GROUNDING_KEY, GROUNDING_SOURCE_FIELD, NUMERIC_KEY, RECOGNITION_KEY,
    REVIEW_ENTRIES_KEY, REVIEW_NUMBER_KEY, TIME_KEY, TREND_KEY,
)
from src.features.composer.review_evidence_constants import (
    REVIEW_DISPLAY_ID_RE, TREND_OBSERVATIONS_KEY,
)
from src.features.composer.verdict_number import coerce_verdict_number
from src.features.composer.logic import extract_json_payload


@dataclass(frozen=True)
class ReviewEvidenceContext:
    """전체 원 ID와 문장별 인용·보조 근거의 소유권을 함께 보관한다."""

    source_ids: frozenset[str]
    citations_by_number: Mapping[int, frozenset[str]]
    proof_ids_by_number: Mapping[int, frozenset[str]]


def _source_id(value: object, allowed: frozenset[str], registry: frozenset[str]) -> str:
    source_id = str(value).strip()
    # 실제 ID와 표시 이름이 충돌하면 실제 ID의 뜻을 먼저 지킨다.
    if source_id in registry or source_id in allowed:
        return source_id
    match = REVIEW_DISPLAY_ID_RE.fullmatch(source_id)
    if match is not None and match[1] in allowed and match[1] in registry:
        return match[1]
    return source_id


def normalize_review_entry(
    entry: Mapping, context: ReviewEvidenceContext,
    *, validate_proof_ids: bool = True,
) -> tuple[dict, bool]:
    """닫힌 ID 칸만 복원하고, 불필요한 증명에 든 외부 출처도 거절한다."""
    derived = deepcopy(dict(entry))
    number = coerce_verdict_number(entry.get(REVIEW_NUMBER_KEY))
    citations = context.citations_by_number.get(number, frozenset())
    proof_ids = context.proof_ids_by_number.get(number, frozenset())
    raw_ids = entry.get(GROUNDING_SOURCE_FIELD)
    valid = isinstance(raw_ids, list)
    if isinstance(raw_ids, list):
        ids = [_source_id(value, citations, context.source_ids) for value in raw_ids]
        derived[GROUNDING_SOURCE_FIELD] = ids
        valid = bool(ids) and len(ids) == len(set(ids)) and frozenset(ids) == citations
    grounding = derived.get(GROUNDING_KEY)
    if not isinstance(grounding, dict):
        return derived, valid

    def normalize_proofs(rows: object) -> None:
        nonlocal valid
        if not isinstance(rows, list):
            return
        for row in rows:
            if not isinstance(row, dict) or GROUNDING_SOURCE_FIELD not in row:
                continue
            value = _source_id(row[GROUNDING_SOURCE_FIELD], proof_ids, context.source_ids)
            row[GROUNDING_SOURCE_FIELD] = value
            if validate_proof_ids and value not in proof_ids:
                valid = False

    for key in (NUMERIC_KEY, TIME_KEY, RELATION_KEY, FUTURE_KEY, RECOGNITION_KEY):
        normalize_proofs(grounding.get(key))
    trends = grounding.get(TREND_KEY)
    if isinstance(trends, list):
        for trend in trends:
            if isinstance(trend, dict):
                normalize_proofs(trend.get(TREND_OBSERVATIONS_KEY))
    return derived, valid


def normalize_review_binding_text(
    raw: str | None, context: ReviewEvidenceContext,
) -> tuple[str | None, frozenset[int]]:
    """구제·병합된 응답도 판정 파서와 같은 복원 규칙으로 근거 검사에 넘긴다."""
    payload = extract_json_payload(raw or "")
    if not isinstance(payload, Mapping) or not isinstance(payload.get(REVIEW_ENTRIES_KEY), list):
        return raw, frozenset()
    rows = []
    invalid: set[int] = set()
    for entry in payload[REVIEW_ENTRIES_KEY]:
        if not isinstance(entry, Mapping):
            rows.append(entry)
            continue
        derived, valid = normalize_review_entry(entry, context)
        rows.append(derived)
        number = coerce_verdict_number(entry.get(REVIEW_NUMBER_KEY))
        if not valid and number is not None:
            invalid.add(number)
    derived_payload = {**payload, REVIEW_ENTRIES_KEY: rows}
    text = raw if derived_payload == payload else json.dumps(derived_payload, ensure_ascii=False)
    return text, frozenset(invalid)
