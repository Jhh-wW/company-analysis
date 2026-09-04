"""DART 근거수집 조율 — filing_select → segment → relevance → classify를 묶어
DartEvidenceHarvest 하나를 만든다.

실제 DART 연동(fetcher 구현)은 이 슬라이스의 범위 밖이다 — LIVE_COLLECTION_UNVERIFIED.
여기서는 주입된 fetcher만 쓰고 실제 네트워크 접근을 하지 않는다.
"""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Callable
from datetime import date

from core.dart_client import DartAuthenticationError, DartLimitReached
from features.evidence_collection import classify, constants as c, filing_select, relevance, segment
from features.evidence_collection.fetch_failure import (
    is_recoverable_external_fetch_error,
)
from features.evidence_collection.filing_select import DartFetcher, DocumentFetchResult, SelectedFiling
from features.evidence_collection.models import (
    CollectedDocument,
    CollectionAttempt,
    DartEvidenceHarvest,
    EvidenceCollectionError,
    EvidenceFragment,
    OfficialUrlCandidate,
)


_DART_RECEIPT_DATE_RE = re.compile(r"\d{8}")


def _published_on_from_receipt_date(raw: str) -> str:
    """OpenDART YYYYMMDD 접수일을 공개 계약의 엄격 ISO 날짜로 바꾼다.

    목록 선택·정정본 정렬은 OpenDART 원문 ``rcept_dt``를 그대로 사용한다.
    문서 DTO를 만들 때만 변환해, 소비자가 같은 값을 서로 다르게 해석하지
    않게 한다. 모양만 8자리이거나 불가능한 날짜는 조용히 보정하지 않는다.
    """

    if not isinstance(raw, str) or _DART_RECEIPT_DATE_RE.fullmatch(raw) is None:
        raise EvidenceCollectionError("DART 접수일은 YYYYMMDD 8자리여야 합니다")
    normalized = f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
    try:
        date.fromisoformat(normalized)
    except ValueError as error:
        raise EvidenceCollectionError("DART 접수일이 실제 달력 날짜가 아닙니다") from error
    return normalized


def _safe_fetch_document(fetcher: DartFetcher, rcept_no: str) -> DocumentFetchResult:
    # filing_select._safe_fetch_list와 같이, 확인된 외부 수집 실패만 이
    # 경계에서 흡수한다(요구사항 7 — 「조회 실패」와 「자료 없음」 분리).
    # 포트 배선·코드 계약 오류는 전체 실행을 내부 오류로 닫도록 전파한다.
    try:
        return fetcher.fetch_document_text(rcept_no)
    except (DartLimitReached, DartAuthenticationError):
        # 전역 한도·인증 실패를 문서 한 건 실패로 축소하지 않는다. 호출자가
        # 즉시 전체 실행을 멈춰 추가 DART 호출·후속 AI 비용을 막아야 한다.
        raise
    except Exception as error:  # noqa: BLE001 - 닫힌 외부 오류만 아래서 흡수
        if not is_recoverable_external_fetch_error(error):
            # callback 시그니처·반환 자료형·구현 불변식 오류를 회사의 자료
            # 실패로 위장하지 않는다. app 경계가 내부 계약 오류로 닫는다.
            raise
        return DocumentFetchResult(state=c.ATTEMPT_STATE_FAILED)


def _document_attempt(
    company_id: str,
    filing: SelectedFiling,
    state: str,
    reason_code: str,
    fetch_result: DocumentFetchResult,
    *,
    documents_seen: int = 1,
    slot_ids: tuple[str, ...] | None = None,
    requirement: str | None = None,
) -> CollectionAttempt:
    """문서 단계 attempt 1건을 만든다.

    ★ item 2 불변식 — ``slot_ids``를 지정하지
    않으면 source_kind의 전체 범위(광역)를 쓴다. 이건 REQUIRED+OK/MISSING
    조합에서는 금지된 조합이므로(광역 slot 집합 = «이 자료가 없다»는 사실
    주장이 되어 버린다), 그 조합으로 호출하는 자리는 반드시 ``slot_ids``에
    «실제로 확인한 슬롯만»을 넘기거나, ``requirement=REQUIREMENT_OPTIONAL``로
    내려 불변식 예외 조건(b)를 타게 해야 한다. FAILED/TRUNCATED는 조건(a)로
    이미 예외라 그대로 둬도 된다.
    """
    return CollectionAttempt(
        company_id=company_id,
        attempt_id=f"document:{filing.source_kind}:{filing.rcept_no}",
        source_kind=filing.source_kind,
        requirement=requirement if requirement is not None else filing.requirement,
        state=state,
        slot_ids=slot_ids if slot_ids is not None else c.SOURCE_KIND_SLOT_SCOPE[filing.source_kind],
        reason_code=reason_code,
        elapsed_ms=max(0, fetch_result.elapsed_ms),
        bytes_downloaded=max(0, fetch_result.bytes_downloaded),
        documents_seen=documents_seen,
    )


