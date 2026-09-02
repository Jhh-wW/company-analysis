"""친구(MEMBER)마다 하루 한도를 «따로» 준다.

★ 이 시험이 막는 것 — 「저 친구 한 명만 더 쓰게 해 달라」를 상수 하나로 고쳐서
  **명단 전원의 하루 상한이 같이 올라가는 것.** 비용 노출은 인원 수만큼 곱해지므로
  (`allowlist.list_all` 주석), 한 명을 위한 상수 변경은 전원에게 청구된다.

★ 한도가 비어 있으면(NULL) «기본값을 쓰라»는 뜻이다. 0이 아니다 —
  0으로 읽으면 아무도 아무것도 못 쓰게 된다.

★ 기본값을 여기서 해석하지 않는 이유 — 성공 건수 기본값은 `admin_dashboard`가,
  비용 기본값은 `sharelink/constants.py`가 각각 정본이다. 두 feature의 상수를
  이 파일이 가져와 해석하면 정의가 두 곳이 된다(P-83과 같은 함정).
  이 표는 «관리자가 덮어쓴 값이 있나 없나»만 기억한다.
"""

from __future__ import annotations

import sqlite3

import pytest

from src.features.sharelink import allowlist as share_allow
from src.features.sharelink.tracks import Track, budget_of

_초대일 = "2026-09-02T10:00:00+09:00"
_친구 = "friend@example.com"
_다른친구 = "other@example.com"


def _새표() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    share_allow.ensure_schema(conn)
    return conn


def _열이름(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[1])
        for row in conn.execute(
            f"PRAGMA table_info({share_allow.TABLE_ALLOWED_USERS})"
        )
    }


# ══════════════════════════════════════════════════════════
# ① 표 모양 — 열 2개가 «비어 있을 수 있게» 생긴다
# ══════════════════════════════════════════════════════════


def test_초대_명단에_회원별_한도_열_두_개가_있다():
    with _새표() as conn:
        assert {"daily_success_limit", "daily_budget_krw"} <= _열이름(conn)


def test_옛_표에도_한도_열을_멱등으로_더하고_기존_행을_지우지_않는다():
    """운영 DB에는 이미 사람이 들어 있다. 열을 더하며 행을 잃으면 초대가 사라진다."""
    conn = sqlite3.connect(":memory:")
    conn.execute(
        f"""CREATE TABLE {share_allow.TABLE_ALLOWED_USERS} (
            email TEXT PRIMARY KEY,
            display_name TEXT NOT NULL DEFAULT '',
            note TEXT NOT NULL DEFAULT '',
            invited_at TEXT NOT NULL,
            is_active INTEGER NOT NULL DEFAULT 1,
            revoked_at TEXT NOT NULL DEFAULT ''
        )"""
    )
    conn.execute(
        f"INSERT INTO {share_allow.TABLE_ALLOWED_USERS} "
        "(email, display_name, note, invited_at, is_active, revoked_at) "
        "VALUES (?, '김민지', '스터디', ?, 1, '')",
        (_친구, _초대일),
    )

    with conn:
        share_allow.ensure_schema(conn)
        share_allow.ensure_schema(conn)  # 두 번 불러도 같은 결과여야 한다

        assert {"daily_success_limit", "daily_budget_krw"} <= _열이름(conn)
        살아남은 = share_allow.load(conn, _친구)
        assert 살아남은 is not None
        assert 살아남은.display_name == "김민지"
        assert 살아남은.daily_success_limit is None
        assert 살아남은.daily_budget_krw is None


def test_한도를_한_번도_안_바꾼_친구는_두_값이_모두_비어_있다():
    """비어 있음 = 「기본값을 쓰라」. 0이 아니다."""
    with _새표() as conn:
        assert share_allow.invite(conn, email=_친구, note="", now_iso=_초대일)
        초대된 = share_allow.load(conn, _친구)

        assert 초대된 is not None
        assert 초대된.daily_success_limit is None
        assert 초대된.daily_budget_krw is None
        assert 초대된.limit_reason == ""


# ══════════════════════════════════════════════════════════
# ② 한도 바꾸기 — 영구 값이고, 이유가 함께 남는다
# ══════════════════════════════════════════════════════════


