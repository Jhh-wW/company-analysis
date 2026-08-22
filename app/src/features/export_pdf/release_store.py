"""PDF 승인과 최종 hash 출고 기록을 보존하는 작은 SQLite 어댑터.

웹 승인 화면과 분리된 저장 경계다. PDF별 작성자·생산자·세 검수자의 불변 참여자
원장을 먼저 잠그고, 원장과 정확히 같은 역할 승인만 누적한다. 승인이나 참여자 원장이
없거나 손상되면 다운로드 경로가 닫히도록 조회·기록 API가 모두 fail-closed한다.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import sqlite3
from dataclasses import asdict, dataclass
from typing import Final

from src.core.persisted_json import validate_persisted_json_text
from src.features.export_pdf.release import (
    ALLOWED_VISUAL_REVIEW_KINDS,
    ApprovalDecision,
    PdfReleaseCandidate,
    PdfReleaseApproval,
    PdfReleaseRecord,
    ReleasedPdf,
    is_valid_reviewer_id,
    is_valid_sha256,
    release_record_sha256,
    release_pdf,
    validate_approval,
    validate_release_record,
)
from src.features.export_pdf.automatic_release import (
    AutomaticGateStopped,
    AutomaticallyReleasedPdf,
)
from src.shared.automatic_release_record import (
    AUTOMATIC_CHECKER_VERSION,
    AutomaticReleaseRecord,
    automatic_release_json,
    parse_automatic_release_json,
    validate_automatic_release_record,
    validate_persisted_automatic_release,
)
from src.features.export_pdf.schema import (
    AUTOMATIC_TABLE_NAME,
    CREATE_AUTOMATIC_SQL,
    CREATE_DECISION_SQL,
    CREATE_PARTICIPANT_SQL,
    CREATE_SQL,
    DECISION_TABLE_NAME,
    PARTICIPANT_TABLE_NAME,
    TABLE_NAME,
    ensure_schema,
)


def _validated_json(payload: str) -> str:
    validate_persisted_json_text(payload)
    return payload


APPROVAL_ROLES: Final[tuple[str, ...]] = ("fact", "editorial", "visual")
PARTICIPANT_ROLES: Final[tuple[str, ...]] = (
    "author",
    "producer",
    *APPROVAL_ROLES,
)


class PdfReleaseStoreError(RuntimeError):
    """승인 기록을 안전하게 읽거나 쓰지 못해 출고를 중단한다."""


@dataclass(frozen=True)
class PdfRoleDecision:
    """한 명의 검수자가 한 역할만 승인한 불변 기록."""

    role: str
    pdf_sha256: str
    page_png_sha256s: tuple[str, ...]
    reviewed_pages: tuple[int, ...]
    expected_fact_ids: tuple[str, ...]
    decision: ApprovalDecision
    reviewed_fact_ids: tuple[str, ...] = ()
    fact_failed_count: int = 0
    visual_review_kind: str = ""


@dataclass(frozen=True)
class PdfReleaseParticipant:
    """보고서 작성·생산과 세 독립 검수 역할의 불변 사람 배정 한 행."""

    role: str
    person_id: str
    assigned_at: str


# 기존 feature 내부 호출 호환. 새 bootstrap registry는 공개 계약만 사용한다.
_ensure_schema = ensure_schema


def _time_value(value: str) -> dt.datetime | None:
    if not isinstance(value, str) or value != value.strip():
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo is not None else None


def _valid_time(value: str) -> bool:
    return _time_value(value) is not None


def _validate_participant_ids(participants: dict[str, str]) -> tuple[str, ...]:
    problems: list[str] = []
    if not isinstance(participants, dict) or set(participants) != set(PARTICIPANT_ROLES):
        return ("작성자·생산자·세 검수자의 완전한 역할 배정이 필요합니다",)
    if any(not is_valid_reviewer_id(value) for value in participants.values()):
        problems.append("PDF 참여자 ID 형식이 올바르지 않습니다")
        return tuple(problems)
    reviewers = tuple(participants[role] for role in APPROVAL_ROLES)
    excluded = {participants["author"], participants["producer"]}
    if len(set(reviewers)) != len(APPROVAL_ROLES):
        problems.append("사실·편집·시각 검수자는 서로 다른 세 명이어야 합니다")
    if any(reviewer in excluded for reviewer in reviewers):
        problems.append("보고서 작성자나 생산자는 PDF 출고를 검수할 수 없습니다")
    return tuple(problems)


def load_participant_ledger(
    conn: sqlite3.Connection,
    *,
    report_id: str,
    pdf_sha256: str,
) -> dict[str, str] | None:
    """완전하고 역할 분리가 유효한 참여자 원장만 돌려준다."""

    clean_report_id = report_id.strip()
    if not clean_report_id or not is_valid_sha256(pdf_sha256):
        raise PdfReleaseStoreError("올바른 보고서와 PDF hash만 조회할 수 있습니다")
    _ensure_schema(conn)
    rows = conn.execute(
        f"""
        SELECT role, person_id, assigned_at
        FROM {PARTICIPANT_TABLE_NAME}
        WHERE report_id=? AND pdf_sha256=?
        ORDER BY role
        """,
        (clean_report_id, pdf_sha256),
    ).fetchall()
    if not rows:
        return None
    if len(rows) != len(PARTICIPANT_ROLES) or any(
        type(value) is not str for row in rows for value in row
    ):
        raise PdfReleaseStoreError("PDF 참여자 원장이 부분 기록이거나 손상됐습니다")
    if any(not _valid_time(row[2]) for row in rows):
        raise PdfReleaseStoreError("PDF 참여자 배정 시각이 올바르지 않습니다")
    participants = {str(row[0]): str(row[1]) for row in rows}
    if _validate_participant_ids(participants):
        raise PdfReleaseStoreError("PDF 참여자 역할 분리가 현재 계약을 통과하지 못했습니다")
    return participants


def ensure_participant_ledger(
    conn: sqlite3.Connection,
    *,
    report_id: str,
    pdf_sha256: str,
    participants: dict[str, str],
    assigned_at: str,
) -> dict[str, str]:
    """설정된 참여자를 PDF hash별 불변 원장으로 최초 한 번 결속한다.

    이후 설정이 달라져도 기존 배정을 덮어쓰지 않는다. 일부 행만 저장되는 일을
    막기 위해 함수 내부 SAVEPOINT에서 다섯 행을 함께 기록한다.
    """

    clean_report_id = report_id.strip()
    if (
        not clean_report_id
        or not is_valid_sha256(pdf_sha256)
        or not _valid_time(assigned_at)
        or _validate_participant_ids(participants)
    ):
        raise PdfReleaseStoreError("올바른 PDF 참여자 역할 배정만 저장할 수 있습니다")
    _ensure_schema(conn)
    existing = load_participant_ledger(
        conn,
        report_id=clean_report_id,
        pdf_sha256=pdf_sha256,
    )
    if existing is not None:
        if existing == participants:
            return existing
        raise PdfReleaseStoreError("PDF 참여자 역할 배정은 덮어쓸 수 없습니다")

    savepoint = "pdf_release_participants"
    conn.execute(f"SAVEPOINT {savepoint}")
    try:
        for role in PARTICIPANT_ROLES:
            conn.execute(
                f"""
                INSERT INTO {PARTICIPANT_TABLE_NAME} (
                    report_id, pdf_sha256, role, person_id, assigned_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    clean_report_id,
                    pdf_sha256,
                    role,
                    participants[role],
                    assigned_at,
                ),
            )
        stored = load_participant_ledger(
            conn,
            report_id=clean_report_id,
            pdf_sha256=pdf_sha256,
        )
        if stored != participants:
            raise PdfReleaseStoreError(
                "PDF 참여자 역할 배정을 원자적으로 확정하지 못했습니다"
            )
    except Exception as exc:
        conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        if isinstance(exc, sqlite3.IntegrityError):
            raise PdfReleaseStoreError(
                "PDF 참여자 역할 배정이 동시에 충돌했습니다"
            ) from exc
        raise
    conn.execute(f"RELEASE SAVEPOINT {savepoint}")
    return stored


