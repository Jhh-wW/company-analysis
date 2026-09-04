"""공식 양사 비교 후보 basis와 봉인 Source 등록부의 공용 결속 검사."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Protocol

from src.shared.comparison_candidate_basis import (
    comparison_candidate_sentence_matches,
    comparison_comparator_source_id,
    comparison_dart_profile_attestation_is_valid,
    comparison_evidence_sha256,
    comparison_source_basis_is_allowed,
    comparison_source_overlap_dimension,
    comparison_source_sentence_has_self_subject,
    parse_comparison_source_basis_v2,
)
from src.shared.company_identity import exact_company_names_equivalent


class ComparisonBasisFact(Protocol):
    legal_entity: str
    comparison_target: str
    comparison_basis: str
    comparison_period: str
    comparison_scope: str
    comparator_source_id: str


class ComparisonBasisSource(Protocol):
    source_id: str
    kind: object
    source_type: str
    publisher: str
    host: str
    url: str
    document_id: str
    location: str
    published_at: str
    disclosed_at: str
    collected_at: str
    evidence_hashes: Sequence[str]
    exact_evidence_hashes: Sequence[str]
    domain_attestation_source_id: str
    domain_attestation_evidence: str
    provenance_role: str


def _source_kind(source: ComparisonBasisSource) -> str:
    return str(getattr(source.kind, "value", source.kind) or "")


def _source_date(source: ComparisonBasisSource) -> str:
    return source.published_at or source.disclosed_at or source.collected_at


def comparison_basis_attester_source_ids(
    facts: Sequence[ComparisonBasisFact],
) -> frozenset[str]:
    """닫힌 v2 basis가 직접 지목한 신원 attester ID 집합."""

    source_ids: set[str] = set()
    for fact in facts:
        payload = parse_comparison_source_basis_v2(fact.comparison_basis)
        if payload is None:
            continue
        source_id = payload["self_attestation_source_id"].strip()
        if source_id:
            source_ids.add(source_id)
    return frozenset(source_ids)


def comparison_basis_v2_problems(
    fact: ComparisonBasisFact,
    sources: Mapping[str, ComparisonBasisSource],
) -> tuple[str, ...]:
    """닫힌 v2 basis를 후보 Source·DART attester·비교사 ID에 역결속한다."""

    payload = parse_comparison_source_basis_v2(fact.comparison_basis)
    if payload is None:
        return ("비교 후보 basis가 company-comparison-source-basis-v2가 아닙니다",)
    problems: list[str] = []
    expected_comparator_id = comparison_comparator_source_id(
        corp_code=payload["candidate_corp_code"],
        comparison_period=fact.comparison_period,
        comparison_scope=fact.comparison_scope,
    )
    if not expected_comparator_id or fact.comparator_source_id != expected_comparator_id:
        problems.append("후보 DART 고유번호가 실제 비교사 Source ID와 다릅니다")

    candidate = sources.get(payload["candidate_source_id"])
    if candidate is None:
        return (*problems, "비교 후보 Source가 등록부에 없습니다")
    expected_metadata = {
        "source_kind": _source_kind(candidate),
        "source_type": candidate.source_type,
        "source_publisher": candidate.publisher,
        "source_host": candidate.host,
        "source_url": candidate.url,
        "source_document_id": candidate.document_id,
        "source_location": candidate.location,
        "source_date": _source_date(candidate),
    }
    for key, expected in expected_metadata.items():
        if payload[key] != expected:
            problems.append(f"비교 후보 basis의 {key}가 봉인 Source와 다릅니다")
    if not exact_company_names_equivalent(candidate.publisher, fact.legal_entity):
        problems.append("비교 후보 Source 발행 법인이 보고서 대상 법인과 다릅니다")
    if payload["evidence_sha256"] not in candidate.evidence_hashes:
        problems.append("비교 후보 문장 해시가 Source 원문 등록부에 없습니다")
    if payload["evidence_exact_sha256"] not in candidate.exact_evidence_hashes:
        problems.append("비교 후보 byte-exact 문장 해시가 Source 등록부에 없습니다")
    if not comparison_source_basis_is_allowed(payload):
        problems.append("비교 후보 basis가 허용된 공식 출처가 아닙니다")
    known_companies = tuple(
        dict.fromkeys(
            (fact.legal_entity, fact.comparison_target, payload["candidate_name"])
        )
    )
    if not comparison_candidate_sentence_matches(
        payload,
        comparison_target=fact.comparison_target,
        evidence_text=payload["evidence_text"],
        self_company=fact.legal_entity,
        known_company_aliases=known_companies,
    ):
        problems.append("비교 대상 법인·관계 표지·문장 해시가 한 공식 원문에 결속되지 않았습니다")
    if not comparison_source_sentence_has_self_subject(
        payload["evidence_text"],
        fact.legal_entity,
    ):
        problems.append("비교 후보 원문에 보고서 대상 법인 주어가 없습니다")
    if comparison_source_overlap_dimension(payload["evidence_text"]) != payload[
        "overlap_dimension"
    ]:
        problems.append("비교 후보 겹침 축이 공식 원문과 다릅니다")

    attester_id = payload["self_attestation_source_id"]
    attester = sources.get(attester_id)
    candidate_requires_domain_attester = _source_kind(candidate) == "기타"
    candidate_domain_binding_is_valid = (
        candidate.domain_attestation_source_id == attester_id
        and candidate.domain_attestation_evidence
        == payload["self_attestation_evidence"]
        if candidate_requires_domain_attester
        else not candidate.domain_attestation_source_id.strip()
        and not candidate.domain_attestation_evidence.strip()
    )
    if (
        attester is None
        or attester.provenance_role != "attestation_only"
        or not candidate_domain_binding_is_valid
        or attester.document_id != payload["self_corp_code"]
        or not exact_company_names_equivalent(attester.publisher, fact.legal_entity)
        or attester.domain_attestation_evidence != payload["self_attestation_evidence"]
        or comparison_evidence_sha256(payload["self_attestation_evidence"])
        not in attester.evidence_hashes
    ):
        problems.append("비교 후보의 직접 DART 법인 attester 결속이 다릅니다")
    if attester is not None and not comparison_dart_profile_attestation_is_valid(
        source_kind=_source_kind(attester),
        source_type=attester.source_type,
        source_publisher=attester.publisher,
        source_host=attester.host,
        source_url=attester.url,
        source_document_id=attester.document_id,
        evidence=payload["self_attestation_evidence"],
        self_corp_code=payload["self_corp_code"],
        self_company=fact.legal_entity,
    ):
        problems.append("비교 후보 attester가 닫힌 OpenDART 기업개황과 다릅니다")
    return tuple(dict.fromkeys(problems))


__all__ = [
    "comparison_basis_attester_source_ids",
    "comparison_basis_v2_problems",
]
