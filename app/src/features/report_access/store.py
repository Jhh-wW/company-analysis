"""익명 데모 브라우저의 보고서 열람 grant 저장소.

주소의 32자리 ID는 위치만 알려 준다. 실제 권한 난수는 HttpOnly 쿠키에 있고,
DB에는 그 난수의 SHA-256 지문과 이 브라우저가 시작한 run/report 결속만 둔다.
"""

from __future__ import annotations

import hashlib
import datetime as dt
import re
import secrets
import sqlite3
import time
from dataclasses import dataclass
from typing import Final

from src.core import clock
from src.features.report_access import constants


TABLE_GRANTS: Final[str] = "report_access_public_grants"
TABLE_BINDINGS: Final[str] = "report_access_public_bindings"
TABLE_MEMBER_BINDINGS: Final[str] = "report_access_member_bindings"
TABLE_CUTOVER: Final[str] = "report_access_cutover"
TABLE_LEGACY_RESOURCES: Final[str] = "report_access_legacy_resources"
INDEX_BINDINGS_REPORT: Final[str] = "idx_report_access_public_bindings_report"
INDEX_MEMBER_BINDINGS_REPORT: Final[str] = "idx_report_access_member_bindings_report"
INDEX_LEGACY_REPORT: Final[str] = "idx_report_access_legacy_report"
TRIGGER_GRANT_CAPACITY: Final[str] = "report_access_public_grants_capacity"
TRIGGER_BINDING_CAPACITY: Final[str] = "report_access_public_bindings_capacity"
_LEGACY_BINDINGS: Final[str] = "report_access_public_bindings_no_cascade"
_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{40,128}$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_LOCATOR_RE = re.compile(
    rf"^[0-9a-f]{{{constants.REPORT_ID_HEX_CHARS}}}$"
)
LEGACY_AUDIENCE_PUBLIC: Final[str] = "public"
LEGACY_AUDIENCE_MEMBER: Final[str] = "member"

_CREATE_GRANTS_SQL: Final[str] = f"""
    CREATE TABLE IF NOT EXISTS {TABLE_GRANTS} (
        grant_hash  TEXT PRIMARY KEY CHECK(length(grant_hash) = 64),
        created_at  REAL NOT NULL,
        expires_at  REAL NOT NULL CHECK(expires_at > created_at),
        revoked_at  REAL NOT NULL DEFAULT 0 CHECK(revoked_at >= 0)
    )
    """
_CREATE_BINDINGS_SQL: Final[str] = f"""
    CREATE TABLE IF NOT EXISTS {TABLE_BINDINGS} (
        grant_hash  TEXT NOT NULL,
        run_id      TEXT NOT NULL,
        report_id   TEXT NOT NULL DEFAULT '',
        created_at  REAL NOT NULL,
        PRIMARY KEY (grant_hash, run_id),
        FOREIGN KEY (grant_hash) REFERENCES {TABLE_GRANTS}(grant_hash)
            ON DELETE CASCADE
    )
    """
_CREATE_MEMBER_BINDINGS_SQL: Final[str] = f"""
    CREATE TABLE IF NOT EXISTS {TABLE_MEMBER_BINDINGS} (
        run_id        TEXT PRIMARY KEY,
        report_id     TEXT NOT NULL DEFAULT '',
        subject_hash  TEXT NOT NULL CHECK(length(subject_hash) = 64),
        created_at    REAL NOT NULL
    )
    """
_CREATE_CUTOVER_SQL: Final[str] = f"""
    CREATE TABLE IF NOT EXISTS {TABLE_CUTOVER} (
        singleton    INTEGER PRIMARY KEY CHECK(singleton = 1),
        cutover_at   REAL NOT NULL CHECK(cutover_at > 0)
    )
    """
_CREATE_LEGACY_RESOURCES_SQL: Final[str] = f"""
    CREATE TABLE IF NOT EXISTS {TABLE_LEGACY_RESOURCES} (
        run_id            TEXT PRIMARY KEY,
        report_id         TEXT NOT NULL,
        audience          TEXT NOT NULL CHECK(audience IN (
                              '{LEGACY_AUDIENCE_PUBLIC}',
                              '{LEGACY_AUDIENCE_MEMBER}'
                          )),
        actor_email_hash  TEXT NOT NULL DEFAULT '',
        expires_at        REAL NOT NULL CHECK(expires_at > 0),
        captured_at       REAL NOT NULL CHECK(captured_at > 0),
        revoked_at        REAL NOT NULL DEFAULT 0 CHECK(revoked_at >= 0),
        CHECK(
            (audience = '{LEGACY_AUDIENCE_PUBLIC}' AND actor_email_hash = '')
            OR
            (audience = '{LEGACY_AUDIENCE_MEMBER}' AND length(actor_email_hash) = 64)
        )
    )
    """
