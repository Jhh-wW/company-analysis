"""보완조사 출고 경계에 기존 provenance 정본의 검증 결과를 제공한다."""

from datetime import date

from src.features.provenance.sources import (
    Source,
    SourceKind,
    exact_evidence_text_hash,
    full_typed_source_registry_problem,
    has_valid_provenance_seal,
    is_canonical_official_with_registry,
    source_has_evidence_text,
)
from src.shared.report_evidence.constants import FORMAL_DOCUMENT_SOURCE_KINDS
from src.shared.report_evidence.source_verification import SourceVerification
from src.shared.report_quality.source_identity import document_identity


def verify_supplementary_research_source(
    source: object,
    registry: tuple[object, ...],
    *,
    reference_date: str,
    evidence_text: str,
) -> SourceVerification | None:
    """봉인이나 등록부가 깨졌으면 닫고, 새 키나 별도 도장 규칙은 만들지 않는다."""

    if (
        type(source) is not Source
        or type(registry) is not tuple
        or not registry
        or any(type(item) is not Source for item in registry)
        or type(reference_date) is not str
        or type(evidence_text) is not str
    ):
        return None
    try:
        # 저장 DTO가 타입 힌트를 무시한 값을 담아도 공개 경계 밖으로 예외나
        # 원본 문자열을 내보내지 않는다. 검증 함수의 버그는 이 밖에서 드러난다.
        reference = date.fromisoformat(reference_date)
        if (
            type(source.number) is not int
            or source not in registry
            or len({item.number for item in registry}) != len(registry)
            or len({item.source_id for item in registry}) != len(registry)
            or not source.is_valid
            or not source.is_canonical_valid
            or not has_valid_provenance_seal(source)
        ):
            return None
        identity = document_identity(source)
        if not identity:
            return None
        formal_kind = source.formal_source_kind.strip()
        if formal_kind and (
            formal_kind not in FORMAL_DOCUMENT_SOURCE_KINDS
            or full_typed_source_registry_problem(
                source, registry, reference_date=reference_date,
            )
        ):
            return None
        # 공식 웹의 신원을 확인하는 내부 attester도 등록부에는 남는다.
        # 검증에서 누락시키지는 않되 본문 근거나 독립 공식 자료로 세지 않는다.
        is_body_citation = source.provenance_role == "citation"
        official = bool(
            is_body_citation
            and formal_kind in FORMAL_DOCUMENT_SOURCE_KINDS
            and is_canonical_official_with_registry(source, registry)
        )
        news = bool(
            is_body_citation
            and source.kind is SourceKind.NEWS
            and not formal_kind
            and date.fromisoformat(source.published_at) <= reference
        )
        exact_hash = exact_evidence_text_hash(evidence_text)
        evidence_bound = bool(
            is_body_citation
            and evidence_text.strip()
            and exact_hash in source.exact_evidence_hashes
            and source_has_evidence_text(source, evidence_text)
        )
        return SourceVerification(
            source_id=source.source_id,
            number=source.number,
            document_identity=identity,
            content_sha256=source.document_content_sha256,
            formal_kind=formal_kind,
            exact_evidence_hashes=tuple(source.exact_evidence_hashes),
            official=official,
            news=news,
            evidence_bound=evidence_bound,
        )
    except (AttributeError, TypeError, ValueError):
        return None
