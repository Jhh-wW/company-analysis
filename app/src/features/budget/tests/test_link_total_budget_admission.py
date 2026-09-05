"""링크 누적 상한이 «예약을 커밋하는 그 자리»에서도 지켜지는지 못 박는다 (P1).

★ 왜 사전 검사만으로는 부족한가 — `request_helpers._guard_run`은 요청을 받자마자
  누적을 «한 번 읽고» 통과시킨다. 같은 링크로 세 요청이 거의 동시에 들어오면
  셋 다 같은 옛 숫자를 읽고 셋 다 통과한다. 그 뒤 각자 본조사 예약액을 잡아 버리면
  천장을 훌쩍 넘는다. 실측 재현: 잔여 1원에서 3동시 → 5,699원(초과 2,699원).

★ 하루 상한은 이미 이 문제를 풀어 놨다 — `state_machine.begin_phase`가
  `BEGIN IMMEDIATE` 안에서 다시 확인한다. 누적도 **같은 자리에서 같은 규칙으로**
  확인해야 한다. 이 시험이 그 대칭을 지킨다.
"""

from __future__ import annotations

import datetime as dt
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from src.core import clock
from src.features.budget import spend_store, state_machine
from src.features.budget.constants import (
    MAX_CONCURRENT_PER_LINK,
    PAID_PHASE_PROVIDER_BUDGET_KRW,
    SPEND_PHASE_PIPELINE,
)
from src.features.sharelink import store as share_store
from src.features.storage import db as storage_db
from src.web import paid_runtime

_열쇠 = "c1c2c3d4e5f60718c1c2c3d4e5f60718"
_시각 = "2026-09-02T09:00:00+09:00"

#: ★ 리터럴로 못 박는다 — 생산 상수를 import해 비교하면 값이 낮아져도 통과한다.
_누적상한 = 3000.0
_하루상한 = 3000.0
_본조사예약 = 1000.0

#: ★ 동시성 시험의 스레드 수는 슬롯 상한과 «무관한 리터럴 3»으로 고정한다.
#:   링크 동시 실행 상한이 3→1로 내려가도 «같은 순간에 여러 요청이 몰리는 경쟁»
#:   자체는 그대로 재현해야 한다. 상한을 스레드 수로 쓰면 상한이 내려갈 때 시험이
#:   1스레드가 되어 경쟁이 사라지고, 이 파일이 지키던 P1(누적 초과 예약)이 무방비가 된다.
_동시스레드 = 3

_DAY = dt.date(2026, 9, 2)
_LEASE_EXPIRES_AT = "2026-09-02T10:00:00+09:00"


def test_본조사_예약액_전제가_그대로다() -> None:
    """★ 이 시험의 산수(잔여 1원 vs 1,000원)가 서 있는 바닥을 먼저 확인한다.

    2026-09-05 본조사 예약액을 실측에 따라 1,000원으로 내렸다.

    이 파일의 «동시»는 링크 슬롯 상한이 아니라 `_동시스레드`(리터럴 3)로
    예약 transaction의 경쟁을 직접 재현한다.
    """
    assert PAID_PHASE_PROVIDER_BUDGET_KRW[SPEND_PHASE_PIPELINE] == 1000.0
    assert MAX_CONCURRENT_PER_LINK == 1
    assert _동시스레드 == 3


@pytest.mark.parametrize(("하루상한", "허용건수"), ((3000.0, 3), (5000.0, 5)))
def test_본조사_예약액은_하루상한별_허용건수를_정확히_가른다(
    tmp_path,
    하루상한: float,
    허용건수: int,
) -> None:
    """1,000원 예약은 3,000원에서 3건, 5,000원에서 5건까지 들어간다."""

    conn = _새_원장(tmp_path)
    try:
        for 번호 in range(허용건수):
            state_machine.begin_phase(
                conn,
                run_id=f"daily-fit-{번호}",
                phase=SPEND_PHASE_PIPELINE,
                day=_DAY,
                bucket="user:daily-limit@example.com",
                reservation_krw=_본조사예약,
                bucket_limit_krw=하루상한,
                run_limit_krw=None,
                lease_owner_id=f"worker:{번호}",
                lease_expires_at=_LEASE_EXPIRES_AT,
                started_at=_시각,
            )
        with pytest.raises(state_machine.AdmissionLimitExceeded):
            state_machine.begin_phase(
                conn,
                run_id="daily-over",
                phase=SPEND_PHASE_PIPELINE,
                day=_DAY,
                bucket="user:daily-limit@example.com",
                reservation_krw=_본조사예약,
                bucket_limit_krw=하루상한,
                run_limit_krw=None,
                lease_owner_id="worker:over",
                lease_expires_at=_LEASE_EXPIRES_AT,
                started_at=_시각,
            )
    finally:
        conn.close()