def _expected_approval_created_at(approval: PdfReleaseApproval) -> str:
    values = (
        approval.fact.approved_at,
        approval.editorial.approved_at,
        approval.visual.approved_at,
    )
    if any(_time_value(value) is None for value in values):
        raise PdfReleaseStoreError("승인 시각을 확정할 수 없습니다")
    return max(values, key=lambda value: _time_value(value) or dt.datetime.min)


def validate_role_decision(value: PdfRoleDecision) -> tuple[str, ...]:
    """부분 승인이 후보 전체와 역할 책임에 정확히 결속됐는지 검사한다."""

    problems: list[str] = []
    if value.role not in APPROVAL_ROLES:
        problems.append("알 수 없는 PDF 검수 역할입니다")
    if not is_valid_sha256(value.pdf_sha256):
        problems.append("PDF SHA-256이 올바르지 않습니다")
    valid_page_hashes = (
        isinstance(value.page_png_sha256s, tuple)
        and bool(value.page_png_sha256s)
        and all(is_valid_sha256(page_hash) for page_hash in value.page_png_sha256s)
    )
    if not valid_page_hashes:
        problems.append("전 페이지 PNG SHA-256이 필요합니다")
    expected_pages = (
        tuple(range(1, len(value.page_png_sha256s) + 1))
        if isinstance(value.page_png_sha256s, tuple)
        else ()
    )
    if (
        not isinstance(value.reviewed_pages, tuple)
        or any(
            isinstance(page_number, bool) or not isinstance(page_number, int)
            for page_number in value.reviewed_pages
        )
        or value.reviewed_pages != expected_pages
    ):
        problems.append("검수 페이지가 전체 페이지와 정확히 일치하지 않습니다")
    valid_expected_fact_ids = (
        isinstance(value.expected_fact_ids, tuple)
        and bool(value.expected_fact_ids)
        and all(
            isinstance(fact_id, str) and bool(fact_id.strip())
            for fact_id in value.expected_fact_ids
        )
    )
    if not valid_expected_fact_ids:
        problems.append("후보의 전체 fact_id가 필요합니다")
    elif len(value.expected_fact_ids) != len(set(value.expected_fact_ids)):
        problems.append("후보 fact_id가 중복됐습니다")
    if not isinstance(value.decision, ApprovalDecision):
        problems.append("승인 결정 형식이 올바르지 않습니다")
    else:
        if value.decision.approved is not True:
            problems.append("승인 결정이 통과 상태가 아닙니다")
        if not is_valid_reviewer_id(value.decision.reviewer):
            problems.append("검수자 ID가 안전한 소문자 형식이 아닙니다")
        if not _valid_time(value.decision.approved_at):
            problems.append("승인 시각에 시간대가 없습니다")
    if value.role == "fact":
        if value.reviewed_fact_ids != value.expected_fact_ids:
            problems.append("검수한 fact_id가 후보 전체 사실 장부와 정확히 일치하지 않습니다")
        if (
            isinstance(value.fact_failed_count, bool)
            or not isinstance(value.fact_failed_count, int)
            or value.fact_failed_count != 0
        ):
            problems.append("사실 검수 실패 건수가 0이 아닙니다")
        if value.visual_review_kind:
            problems.append("사실 승인에 시각 검수 유형을 기록할 수 없습니다")
    else:
        if value.reviewed_fact_ids:
            problems.append("사실 역할 외 승인에는 fact_id 검수 기록을 넣을 수 없습니다")
        if (
            isinstance(value.fact_failed_count, bool)
            or not isinstance(value.fact_failed_count, int)
            or value.fact_failed_count != 0
        ):
            problems.append("사실 역할 외 승인에는 실패 건수를 넣을 수 없습니다")
        if value.role == "visual":
            if (
                not isinstance(value.visual_review_kind, str)
                or value.visual_review_kind not in ALLOWED_VISUAL_REVIEW_KINDS
            ):
                problems.append("시각 승인 유형이 올바르지 않습니다")
        elif value.visual_review_kind:
            problems.append("편집 승인에 시각 검수 유형을 기록할 수 없습니다")
    return tuple(problems)