_CREATE_INDEX_SQL: Final[str] = f"""
    CREATE INDEX IF NOT EXISTS {INDEX_BINDINGS_REPORT}
        ON {TABLE_BINDINGS}(grant_hash, report_id)
    """
_CREATE_MEMBER_INDEX_SQL: Final[str] = f"""
    CREATE INDEX IF NOT EXISTS {INDEX_MEMBER_BINDINGS_REPORT}
        ON {TABLE_MEMBER_BINDINGS}(subject_hash, report_id)
    """
_CREATE_LEGACY_INDEX_SQL: Final[str] = f"""
    CREATE INDEX IF NOT EXISTS {INDEX_LEGACY_REPORT}
        ON {TABLE_LEGACY_RESOURCES}(report_id)
    """
_CREATE_GRANT_CAPACITY_TRIGGER_SQL: Final[str] = f"""
    CREATE TRIGGER {TRIGGER_GRANT_CAPACITY}
    BEFORE INSERT ON {TABLE_GRANTS}
    WHEN (
        SELECT COUNT(*) FROM {TABLE_GRANTS}
         WHERE revoked_at = 0
           AND created_at <= NEW.created_at
           AND expires_at > NEW.created_at
    ) >= {constants.PUBLIC_ACTIVE_GRANT_LIMIT}
    BEGIN
        SELECT RAISE(ABORT, 'report access active grant capacity reached');
    END
    """
_CREATE_BINDING_CAPACITY_TRIGGER_SQL: Final[str] = f"""
    CREATE TRIGGER {TRIGGER_BINDING_CAPACITY}
    BEFORE INSERT ON {TABLE_BINDINGS}
    WHEN NOT EXISTS (
        SELECT 1 FROM {TABLE_BINDINGS}
         WHERE grant_hash = NEW.grant_hash AND run_id = NEW.run_id
    ) AND (
        SELECT COUNT(*) FROM {TABLE_BINDINGS}
         WHERE grant_hash = NEW.grant_hash
    ) >= {constants.PUBLIC_BINDINGS_PER_GRANT_LIMIT}
    BEGIN
        SELECT RAISE(ABORT, 'report access binding capacity reached');
    END
    """

CREATE_SQL: Final[tuple[str, ...]] = (
    _CREATE_GRANTS_SQL,
    _CREATE_BINDINGS_SQL,
    _CREATE_MEMBER_BINDINGS_SQL,
    _CREATE_CUTOVER_SQL,
    _CREATE_LEGACY_RESOURCES_SQL,
    _CREATE_INDEX_SQL,
    _CREATE_MEMBER_INDEX_SQL,
    _CREATE_LEGACY_INDEX_SQL,
)


@dataclass(frozen=True)
class IssuedGrant:
    token: str
    grant_hash: str
    expires_at: float
    reused: bool


@dataclass(frozen=True)
class LegacyAccess:
    audience: str
    actor_email_hash: str
    expires_at: float


def _object_type(conn: sqlite3.Connection, name: str) -> str:
    row = conn.execute(
        "SELECT type FROM sqlite_master WHERE name = ?", (name,)
    ).fetchone()
    return "" if row is None else str(row[0])


def _columns(conn: sqlite3.Connection, table: str) -> tuple[str, ...]:
    escaped = table.replace('"', '""')
    return tuple(
        str(row[1]) for row in conn.execute(f'PRAGMA table_info("{escaped}")')
    )


def _binding_has_cascade(conn: sqlite3.Connection) -> bool:
    rows = conn.execute(f"PRAGMA foreign_key_list({TABLE_BINDINGS})").fetchall()
    return len(rows) == 1 and (
        str(rows[0][2]),
        str(rows[0][3]),
        str(rows[0][4]),
        str(rows[0][6]).upper(),
    ) == (TABLE_GRANTS, "grant_hash", "grant_hash", "CASCADE")


