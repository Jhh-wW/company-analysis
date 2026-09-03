"""초대 링크의 «수명 전체 누적 상한»을 못 박는다.

★ 이 시험이 지키는 것 — **링크 하나가 평생 쓸 수 있는 돈에 천장이 있다.**
  기존 「하루 3,000원」은 하루가 지나면 되살아나므로, 60일짜리 링크 하나의
  최악 노출은 3,000 × 60 = 18만 원이었다. 누적 상한은 그 곱셈을 끊는다.

⚠️ 여기 적힌 금액은 **리터럴**이다. 생산 상수를 import해 같은 상수와 비교하면
  값이 몰래 낮아져도 시험이 그대로 통과한다 — 그건 검증이 아니라 순환이다.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator

import pytest

from src.features.budget import spend_store as budget_spend_store
from src.features.sharelink import constants
from src.features.sharelink import logic as share_logic
from src.features.sharelink import store as share_store

_열쇠 = "aa11bb22cc33dd44aa11bb22cc33dd44"
_다른열쇠 = "99887766554433229988776655443322"
_시각 = "2026-09-02T09:00:00+09:00"


@pytest.fixture
def conn() -> Iterator[sqlite3.Connection]:
    connection = sqlite3.connect(":memory:")
    share_store.ensure_schema(connection)
    budget_spend_store.ensure_schema(connection)
    try:
        yield connection
    finally:
        connection.close()


def _링크를_만든다(
    conn: sqlite3.Connection,
    *,
    key: str = _열쇠,
    report_id: str = "",
) -> str:
    assert share_store.insert_new(
        conn,
        key=key,
        company="카카오",
        job="백엔드 개발",
        report_id=report_id,
        note="지원 링크",
        now_iso=_시각,
    )
    return share_store.key_hash_of(key)


def _끝난_조사를_넣는다(
    conn: sqlite3.Connection,
    *,
    key: str,
    run_id: str,
    원가: float,
) -> None:
    """실측 원가가 확정된 종결 실행 한 건."""
    assert share_store.start_run(
        conn,
        key=key,
        run_id=run_id,
        started_at=_시각,
        input_company="카카오",
        confirmed_company="카카오",
        company_id="corp-1",
    )
    assert share_store.finish_run(
        conn,
        run_id=run_id,
        status=share_store.RUN_STATUS_AWAITING_RELEASE,
        finished_at=_시각,
        report_id=run_id,
        internal_ai_cost_krw=원가,
    )


def _예약중인_조사를_넣는다(
    conn: sqlite3.Connection,
    *,
    key: str,
    run_id: str,
    예약액: float,
    state: str = "ACTIVE",
) -> None:
    """아직 안 끝난 실행 하나와 그 실행이 잡아 둔 예약 원장 행."""
    assert share_store.start_run(
        conn,
        key=key,
        run_id=run_id,
        started_at=_시각,
        input_company="네이버",
        confirmed_company="네이버",
        company_id="corp-2",
    )
    활성 = state == "ACTIVE"
    conn.execute(
        """
        INSERT INTO budget_phase_accounts (
            run_id, phase, day, bucket_id, state, reservation_krw,
            lease_owner_id, lease_expires_at, started_at, updated_at, version
        )
        VALUES (?, 'report', '2026-09-02', 'bucket-1', ?, ?, ?, ?, ?, ?, 0)
        """,
        (
            run_id,
            state,
            예약액 if 활성 else 0.0,
            "owner-1" if 활성 else None,
            "2026-09-02T10:00:00+09:00" if 활성 else None,
            _시각,
            _시각,
        ),
    )


# ══════════════════════════════════════════════════════════
# ① 리터럴 캐너리 — 값이 몰래 바뀌면 여기가 먼저 깨진다
# ══════════════════════════════════════════════════════════


def test_LINK_누적_예산은_리터럴_3000원이다() -> None:
    """★ 상수끼리 비교하면 값이 바뀌어도 안 깨진다. 그래서 리터럴로 못 박는다."""
    assert constants.LINK_TOTAL_BUDGET_KRW == 3000.0


def test_소진_문구는_정확히_이_문장이다() -> None:
    """★ 정해진 문장 그대로여야 한다."""
    assert constants.LINK_TOTAL_BUDGET_EXHAUSTED_MESSAGE == (
        "이 링크의 이용 한도를 모두 사용했습니다. "
        "미리 준비된 회사 보고서는 계속 볼 수 있습니다."
    )


def test_소진_문구는_우리_내_같은_사정을_말하지_않는다() -> None:
    """★ 화면 문구에 만든 쪽 사정을 넣지 않는다 (제품 결정)."""
    문구 = constants.LINK_TOTAL_BUDGET_EXHAUSTED_MESSAGE
    assert "우리" not in 문구
    assert "내" not in 문구


def test_소진_문구는_내부_용어를_노출하지_않는다() -> None:
    """★ LINK·bucket·KRW 같은 코드 용어는 손님이 알 필요가 없다."""
    문구 = constants.LINK_TOTAL_BUDGET_EXHAUSTED_MESSAGE
    for 금지어 in ("LINK", "bucket", "KRW", "budget", "cap"):
        assert 금지어 not in 문구


def test_하루_상한은_그대로_리터럴_3000원이다() -> None:
    """★ 누적 상한을 넣느라 하루 상한을 건드리지 않았다는 반대 경우 시험."""
    assert constants.PER_LINK_DAILY_BUDGET_KRW == 3000.0


# ══════════════════════════════════════════════════════════
# ② 링크별 누적 상한값 — 기존 행은 NULL이다
# ══════════════════════════════════════════════════════════


def test_기존행은_NULL이면_기본_3000을_쓴다(conn: sqlite3.Connection) -> None:
    """★ 이미 뿌린 링크에 값을 채워 넣지 않는다. 비어 있으면 기본값이다."""
    _링크를_만든다(conn)

    링크 = share_store.load(conn, _열쇠)

    assert 링크 is not None
    assert 링크.total_budget_krw is None
    assert 링크.effective_total_budget_krw == 3000.0


def test_누적상한_열은_기존_스키마에도_멱등으로_붙는다() -> None:
    """★ 열이 없던 옛 DB를 열어도 마이그레이션이 값을 잃지 않는다."""
    옛DB = sqlite3.connect(":memory:")
    try:
        옛DB.execute(
            """
            CREATE TABLE share_links (
                key_hash        TEXT PRIMARY KEY,
                company         TEXT NOT NULL,
                job             TEXT NOT NULL,
                report_id       TEXT NOT NULL DEFAULT '',
                note            TEXT NOT NULL DEFAULT '',
                created_at      TEXT NOT NULL,
                opened_count    INTEGER NOT NULL DEFAULT 0,
                first_opened_at TEXT NOT NULL DEFAULT '',
                last_opened_at  TEXT NOT NULL DEFAULT '',
                revoked_at      TEXT NOT NULL DEFAULT ''
            )
            """
        )
        옛DB.execute(
            """
            INSERT INTO share_links
                (key_hash, company, job, report_id, note, created_at)
            VALUES (?, '카카오', '백엔드 개발', 'r1', '', ?)
            """,
            (share_store.key_hash_of(_열쇠), _시각),
        )
        share_store.ensure_schema(옛DB)
        # 같은 DB에 두 번 걸어도 깨지지 않는다.
        share_store.ensure_schema(옛DB)

        링크 = share_store.load(옛DB, _열쇠)

        assert 링크 is not None
        assert 링크.report_id == "r1"
        assert 링크.total_budget_krw is None
        assert 링크.effective_total_budget_krw == 3000.0
    finally:
        옛DB.close()


def test_링크에_직접_적어_둔_누적상한이_기본값을_이긴다(
    conn: sqlite3.Connection,
) -> None:
    """★ 나중에 링크별로 조정할 자리를 지금 열어 둔다 (조각 S4)."""
    key_hash = _링크를_만든다(conn)
    conn.execute(
        "UPDATE share_links SET total_budget_krw = 1500 WHERE key_hash = ?",
        (key_hash,),
    )

    링크 = share_store.load(conn, _열쇠)

    assert 링크 is not None
    assert 링크.total_budget_krw == 1500.0
    assert 링크.effective_total_budget_krw == 1500.0


def test_음수_누적상한은_표가_먼저_막는다(conn: sqlite3.Connection) -> None:
    """★ 깨진 금액이 저장되는 길 자체를 DB가 닫는다."""
    key_hash = _링크를_만든다(conn)

    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "UPDATE share_links SET total_budget_krw = -1 WHERE key_hash = ?",
            (key_hash,),
        )


def test_그래도_음수가_들어오면_링크를_열지_않고_닫는다() -> None:
    """★ 표를 통과해 버린 깨진 값은 기본값으로 «되살리지» 않는다.

    NULL과 음수는 다른 사건이다. NULL은 「아직 정한 적 없다」라서 기본값이 맞고,
    음수는 「저장이 깨졌다」라서 돈을 더 쓰면 안 된다.
    """
    깨진_링크 = share_store.ShareLink(
        key_hash="0" * 64,
        company="카카오",
        job="백엔드 개발",
        report_id="",
        note="",
        created_at=_시각,
        opened_count=0,
        first_opened_at="",
        last_opened_at="",
        revoked_at="",
        total_budget_krw=-1.0,
    )

    assert 깨진_링크.effective_total_budget_krw == 0.0
    assert not share_logic.can_start_within_total_budget(
        0.0, 깨진_링크.effective_total_budget_krw
    )


# ══════════════════════════════════════════════════════════
# ③ 누적 사용액 = 종결 실행의 실측 원가 + 진행 중 예약
# ══════════════════════════════════════════════════════════


def test_누적은_그_링크의_종결된_실행_원가를_모두_더한다(
    conn: sqlite3.Connection,
) -> None:
    _링크를_만든다(conn)
    _끝난_조사를_넣는다(conn, key=_열쇠, run_id="run-1", 원가=900.0)
    _끝난_조사를_넣는다(conn, key=_열쇠, run_id="run-2", 원가=1100.0)

    assert share_store.link_total_spent_krw(
        conn, key_hash=share_store.key_hash_of(_열쇠)
    ) == 2000.0


def test_누적검사는_진행중_예약을_포함한다(conn: sqlite3.Connection) -> None:
    """★ 이게 없으면 900원짜리 조사가 도는 «동안» 새 조사가 계속 들어온다.

    진행 중 실행은 아직 실측 원가가 0으로 남아 있다. 예약액을 안 세면
    상한 직전에서 여러 건이 동시에 통과해 천장을 넘어 버린다.
    """
    _링크를_만든다(conn)
    _끝난_조사를_넣는다(conn, key=_열쇠, run_id="run-1", 원가=2000.0)
    _예약중인_조사를_넣는다(conn, key=_열쇠, run_id="run-2", 예약액=900.0)

    key_hash = share_store.key_hash_of(_열쇠)

    assert share_store.link_run_cost_sum_krw(conn, key_hash=key_hash) == 2000.0
    assert share_store.link_active_reservation_krw(
        conn, key_hash=key_hash
    ) == 900.0
    assert share_store.link_total_spent_krw(conn, key_hash=key_hash) == 2900.0


def test_끝난_단계의_예약은_누적에_두번_세지_않는다(
    conn: sqlite3.Connection,
) -> None:
    """★ 반대 경우 시험 — 이미 정산된 단계까지 더하면 실제보다 비싸게 막는다."""
    _링크를_만든다(conn)
    _예약중인_조사를_넣는다(
        conn, key=_열쇠, run_id="run-1", 예약액=0.0, state="SUCCEEDED"
    )
    assert share_store.finish_run(
        conn,
        run_id="run-1",
        status=share_store.RUN_STATUS_AWAITING_RELEASE,
        finished_at=_시각,
        report_id="run-1",
        internal_ai_cost_krw=700.0,
    )

    assert share_store.link_total_spent_krw(
        conn, key_hash=share_store.key_hash_of(_열쇠)
    ) == 700.0


def test_다른_링크의_누적은_섞이지_않는다(conn: sqlite3.Connection) -> None:
    """★ 「링크 하나 = 통장 하나」는 누적에서도 그대로다."""
    _링크를_만든다(conn)
    _링크를_만든다(conn, key=_다른열쇠)
    _끝난_조사를_넣는다(conn, key=_열쇠, run_id="run-1", 원가=2500.0)

    assert share_store.link_total_spent_krw(
        conn, key_hash=share_store.key_hash_of(_다른열쇠)
    ) == 0.0


def test_예약원장_표가_아직_없어도_누적을_읽는다() -> None:
    """★ 비용 원장 전환 전 DB에서도 링크 누적 검사가 터지면 안 된다."""
    연결 = sqlite3.connect(":memory:")
    try:
        share_store.ensure_schema(연결)
        # 비용 원장 표를 «만들지 않는다» — cutover 전 DB와 같은 상태다.
        _링크를_만든다(연결)
        _끝난_조사를_넣는다(연결, key=_열쇠, run_id="run-1", 원가=300.0)
        연결.execute("DROP TABLE IF EXISTS budget_phase_accounts")

        assert share_store.link_total_spent_krw(
            연결, key_hash=share_store.key_hash_of(_열쇠)
        ) == 300.0
    finally:
        연결.close()


# ══════════════════════════════════════════════════════════
# ④ 판정 — 경계는 2,999 / 3,000 / 3,001
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    ("누적", "새조사가_되나"),
    [(2999.0, True), (3000.0, False), (3001.0, False)],
)
def test_누적_경계에서_새조사_허용이_갈린다(
    누적: float, 새조사가_되나: bool
) -> None:
    assert (
        share_logic.can_start_within_total_budget(누적, 3000.0) is 새조사가_되나
    )


def test_누적이_상한에_닿으면_남은_돈은_0원이다() -> None:
    assert share_logic.total_budget_left(3000.0, 3000.0) == 0.0
    assert share_logic.total_budget_left(3200.0, 3000.0) == 0.0
    assert share_logic.total_budget_left(1200.0, 3000.0) == 1800.0


def test_하루가_남아도_누적이_차면_새조사를_막는다() -> None:
    """★ 둘 중 하나만 넘어도 막는다 — 이게 「둘 다 적용」의 뜻이다."""
    import datetime as dt  # noqa: PLC0415

    오늘 = dt.date(2026, 9, 2)
    장부 = share_logic.DailySpend(day=오늘)  # 하루 사용액 0원

    assert share_logic.can_start_new_run(장부, _열쇠, 오늘, 3000.0)
    assert not share_logic.can_start_new_run(
        장부, _열쇠, 오늘, 3000.0, total_spent_krw=3000.0, total_cap_krw=3000.0
    )


def test_누적이_남아도_하루가_차면_새조사를_막는다() -> None:
    """★ 반대 방향 시험 — 누적을 넣느라 하루 상한을 무력화하지 않았다."""
    import datetime as dt  # noqa: PLC0415

    오늘 = dt.date(2026, 9, 2)
    장부 = share_logic.add_spend(
        share_logic.DailySpend(day=오늘), _열쇠, 오늘, 3000.0
    )

    assert not share_logic.can_start_new_run(
        장부, _열쇠, 오늘, 3000.0, total_spent_krw=0.0, total_cap_krw=3000.0
    )
