"""관련 공시 묶음 선택 — 사업보고서 우선, 감사보고서 폴백, 정정공시 계보.

network 호출부는 이 모듈이 정의하는 `DartFetcher` Protocol로 감싼다. 실제
DART 연동은 다음 담당자가 `core/dart_client.py`를 재사용해 구현한다(이번
슬라이스는 미검증 — LIVE_COLLECTION_UNVERIFIED). 시험은 이 Protocol을
구현하는 가짜 객체와 로컬 fixture만 쓴다(실제 네트워크 접근 0건).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol

from features.evidence_collection import constants as c
from features.evidence_collection.models import CollectionAttempt


@dataclass(frozen=True)
class RawFilingRow:
    """list.json 응답 행 하나 — 이 feature가 실제로 쓰는 필드만 남긴다."""

    rcept_no: str
    report_nm: str
    rcept_dt: str


@dataclass(frozen=True)
class FilingListResult:
    """list.json 조회 1건의 결과. state가 FAILED면 rows는 의미가 없다."""

    state: str
    rows: tuple[RawFilingRow, ...] = ()
    elapsed_ms: int = 0
    bytes_downloaded: int = 0


@dataclass(frozen=True)
class DocumentFetchResult:
    """공시서류 원문 조회 1건의 결과. state가 FAILED면 text는 의미가 없다."""

    state: str
    text: str = ""
    elapsed_ms: int = 0
    bytes_downloaded: int = 0


class DartFetcher(Protocol):
    """이 feature가 필요로 하는 최소 네트워크 경계. 실제 구현은 주입한다."""

    def fetch_filing_list(self, company_id: str, pblntf_ty: str) -> FilingListResult:
        ...

    def fetch_document_text(self, rcept_no: str) -> DocumentFetchResult:
        ...


@dataclass(frozen=True)
class SelectedFiling:
    """묶음에 뽑힌 공시 1건 — 정정본을 썼다면 원공시 rcept_no를 계보에 남긴다."""

    source_kind: str
    requirement: str
    rcept_no: str
    report_nm: str
    rcept_dt: str
    lineage_original_rcept_no: str = ""


@dataclass(frozen=True)
class FilingSelectionResult:
    selected: tuple[SelectedFiling, ...]
    truncated: tuple[SelectedFiling, ...]
    attempts: tuple[CollectionAttempt, ...]


_CORRECTION_PREFIX_PATTERN = re.compile(
    rf"^\[[^\]]*{re.escape(c.CONTENT_CORRECTION_BRACKET_MARKER)}[^\]]*\]\s*"
)


def _safe_fetch_list(fetcher: DartFetcher, company_id: str, pblntf_ty: str) -> FilingListResult:
    # fetcher는 외부에서 주입되는 경계다. 여기서 예외를 흡수하지 않으면 조회
    # 실패 하나가 회사 전체 수집을 중단시킨다 — 「수집 장애」와 「자료 없음」을
    # 분리하는 요구사항(7번)을 지키려면 이 경계에서 반드시 잡아야 한다.
    try:
        return fetcher.fetch_filing_list(company_id, pblntf_ty)
    except Exception:  # noqa: BLE001 - fetcher 경계 흡수(위 사유)
        return FilingListResult(state=c.ATTEMPT_STATE_FAILED)


def _filter_rows(rows: tuple[RawFilingRow, ...], name_keyword: str) -> list[RawFilingRow]:
    return [
        row for row in rows
        if name_keyword in row.report_nm
        and c.CONSOLIDATED_REPORT_NAME_MARKER not in row.report_nm
        and not any(marker in row.report_nm for marker in c.EXCLUDED_REPORT_NAME_MARKERS)
    ]


def _pick_latest_with_lineage(rows: list[RawFilingRow]) -> tuple[RawFilingRow, str]:
    """가장 최근 정정본을 우선 채택하고, 있으면 원공시 rcept_no를 계보로 돌려준다.

    정정 판정은 report_nm이 「[...기재정정...]」로 «시작»할 때만이다(느슨한
    부분일치가 아니라 선두 대괄호 표기만 — 실측 근거 없는 패턴 추측을 피하기
    위해 가장 보수적인 규칙을 쓴다). 같은 대괄호를 뗀 나머지 이름이 완전히
    같고 접수번호가 더 이른 공시만 원공시 후보로 본다.
    """
    corrections: list[tuple[RawFilingRow, str]] = []
    plain: list[RawFilingRow] = []
    for row in rows:
        matched = _CORRECTION_PREFIX_PATTERN.match(row.report_nm)
        if matched:
            corrections.append((row, row.report_nm[matched.end():].strip()))
        else:
            plain.append(row)

    if corrections:
        chosen, base_name = max(corrections, key=lambda pair: pair[0].rcept_no)
        earlier_originals = [
            row for row in plain
            if row.report_nm.strip() == base_name and row.rcept_no < chosen.rcept_no
        ]
        original = max(earlier_originals, key=lambda row: row.rcept_no) if earlier_originals else None
        return chosen, (original.rcept_no if original else "")

    chosen = max(plain, key=lambda row: row.rcept_no)
    return chosen, ""


def _attempt_for_list_query(
    spec: c.FilingKindSpec, result: FilingListResult,
) -> tuple[CollectionAttempt, list[RawFilingRow]]:
    if result.state == c.ATTEMPT_STATE_FAILED:
        attempt = CollectionAttempt(
            attempt_id=f"list:{spec.source_kind}",
            source_kind=spec.source_kind,
            requirement=spec.requirement,
            state=c.ATTEMPT_STATE_FAILED,
            slot_ids=c.SOURCE_KIND_SLOT_SCOPE[spec.source_kind],
            reason_code=c.REASON_LIST_QUERY_FAILED,
            elapsed_ms=max(0, result.elapsed_ms),
            bytes_downloaded=max(0, result.bytes_downloaded),
            documents_seen=0,
        )
        return attempt, []

    filtered = _filter_rows(result.rows, spec.name_keyword)
    state = c.ATTEMPT_STATE_OK if filtered else c.ATTEMPT_STATE_MISSING
    reason = c.REASON_LIST_QUERY_OK if filtered else c.REASON_LIST_QUERY_MISSING
    attempt = CollectionAttempt(
        attempt_id=f"list:{spec.source_kind}",
        source_kind=spec.source_kind,
        requirement=spec.requirement,
        state=state,
        slot_ids=c.SOURCE_KIND_SLOT_SCOPE[spec.source_kind],
        reason_code=reason,
        elapsed_ms=max(0, result.elapsed_ms),
        bytes_downloaded=max(0, result.bytes_downloaded),
        documents_seen=len(result.rows),
    )
    return attempt, filtered


def select_related_filings(fetcher: DartFetcher, company_id: str) -> FilingSelectionResult:
    """관련 공시 묶음을 고른다 — 사업보고서 우선, 없으면 감사보고서(요구사항 1번).

    반기·분기보고서는 있으면 보충으로 더한다(OPTIONAL). 상한(MAX_RELATED_FILINGS)을
    넘는 후보는 TRUNCATED로 기록하고 뺀다 — 상한은 관측용이지 회사를 거절하는
    근거가 아니다.
    """
    attempts: list[CollectionAttempt] = []
    candidates: list[SelectedFiling] = []
    # pblntf_ty별 캐시 — 사업보고서(A)·반기(A)·분기(A)가 같은 pblntf_ty를
    # 쓰므로 실제 DART 호출은 최대 2번(A·F)만 하고 나머지는 클라이언트 쪽에서
    # report_nm 키워드로만 다시 나눈다(비용 상한 요구사항).
    list_result_cache: dict[str, FilingListResult] = {}

    def fetch_cached(pblntf_ty: str) -> FilingListResult:
        if pblntf_ty not in list_result_cache:
            list_result_cache[pblntf_ty] = _safe_fetch_list(fetcher, company_id, pblntf_ty)
        return list_result_cache[pblntf_ty]

    for source_kind in c.PRIMARY_LOOKUP_ORDER:
        spec = c.FILING_KIND_SPEC_BY_SOURCE_KIND[source_kind]
        result = fetch_cached(spec.pblntf_ty)
        attempt, filtered = _attempt_for_list_query(spec, result)
        attempts.append(attempt)
        if filtered:
            chosen, lineage_original = _pick_latest_with_lineage(filtered)
            candidates.append(SelectedFiling(
                source_kind=spec.source_kind,
                requirement=spec.requirement,
                rcept_no=chosen.rcept_no,
                report_nm=chosen.report_nm,
                rcept_dt=chosen.rcept_dt,
                lineage_original_rcept_no=lineage_original,
            ))
            break  # 사업보고서를 찾았으면 감사보고서 폴백은 시도하지 않는다

    for source_kind in c.SUPPLEMENT_LOOKUP_ORDER:
        spec = c.FILING_KIND_SPEC_BY_SOURCE_KIND[source_kind]
        result = fetch_cached(spec.pblntf_ty)
        attempt, filtered = _attempt_for_list_query(spec, result)
        attempts.append(attempt)
        if filtered:
            chosen, lineage_original = _pick_latest_with_lineage(filtered)
            candidates.append(SelectedFiling(
                source_kind=spec.source_kind,
                requirement=spec.requirement,
                rcept_no=chosen.rcept_no,
                report_nm=chosen.report_nm,
                rcept_dt=chosen.rcept_dt,
                lineage_original_rcept_no=lineage_original,
            ))

    selected = candidates[: c.MAX_RELATED_FILINGS]
    overflow = candidates[c.MAX_RELATED_FILINGS:]
    for extra in overflow:
        attempts.append(CollectionAttempt(
            attempt_id=f"cap:{extra.source_kind}:{extra.rcept_no}",
            source_kind=extra.source_kind,
            requirement=extra.requirement,
            state=c.ATTEMPT_STATE_TRUNCATED,
            slot_ids=c.SOURCE_KIND_SLOT_SCOPE[extra.source_kind],
            reason_code=c.REASON_CAP_REACHED,
            elapsed_ms=0,
            bytes_downloaded=0,
            documents_seen=1,
        ))

    return FilingSelectionResult(
        selected=tuple(selected),
        truncated=tuple(overflow),
        attempts=tuple(attempts),
    )