def _링크와_지난_원가를_둔다(원가: float, *, key: str = _열쇠) -> str:
    """이 링크가 이미 «원가»만큼 쓴 상태로 만든다 (종결된 실행 이력)."""
    with storage_db.connect() as conn:
        assert share_store.insert_new(
            conn,
            key=key,
            company="카카오",
            job="마케팅",
            report_id="",
            now_iso=_시각,
        )
        assert share_store.start_run(
            conn,
            key=key,
            run_id="spent-history",
            started_at=_시각,
            input_company="카카오",
            confirmed_company="카카오",
            company_id="corp-1",
        )
        assert share_store.finish_run(
            conn,
            run_id="spent-history",
            status=share_store.RUN_STATUS_AWAITING_RELEASE,
            finished_at=_시각,
            report_id="spent-history",
            internal_ai_cost_krw=원가,
        )
        conn.commit()
    return share_store.key_hash_of(key)


def _cutover() -> None:
    paid_runtime.prepare_budget_state_machine_cutover()
    paid_runtime._seed_ledger()


def _누적() -> float:
    with storage_db.connect() as conn:
        return share_store.link_total_spent_krw(
            conn, key_hash=share_store.key_hash_of(_열쇠)
        )


def _생성이력을_연다(run_id: str, *, key: str = _열쇠) -> None:
    """운영과 같은 순서 — 유료 단계보다 «먼저» LINK 생성 이력을 연다.

    `job_runtime._start_with_reserved_slot`이 provider 호출 전에 `start_run`을
    부른다. 시험도 같은 순서를 지켜야 진짜 상황을 재현한다.
    """
    with storage_db.connect() as conn:
        assert share_store.start_run(
            conn,
            key=key,
            run_id=run_id,
            started_at=_시각,
            input_company="네이버",
            confirmed_company="네이버",
            company_id="corp-2",
        )
        conn.commit()


def _동시에_예약한다(개수: int, *, share_key: str = _열쇠) -> list:
    """Barrier로 «같은 순간»에 예약을 밀어 넣는다."""
    barrier = threading.Barrier(개수)

    def 예약(index: int):
        run_id = f"concurrent-total-{index}"
        _생성이력을_연다(run_id, key=share_key)
        barrier.wait(timeout=10)
        return paid_runtime._begin_paid_phase(
            run_id=run_id,
            phase=SPEND_PHASE_PIPELINE,
            share_key=share_key,
            cap_krw=_하루상한,
        )

    with ThreadPoolExecutor(max_workers=개수) as pool:
        futures = [pool.submit(예약, index) for index in range(개수)]
        return [future.result(timeout=20) for future in futures]


# ══════════════════════════════════════════════════════════
# ① 동시 요청이 천장을 넘지 못한다
# ══════════════════════════════════════════════════════════


def test_같은_링크_동시_요청_3개는_누적_상한을_넘겨_예약하지_못한다() -> None:
    """★ P1 그 자체. 잔여 1원인데 1,000원짜리 세 건이 동시에 들어온다.

    수정 전에는 셋 다 통과해 최종 누적이 5,699원이 됐다 (상한 초과 2,699원).
    """
    _링크와_지난_원가를_둔다(2999.0)
    _cutover()

    표 = _동시에_예약한다(_동시스레드)
    최종누적 = _누적()

    assert 최종누적 <= _누적상한, f"누적 상한을 넘겨 예약됐다: {최종누적}원"
    assert [t for t in 표 if t is not None] == [], "잔여 1원에 1,000원이 들어갔다"
    assert 최종누적 == 2999.0


def test_동시_요청_중_남은_몫에_들어가는_한_건만_예약된다() -> None:
    """★ 반대 방향 시험 — 다 막아 버리면 그냥 고장이다.

    잔여가 정확히 1,000원이면 «한 건»은 들어가야 하고, 나머지 둘은 막혀야 한다.
    """
    _링크와_지난_원가를_둔다(2000.0)
    _cutover()

    표 = _동시에_예약한다(_동시스레드)
    성공 = [t for t in 표 if t is not None]

    assert len(성공) == 1, f"들어간 예약 수가 1이 아니다: {len(성공)}"
    assert _누적() == 3000.0


def test_누적이_비어있으면_정확히_세건이_들어간다() -> None:
    """★ 반대 경우 시험 — 누적 검사가 정상 사용까지 막지는 않는다.

    본조사 예약액 1,000원 세 건은 링크 수명 상한 3,000원을 정확히 채운다.
    슬롯 경계 밖의 동시 예약 transaction도 그 셋을 모두 받아야 한다.
    """
    _링크와_지난_원가를_둔다(0.0)
    _cutover()

    표 = _동시에_예약한다(_동시스레드)

    assert len([t for t in 표 if t is not None]) == 3
    assert _누적() == _누적상한


