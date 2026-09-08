"""수집 조각을 FULL 장별 근거 packet으로 옮기는 순수 경계.

legacy 조각은 shared 종류 정본의 정확 소유 장만 따른다. typed 조각은 수집기가
봉인한 장·슬롯·문서 신원을 그대로 따르며, ``종류``로 장을 다시 추측하지 않는다.
어느 입력 계약이든 손상되면 작성기 호출 전에 닫힌 진단 코드로 실패한다.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final
from urllib.parse import urlsplit

from src.core.news_intake_switch import news_intake_enabled
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
    fragments_from_raw,
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
    SUPPLEMENTARY_DOCUMENT_SOURCE_KINDS,
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
    SUPPLEMENTARY_WRITER_TRUST,
    FormalSourceKindContractError,
    document_slots_for_formal_source_kind,
    formal_source_writer_ineligibility_reason,
    supplementary_slots_for_source_kind,
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
    is_supplementary = source_kind in SUPPLEMENTARY_DOCUMENT_SOURCE_KINDS
    if source_kind not in FORMAL_DOCUMENT_SOURCE_KINDS and not (
        is_supplementary and news_intake_enabled()
    ):
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
        allowed_slot_ids = (
            supplementary_slots_for_source_kind(source_kind)
            if is_supplementary
            else document_slots_for_formal_source_kind(source_kind)
        )
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
    expected_identity = (
        document_identity_from_parts(
            document_id=document_id,
            host=(urlsplit(source_url).hostname or ""),
            url=source_url,
        )
        if is_supplementary
        else collected_document_identity(
            source_kind=source_kind,
            document_id=document_id,
            url=source_url,
        )
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
        allow_empty=is_supplementary,
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
    if is_supplementary:
        if (
            not source_url
            or not _optional_text(raw, "문서일")
            or not _optional_text(raw, "문서명")
            or not _optional_text(raw, "원문위치")
        ):
            raise _packet_invalid(
                "typed 보조 문서의 언론사·제목·날짜·URL·원문 위치가 비었습니다"
            )
        if any(
            (
                domain_attestation_source_id,
                domain_attestation_evidence,
                reporting_period,
                attachment_url,
                ir_metadata_verification,
                *redirect_parts,
            )
        ):
            raise _packet_invalid("typed 보조 문서에 공식 웹·IR provenance가 섞였습니다")
    elif source_kind in OFFICIAL_WEB_SOURCE_KINDS:
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

    if is_supplementary:
        # 표를 실제 transport가 읽게 해 목록과 Writer 자격이 선언만 남지 않게 한다.
        SUPPLEMENTARY_WRITER_TRUST[source_kind]
    else:
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
    if is_supplementary:
        marker_payload["counts_toward_document_floor"] = False
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


def _collected_fragment_from_raw(
    public_id: int,
    raw: Mapping[str, object],
    *,
    corp_id: str,
    filing_meta: Any,
    seen_origin_ids: set[str],
) -> tuple[CollectedFragment, tuple[str, ...]]:
    """공개 번호 하나를 검증된 조각과 그 조각이 선언한 장 목록으로 옮긴다.

    장별 packet 빌더와 평면 변환기가 «같은» 검증·같은 바이트를 쓰게 하는 단
    하나의 자리다. 두 곳에 같은 코드를 복사하면 한쪽만 고쳐져 부분 보고서와
    FULL이 서로 다른 조각을 보게 된다.

    ``seen_origin_ids``는 호출자가 소유하고 이 함수가 갱신한다 — 같은 typed
    origin 조각이 두 공개 번호로 들어오는 것을 묶음 단위로 막기 위해서다.
    """

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
        counts_toward_document_floor=(
            formal_source_kind not in SUPPLEMENTARY_DOCUMENT_SOURCE_KINDS
        ),
        news_grounded=(
            formal_source_kind in SUPPLEMENTARY_DOCUMENT_SOURCE_KINDS
            and raw.get("news_grounded") is True
        ),
        news_claim_kind=_optional_text(raw, "news_claim_kind"),
        news_temporal_status=_optional_text(raw, "news_temporal_status"),
        news_event_on=_optional_text(raw, "news_event_on"),
        news_event_key=_optional_text(raw, "news_event_key"),
        news_source_category=_optional_text(raw, "news_source_category"),
    )
    return fragment, section_ids


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
        fragment, section_ids = _collected_fragment_from_raw(
            public_id,
            frags[public_id],
            corp_id=corp_id,
            filing_meta=filing_meta,
            seen_origin_ids=seen_origin_ids,
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


#: 원형 유지 사유 문자열의 상한. 실행 기록·로그에 그대로 실리므로 길이를 묶어
#: 둔다. 사유는 조각 종류와 닫힌 내부 메시지뿐이고 원문·URL은 들어가지 않는다.
CARRIED_RAW_REASON_MAX_LENGTH: Final[int] = 80
#: 종류가 비었거나 문자열이 아닐 때 쓰는 자리표시자. 빈 접두를 그대로 두면
#: 「: 메시지」가 되어 종류를 안 적은 건지 빈 건지 읽는 사람이 못 가른다.
CARRIED_RAW_UNKNOWN_KIND: Final[str] = "(종류 없음)"
#: 사유 열쇠에 실을 수 있는 종류 이름의 길이 상한. 2026-09-07 실측으로 정본
#: 종류 이름의 최대 길이는 서른다섯 자(``official_identity_verified_web_page``,
#: ``FORMAL_DOCUMENT_WRITER_TRUST_BY_SOURCE_KIND``)이고 legacy 정본은 여덟 자
#: (「감사보고서 재무」, ``LEGACY_FRAGMENT_KINDS``)다. 새 정본 이름이 조금 길어져도
#: 가려지지 않게 다섯 자 여유를 둔다. 상한을 넘는 정본 이름이 생기면 시험
#: ``test_정본_종류_이름은_모두_사유열쇠에_그대로_실린다``가 먼저 깨진다.
CARRIED_RAW_KIND_MAX_LENGTH: Final[int] = 40
#: 종류 이름에 허용하는 글자. 한글 음절·영숫자·공백과 정본 이름이 실제로 쓰는
#: 이음 기호(``&``·``_``·``-``·``·``·괄호)뿐이다. URL의 「:」·「/」와 자유 문장의
#: 마침표·쉼표·따옴표·줄바꿈은 여기서 걸린다.
CARRIED_RAW_KIND_ALLOWED_RE: Final[re.Pattern[str]] = re.compile(
    r"^[가-힣A-Za-z0-9 &_\-·()]+$"
)
#: 길이나 글자 형식을 벗어난 종류를 대신할 자리표시자. 「종류 없음」과 다른 말을
#: 쓰는 이유는, 안 적어서 빈 것과 적었는데 형식이 아닌 것이 서로 다른 고장이라
#: 운영에서 같은 칸으로 세면 원인을 못 가르기 때문이다.
CARRIED_RAW_KIND_OUT_OF_FORM: Final[str] = "(형식 밖 종류)"


def _carried_raw_kind_label(kind: str) -> str:
    """사유 열쇠에 실을 종류 이름을 «형식이 맞는 것»으로만 좁힌다.

    ★ 왜 필요한가 — 이 관용 경로는 애초에 「정본이 모르는 종류」를 위해 열린
      길인데, 정작 그 종류 필드만 아무 검사 없이 실행 기록(``v2_조각_typed전달.
      원형유지_사유별``)과 경고 로그의 열쇠가 됐다. 상류가 종류 칸에 URL이나
      사람 이름·자유 문장을 담아 보내면 그것이 그대로 운영 기록에 남는다. 사유
      열쇠는 «어느 생산자의 조각이 걸렸나»를 세는 자리지 원문을 옮기는 자리가
      아니므로, 형식을 벗어난 값은 자리표시자로 바꾼다.

    ★ 왜 잘라 싣지 않고 통째로 바꾸나 — 앞 마흔 자만 남기면 URL의 host가 그대로
      남는다. 형식을 벗어났다는 사실만 세는 것이 사유 열쇠의 목적에 맞다.

    Args:
        kind: raw 조각이 적어 낸 종류. 앞뒤 공백은 여기서 지운다.

    Returns:
        형식이 맞으면 그대로, 비면 ``CARRIED_RAW_UNKNOWN_KIND``, 길이·글자
        형식을 벗어나면 ``CARRIED_RAW_KIND_OUT_OF_FORM``.
    """

    label = kind.strip()
    if not label:
        return CARRIED_RAW_UNKNOWN_KIND
    if len(label) > CARRIED_RAW_KIND_MAX_LENGTH:
        return CARRIED_RAW_KIND_OUT_OF_FORM
    if CARRIED_RAW_KIND_ALLOWED_RE.fullmatch(label) is None:
        return CARRIED_RAW_KIND_OUT_OF_FORM
    return label


def _carried_raw_reason(error: EvidenceTransportError, *, kind: str) -> str:
    """원형 유지 사유를 「종류: 메시지」 한 줄로 줄인다.

    ★ 왜 메시지만으로는 모자라나 — 사유 메시지 하나(예: 「등록되지 않은 수집
      조각 종류입니다」)에 스무 가지 넘는 생산자가 걸린다. 운영 기록에서
      「어느 생산자의 조각이 걸렸나」를 바로 읽으려면 종류가 함께 있어야 한다.

    종류 이름 자체는 ``_carried_raw_kind_label``로 형식을 좁힌 뒤에 싣는다 —
    상류가 보낸 값을 검사 없이 실행 기록의 열쇠로 쓰지 않기 위해서다.
    """

    label = _carried_raw_kind_label(kind)
    message = str(error).strip() or "알 수 없는 사유"
    reason = f"{label}: {message}"
    if len(reason) > CARRIED_RAW_REASON_MAX_LENGTH:
        # 잘렸다는 사실이 보이게 마지막 한 글자를 말줄임으로 바꾼다.
        reason = reason[: CARRIED_RAW_REASON_MAX_LENGTH - 1] + "…"
    return reason


@dataclass(frozen=True)
class FlatFragmentConversion:
    """부분 보고서 평면 변환의 결과와 «무엇이 어떻게 실렸는지»를 함께 담는다.

    수만 세는 것이 아니라 사유별 수까지 돌려주는 이유는, 운영에서 보도표가
    비었을 때 「조각이 없었나」와 「조각이 원형으로 실렸나」를 실행 기록만 보고
    가를 수 있어야 하기 때문이다. 처음 판은 성공/실패 두 갈래뿐이라 원인을
    가르지 못했다.
    """

    #: 공개 번호 오름차순 조각. typed·legacy·원형 유지가 섞여 있다.
    fragments: tuple[CollectedFragment, ...] = ()
    #: typed transport 메타를 다 채워 봉인된 신원 그대로 실린 조각 수.
    typed_count: int = 0
    #: typed 키 없이 정본에 등록된 legacy 종류로 변환된 조각 수.
    legacy_count: int = 0
    #: 계약을 못 채워 옛 어댑터 모양으로 실린 조각 수(버리지 않는다).
    carried_raw_count: int = 0
    #: 원문이 비어 옛 어댑터와 같은 규칙으로 건너뛴 조각 수.
    skipped_empty_count: int = 0
    #: (「종류: 사유 메시지」, 개수) 쌍을 사유 문자열 오름차순으로 담는다.
    carried_raw_reasons: tuple[tuple[str, int], ...] = ()

    @property
    def supplementary_count(self) -> int:
        """보조 문서(보도 자료 등) 종류로 실린 조각 수.

        호출자가 같은 계산을 다시 쓰면 「보조가 무엇인가」의 정본이 둘로 갈린다.
        """

        return sum(
            1
            for fragment in self.fragments
            if fragment.formal_source_kind in SUPPLEMENTARY_DOCUMENT_SOURCE_KINDS
        )


def typed_fragments_from_raw(
    *,
    corp_id: str,
    frags: Mapping[int, Mapping[str, object]],
    filing_meta: Any,
) -> FlatFragmentConversion:
    """장별 묶음 없이도 조각의 typed 신원을 그대로 보존해 넘긴다.

    ★ 왜 packet 없이 이 함수가 필요한가 — 장별 packet은 FULL 출고 계약(아홉 장
      모두에 근거가 있고 독립 문서 하한을 채운다)에 묶여 있다. 부분 보고서
      갈래는 그 하한을 못 채워서 열린 길이므로 packet을 만들 수 없고, 지금까지
      작성기에 raw dict를 그대로 넘겼다. raw dict를 받는 작성기 어댑터는 종류·
      원문·출처·문서명·원문위치만 읽어 발행처·문서일·문서 종류·의미 칸·장
      선언을 통째로 버린다. 그래서 보조 문서(보도 자료)로 만드는 표는 소유 장을
      잃고 «부분 보고서에서만» 구조적으로 만들어지지 않았다.

    ★ 조각의 typed 신원은 릴리스 모드와 무관한 사실이다. 그래서 조각 하나의
      검증은 packet 빌더와 «같은» ``_collected_fragment_from_raw``를 지난다.

    ★ 왜 FULL packet은 엄격한데 여기는 조각별로 관용하는가 (2026-09-07 실측) —
      비상장 외감 회사의 묶음에는 「감사보고서 재무」처럼 정본 등록표에 아직
      없던 종류가 섞인다. 처음 판에서는 그 조각 하나가 ``EvidenceTransportError``
      를 내면 «묶음 전체»가 raw dict로 되돌아갔고, 같은 묶음에 있던 정상 뉴스
      조각의 typed 신원까지 함께 잃어 보도표가 0건이 됐다. FULL은 아홉 장 계약
      자체가 걸린 출고 관문이라 하나라도 어긋나면 거절하는 것이 맞지만, 부분
      보고서는 애초에 그 하한을 못 채워서 열린 길이다. 여기서 묶음을 통째로
      포기하면 «고칠 수 있었던 조각»까지 같이 버린다.

    그래서 조각 하나가 계약을 어기면 그 조각만 옛 어댑터(``fragments_from_raw``)
    모양으로 싣고(버리지 않는다) 사유를 세어 돌려준다. 옛 어댑터와 마찬가지로
    원문이 빈 조각만 건너뛴다. 묶음 자체가 잘못된 입력(회사 식별자·공개 번호·
    묶음/조각 자료형)일 때만 예외로 남긴다 — 그건 한 조각의 품질 문제가 아니라
    호출자의 계약 위반이라 조용히 넘기면 원인을 못 찾는다.

    Args:
        corp_id: 여덟 자리 회사 고유번호. typed 조각의 회사 결속을 검산한다.
        frags: 공개 인용 번호(양의 정수) → raw 조각 Mapping.
        filing_meta: legacy 조각의 문서 신원을 만들 때 쓰는 공시 문서 신원.

    Returns:
        ``FlatFragmentConversion``. 조각은 공개 번호 오름차순이고 ``frags``가
        비면 조각도 사유도 비어 있다.

    Raises:
        EvidenceTransportError: 묶음 단위 입력 계약이 깨진 경우. 진단 코드는
            packet 빌더와 같은 값을 그대로 쓴다.
    """

    if type(corp_id) is not str or _COMPANY_ID_RE.fullmatch(corp_id) is None:
        raise _packet_invalid("근거 transport 회사 식별자가 올바르지 않습니다")
    if not isinstance(frags, Mapping):
        raise _packet_invalid("근거 transport 조각 묶음이 비었습니다")
    if not frags:
        # 부분 보고서는 조각이 0개일 수도 있다. 그건 계약 손상이 아니라 실제
        # 수집 결과이므로 예외 대신 빈 결과로 정직하게 돌려준다.
        return FlatFragmentConversion(fragments=())

    public_ids = tuple(frags)
    if any(type(public_id) is not int or public_id <= 0 for public_id in public_ids):
        raise _packet_invalid("공개 근거 번호는 양의 정수여야 합니다")

    seen_origin_ids: set[str] = set()
    fragments: list[CollectedFragment] = []
    typed_count = 0
    legacy_count = 0
    carried_raw_count = 0
    skipped_empty_count = 0
    carried_raw_reasons: Counter[str] = Counter()
    for public_id in sorted(public_ids):
        raw = frags[public_id]
        if not isinstance(raw, Mapping):
            # 조각이 Mapping이 아니면 옛 어댑터도 읽을 수 없다. 이건 한 조각의
            # 품질 문제가 아니라 호출자가 넘긴 자료형 계약 위반이다.
            raise _packet_invalid("근거 조각은 Mapping이어야 합니다")
        if not str(raw.get("원문") or "").strip():
            # 옛 어댑터와 같은 규칙이다 — 인용해도 대조할 원문이 없으면 근거가
            # 못 된다. 내용을 보고 거르는 게 아니라 «비어 있는가»만 본다.
            skipped_empty_count += 1
            continue
        # ★ 실패한 조각의 origin이 묶음 상태에 남으면 뒤 조각이 「중복 origin」
        #   으로 잘못 거절된다. 도우미는 packet 빌더와 공유하므로 손대지 않고,
        #   사본을 넘겨 «성공했을 때만» 되돌려 받는다.
        attempted_origin_ids = set(seen_origin_ids)
        try:
            fragment, _section_ids = _collected_fragment_from_raw(
                public_id,
                raw,
                corp_id=corp_id,
                filing_meta=filing_meta,
                seen_origin_ids=attempted_origin_ids,
            )
        except EvidenceTransportError as error:
            carried = fragments_from_raw({public_id: raw})
            if not carried:
                # 위 빈 원문 검사와 옛 어댑터의 규칙이 어긋난 경우에만 온다.
                # 만들 수 없는 조각을 지어내지 않고 건너뛴 것으로 센다.
                skipped_empty_count += 1
                continue
            fragments.extend(carried)
            carried_raw_count += 1
            carried_raw_reasons[
                _carried_raw_reason(
                    error, kind=str(raw.get("종류") or "").strip()
                )
            ] += 1
            continue
        seen_origin_ids = attempted_origin_ids
        fragments.append(fragment)
        if _is_typed_raw(raw):
            typed_count += 1
        else:
            legacy_count += 1

    return FlatFragmentConversion(
        fragments=tuple(fragments),
        typed_count=typed_count,
        legacy_count=legacy_count,
        carried_raw_count=carried_raw_count,
        skipped_empty_count=skipped_empty_count,
        carried_raw_reasons=tuple(sorted(carried_raw_reasons.items())),
    )
