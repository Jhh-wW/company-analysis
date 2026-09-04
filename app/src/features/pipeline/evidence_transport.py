"""수집 조각을 FULL 장별 근거 packet으로 옮기는 순수 경계.

legacy 조각은 shared 종류 정본의 정확 소유 장만 따른다. typed 조각은 수집기가
봉인한 장·슬롯·문서 신원을 그대로 따르며, ``종류``로 장을 다시 추측하지 않는다.
어느 입력 계약이든 손상되면 작성기 호출 전에 닫힌 진단 코드로 실패한다.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any, Final

from src.features.composer.constants import (
    DART_DOCUMENT_HOST,
    DART_DOCUMENT_URL_TEMPLATE,
    DART_FINANCIAL_API_DOCUMENT_ID,
    DART_FINANCIAL_API_HOST,
    DART_FINANCIAL_API_PREFIX,
    DART_FINANCIAL_API_URL,
    SECTION_IDS,
)
from src.features.composer.port import (
    CollectedFragment,
    SectionEvidencePacket,
    SectionEvidencePacketSet,
)
from src.shared.final_gate_diagnostics import (
    FINAL_GATE_DETAIL_PREFLIGHT_PACKET_INVALID,
    FINAL_GATE_DETAIL_PREFLIGHT_UNREGISTERED_FRAGMENT_KIND,
)
from src.shared.report_claim_policy import CLAIM_SLOTS_BY_SECTION
from src.shared.report_evidence.legacy_fragment_kinds import (
    LEGACY_FRAGMENT_KINDS,
    sections_for_legacy_fragment_kind,
)
from src.shared.report_evidence.constants import (
    FORMAL_DOCUMENT_SOURCE_KINDS,
    OFFICIAL_WEB_SOURCE_KINDS,
    SOURCE_KIND_OFFICIAL_IR_PDF,
    SOURCE_KIND_OFFICIAL_IDENTITY_VERIFIED_WEB_PAGE,
)
from src.shared.report_evidence.identity_verified_web import (
    verified_dart_filing_binding_allows_url,
)
from src.shared.report_evidence.profile_domain_attestation import (
    dart_profile_attestation_allows_source_url,
    parse_dart_profile_domain_attestation,
)
from src.shared.report_evidence.source_kind_policy import (
    FORMAL_DOCUMENT_WRITER_TRUST_BY_SOURCE_KIND,
    FormalSourceKindContractError,
    document_slots_for_formal_source_kind,
    formal_source_writer_ineligibility_reason,
)
from src.shared.report_quality.source_identity import (
    bind_declared_document_identity_to_url,
    collected_document_identity,
    document_identity_from_parts,
)


# typed→legacy 임시 adapter가 raw 조각에 붙이는 닫힌 메타데이터 열쇠. 호출자는
# 문자열을 복사하지 않고 이 상수를 import해야 한다.
RAW_EVIDENCE_SECTION_IDS_KEY: Final[str] = "_evidence_section_ids"
RAW_EVIDENCE_DOCUMENT_IDENTITY_KEY: Final[str] = "_evidence_document_identity"
RAW_EVIDENCE_DOCUMENT_CONTENT_SHA256_KEY: Final[str] = (
    "_evidence_document_content_sha256"
)
RAW_EVIDENCE_ORIGIN_FRAGMENT_IDS_KEY: Final[str] = "_evidence_origin_fragment_ids"
RAW_EVIDENCE_SLOT_IDS_KEY: Final[str] = "_evidence_slot_ids"
RAW_EVIDENCE_COMPANY_ID_KEY: Final[str] = "company_id"
RAW_EVIDENCE_IDENTITY_BINDING_KEY: Final[str] = "_evidence_identity_binding"
RAW_EVIDENCE_PUBLISHER_KEY: Final[str] = "_evidence_publisher"
RAW_EVIDENCE_COLLECTED_ON_KEY: Final[str] = "_evidence_collected_on"
RAW_EVIDENCE_DOMAIN_ATTESTATION_SOURCE_ID_KEY: Final[str] = (
    "_evidence_domain_attestation_source_id"
)
RAW_EVIDENCE_DOMAIN_ATTESTATION_EVIDENCE_KEY: Final[str] = (
    "_evidence_domain_attestation_evidence"
)
RAW_EVIDENCE_REPORTING_PERIOD_KEY: Final[str] = "_evidence_reporting_period"
RAW_EVIDENCE_ATTACHMENT_URL_KEY: Final[str] = "_evidence_attachment_url"
RAW_EVIDENCE_IR_METADATA_VERIFICATION_KEY: Final[str] = (
    "_evidence_ir_metadata_verification"
)
RAW_EVIDENCE_DOMAIN_REDIRECT_VERIFICATION_KEY: Final[str] = (
    "_evidence_domain_redirect_verification"
)
RAW_EVIDENCE_DOMAIN_REDIRECT_FROM_HOST_KEY: Final[str] = (
    "_evidence_domain_redirect_from_host"
)
RAW_EVIDENCE_DOMAIN_REDIRECT_TO_HOST_KEY: Final[str] = (
    "_evidence_domain_redirect_to_host"
)

TYPED_TRANSPORT_KIND_PREFIX: Final[str] = "typed-evidence-v3:"

_RCEPT_NO_RE: Final[re.Pattern[str]] = re.compile(r"[0-9]{14}")
_COMPANY_ID_RE: Final[re.Pattern[str]] = re.compile(r"[0-9]{8}")
_SHA256_RE: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}")
_TYPED_REQUIRED_KEYS: Final[frozenset[str]] = frozenset(
    {
        RAW_EVIDENCE_SECTION_IDS_KEY,
        RAW_EVIDENCE_DOCUMENT_IDENTITY_KEY,
        RAW_EVIDENCE_DOCUMENT_CONTENT_SHA256_KEY,
        RAW_EVIDENCE_ORIGIN_FRAGMENT_IDS_KEY,
        RAW_EVIDENCE_SLOT_IDS_KEY,
        RAW_EVIDENCE_COMPANY_ID_KEY,
        RAW_EVIDENCE_IDENTITY_BINDING_KEY,
        RAW_EVIDENCE_PUBLISHER_KEY,
        RAW_EVIDENCE_COLLECTED_ON_KEY,
        RAW_EVIDENCE_DOMAIN_ATTESTATION_SOURCE_ID_KEY,
        RAW_EVIDENCE_DOMAIN_ATTESTATION_EVIDENCE_KEY,
        RAW_EVIDENCE_REPORTING_PERIOD_KEY,
        RAW_EVIDENCE_ATTACHMENT_URL_KEY,
        RAW_EVIDENCE_IR_METADATA_VERIFICATION_KEY,
        RAW_EVIDENCE_DOMAIN_REDIRECT_VERIFICATION_KEY,
        RAW_EVIDENCE_DOMAIN_REDIRECT_FROM_HOST_KEY,
        RAW_EVIDENCE_DOMAIN_REDIRECT_TO_HOST_KEY,
    }
)
_SLOT_SECTION_OF: Final[dict[str, str]] = {
    slot_id: section_id
    for section_id, slot_ids in CLAIM_SLOTS_BY_SECTION.items()
    for slot_id in slot_ids
}


class EvidenceTransportError(ValueError):
    """근거 transport가 작성기 전에 닫힌 사유로 입력을 거절했다."""

    def __init__(self, message: str, *, detail_code: str) -> None:
        super().__init__(message)
        self.detail_code = detail_code


def _packet_invalid(message: str) -> EvidenceTransportError:
    return EvidenceTransportError(
        message, detail_code=FINAL_GATE_DETAIL_PREFLIGHT_PACKET_INVALID
    )


def _unregistered_kind() -> EvidenceTransportError:
    return EvidenceTransportError(
        "등록되지 않은 수집 조각 종류입니다",
        detail_code=FINAL_GATE_DETAIL_PREFLIGHT_UNREGISTERED_FRAGMENT_KIND,
    )


def _require_text(value: object, *, field: str, allow_empty: bool = False) -> str:
    if type(value) is not str or value != value.strip() or (not value and not allow_empty):
        raise _packet_invalid(f"근거 조각의 {field} 형식이 올바르지 않습니다")
    return value


def _optional_text(raw: Mapping[str, object], key: str) -> str:
    value = raw.get(key, "")
    if type(value) is not str:
        raise _packet_invalid(f"근거 조각의 {key} 형식이 올바르지 않습니다")
    return value.strip()


def _require_string_sequence(value: object, *, field: str) -> tuple[str, ...]:
    if type(value) not in (tuple, list) or not value:
        raise _packet_invalid(f"typed 근거의 {field}는 비어 있지 않은 tuple/list여야 합니다")
    items = tuple(value)
    if any(type(item) is not str or not item or item != item.strip() for item in items):
        raise _packet_invalid(f"typed 근거의 {field} 항목 형식이 올바르지 않습니다")
    if len(items) != len(set(items)):
        raise _packet_invalid(f"typed 근거의 {field}에 중복이 있습니다")
    return items


def _document_identity_is_valid(value: str) -> bool:
    if value.startswith("url:"):
        url = value.removeprefix("url:")
        return bool(url) and document_identity_from_parts(url=url) == value
    if value.startswith("document:"):
        parts = value.split(":", 2)
        if len(parts) != 3:
            return False
        _prefix, host, document_id = parts
        return bool(host and document_id) and document_identity_from_parts(
            document_id=document_id, host=host
        ) == value
    return False


def _legacy_document_identity(
    raw: Mapping[str, object],
    *,
    text: str,
    source_url: str,
    filing_meta: Any,
) -> str:
    fragment_document_id = _optional_text(raw, "문서ID")
    if text.startswith(DART_FINANCIAL_API_PREFIX):
        return document_identity_from_parts(
            document_id=DART_FINANCIAL_API_DOCUMENT_ID,
            host=DART_FINANCIAL_API_HOST,
            url=DART_FINANCIAL_API_URL,
        )
    if source_url:
        return document_identity_from_parts(url=source_url)
    if _RCEPT_NO_RE.fullmatch(fragment_document_id):
        return document_identity_from_parts(
            document_id=fragment_document_id,
            host=DART_DOCUMENT_HOST,
            url=DART_DOCUMENT_URL_TEMPLATE.format(document_id=fragment_document_id),
        )
    filing_document_id = str(getattr(filing_meta, "document_id", "") or "").strip()
    if filing_document_id:
        return document_identity_from_parts(
            document_id=filing_document_id,
            host=DART_DOCUMENT_HOST,
            url=DART_DOCUMENT_URL_TEMPLATE.format(document_id=filing_document_id),
        )
    return ""


def _typed_metadata(
    raw: Mapping[str, object],
    *,
    corp_id: str,
    source_kind: str,
    source_url: str,
) -> tuple[
    tuple[str, ...],
    tuple[str, ...],
    tuple[str, ...],
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    str,
    str,
]:
    if source_kind not in FORMAL_DOCUMENT_SOURCE_KINDS:
        raise _unregistered_kind()
    missing = _TYPED_REQUIRED_KEYS - set(raw)
    if missing:
        raise _packet_invalid("typed 근거의 필수 transport 메타데이터가 빠졌습니다")
    raw_company_id = _require_text(
        raw[RAW_EVIDENCE_COMPANY_ID_KEY], field=RAW_EVIDENCE_COMPANY_ID_KEY
    )
    if raw_company_id != corp_id:
        raise _packet_invalid("다른 회사의 typed 근거를 섞을 수 없습니다")

    section_ids = _require_string_sequence(
        raw[RAW_EVIDENCE_SECTION_IDS_KEY], field=RAW_EVIDENCE_SECTION_IDS_KEY
    )
    if any(section_id not in SECTION_IDS for section_id in section_ids):
        raise _packet_invalid("typed 근거에 알 수 없는 장 식별자가 있습니다")
    ordered_section_ids = tuple(
        section_id for section_id in SECTION_IDS if section_id in section_ids
    )

    slot_ids = _require_string_sequence(
        raw[RAW_EVIDENCE_SLOT_IDS_KEY], field=RAW_EVIDENCE_SLOT_IDS_KEY
    )
    if any(slot_id not in _SLOT_SECTION_OF for slot_id in slot_ids):
        raise _packet_invalid("typed 근거에 알 수 없는 의미 칸이 있습니다")
    try:
        allowed_slot_ids = document_slots_for_formal_source_kind(source_kind)
    except FormalSourceKindContractError as error:
        # 위의 exact source-kind 검사와 함께 유지하는 방어 심층화다. 정본 두
        # 부분이 나중에 어긋나도 등록되지 않은 종류를 packet에 넣지 않는다.
        raise _unregistered_kind() from error
    if not set(slot_ids) <= allowed_slot_ids:
        raise _packet_invalid(
            "typed 근거의 문서 종류가 소유하지 않은 의미 칸을 주장했습니다"
        )
    slot_sections = {_SLOT_SECTION_OF[slot_id] for slot_id in slot_ids}
    if slot_sections != set(ordered_section_ids):
        raise _packet_invalid("typed 근거의 장과 의미 칸 소유권이 일치하지 않습니다")
    ordered_slot_ids = tuple(sorted(slot_ids))

    origin_fragment_ids = _require_string_sequence(
        raw[RAW_EVIDENCE_ORIGIN_FRAGMENT_IDS_KEY],
        field=RAW_EVIDENCE_ORIGIN_FRAGMENT_IDS_KEY,
    )
    ordered_origin_ids = tuple(sorted(origin_fragment_ids))

    document_identity = _require_text(
        raw[RAW_EVIDENCE_DOCUMENT_IDENTITY_KEY],
        field=RAW_EVIDENCE_DOCUMENT_IDENTITY_KEY,
    )
    if not _document_identity_is_valid(document_identity):
        raise _packet_invalid("typed 근거의 문서 신원이 올바르지 않습니다")
    document_content_sha256 = _require_text(
        raw[RAW_EVIDENCE_DOCUMENT_CONTENT_SHA256_KEY],
        field=RAW_EVIDENCE_DOCUMENT_CONTENT_SHA256_KEY,
    )
    if _SHA256_RE.fullmatch(document_content_sha256) is None:
        raise _packet_invalid("typed 근거의 문서 원문 SHA-256이 올바르지 않습니다")
    document_id = _optional_text(raw, "문서ID")
    if not document_id:
        raise _packet_invalid("typed 근거의 원본 문서 ID가 비었습니다")
    expected_identity = collected_document_identity(
        source_kind=source_kind,
        document_id=document_id,
        url=source_url,
    )
    if (
        not expected_identity
        or expected_identity != document_identity
        or bind_declared_document_identity_to_url(document_identity, source_url)
        != document_identity
    ):
        raise _packet_invalid(
            "typed 근거의 문서 신원이 문서 종류·원본 ID·URL과 일치하지 않습니다"
        )

    source_publisher = _require_text(
        raw[RAW_EVIDENCE_PUBLISHER_KEY], field=RAW_EVIDENCE_PUBLISHER_KEY
    )
    identity_binding = _require_text(
        raw[RAW_EVIDENCE_IDENTITY_BINDING_KEY],
        field=RAW_EVIDENCE_IDENTITY_BINDING_KEY,
    )
    if (
        source_kind == SOURCE_KIND_OFFICIAL_IDENTITY_VERIFIED_WEB_PAGE
        and not verified_dart_filing_binding_allows_url(
            identity_binding,
            source_url=source_url,
        )
    ):
        raise _packet_invalid(
            "신원검증 공식 웹의 DART proof가 원본 URL과 일치하지 않습니다"
        )

    source_collected_on = _require_text(
        raw[RAW_EVIDENCE_COLLECTED_ON_KEY], field=RAW_EVIDENCE_COLLECTED_ON_KEY
    )
    domain_attestation_source_id = _optional_text(
        raw, RAW_EVIDENCE_DOMAIN_ATTESTATION_SOURCE_ID_KEY
    )
    domain_attestation_evidence = _optional_text(
        raw, RAW_EVIDENCE_DOMAIN_ATTESTATION_EVIDENCE_KEY
    )
    if bool(domain_attestation_source_id) != bool(domain_attestation_evidence):
        raise _packet_invalid(
            "typed 근거의 도메인 attestation Source ID와 exact 원문이 갈렸습니다"
        )
    reporting_period = _optional_text(raw, RAW_EVIDENCE_REPORTING_PERIOD_KEY)
    attachment_url = _optional_text(raw, RAW_EVIDENCE_ATTACHMENT_URL_KEY)
    ir_metadata_verification = _optional_text(
        raw, RAW_EVIDENCE_IR_METADATA_VERIFICATION_KEY
    )
    domain_redirect_verification = _optional_text(
        raw, RAW_EVIDENCE_DOMAIN_REDIRECT_VERIFICATION_KEY
    )
    domain_redirect_from_host = _optional_text(
        raw, RAW_EVIDENCE_DOMAIN_REDIRECT_FROM_HOST_KEY
    )
    domain_redirect_to_host = _optional_text(
        raw, RAW_EVIDENCE_DOMAIN_REDIRECT_TO_HOST_KEY
    )
    redirect_parts = (
        domain_redirect_verification,
        domain_redirect_from_host,
        domain_redirect_to_host,
    )
    if any(redirect_parts) and not all(redirect_parts):
        raise _packet_invalid("typed 근거의 redirect proof 세 필드가 갈렸습니다")
    if source_kind in OFFICIAL_WEB_SOURCE_KINDS:
        strict_url_proof = verified_dart_filing_binding_allows_url(
            identity_binding,
            source_url=source_url,
        )
        if not strict_url_proof and not (
            domain_attestation_source_id and domain_attestation_evidence
        ):
            raise _packet_invalid("typed 공식 웹의 회사·도메인 proof가 비었습니다")
        if domain_attestation_source_id:
            profile_attestation = parse_dart_profile_domain_attestation(
                domain_attestation_evidence
            )
            if (
                profile_attestation is None
                or profile_attestation.corp_code != corp_id
                or domain_attestation_source_id
                != f"dart-company-profile-{corp_id}"
                or not dart_profile_attestation_allows_source_url(
                    domain_attestation_evidence,
                    source_url=source_url,
                    redirect_verification=domain_redirect_verification,
                    redirect_from_host=domain_redirect_from_host,
                    redirect_to_host=domain_redirect_to_host,
                )
            ):
                raise _packet_invalid(
                    "typed 공식 웹의 DART 기업개황 도메인 proof가 URL과 다릅니다"
                )
        elif any(redirect_parts):
            raise _packet_invalid(
                "typed 공식 웹의 redirect proof에 DART 기업개황 근거가 없습니다"
            )
    elif any(
        (
            domain_attestation_source_id,
            domain_attestation_evidence,
            reporting_period,
            attachment_url,
            ir_metadata_verification,
            *redirect_parts,
        )
    ):
        raise _packet_invalid("typed DART 공시에 웹·IR provenance가 섞였습니다")

    writer_tier, writer_requirement = (
        FORMAL_DOCUMENT_WRITER_TRUST_BY_SOURCE_KIND[source_kind]
    )
    writer_problem = formal_source_writer_ineligibility_reason(
        source_kind=source_kind,
        source_tier=writer_tier,
        requirement=writer_requirement,
        canonical_url=source_url,
        publisher=source_publisher,
        published_on=_optional_text(raw, "문서일"),
        collected_at=source_collected_on,
        identity_binding=identity_binding,
        domain_attestation_source_id=domain_attestation_source_id,
        domain_attestation_evidence=domain_attestation_evidence,
        reporting_period=reporting_period,
        attachment_url=attachment_url,
        ir_metadata_verification=ir_metadata_verification,
        domain_redirect_verification=domain_redirect_verification,
        domain_redirect_from_host=domain_redirect_from_host,
        domain_redirect_to_host=domain_redirect_to_host,
    )
    if writer_problem:
        raise _packet_invalid(
            f"typed formal 문서가 Writer 자격을 잃었습니다: {writer_problem}"
        )

    marker_payload = {
        "version": 3,
        "company_id": corp_id,
        "source_kind": source_kind,
        "section_ids": ordered_section_ids,
        "slot_ids": ordered_slot_ids,
        "origin_fragment_ids": ordered_origin_ids,
        "document_identity": document_identity,
        "document_content_sha256": document_content_sha256,
        "source_publisher": source_publisher,
        "identity_binding": identity_binding,
        "source_collected_on": source_collected_on,
        "domain_attestation_source_id": domain_attestation_source_id,
        "domain_attestation_evidence": domain_attestation_evidence,
        "reporting_period": reporting_period,
        "attachment_url": attachment_url,
        "ir_metadata_verification": ir_metadata_verification,
        "domain_redirect_verification": domain_redirect_verification,
        "domain_redirect_from_host": domain_redirect_from_host,
        "domain_redirect_to_host": domain_redirect_to_host,
    }
    marker_digest = hashlib.sha256(
        json.dumps(
            marker_payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return (
        ordered_section_ids,
        ordered_slot_ids,
        ordered_origin_ids,
        document_identity,
        document_content_sha256,
        source_kind,
        document_id,
        source_publisher,
        identity_binding,
        source_collected_on,
        domain_attestation_source_id,
        domain_attestation_evidence,
        reporting_period,
        attachment_url,
        ir_metadata_verification,
        domain_redirect_verification,
        domain_redirect_from_host,
        domain_redirect_to_host,
        f"{TYPED_TRANSPORT_KIND_PREFIX}{marker_digest}",
    )


def _is_typed_raw(raw: Mapping[str, object]) -> bool:
    return bool(_TYPED_REQUIRED_KEYS & set(raw))


def build_section_evidence_packet_set(
    *,
    corp_id: str,
    source_generation_sha256: str,
    frags: Mapping[int, Mapping[str, object]],
    filing_meta: Any,
) -> SectionEvidencePacketSet:
    """raw 조각을 검증해 정책 순서의 아홉 장 packet으로 옮긴다.

    숫자 dict 키가 공개 인용 번호다. typed origin ID는 공개 번호로 바꾸지 않고
    중복·결속 검증과 packet hash 표식에만 사용한다.
    """

    if type(corp_id) is not str or _COMPANY_ID_RE.fullmatch(corp_id) is None:
        raise _packet_invalid("근거 transport 회사 식별자가 올바르지 않습니다")
    if (
        type(source_generation_sha256) is not str
        or _SHA256_RE.fullmatch(source_generation_sha256) is None
    ):
        raise _packet_invalid("근거 transport generation이 올바르지 않습니다")
    if not isinstance(frags, Mapping) or not frags:
        raise _packet_invalid("근거 transport 조각 묶음이 비었습니다")

    fragments_by_section: dict[str, list[CollectedFragment]] = {
        section_id: [] for section_id in SECTION_IDS
    }
    seen_origin_ids: set[str] = set()
    public_ids = tuple(frags)
    if any(type(public_id) is not int or public_id <= 0 for public_id in public_ids):
        raise _packet_invalid("공개 근거 번호는 양의 정수여야 합니다")
    for public_id in sorted(public_ids):
        raw = frags[public_id]
        if not isinstance(raw, Mapping):
            raise _packet_invalid("근거 조각은 Mapping이어야 합니다")
        text = _require_text(raw.get("원문"), field="원문")
        source_kind = _require_text(raw.get("종류"), field="종류")

        source_url = _optional_text(raw, "출처")
        typed = _is_typed_raw(raw)
        if typed:
            (
                section_ids,
                supported_claim_slots,
                origin_ids,
                document_identity,
                document_content_sha256,
                formal_source_kind,
                source_document_id,
                source_publisher,
                identity_binding,
                source_collected_on,
                domain_attestation_source_id,
                domain_attestation_evidence,
                reporting_period,
                attachment_url,
                ir_metadata_verification,
                domain_redirect_verification,
                domain_redirect_from_host,
                domain_redirect_to_host,
                packet_kind,
            ) = _typed_metadata(
                raw,
                corp_id=corp_id,
                source_kind=source_kind,
                source_url=source_url,
            )
            duplicate_origins = seen_origin_ids & set(origin_ids)
            if duplicate_origins:
                raise _packet_invalid(
                    "같은 typed origin 조각을 둘 이상의 공개 번호로 만들 수 없습니다"
                )
            seen_origin_ids.update(origin_ids)
        else:
            if source_kind not in LEGACY_FRAGMENT_KINDS:
                raise _unregistered_kind()
            section_ids = tuple(
                section_id
                for section_id in SECTION_IDS
                if section_id in sections_for_legacy_fragment_kind(source_kind)
            )
            document_identity = _legacy_document_identity(
                raw, text=text, source_url=source_url, filing_meta=filing_meta
            )
            document_content_sha256 = ""
            formal_source_kind = ""
            source_document_id = _optional_text(raw, "문서ID")
            source_publisher = ""
            identity_binding = ""
            source_collected_on = ""
            domain_attestation_source_id = ""
            domain_attestation_evidence = ""
            reporting_period = ""
            attachment_url = ""
            ir_metadata_verification = ""
            domain_redirect_verification = ""
            domain_redirect_from_host = ""
            domain_redirect_to_host = ""
            # legacy 종류는 장 범위만 알고, 그 안의 정확한 의미 칸은 모른다.
            # 종류→slot을 추측하면 근거가 없는 claim까지 지원한다고 과대 표시한다.
            supported_claim_slots = ()
            packet_kind = source_kind

        if not _document_identity_is_valid(document_identity):
            raise _packet_invalid("근거 조각의 문서 신원을 확인할 수 없습니다")
        fragment = CollectedFragment(
            fragment_id=str(public_id),
            kind=packet_kind,
            text=text,
            source_url=source_url,
            document_title=_optional_text(raw, "문서명"),
            location=_optional_text(raw, "원문위치"),
            document_date=_optional_text(raw, "문서일"),
            document_identity=document_identity,
            document_content_sha256=document_content_sha256,
            supported_claim_slots=supported_claim_slots,
            formal_source_kind=formal_source_kind,
            source_document_id=source_document_id,
            source_publisher=source_publisher,
            identity_binding=identity_binding,
            source_collected_on=source_collected_on,
            domain_attestation_source_id=domain_attestation_source_id,
            domain_attestation_evidence=domain_attestation_evidence,
            reporting_period=reporting_period,
            attachment_url=attachment_url,
            ir_metadata_verification=ir_metadata_verification,
            domain_redirect_verification=domain_redirect_verification,
            domain_redirect_from_host=domain_redirect_from_host,
            domain_redirect_to_host=domain_redirect_to_host,
        )
        for section_id in section_ids:
            fragments_by_section[section_id].append(fragment)

    empty_sections = tuple(
        section_id
        for section_id in SECTION_IDS
        if not fragments_by_section[section_id]
    )
    if empty_sections:
        raise _packet_invalid("아홉 장 중 근거 조각이 없는 장이 있습니다")

    try:
        packets = tuple(
            SectionEvidencePacket(
                company_id=corp_id,
                evidence_generation_sha256=source_generation_sha256,
                section_id=section_id,
                fragments=tuple(fragments_by_section[section_id]),
            )
            for section_id in SECTION_IDS
        )
        return SectionEvidencePacketSet(
            company_id=corp_id,
            evidence_generation_sha256=source_generation_sha256,
            packets=packets,
        )
    except (TypeError, ValueError) as error:
        raise _packet_invalid("장별 근거 packet 생성 계약이 손상됐습니다") from error