def _migrate_binding_cascade(conn: sqlite3.Connection) -> None:
    """초판 NO ACTION FK를 행 손실 없이 ON DELETE CASCADE로 원자 전환한다."""

    if _object_type(conn, _LEGACY_BINDINGS):
        raise RuntimeError("보고서 접근 binding 중간 마이그레이션 표가 남아 있습니다")
    conn.execute(f"ALTER TABLE {TABLE_BINDINGS} RENAME TO {_LEGACY_BINDINGS}")
    conn.execute(_CREATE_BINDINGS_SQL)
    conn.execute(
        f"""
        INSERT INTO {TABLE_BINDINGS}(grant_hash, run_id, report_id, created_at)
        SELECT grant_hash, run_id, report_id, created_at
          FROM {_LEGACY_BINDINGS}
        """
    )
    old_count = int(
        conn.execute(f"SELECT COUNT(*) FROM {_LEGACY_BINDINGS}").fetchone()[0]
    )
    new_count = int(
        conn.execute(f"SELECT COUNT(*) FROM {TABLE_BINDINGS}").fetchone()[0]
    )
    if old_count != new_count:
        raise RuntimeError("보고서 접근 binding FK 전환 중 행 수가 달라졌습니다")
    conn.execute(f"DROP TABLE {_LEGACY_BINDINGS}")


def _iso_timestamp(value: object) -> float:
    try:
        parsed = dt.datetime.fromisoformat(str(value or "").strip())
    except (TypeError, ValueError):
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=clock.KST)
    return parsed.timestamp()


def _cutover_timestamp() -> float:
    stamp = _iso_timestamp(constants.LEGACY_COMPAT_CUTOVER_AT_ISO)
    if stamp <= 0:
        raise RuntimeError("보고서 접근 cutover 정본 시각이 올바르지 않습니다")
    return stamp


def email_hash(email: object) -> str:
    """legacy MEMBER 비교용 정규화 이메일 지문(원문은 새 표에 넣지 않는다)."""

    clean = str(email or "").strip().lower()
    if not clean or "@" not in clean:
        return ""
    return hashlib.sha256(clean.encode("utf-8", errors="strict")).hexdigest()


def _legacy_expiry(
    *, generated_at: object, delivery_expires_at: object, cutover_at: float
) -> float:
    """당시 Delivery 만료를 우선하고, 없으면 기존 60일 날짜 계약을 쓴다."""

    exact_delivery = _iso_timestamp(delivery_expires_at)
    if exact_delivery > 0:
        return exact_delivery
    try:
        made = clock.business_date_from_iso(str(generated_at or "").strip())
    except (TypeError, ValueError):
        # 기존 expiry.py도 날짜가 손상되면 즉시 닫는다. cutover+N일을 임의로
        # 주면 원래 닫혀 있던 주소를 새 배포가 되살리므로 같은 원칙을 지킨다.
        return 0.0
    cutover_day = dt.datetime.fromtimestamp(cutover_at, clock.KST).date()
    if made > cutover_day:
        return 0.0
    expires = dt.datetime.combine(
        made + dt.timedelta(days=constants.REPORT_LINK_MAX_AGE_DAYS),
        dt.time.min,
        tzinfo=clock.KST,
    )
    return expires.timestamp()


