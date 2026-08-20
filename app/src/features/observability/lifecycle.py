"""유료 앞단부터 최종 관측 1행까지 잇는 SQLite 상태 기계.

한 요청은 ``pending -> running -> final`` 순서로만 움직인다. 현재 상태 표는
``run_id``를 기본 키로 삼아 최종 관측값을 정확히 하나만 보관하고, 별도 감사 표는
상태 전이를 덧붙이기만 한다. 따라서 서버 재시작 뒤에도 확인 대기·실행 중 요청을
찾을 수 있고, 같은 요청을 두 실행 흐름이 동시에 소비해도 한쪽만 성공한다.

이 모듈은 DB 위치나 연결 방법을 모른다. 호출자가 연 ``sqlite3.Connection``만
받으므로 storage feature를 import하지 않는다.

개인정보 경계
-------------
대기 상태에는 요청 번호·시각·직무·확정 비용·소요 시간·모델·만료 시각만 둔다.
회사명·주소·공고·이미지·이메일·공유 열쇠·확인 토큰을 받을 필드 자체가 없다.
직무는 자유 입력이므로 길이를 제한하고 이메일·전화번호·주민등록번호처럼 보이는
값은 저장 전에 거부한다. 사람 이름이나 회사 이름을 완벽히 판별하는 기능은 아니며,
호출부도 반드시 ``job``에 직무만 넘겨야 한다.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import math
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from typing import Final, Iterator

from src.features.observability.records import RunRecord, normalize_persisted_cells


TABLE_RUN_LIFECYCLE: Final[str] = "observability_run_lifecycle"
TABLE_RUN_AUDIT: Final[str] = "observability_run_lifecycle_audit"

STATE_PENDING: Final[str] = "pending"
STATE_RUNNING: Final[str] = "running"
STATE_FINAL: Final[str] = "final"
STATE_VALUES: Final[tuple[str, ...]] = (
    STATE_PENDING,
    STATE_RUNNING,
    STATE_FINAL,
)

MAX_RUN_ID_CHARS: Final[int] = 128
MAX_JOB_CHARS: Final[int] = 80
MAX_MODEL_CHARS: Final[int] = 256

_RUN_ID_RE = re.compile(r"^[A-Za-z0-9._:-]+$")
_EMAIL_RE = re.compile(r"(?i)[^\s@()<>]+@[^\s@()<>]+\.[^\s@()<>]+")
_PHONE_RE = re.compile(
    r"(?<!\d)(?:\+?82[-.\s]?)?0(?:1[016789]|2|[3-6][1-5])"
    r"[-.\s]?\d{3,4}[-.\s]?\d{4}(?!\d)"
)
_RESIDENT_ID_RE = re.compile(r"(?<!\d)\d{6}[-.\s]?[1-4]\d{6}(?!\d)")


CREATE_CURRENT_SQL: Final[str] = f"""
CREATE TABLE IF NOT EXISTS {TABLE_RUN_LIFECYCLE} (
    run_id              TEXT PRIMARY KEY,
    state               TEXT NOT NULL
                        CHECK(state IN ('{STATE_PENDING}', '{STATE_RUNNING}', '{STATE_FINAL}')),
    at                  TEXT NOT NULL,
    job                 TEXT NOT NULL
                        CHECK(length(job) BETWEEN 1 AND {MAX_JOB_CHARS}),
    confirmed_cost_krw  REAL NOT NULL
                        CHECK(confirmed_cost_krw >= 0),
    elapsed_sec         REAL NOT NULL
                        CHECK(elapsed_sec >= 0),
    model               TEXT NOT NULL
                        CHECK(length(model) <= {MAX_MODEL_CHARS}),
    expires_at          TEXT,
    final_record_json   TEXT,
    CHECK (
        (state = '{STATE_FINAL}' AND final_record_json IS NOT NULL AND expires_at IS NULL)
        OR
        (state IN ('{STATE_PENDING}', '{STATE_RUNNING}')
            AND final_record_json IS NULL AND expires_at IS NOT NULL)
    )
)
"""

CREATE_AUDIT_SQL: Final[str] = f"""
CREATE TABLE IF NOT EXISTS {TABLE_RUN_AUDIT} (
    event_id       INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id         TEXT NOT NULL,
    from_state     TEXT
                   CHECK(from_state IS NULL OR from_state IN
                       ('{STATE_PENDING}', '{STATE_RUNNING}', '{STATE_FINAL}')),
    to_state       TEXT NOT NULL
                   CHECK(to_state IN ('{STATE_PENDING}', '{STATE_RUNNING}', '{STATE_FINAL}')),
    event_at       TEXT NOT NULL,
    record_sha256  TEXT
)
"""

CREATE_AUDIT_INDEX_SQL: Final[str] = f"""
CREATE INDEX IF NOT EXISTS idx_observability_run_audit
    ON {TABLE_RUN_AUDIT}(run_id, event_id)
