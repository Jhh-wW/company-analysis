"""관련 공시 묶음 선택 — 사업보고서 우선, 감사보고서 폴백, 정정공시 계보.

network 호출부는 이 모듈이 정의하는 `DartFetcher` Protocol로 감싼다. 실제
DART 연동은 다음 담당자가 `core/dart_client.py`를 재사용해 구현한다(이번
슬라이스는 미검증 — LIVE_COLLECTION_UNVERIFIED). 시험은 이 Protocol을
구현하는 가짜 객체와 로컬 fixture만 쓴다(실제 네트워크 접근 0건).
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import Protocol

from features.evidence_collection import constants as c
from features.evidence_collection.models import CollectionAttempt


@dataclass(frozen=True)
class RawFilingRow:
    """list.json 응답 행 하나 — 이 feature가 실제로 쓰는 필드만 남긴다.

    ★ item 3(2026-08-31 team-lead 통보) — ``corp_code``·``corp_name``은
    fetcher가 방어적으로(``.get``) 읽어 실어 주면 요청 회사와 대조하는 데
    쓴다. 필드가 실제로 list.json 응답에 오는지는 실측하지 못했다(확인 못
    함 — live smoke 필요) — 그래서 기본값은 빈 문자열이고, 비어 있으면
    지금처럼 대조 없이 통과시킨다(«불일치»가 아니라 «확인 못 함»).
    """

    rcept_no: str
    report_nm: str
    rcept_dt: str
    corp_code: str = ""
    corp_name: str = ""


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
    #: fetcher가 실제로 확인한 문서 소유 회사 corp_code(P1-4). fetcher가 이
    #: 신원을 돌려주지 못하면(예: DART document.xml 응답 자체에는 구조화된
    #: corp_code가 없다) 빈 문자열로 둔다 — 「대조했다」고 거짓 주장하지
    #: 않기 위함이다. 값이 있고 요청 corp_code와 다르면 collect.py가 그
    #: 문서를 버린다.
    corp_code: str = ""


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
#: 정정 계보 이름 대조용 — 괄호 앞뒤 공백 차이(「사업보고서(2025.03)」 대
#: 「사업보고서 (2025.03)」)만으로 계보가 끊기지 않게 모든 공백을 지우고
#: 비교한다(P2, 2026-08-31). 원공시·정정본 판정 자체(정규식 매칭)는 그대로다
#: — 이건 어디까지나 「같은 이름인지」 비교 시의 공백 관용이다.
_WHITESPACE_PATTERN = re.compile(r"\s+")


def _normalize_report_name(name: str) -> str:
    return _WHITESPACE_PATTERN.sub("", name)


def _safe_fetch_list(fetcher: DartFetcher, company_id: str, pblntf_ty: str) -> FilingListResult:
    # fetcher는 외부에서 주입되는 경계다. 여기서 예외를 흡수하지 않으면 조회
    # 실패 하나가 회사 전체 수집을 중단시킨다 — 「수집 장애」와 「자료 없음」을
    # 분리하는 요구사항(7번)을 지키려면 이 경계에서 반드시 잡아야 한다.
    try:
        return fetcher.fetch_filing_list(company_id, pblntf_ty)
    except Exception:  # noqa: BLE001 - fetcher 경계 흡수(위 사유)
        return FilingListResult(state=c.ATTEMPT_STATE_FAILED)


def _filter_rows(
    rows: tuple[RawFilingRow, ...], name_keyword: str, company_id: str,
) -> tuple[list[RawFilingRow], int]:
    """이름 키워드·연결/정정 제외로 거르고, corp_code가 있는데 요청 회사와
    다르면 문서를 조회하지도 않고 목록 단계에서 미리 버린다(item 3).

    corp_code가 없는 행은 지금처럼 통과시킨다(«확인 못 함»이지 «불일치»가
    아니다 — 실제 응답에 이 필드가 오는지 실측하지 못했다). 몇 건이
    corp_code 불일치로 걸러졌는지 세어 함께 돌려준다(전용 사유 코드로
    남기기 위함, item 3).
    """
    matched: list[RawFilingRow] = []
    identity_mismatch_count = 0
    for row in rows:
        if name_keyword not in row.report_nm:
            continue
        if c.CONSOLIDATED_REPORT_NAME_MARKER in row.report_nm:
            continue
        if any(marker in row.report_nm for marker in c.EXCLUDED_REPORT_NAME_MARKERS):
            continue
        if row.corp_code and row.corp_code != company_id:
            identity_mismatch_count += 1
            continue
        matched.append(row)
    return matched, identity_mismatch_count


def _pick_latest_with_lineage(rows: list[RawFilingRow]) -> tuple[RawFilingRow, str]:
    """가장 최근 공시를 고르고, 그것이 정정본이면 원공시 rcept_no를 계보로 남긴다.

    ★ P0-6 수정(2026-08-31, 3관점 독립 확정) — 예전 코드는 정정본이 하나라도
    있으면 «정정본 그룹 안에서만» 최신을 골랐다. 조회 창에 여러 사업연도가
    들어오면(연 단위 조회라 흔하다) 옛 연도의 정정본이 더 최신인 다음 연도
    원공시를 밀어내는 결함이 있었다. 지금은 정정본·원공시를 «같은 무대»에서
    접수번호로만 비교한다 — 정정은 항상 원공시보다 나중에 접수되므로, 같은
    문서 계보 안에서는 이 비교만으로도 정정본이 자연히 이긴다. 서로 다른
    계보(다른 사업연도)면 접수번호가 더 큰(=더 최신인) 쪽이 그대로 이긴다.

    정정 판정은 report_nm이 「[...기재정정...]」로 «시작»할 때만이다(느슨한
    부분일치가 아니라 선두 대괄호 표기만 — 실측 근거 없는 패턴 추측을 피하기
    위해 가장 보수적인 규칙을 쓴다). 같은 대괄호를 뗀 나머지 이름이 완전히
    같고 접수번호가 더 이른 공시만 원공시 후보로 본다.
    """
    correction_base_name: dict[str, str] = {}
    for row in rows:
        matched = _CORRECTION_PREFIX_PATTERN.match(row.report_nm)
        if matched:
            correction_base_name[row.rcept_no] = row.report_nm[matched.end():].strip()

    chosen = max(rows, key=lambda row: row.rcept_no)
    base_name = correction_base_name.get(chosen.rcept_no)
    if base_name is None:
        return chosen, ""  # 정정본이 아니라 원공시가 최신 — 계보 없음

    normalized_base_name = _normalize_report_name(base_name)
    earlier_originals = [
        row for row in rows
        if row.rcept_no not in correction_base_name  # 정정본이 아닌 것(원공시 후보)만
        and _normalize_report_name(row.report_nm) == normalized_base_name
        and row.rcept_no < chosen.rcept_no
    ]
    original = max(earlier_originals, key=lambda row: row.rcept_no) if earlier_originals else None
    return chosen, (original.rcept_no if original else "")


def _attempt_for_list_query(
    company_id: str, spec: c.FilingKindSpec, result: FilingListResult,
) -> tuple[CollectionAttempt, list[RawFilingRow], int]:
    """목록 조회 attempt 1건을 만든다. FAILED가 아니면 identity_mismatch_count도 함께 돌려준다.

    ★ item 2(불변식, 2026-08-31 team-lead 통보) — 목록 조회는 문서 내용을
    한 번도 보지 않았으므로 REQUIRED+OK/MISSING로 slot_ids(source_kind
    전체 범위)를 «확인했다»고 주장하면 안 된다(넓은 slot 집합 + REQUIRED +
    OK/MISSING 조합 금지). FAILED일 때만 REQUIRED를 유지하고(P1-1의
    필수 목록 조회 실패 판정이 이 값에 의존한다), 그 밖(OK/MISSING)은
    OPTIONAL로 내려 광역 slot_ids를 써도 불변식을 어기지 않게 한다.
    """
    if result.state == c.ATTEMPT_STATE_FAILED:
        attempt = CollectionAttempt(
            company_id=company_id,
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
        return attempt, [], 0

    filtered, identity_mismatch_count = _filter_rows(result.rows, spec.name_keyword, company_id)
    if filtered:
        state = c.ATTEMPT_STATE_OK
        reason = c.REASON_LIST_QUERY_OK
    elif result.rows:
        # item 4 — 행은 있었지만(대상 회사가 그 공시유형을 낸 적은 있지만)
        # 이름 키워드·연결/정정 제외·corp_code 불일치로 전부 걸러졌다.
        # 「행이 아예 없었다」와 원인이 다르므로 다른 사유 코드로 남긴다.
        state = c.ATTEMPT_STATE_MISSING
        reason = c.REASON_LIST_ROWS_ALL_FILTERED
    else:
        state = c.ATTEMPT_STATE_MISSING
        reason = c.REASON_LIST_QUERY_MISSING
    attempt = CollectionAttempt(
        company_id=company_id,
        attempt_id=f"list:{spec.source_kind}",
        source_kind=spec.source_kind,
        requirement=c.REQUIREMENT_OPTIONAL,  # item 2 — 위 docstring 참고
        state=state,
        slot_ids=c.SOURCE_KIND_SLOT_SCOPE[spec.source_kind],
        reason_code=reason,
        elapsed_ms=max(0, result.elapsed_ms),
        bytes_downloaded=max(0, result.bytes_downloaded),
        documents_seen=len(result.rows),
    )
    return attempt, filtered, identity_mismatch_count


def _identity_mismatch_list_attempt(
    company_id: str, spec: c.FilingKindSpec, mismatch_count: int,
) -> CollectionAttempt:
    """목록 행 수준에서 다른 회사 corp_code로 걸러낸 건수를 관측치로 남긴다(item 3)."""
    return CollectionAttempt(
        company_id=company_id,
        attempt_id=f"list_identity_mismatch:{spec.source_kind}",
        source_kind=spec.source_kind,
        requirement=c.REQUIREMENT_OPTIONAL,  # 관측용 — item 2 불변식과 무관
        state=c.ATTEMPT_STATE_OK,
        slot_ids=c.SOURCE_KIND_SLOT_SCOPE[spec.source_kind],
        reason_code=c.REASON_LIST_ROW_IDENTITY_MISMATCH,
        elapsed_ms=0,
        bytes_downloaded=0,
        documents_seen=mismatch_count,
    )


def _deadline_list_attempt(company_id: str, spec: c.FilingKindSpec) -> CollectionAttempt:
    return CollectionAttempt(
        company_id=company_id,
        attempt_id=f"list:{spec.source_kind}",
        source_kind=spec.source_kind,
        requirement=spec.requirement,
        state=c.ATTEMPT_STATE_TRUNCATED,
        slot_ids=c.SOURCE_KIND_SLOT_SCOPE[spec.source_kind],
        reason_code=c.REASON_DEADLINE_EXCEEDED,
        elapsed_ms=0,
        bytes_downloaded=0,
        documents_seen=0,
    )


def select_related_filings(
    fetcher: DartFetcher, company_id: str, *, deadline_at: float | None = None,
) -> FilingSelectionResult:
    """관련 공시 묶음을 고른다 — 사업보고서 우선, 없으면 감사보고서(요구사항 1번).

    반기·분기보고서는 있으면 보충으로 더한다(OPTIONAL). 상한(MAX_RELATED_FILINGS)을
    넘는 후보는 TRUNCATED로 기록하고 뺀다 — 상한은 관측용이지 회사를 거절하는
    근거가 아니다.

    ``deadline_at``이 주어지면(``time.monotonic()`` 기준) 새 목록 조회를
    시작하기 «직전»마다 다시 확인한다(P1-3) — 이미 넘겼으면 그 조회는
    시작하지 않고 TRUNCATED로 남긴다(캐시된 결과 재사용은 새 조회가 아니므로
    막지 않는다).
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

    def deadline_exceeded() -> bool:
        return deadline_at is not None and time.monotonic() > deadline_at

    for source_kind in c.PRIMARY_LOOKUP_ORDER:
        spec = c.FILING_KIND_SPEC_BY_SOURCE_KIND[source_kind]
        if spec.pblntf_ty not in list_result_cache and deadline_exceeded():
            attempts.append(_deadline_list_attempt(company_id, spec))
            continue
        result = fetch_cached(spec.pblntf_ty)
        attempt, filtered, identity_mismatch_count = _attempt_for_list_query(company_id, spec, result)
        attempts.append(attempt)
        if identity_mismatch_count:
            attempts.append(_identity_mismatch_list_attempt(company_id, spec, identity_mismatch_count))
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
        if spec.pblntf_ty not in list_result_cache and deadline_exceeded():
            attempts.append(_deadline_list_attempt(company_id, spec))
            continue
        result = fetch_cached(spec.pblntf_ty)
        attempt, filtered, identity_mismatch_count = _attempt_for_list_query(company_id, spec, result)
        attempts.append(attempt)
        if identity_mismatch_count:
            attempts.append(_identity_mismatch_list_attempt(company_id, spec, identity_mismatch_count))
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
            company_id=company_id,
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
