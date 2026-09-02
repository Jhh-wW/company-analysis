"""성공 보고서 건수 상한이 «회원마다» 다를 수 있다 (결정 D-G4 (a), 티켓 G-S5).

★ 이 시험이 막는 것 두 가지
  1. 한 친구의 한도만 올렸는데 실제 예약 자리(`reserve_member_run`)는 옛 상수 3을
     그대로 봐서, 화면은 「5건까지」인데 4번째부터 거절되는 것.
  2. 한 친구의 한도를 1로 낮췄는데 예약 자리는 3을 봐서, 사전 확인만 통과하면
     하루 3건이 나가는 것. **예약 자리가 정본**이다 — 사전 확인은 transaction
     밖이라 동시 요청을 못 막는다.

★ 기본값은 리터럴로 단정한다 — 생산 상수를 가져와 자기 자신과 비교하면 값이
  내려가는 회귀를 못 잡는다(순환 검증). 지금 계약은 「하루 성공 3건」이다.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager

import pytest

from src.features.admin_dashboard import store
from src.features.sharelink import allowlist as share_allow
from src.features.storage import db

_친구 = "friend@example.com"
_다른친구 = "other@example.com"
_오늘 = "2026-09-02"
_내일 = "2026-09-03"
_시각 = "2026-09-02T10:00:00+09:00"


@contextmanager
def _열린표(tmp_path, name: str = "dashboard.db") -> Iterator[sqlite3.Connection]:
    """대시보드 표와 초대 명단이 같은 DB에 있는 실제 배치를 그대로 만든다."""
    with db.connect(tmp_path / name) as conn:
        share_allow.ensure_schema(conn)
        yield conn


def _초대한다(conn: sqlite3.Connection, email: str) -> None:
    assert share_allow.invite(conn, email=email, note="", now_iso=_시각)


def _한도를_정한다(
    conn: sqlite3.Connection, email: str, *, 건수: int | None, 금액: float | None
) -> None:
    assert share_allow.set_limits(
        conn,
        email=email,
        daily_success_limit=건수,
        daily_budget_krw=금액,
        reason="시험",
        now_iso=_시각,
    )


def _예약한다(conn: sqlite3.Connection, run_id: str, email: str, day: str = _오늘) -> bool:
    return store.reserve_member_run(
        conn, run_id=run_id, actor_email=email, day=day, now_iso=_시각
    )


# ══════════════════════════════════════════════════════════
# ① 한도가 비어 있으면 지금 계약(3건)을 그대로 쓴다
# ══════════════════════════════════════════════════════════


def test_한도_NULL은_상수3을_쓴다(tmp_path):
    """★ 열만 더하고 값을 안 채운 기존 친구가 갑자기 0건이 되면 안 된다."""
    with _열린표(tmp_path) as conn:
        _초대한다(conn, _친구)

        assert store.member_success_limit(conn, actor_email=_친구) == 3
        for number in range(3):
            assert _예약한다(conn, f"run-{number}", _친구)
        assert not _예약한다(conn, "run-four", _친구)


def test_명단에_아예_없는_이메일도_상수3을_쓴다(tmp_path):
    """명단을 못 찾았다고 상한을 «푸는» 쪽으로 떨어지면 안 된다."""
    with _열린표(tmp_path) as conn:
        assert store.member_success_limit(conn, actor_email="ghost@example.com") == 3


def test_초대_명단_표가_없어도_상수3으로_동작한다(tmp_path):
    """대시보드 표만 있는 옛 DB에서도 예약이 «열리지도 막히지도» 않아야 한다."""
    with db.connect(tmp_path / "only-dashboard.db") as conn:
        conn.execute(f"DROP TABLE IF EXISTS {share_allow.TABLE_ALLOWED_USERS}")

        assert store.member_success_limit(conn, actor_email=_친구) == 3
        for number in range(3):
            assert _예약한다(conn, f"run-{number}", _친구)
        assert not _예약한다(conn, "run-four", _친구)


# ══════════════════════════════════════════════════════════
# ② 회원값이 있으면 예약 자리가 그 값을 본다
# ══════════════════════════════════════════════════════════


def test_한도를_올린_친구는_옛_상수3에서_막히지_않는다(tmp_path):
    with _열린표(tmp_path) as conn:
        _초대한다(conn, _친구)
        _한도를_정한다(conn, _친구, 건수=5, 금액=None)

        assert store.member_success_limit(conn, actor_email=_친구) == 5
        for number in range(5):
            assert _예약한다(conn, f"run-{number}", _친구)
        assert not _예약한다(conn, "run-six", _친구)


def test_한도를_낮춘_친구는_예약_자리에서_먼저_막힌다(tmp_path):
    """★ 사전 확인이 아니라 «예약 자리»가 막아야 동시 요청도 함께 닫힌다."""
    with _열린표(tmp_path) as conn:
        _초대한다(conn, _친구)
        _한도를_정한다(conn, _친구, 건수=1, 금액=None)

        assert _예약한다(conn, "run-0", _친구)
        assert not _예약한다(conn, "run-1", _친구)
        assert store.member_usage_today(conn, actor_email=_친구, day=_오늘) == (0, 1)


def test_다른_회원의_한도는_다른_회원에_영향_없다(tmp_path):
    with _열린표(tmp_path) as conn:
        _초대한다(conn, _친구)
        _초대한다(conn, _다른친구)
        _한도를_정한다(conn, _친구, 건수=1, 금액=None)

        assert store.member_success_limit(conn, actor_email=_친구) == 1
        assert store.member_success_limit(conn, actor_email=_다른친구) == 3

        assert _예약한다(conn, "낮은-0", _친구)
        assert not _예약한다(conn, "낮은-1", _친구)
        for number in range(3):
            assert _예약한다(conn, f"보통-{number}", _다른친구)
        assert not _예약한다(conn, "보통-3", _다른친구)


def test_기본한도_변경은_다음날도_유지된다(tmp_path):
    """★ 영구 값이다 — 자정이 지났다고 3건으로 되돌아가면 D-G4 (a)가 아니다."""
    with _열린표(tmp_path) as conn:
        _초대한다(conn, _친구)
        _한도를_정한다(conn, _친구, 건수=5, 금액=4000.0)

        for number in range(5):
            assert _예약한다(conn, f"오늘-{number}", _친구)
        assert not _예약한다(conn, "오늘-5", _친구)

        for number in range(5):
            assert _예약한다(conn, f"내일-{number}", _친구, day=_내일)
        assert not _예약한다(conn, "내일-5", _친구, day=_내일)

        남은 = share_allow.load(conn, _친구)
        assert 남은 is not None
        assert 남은.daily_success_limit == 5
        assert 남은.daily_budget_krw == 4000.0


# ══════════════════════════════════════════════════════════
# ③ 부르는 쪽이 값을 직접 줄 수도 있다 (명시값이 이긴다)
# ══════════════════════════════════════════════════════════


def test_부르는_쪽이_준_한도가_표의_값보다_우선한다(tmp_path):
    with _열린표(tmp_path) as conn:
        _초대한다(conn, _친구)
        _한도를_정한다(conn, _친구, 건수=5, 금액=None)

        assert store.member_can_start(
            conn, actor_email=_친구, day=_오늘, success_limit=1
        )
        assert _예약한다(conn, "run-0", _친구)
        assert not store.member_can_start(
            conn, actor_email=_친구, day=_오늘, success_limit=1
        )
        assert store.member_can_start(conn, actor_email=_친구, day=_오늘)


@pytest.mark.parametrize("한도", [1, 2, 3, 4, 5])
def test_예약은_주어진_한도의_경계에서_정확히_끊긴다(tmp_path, 한도: int):
    """N번째까지 되고 N+1번째가 안 된다 — 경계 자체를 본다."""
    with _열린표(tmp_path, name=f"boundary-{한도}.db") as conn:
        _초대한다(conn, _친구)
        _한도를_정한다(conn, _친구, 건수=한도, 금액=None)

        for number in range(한도):
            assert _예약한다(conn, f"run-{number}", _친구)
        assert not _예약한다(conn, f"run-{한도}", _친구)


def test_실패로_반환한_예약은_회원별_한도에서도_다시_쓸_수_있다(tmp_path):
    with _열린표(tmp_path) as conn:
        _초대한다(conn, _친구)
        _한도를_정한다(conn, _친구, 건수=2, 금액=None)

        assert _예약한다(conn, "run-0", _친구)
        assert _예약한다(conn, "run-1", _친구)
        assert not _예약한다(conn, "run-2", _친구)

        assert store.settle_member_run(
            conn, run_id="run-1", succeeded=False, report_id="",
            now_iso="2026-09-02T10:01:00+09:00",
        )

        assert _예약한다(conn, "run-2", _친구)
        assert not _예약한다(conn, "run-3", _친구)