# ══════════════════════════════════════════════════════════
# ② 재확인은 예약 트랜잭션 «안»에 있다 (프로세스 락에 기대지 않는다)
# ══════════════════════════════════════════════════════════


def _새_원장(tmp_path) -> sqlite3.Connection:
    """파일 원장 하나를 열고 전환까지 끝낸다.

    ★ 반드시 commit 한다 — 다른 스레드가 «다른 연결»로 같은 파일을 열기 때문에,
      전환 기록이 이 연결의 열린 transaction 안에만 있으면 저쪽에서 안 보인다.
    """
    connection = sqlite3.connect(str(tmp_path / "ledger.db"), timeout=10)
    spend_store.ensure_schema(connection)
    state_machine.prepare_cutover(connection, migrated_at=_시각)
    connection.commit()
    return connection


def test_누적_재확인은_예약_트랜잭션_안에서_동시_스레드를_막는다(tmp_path) -> None:
    """★ 프로세스 락이 없어도 막혀야 한다 — 배포에서 worker가 여럿일 수 있다.

    연결을 스레드마다 따로 열고 `_PAID_PHASE_LOCK`을 거치지 않는다. 막는 힘이
    파이썬 락이 아니라 DB write transaction에서 나오는지 본다.
    """
    준비 = _새_원장(tmp_path)
    준비.close()
    개수 = _동시스레드
    barrier = threading.Barrier(개수)

    def 예약(index: int) -> bool:
        conn = sqlite3.connect(str(tmp_path / "ledger.db"), timeout=10)
        try:
            barrier.wait(timeout=10)
            state_machine.begin_phase(
                conn,
                run_id=f"tx-total-{index}",
                phase=SPEND_PHASE_PIPELINE,
                day=_DAY,
                bucket=_열쇠,
                reservation_krw=_본조사예약,
                bucket_limit_krw=_하루상한,
                run_limit_krw=None,
                bucket_total_limit_krw=_누적상한,
                bucket_prior_cost_krw=2999.0,
                lease_owner_id=f"worker:{index}",
                lease_expires_at=_LEASE_EXPIRES_AT,
                started_at=_시각,
            )
            conn.commit()
            return True
        except state_machine.AdmissionLimitExceeded:
            return False
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=개수) as pool:
        결과 = [
            future.result(timeout=20)
            for future in [pool.submit(예약, index) for index in range(개수)]
        ]

    assert 결과 == [False] * 개수, "잔여 1원에 1,000원 예약이 들어갔다"


def test_프로세스_락_없이도_남은_몫_한_건만_예약된다(tmp_path) -> None:
    """★ 막는 힘이 파이썬 락이 아니라 DB write transaction에서 나온다는 증거.

    잔여가 정확히 1,000원일 때 «셋 중 하나»만 들어가야 한다. 파이썬 락 없이
    이게 성립하면 worker가 여러 프로세스여도 천장이 지켜진다.
    """
    준비 = _새_원장(tmp_path)
    준비.close()
    개수 = _동시스레드
    barrier = threading.Barrier(개수)

    def 예약(index: int) -> bool:
        conn = sqlite3.connect(str(tmp_path / "ledger.db"), timeout=10)
        try:
            barrier.wait(timeout=10)
            state_machine.begin_phase(
                conn,
                run_id=f"tx-fit-{index}",
                phase=SPEND_PHASE_PIPELINE,
                day=_DAY,
                bucket=_열쇠,
                reservation_krw=_본조사예약,
                bucket_limit_krw=_하루상한,
                run_limit_krw=None,
                bucket_total_limit_krw=_누적상한,
                bucket_prior_cost_krw=2000.0,   # 잔여 정확히 1,000원
                lease_owner_id=f"worker:{index}",
                lease_expires_at=_LEASE_EXPIRES_AT,
                started_at=_시각,
            )
            conn.commit()
            return True
        except state_machine.AdmissionLimitExceeded:
            return False
        finally:
            conn.close()

    with ThreadPoolExecutor(max_workers=개수) as pool:
        결과 = [
            future.result(timeout=20)
            for future in [pool.submit(예약, index) for index in range(개수)]
        ]

    assert 결과.count(True) == 1, f"들어간 예약 수가 1이 아니다: {결과}"