def test_한도를_바꾸면_그_값과_이유가_그대로_남는다():
    with _새표() as conn:
        assert share_allow.invite(conn, email=_친구, note="", now_iso=_초대일)

        assert share_allow.set_limits(
            conn,
            email=_친구,
            daily_success_limit=7,
            daily_budget_krw=4500.0,
            reason="면접 준비 기간",
            now_iso="2026-09-02T11:00:00+09:00",
        )
        바뀐 = share_allow.load(conn, _친구)

        assert 바뀐 is not None
        assert 바뀐.daily_success_limit == 7
        assert 바뀐.daily_budget_krw == 4500.0
        assert 바뀐.limit_reason == "면접 준비 기간"
        assert 바뀐.limit_updated_at == "2026-09-02T11:00:00+09:00"


def test_한도를_비우면_다시_기본값을_쓰라는_뜻으로_돌아간다():
    with _새표() as conn:
        assert share_allow.invite(conn, email=_친구, note="", now_iso=_초대일)
        assert share_allow.set_limits(
            conn, email=_친구, daily_success_limit=7, daily_budget_krw=4500.0,
            reason="면접 준비 기간", now_iso="2026-09-02T11:00:00+09:00",
        )

        assert share_allow.set_limits(
            conn, email=_친구, daily_success_limit=None, daily_budget_krw=None,
            reason="기간 끝", now_iso="2026-09-03T11:00:00+09:00",
        )
        되돌린 = share_allow.load(conn, _친구)

        assert 되돌린 is not None
        assert 되돌린.daily_success_limit is None
        assert 되돌린.daily_budget_krw is None


def test_다른_회원의_한도는_다른_회원에_영향_없다():
    """★ 이 기능의 핵심. 한 명을 올려도 옆 사람은 그대로여야 한다."""
    with _새표() as conn:
        assert share_allow.invite(conn, email=_친구, note="", now_iso=_초대일)
        assert share_allow.invite(conn, email=_다른친구, note="", now_iso=_초대일)

        assert share_allow.set_limits(
            conn, email=_친구, daily_success_limit=9, daily_budget_krw=9000.0,
            reason="시연", now_iso="2026-09-02T11:00:00+09:00",
        )

        올린쪽 = share_allow.load(conn, _친구)
        안건드린쪽 = share_allow.load(conn, _다른친구)
        assert 올린쪽 is not None and 안건드린쪽 is not None
        assert 올린쪽.daily_success_limit == 9
        assert 올린쪽.daily_budget_krw == 9000.0
        assert 안건드린쪽.daily_success_limit is None
        assert 안건드린쪽.daily_budget_krw is None


def test_뺐다가_다시_초대하면_한도는_기본값으로_돌아간다():
    """★ 올려 둔 몫이 조용히 따라오면, 다시 넣은 사람은 기본 한도인 줄 안다."""
    with _새표() as conn:
        assert share_allow.invite(conn, email=_친구, note="", now_iso=_초대일)
        assert share_allow.set_limits(
            conn, email=_친구, daily_success_limit=15, daily_budget_krw=15_000.0,
            reason="시연 주간", now_iso="2026-09-02T11:00:00+09:00",
        )
        assert share_allow.revoke(conn, _친구, now_iso="2026-09-05T11:00:00+09:00")

        assert share_allow.invite(
            conn, email=_친구, note="다시 초대", now_iso="2026-09-10T10:00:00+09:00"
        )
        다시초대된 = share_allow.load(conn, _친구)

        assert 다시초대된 is not None
        assert 다시초대된.daily_success_limit is None
        assert 다시초대된.daily_budget_krw is None
        assert 다시초대된.limit_reason == ""


def test_명단에_없거나_철회된_사람의_한도는_바꿀_수_없다():
    """철회한 사람에게 한도를 붙이면 「빼 놨는데 몫이 살아 있는」 상태가 된다."""
    with _새표() as conn:
        assert not share_allow.set_limits(
            conn, email="nobody@example.com", daily_success_limit=5,
            daily_budget_krw=1000.0, reason="없는 사람",
            now_iso="2026-09-02T11:00:00+09:00",
        )

        assert share_allow.invite(conn, email=_친구, note="", now_iso=_초대일)
        assert share_allow.revoke(conn, _친구, now_iso="2026-09-02T10:30:00+09:00")
        assert not share_allow.set_limits(
            conn, email=_친구, daily_success_limit=5, daily_budget_krw=1000.0,
            reason="뺀 사람", now_iso="2026-09-02T11:00:00+09:00",
        )


