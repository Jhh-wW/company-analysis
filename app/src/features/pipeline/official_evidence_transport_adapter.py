"""typed 장 후보를 기존 숫자 인용 조각에 손실 없이 연결한다.

작가·렌더러는 양의 숫자 인용을 사용하지만 수집기는 안정적인 문자열 조각 ID를
사용한다. 이 어댑터는 숫자를 새로 배정하되 원래 ID·장·의미 칸·문서 신원을
raw transport 메타데이터에 함께 보존한다. 장 배정은 이후 transport가 이
메타데이터만 보고 수행하며 ``종류`` 문자열로 다시 추측하지 않는다.
"""

from __future__ import annotations

from collections.abc import Mapping

from src.features.pipeline.evidence_transport import (
    RAW_EVIDENCE_COMPANY_ID_KEY,
    RAW_EVIDENCE_ATTACHMENT_URL_KEY,
    RAW_EVIDENCE_COLLECTED_ON_KEY,
    RAW_EVIDENCE_DOCUMENT_CONTENT_SHA256_KEY,
    RAW_EVIDENCE_DOCUMENT_IDENTITY_KEY,
    RAW_EVIDENCE_DOMAIN_ATTESTATION_EVIDENCE_KEY,
    RAW_EVIDENCE_DOMAIN_ATTESTATION_SOURCE_ID_KEY,
    RAW_EVIDENCE_DOMAIN_REDIRECT_FROM_HOST_KEY,
    RAW_EVIDENCE_DOMAIN_REDIRECT_TO_HOST_KEY,
    RAW_EVIDENCE_DOMAIN_REDIRECT_VERIFICATION_KEY,
    RAW_EVIDENCE_IDENTITY_BINDING_KEY,
    RAW_EVIDENCE_IR_METADATA_VERIFICATION_KEY,
    RAW_EVIDENCE_ORIGIN_FRAGMENT_IDS_KEY,
    RAW_EVIDENCE_PUBLISHER_KEY,
    RAW_EVIDENCE_REPORTING_PERIOD_KEY,
    RAW_EVIDENCE_SECTION_IDS_KEY,
    RAW_EVIDENCE_SLOT_IDS_KEY,
)
from src.shared.report_evidence.runtime_port import OfficialEvidenceCollectionResult
from src.shared.report_quality.source_identity import collected_document_identity


def _document_identity(
    *, source_kind: str, document_id: str, canonical_url: str
) -> str:
    """typed 문서의 안정 신원을 공개 URL과 원본 ID에서 만든다."""

    identity = collected_document_identity(
        source_kind=source_kind,
        document_id=document_id,
        url=canonical_url,
    )
    if not identity:
        raise ValueError(
            "typed 공식 문서 종류·문서 ID·URL의 안정 신원을 만들 수 없습니다"
        )
    return identity


