"""오류 신고 접수·조회·상태 변경의 공개 API — 웹 라우터가 이 계층만 호출한다.

★ 외부 입력은 여기서 전부 검증한다. 본문은 «원문 그대로» 저장하고,
  HTML escape는 렌더하는 웹 계층의 몫이다 (이중 escape를 막기 위한 분업).
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from dataclasses import dataclass
from urllib.parse import urlsplit

from src.core import clock
from src.features.feedback_report import constants, store


class FeedbackReportError(ValueError):
    """신고 입력이나 상태 전이가 계약을 벗어났다. 메시지는 화면에 그대로 쓴다."""


class FeedbackReportLimitError(FeedbackReportError):
    """같은 신고자 식별자의 하루 접수 상한 초과."""


@dataclass(frozen=True)
class FeedbackReportPage:
    """관리자 목록 화면 한 페이지."""

    items: tuple[store.FeedbackReport, ...]
    total: int
    page: int
    page_size: int
    page_count: int


def _require_clean_text(value: str, *, label: str, maximum: int, required: bool = False) -> str:
    """앞뒤 공백만 다듬고 원문을 보존한다. NUL·초과 길이는 거부한다."""

    text = str(value or "").strip()
    if "\x00" in text:
        raise FeedbackReportError(f"{label}에 쓸 수 없는 문자가 있습니다")
    if required and not text:
        raise FeedbackReportError(f"{label}을(를) 입력해 주세요")
    if len(text) > maximum:
        raise FeedbackReportError(f"{label}은(는) {maximum}자 이내로 입력해 주세요")
    return text


def _validate_ref_url(value: str) -> str:
    """참고 URL은 http/https 절대 주소만 통과시킨다 (빈 값은 허용)."""

    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) > constants.MAX_REF_URL_CHARS:
        raise FeedbackReportError(
            f"참고 URL은 {constants.MAX_REF_URL_CHARS}자 이내로 입력해 주세요"
        )
    if any(ch.isspace() for ch in text) or "\x00" in text:
        raise FeedbackReportError("참고 URL 형식이 올바르지 않습니다")
    try:
        parts = urlsplit(text)
    except ValueError as exc:
        raise FeedbackReportError("참고 URL 형식이 올바르지 않습니다") from exc
    if parts.scheme.lower() not in constants.ALLOWED_REF_URL_SCHEMES or not parts.netloc:
        raise FeedbackReportError("참고 URL은 http 또는 https 주소만 입력할 수 있습니다")
    return text


def _resolve_now(now_iso: str) -> str:
    """비우면 현재 KST, 넘기면 시간대 포함 ISO 시각만 인정한다."""

    text = str(now_iso or "").strip()
    if not text:
        return clock.iso_now_kst()
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError as exc:
        raise FeedbackReportError("접수 시각이 올바르지 않습니다") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise FeedbackReportError("접수 시각에는 시간대가 필요합니다")
    return text


def _validate_day(value: str, *, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    try:
        return dt.date.fromisoformat(text).isoformat()
    except ValueError as exc:
        raise FeedbackReportError(f"{label}은 YYYY-MM-DD 형식이어야 합니다") from exc


def create_report(
    conn: sqlite3.Connection,
    *,
    stage: str,
    category: str,
    body: str,
    company_name: str = "",
    report_ref: str = "",
    item_label: str = "",
    ref_url: str = "",
    reporter_key: str = "",
    now_iso: str = "",
) -> store.FeedbackReport:
    """신고 한 건을 검증·접수하고 저장된 원장 행을 돌려준다.

    반환 전 commit은 하지 않는다 — 트랜잭션 마감은 연결을 연 쪽의 몫이다.
    """

    clean_stage = str(stage or "").strip()
    if clean_stage not in constants.REPORT_STAGES:
        raise FeedbackReportError("신고 단계가 올바르지 않습니다")
    clean_category = str(category or "").strip()
    if clean_category not in constants.REPORT_CATEGORIES:
        raise FeedbackReportError("신고 유형이 올바르지 않습니다")
    clean_body = _require_clean_text(
        body, label="신고 내용", maximum=constants.MAX_BODY_CHARS, required=True
    )
    clean_company = _require_clean_text(
        company_name, label="기업명", maximum=constants.MAX_COMPANY_NAME_CHARS
    )
    clean_report_ref = _require_clean_text(
        report_ref, label="대상 보고서", maximum=constants.MAX_REPORT_REF_CHARS
    )
    clean_item_label = _require_clean_text(
        item_label, label="오류 발생 항목", maximum=constants.MAX_ITEM_LABEL_CHARS
    )
    clean_ref_url = _validate_ref_url(ref_url)
    clean_reporter_key = _require_clean_text(
        reporter_key, label="신고자 식별자", maximum=constants.MAX_REPORTER_KEY_CHARS
    )
    created_at = _resolve_now(now_iso)
    day = clock.business_date_from_iso(created_at).isoformat()

    store.ensure_schema(conn)
    # 상한 판정과 접수를 한 트랜잭션으로 묶어 동시 요청이 상한을 넘지 못하게 한다.
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    submitted = store.count_for_reporter_day(
        conn, reporter_key=clean_reporter_key, day=day
    )
    if submitted >= constants.DAILY_CREATE_LIMIT_PER_REPORTER:
        raise FeedbackReportLimitError(constants.DAILY_LIMIT_MESSAGE)
    return store.create_report(
        conn,
        stage=clean_stage,
        category=clean_category,
        body=clean_body,
        company_name=clean_company,
        report_ref=clean_report_ref,
        item_label=clean_item_label,
        ref_url=clean_ref_url,
        reporter_key=clean_reporter_key,
        created_at=created_at,
        day=day,
    )


def get_report(conn: sqlite3.Connection, report_id: str) -> store.FeedbackReport | None:
    """신고 단건 조회. 없으면 None."""

    clean_id = str(report_id or "").strip()
    if not clean_id or len(clean_id) > 64 or "\x00" in clean_id:
        return None
    return store.get_report(conn, clean_id)


def list_reports(
    conn: sqlite3.Connection,
    *,
    status: str = "",
    category: str = "",
    stage: str = "",
    date_from: str = "",
    date_to: str = "",
    keyword: str = "",
    page: int = 1,
    page_size: int = constants.DEFAULT_PAGE_SIZE,
) -> FeedbackReportPage:
    """필터·검색어를 적용한 최신순 목록 한 페이지 (빈 필터 = 전체)."""

    clean_status = str(status or "").strip()
    if clean_status and clean_status not in constants.REPORT_STATUSES:
        raise FeedbackReportError("처리 상태 필터가 올바르지 않습니다")
    clean_category = str(category or "").strip()
    if clean_category and clean_category not in constants.REPORT_CATEGORIES:
        raise FeedbackReportError("신고 유형 필터가 올바르지 않습니다")
    clean_stage = str(stage or "").strip()
    if clean_stage and clean_stage not in constants.REPORT_STAGES:
        raise FeedbackReportError("신고 단계 필터가 올바르지 않습니다")
    clean_from = _validate_day(date_from, label="접수 시작일")
    clean_to = _validate_day(date_to, label="접수 종료일")
    if clean_from and clean_to and clean_from > clean_to:
        raise FeedbackReportError("접수 시작일이 종료일보다 늦을 수 없습니다")
    clean_keyword = _require_clean_text(
        keyword, label="검색어", maximum=constants.MAX_KEYWORD_CHARS
    )
    clean_page = max(1, int(page))
    clean_size = max(1, min(int(page_size), constants.MAX_PAGE_SIZE))

    items, total = store.list_reports(
        conn,
        status=clean_status,
        category=clean_category,
        stage=clean_stage,
        date_from=clean_from,
        date_to=clean_to,
        keyword=clean_keyword,
        limit=clean_size,
        offset=(clean_page - 1) * clean_size,
    )
    page_count = (total + clean_size - 1) // clean_size if total else 0
    return FeedbackReportPage(
        items=tuple(items),
        total=total,
        page=clean_page,
        page_size=clean_size,
        page_count=page_count,
    )


def count_by_status(conn: sqlite3.Connection) -> dict[str, int]:
    """네 처리 상태 각각의 건수. 전체 건수는 합으로 구한다."""

    return store.count_by_status(conn)


def change_status(
    conn: sqlite3.Connection,
    *,
    report_id: str,
    to_status: str,
    admin_note: str = "",
    actor: str = "",
    now_iso: str = "",
) -> store.FeedbackReport:
    """허용된 전이(미처리→검토중→처리완료|반려)만 실행하고 변경본을 돌려준다."""

    current = get_report(conn, report_id)
    if current is None:
        raise FeedbackReportError("해당 신고를 찾을 수 없습니다")
    clean_to = str(to_status or "").strip()
    if clean_to not in constants.REPORT_STATUSES:
        raise FeedbackReportError("처리 상태가 올바르지 않습니다")
    allowed = constants.ALLOWED_STATUS_TRANSITIONS.get(current.status, frozenset())
    if clean_to not in allowed:
        raise FeedbackReportError(
            f"'{current.status}' 상태에서 '{clean_to}'(으)로 바꿀 수 없습니다"
        )
    clean_note = _require_clean_text(
        admin_note, label="관리자 메모", maximum=constants.MAX_ADMIN_NOTE_CHARS
    )
    clean_actor = _require_clean_text(
        actor, label="처리자", maximum=constants.MAX_ACTOR_CHARS
    )
    updated_at = _resolve_now(now_iso)
    changed = store.update_status(
        conn,
        report_id=current.report_id,
        from_status=current.status,
        to_status=clean_to,
        admin_note=clean_note,
        actor=clean_actor,
        now_iso=updated_at,
    )
    if not changed:
        raise FeedbackReportError("다른 처리와 겹쳐 상태를 바꾸지 못했습니다. 새로고침 후 다시 시도해 주세요")
    result = store.get_report(conn, current.report_id)
    if result is None:
        raise store.FeedbackReportStoreError("상태 변경 후 신고를 다시 읽지 못했습니다")
    return result
