"""공식 수집 ``source_kind``와 의미 칸 소유권의 닫힌 정본.

문서·조회 기록의 종류를 단순 문자열이나 ``dart_``/``official_`` 접두어로
받으면 오타와 새 생산자가 검증 없이 FULL 작성 입력에 들어간다. 이 모듈은
실서비스 formal collector가 낼 수 있는 종류와 각 종류가 주장할 수 있는
수집 슬롯의 상한을 정확 일치로 고정한다.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from types import MappingProxyType
from typing import TYPE_CHECKING, Final
from urllib.parse import urlsplit

from src.shared.official_ir import (
    IR_METADATA_VERIFICATION_VALUES,
    official_ir_time_is_usable,
    reporting_period_is_valid,
    safe_https_attachment_url,
)

from src.shared.report_evidence.constants import (
    FORMAL_ATTEMPT_SOURCE_KINDS,
    FORMAL_DOCUMENT_SOURCE_KINDS,
    OFFICIAL_WEB_SOURCE_KINDS,
    SOURCE_KIND_DART_AUDIT_REPORT,
    SOURCE_KIND_DART_BUSINESS_REPORT,
    SOURCE_KIND_DART_CONSOLIDATED_AUDIT_REPORT,
    SOURCE_KIND_DART_QUARTERLY_REPORT,
    SOURCE_KIND_DART_SEMIANNUAL_REPORT,
    SOURCE_KIND_OFFICIAL_IR_PDF,
    SOURCE_KIND_OFFICIAL_IDENTITY_VERIFIED_WEB_PAGE,
    SOURCE_KIND_OFFICIAL_RECRUIT_PAGE,
    SOURCE_KIND_OFFICIAL_WEB_PAGE,
    SOURCE_KIND_ROBOTS_TXT,
    SourceRequirement,
    SourceTier,
)
from src.shared.report_evidence.policy import (
    REQUIRED_EVIDENCE_SECTION_IDS,
    collector_slots_for,
)
from src.shared.report_evidence.identity_verified_web import (
    canonical_identity_verified_web_url,
    identity_verified_web_expected_trust,
    verified_dart_filing_binding_allows_public_source,
    verified_dart_filing_binding_allows_url,
)
from src.shared.report_evidence.profile_domain_attestation import (
    dart_profile_attestation_allows_source_url,
    dart_profile_attestation_matches_company,
    parse_dart_profile_domain_attestation,
)

if TYPE_CHECKING:
    from src.shared.report_evidence.models import ChapterEvidenceCandidates


class FormalSourceKindContractError(ValueError):
    """공식 수집 종류·장·슬롯 소유권이 닫힌 계약을 어겼다."""


@dataclass(frozen=True)
class FormalPublicSourceMetadata:
    """typed 문서 종류에서 공개 Source로 옮길 정확한 표시·신원 필드."""

    host: str
    source_type: str
    formal_source_kind: str
    identity_binding: str
    domain_attestation_source_id: str
    domain_attestation_evidence: str
    reporting_period: str
    attachment_url: str
    ir_metadata_verification: str
    domain_redirect_verification: str
    domain_redirect_from_host: str
    domain_redirect_to_host: str


def formal_web_public_source_metadata(
    *,
    source_kind: object,
    source_url: object,
    company_name: object,
    identity_binding: object,
    domain_attestation_source_id: object = "",
    domain_attestation_evidence: object = "",
    reporting_period: object = "",
    attachment_url: object = "",
    ir_metadata_verification: object = "",
    domain_redirect_verification: object = "",
    domain_redirect_from_host: object = "",
    domain_redirect_to_host: object = "",
) -> FormalPublicSourceMetadata | None:
    """renderer·public manifest가 공유하는 typed 공식 웹 변환 정본.

    URL이 있다는 모양만 보고 공식 웹이라고 추측하지 않는다. formal 종류가
    닫힌 웹 어휘에 정확히 있고 URL이 exact HTTPS일 때만 host와 사용자용
    자료 분류를 만든다. DART 첨부에서 발견한 교차 도메인은 추가로 canonical
    proof가 현재 법인명·URL과 일치해야 한다.
    """

    if type(source_kind) is not str or source_kind not in OFFICIAL_WEB_SOURCE_KINDS:
        return None
    canonical_url = canonical_identity_verified_web_url(source_url)
    if not canonical_url:
        return None
    values = (
        identity_binding,
        domain_attestation_source_id,
        domain_attestation_evidence,
        reporting_period,
        attachment_url,
        ir_metadata_verification,
        domain_redirect_verification,
        domain_redirect_from_host,
        domain_redirect_to_host,
    )
    if type(company_name) is not str or any(type(value) is not str for value in values):
        return None
    attestation_id = domain_attestation_source_id.strip()
    attestation_evidence = domain_attestation_evidence.strip()
    strict_dart_binding = verified_dart_filing_binding_allows_public_source(
        identity_binding,
        company_name=company_name,
        source_url=canonical_url,
    )
    profile = parse_dart_profile_domain_attestation(attestation_evidence)
    profile_binding = bool(
        attestation_id
        and profile is not None
        and attestation_id == f"dart-company-profile-{profile.corp_code}"
        and dart_profile_attestation_matches_company(
            attestation_evidence,
            corp_code=profile.corp_code,
            company_name=company_name,
        )
        and dart_profile_attestation_allows_source_url(
            attestation_evidence,
            source_url=canonical_url,
            redirect_verification=domain_redirect_verification,
            redirect_from_host=domain_redirect_from_host,
            redirect_to_host=domain_redirect_to_host,
        )
    )
    # strict DART 첨부 proof를 쓰더라도 함께 실린 기업개황 proof가 틀렸다면
    # 조용히 무시하지 않는다. 두 주장 중 하나가 변조된 상태이기 때문이다.
    if bool(attestation_id or attestation_evidence) and not profile_binding:
        return None
    if source_kind == SOURCE_KIND_OFFICIAL_IDENTITY_VERIFIED_WEB_PAGE:
        if not strict_dart_binding:
            return None
    elif not strict_dart_binding and not profile_binding:
        return None
    if source_kind == SOURCE_KIND_OFFICIAL_IR_PDF:
        canonical_attachment = safe_https_attachment_url(attachment_url)
        if (
            ir_metadata_verification.strip() not in IR_METADATA_VERIFICATION_VALUES
            or not reporting_period_is_valid(reporting_period)
            or not canonical_attachment
            or canonical_attachment != attachment_url.strip()
            or canonical_attachment != canonical_url
        ):
            return None
    elif any(
        value.strip()
        for value in (reporting_period, attachment_url, ir_metadata_verification)
    ):
        return None
    host = (urlsplit(canonical_url).hostname or "").casefold().rstrip(".")
    return FormalPublicSourceMetadata(
        host=host,
        source_type=(
            "회사 공식 IR"
            if source_kind == SOURCE_KIND_OFFICIAL_IR_PDF
            else "회사 공식 웹"
        ),
        formal_source_kind=source_kind,
        identity_binding=identity_binding.strip(),
        domain_attestation_source_id=attestation_id,
        domain_attestation_evidence=attestation_evidence,
        reporting_period=reporting_period.strip(),
        attachment_url=attachment_url.strip(),
        ir_metadata_verification=ir_metadata_verification.strip(),
        domain_redirect_verification=domain_redirect_verification.strip(),
        domain_redirect_from_host=domain_redirect_from_host.strip(),
        domain_redirect_to_host=domain_redirect_to_host.strip(),
    )


_ALL_COLLECTOR_SLOT_IDS = frozenset(
    slot_id
    for section_id in REQUIRED_EVIDENCE_SECTION_IDS
    for slot_id in collector_slots_for(section_id)
)
_RECENT_FILING_SLOT_IDS = frozenset(
    (*collector_slots_for("past_changes"), *collector_slots_for("current_challenges"))
)

FORMAL_DOCUMENT_SLOT_IDS_BY_SOURCE_KIND: Final = MappingProxyType(
    {
        SOURCE_KIND_DART_BUSINESS_REPORT: _ALL_COLLECTOR_SLOT_IDS,
        SOURCE_KIND_DART_AUDIT_REPORT: _ALL_COLLECTOR_SLOT_IDS,
        SOURCE_KIND_DART_CONSOLIDATED_AUDIT_REPORT: _ALL_COLLECTOR_SLOT_IDS,
        SOURCE_KIND_DART_SEMIANNUAL_REPORT: _RECENT_FILING_SLOT_IDS,
        SOURCE_KIND_DART_QUARTERLY_REPORT: _RECENT_FILING_SLOT_IDS,
        SOURCE_KIND_OFFICIAL_WEB_PAGE: _ALL_COLLECTOR_SLOT_IDS,
        SOURCE_KIND_OFFICIAL_RECRUIT_PAGE: frozenset(
            collector_slots_for("culture")
        ),
        SOURCE_KIND_OFFICIAL_IR_PDF: _ALL_COLLECTOR_SLOT_IDS,
        SOURCE_KIND_OFFICIAL_IDENTITY_VERIFIED_WEB_PAGE: _ALL_COLLECTOR_SLOT_IDS,
    }
)

FORMAL_ATTEMPT_SLOT_IDS_BY_SOURCE_KIND: Final = MappingProxyType(
    {
        **FORMAL_DOCUMENT_SLOT_IDS_BY_SOURCE_KIND,
        # robots.txt는 한 페이지 종류가 아니라 호스트 전체의 진입 게이트다.
        SOURCE_KIND_ROBOTS_TXT: _ALL_COLLECTOR_SLOT_IDS,
    }
)

# 문서 생산자가 실제로 만들 수 있는 trust/requiredness 조합. source_kind와
# 슬롯만 검사하면 외부 CDN IR(TIER_3/OPTIONAL)을 TIER_1/REQUIRED로 잘못
# 승격해도 필수 근거·문서 수를 채울 수 있다.
FORMAL_DOCUMENT_TRUST_BY_SOURCE_KIND: Final = MappingProxyType(
    {
        SOURCE_KIND_DART_BUSINESS_REPORT: frozenset(
            {(SourceTier.TIER_1_OFFICIAL, SourceRequirement.REQUIRED)}
        ),
        SOURCE_KIND_DART_AUDIT_REPORT: frozenset(
            {(SourceTier.TIER_1_OFFICIAL, SourceRequirement.REQUIRED)}
        ),
        SOURCE_KIND_DART_CONSOLIDATED_AUDIT_REPORT: frozenset(
            {(SourceTier.TIER_1_OFFICIAL, SourceRequirement.OPTIONAL)}
        ),
        SOURCE_KIND_DART_SEMIANNUAL_REPORT: frozenset(
            {(SourceTier.TIER_1_OFFICIAL, SourceRequirement.OPTIONAL)}
        ),
        SOURCE_KIND_DART_QUARTERLY_REPORT: frozenset(
            {(SourceTier.TIER_1_OFFICIAL, SourceRequirement.OPTIONAL)}
        ),
        SOURCE_KIND_OFFICIAL_WEB_PAGE: frozenset(
            {(SourceTier.TIER_1_OFFICIAL, SourceRequirement.REQUIRED)}
        ),
        SOURCE_KIND_OFFICIAL_RECRUIT_PAGE: frozenset(
            {(SourceTier.TIER_1_OFFICIAL, SourceRequirement.REQUIRED)}
        ),
        SOURCE_KIND_OFFICIAL_IR_PDF: frozenset(
            {
                (SourceTier.TIER_1_OFFICIAL, SourceRequirement.REQUIRED),
                (SourceTier.TIER_3_TRUSTED, SourceRequirement.OPTIONAL),
            }
        ),
        SOURCE_KIND_OFFICIAL_IDENTITY_VERIFIED_WEB_PAGE: frozenset(
            {
                (SourceTier.TIER_1_OFFICIAL, SourceRequirement.REQUIRED),
                (SourceTier.TIER_3_TRUSTED, SourceRequirement.OPTIONAL),
            }
        ),
    }
)

# Writer에 실제로 들어갈 수 있는 유일한 등급·필수 여부. 수집 사실을
# 보존하는 OPTIONAL provenance 문서와 글쓰기 입력을 같은 표에서 갈라낸다.
FORMAL_DOCUMENT_WRITER_TRUST_BY_SOURCE_KIND: Final = MappingProxyType(
    {
        SOURCE_KIND_DART_BUSINESS_REPORT: (
            SourceTier.TIER_1_OFFICIAL,
            SourceRequirement.REQUIRED,
        ),
        SOURCE_KIND_DART_AUDIT_REPORT: (
            SourceTier.TIER_1_OFFICIAL,
            SourceRequirement.REQUIRED,
        ),
        SOURCE_KIND_DART_CONSOLIDATED_AUDIT_REPORT: (
            SourceTier.TIER_1_OFFICIAL,
            SourceRequirement.OPTIONAL,
        ),
        SOURCE_KIND_DART_SEMIANNUAL_REPORT: (
            SourceTier.TIER_1_OFFICIAL,
            SourceRequirement.OPTIONAL,
        ),
        SOURCE_KIND_DART_QUARTERLY_REPORT: (
            SourceTier.TIER_1_OFFICIAL,
            SourceRequirement.OPTIONAL,
        ),
        SOURCE_KIND_OFFICIAL_WEB_PAGE: (
            SourceTier.TIER_1_OFFICIAL,
            SourceRequirement.REQUIRED,
        ),
        SOURCE_KIND_OFFICIAL_RECRUIT_PAGE: (
            SourceTier.TIER_1_OFFICIAL,
            SourceRequirement.REQUIRED,
        ),
        SOURCE_KIND_OFFICIAL_IR_PDF: (
            SourceTier.TIER_1_OFFICIAL,
            SourceRequirement.REQUIRED,
        ),
        SOURCE_KIND_OFFICIAL_IDENTITY_VERIFIED_WEB_PAGE: (
            SourceTier.TIER_1_OFFICIAL,
            SourceRequirement.REQUIRED,
        ),
    }
)

if frozenset(FORMAL_DOCUMENT_SLOT_IDS_BY_SOURCE_KIND) != FORMAL_DOCUMENT_SOURCE_KINDS:
    raise FormalSourceKindContractError("공식 문서 종류와 슬롯 소유권 표가 다릅니다")
if frozenset(FORMAL_ATTEMPT_SLOT_IDS_BY_SOURCE_KIND) != FORMAL_ATTEMPT_SOURCE_KINDS:
    raise FormalSourceKindContractError("공식 조회 종류와 슬롯 소유권 표가 다릅니다")
if frozenset(FORMAL_DOCUMENT_TRUST_BY_SOURCE_KIND) != FORMAL_DOCUMENT_SOURCE_KINDS:
    raise FormalSourceKindContractError("공식 문서 종류와 신뢰·필수 조합 표가 다릅니다")
if (
    frozenset(FORMAL_DOCUMENT_WRITER_TRUST_BY_SOURCE_KIND)
    != FORMAL_DOCUMENT_SOURCE_KINDS
):
    raise FormalSourceKindContractError("공식 문서 종류와 Writer 자격 표가 다릅니다")


def _slots_for_source_kind(
    source_kind: str,
    *,
    ownership: object,
    label: str,
) -> frozenset[str]:
    if type(source_kind) is not str:
        raise FormalSourceKindContractError(f"{label} 종류는 문자열이어야 합니다")
    try:
        return ownership[source_kind]  # type: ignore[index]
    except KeyError as error:
        raise FormalSourceKindContractError(
            f"등록되지 않은 {label} 종류입니다: {source_kind!r}"
        ) from error


def document_slots_for_formal_source_kind(source_kind: str) -> frozenset[str]:
    """문서 종류가 직접 주장할 수 있는 수집 슬롯을 정확 일치로 돌려준다."""

    return _slots_for_source_kind(
        source_kind,
        ownership=FORMAL_DOCUMENT_SLOT_IDS_BY_SOURCE_KIND,
        label="공식 문서",
    )


def attempt_slots_for_formal_source_kind(source_kind: str) -> frozenset[str]:
    """조회 종류가 관측할 수 있는 수집 슬롯을 정확 일치로 돌려준다."""

    return _slots_for_source_kind(
        source_kind,
        ownership=FORMAL_ATTEMPT_SLOT_IDS_BY_SOURCE_KIND,
        label="공식 조회",
    )


def _validate_document_trust(document: object) -> None:
    source_kind = document.source_kind  # type: ignore[attr-defined]
    allowed = FORMAL_DOCUMENT_TRUST_BY_SOURCE_KIND[source_kind]
    actual = (document.source_tier, document.requirement)  # type: ignore[attr-defined]
    if actual not in allowed:
        raise FormalSourceKindContractError(
            "공식 문서 종류의 출처 등급·필수 여부 조합이 생산자 계약과 다릅니다"
        )
    if source_kind == SOURCE_KIND_OFFICIAL_IDENTITY_VERIFIED_WEB_PAGE:
        expected = identity_verified_web_expected_trust(document.identity_binding)  # type: ignore[attr-defined]
        if actual != expected:
            raise FormalSourceKindContractError(
                "신원검증 공식 웹의 공시 proof와 출처 등급·필수 여부가 다릅니다"
            )
        return
    if source_kind != SOURCE_KIND_OFFICIAL_IR_PDF:
        return

    try:
        document_host = (
            urlsplit(document.canonical_url).hostname or ""  # type: ignore[attr-defined]
        ).casefold().rstrip(".")
    except ValueError as error:
        raise FormalSourceKindContractError(
            "공식 IR 문서 URL의 host를 확인할 수 없습니다"
        ) from error
    publisher_host = str(document.publisher).casefold().rstrip(".")  # type: ignore[attr-defined]
    is_external_attachment = not document_host or document_host != publisher_host
    expected = (
        (SourceTier.TIER_3_TRUSTED, SourceRequirement.OPTIONAL)
        if is_external_attachment
        else (SourceTier.TIER_1_OFFICIAL, SourceRequirement.REQUIRED)
    )
    if actual != expected:
        raise FormalSourceKindContractError(
            "외부 IR 첨부의 출처 등급·필수 여부가 공식 host 문서처럼 승격됐습니다"
        )


def _enum_value(value: object) -> object:
    return getattr(value, "value", value)


def formal_source_writer_ineligibility_reason(
    *,
    source_kind: object,
    source_tier: object,
    requirement: object,
    canonical_url: object,
    publisher: object,
    published_on: object,
    collected_at: object,
    identity_binding: object,
    domain_attestation_source_id: object = "",
    domain_attestation_evidence: object = "",
    reporting_period: object = "",
    attachment_url: object = "",
    ir_metadata_verification: object = "",
    domain_redirect_verification: object = "",
    domain_redirect_from_host: object = "",
    domain_redirect_to_host: object = "",
) -> str:
    """formal 문서가 Writer 입력이 될 수 없는 첫 닫힌 사유를 돌려준다.

    문서가 수집됐다는 사실과 Writer 자격은 다르다. 특히 IR은 공식 host에
    있어도 발행일·보고기간·exact 첨부 결속이 하나라도 없으면 Writer가
    아니며, 호출자는 이 사유를 provenance-only 차선에 보존한다.
    """

    if type(source_kind) is not str or source_kind not in FORMAL_DOCUMENT_SOURCE_KINDS:
        return "formal_writer_source_kind_unregistered"
    if source_kind == SOURCE_KIND_OFFICIAL_IR_PDF:
        canonical_attachment = safe_https_attachment_url(str(attachment_url or ""))
        canonical_source = safe_https_attachment_url(str(canonical_url or ""))
        reference_date = str(collected_at or "").strip()[:10]
        if (
            str(ir_metadata_verification or "").strip()
            not in IR_METADATA_VERIFICATION_VALUES
            or not reporting_period_is_valid(str(reporting_period or ""))
            or not canonical_attachment
            or canonical_attachment != canonical_source
            or not official_ir_time_is_usable(
                published_at=str(published_on or ""),
                reporting_period=str(reporting_period or ""),
                reference_date=reference_date,
            )
        ):
            return "official_ir_writer_metadata_incomplete"
    elif any(
        str(value or "").strip()
        for value in (reporting_period, attachment_url, ir_metadata_verification)
    ):
        return "non_ir_writer_has_ir_metadata"

    expected_tier, expected_requirement = (
        FORMAL_DOCUMENT_WRITER_TRUST_BY_SOURCE_KIND[source_kind]
    )
    if (
        _enum_value(source_tier) != expected_tier.value
        or _enum_value(requirement) != expected_requirement.value
    ):
        return "formal_writer_trust_not_eligible"

    if source_kind == SOURCE_KIND_OFFICIAL_IDENTITY_VERIFIED_WEB_PAGE:
        if not verified_dart_filing_binding_allows_url(
            identity_binding,
            source_url=canonical_url,
        ):
            return "identity_verified_web_writer_proof_incomplete"
    return ""


def formal_document_writer_ineligibility_reason(document: object) -> str:
    """문서 DTO 종류와 무관하게 위 Writer 정본을 적용한다."""

    return formal_source_writer_ineligibility_reason(
        source_kind=getattr(document, "source_kind", ""),
        source_tier=getattr(document, "source_tier", None),
        requirement=getattr(document, "requirement", None),
        canonical_url=getattr(document, "canonical_url", ""),
        publisher=getattr(document, "publisher", ""),
        published_on=getattr(document, "published_on", ""),
        collected_at=getattr(document, "collected_at", ""),
        identity_binding=getattr(document, "identity_binding", ""),
        domain_attestation_source_id=getattr(
            document, "domain_attestation_source_id", ""
        ),
        domain_attestation_evidence=getattr(
            document, "domain_attestation_evidence", ""
        ),
        reporting_period=getattr(document, "reporting_period", ""),
        attachment_url=getattr(document, "attachment_url", ""),
        ir_metadata_verification=getattr(
            document, "ir_metadata_verification", ""
        ),
        domain_redirect_verification=getattr(
            document, "domain_redirect_verification", ""
        ),
        domain_redirect_from_host=getattr(
            document, "domain_redirect_from_host", ""
        ),
        domain_redirect_to_host=getattr(document, "domain_redirect_to_host", ""),
    )


def formal_document_is_writer_eligible(document: object) -> bool:
    """수집기·mapping·장 선택·runtime이 공유하는 Writer 자격 정본."""

    return not formal_document_writer_ineligibility_reason(document)


def validate_formal_candidate_sources(
    candidates: Iterable["ChapterEvidenceCandidates"],
) -> None:
    """FULL 공식 후보의 문서·조각·조회 종류와 슬롯 소유권을 검증한다."""

    for candidate in candidates:
        documents_by_id = {document.document_id: document for document in candidate.documents}
        for document in candidate.documents:
            document_slots_for_formal_source_kind(document.source_kind)
            _validate_document_trust(document)

        for fragment in candidate.fragments:
            document = documents_by_id.get(fragment.document_id)
            if document is None:
                # ChapterEvidenceCandidates도 막지만 이 계약의 실패 의미를 분명히 둔다.
                raise FormalSourceKindContractError(
                    "공식 근거 조각의 원본 문서가 장 후보에 없습니다"
                )
            allowed_slots = document_slots_for_formal_source_kind(document.source_kind)
            if not formal_document_is_writer_eligible(document):
                raise FormalSourceKindContractError(
                    "낮은 신뢰의 외부 문서는 필수 의미 칸 조각이나 Writer 입력이 될 수 없습니다"
                )
            claimed_slots = frozenset(fragment.covered_slot_ids)
            if not claimed_slots <= allowed_slots:
                raise FormalSourceKindContractError(
                    "공식 문서 종류가 소유하지 않은 의미 칸을 주장했습니다"
                )
            if any(
                not slot_id.startswith(f"{candidate.section_id}:")
                for slot_id in claimed_slots
            ):
                raise FormalSourceKindContractError(
                    "공식 근거 조각의 장과 의미 칸이 다릅니다"
                )

        for attempt in candidate.attempts:
            allowed_slots = attempt_slots_for_formal_source_kind(attempt.source_kind)
            if not frozenset(attempt.slot_ids) <= allowed_slots:
                raise FormalSourceKindContractError(
                    "공식 조회 종류가 소유하지 않은 의미 칸을 주장했습니다"
                )