def _string_tuple(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(str(item).strip() for item in value if str(item).strip())


def merge_official_evidence_fragments(
    frags: Mapping[int, Mapping[str, object]],
    result: OfficialEvidenceCollectionResult,
) -> tuple[dict[int, dict[str, object]], int]:
    """typed 후보를 숫자 인용 조각으로 합치고 새로 생긴 번호 수를 돌려준다.

    같은 문서·같은 원문이 이미 legacy 수집에 있으면 그 번호를 업그레이드한다.
    그래야 그 번호를 이미 가리키는 매출·재무 표가 끊기지 않는다. 문서가 다른데
    문구만 같은 경우에는 별도 번호를 만들어 출처를 섞지 않는다.
    """

    merged: dict[int, dict[str, object]] = {
        int(number): dict(raw) for number, raw in frags.items()
    }
    documents = {}
    grouped: dict[tuple[str, str], dict[str, object]] = {}
    for candidate in result.candidates:
        for document in candidate.documents:
            previous = documents.setdefault(document.document_id, document)
            if previous != document:
                raise ValueError("같은 typed 문서 ID가 서로 다른 문서를 가리킵니다")
        for fragment in candidate.fragments:
            # 같은 글자가 한 문서의 서로 다른 위치에 반복돼도 출처 위치를
            # 합치지 않는다. 수집기 origin ID가 provenance의 최소 단위다.
            key = (fragment.document_id, fragment.fragment_id)
            group = grouped.setdefault(
                key,
                {
                    "text": fragment.text,
                    "text_sha256": fragment.text_sha256,
                    "location": fragment.location,
                    "section_ids": set(),
                    "slot_ids": set(),
                    "origin_ids": set(),
                },
            )
            if (
                group["text"] != fragment.text
                or group["text_sha256"] != fragment.text_sha256
                or group["location"] != fragment.location
            ):
                raise ValueError("같은 typed origin ID가 서로 다른 원문을 가리킵니다")
            group["section_ids"].add(fragment.section_id)
            group["slot_ids"].update(fragment.covered_slot_ids)
            group["origin_ids"].add(fragment.fragment_id)

    added = 0
    for (document_id, _origin_fragment_id), group in sorted(grouped.items()):
        document = documents.get(document_id)
        if document is None:
            raise ValueError("typed 조각의 원본 문서가 수집 결과에 없습니다")
        text = str(group["text"])
        identity = _document_identity(
            source_kind=document.source_kind,
            document_id=document.document_id,
            canonical_url=document.canonical_url,
        )

        existing_number = None
        for number in sorted(merged):
            raw = merged[number]
            if str(raw.get("원문") or "") != text:
                continue
            raw_identity = str(
                raw.get(RAW_EVIDENCE_DOCUMENT_IDENTITY_KEY) or ""
            ).strip()
            raw_url = str(raw.get("출처") or "").strip()
            raw_document_id = str(raw.get("문서ID") or "").strip()
            raw_origins = set(
                _string_tuple(raw.get(RAW_EVIDENCE_ORIGIN_FRAGMENT_IDS_KEY))
            )
            formal_origins = set(group["origin_ids"])
            binding_checks: list[bool] = []
            if raw_identity:
                binding_checks.append(raw_identity == identity)
            if raw_url:
                binding_checks.append(raw_url == document.canonical_url)
            if raw_document_id:
                binding_checks.append(
                    raw_document_id
                    in {
                        document.document_id,
                        document.document_id.rpartition(":")[2],
                    }
                )
            if raw_origins:
                binding_checks.append(raw_origins == formal_origins)
            # 같은 글자는 문서 신원이 아니다. 공시·홈페이지에 같은 상투문구가
            # 있어도 출처 필드가 하나도 없는 legacy 조각을 formal 문서로
            # 승격하지 않는다. 하나 이상 명시돼 있고, 명시된 필드는 전부 지금
            # formal 문서/원본 조각과 정확히 맞을 때만 공개 번호를 보존한다.
            if not binding_checks or not all(binding_checks):
                continue
            existing_number = number
            break

        if existing_number is None:
            existing_number = max(merged, default=0) + 1
            merged[existing_number] = {
                "종류": document.source_kind,
                "원문": text,
            }
            added += 1

        raw = merged[existing_number]
        existing_sections = set(
            _string_tuple(raw.get(RAW_EVIDENCE_SECTION_IDS_KEY))
        )
        existing_slots = set(_string_tuple(raw.get(RAW_EVIDENCE_SLOT_IDS_KEY)))
        existing_origins = set(
            _string_tuple(raw.get(RAW_EVIDENCE_ORIGIN_FRAGMENT_IDS_KEY))
        )
        raw.update(
            {
                # typed 메타데이터를 붙인 순간부터 종류도 formal 정본이어야
                # 한다. 기존 공개 번호만 보존하고 legacy 종류 추측은 남기지 않는다.
                "종류": document.source_kind,
                "출처": document.canonical_url,
                "문서ID": document.document_id,
                "문서명": document.title,
                "문서일": document.published_on,
                "원문위치": str(group["location"]),
                RAW_EVIDENCE_COMPANY_ID_KEY: result.company_id,
                RAW_EVIDENCE_DOCUMENT_IDENTITY_KEY: identity,
                RAW_EVIDENCE_DOCUMENT_CONTENT_SHA256_KEY: (
                    document.content_sha256
                ),
                RAW_EVIDENCE_IDENTITY_BINDING_KEY: document.identity_binding,
                RAW_EVIDENCE_PUBLISHER_KEY: document.publisher,
                RAW_EVIDENCE_COLLECTED_ON_KEY: document.collected_at,
                RAW_EVIDENCE_DOMAIN_ATTESTATION_SOURCE_ID_KEY: (
                    document.domain_attestation_source_id
                ),
                RAW_EVIDENCE_DOMAIN_ATTESTATION_EVIDENCE_KEY: (
                    document.domain_attestation_evidence
                ),
                RAW_EVIDENCE_REPORTING_PERIOD_KEY: document.reporting_period,
                RAW_EVIDENCE_ATTACHMENT_URL_KEY: document.attachment_url,
                RAW_EVIDENCE_IR_METADATA_VERIFICATION_KEY: (
                    document.ir_metadata_verification
                ),
                RAW_EVIDENCE_DOMAIN_REDIRECT_VERIFICATION_KEY: (
                    document.domain_redirect_verification
                ),
                RAW_EVIDENCE_DOMAIN_REDIRECT_FROM_HOST_KEY: (
                    document.domain_redirect_from_host
                ),
                RAW_EVIDENCE_DOMAIN_REDIRECT_TO_HOST_KEY: (
                    document.domain_redirect_to_host
                ),
                RAW_EVIDENCE_SECTION_IDS_KEY: tuple(
                    sorted(existing_sections | set(group["section_ids"]))
                ),
                RAW_EVIDENCE_SLOT_IDS_KEY: tuple(
                    sorted(existing_slots | set(group["slot_ids"]))
                ),
                RAW_EVIDENCE_ORIGIN_FRAGMENT_IDS_KEY: tuple(
                    sorted(existing_origins | set(group["origin_ids"]))
                ),
            }
        )

    return merged, added