def _unscored_fragments_attempt(
    company_id: str, filing: SelectedFiling, unscored_count: int,
) -> CollectionAttempt:
    """무신호 문단 개수를 관측치로만 남긴다 — 조각 자체는 harvest에 넣지 않는다.

    fetch·분할·채점을 실제로 거친 뒤 나오는 attempt이므로(item 2 정정)
    requirement는 filing.requirement(REQUIRED) 그대로 둔다.
    """
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
    short_observation_filter: Callable[[str], bool] | None = None,
) -> DartEvidenceHarvest:
    """company_id(corp_code) 하나에 대한 DART 근거수집 전체를 실행한다.

    ``now``는 호출자가 넘기는 수집 시각 문자열이다(이 함수는 시계에 손대지
    않는다 — 시험이 시각을 고정할 수 있게).

    최종 ``fragments``에는 채점(scored)된 조각만 남는다(section_id·slot_id가
    모두 채워진 것). 무신호 문단은 근거인 척 섞지 않고
    ``unclassified_fragments``와 ``unclassified_documents``에 원문·위치·해시를
    별도로 보존한다. 개수만 attempt에 남기고 원문을 버리던 옛 동작은 분류기
    어휘가 좁은 내부 결함을 「회사 자료가 없음」으로 바꿨다.

    ``documents``·``fragments``·``attempts`` 전부가 이 함수에 넘긴
    ``company_id``를 자기 필드로 직접 싣는다(generation=8) — 만드는
    자리에서 실제 대상 회사 값을 넣고,
    DartEvidenceHarvest 생성 시 하나라도 다르면 즉시 거절된다. 「회사별로
    따로 수집하니 섞일 리 없다」는 호출자 기억에 기대지 않는다.
    """
    deadline_at = time.monotonic() + deadline_seconds
    selection = filing_select.select_related_filings(fetcher, company_id, deadline_at=deadline_at)

    attempts: list[CollectionAttempt] = list(selection.attempts)
    documents: list[CollectedDocument] = []
    fragments: list[EvidenceFragment] = []
    unclassified_documents: list[CollectedDocument] = []
    unclassified_fragments: list[EvidenceFragment] = []
    # classify는 채점 여부와 무관하게 문서 전체 원문을 훑어야 한다(예:
    # 「매출액」 단독은 v1 키워드 어휘에서 일부러 뺐다 — relevance.py 주석
    # 참고 — 그래서 채점되지 않는 문단에도 있을 수 있다). fragments(=채점된
    # 것만)와는 별도로 모든 후보 문단 원문을 따로 모은다.
    classify_probe_texts: list[str] = []
    official_url_candidates: list[OfficialUrlCandidate] = []
    seen_official_candidate_urls: set[str] = set()
    seen_content_hashes: set[str] = set()
    total_bytes = 0

    for filing in selection.selected:
        if time.monotonic() > deadline_at:
            attempts.append(_deadline_attempt(company_id, filing))
            continue

        try:
            published_on = _published_on_from_receipt_date(filing.rcept_dt)
        except EvidenceCollectionError:
            # 잘못된 접수일을 빈 날짜나 비슷한 값으로 보정하면 수집은 성공한
            # 것처럼 보였다가 공개 Source 봉인에서 늦게 깨진다. DART 원자료
            # 이상을 이 문서의 REQUIRED+FAILED로 즉시 남기고 호출도 하지 않는다.
            attempts.append(
                _document_attempt(
                    company_id,
                    filing,
                    c.ATTEMPT_STATE_FAILED,
                    c.REASON_FILING_RECEIPT_DATE_INVALID,
                    DocumentFetchResult(state=c.ATTEMPT_STATE_FAILED),
                    documents_seen=0,
                )
            )
            continue

        fetch_result = _safe_fetch_document(fetcher, filing.rcept_no)

        if fetch_result.state == c.ATTEMPT_STATE_MISSING:
            # 확인된 부재 — 전송 장애(FAILED)와 분리해서 남긴다.
            # ★ item 2 정정 — fetcher가 「이
            # 문서는 원래 없다」고 확인한 것은 목록 단계 MISSING과 같은
            # 성격의 «참인 확인»이다. REQUIRED+광역을 유지한다(다운그레이드
            # 하지 않는다).
            attempts.append(_document_attempt(
                company_id, filing, c.ATTEMPT_STATE_MISSING, c.REASON_DOCUMENT_FETCH_MISSING, fetch_result,
            ))
            continue
        if fetch_result.state != c.ATTEMPT_STATE_OK:
            # FAILED뿐 아니라 알 수 없는 state 문자열도 여기서 fail-closed로
            # FAILED 처리한다 — 「모르는 상태」를 「확인된 부재」로
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

        # URL 발견 provenance는 평문 본문 중복 제거와 독립된 산출물이다.
        # 태그를 지운 평문 SHA가 같아도 정정 XML의 href는 달라질 수 있으므로,
        # duplicate ``continue``보다 먼저 receipt/location/raw hash를 보존한다.
        # 여기서 공식 사이트로 승격하지 않으며 app이 실제 대상 HTML의
        # 법인명+등록번호를 다시 확인한 후보만 사용한다.
        for discovered in fetch_result.official_url_candidates:
            if len(official_url_candidates) >= c.MAX_OFFICIAL_URL_CANDIDATES:
                break
            if discovered.url in seen_official_candidate_urls:
                continue
            try:
                candidate = OfficialUrlCandidate(
                    company_id=company_id,
                    url=discovered.url,
                    source_document_id=document_id,
                    source_receipt_no=filing.rcept_no,
                    source_member_name=discovered.source_member_name,
                    source_location=discovered.location,
                    source_document_sha256=content_sha256,
                    source_payload_sha256=discovered.source_payload_sha256,
                )
            except EvidenceCollectionError:
                continue
            official_url_candidates.append(candidate)
            seen_official_candidate_urls.add(discovered.url)

        if content_sha256 in seen_content_hashes:
            # 중복이면 total_bytes에 가산하지 않는다(P1-5) — 가산 후 중복
            # 판정을 하면 실제로 쓰이지 않는 바이트가 예산을 유령처럼
            # 소비해 무관한 다음 문서가 부당하게 TRUNCATED될 수 있었다.
            # ★ item 2 정정 — 이 문서는 분할·채점을 «아예 건너뛴다»(원본
            # 문서만 실제로 훑었다). 「fetch·분할·채점을 실제로 거친 OK」만
            # 광역+REQUIRED가 정직하다는 것이 기준이고, 이
            # 경로는 그 파이프라인을 안 거치므로 여전히 OPTIONAL로 낮춘다.
            attempts.append(_document_attempt(
                company_id, filing, c.ATTEMPT_STATE_OK, c.REASON_DOCUMENT_DUPLICATE, fetch_result,
                requirement=c.REQUIREMENT_OPTIONAL,
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

        segmentation = segment.segment_document_with_status(fetch_result.text)
        candidates = list(segmentation.candidates)
        segmentation_truncation_reason = segmentation.truncation_reason
        short_segmentation = segment.segment_short_observation_candidates_with_status(
            fetch_result.text,
            candidate_filter=short_observation_filter,
        )
        short_candidates = list(short_segmentation.candidates)
        # 정식 호출자가 짧은 후보 의미를 filter로 선언했을 때만 그 scope의
        # 완전성을 main attempt에 합친다. filter 없는 옛 v1/SHADOW 호출은
        # 이 보조 관측 차선을 출고 근거로 의존하지 않으므로 소급 차단하지 않는다.
        if short_observation_filter is not None and short_segmentation.truncation_reason:
            segmentation_truncation_reason = (
                segmentation_truncation_reason
                or short_segmentation.truncation_reason
            )
        classify_probe_texts.extend(
            candidate.text for candidate in (*candidates, *short_candidates)
        )
        short_observations = [
            (candidate_index, candidate)
            for candidate_index, candidate in enumerate(short_candidates)
        ]

        scored: list[
            tuple[int, segment.FragmentCandidate, tuple[relevance.SlotScore, ...]]
        ] = []
        unscored: list[tuple[int, segment.FragmentCandidate]] = []
        allowed_slot_ids = frozenset(
            c.SOURCE_KIND_SLOT_SCOPE[filing.source_kind]
        )
        for candidate_index, candidate in enumerate(candidates):
            slot_scores, has_any_direct_signal = (
                relevance.score_fragment_slots_with_signal(
                    candidate.text,
                    candidate.section_heading,
                    allowed_slot_ids=allowed_slot_ids,
                )
            )
            if not slot_scores:
                # 분류기가 뜻을 전혀 못 알아본 원문만 무분류 차선에 둔다.
                # 반기·분기 자료가 회사 개요처럼 자기 소유 밖 슬롯의 신호를
                # 가진 경우는 이미 분류된 문단이다. 이를 무분류로 바꾸면
                # 후단이 classifier coverage gap으로 오판한다.
                if not has_any_direct_signal:
                    unscored.append((candidate_index, candidate))
            else:
                scored.append((candidate_index, candidate, slot_scores))

        unclassified_candidates = [
            (f"unclassified{candidate_index}", candidate)
            for candidate_index, candidate in unscored
        ] + [
            (f"short{candidate_index}", candidate)
            for candidate_index, candidate in short_observations
        ]

        identity_binding = _identity_binding(company_id, filing, fetch_result)
        try:
            if unclassified_candidates:
                unclassified_document = CollectedDocument(
                    company_id=company_id,
                    document_id=document_id,
                    canonical_url=c.DART_DOCUMENT_URL_TEMPLATE.format(
                        rcept_no=filing.rcept_no
                    ),
                    source_tier=c.SOURCE_TIER_OFFICIAL,
                    source_kind=filing.source_kind,
                    publisher=c.DART_PUBLISHER_NAME,
                    title=filing.report_nm,
                    published_on=published_on,
                    collected_at=now,
                    content_sha256=content_sha256,
                    identity_binding=identity_binding,
                    usable_ranges=segment.usable_ranges_from_candidates(
                        [
                            candidate
                            for _suffix, candidate in unclassified_candidates
                        ]
                    ),
                    collector_version=c.COLLECTOR_VERSION,
                    parser_version=c.PARSER_VERSION,
                    requirement=filing.requirement,
                )
                new_unclassified_fragments = [
                    EvidenceFragment(
                        company_id=company_id,
                        fragment_id=(
                            f"{document_id}:{candidate_suffix}"
                        ),
                        document_id=document_id,
                        location=f"{candidate.start}-{candidate.end}",
                        text_sha256=hashlib.sha256(
                            candidate.text.encode("utf-8")
                        ).hexdigest(),
                        text=candidate.text,
                        section_id="",
                        slot_id="",
                        score_millis=0,
                        reason_codes=(c.REASON_NO_SIGNAL,),
                        covered_slot_ids=(),
                    )
                    for candidate_suffix, candidate in unclassified_candidates
                ]
            else:
                unclassified_document = None
                new_unclassified_fragments = []
        except EvidenceCollectionError:
            attempts.append(_document_attempt(
                company_id,
                filing,
                c.ATTEMPT_STATE_FAILED,
                c.REASON_DOCUMENT_MODEL_INVALID,
                fetch_result,
            ))
            continue

        if unclassified_document is not None:
            unclassified_documents.append(unclassified_document)
            unclassified_fragments.extend(new_unclassified_fragments)

        if not scored:
            # 채점 가능한 근거가 하나도 없다 — 보고서 근거 documents에서는
            # 빼되 무분류 차선에는 원문을 이미 보존했다.
            # 후보 상한에 닿지 않은 경우에만 fetch·분할·채점을 실제로 다
            # 거쳐 문서 전문을 훑었다. 그때만 광역 slot_ids + REQUIRED + OK가
            # 정직하다. 상한에 닿은 경우는 아래에서 TRUNCATED로 갈린다.
            # 후보/문자/제목 상한에 닿았으면 문서 뒷부분을 안 본 것이다.
            # 일부에서 점수가 없었다는 사실을 «전문에 근거가 없다»는 OK로
            # 확대하지 않고 TRUNCATED로 남겨 AI 전 진단이 내부 완전성 문제로
            # 멈추게 한다.
            attempts.append(_document_attempt(
                company_id,
                filing,
                (
                    c.ATTEMPT_STATE_TRUNCATED
                    if segmentation_truncation_reason
                    else c.ATTEMPT_STATE_OK
                ),
                segmentation_truncation_reason
                or c.REASON_DOCUMENT_NO_SCORED_EVIDENCE,
                fetch_result,
                documents_seen=len(candidates) + len(short_candidates),
            ))
            continue

        # 한 문단이 여러 의미 칸을 직접 뒷받침해도 원문 구간은 문서에 한 번만
        # 기록한다. 슬롯별 fragment는 아래에서 갈라지지만 provenance 구간을
        # 복제해 겹치게 만들지는 않는다.
        usable_ranges = segment.usable_ranges_from_candidates(
            [candidate for _index, candidate, _slot_scores in scored]
        )

        try:
            document = CollectedDocument(
                company_id=company_id,
                document_id=document_id,
                canonical_url=c.DART_DOCUMENT_URL_TEMPLATE.format(rcept_no=filing.rcept_no),
                source_tier=c.SOURCE_TIER_OFFICIAL,
                source_kind=filing.source_kind,
                publisher=c.DART_PUBLISHER_NAME,
                title=filing.report_nm,
                published_on=published_on,
                collected_at=now,
                content_sha256=content_sha256,
                identity_binding=identity_binding,
                usable_ranges=usable_ranges,
                collector_version=c.COLLECTOR_VERSION,
                parser_version=c.PARSER_VERSION,
                requirement=filing.requirement,
            )
            new_fragments = []
            for candidate_index, candidate, slot_scores in scored:
                # 한 원문 범위가 여러 슬롯을 직접 뒷받침해도 원문·토큰을
                # 슬롯 수만큼 복제하지 않는다. 가장 높은 점수 슬롯을 대표로
                # 두고 같은 장의 전체 커버리지를 ID 하나에 함께 봉인한다.
                primary_score = slot_scores[0]
                reason_codes = tuple(
                    dict.fromkeys(
                        reason_code
                        for slot_score in slot_scores
                        for reason_code in slot_score.reason_codes
                    )
                )
                new_fragments.append(
                    EvidenceFragment(
                        company_id=company_id,
                        fragment_id=f"{document_id}:frag{candidate_index}",
                        document_id=document_id,
                        location=f"{candidate.start}-{candidate.end}",
                        text_sha256=hashlib.sha256(candidate.text.encode("utf-8")).hexdigest(),
                        text=candidate.text,
                        section_id=primary_score.section_id,
                        slot_id=primary_score.slot_id,
                        score_millis=primary_score.score_millis,
                        reason_codes=reason_codes,
                        covered_slot_ids=tuple(
                            slot_score.slot_id for slot_score in slot_scores
                        ),
                    )
                )
        except EvidenceCollectionError:
            # 자료형 검증 실패 하나가 harvest 전체(이미 쌓인 attempts 포함)를
            # 무너뜨리지 않게 이 문서만 버리고 다음 문서로 넘어간다(P1-2).
            attempts.append(_document_attempt(
                company_id, filing, c.ATTEMPT_STATE_FAILED, c.REASON_DOCUMENT_MODEL_INVALID, fetch_result,
            ))
            continue

        documents.append(document)
        fragments.extend(new_fragments)
        # 「REQUIRED+OK+광역 slot_ids」는 전문을 끝까지 훑은 경우에만
        # 정직하다. 후보 상한에 닿았으면 부분 근거는 보존하되 이 attempt는
        # TRUNCATED로 남겨 미검사 뒷부분을 없다고 주장하지 않는다.
        attempts.append(_document_attempt(
            company_id,
            filing,
            (
                c.ATTEMPT_STATE_TRUNCATED
                if segmentation_truncation_reason
                else c.ATTEMPT_STATE_OK
            ),
            segmentation_truncation_reason or c.REASON_DOCUMENT_FETCH_OK,
            fetch_result,
        ))
        if unclassified_candidates and not segmentation_truncation_reason:
            # 이 attempt도 같은 fetch·분할·채점 파이프라인을 거쳤으므로
            # 광역+REQUIRED가 정직하다(위와 같은 이유) — 다운그레이드하지 않는다.
            attempts.append(
                _unscored_fragments_attempt(
                    company_id,
                    filing,
                    len(unclassified_candidates),
                )
            )

    # 회사 유형은 의미 칸 분류 성공 여부와 무관하게 실제로 연 문서 종류를
    # 봐야 한다. 무분류 문서만 있었다고 상장/외감 여부까지 「모름」으로
    # 되돌리지 않는다. 같은 document_id는 한 번만 넘긴다.
    classification_documents = {
        document.document_id: document
        for document in (*documents, *unclassified_documents)
    }
    company_type = classify.classify_company_type(
        classification_documents.values(), classify_probe_texts, attempts=attempts
    )

    return DartEvidenceHarvest(
        company_id=company_id,
        company_type=company_type,
        documents=tuple(documents),
        fragments=tuple(fragments),
        attempts=tuple(attempts),
        unclassified_documents=tuple(unclassified_documents),
        unclassified_fragments=tuple(unclassified_fragments),
        official_url_candidates=tuple(official_url_candidates),
    )