def _snapshot_legacy_resources(
    conn: sqlite3.Connection, *, cutover_at: float
) -> None:
    """cutover 전에 DB에 이미 있던 보고서 집합만 한 번 고정한다.

    기존 LINK는 LINK cookie가 이미 있고, 기존 MEMBER는 이메일 소유자를 DB가
    증명하므로 익명 PUBLIC fallback에 절대 섞지 않는다.
    """

    if _object_type(conn, "reports") != "table":
        return
    delivery_expiry_by_report: dict[str, object] = {}
    if _object_type(conn, "report_delivery_deliveries") == "table":
        delivery_expiry_by_report = {
            str(row[0]).strip().lower(): row[1]
            for row in conn.execute(
                "SELECT public_id, expires_at FROM report_delivery_deliveries"
            )
        }

    member_by_report: dict[str, list[tuple[str, str]]] = {}
    member_claimed_reports: set[str] = set()
    if _object_type(conn, "dashboard_member_usage") == "table":
        for run_id, report_id, actor_email, state in conn.execute(
            """
            SELECT run_id, report_id, actor_email, state
              FROM dashboard_member_usage
            """
        ):
            clean_report = str(report_id).strip().lower()
            clean_run = str(run_id).strip().lower()
            for claimed in (clean_run, clean_report):
                if _LOCATOR_RE.fullmatch(claimed) is not None:
                    member_claimed_reports.add(claimed)
            if (
                str(state) != "used"
                or _LOCATOR_RE.fullmatch(clean_report) is None
            ):
                continue
            member_by_report.setdefault(clean_report, []).append(
                (clean_run, email_hash(actor_email))
            )

    link_reports: set[str] = set()
    if _object_type(conn, "share_links") == "table":
        link_reports.update(
            str(row[0]).strip().lower()
            for row in conn.execute(
                "SELECT report_id FROM share_links WHERE report_id <> ''"
            )
        )
    if _object_type(conn, "share_link_run_history") == "table":
        for run_id, report_id in conn.execute(
            "SELECT run_id, report_id FROM share_link_run_history"
        ):
            link_reports.update(
                {
                    str(run_id).strip().lower(),
                    str(report_id).strip().lower(),
                }
            )

    for report_id, generated_at, created_at in conn.execute(
        "SELECT report_id, generated_at, created_at FROM reports"
    ):
        clean_report = str(report_id).strip().lower()
        # 존재 집합뿐 아니라 영속 created_at도 고정 cutover보다 앞서야 한다.
        # access 표 둘이 통째로 롤백돼도 이후 보고서는 legacy로 재분류되지 않는다.
        created = _iso_timestamp(created_at)
        if (
            _LOCATOR_RE.fullmatch(clean_report) is None
            or created <= 0
            or created >= cutover_at
        ):
            continue
        expires_at = _legacy_expiry(
            generated_at=generated_at,
            delivery_expires_at=delivery_expiry_by_report.get(clean_report, ""),
            cutover_at=cutover_at,
        )
        if expires_at <= 0:
            continue
        member_rows = member_by_report.get(clean_report, [])
        member_hashes = {digest for _run, digest in member_rows if digest}
        # 두 feature가 같은 보고서를 서로 소유한다고 주장하거나, 과거 MEMBER
        # email이 충돌하면 어느 쪽도 추측하지 않고 닫는다.
        if clean_report in link_reports:
            continue
        if member_rows:
            if len(member_hashes) != 1:
                continue
            digest = next(iter(member_hashes))
            for raw_run, _actor_hash in member_rows:
                clean_run = (
                    raw_run
                    if _LOCATOR_RE.fullmatch(raw_run) is not None
                    else clean_report
                )
                conn.execute(
                    f"""
                    INSERT OR IGNORE INTO {TABLE_LEGACY_RESOURCES}
                        (run_id, report_id, audience, actor_email_hash,
                         expires_at, captured_at, revoked_at)
                    VALUES (?, ?, ?, ?, ?, ?, 0)
                    """,
                    (
                        clean_run,
                        clean_report,
                        LEGACY_AUDIENCE_MEMBER,
                        digest,
                        expires_at,
                        cutover_at,
                    ),
                )
            continue
        if clean_report in member_claimed_reports:
            # 중단/부분 settle MEMBER를 PUBLIC으로 열 바에는 관리자 확인까지 닫는다.
            continue
        conn.execute(
            f"""
            INSERT OR IGNORE INTO {TABLE_LEGACY_RESOURCES}
                (run_id, report_id, audience, actor_email_hash,
                 expires_at, captured_at, revoked_at)
            VALUES (?, ?, ?, '', ?, ?, 0)
            """,
            (
                clean_report,
                clean_report,
                LEGACY_AUDIENCE_PUBLIC,
                expires_at,
                cutover_at,
            ),
        )