"""

CREATE_AUDIT_NO_UPDATE_SQL: Final[str] = f"""
CREATE TRIGGER IF NOT EXISTS trg_observability_run_audit_no_update
BEFORE UPDATE ON {TABLE_RUN_AUDIT}
BEGIN
    SELECT RAISE(ABORT, '관측 상태 감사 이력은 고칠 수 없습니다');
END
"""

CREATE_AUDIT_NO_DELETE_SQL: Final[str] = f"""
CREATE TRIGGER IF NOT EXISTS trg_observability_run_audit_no_delete
BEFORE DELETE ON {TABLE_RUN_AUDIT}
BEGIN
    SELECT RAISE(ABORT, '관측 상태 감사 이력은 지울 수 없습니다');
END
"""

_SELECT_CURRENT: Final[str] = f"""
SELECT run_id, state, at, job, confirmed_cost_krw, elapsed_sec, model,
       expires_at, final_record_json
  FROM {TABLE_RUN_LIFECYCLE}
"""


class LifecycleError(ValueError):
    """관측 상태 전이 요청이 현재 상태 또는 저장 규칙과 맞지 않는다."""


class RunNotFoundError(LifecycleError):
    """요청 번호에 해당하는 관측 상태가 없다."""


class StateConflictError(LifecycleError):
    """같은 요청을 다른 값으로 다시 쓰거나 되돌리려 했다."""


class UnsafePendingDataError(LifecycleError):
    """대기표에 저장하면 안 되는 개인정보 모양의 값이 발견됐다."""


class LifecycleCorruptionError(RuntimeError):
    """DB에 API가 만들 수 없는 깨진 관측 상태가 들어 있다."""


@dataclass(frozen=True)
class LifecycleEntry:
    """한 요청의 현재 관측 상태."""

    run_id: str
    state: str
    at: str
    job: str
    confirmed_cost_krw: float
    elapsed_sec: float
    model: str
    expires_at: str | None
    final_record: RunRecord | None


@dataclass(frozen=True)
class AuditEvent:
    """고칠 수 없고 덧붙이기만 하는 상태 전이 한 건."""

    event_id: int
    run_id: str
    from_state: str | None
    to_state: str
    event_at: str
    record_sha256: str | None


def ensure_schema(conn: sqlite3.Connection) -> None:
    """현재 상태 표·감사 표·감사 표 변경 방지 장치를 만든다."""
    conn.execute(CREATE_CURRENT_SQL)
    conn.execute(CREATE_AUDIT_SQL)
    conn.execute(CREATE_AUDIT_INDEX_SQL)
    conn.execute(CREATE_AUDIT_NO_UPDATE_SQL)
    conn.execute(CREATE_AUDIT_NO_DELETE_SQL)


def begin_pending(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    at: str,
    job: str,
    confirmed_cost_krw: float,
    elapsed_sec: float,
    model: str,
    expires_at: str,
) -> bool:
    """회사 확인을 기다리는 요청을 만든다.

    완전히 같은 값으로 다시 부르면 ``False``다. 값이 하나라도 다르거나 이미 실행·
    마감된 요청 번호라면 조용히 덮지 않고 ``StateConflictError``를 낸다.
    """
    clean = _clean_pending_values(
        run_id=run_id,
        at=at,
        job=job,
        confirmed_cost_krw=confirmed_cost_krw,
        elapsed_sec=elapsed_sec,
        model=model,
        expires_at=expires_at,
    )
    if _timeline(clean[6]) <= _timeline(clean[1]):
        raise LifecycleError("확인 대기 만료 시각은 시작 시각보다 뒤여야 합니다")

    with _savepoint(conn, "obs_begin_pending"):
        cursor = conn.execute(
            f"""
            INSERT OR IGNORE INTO {TABLE_RUN_LIFECYCLE}
                (run_id, state, at, job, confirmed_cost_krw, elapsed_sec,
                 model, expires_at, final_record_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL)
            """,
            (
                clean[0],
                STATE_PENDING,
                clean[1],
                clean[2],
                clean[3],
                clean[4],
                clean[5],
                clean[6],
            ),
        )
        if cursor.rowcount == 1:
            _append_audit(
                conn,
                run_id=clean[0],
                from_state=None,
                to_state=STATE_PENDING,
                event_at=clean[1],
            )
            return True

        existing = get_entry(conn, clean[0])
        if existing is None:
            raise LifecycleCorruptionError("대기 요청 삽입 경합 뒤 현재 행을 찾지 못했습니다")
        wanted = (
            clean[0],
            STATE_PENDING,
            clean[1],
            clean[2],
            clean[3],
            clean[4],
            clean[5],
            clean[6],
        )
        found = (
            existing.run_id,
            existing.state,
            existing.at,
            existing.job,
            existing.confirmed_cost_krw,
            existing.elapsed_sec,
            existing.model,
            existing.expires_at,
        )
        if found == wanted and existing.final_record is None:
            return False
        raise StateConflictError("같은 요청 번호에 다른 관측 상태 또는 값이 있습니다")


def mark_running(
    conn: sqlite3.Connection, run_id: str, *, event_at: str
) -> bool:
    """대기 요청을 정확히 한 실행 흐름만 소비한다.

    첫 소비자는 ``True``를 받고 provider 실행을 이어간다. 동시에 들어온 두 번째
    소비자나 같은 호출의 재시도는 ``False``를 받아 실행하지 않아야 한다.
    """
    clean_run = _clean_run_id(run_id)
    clean_event_at = _clean_iso(event_at, "event_at")
    with _savepoint(conn, "obs_mark_running"):
        cursor = conn.execute(
            f"""
            UPDATE {TABLE_RUN_LIFECYCLE}
               SET state = ?
             WHERE run_id = ? AND state = ?
            """,
            (STATE_RUNNING, clean_run, STATE_PENDING),
        )
        if cursor.rowcount == 1:
            _append_audit(
                conn,
                run_id=clean_run,
                from_state=STATE_PENDING,
                to_state=STATE_RUNNING,
                event_at=clean_event_at,
            )
            return True

        existing = get_entry(conn, clean_run)
        if existing is None:
            raise RunNotFoundError(f"관측 대기 요청을 찾지 못했습니다: {clean_run}")
        if existing.state == STATE_RUNNING:
            return False
        raise StateConflictError("이미 최종 마감된 요청은 다시 실행할 수 없습니다")


def consume_pending(
    conn: sqlite3.Connection, run_id: str, *, event_at: str
) -> bool:
    """``mark_running``의 화면 배선용 별칭."""
    return mark_running(conn, run_id, event_at=event_at)


def finalize_once(
    conn: sqlite3.Connection,
    record: RunRecord,
    *,
    event_at: str | None = None,
    expected_state: str | None = None,
) -> bool:
    """현재 요청을 최종 관측값 하나로 마감한다.

    대기·실행 중 요청뿐 아니라 식별 기술 오류처럼 대기표를 만들기 전에 끝난 요청도
    곧바로 final로 넣을 수 있다. 같은 ``RunRecord`` 재시도는 ``False``이며, 같은
    요청 번호의 다른 최종값은 ``StateConflictError``로 막는다.

    ``expected_state``를 주면 그 상태에서만 마감한다. 확인 거절·만료 정리에는
    ``pending``, 실행 완료에는 ``running``을 넘겨 경합 중 오분류를 막을 수 있다.
    """
    if expected_state is not None and expected_state not in (
        STATE_PENDING,
        STATE_RUNNING,
    ):
        raise LifecycleError("expected_state는 pending 또는 running이어야 합니다")

    clean_record = _clean_record(record)
    record_json = _encode_record(clean_record)
    event_time = _clean_iso(event_at or clean_record.at, "event_at")
    record_hash = hashlib.sha256(record_json.encode("utf-8")).hexdigest()
    allowed_states = (
        (expected_state,) if expected_state is not None else (STATE_PENDING, STATE_RUNNING)
    )

    with _savepoint(conn, "obs_finalize_once"):
        # 상태를 하나씩 조건부 갱신하면 UPDATE 자체가 소비권이다. 먼저 읽고 나중에
        # 쓰는 방식과 달리 두 연결이 동시에 같은 요청을 마감할 틈이 없다.
        for from_state in allowed_states:
            cursor = conn.execute(
                f"""
                UPDATE {TABLE_RUN_LIFECYCLE}
                   SET state = ?, expires_at = NULL, final_record_json = ?
                 WHERE run_id = ? AND state = ? AND job = ?
                   AND confirmed_cost_krw <= ? AND elapsed_sec <= ?
                """,
                (
                    STATE_FINAL,
                    record_json,
                    clean_record.run_id,
                    from_state,
                    clean_record.job,
                    clean_record.cost_krw,
                    clean_record.elapsed_sec,
                ),
            )
            if cursor.rowcount == 1:
                _append_audit(
                    conn,
                    run_id=clean_record.run_id,
                    from_state=from_state,
                    to_state=STATE_FINAL,
                    event_at=event_time,
                    record_sha256=record_hash,
                )
                return True

        existing = get_entry(conn, clean_record.run_id)
        if existing is not None:
            if existing.state == STATE_FINAL:
                if existing.final_record == clean_record:
                    return False
                raise StateConflictError("같은 요청 번호에 다른 최종 관측값이 있습니다")
            if expected_state is not None and existing.state != expected_state:
                raise StateConflictError(
                    f"{expected_state} 요청만 마감할 수 있지만 현재는 {existing.state}입니다"
                )
            raise StateConflictError(
                "대기값의 직무·확정 비용·소요 시간이 최종 관측값과 맞지 않습니다"
            )

        if expected_state is not None:
            raise RunNotFoundError(
                f"{expected_state} 상태의 요청을 찾지 못했습니다: {clean_record.run_id}"
            )

        cursor = conn.execute(
            f"""
            INSERT OR IGNORE INTO {TABLE_RUN_LIFECYCLE}
                (run_id, state, at, job, confirmed_cost_krw, elapsed_sec,
                 model, expires_at, final_record_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?)
            """,
            (
                clean_record.run_id,
                STATE_FINAL,
                clean_record.at,
                clean_record.job,
                clean_record.cost_krw,
                clean_record.elapsed_sec,
                clean_record.model,
                record_json,
            ),
        )
        if cursor.rowcount == 1:
            _append_audit(
                conn,
                run_id=clean_record.run_id,
                from_state=None,
                to_state=STATE_FINAL,
                event_at=event_time,
                record_sha256=record_hash,
            )
            return True

        # 다른 연결이 같은 순간 직접 final을 넣은 경우 같은 값만 멱등으로 본다.
        raced = get_entry(conn, clean_record.run_id)
        if raced is not None and raced.state == STATE_FINAL:
            if raced.final_record == clean_record:
                return False
        raise StateConflictError("같은 요청 번호가 동시에 다른 값으로 마감됐습니다")


def get_entry(conn: sqlite3.Connection, run_id: str) -> LifecycleEntry | None:
    """요청의 현재 상태를 읽는다. 없으면 ``None``이다."""
    clean_run = _clean_run_id(run_id)
    row = conn.execute(
        _SELECT_CURRENT + " WHERE run_id = ?",
        (clean_run,),
    ).fetchone()
    return _entry_from_row(row) if row is not None else None


def read_final(conn: sqlite3.Connection, run_id: str) -> RunRecord | None:
    """최종 마감된 관측값만 돌려준다."""
    entry = get_entry(conn, run_id)
    if entry is None or entry.state != STATE_FINAL:
        return None
    if entry.final_record is None:
        raise LifecycleCorruptionError("final 상태인데 최종 관측값이 없습니다")
    return entry.final_record


def list_final(conn: sqlite3.Connection) -> list[RunRecord]:
    """현재 상태가 final인 관측값을 ``(at, run_id)`` 순서로 읽는다.

    대시보드는 이 결과를 그대로 집계할 수 있다. JSON이나 상태가 깨진 행은 조용히
    건너뛰지 않고 ``LifecycleCorruptionError``를 내어 관측 누락을 드러낸다.
    """
    rows = conn.execute(
        _SELECT_CURRENT + " WHERE state = ?",
        (STATE_FINAL,),
    ).fetchall()
    records: list[RunRecord] = []
    for row in rows:
        entry = _entry_from_row(row)
        if entry.final_record is None:
            raise LifecycleCorruptionError("final 상태인데 최종 관측값이 없습니다")
        records.append(entry.final_record)
    return sorted(records, key=lambda record: (record.at, record.run_id))


def list_expired_pending(
    conn: sqlite3.Connection, *, now: str
) -> list[LifecycleEntry]:
    """만료 시각이 지난 pending 요청을 오래된 순서로 읽는다.

    읽기만 한다. 호출자는 각 항목에 맞는 종료값으로 ``RunRecord``를 만든 뒤
    ``finalize_once(..., expected_state=STATE_PENDING)``로 마감한다.
    """
    now_point = _timeline(_clean_iso(now, "now"))
    rows = conn.execute(
        _SELECT_CURRENT + " WHERE state = ? ORDER BY at, run_id",
        (STATE_PENDING,),
    ).fetchall()
    entries = [_entry_from_row(row) for row in rows]
    return [
        entry
        for entry in entries
        if entry.expires_at is not None
        and _timeline(entry.expires_at) <= now_point
    ]


def list_restart_candidates(conn: sqlite3.Connection) -> list[LifecycleEntry]:
    """재시작으로 이어갈 수 없어진 pending·running 요청을 읽는다.

    pending은 확인 종료, running은 실행 중 기술 중단처럼 서로 다른 종료값이 필요할
    수 있으므로 상태를 합치지 않고 그대로 돌려준다. 이 함수는 서버 시작 시점처럼
    새 실행 흐름이 생기기 전에 호출해야 한다.
    """
    rows = conn.execute(
        _SELECT_CURRENT
        + " WHERE state IN (?, ?) ORDER BY at, run_id",
        (STATE_PENDING, STATE_RUNNING),
    ).fetchall()
    return [_entry_from_row(row) for row in rows]


def list_audit(conn: sqlite3.Connection, run_id: str) -> list[AuditEvent]:
    """요청 하나의 덧붙이기 전용 상태 전이를 순서대로 읽는다."""
    clean_run = _clean_run_id(run_id)
    rows = conn.execute(
        f"""
        SELECT event_id, run_id, from_state, to_state, event_at, record_sha256
          FROM {TABLE_RUN_AUDIT}
         WHERE run_id = ?
         ORDER BY event_id
        """,
        (clean_run,),
    ).fetchall()
    return [
        AuditEvent(
            event_id=int(row[0]),
            run_id=str(row[1]),
            from_state=str(row[2]) if row[2] is not None else None,
            to_state=str(row[3]),
            event_at=str(row[4]),
            record_sha256=str(row[5]) if row[5] is not None else None,
        )
        for row in rows
    ]


def safe_job(value: str) -> str:
    """관측 저장에 안전한 직무만 정규화해 돌려준다.

    길이를 넘거나 이메일·전화번호·주민등록번호처럼 보이면
    ``UnsafePendingDataError`` 또는 ``LifecycleError``를 낸다. 호출부는 이 예외를
    잡아 원문 대신 고정된 비식별 문구를 저장할 수 있으며 사용자 흐름을 막을 필요가
    없다. pending과 final이 같은 방어 규칙을 쓰도록 공개한 단일 진입점이다.
    """
    return _clean_job(value)


def _clean_pending_values(
    *,
    run_id: str,
    at: str,
    job: str,
    confirmed_cost_krw: float,
    elapsed_sec: float,
    model: str,
    expires_at: str,
) -> tuple[str, str, str, float, float, str, str]:
    return (
        _clean_run_id(run_id),
        _clean_iso(at, "at"),
        safe_job(job),
        _clean_nonnegative_number(confirmed_cost_krw, "confirmed_cost_krw"),
        _clean_nonnegative_number(elapsed_sec, "elapsed_sec"),
        _clean_model(model),
        _clean_iso(expires_at, "expires_at"),
    )


def _clean_record(record: RunRecord) -> RunRecord:
    clean_run = _clean_run_id(record.run_id)
    clean_at = _clean_iso(record.at, "record.at")
    clean_job = safe_job(record.job)
    clean_cost = _clean_nonnegative_number(record.cost_krw, "record.cost_krw")
    clean_elapsed = _clean_nonnegative_number(record.elapsed_sec, "record.elapsed_sec")
    clean_model = _clean_model(record.model)
    return replace(
        record,
        run_id=clean_run,
        at=clean_at,
        job=clean_job,
        cost_krw=clean_cost,
        elapsed_sec=clean_elapsed,
        model=clean_model,
    )


def _clean_run_id(value: str) -> str:
    if not isinstance(value, str):
        raise LifecycleError("run_id는 문자열이어야 합니다")
    clean = value.strip()
    if not clean or len(clean) > MAX_RUN_ID_CHARS or _RUN_ID_RE.fullmatch(clean) is None:
        raise LifecycleError("run_id 형식이 올바르지 않습니다")
    return clean


def _clean_job(value: str) -> str:
    if not isinstance(value, str):
        raise LifecycleError("job은 문자열이어야 합니다")
    clean = " ".join(value.split())
    if not clean:
        raise LifecycleError("job이 비어 있습니다")
    if len(clean) > MAX_JOB_CHARS:
        raise LifecycleError(f"job은 {MAX_JOB_CHARS}자를 넘을 수 없습니다")
    if any(pattern.search(clean) for pattern in (_EMAIL_RE, _PHONE_RE, _RESIDENT_ID_RE)):
        raise UnsafePendingDataError("job에 개인정보처럼 보이는 값이 있어 저장하지 않습니다")
    return clean


def _clean_model(value: str) -> str:
    if not isinstance(value, str):
        raise LifecycleError("model은 문자열이어야 합니다")
    clean = " ".join(value.split())
    if len(clean) > MAX_MODEL_CHARS:
        raise LifecycleError(f"model은 {MAX_MODEL_CHARS}자를 넘을 수 없습니다")
    return clean


def _clean_nonnegative_number(value: float, field_name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise LifecycleError(f"{field_name}은 숫자여야 합니다") from exc
    if not math.isfinite(number) or number < 0:
        raise LifecycleError(f"{field_name}은 0 이상의 유한한 수여야 합니다")
    return number


def _clean_iso(value: str, field_name: str) -> str:
    if not isinstance(value, str):
        raise LifecycleError(f"{field_name}은 ISO 8601 문자열이어야 합니다")
    try:
        parsed = dt.datetime.fromisoformat(value.strip())
    except (TypeError, ValueError) as exc:
        raise LifecycleError(f"{field_name}은 ISO 8601 형식이어야 합니다") from exc
    return parsed.isoformat(timespec="seconds")


def _timeline(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is not None:
        return parsed.astimezone(dt.timezone.utc).replace(tzinfo=None)
    return parsed


def _encode_record(record: RunRecord) -> str:
    try:
        return json.dumps(
            asdict(record),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise LifecycleError("최종 관측값을 안전한 JSON으로 만들 수 없습니다") from exc


def _decode_record(raw: str) -> RunRecord:
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise TypeError("최종 관측값 JSON이 객체가 아닙니다")
        return RunRecord(**normalize_persisted_cells(data))
    except (json.JSONDecodeError, TypeError, ValueError) as exc:
        raise LifecycleCorruptionError("최종 관측값 JSON이 깨졌습니다") from exc


def _entry_from_row(row: sqlite3.Row | tuple) -> LifecycleEntry:
    state = str(row[1])
    if state not in STATE_VALUES:
        raise LifecycleCorruptionError(f"모르는 관측 상태입니다: {state!r}")
    raw_final = row[8]
    final_record = _decode_record(str(raw_final)) if raw_final is not None else None
    if (state == STATE_FINAL) != (final_record is not None):
        raise LifecycleCorruptionError("현재 상태와 최종 관측값 유무가 맞지 않습니다")
    return LifecycleEntry(
        run_id=str(row[0]),
        state=state,
        at=str(row[2]),
        job=str(row[3]),
        confirmed_cost_krw=float(row[4]),
        elapsed_sec=float(row[5]),
        model=str(row[6]),
        expires_at=str(row[7]) if row[7] is not None else None,
        final_record=final_record,
    )


def _append_audit(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    from_state: str | None,
    to_state: str,
    event_at: str,
    record_sha256: str | None = None,
) -> None:
    conn.execute(
        f"""
        INSERT INTO {TABLE_RUN_AUDIT}
            (run_id, from_state, to_state, event_at, record_sha256)
        VALUES (?, ?, ?, ?, ?)
        """,
        (run_id, from_state, to_state, event_at, record_sha256),
    )


@contextmanager
def _savepoint(conn: sqlite3.Connection, name: str) -> Iterator[None]:
    """호출자의 바깥 트랜잭션을 커밋하지 않고 상태+감사를 원자적으로 묶는다."""
    conn.execute(f"SAVEPOINT {name}")
    try:
        yield
    except BaseException:
        conn.execute(f"ROLLBACK TO SAVEPOINT {name}")
        conn.execute(f"RELEASE SAVEPOINT {name}")
        raise
    else:
        conn.execute(f"RELEASE SAVEPOINT {name}")