def save_role_decision(
    conn: sqlite3.Connection,
    *,
    report_id: str,
    role_decision: PdfRoleDecision,
) -> None:
    """역할별 승인을 누적하되 한 검수자의 복수 역할 승인을 DB에서도 막는다."""

    clean_report_id = report_id.strip()
    if not clean_report_id or validate_role_decision(role_decision):
        raise PdfReleaseStoreError("올바른 역할별 PDF 승인만 저장할 수 있습니다")
    _ensure_schema(conn)
    participants = load_participant_ledger(
        conn,
        report_id=clean_report_id,
        pdf_sha256=role_decision.pdf_sha256,
    )
    if (
        participants is None
        or participants.get(role_decision.role) != role_decision.decision.reviewer
    ):
        raise PdfReleaseStoreError(
            "이 PDF 역할에 미리 배정된 불변 신원의 검수자만 승인할 수 있습니다"
        )
    payload = (
        clean_report_id,
        role_decision.pdf_sha256,
        role_decision.role,
        _validated_json(json.dumps(role_decision.page_png_sha256s, separators=(",", ":"))),
        _validated_json(json.dumps(role_decision.reviewed_pages, separators=(",", ":"))),
        _validated_json(
            json.dumps(
                role_decision.expected_fact_ids,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        ),
        _validated_json(
            json.dumps(
                role_decision.reviewed_fact_ids,
                ensure_ascii=False,
                separators=(",", ":"),
            )
        ),
        role_decision.fact_failed_count,
        role_decision.decision.reviewer,
        role_decision.decision.approved_at,
        role_decision.visual_review_kind,
    )
    try:
        cursor = conn.execute(
            f"""
            INSERT INTO {DECISION_TABLE_NAME} (
                report_id, pdf_sha256, role, page_hashes_json, reviewed_pages_json,
                expected_fact_ids_json, reviewed_fact_ids_json, fact_failed_count,
                reviewer, approved_at, visual_review_kind
            )
            SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            WHERE NOT EXISTS (
                SELECT 1 FROM {TABLE_NAME}
                WHERE report_id=? AND pdf_sha256=? AND released_at IS NOT NULL
            )
            ON CONFLICT(report_id, pdf_sha256, role) DO NOTHING
            """,
            (*payload, clean_report_id, role_decision.pdf_sha256),
        )
    except sqlite3.IntegrityError as exc:
        raise PdfReleaseStoreError(
            "한 검수자는 같은 PDF에서 하나의 역할만 승인할 수 있습니다"
        ) from exc
    if cursor.rowcount == 1:
        return
    existing_release = conn.execute(
        f"SELECT released_at FROM {TABLE_NAME} WHERE report_id=? AND pdf_sha256=?",
        (clean_report_id, role_decision.pdf_sha256),
    ).fetchone()
    if existing_release is not None and existing_release[0]:
        raise PdfReleaseStoreError("이미 출고한 PDF에 승인을 추가할 수 없습니다")
    existing = conn.execute(
        f"""
        SELECT report_id, pdf_sha256, role, page_hashes_json, reviewed_pages_json,
               expected_fact_ids_json, reviewed_fact_ids_json, fact_failed_count,
               reviewer, approved_at, visual_review_kind
        FROM {DECISION_TABLE_NAME}
        WHERE report_id=? AND pdf_sha256=? AND role=?
        """,
        (clean_report_id, role_decision.pdf_sha256, role_decision.role),
    ).fetchone()
    if existing is not None and tuple(existing) == payload:
        return
    raise PdfReleaseStoreError("이 역할은 이미 승인되어 덮어쓸 수 없습니다")


def _json_tuple(raw: str, *, integers: bool = False) -> tuple:
    try:
        values = json.loads(raw)
        if not isinstance(values, list):
            raise TypeError
        if integers:
            if any(isinstance(value, bool) or not isinstance(value, int) for value in values):
                raise TypeError
        elif any(not isinstance(value, str) for value in values):
            raise TypeError
        return tuple(values)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise PdfReleaseStoreError("역할별 PDF 승인 기록을 읽을 수 없습니다") from exc


def _role_decision_from_row(row: sqlite3.Row | tuple) -> PdfRoleDecision:
    if any(type(row[index]) is not str for index in (0, 1, 2, 3, 4, 5, 7, 8, 9)):
        raise PdfReleaseStoreError("역할별 PDF 승인 DB 형식이 손상됐습니다")
    if type(row[6]) is not int:
        raise PdfReleaseStoreError("사실 검수 실패 건수 DB 형식이 손상됐습니다")
    value = PdfRoleDecision(
        role=row[0],
        pdf_sha256=row[1],
        page_png_sha256s=_json_tuple(row[2]),
        reviewed_pages=_json_tuple(row[3], integers=True),
        expected_fact_ids=_json_tuple(row[4]),
        reviewed_fact_ids=_json_tuple(row[5]),
        fact_failed_count=row[6],
        decision=ApprovalDecision(True, row[7], row[8]),
        visual_review_kind=row[9],
    )
    if validate_role_decision(value):
        raise PdfReleaseStoreError("역할별 PDF 승인 기록이 현재 계약을 통과하지 못했습니다")
    return value


def load_role_decisions(
    conn: sqlite3.Connection,
    *,
    report_id: str,
    pdf_sha256: str,
) -> tuple[PdfRoleDecision, ...]:
    clean_report_id = report_id.strip()
    if not clean_report_id or not is_valid_sha256(pdf_sha256):
        raise PdfReleaseStoreError("올바른 보고서와 PDF hash만 조회할 수 있습니다")
    _ensure_schema(conn)
    rows = conn.execute(
        f"""
        SELECT role, pdf_sha256, page_hashes_json, reviewed_pages_json,
               expected_fact_ids_json, reviewed_fact_ids_json, fact_failed_count,
               reviewer, approved_at, visual_review_kind
        FROM {DECISION_TABLE_NAME}
        WHERE report_id=? AND pdf_sha256=?
        ORDER BY CASE role WHEN 'fact' THEN 1 WHEN 'editorial' THEN 2 ELSE 3 END
        """,
        (clean_report_id, pdf_sha256),
    ).fetchall()
    decisions = tuple(_role_decision_from_row(row) for row in rows)
    if decisions:
        participants = load_participant_ledger(
            conn,
            report_id=clean_report_id,
            pdf_sha256=pdf_sha256,
        )
        if participants is None or any(
            participants.get(item.role) != item.decision.reviewer
            for item in decisions
        ):
            raise PdfReleaseStoreError(
                "역할별 PDF 승인이 불변 참여자 원장과 일치하지 않습니다"
            )
    return decisions


def load_complete_approval(
    conn: sqlite3.Connection,
    *,
    report_id: str,
    pdf_sha256: str,
) -> PdfReleaseApproval | None:
    """서로 다른 3명의 역할 승인이 모두 모였을 때만 최종 승인을 조립한다."""

    decisions = load_role_decisions(
        conn,
        report_id=report_id,
        pdf_sha256=pdf_sha256,
    )
    if len(decisions) != len(APPROVAL_ROLES):
        return None
    by_role = {item.role: item for item in decisions}
    if set(by_role) != set(APPROVAL_ROLES):
        return None
    fact = by_role["fact"]
    first_binding = (
        fact.pdf_sha256,
        fact.page_png_sha256s,
        fact.reviewed_pages,
        fact.expected_fact_ids,
    )
    if any(
        (
            item.pdf_sha256,
            item.page_png_sha256s,
            item.reviewed_pages,
            item.expected_fact_ids,
        )
        != first_binding
        for item in decisions
    ):
        raise PdfReleaseStoreError("세 역할 승인이 서로 다른 PDF 후보에 묶였습니다")
    approval = PdfReleaseApproval(
        pdf_sha256=pdf_sha256,
        page_png_sha256s=fact.page_png_sha256s,
        reviewed_pages=fact.reviewed_pages,
        reviewed_fact_ids=fact.reviewed_fact_ids,
        fact_failed_count=fact.fact_failed_count,
        fact=fact.decision,
        editorial=by_role["editorial"].decision,
        visual=by_role["visual"].decision,
        visual_review_kind=by_role["visual"].visual_review_kind,
    )
    if validate_approval(approval):
        raise PdfReleaseStoreError("역할별 승인 묶음이 최종 출고 계약을 통과하지 못했습니다")
    return approval


def _approval_json(approval: PdfReleaseApproval) -> str:
    return _validated_json(
        json.dumps(
            asdict(approval),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def save_approval(
    conn: sqlite3.Connection,
    *,
    report_id: str,
    approval: PdfReleaseApproval,
    created_at: str,
) -> None:
    """역할별 원장 3개와 정확히 같은 최종 승인만 불변 기록으로 저장한다."""

    clean_report_id = report_id.strip()
    problems = validate_approval(approval)
    if (
        not clean_report_id
        or not _valid_time(created_at)
        or problems
        or created_at != _expected_approval_created_at(approval)
    ):
        raise PdfReleaseStoreError("올바른 PDF 승인 기록만 저장할 수 있습니다")
    _ensure_schema(conn)
    assembled = load_complete_approval(
        conn,
        report_id=clean_report_id,
        pdf_sha256=approval.pdf_sha256,
    )
    if assembled is None or assembled != approval:
        raise PdfReleaseStoreError(
            "역할별 세 승인과 정확히 같은 최종 승인만 저장할 수 있습니다"
        )
    canonical_json = _approval_json(approval)
    cursor = conn.execute(
        f"""
        INSERT INTO {TABLE_NAME} (
            report_id, pdf_sha256, approval_json, approval_created_at
        ) VALUES (?, ?, ?, ?)
        ON CONFLICT(report_id, pdf_sha256) DO NOTHING
        """,
        (
            clean_report_id,
            approval.pdf_sha256,
            canonical_json,
            created_at,
        ),
    )
    if cursor.rowcount == 1:
        return
    existing = conn.execute(
        f"""
        SELECT approval_json, approval_created_at, released_at
        FROM {TABLE_NAME}
        WHERE report_id=? AND pdf_sha256=?
        """,
        (clean_report_id, approval.pdf_sha256),
    ).fetchone()
    if existing is not None and existing[2]:
        raise PdfReleaseStoreError("이미 출고한 PDF 승인은 덮어쓸 수 없습니다")
    if (
        existing is not None
        and str(existing[0]) == canonical_json
        and str(existing[1]) == created_at
    ):
        return
    raise PdfReleaseStoreError("저장된 PDF 승인은 덮어쓸 수 없습니다")


def _decision(payload: object) -> ApprovalDecision:
    if not isinstance(payload, dict) or set(payload) != {
        "approved",
        "reviewer",
        "approved_at",
    }:
        raise PdfReleaseStoreError("PDF 승인 결정 형식이 손상됐습니다")
    if (
        payload["approved"] is not True
        or not isinstance(payload["reviewer"], str)
        or not isinstance(payload["approved_at"], str)
    ):
        raise PdfReleaseStoreError("PDF 승인 결정 형식이 손상됐습니다")
    return ApprovalDecision(
        approved=True,
        reviewer=payload["reviewer"],
        approved_at=payload["approved_at"],
    )


def _parse_approval(raw: str) -> PdfReleaseApproval:
    try:
        payload = json.loads(raw)
        if not isinstance(payload, dict) or set(payload) != {
            "pdf_sha256",
            "page_png_sha256s",
            "reviewed_pages",
            "reviewed_fact_ids",
            "fact_failed_count",
            "fact",
            "editorial",
            "visual",
            "visual_review_kind",
        }:
            raise TypeError
        string_fields = ("pdf_sha256", "visual_review_kind")
        if any(not isinstance(payload[name], str) for name in string_fields):
            raise TypeError
        for name in ("page_png_sha256s", "reviewed_fact_ids"):
            if not isinstance(payload[name], list) or any(
                not isinstance(value, str) for value in payload[name]
            ):
                raise TypeError
        if not isinstance(payload["reviewed_pages"], list) or any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in payload["reviewed_pages"]
        ):
            raise TypeError
        if isinstance(payload["fact_failed_count"], bool) or not isinstance(
            payload["fact_failed_count"], int
        ):
            raise TypeError
        approval = PdfReleaseApproval(
            pdf_sha256=payload["pdf_sha256"],
            page_png_sha256s=tuple(payload["page_png_sha256s"]),
            reviewed_pages=tuple(payload["reviewed_pages"]),
            reviewed_fact_ids=tuple(payload["reviewed_fact_ids"]),
            fact_failed_count=payload["fact_failed_count"],
            fact=_decision(payload["fact"]),
            editorial=_decision(payload["editorial"]),
            visual=_decision(payload["visual"]),
            visual_review_kind=payload["visual_review_kind"],
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise PdfReleaseStoreError("PDF 승인 기록을 읽을 수 없습니다") from exc
    if validate_approval(approval):
        raise PdfReleaseStoreError("PDF 승인 기록이 현재 계약을 통과하지 못했습니다")
    return approval


def load_approval(
    conn: sqlite3.Connection,
    *,
    report_id: str,
    pdf_sha256: str,
) -> PdfReleaseApproval | None:
    clean_report_id = report_id.strip()
    if not clean_report_id or not is_valid_sha256(pdf_sha256):
        raise PdfReleaseStoreError("올바른 보고서와 PDF hash만 조회할 수 있습니다")
    _ensure_schema(conn)
    row = conn.execute(
        f"""
        SELECT approval_json, approval_created_at
        FROM {TABLE_NAME}
        WHERE report_id=? AND pdf_sha256=?
        """,
        (clean_report_id, pdf_sha256),
    ).fetchone()
    if row is None:
        return None
    if type(row[0]) is not str or type(row[1]) is not str:
        raise PdfReleaseStoreError("PDF 승인 DB 형식이 손상됐습니다")
    approval = _parse_approval(row[0])
    if (
        approval.pdf_sha256 != pdf_sha256
        or row[0] != _approval_json(approval)
        or row[1] != _expected_approval_created_at(approval)
    ):
        raise PdfReleaseStoreError("PDF 승인 정본의 무결성을 확인할 수 없습니다")
    assembled = load_complete_approval(
        conn,
        report_id=clean_report_id,
        pdf_sha256=pdf_sha256,
    )
    if assembled is None or approval != assembled:
        raise PdfReleaseStoreError("PDF 승인 정본과 역할별 세 승인이 일치하지 않습니다")
    return approval


def _store_release_record(
    conn: sqlite3.Connection,
    *,
    report_id: str,
    released_pdf: ReleasedPdf,
) -> None:
    """재검증된 PDF bytes와 출고 정본을 같은 승인 행에 붙이는 내부 경계."""

    if not isinstance(released_pdf, ReleasedPdf) or not isinstance(
        released_pdf.record, PdfReleaseRecord
    ):
        raise PdfReleaseStoreError("ReleasedPdf 결과만 출고 기록으로 저장할 수 있습니다")
    record = released_pdf.record
    clean_report_id = report_id.strip()
    if (
        not clean_report_id
        or not isinstance(released_pdf.content, bytes)
        or not released_pdf.content
        or hashlib.sha256(released_pdf.content).hexdigest() != record.pdf_sha256
        or validate_release_record(record)
    ):
        raise PdfReleaseStoreError("올바른 PDF 출고 기록만 저장할 수 있습니다")
    _ensure_schema(conn)
    approval = load_approval(
        conn,
        report_id=clean_report_id,
        pdf_sha256=record.pdf_sha256,
    )
    if approval is None or not _record_matches_approval(record, approval):
        raise PdfReleaseStoreError("승인 정본과 정확히 결속된 PDF만 출고할 수 있습니다")
    release_json = _release_json(record)
    existing = conn.execute(
        f"""
        SELECT release_json, release_sha256, released_at
        FROM {TABLE_NAME}
        WHERE report_id=? AND pdf_sha256=?
        """,
        (clean_report_id, record.pdf_sha256),
    ).fetchone()
    if existing is None:
        raise PdfReleaseStoreError("승인되지 않은 PDF는 출고할 수 없습니다")
    if any(value is not None for value in existing):
        if all(value is not None for value in existing):
            stored = load_release_record(
                conn,
                report_id=clean_report_id,
                pdf_sha256=record.pdf_sha256,
            )
            if stored == record:
                return
        raise PdfReleaseStoreError("다른 출고 기록 또는 손상된 상태가 이미 존재합니다")
    cursor = conn.execute(
        f"""
        UPDATE {TABLE_NAME}
        SET release_json=?, release_sha256=?, released_at=?
        WHERE report_id=? AND pdf_sha256=?
          AND release_json IS NULL
          AND release_sha256 IS NULL
          AND released_at IS NULL
        """,
        (
            release_json,
            record.record_sha256,
            record.released_at,
            clean_report_id,
            record.pdf_sha256,
        ),
    )
    if cursor.rowcount != 1:
        stored = load_release_record(
            conn,
            report_id=clean_report_id,
            pdf_sha256=record.pdf_sha256,
        )
        if stored != record:
            raise PdfReleaseStoreError("PDF 출고 기록을 원자적으로 확정하지 못했습니다")


def record_release(
    conn: sqlite3.Connection,
    *,
    report_id: str,
    released_pdf: ReleasedPdf,
    candidate: PdfReleaseCandidate,
    approval: PdfReleaseApproval,
) -> None:
    """후보·승인·실제 bytes를 다시 결속한 ReleasedPdf만 저장한다."""

    if not isinstance(released_pdf, ReleasedPdf) or not isinstance(
        released_pdf.record, PdfReleaseRecord
    ):
        raise PdfReleaseStoreError("ReleasedPdf 결과만 출고 기록으로 저장할 수 있습니다")
    regenerated = release_pdf(
        candidate,
        approval,
        released_at=released_pdf.record.released_at,
    )
    if regenerated != released_pdf:
        raise PdfReleaseStoreError("출고 결과가 검증된 PDF 후보에서 생성되지 않았습니다")
    _store_release_record(conn, report_id=report_id, released_pdf=released_pdf)


def finalize_release(
    conn: sqlite3.Connection,
    *,
    report_id: str,
    candidate: PdfReleaseCandidate,
    approval: PdfReleaseApproval,
    created_at: str,
    released_at: str,
) -> ReleasedPdf:
    """후보 재검증부터 승인·출고 저장까지 한 SAVEPOINT에서 원자적으로 확정한다."""

    released = release_pdf(candidate, approval, released_at=released_at)
    savepoint = "pdf_release_finalize"
    conn.execute(f"SAVEPOINT {savepoint}")
    try:
        save_approval(
            conn,
            report_id=report_id,
            approval=approval,
            created_at=created_at,
        )
        _store_release_record(conn, report_id=report_id, released_pdf=released)
    except Exception:
        conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise
    conn.execute(f"RELEASE SAVEPOINT {savepoint}")
    return released


def _release_json(record: PdfReleaseRecord) -> str:
    return _validated_json(
        json.dumps(
            asdict(record),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def _record_matches_approval(
    record: PdfReleaseRecord,
    approval: PdfReleaseApproval,
) -> bool:
    return (
        record.pdf_sha256 == approval.pdf_sha256
        and record.page_count == len(approval.page_png_sha256s)
        and record.page_png_sha256s == approval.page_png_sha256s
        and record.expected_fact_ids == approval.reviewed_fact_ids
        and record.reviewed_fact_ids == approval.reviewed_fact_ids
        and record.fact_failed_count == approval.fact_failed_count
        and record.fact_reviewer == approval.fact.reviewer
        and record.fact_approved_at == approval.fact.approved_at
        and record.editorial_reviewer == approval.editorial.reviewer
        and record.editorial_approved_at == approval.editorial.approved_at
        and record.visual_reviewer == approval.visual.reviewer
        and record.visual_approved_at == approval.visual.approved_at
        and record.visual_review_kind == approval.visual_review_kind
    )


def _parse_release_record(raw: str) -> PdfReleaseRecord:
    try:
        payload = json.loads(raw)
        expected_fields = set(PdfReleaseRecord.__dataclass_fields__)
        if not isinstance(payload, dict) or set(payload) != expected_fields:
            raise TypeError
        for name in (
            "page_png_sha256s",
            "expected_fact_ids",
            "reviewed_fact_ids",
        ):
            if not isinstance(payload[name], list) or any(
                not isinstance(value, str) for value in payload[name]
            ):
                raise TypeError
            payload[name] = tuple(payload[name])
        record = PdfReleaseRecord(**payload)
    except (TypeError, ValueError, KeyError, json.JSONDecodeError) as exc:
        raise PdfReleaseStoreError("PDF 출고 기록을 읽을 수 없습니다") from exc
    if validate_release_record(record):
        raise PdfReleaseStoreError("PDF 출고 기록이 현재 무결성 계약을 통과하지 못했습니다")
    return record


def load_release_record(
    conn: sqlite3.Connection,
    *,
    report_id: str,
    pdf_sha256: str,
) -> PdfReleaseRecord | None:
    clean_report_id = report_id.strip()
    if not clean_report_id or not is_valid_sha256(pdf_sha256):
        raise PdfReleaseStoreError("올바른 보고서와 PDF hash만 조회할 수 있습니다")
    _ensure_schema(conn)
    row = conn.execute(
        f"""
        SELECT release_json, release_sha256, released_at
        FROM {TABLE_NAME}
        WHERE report_id=? AND pdf_sha256=?
        """,
        (clean_report_id, pdf_sha256),
    ).fetchone()
    if row is None:
        return None
    if all(value is None for value in row):
        return None
    if any(value is None for value in row):
        raise PdfReleaseStoreError("PDF 출고 DB 상태가 부분 기록으로 손상됐습니다")
    if any(type(value) is not str for value in row):
        raise PdfReleaseStoreError("PDF 출고 DB 형식이 손상됐습니다")
    raw_json, db_digest, db_released_at = row
    record = _parse_release_record(raw_json)
    if (
        record.pdf_sha256 != pdf_sha256
        or record.released_at != db_released_at
        or not is_valid_sha256(db_digest)
        or release_record_sha256(record) != record.record_sha256
        or record.record_sha256 != db_digest
        or raw_json != _release_json(record)
    ):
        raise PdfReleaseStoreError("PDF 출고 정본과 DB digest가 일치하지 않습니다")
    approval = load_approval(
        conn,
        report_id=clean_report_id,
        pdf_sha256=pdf_sha256,
    )
    if approval is None or not _record_matches_approval(record, approval):
        raise PdfReleaseStoreError("PDF 출고 정본과 역할별 승인이 일치하지 않습니다")
    return record


def _automatic_release_json(record: AutomaticReleaseRecord) -> str:
    return _validated_json(automatic_release_json(record))


def _parse_automatic_release_record(raw: str) -> AutomaticReleaseRecord:
    try:
        return parse_automatic_release_json(raw)
    except ValueError as exc:
        raise PdfReleaseStoreError(
            "자동출고 기록을 안전하게 읽을 수 없습니다"
        ) from exc


def _reject_changed_automatic_subject(
    conn: sqlite3.Connection,
    *,
    report_id: str,
    report_sha256: str,
    pdf_sha256: str,
) -> None:
    rows = conn.execute(
        f"""
        SELECT DISTINCT report_sha256, pdf_sha256
          FROM {AUTOMATIC_TABLE_NAME}
         WHERE report_id=?
        """,
        (report_id,),
    ).fetchall()
    if any(
        str(row[0]) != report_sha256 or str(row[1]) != pdf_sha256
        for row in rows
    ):
        raise AutomaticGateStopped(
            ("자동검사 뒤 같은 작업의 보고서 또는 PDF 지문이 변경되었습니다",)
        )


def load_automatic_release_record(
    conn: sqlite3.Connection,
    *,
    report_id: str,
    report_sha256: str,
    pdf_sha256: str,
) -> AutomaticReleaseRecord | None:
    """Load only the current checker version and exact immutable subject."""

    clean_report_id = report_id.strip()
    if (
        not clean_report_id
        or not is_valid_sha256(report_sha256)
        or not is_valid_sha256(pdf_sha256)
    ):
        raise PdfReleaseStoreError("올바른 보고서·PDF 지문만 조회할 수 있습니다")
    _ensure_schema(conn)
    _reject_changed_automatic_subject(
        conn,
        report_id=clean_report_id,
        report_sha256=report_sha256,
        pdf_sha256=pdf_sha256,
    )
    row = conn.execute(
        f"""
        SELECT release_json, release_sha256, released_at
          FROM {AUTOMATIC_TABLE_NAME}
         WHERE report_id=? AND report_sha256=? AND pdf_sha256=?
           AND checker_version=?
        """,
        (
            clean_report_id,
            report_sha256,
            pdf_sha256,
            AUTOMATIC_CHECKER_VERSION,
        ),
    ).fetchone()
    if row is None:
        return None
    if any(type(value) is not str for value in row):
        raise PdfReleaseStoreError("자동출고 DB 형식이 손상됐습니다")
    raw_json, db_digest, db_released_at = row
    try:
        record = validate_persisted_automatic_release(
            report_sha256=report_sha256,
            pdf_sha256=pdf_sha256,
            checker_version=AUTOMATIC_CHECKER_VERSION,
            release_json=raw_json,
            release_sha256=db_digest,
            released_at=db_released_at,
        )
    except ValueError as exc:
        raise PdfReleaseStoreError("자동출고 기록과 DB 지문이 일치하지 않습니다")
    if _automatic_release_json(record) != raw_json:
        raise PdfReleaseStoreError("자동출고 기록과 DB 지문이 일치하지 않습니다")
    return record


def save_automatic_release(
    conn: sqlite3.Connection,
    *,
    report_id: str,
    released_pdf: AutomaticallyReleasedPdf,
) -> AutomaticReleaseRecord:
    """Persist an automatic release without modifying legacy approval rows."""

    clean_report_id = report_id.strip()
    if not clean_report_id or not isinstance(released_pdf, AutomaticallyReleasedPdf):
        raise PdfReleaseStoreError("올바른 자동출고 결과만 저장할 수 있습니다")
    record = released_pdf.record
    if (
        hashlib.sha256(released_pdf.content).hexdigest() != record.pdf_sha256
        or validate_automatic_release_record(record)
    ):
        raise PdfReleaseStoreError("자동출고 결과의 무결성을 확인할 수 없습니다")
    _ensure_schema(conn)
    _reject_changed_automatic_subject(
        conn,
        report_id=clean_report_id,
        report_sha256=record.report_sha256,
        pdf_sha256=record.pdf_sha256,
    )
    raw_json = _automatic_release_json(record)
    conn.execute(
        f"""
        INSERT INTO {AUTOMATIC_TABLE_NAME} (
            report_id, report_sha256, pdf_sha256, checker_version,
            release_json, release_sha256, released_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(report_id, report_sha256, pdf_sha256, checker_version)
        DO NOTHING
        """,
        (
            clean_report_id,
            record.report_sha256,
            record.pdf_sha256,
            record.checker_version,
            raw_json,
            record.record_sha256,
            record.released_at,
        ),
    )
    stored = load_automatic_release_record(
        conn,
        report_id=clean_report_id,
        report_sha256=record.report_sha256,
        pdf_sha256=record.pdf_sha256,
    )
    if stored != record:
        raise PdfReleaseStoreError(
            "같은 자동출고 대상에 다른 기록이 이미 존재합니다"
        )
    return stored