# ══════════════════════════════════════════════════════════
# ③ 입력 범위 — 화면이 아니라 저장 직전에서 막는다
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize("건수", [0, -1, 21, 1000])
def test_성공_건수_한도가_1에서_20_밖이면_거절한다(건수: int):
    with _새표() as conn:
        assert share_allow.invite(conn, email=_친구, note="", now_iso=_초대일)
        with pytest.raises(ValueError):
            share_allow.set_limits(
                conn, email=_친구, daily_success_limit=건수,
                daily_budget_krw=1000.0, reason="범위 밖",
                now_iso="2026-09-02T11:00:00+09:00",
            )


@pytest.mark.parametrize("건수", [1, 20])
def test_성공_건수_한도의_경계값은_받아들인다(건수: int):
    with _새표() as conn:
        assert share_allow.invite(conn, email=_친구, note="", now_iso=_초대일)
        assert share_allow.set_limits(
            conn, email=_친구, daily_success_limit=건수, daily_budget_krw=1000.0,
            reason="경계", now_iso="2026-09-02T11:00:00+09:00",
        )
        저장된 = share_allow.load(conn, _친구)
        assert 저장된 is not None and 저장된.daily_success_limit == 건수


@pytest.mark.parametrize("금액", [-1.0, 20_000.5, 100_000.0])
def test_하루_비용_한도가_0에서_20000원_밖이면_거절한다(금액: float):
    with _새표() as conn:
        assert share_allow.invite(conn, email=_친구, note="", now_iso=_초대일)
        with pytest.raises(ValueError):
            share_allow.set_limits(
                conn, email=_친구, daily_success_limit=3, daily_budget_krw=금액,
                reason="범위 밖", now_iso="2026-09-02T11:00:00+09:00",
            )


@pytest.mark.parametrize("금액", [0.0, 20_000.0])
def test_하루_비용_한도의_경계값은_받아들인다(금액: float):
    with _새표() as conn:
        assert share_allow.invite(conn, email=_친구, note="", now_iso=_초대일)
        assert share_allow.set_limits(
            conn, email=_친구, daily_success_limit=3, daily_budget_krw=금액,
            reason="경계", now_iso="2026-09-02T11:00:00+09:00",
        )
        저장된 = share_allow.load(conn, _친구)
        assert 저장된 is not None and 저장된.daily_budget_krw == 금액


def test_이유_없이는_한도를_못_바꾼다():
    """왜 올렸는지 없으면 나중에 「이거 왜 5건이지」를 아무도 답할 수 없다."""
    with _새표() as conn:
        assert share_allow.invite(conn, email=_친구, note="", now_iso=_초대일)
        with pytest.raises(ValueError):
            share_allow.set_limits(
                conn, email=_친구, daily_success_limit=5, daily_budget_krw=1000.0,
                reason="   ", now_iso="2026-09-02T11:00:00+09:00",
            )


# ══════════════════════════════════════════════════════════
# ④ 비용 상한 — MEMBER 갈래만 회원값으로 덮인다
# ══════════════════════════════════════════════════════════


def test_회원_비용_상한은_회원값이_있으면_그_값을_쓴다():
    assert budget_of(Track.MEMBER, member_daily_budget_krw=900.0) == 900.0


def test_회원_비용_상한이_비어_있으면_기존_3000원을_쓴다():
    assert budget_of(Track.MEMBER) == 3000.0
    assert budget_of(Track.MEMBER, member_daily_budget_krw=None) == 3000.0


@pytest.mark.parametrize(
    ("갈래", "기존값"),
    [(Track.ADMIN, 5000.0), (Track.LINK, 3000.0), (Track.PUBLIC, 0.0)],
)
def test_회원값은_다른_갈래의_상한을_건드리지_않는다(갈래: Track, 기존값: float):
    """★ 반대 경우 시험 — 회원 한도 인자를 줘도 LINK·ADMIN·PUBLIC은 그대로다."""
    assert budget_of(갈래, member_daily_budget_krw=900.0) == 기존값
    assert budget_of(갈래) == 기존값
