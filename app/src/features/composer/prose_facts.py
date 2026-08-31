"""독립 검수를 통과한 일반 산문을 원자 사실 장부에 결속한다.

검수 AI가 ``참``이라고 답했다는 표식만으로는 공개 근거가 되지 않는다. 이
모듈은 검수 뒤 문장, 작가가 미리 고른 닫힌 주장 범주, 실제 인용 조각의
출처 신원과 정확한 바이트 해시를 한 FactRecord에 함께 잠근다. 문장에서
숫자나 출처를 다시 추측하지 않으며, 하나라도 맞지 않으면 사실을 만들지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from typing import Final, Optional, Sequence

from src.features.composer.constants import GRADE_CONFIRMED, GRADE_INTERPRETED
from src.features.composer.port import ComposedSentence
from src.features.pipeline.port import FactRecord
from src.features.provenance.sources import Source, exact_evidence_text_hash
from src.shared.report_claim_policy import CLAIM_SLOTS_BY_SECTION
from src.shared.report_quality.evidence_support import prose_evidence_support_ready
from src.shared.report_quality.fact_binding import fact_evidence_binding
from src.shared.report_quality.source_identity import document_identity


PROSE_FACT_BINDING_VERSION: Final[str] = "verified-prose-binding-v1"
PROSE_FACT_ID_PREFIX: Final[str] = "v2-prose-"
_WORD_RE: Final[re.Pattern[str]] = re.compile(r"[0-9A-Za-z가-힣]{2,}")
_TIME_STATE_BY_SECTION: Final[dict[str, str]] = {
    "past_changes": "past",
    "current_challenges": "present",
    "future_strategy": "future",
}


@dataclass(frozen=True)
class ProseEvidence:
    """문장 하나가 실제로 인용한 조각과 공개 출처 한 쌍."""

    fragment_id: str
    source: Source
    exact_text: str


def _normalized_text(value: object) -> str:
    return " ".join(str(value or "").split())


def _ordered_unique(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(value).strip() for value in values if str(value).strip()))


def _support_terms(claim: str, evidence: Sequence[ProseEvidence]) -> list[str]:
    """주장과 실제 원문 양쪽에 있는 낱말만 감사용으로 남긴다."""

    evidence_text = " ".join(item.exact_text for item in evidence).casefold()
    out: list[str] = []
    for token in _WORD_RE.findall(claim):
        normalized = token.casefold()
        if normalized in evidence_text and normalized not in out:
            out.append(normalized)
    return out[:12]


def _fact_id(
    *,
    company_name: str,
    section_id: str,
    claim_slot: str,
    claim: str,
    evidence_manifest: Sequence[dict[str, str]],
) -> str:
    payload = {
        "version": PROSE_FACT_BINDING_VERSION,
        "company": _normalized_text(company_name),
        "section_id": section_id,
        "claim_slot": claim_slot,
        "claim": _normalized_text(claim),
        "evidence": list(evidence_manifest),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return PROSE_FACT_ID_PREFIX + hashlib.sha256(encoded).hexdigest()[:32]


def build_verified_prose_fact(
    sentence: ComposedSentence,
    *,
    section_id: str,
    company_name: str,
    as_of_date: str,
    evidence: Sequence[ProseEvidence],
) -> Optional[FactRecord]:
    """검증 문장과 모든 인용이 정확히 맞을 때만 FactRecord를 만든다."""

    claim = _normalized_text(sentence.text)
    claim_slot = sentence.planned_claim_slot.strip()
    citations = _ordered_unique(sentence.citations)
    if (
        sentence.structured_claim is not None
        or sentence.verification_state != "verified"
        or sentence.grade not in (GRADE_CONFIRMED, GRADE_INTERPRETED)
        or not claim
        or claim_slot not in CLAIM_SLOTS_BY_SECTION.get(section_id, ())
        or not citations
        or tuple(item.fragment_id.strip() for item in evidence) != citations
    ):
        return None

    manifest: list[dict[str, str]] = []
    source_ids: list[str] = []
    source_identities: list[str] = []
    evidence_hashes: list[str] = []
    for item in evidence:
        source = item.source
        source_id = source.source_id.strip()
        identity = document_identity(source)
        evidence_hash = exact_evidence_text_hash(item.exact_text)
        if (
            not source_id
            or not identity
            or not evidence_hash
            or evidence_hash not in source.exact_evidence_hashes
            or source_id in source_ids
        ):
            return None
        source_ids.append(source_id)
        source_identities.append(identity)
        evidence_hashes.append(evidence_hash)
        manifest.append(
            {
                "fragment_id": item.fragment_id.strip(),
                "source_id": source_id,
                "document_identity": identity,
                "exact_sha256": evidence_hash,
            }
        )

    support_terms = _support_terms(claim, evidence)
    if not prose_evidence_support_ready(
        (
            "verified_prose"
            if sentence.grade == GRADE_CONFIRMED
            else "evidence_based_interpretation"
        ),
        support_terms,
    ):
        return None

    primary = evidence[0].source
    fact = FactRecord(
        fact_id=_fact_id(
            company_name=company_name,
            section_id=section_id,
            claim_slot=claim_slot,
            claim=claim,
            evidence_manifest=manifest,
        ),
        legal_entity=_normalized_text(company_name),
        subject_scope=_normalized_text(company_name),
        relationship_or_action=claim_slot.split(":", 1)[-1],
        claim=claim,
        claim_type=(
            "verified_prose"
            if sentence.grade == GRADE_CONFIRMED
            else "evidence_based_interpretation"
        ),
        section_owner=section_id,
        time_state=_TIME_STATE_BY_SECTION.get(section_id, "present"),
        as_of=str(as_of_date or "").strip(),
        source_id=source_ids[0],
        source_type=primary.source_type or primary.kind.value,
        source_title=primary.title or primary.label,
        source_publisher=primary.publisher,
        source_host=primary.host,
        source_url=primary.url,
        source_document_id=primary.document_id,
        location=primary.location,
        status="verified",
        fact_status=(
            "actual" if sentence.grade == GRADE_CONFIRMED else "provisional"
        ),
        verification_status="verified",
        # 원문 전체를 문장마다 중복 저장하지 않는다. 정확한 조각 바이트는
        # Source.exact_evidence_hashes와 아래 정렬된 manifest가 함께 잠근다.
        state_evidence=json.dumps(
            manifest,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ),
        source_date=(
            primary.published_at or primary.disclosed_at or primary.collected_at
        ),
        evidence_support_terms=support_terms,
        claim_slot=claim_slot,
        supporting_source_ids=source_ids,
        supporting_source_identities=source_identities,
        supporting_evidence_hashes=evidence_hashes,
    )
    return replace(fact, evidence_binding=fact_evidence_binding(fact))