def ensure_schema(conn: sqlite3.Connection) -> None:
    """보안 스키마의 모양·FK·DB 상한 trigger를 멱등으로 준비한다."""

    savepoint = "ensure_report_access_schema"
    conn.execute(f"SAVEPOINT {savepoint}")
    try:
        conn.execute(_CREATE_GRANTS_SQL)
        if _object_type(conn, TABLE_GRANTS) != "table" or _columns(
            conn, TABLE_GRANTS
        ) != ("grant_hash", "created_at", "expires_at", "revoked_at"):
            raise RuntimeError("보고서 접근 grant 스키마가 올바르지 않습니다")

        binding_type = _object_type(conn, TABLE_BINDINGS)
        if not binding_type:
            conn.execute(_CREATE_BINDINGS_SQL)
        elif binding_type != "table" or _columns(conn, TABLE_BINDINGS) != (
            "grant_hash",
            "run_id",
            "report_id",
            "created_at",
        ):
            raise RuntimeError("보고서 접근 binding 스키마가 올바르지 않습니다")
        elif not _binding_has_cascade(conn):
            _migrate_binding_cascade(conn)

        conn.execute(_CREATE_MEMBER_BINDINGS_SQL)
        if _object_type(conn, TABLE_MEMBER_BINDINGS) != "table" or _columns(
            conn, TABLE_MEMBER_BINDINGS
        ) != ("run_id", "report_id", "subject_hash", "created_at"):
            raise RuntimeError("MEMBER 접근 binding 스키마가 올바르지 않습니다")
        conn.execute(_CREATE_CUTOVER_SQL)
        if _object_type(conn, TABLE_CUTOVER) != "table" or _columns(
            conn, TABLE_CUTOVER
        ) != ("singleton", "cutover_at"):
            raise RuntimeError("보고서 접근 cutover 스키마가 올바르지 않습니다")
        conn.execute(_CREATE_LEGACY_RESOURCES_SQL)
        if _object_type(conn, TABLE_LEGACY_RESOURCES) != "table" or _columns(
            conn, TABLE_LEGACY_RESOURCES
        ) != (
            "run_id",
            "report_id",
            "audience",
            "actor_email_hash",
            "expires_at",
            "captured_at",
            "revoked_at",
        ):
            raise RuntimeError("보고서 접근 legacy 스키마가 올바르지 않습니다")
        expected_cutover = _cutover_timestamp()
        cutover_row = conn.execute(
            f"SELECT cutover_at FROM {TABLE_CUTOVER} WHERE singleton = 1"
        ).fetchone()
        if cutover_row is None:
            conn.execute(
                f"INSERT INTO {TABLE_CUTOVER}(singleton, cutover_at) VALUES (1, ?)",
                (expected_cutover,),
            )
            _snapshot_legacy_resources(conn, cutover_at=expected_cutover)
        elif float(cutover_row[0]) != expected_cutover:
            # 재배포/rollback이 호환 기간을 새로 시작하지 못하게 한다.
            raise RuntimeError("보고서 접근 cutover 시각이 코드 정본과 다릅니다")
        if not _binding_has_cascade(conn):
            raise RuntimeError("보고서 접근 binding cascade를 확인할 수 없습니다")

        conn.execute(_CREATE_INDEX_SQL)
        conn.execute(_CREATE_MEMBER_INDEX_SQL)
        conn.execute(_CREATE_LEGACY_INDEX_SQL)
        # 같은 이름의 약한 trigger를 IF NOT EXISTS로 믿지 않고 정본으로 교체한다.
        conn.execute(f"DROP TRIGGER IF EXISTS {TRIGGER_GRANT_CAPACITY}")
        conn.execute(f"DROP TRIGGER IF EXISTS {TRIGGER_BINDING_CAPACITY}")
        conn.execute(_CREATE_GRANT_CAPACITY_TRIGGER_SQL)
        conn.execute(_CREATE_BINDING_CAPACITY_TRIGGER_SQL)
    except BaseException:
        try:
            conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        finally:
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise
    else:
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")


def token_hash(token: object) -> str:
    clean = token if isinstance(token, str) else ""
    return hashlib.sha256(clean.encode("utf-8", errors="replace")).hexdigest()


def subject_hash(subject: object) -> str:
    """OAuth 불변 subject 원문을 저장하지 않는 MEMBER 소유자 지문."""

    clean = subject if isinstance(subject, str) else ""
    if not clean:
        return ""
    return hashlib.sha256(clean.encode("utf-8", errors="strict")).hexdigest()


def token_has_valid_shape(token: object) -> bool:
    return isinstance(token, str) and _TOKEN_RE.fullmatch(token) is not None


