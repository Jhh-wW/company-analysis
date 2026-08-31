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
    EvidenceCollectionError,
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
    company_id: str,
    filing: SelectedFiling,
    state: str,
    reason_code: str,
    fetch_result: DocumentFetchResult,
    *,
    documents_seen: int = 1,
) -> CollectionAttempt:
    return CollectionAttempt(
        company_id=company_id,
        attempt_id=f"document:{filing.source_kind}:{filing.rcept_no}",
        source_kind=filing.source_kind,
        requirement=filing.requirement,
        state=state,
        slot_ids=c.SOURCE_KIND_SLOT_SCOPE[filing.source_kind],
        reason_code=reason_code,
        elapsed_ms=max(0, fetch_result.elapsed_ms),
        bytes_downloaded=max(0, fetch_result.bytes_downloaded),
        documents_seen=documents_seen,
    )


def _unscored_fragments_attempt(
    company_id: str, filing: SelectedFiling, unscored_count: int,
) -> CollectionAttempt:
    """무신호 문단 개수를 관측치로만 남긴다(P0-1) — 조각 자체는 harvest에 넣지 않는다."""
    return CollectionAttempt(
        company_id=company_id,
        attempt_id=f"fragments:{filing.source_kind}:{filing.rcept_no}",
        source_kind=filing.source_kind,
        requirement=filing.requirement,
        state=c.ATTEMPT_STATE_OK,
        slot_ids=c.SOURCE_KIND_SLOT_SCOPE[filing.source_kind],
        reason_code=c.REASON_NO_SIGNAL,
        elapsed_ms=0,
        bytes_downloaded=0,
        documents_seen=unscored_count,
    )


