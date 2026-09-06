"""이름 후보를 기존 정식 공시 신원에 묶는 typed 조각 변환."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Final

from src.shared.report_evidence.constants import (
    FORMAL_DOCUMENT_SOURCE_KINDS,
    SOURCE_KIND_DART_AUDIT_REPORT,
    SOURCE_KIND_DART_BUSINESS_REPORT,
    SOURCE_KIND_DART_CONSOLIDATED_AUDIT_REPORT,
    SOURCE_KIND_DART_QUARTERLY_REPORT,
    SOURCE_KIND_DART_SEMIANNUAL_REPORT,
)
from src.shared.name_fragments.constants import (
    NAME_KIND_LABELS,
    compose_name_location,
)
from src.shared.report_evidence.source_kind_policy import (
    FormalSourceKindContractError,
    document_slots_for_formal_source_kind,
)
from src.shared.report_generation.models import exact_text_sha256

from .constants import (
    MAX_NAME_FRAGMENTS_PER_FILING,
    MAX_NAME_FRAGMENTS_PER_KIND,
    NAME_FRAGMENT_KIND_ORDER,
)
from .models import NameCandidate


NAME_FRAGMENT_SECTION_ID: Final[str] = "portfolio"
NAME_FRAGMENT_SLOT_ID: Final[str] = "portfolio:product_role"

_RAW_SECTION_IDS_KEY: Final[str] = "_evidence_section_ids"
_RAW_DOCUMENT_IDENTITY_KEY: Final[str] = "_evidence_document_identity"
_RAW_DOCUMENT_CONTENT_SHA256_KEY: Final[str] = (
    "_evidence_document_content_sha256"
)
_RAW_ORIGIN_FRAGMENT_IDS_KEY: Final[str] = "_evidence_origin_fragment_ids"
_RAW_SLOT_IDS_KEY: Final[str] = "_evidence_slot_ids"
_RAW_COMPANY_ID_KEY: Final[str] = "company_id"
_RAW_IDENTITY_BINDING_KEY: Final[str] = "_evidence_identity_binding"
_RAW_PUBLISHER_KEY: Final[str] = "_evidence_publisher"
_RAW_COLLECTED_ON_KEY: Final[str] = "_evidence_collected_on"
_RAW_DOMAIN_ATTESTATION_SOURCE_ID_KEY: Final[str] = (
    "_evidence_domain_attestation_source_id"
)
_RAW_DOMAIN_ATTESTATION_EVIDENCE_KEY: Final[str] = (
    "_evidence_domain_attestation_evidence"
)
_RAW_REPORTING_PERIOD_KEY: Final[str] = "_evidence_reporting_period"
_RAW_ATTACHMENT_URL_KEY: Final[str] = "_evidence_attachment_url"
_RAW_IR_METADATA_VERIFICATION_KEY: Final[str] = (
    "_evidence_ir_metadata_verification"
)
_RAW_DOMAIN_REDIRECT_VERIFICATION_KEY: Final[str] = (
    "_evidence_domain_redirect_verification"
)
_RAW_DOMAIN_REDIRECT_FROM_HOST_KEY: Final[str] = (
    "_evidence_domain_redirect_from_host"
)
_RAW_DOMAIN_REDIRECT_TO_HOST_KEY: Final[str] = (
    "_evidence_domain_redirect_to_host"
)

_TYPED_METADATA_KEYS: Final[frozenset[str]] = frozenset(
    {
        _RAW_SECTION_IDS_KEY,
        _RAW_DOCUMENT_IDENTITY_KEY,
        _RAW_DOCUMENT_CONTENT_SHA256_KEY,
        _RAW_ORIGIN_FRAGMENT_IDS_KEY,
        _RAW_SLOT_IDS_KEY,
        _RAW_COMPANY_ID_KEY,
        _RAW_IDENTITY_BINDING_KEY,
        _RAW_PUBLISHER_KEY,
        _RAW_COLLECTED_ON_KEY,
        _RAW_DOMAIN_ATTESTATION_SOURCE_ID_KEY,
        _RAW_DOMAIN_ATTESTATION_EVIDENCE_KEY,
        _RAW_REPORTING_PERIOD_KEY,
        _RAW_ATTACHMENT_URL_KEY,
        _RAW_IR_METADATA_VERIFICATION_KEY,
        _RAW_DOMAIN_REDIRECT_VERIFICATION_KEY,
        _RAW_DOMAIN_REDIRECT_FROM_HOST_KEY,
        _RAW_DOMAIN_REDIRECT_TO_HOST_KEY,
    }
)
_REQUIRED_NONEMPTY_PROVENANCE_KEYS: Final[tuple[str, ...]] = (
    "종류",
    "출처",
    "문서ID",
    _RAW_COMPANY_ID_KEY,
    _RAW_DOCUMENT_IDENTITY_KEY,
    _RAW_DOCUMENT_CONTENT_SHA256_KEY,
    _RAW_IDENTITY_BINDING_KEY,
    _RAW_PUBLISHER_KEY,
    _RAW_COLLECTED_ON_KEY,
)
_COPIED_PROVENANCE_KEYS: Final[tuple[str, ...]] = (
    "종류",
    "출처",
    "문서ID",
    "문서명",
    "문서일",
    *tuple(sorted(_TYPED_METADATA_KEYS)),
)
_DART_SOURCE_KINDS: Final[frozenset[str]] = frozenset(
    {
        SOURCE_KIND_DART_BUSINESS_REPORT,
        SOURCE_KIND_DART_AUDIT_REPORT,
        SOURCE_KIND_DART_CONSOLIDATED_AUDIT_REPORT,
        SOURCE_KIND_DART_SEMIANNUAL_REPORT,
        SOURCE_KIND_DART_QUARTERLY_REPORT,
    }
)


def _filing_document_id(filing_meta: object) -> str:
    if isinstance(filing_meta, Mapping):
        return str(
            filing_meta.get("rcept_no")
            or filing_meta.get("rceptNo")
            or filing_meta.get("document_id")
            or ""
        ).strip()
    return str(getattr(filing_meta, "document_id", "") or "").strip()


def _fragment_mappings(
    typed_fragments: Iterable[Mapping[str, object]] | Mapping[object, object],
) -> tuple[Mapping[str, object], ...]:
    if isinstance(typed_fragments, Mapping):
        if "종류" in typed_fragments:
            return (typed_fragments,)
        values = typed_fragments.values()
    else:
        values = typed_fragments
    return tuple(item for item in values if isinstance(item, Mapping))


def _exact_nonempty_text(raw: Mapping[str, object], key: str) -> bool:
    value = raw.get(key)
    return type(value) is str and bool(value) and value == value.strip()


def _supports_name_slot(source_kind: str) -> bool:
    if (
        source_kind not in FORMAL_DOCUMENT_SOURCE_KINDS
        or source_kind not in _DART_SOURCE_KINDS
    ):
        return False
    try:
        return NAME_FRAGMENT_SLOT_ID in document_slots_for_formal_source_kind(
            source_kind
        )
    except FormalSourceKindContractError:
        return False


def _typed_template_for_filing(
    *,
    filing_meta: object,
    corp_id: str,
    typed_fragments: Iterable[Mapping[str, object]] | Mapping[object, object],
) -> Mapping[str, object] | None:
    document_id = _filing_document_id(filing_meta)
    if not document_id or not corp_id:
        return None
    for raw in _fragment_mappings(typed_fragments):
        if not _TYPED_METADATA_KEYS <= set(raw):
            continue
        if any(
            not _exact_nonempty_text(raw, key)
            for key in _REQUIRED_NONEMPTY_PROVENANCE_KEYS
        ):
            continue
        source_kind = str(raw["종류"])
        source_document_id = str(raw["문서ID"])
        if (
            raw[_RAW_COMPANY_ID_KEY] != corp_id
            or source_document_id.rpartition(":")[2] != document_id
            or not _supports_name_slot(source_kind)
        ):
            continue
        return raw
    return None


def formal_source_kind_for_filing(
    *,
    filing_meta: object,
    corp_id: str,
    typed_fragments: Iterable[Mapping[str, object]] | Mapping[object, object],
) -> str:
    """같은 공시의 재사용 가능한 typed 조각이 선언한 정식 종류를 돌려준다."""

    template = _typed_template_for_filing(
        filing_meta=filing_meta,
        corp_id=corp_id,
        typed_fragments=typed_fragments,
    )
    return str(template["종류"]) if template is not None else ""


def _budgeted_candidates(
    candidates: Iterable[NameCandidate],
) -> tuple[NameCandidate, ...]:
    """예산 안에서 종류를 골고루 담는다.

    한 종류가 예산을 다 먹으면 다른 종류가 한 건도 못 들어간다. 실측(상장
    엔터사)에서 제품 후보가 먼저 나와, 종류별 상한이 없으면 대표 IP가 0건이 됐다.
    그래서 ①정해진 종류 순서로 종류별 상한까지 담고, ②그래도 자리가 남으면
    같은 순서로 남은 후보를 마저 담는다(한 종류만 있는 회사도 예산을 쓴다).
    """

    by_kind: dict[str, list[NameCandidate]] = {}
    for candidate in candidates:
        by_kind.setdefault(candidate.subject_kind, []).append(candidate)
    ordered_kinds = [kind for kind in NAME_FRAGMENT_KIND_ORDER if kind in by_kind]
    ordered_kinds += [kind for kind in by_kind if kind not in NAME_FRAGMENT_KIND_ORDER]

    taken: list[NameCandidate] = []
    used: dict[str, int] = {}
    for limit in (MAX_NAME_FRAGMENTS_PER_KIND, MAX_NAME_FRAGMENTS_PER_FILING):
        for kind in ordered_kinds:
            for candidate in by_kind[kind][used.get(kind, 0) : limit]:
                if len(taken) >= MAX_NAME_FRAGMENTS_PER_FILING:
                    return tuple(taken)
                taken.append(candidate)
                used[kind] = used.get(kind, 0) + 1
    return tuple(taken)


def _origin_fragment_id(candidate: NameCandidate, index: int) -> str:
    material = "\x1f".join(
        (
            candidate.subject_kind,
            candidate.name,
            candidate.excerpt_sha256,
            str(index),
        )
    )
    return f"product-name:{exact_text_sha256(material)}"


def name_candidate_fragments(
    candidates: Iterable[NameCandidate],
    *,
    filing_meta: object,
    corp_id: str,
    typed_fragments: Iterable[Mapping[str, object]] | Mapping[object, object],
) -> list[dict[str, object]]:
    """이름 후보를 같은 공시의 검증된 문서 신원에 묶은 raw 조각으로 만든다.

    문서 신원은 새로 계산하지 않는다. 같은 접수번호·회사인 typed 조각이 이미
    가진 provenance만 복사하며, 그런 조각이 없으면 빈 목록을 돌려준다.
    """

    template = _typed_template_for_filing(
        filing_meta=filing_meta,
        corp_id=corp_id,
        typed_fragments=typed_fragments,
    )
    if template is None:
        return []
    source_kind = str(template["종류"])
    limited = _budgeted_candidates(candidates)
    made: list[dict[str, object]] = []
    for index, candidate in enumerate(limited, start=1):
        label = NAME_KIND_LABELS.get(candidate.subject_kind)
        if (
            label is None
            or candidate.source_kind != source_kind
            or not candidate.location.strip()
            or not candidate.excerpt.strip()
            or candidate.excerpt != candidate.excerpt.strip()
            or candidate.name not in candidate.excerpt
            or exact_text_sha256(candidate.excerpt) != candidate.excerpt_sha256
        ):
            continue
        raw = {
            key: template[key]
            for key in _COPIED_PROVENANCE_KEYS
            if key in template
        }
        raw.update(
            {
                "원문": candidate.excerpt,
                "원문위치": compose_name_location(
                    candidate.location,
                    candidate.subject_kind,
                    candidate.name,
                ),
                _RAW_SECTION_IDS_KEY: (NAME_FRAGMENT_SECTION_ID,),
                _RAW_SLOT_IDS_KEY: (NAME_FRAGMENT_SLOT_ID,),
                _RAW_ORIGIN_FRAGMENT_IDS_KEY: (
                    _origin_fragment_id(candidate, index),
                ),
            }
        )
        made.append(raw)
    return made


__all__ = [
    "NAME_FRAGMENT_SECTION_ID",
    "NAME_FRAGMENT_SLOT_ID",
    "formal_source_kind_for_filing",
    "name_candidate_fragments",
]