def test_누적_초과는_하루_초과와_같은_실패_종류다(tmp_path) -> None:
    """★ 새 예외를 만들면 호출부가 이것만 «고장»으로 오인해 통장을 닫는다."""
    conn = _새_원장(tmp_path)
    try:
        with pytest.raises(state_machine.AdmissionLimitExceeded):
            state_machine.begin_phase(
                conn,
                run_id="total-over",
                phase=SPEND_PHASE_PIPELINE,
                day=_DAY,
                bucket=_열쇠,
                reservation_krw=_본조사예약,
                bucket_limit_krw=_하루상한,          # 하루는 넉넉하다
                run_limit_krw=None,
                bucket_total_limit_krw=_누적상한,
                bucket_prior_cost_krw=2999.0,        # 누적만 모자라다
                lease_owner_id="worker:one",
                lease_expires_at=_LEASE_EXPIRES_AT,
                started_at=_시각,
            )
    finally:
        conn.close()


def test_누적_상한을_안_주면_옛_호출부는_그대로_돈다(tmp_path) -> None:
    """★ 하루 상한만 쓰던 기존 호출부의 동작을 바꾸지 않는다."""
    conn = _새_원장(tmp_path)
    try:
        계정 = state_machine.begin_phase(
            conn,
            run_id="legacy-caller",
            phase=SPEND_PHASE_PIPELINE,
            day=_DAY,
            bucket=_열쇠,
            reservation_krw=_본조사예약,
            bucket_limit_krw=_하루상한,
            run_limit_krw=None,
            lease_owner_id="worker:one",
            lease_expires_at=_LEASE_EXPIRES_AT,
            started_at=_시각,
        )
        assert 계정.reservation_krw == _본조사예약
    finally:
        conn.close()


def test_지난_날짜의_진행_예약도_누적에_센다(tmp_path) -> None:
    """★ 「수명 전체」이므로 어제 잡아 둔 예약도 오늘 판단에 들어가야 한다."""
    conn = _새_원장(tmp_path)
    try:
        state_machine.begin_phase(
            conn,
            run_id="yesterday",
            phase=SPEND_PHASE_PIPELINE,
            day=_DAY - dt.timedelta(days=1),
            bucket=_열쇠,
            reservation_krw=_본조사예약,
            bucket_limit_krw=_하루상한,
            run_limit_krw=None,
            lease_owner_id="worker:one",
            lease_expires_at=_LEASE_EXPIRES_AT,
            started_at="2026-09-01T09:00:00+09:00",
        )
        with pytest.raises(state_machine.AdmissionLimitExceeded):
            state_machine.begin_phase(
                conn,
                run_id="today",
                phase=SPEND_PHASE_PIPELINE,
                day=_DAY,
                bucket=_열쇠,
                reservation_krw=_본조사예약,
                bucket_limit_krw=_하루상한,
                run_limit_krw=None,
                bucket_total_limit_krw=_누적상한,
                bucket_prior_cost_krw=1001.0,   # 1001 + 1000(어제) + 1000 > 3000
                lease_owner_id="worker:two",
                lease_expires_at=_LEASE_EXPIRES_AT,
                started_at=_시각,
            )
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════
# ③ 다른 갈래는 이 재확인을 타지 않는다
# ══════════════════════════════════════════════════════════


def test_누적_재확인은_LINK_갈래만_탄다(monkeypatch) -> None:
    """★ 「수명 전체」는 링크에만 있는 개념이다. 사람 통장에는 없다.

    회원·관리자·모르는 손님의 예약에서는 링크 누적을 «읽지도» 않아야 한다.
    """
    _링크와_지난_원가를_둔다(0.0)
    _cutover()
    읽은_지문: list[str] = []
    실제 = share_store.link_run_cost_sum_krw

    def 엿본다(conn, *, key_hash: str) -> float:
        읽은_지문.append(key_hash)
        return 실제(conn, key_hash=key_hash)

    # 예약 지점이 실제로 부르는 함수를 그대로 지켜본다.
    monkeypatch.setattr(share_store, "link_run_cost_sum_krw", 엿본다)

    for 통장, 상한 in (
        ("user:friend@example.com", 3000.0),
        ("user:admin@example.com", 50000.0),
        ("(열쇠 없음)", 0.0),
    ):
        paid_runtime._begin_paid_phase(
            run_id=f"track-{통장}",
            phase=SPEND_PHASE_PIPELINE,
            share_key=통장,
            cap_krw=상한,
        )
    assert 읽은_지문 == [], "링크가 아닌 통장에서 링크 누적을 읽었다"

    paid_runtime._begin_paid_phase(
        run_id="track-link",
        phase=SPEND_PHASE_PIPELINE,
        share_key=_열쇠,
        cap_krw=_하루상한,
    )

    assert 읽은_지문 == [share_store.key_hash_of(_열쇠)]