def _deadline_attempt(company_id: str, filing: SelectedFiling) -> CollectionAttempt:
    return CollectionAttempt(
        company_id=company_id,
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


def _identity_binding(company_id: str, filing: SelectedFiling, fetch_result: DocumentFetchResult) -> str:
    """요청 corp_code와 fetcher 메타를 정직하게 대조한 결과까지 문자열에 남긴다(P1-4).

    fetcher가 corp_code를 돌려주지 못하면(메타 없음) 「검증했다」고 거짓으로
    주장하지 않고 unverifiable로 남긴다 — 실제 mismatch는 이 함수 호출 전에
    이미 걸러졌으므로 여기 도달했다면 일치하거나 확인 불가한 경우뿐이다.
    """
    verified = bool(fetch_result.corp_code)
    check = c.IDENTITY_CHECK_VERIFIED if verified else c.IDENTITY_CHECK_UNVERIFIED
    return (
        f"corp_code={company_id};rcept_no={filing.rcept_no};source_kind={filing.source_kind};"
        f"identity_check={check}"
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

    최종 ``fragments``에는 채점(scored)된 조각만 남는다(section_id·slot_id가
    모두 채워진 것) — app 계약이 빈 값을 거절하기 때문이다(P0-1). 무신호
    문단은 harvest 밖으로 사라지지 않고 ``attempts``에 개수·사유 코드로
    남는다. 같은 이유로 채점된 조각이 하나도 없는 문서는 ``documents``에도
    올라가지 않는다(P0-3) — 「조회했다」는 사실 자체는 attempt로 보존한다.

    ``documents``·``fragments``·``attempts`` 전부가 이 함수에 넘긴
    ``company_id``를 자기 필드로 직접 싣는다(generation=8, 2026-08-31
    team-lead 통보) — 만드는 자리에서 실제 대상 회사 값을 넣고,
    DartEvidenceHarvest 생성 시 하나라도 다르면 즉시 거절된다. 「회사별로
    따로 수집하니 섞일 리 없다」는 호출자 기억에 기대지 않는다.
    """
    deadline_at = time.monotonic() + deadline_seconds
    selection = filing_select.select_related_filings(fetcher, company_id, deadline_at=deadline_at)

    attempts: list[CollectionAttempt] = list(selection.attempts)
    documents: list[CollectedDocument] = []
    fragments: list[EvidenceFragment] = []
    # classify는 채점 여부와 무관하게 문서 전체 원문을 훑어야 한다(예:
    # 「매출액」 단독은 v1 키워드 어휘에서 일부러 뺐다 — relevance.py 주석
    # 참고 — 그래서 채점되지 않는 문단에도 있을 수 있다). fragments(=채점된
    # 것만)와는 별도로 모든 후보 문단 원문을 따로 모은다.
    classify_probe_texts: list[str] = []
    seen_content_hashes: set[str] = set()
    total_bytes = 0

    for filing in selection.selected:
        if time.monotonic() > deadline_at:
            attempts.append(_deadline_attempt(company_id, filing))
            continue

        fetch_result = _safe_fetch_document(fetcher, filing.rcept_no)

        if fetch_result.state == c.ATTEMPT_STATE_MISSING:
            # 확인된 부재(P0-2) — 전송 장애(FAILED)와 분리해서 남긴다.
            attempts.append(_document_attempt(
                company_id, filing, c.ATTEMPT_STATE_MISSING, c.REASON_DOCUMENT_FETCH_MISSING, fetch_result,
            ))
            continue
        if fetch_result.state != c.ATTEMPT_STATE_OK:
            # FAILED뿐 아니라 알 수 없는 state 문자열도 여기서 fail-closed로
            # FAILED 처리한다(P0-2) — 「모르는 상태」를 「확인된 부재」로
            # 착각하지 않는다.
            attempts.append(_document_attempt(
                company_id, filing, c.ATTEMPT_STATE_FAILED, c.REASON_DOCUMENT_FETCH_FAILED, fetch_result,
            ))
            continue

        if fetch_result.corp_code and fetch_result.corp_code != company_id:
            # 다른 회사 문서가 섞여 들어오는 것을 막는다(P1-4).
            attempts.append(_document_attempt(
                company_id, filing, c.ATTEMPT_STATE_FAILED, c.REASON_DOCUMENT_IDENTITY_MISMATCH, fetch_result,
            ))
            continue

        text_bytes = len(fetch_result.text.encode("utf-8"))
        if text_bytes > c.MAX_DOCUMENT_TEXT_BYTES:
            attempts.append(_document_attempt(
                company_id, filing, c.ATTEMPT_STATE_FAILED, c.REASON_DOCUMENT_TOO_LARGE, fetch_result,
            ))
            continue

        content_sha256 = hashlib.sha256(fetch_result.text.encode("utf-8")).hexdigest()
        document_id = f"{filing.source_kind}:{filing.rcept_no}"
        if content_sha256 in seen_content_hashes:
            # 중복이면 total_bytes에 가산하지 않는다(P1-5) — 가산 후 중복
            # 판정을 하면 실제로 쓰이지 않는 바이트가 예산을 유령처럼
            # 소비해 무관한 다음 문서가 부당하게 TRUNCATED될 수 있었다.
            attempts.append(_document_attempt(
                company_id, filing, c.ATTEMPT_STATE_OK, c.REASON_DOCUMENT_DUPLICATE, fetch_result,
            ))
            continue

        if total_bytes + text_bytes > c.MAX_TOTAL_TEXT_BYTES:
            attempts.append(_document_attempt(
                company_id, filing, c.ATTEMPT_STATE_TRUNCATED, c.REASON_TOTAL_BYTES_EXCEEDED, fetch_result,
            ))
            continue
        total_bytes += text_bytes
        seen_content_hashes.add(content_sha256)

        if time.monotonic() > deadline_at:
            # 조회 자체가 느려 deadline을 넘겼는데도 분할·채점이 검사 없이
            # 진행되던 결함(P1-3) — 조회 직후 다시 확인한다.
            attempts.append(_document_attempt(
                company_id, filing, c.ATTEMPT_STATE_TRUNCATED, c.REASON_DEADLINE_EXCEEDED, fetch_result,
            ))
            continue

        candidates = segment.segment_document(fetch_result.text)
        classify_probe_texts.extend(candidate.text for candidate in candidates)

        scored: list[tuple[segment.FragmentCandidate, relevance.SlotScore]] = []
        unscored_count = 0
        for candidate in candidates:
            slot_score = relevance.score_fragment_text(candidate.text, candidate.section_heading)
            if slot_score is None:
                unscored_count += 1
            else:
                scored.append((candidate, slot_score))

        if not scored:
            # 채점 가능한 근거가 하나도 없다 — 문서 자체를 최종 산출에서
            # 뺀다(P0-3). 조회는 성공했다는 사실만 attempt로 남긴다.
            attempts.append(_document_attempt(
                company_id, filing, c.ATTEMPT_STATE_OK, c.REASON_DOCUMENT_NO_SCORED_EVIDENCE, fetch_result,
                documents_seen=len(candidates),
            ))
            continue

        usable_ranges = segment.usable_ranges_from_candidates([candidate for candidate, _ in scored])
        identity_binding = _identity_binding(company_id, filing, fetch_result)

        try:
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
            new_fragments = [
                EvidenceFragment(
                    company_id=company_id,
                    fragment_id=f"{document_id}:frag{index}",
                    document_id=document_id,
                    location=f"{candidate.start}-{candidate.end}",
                    text_sha256=hashlib.sha256(candidate.text.encode("utf-8")).hexdigest(),
                    text=candidate.text,
                    section_id=slot_score.section_id,
                    slot_id=slot_score.slot_id,
                    score_millis=slot_score.score_millis,
                    reason_codes=slot_score.reason_codes,
                )
                for index, (candidate, slot_score) in enumerate(scored)
            ]
        except EvidenceCollectionError:
            # 자료형 검증 실패 하나가 harvest 전체(이미 쌓인 attempts 포함)를
            # 무너뜨리지 않게 이 문서만 버리고 다음 문서로 넘어간다(P1-2).
            attempts.append(_document_attempt(
                company_id, filing, c.ATTEMPT_STATE_FAILED, c.REASON_DOCUMENT_MODEL_INVALID, fetch_result,
            ))
            continue

        documents.append(document)
        fragments.extend(new_fragments)
        attempts.append(_document_attempt(
            company_id, filing, c.ATTEMPT_STATE_OK, c.REASON_DOCUMENT_FETCH_OK, fetch_result,
        ))
        if unscored_count:
            attempts.append(_unscored_fragments_attempt(company_id, filing, unscored_count))

    company_type = classify.classify_company_type(documents, classify_probe_texts, attempts=attempts)

    return DartEvidenceHarvest(
        company_id=company_id,
        company_type=company_type,
        documents=tuple(documents),
        fragments=tuple(fragments),
        attempts=tuple(attempts),
    )
