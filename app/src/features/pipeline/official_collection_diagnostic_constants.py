"""공식 수집 시도 진단의 원문 없는 닫힌 전송 어휘."""

from __future__ import annotations

from http import HTTPStatus
from typing import Final

from src.shared.report_evidence.constants import (
    CollectionState,
    FORMAL_ATTEMPT_SOURCE_KINDS,
    SourceRequirement,
)


OFFICIAL_COLLECTION_DIAGNOSTICS_STEP: Final[str] = "6_수집_공식자료원진단"
UNKNOWN_OFFICIAL_COLLECTION_VALUE: Final[str] = "unknown"

# CollectionAttempt의 실제 formal source_kind만 관측한다. URL·발행사·문서 ID는
# 여기로 올 수 없고, 새 종류가 생기면 이 allowlist를 명시적으로 갱신해야 한다.
ALLOWED_OFFICIAL_COLLECTION_SOURCE_KINDS: Final[frozenset[str]] = (
    FORMAL_ATTEMPT_SOURCE_KINDS
)
ALLOWED_OFFICIAL_COLLECTION_STATES: Final[frozenset[str]] = frozenset(
    state.value for state in CollectionState
)
ALLOWED_OFFICIAL_COLLECTION_REQUIREMENTS: Final[frozenset[str]] = frozenset(
    requirement.value for requirement in SourceRequirement
)

# analysis_engine evidence_collection의 이름 있는 수집 사유와, app 공식 웹
# collector가 실제로 만드는 닫힌 상태만 허용한다. 형식이 맞는 임의 문자열이나
# transport 예외 문구를 넓게 받아 저장하지 않는다.
ALLOWED_OFFICIAL_COLLECTION_REASON_CODES: Final[frozenset[str]] = frozenset(
    {
        "list_query_ok",
        "list_query_missing",
        "list_query_failed",
        "list_row_identity_mismatch",
        "list_rows_all_filtered",
        "document_fetch_ok",
        "document_fetch_failed",
        "document_fetch_missing",
        "document_too_large",
        "total_bytes_exceeded",
        "document_duplicate_sha256",
        "cap_reached",
        "deadline_exceeded",
        "no_keyword_signal",
        "document_identity_mismatch",
        "document_model_invalid",
        "filing_receipt_date_invalid",
        "document_no_scored_evidence",
        "document_section_count_exceeded",
        "document_line_index_exceeded",
        "document_fragment_count_exceeded",
        "document_fragment_chars_exceeded",
        "network_failed",
        "robots_ok",
        "robots_missing",
        "robots_unreachable",
        "robots_disallowed",
        "truncated_page_cap",
        "truncated_time_cap",
        "truncated_byte_cap",
        "truncated_root_identity_supplement_cap",
        "truncated_root_identity_supplement_bytes",
        "truncated_client_redirect_cap",
        "no_usable_content",
        "page_ok",
        "duplicate_content_or_empty",
        "root_identity_unverifiable",
        "root_identity_mismatch",
        "root_identity_supplement_failed",
        "root_identity_name_only",
        "root_identity_verified",
        "redirect_scope_mismatch",
        "cross_domain_identity_mismatch",
        "cross_domain_identity_verified",
        "dart_filing_identity_verified",
        "cross_domain_official_lineage_missing",
        "sitemap_ok",
        "sitemap_failed",
        "ir_origin_unsupported",
        "ir_pdf_ok",
        "ir_pdf_none",
        "ir_pdf_failed",
        "official_ir_writer_metadata_incomplete",
    }
    # 생산자가 HTTP 상태를 덧붙이는 사유도 표준 상태의 유한 집합만 허용한다.
    # 임의 접미사·URL·확장 상태 문자열은 여전히 unknown으로 닫힌다.
    | {f"page_missing_{status}" for status in (404, 410)}
    | {f"sitemap_missing_{status}" for status in (404, 410)}
    | {f"sitemap_denied_{status}" for status in (401, 403, 407)}
    | {f"sitemap_transient_{status}" for status in (408, 409, 429)}
    | {f"page_failed_{status.value}" for status in HTTPStatus
       if status.value not in (200, 404, 410)}
    | {f"sitemap_failed_{status.value}" for status in HTTPStatus
       if status.value not in (200, 401, 403, 404, 407, 408, 409, 410, 429)}
)