def _active_hash(
    conn: sqlite3.Connection, raw_token: object, *, now: float
) -> str:
    if not token_has_valid_shape(raw_token):
        return ""
    digest = token_hash(raw_token)
    row = conn.execute(
        f"""
        SELECT 1 FROM {TABLE_GRANTS}
         WHERE grant_hash = ? AND revoked_at = 0
           AND created_at <= ? AND expires_at > ?
        """,
        (digest, float(now), float(now)),
    ).fetchone()
    return digest if row is not None else ""


def issue_and_bind(
    conn: sqlite3.Connection,
    *,
    existing_token: object,
    run_id: str,
    now: float | None = None,
) -> IssuedGrant:
    """살아 있는 브라우저 grant를 재사용하거나 새로 발급해 run에 결속한다."""

    clean_run = str(run_id or "").strip().lower()
    if len(clean_run) != constants.REPORT_ID_HEX_CHARS or any(
        char not in "0123456789abcdef" for char in clean_run
    ):
        raise ValueError("공개 grant에는 정확한 32자리 run ID가 필요합니다")
    issued_at = float(time.time() if now is None else now)
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")

    # 공격자가 익명 시작을 오래 반복해도 만료 tombstone이 DB를 무한히 키우지
    # 않는다. FK cascade가 결속을 먼저 안전하게 지우며 같은 writer transaction
    # 안에서 정리→상한 확인→발급까지 이어져 동시 요청도 상한을 넘지 못한다.
    conn.execute(
        f"""
        DELETE FROM {TABLE_BINDINGS}
         WHERE grant_hash IN (
             SELECT grant_hash FROM {TABLE_GRANTS}
              WHERE revoked_at > 0 OR expires_at <= ?
         )
        """,
        (issued_at,),
    )
    conn.execute(
        f"DELETE FROM {TABLE_GRANTS} WHERE revoked_at > 0 OR expires_at <= ?",
        (issued_at,),
    )

    active = _active_hash(conn, existing_token, now=issued_at)
    if active:
        row = conn.execute(
            f"SELECT expires_at FROM {TABLE_GRANTS} WHERE grant_hash = ?",
            (active,),
        ).fetchone()
        token = str(existing_token)
        digest = active
        expires_at = float(row[0])
        reused = True
    else:
        active_count = int(
            conn.execute(
                f"""
                SELECT COUNT(*) FROM {TABLE_GRANTS}
                 WHERE revoked_at = 0
                   AND created_at <= ? AND expires_at > ?
                """,
                (issued_at, issued_at),
            ).fetchone()[0]
        )
        if active_count >= constants.PUBLIC_ACTIVE_GRANT_LIMIT:
            raise RuntimeError("PUBLIC 보고서 열람 grant 수용량을 초과했습니다")
        token = ""
        digest = ""
        expires_at = issued_at + constants.PUBLIC_GRANT_MAX_AGE_SEC
        reused = False
        for _attempt in range(constants.PUBLIC_GRANT_ALLOCATION_ATTEMPTS):
            candidate = secrets.token_urlsafe(constants.PUBLIC_GRANT_TOKEN_BYTES)
            candidate_hash = token_hash(candidate)
            try:
                conn.execute(
                    f"""
                    INSERT INTO {TABLE_GRANTS}
                        (grant_hash, created_at, expires_at, revoked_at)
                    VALUES (?, ?, ?, 0)
                    """,
                    (candidate_hash, issued_at, expires_at),
                )
            except sqlite3.IntegrityError as exc:
                if "capacity" in str(exc).lower():
                    raise RuntimeError(
                        "PUBLIC 보고서 열람 grant 수용량을 초과했습니다"
                    ) from exc
                continue
            token = candidate
            digest = candidate_hash
            break
        if not token:
            raise RuntimeError("공개 보고서 열람 grant를 발급할 수 없습니다")

    try:
        conn.execute(
            f"""
            INSERT INTO {TABLE_BINDINGS}
                (grant_hash, run_id, report_id, created_at)
            VALUES (?, ?, '', ?)
            ON CONFLICT(grant_hash, run_id) DO NOTHING
            """,
            (digest, clean_run, issued_at),
        )
    except sqlite3.IntegrityError as exc:
        if "capacity" in str(exc).lower():
            raise RuntimeError(
                "한 브라우저의 PUBLIC 보고서 결속 수용량을 초과했습니다"
            ) from exc
        raise
    return IssuedGrant(token, digest, expires_at, reused)


