"""초대 링크의 «만료일»과 «연장 이력»을 못 박는다.

★ 이 시험이 지키는 것 — **만료를 조용히 바꾸지 않는다.**
  기본 수명을 60일에서 90일로 늘리는 순간, 이미 뿌려 둔 링크까지 30일 더
  열리면 그건 사용자가 결정한 적 없는 노출 연장이다. 그래서 기존 행은
  «그 행이 원래 닫히던 날»을 표에 그대로 적어 굳히고, 90일은 **새 발급부터**
  적용한다.

⚠️ 여기 적힌 날짜 수는 **리터럴**이다. 생산 상수를 import해 같은 상수와
  비교하면 값이 몰래 바뀌어도 시험이 통과한다 — 그건 검증이 아니라 순환이다.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
from collections.abc import Iterator

import pytest

from src.features.sharelink import constants
from src.features.sharelink import logic as share_logic
from src.features.sharelink import store as share_store

_열쇠 = "aa11bb22cc33dd44aa11bb22cc33dd44"
_옛열쇠 = "1234567890abcdef1234567890abcdef"
_발급 = "2026-09-02T09:00:00+09:00"
_발급일 = dt.date(2026, 9, 2)

#: 기존 규칙(리터럴). 이미 뿌린 링크는 발급 60일째부터 닫혔다.
_옛수명 = 60
#: 새 규칙(리터럴). 새로 발급하는 링크의 기본 수명.
_새수명 = 90


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(":memory:")
    share_store.ensure_schema(connection)
    try:
        yield connection
    finally:
        connection.close()


def _옛방식으로_행을_넣는다(connection: sqlite3.Connection, key: str) -> str:
    """만료 열이 생기기 «전»에 저장된 행과 같은 모양을 만든다."""

    key_hash = share_store.key_hash_of(key)
    connection.execute(
        f"""
        INSERT INTO {share_store.TABLE_SHARE_LINKS}
            (key_hash, company, job, report_id, note, created_at, expires_at)
        VALUES (?, ?, '', '', '', ?, '')
        """,
        (key_hash, "옛회사", _발급),
    )
    return key_hash


# ══════════════════════════════════════════════════════════
# ① 기존 행은 옛 규칙 그대로
# ══════════════════════════════════════════════════════════


def test_빈_expires_at은_기존_60일_규칙을_따른다(conn):
    """만료 열이 비어 있던 행은 «원래 닫히던 날»을 그대로 지킨다.

    ★ 이 시험이 없으면 기본값을 90일로 올린 순간 이미 뿌린 링크가 30일 더
      열린다. 그건 아무도 결정하지 않은 노출 연장이다.
    """

    _옛방식으로_행을_넣는다(conn, _옛열쇠)
    share_store.ensure_schema(conn)  # 멱등 재실행이 기존 행을 굳힌다

    링크 = share_store.load(conn, _옛열쇠)
    assert 링크 is not None
    assert 링크.expires_at == (_발급일 + dt.timedelta(days=_옛수명)).isoformat()

    assert not share_logic.link_expired(
        링크, today=_발급일 + dt.timedelta(days=_옛수명 - 1)
    )
    assert share_logic.link_expired(
        링크, today=_발급일 + dt.timedelta(days=_옛수명)
    )


def test_스키마_재실행은_이미_굳은_만료일을_다시_바꾸지_않는다(conn):
    """멱등 확인 — ensure_schema를 몇 번 돌려도 만료일이 흔들리지 않는다."""

    _옛방식으로_행을_넣는다(conn, _옛열쇠)
    share_store.ensure_schema(conn)
    첫값 = share_store.load(conn, _옛열쇠).expires_at
    conn.execute(
        f"UPDATE {share_store.TABLE_SHARE_LINKS} SET expires_at = ? "
        "WHERE key_hash = ?",
        ("2027-01-01", share_store.key_hash_of(_옛열쇠)),
    )
    share_store.ensure_schema(conn)

    assert 첫값 == (_발급일 + dt.timedelta(days=_옛수명)).isoformat()
    assert share_store.load(conn, _옛열쇠).expires_at == "2027-01-01"


# ══════════════════════════════════════════════════════════
# ② 새 발급은 90일
# ══════════════════════════════════════════════════════════


def test_새_발급은_90일_만료다(conn):
    assert share_store.insert_new(
        conn, key=_열쇠, company="카카오", job="", now_iso=_발급
    )

    링크 = share_store.load(conn, _열쇠)
    assert 링크 is not None
    assert 링크.expires_at == (_발급일 + dt.timedelta(days=_새수명)).isoformat()
    assert not share_logic.link_expired(
        링크, today=_발급일 + dt.timedelta(days=_새수명 - 1)
    )
    assert share_logic.link_expired(
        링크, today=_발급일 + dt.timedelta(days=_새수명)
    )


def test_기본_수명_상수는_리터럴_90일이고_쿠키도_같은_날짜수다():
    """★ 상수 자체를 여기서 못 박는다 — 화면·판정은 이 값을 쓴다."""

    assert constants.DEFAULT_LINK_MAX_AGE_DAYS == 90
    assert constants.LEGACY_LINK_MAX_AGE_DAYS == 60
    assert constants.KEY_COOKIE_MAX_AGE_SEC == 60 * 60 * 24 * 90


def test_저장된_만료일은_환경값_수명보다_우선한다(monkeypatch, conn):
    """연장한 링크는 전역 수명 설정이 짧아도 그 날까지 열린다."""

    monkeypatch.setenv(constants.ENV_LINK_MAX_AGE_DAYS, "1")
    assert share_store.insert_new(
        conn, key=_열쇠, company="카카오", job="", now_iso=_발급
    )
    assert share_store.set_expires_at(
        conn, key_hash=share_store.key_hash_of(_열쇠), expires_at="2026-12-25"
    )

    링크 = share_store.load(conn, _열쇠)
    assert not share_logic.link_expired(링크, today=dt.date(2026, 12, 24))
    assert share_logic.link_expired(링크, today=dt.date(2026, 12, 25))


# ══════════════════════════════════════════════════════════
# ③ 만료된 링크는 문도 돈도 닫힌다
# ══════════════════════════════════════════════════════════


def test_저장된_만료일이_지나면_접속기록도_새조사도_거절한다(conn):
    assert share_store.insert_new(
        conn, key=_열쇠, company="카카오", job="", now_iso=_발급
    )
    assert share_store.set_expires_at(
        conn, key_hash=share_store.key_hash_of(_열쇠), expires_at="2026-09-10"
    )

    assert not share_store.mark_opened(conn, _열쇠, "2026-09-10T00:01:00+09:00")
    assert not share_store.start_run(
        conn,
        key=_열쇠,
        run_id="run-expired",
        started_at="2026-09-10T00:01:00+09:00",
        input_company="카카오",
        confirmed_company="카카오",
        company_id="corp-1",
    )
    assert share_store.mark_opened(conn, _열쇠, "2026-09-09T23:59:00+09:00")


# ══════════════════════════════════════════════════════════
# ④ 표시용 라벨
# ══════════════════════════════════════════════════════════


def test_audience_label은_발급할_때_저장되고_기본은_빈값이다(conn):
    assert share_store.insert_new(
        conn,
        key=_열쇠,
        company="하이브",
        job="",
        audience_label="하이브 인사팀",
        now_iso=_발급,
    )
    assert share_store.insert_new(
        conn, key=_옛열쇠, company="카카오", job="", now_iso=_발급
    )

    assert share_store.load(conn, _열쇠).audience_label == "하이브 인사팀"
    assert share_store.load(conn, _옛열쇠).audience_label == ""


# ══════════════════════════════════════════════════════════
# ⑤ 변경 이력표
# ══════════════════════════════════════════════════════════


def test_이력표에_열쇠_원문이_없다(conn):
    """★ 이력은 «지문»만 남긴다 — 원문이 남으면 표 하나가 유출 경로가 된다."""

    assert share_store.insert_new(
        conn, key=_열쇠, company="카카오", job="", now_iso=_발급
    )
    key_hash = share_store.key_hash_of(_열쇠)
    share_store.record_link_adjustment(
        conn,
        key_hash=key_hash,
        kind=share_store.ADJUSTMENT_KIND_EXPIRES,
        old_value="2026-12-01",
        new_value="2026-12-31",
        reason="채용 일정이 밀렸습니다",
        actor_id="actor-1",
        created_at=_발급,
    )

    행들 = conn.execute(
        f"SELECT * FROM {share_store.TABLE_BUDGET_ADJUSTMENTS}"
    ).fetchall()
    assert len(행들) == 1
    적힌글 = " ".join(str(값) for 값 in 행들[0])
    assert _열쇠 not in 적힌글
    assert key_hash in 적힌글

    이력 = share_store.list_link_adjustments(conn, key_hash=key_hash)
    assert [(항목.kind, 항목.old_value, 항목.new_value) for 항목 in 이력] == [
        ("expires", "2026-12-01", "2026-12-31")
    ]


def test_이력표는_고치거나_지울_수_없다(conn):
    assert share_store.insert_new(
        conn, key=_열쇠, company="카카오", job="", now_iso=_발급
    )
    key_hash = share_store.key_hash_of(_열쇠)
    share_store.record_link_adjustment(
        conn,
        key_hash=key_hash,
        kind=share_store.ADJUSTMENT_KIND_EXPIRES,
        old_value="2026-12-01",
        new_value="2026-12-31",
        reason="연장",
        actor_id="actor-1",
        created_at=_발급,
    )

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            f"UPDATE {share_store.TABLE_BUDGET_ADJUSTMENTS} SET reason = 'x'"
        )
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(f"DELETE FROM {share_store.TABLE_BUDGET_ADJUSTMENTS}")


def test_모르는_변경_종류는_이력에_남지_않는다(conn):
    assert share_store.insert_new(
        conn, key=_열쇠, company="카카오", job="", now_iso=_발급
    )
    with pytest.raises(sqlite3.IntegrityError):
        share_store.record_link_adjustment(
            conn,
            key_hash=share_store.key_hash_of(_열쇠),
            kind="무엇이든",
            old_value="1",
            new_value="2",
            reason="이유",
            actor_id="actor-1",
            created_at=_발급,
        )


# ══════════════════════════════════════════════════════════
# ⑥ 깨진 만료일은 열지 않는다
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize("깨진값", ["2026-13-45", "20261225", "곧", "2026-12"])
def test_읽을수없는_만료일은_기본수명으로_되돌아가지_않고_닫는다(conn, 깨진값):
    """★ 되돌아가면 표가 깨진 것만으로 옛 링크가 30일 더 열린다.

    「못 읽었다」와 「아직 안 정했다」는 다른 값이다. 빈 값만 기본 수명을 쓰고,
    적혀 있는데 못 읽는 값은 닫는 쪽으로 간다.
    """

    assert share_store.insert_new(
        conn, key=_열쇠, company="카카오", job="", now_iso=_발급
    )
    conn.execute(
        f"UPDATE {share_store.TABLE_SHARE_LINKS} SET expires_at = ? "
        "WHERE key_hash = ?",
        (깨진값, share_store.key_hash_of(_열쇠)),
    )

    링크 = share_store.load(conn, _열쇠)
    assert share_logic.link_expired(링크, today=_발급일)
    assert share_logic.expiry_date_of(_발급, expires_at=깨진값) is None
    assert not share_store.mark_opened(conn, _열쇠, _발급)
