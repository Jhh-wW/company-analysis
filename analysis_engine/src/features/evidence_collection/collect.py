"""DART 근거수집 조율 — filing_select → segment → relevance → classify를 묶어
DartEvidenceHarvest 하나를 만든다.

실제 DART 연동(fetcher 구현)은 이 슬라이스의 범위 밖이다 — LIVE_COLLECTION_UNVERIFIED.
여기서는 주입된 fetcher만 쓰고 실제 네트워크 접근을 하지 않는다.
"""

from __future__ import annotations

import hashlib
import time

from features.evidence_collection import classify, constants as c, filing_select, relevance, segment
from features.evidence_collection.filing_select import DartFetcher, DocumentFetchResult, SelectedFiling
from features.evidence_collection.models import (
    CollectedDocument,
    CollectionAttempt,
    DartEvidenceHarvest,
    EvidenceFragment,
)


def _safe_fetch_document(fetcher: DartFetcher, rcept_no: str) -> DocumentFetchResult:
    # filing_select._safe_fetch_list와 같은 이유로 이 경계에서 예외를 흡수한다
    # (요구사항 7 — 「조회 실패」와 「자료 없음」 분리, 문서 1건 실패가 전체
    # 수집을 죽이지 않게 한다).
    try:
        return fetcher.fetch_document_text(rcept_no)
    except Exception:  # noqa: BLE001 - fetcher 경계 흡수(위 사유)
        return DocumentFetchResult(state=c.ATTEMPT_STATE_FAILED)


def _document_attempt(
    filing: SelectedFiling, state: str, reason_code: str, fetch_result: DocumentFetchResult,
) -> CollectionAttempt:
    return CollectionAttempt(
        attempt_id=f"document:{filing.source_kind}:{filing.rcept_no}",
        source_kind=filing.source_kind,
        requirement=filing.requirement,
        state=state,
        slot_ids=c.SOURCE_KIND_SLOT_SCOPE[filing.source_kind],
        reason_code=reason_code,
        elapsed_ms=max(0, fetch_result.elapsed_ms),
        bytes_downloaded=max(0, fetch_result.bytes_downloaded),
        documents_seen=1,
    )


def _deadline_attempt(filing: SelectedFiling) -> CollectionAttempt:
    return CollectionAttempt(
        attempt_id=f"deadline:{filing.source_kind}:{filing.rcept_no}",
        source_kind=filing.source_kind,
        requirement=filing.requirement,
        state=c.ATTEMPT_STATE_TRUNCATED,
        slot_ids=c.SOURCE_KIND_SLOT_SCOPE[filing.source_kind],
        reason_code=c.REASON_DEADLINE_EXCEEDED,
        elapsed_ms=0,
        bytes_downloaded=0,
        documents_seen=0,
    )


def collect_dart_evidence(
    fetcher: DartFetcher,
    company_id: str,
    *,
    now: str,
    deadline_seconds: float = c.DEFAULT_COLLECTION_DEADLINE_SECONDS,
) -> DartEvidenceHarvest:
    """company_id(corp_code) 하나에 대한 DART 근거수집 전체를 실행한다.

    ``now``는 호출자가 넘기는 수집 시각 문자열이다(이 함수는 시계에 손대지
    않는다 — 시험이 시각을 고정할 수 있게).
    """
    deadline_at = time.monotonic() + deadline_seconds
    selection = filing_select.select_related_filings(fetcher, company_id)

    attempts: list[CollectionAttempt] = list(selection.attempts)
    documents: list[CollectedDocument] = []
    fragments: list[EvidenceFragment] = []
    seen_content_hashes: set[str] = set()
    total_bytes = 0

    for filing in selection.selected:
        if time.monotonic() > deadline_at:
            attempts.append(_deadline_attempt(filing))
            continue

        fetch_result = _safe_fetch_document(fetcher, filing.rcept_no)
        if fetch_result.state != c.ATTEMPT_STATE_OK:
            attempts.append(_document_attempt(
                filing, c.ATTEMPT_STATE_FAILED, c.REASON_DOCUMENT_FETCH_FAILED, fetch_result,
            ))
            continue

        text_bytes = len(fetch_result.text.encode("utf-8"))
        if text_bytes > c.MAX_DOCUMENT_TEXT_BYTES:
            attempts.append(_document_attempt(
                filing, c.ATTEMPT_STATE_FAILED, c.REASON_DOCUMENT_TOO_LARGE, fetch_result,
            ))
            continue
        if total_bytes + text_bytes > c.MAX_TOTAL_TEXT_BYTES:
            attempts.append(_document_attempt(
                filing, c.ATTEMPT_STATE_TRUNCATED, c.REASON_TOTAL_BYTES_EXCEEDED, fetch_result,
            ))
            continue
        total_bytes += text_bytes

        content_sha256 = hashlib.sha256(fetch_result.text.encode("utf-8")).hexdigest()
        document_id = f"{filing.source_kind}:{filing.rcept_no}"
        if content_sha256 in seen_content_hashes:
            attempts.append(_document_attempt(
                filing, c.ATTEMPT_STATE_OK, c.REASON_DOCUMENT_DUPLICATE, fetch_result,
            ))
            continue
        seen_content_hashes.add(content_sha256)

        candidates = segment.segment_document(fetch_result.text)
        usable_ranges = segment.usable_ranges_from_candidates(candidates)
        identity_binding = (
            f"corp_code={company_id};rcept_no={filing.rcept_no};source_kind={filing.source_kind}"
        )
        document = CollectedDocument(
            company_id=company_id,
            document_id=document_id,
            canonical_url=c.DART_DOCUMENT_URL_TEMPLATE.format(rcept_no=filing.rcept_no),
            source_tier=c.SOURCE_TIER_OFFICIAL,
            source_kind=filing.source_kind,
            publisher=c.DART_PUBLISHER_NAME,
            title=filing.report_nm,
            published_on=filing.rcept_dt,
            collected_at=now,
            content_sha256=content_sha256,
            identity_binding=identity_binding,
            usable_ranges=usable_ranges,
            collector_version=c.COLLECTOR_VERSION,
            parser_version=c.PARSER_VERSION,
            requirement=filing.requirement,
        )
        documents.append(document)
        attempts.append(_document_attempt(
            filing, c.ATTEMPT_STATE_OK, c.REASON_DOCUMENT_FETCH_OK, fetch_result,
        ))

        for index, candidate in enumerate(candidates):
            slot_score = relevance.score_fragment_text(candidate.text, candidate.section_heading)
            fragments.append(EvidenceFragment(
                fragment_id=f"{document_id}:frag{index}",
                document_id=document_id,
                location=f"{candidate.start}-{candidate.end}",
                text_sha256=hashlib.sha256(candidate.text.encode("utf-8")).hexdigest(),
                text=candidate.text,
                section_id=slot_score.section_id if slot_score else "",
                slot_id=slot_score.slot_id if slot_score else "",
                score_millis=slot_score.score_millis if slot_score else 0,
                reason_codes=slot_score.reason_codes if slot_score else (c.REASON_NO_SIGNAL,),
            ))

    company_type = classify.classify_company_type(documents, (fragment.text for fragment in fragments))

    return DartEvidenceHarvest(
        company_id=company_id,
        company_type=company_type,
        documents=tuple(documents),
        fragments=tuple(fragments),
        attempts=tuple(attempts),
    )