def bind_member_run(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    identity_subject: str,
    now: float | None = None,
) -> bool:
    """새 MEMBER run을 이메일이 아닌 OAuth 불변 subject 지문에 결속한다."""

    clean_run = str(run_id or "").strip().lower()
    digest = subject_hash(identity_subject)
    if (
        len(clean_run) != constants.REPORT_ID_HEX_CHARS
        or any(char not in "0123456789abcdef" for char in clean_run)
        or not digest
    ):
        raise ValueError("MEMBER 결속에는 32자리 run과 불변 계정 subject가 필요합니다")
    cursor = conn.execute(
        f"""
        INSERT INTO {TABLE_MEMBER_BINDINGS}
            (run_id, report_id, subject_hash, created_at)
        VALUES (?, '', ?, ?)
        ON CONFLICT(run_id) DO NOTHING
        """,
        (clean_run, digest, float(time.time() if now is None else now)),
    )
    if cursor.rowcount > 0:
        return True
    row = conn.execute(
        f"SELECT subject_hash FROM {TABLE_MEMBER_BINDINGS} WHERE run_id = ?",
        (clean_run,),
    ).fetchone()
    if row is None:
        return False
    if str(row[0]) != digest:
        raise ValueError("같은 MEMBER run의 불변 계정 소유자가 다릅니다")
    return True


def bind_report(
    conn: sqlite3.Connection, *, run_id: str, report_id: str
) -> bool:
    """보고서 저장 transaction 안에서 기존 run 결속에 정확한 report를 붙인다."""

    clean_run = str(run_id or "").strip().lower()
    clean_report = str(report_id or "").strip().lower()
    if not clean_run or not clean_report:
        raise ValueError("run/report ID가 필요합니다")
    public_cursor = conn.execute(
        f"""
        UPDATE {TABLE_BINDINGS}
           SET report_id = ?
         WHERE run_id = ? AND (report_id = '' OR report_id = ?)
        """,
        (clean_report, clean_run, clean_report),
    )
    member_cursor = conn.execute(
        f"""
        UPDATE {TABLE_MEMBER_BINDINGS}
           SET report_id = ?
         WHERE run_id = ? AND (report_id = '' OR report_id = ?)
        """,
        (clean_report, clean_run, clean_report),
    )
    return public_cursor.rowcount > 0 or member_cursor.rowcount > 0


def member_subject_allows(
    conn: sqlite3.Connection, *, identity_subject: str, locator: str
) -> bool:
    """subject 없는 legacy usage는 이메일이 같아도 권한으로 승격하지 않는다."""

    digest = subject_hash(identity_subject)
    if not digest:
        return False
    clean = str(locator or "").strip().lower()
    row = conn.execute(
        f"""
        SELECT 1 FROM {TABLE_MEMBER_BINDINGS}
         WHERE subject_hash = ? AND (run_id = ? OR report_id = ?)
         LIMIT 1
        """,
        (digest, clean, clean),
    ).fetchone()
    return row is not None


def public_grant_allows(
    conn: sqlite3.Connection,
    *,
    raw_token: object,
    locator: str,
    now: float | None = None,
) -> bool:
    """쓰기 없이 현재 grant와 정확히 결속된 run/report만 허용한다."""

    checked_at = float(time.time() if now is None else now)
    digest = _active_hash(conn, raw_token, now=checked_at)
    if not digest:
        return False
    clean = str(locator or "").strip().lower()
    row = conn.execute(
        f"""
        SELECT 1 FROM {TABLE_BINDINGS}
         WHERE grant_hash = ? AND (run_id = ? OR report_id = ?)
         LIMIT 1
        """,
        (digest, clean, clean),
    ).fetchone()
    return row is not None


def legacy_access_for(
    conn: sqlite3.Connection,
    *,
    locator: str,
    now: float | None = None,
) -> LegacyAccess | None:
    """고정 snapshot의 아직 살아 있고 철회되지 않은 호환 권한만 읽는다."""

    clean = str(locator or "").strip().lower()
    if _LOCATOR_RE.fullmatch(clean) is None:
        return None
    checked_at = float(time.time() if now is None else now)
    row = conn.execute(
        f"""
        SELECT audience, actor_email_hash, expires_at
          FROM {TABLE_LEGACY_RESOURCES}
         WHERE (run_id = ? OR report_id = ?)
           AND revoked_at = 0
           AND captured_at = (SELECT cutover_at FROM {TABLE_CUTOVER} WHERE singleton = 1)
           AND expires_at > ?
         LIMIT 1
        """,
        (clean, clean, checked_at),
    ).fetchone()
    if row is None:
        return None
    return LegacyAccess(str(row[0]), str(row[1]), float(row[2]))


def migrate_legacy_member_bindings(
    conn: sqlite3.Connection,
    *,
    member_email: str,
    identity_subject: str,
    now: float | None = None,
) -> int:
    """재로그인한 기존 MEMBER의 확정된 email 소유권을 sub로 원자 이관한다.

    로그인 callback처럼 이미 쓰기인 경계에서만 부른다. 결과 GET에서 이 함수를
    부르면 안 되며, 초대가 철회됐거나 legacy-email 합성 subject면 아무것도
    만들지 않는다.
    """

    clean_email = str(member_email or "").strip().lower()
    digest = email_hash(clean_email)
    stable_subject = str(identity_subject or "").strip()
    if (
        not digest
        or not stable_subject
        or stable_subject.startswith("legacy-email:")
        or _object_type(conn, "allowed_users") != "table"
    ):
        return 0
    allowed = conn.execute(
        "SELECT 1 FROM allowed_users WHERE email = ? AND is_active = 1",
        (clean_email,),
    ).fetchone()
    if allowed is None:
        return 0
    checked_at = float(time.time() if now is None else now)
    rows = conn.execute(
        f"""
        SELECT run_id, report_id
          FROM {TABLE_LEGACY_RESOURCES}
         WHERE audience = ? AND actor_email_hash = ?
           AND revoked_at = 0 AND expires_at > ?
           AND captured_at = (SELECT cutover_at FROM {TABLE_CUTOVER} WHERE singleton = 1)
         ORDER BY run_id
        """,
        (LEGACY_AUDIENCE_MEMBER, digest, checked_at),
    ).fetchall()
    if not rows:
        return 0

    savepoint = "migrate_report_access_legacy_member"
    conn.execute(f"SAVEPOINT {savepoint}")
    migrated = 0
    try:
        for run_id, report_id in rows:
            if not bind_member_run(
                conn,
                run_id=str(run_id),
                identity_subject=stable_subject,
                now=checked_at,
            ):
                raise RuntimeError("기존 MEMBER run의 subject 결속을 만들지 못했습니다")
            if not bind_report(
                conn, run_id=str(run_id), report_id=str(report_id)
            ):
                raise RuntimeError("기존 MEMBER report 결속을 만들지 못했습니다")
            migrated += 1
    except BaseException:
        try:
            conn.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        finally:
            conn.execute(f"RELEASE SAVEPOINT {savepoint}")
        raise
    else:
        conn.execute(f"RELEASE SAVEPOINT {savepoint}")
    return migrated


def revoke_grant(
    conn: sqlite3.Connection, *, grant_hash: str, revoked_at: float | None = None
) -> bool:
    clean = str(grant_hash or "").strip().lower()
    if _HASH_RE.fullmatch(clean) is None:
        return False
    cursor = conn.execute(
        f"""
        UPDATE {TABLE_GRANTS} SET revoked_at = ?
         WHERE grant_hash = ? AND revoked_at = 0
        """,
        (float(time.time() if revoked_at is None else revoked_at), clean),
    )
    return cursor.rowcount > 0


def revoke_resource(
    conn: sqlite3.Connection, *, locator: str, revoked_at: float | None = None
) -> int:
    """대상 run/report 결속만 철회하고 같은 브라우저의 다른 보고서는 보존한다."""

    clean = str(locator or "").strip().lower()
    when = float(time.time() if revoked_at is None else revoked_at)
    cursor = conn.execute(
        f"""
        DELETE FROM {TABLE_BINDINGS}
         WHERE run_id = ? OR report_id = ?
        """,
        (clean, clean),
    )
    legacy_cursor = conn.execute(
        f"""
        UPDATE {TABLE_LEGACY_RESOURCES}
           SET revoked_at = ?
         WHERE revoked_at = 0 AND (run_id = ? OR report_id = ?)
        """,
        (when, clean, clean),
    )
    return cursor.rowcount + legacy_cursor.rowcount
