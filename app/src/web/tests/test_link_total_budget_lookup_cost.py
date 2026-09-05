"""초대 링크의 «수명 전체» 누적 상한이 회사 확인 비용까지 세는지 못 박는다.

★ 무엇이 샜나 — 회사 확인 단계는 조사 이력 행을 만들지 않는다. 누적의 바닥값을
  조사 이력에서만 읽으면 확인 비용이 통째로 0원으로 보인다. 하루 상한은 자정마다
  되살아나므로, 확인만 반복하면 링크 하나가 「하루 상한 × 링크 수명」만큼 쓸 수 있다.
  실측(수정 전): 확인 100원 × 30건으로 3,000원을 쓴 다음 날 본조사 900원이 통과했다.

★ 이 시험이 지키는 것 — 바닥값을 «비용 원장의 단계 단위 합»으로 읽는다는 것.
  원장에는 조사 이력을 남기지 않는 단계도 빠짐없이 들어 있다.

★ 하루 상한 규칙은 건드리지 않는다 — 자정이 지나면 하루 몫은 그대로 되살아나야
  하고, 사람 통장(회원·관리자)에는 「수명」이라는 개념 자체가 없어야 한다.
  아래 시험들이 그 두 가지를 각각 못 박는다.

★ AI·네트워크 호출 0건. provider 응답은 관측 기록으로만 흉내 낸다.
"""

from __future__ import annotations

import datetime as dt

import pytest

from src.core import clock
from src.core.provider_gateway import attempt_context
from src.core.provider_gateway.types import (
    BillingDisposition,
    ProviderObservation,
    TransportState,
)
from src.features.budget import spend_store, state_machine
from src.features.budget.constants import (
    SPEND_PHASE_IDENTIFY,
    SPEND_PHASE_PIPELINE,
)
from src.features.sharelink import store as share_store
from src.features.storage import db as storage_db
from src.web import paid_runtime

_열쇠 = "d1d2e3f4a5b60718d1d2e3f4a5b60718"
_시각 = "2026-09-02T09:00:00+09:00"

#: ★ 리터럴로 못 박는다 — 생산 상수를 import해 비교하면 값이 몰래 낮아져도 통과한다.
#:   상수 자체가 맞는지는 `sharelink/tests/test_link_total_budget.py`가 본다.
_누적상한 = 3000.0
_하루상한 = 3000.0
_확인원가 = 100.0
_본조사예약 = 1800.0

#: 하루 상한(3,000원)을 정확히 채우는 확인 횟수.
_하루를_채우는_확인횟수 = 30


@pytest.fixture
def 날짜를_옮긴다(monkeypatch):
    """모든 시각 읽기를 통째로 하루 단위로 민다.

    ★ 제품 코드는 날짜를 `clock.now_kst` 하나에서만 얻는다(`today_kst`·
      `iso_now_kst`도 이 함수를 부른다). 그래서 이것만 바꾸면 자정을 넘긴
      다음 날의 판단을 실제 코드 경로 그대로 재현할 수 있다.
    """

    상태 = {"일수": 0}
    진짜_now = clock.now_kst

    def _now() -> dt.datetime:
        return 진짜_now() + dt.timedelta(days=상태["일수"])

    monkeypatch.setattr(clock, "now_kst", _now)

    def 옮긴다(일수: int) -> None:
        상태["일수"] = 일수

    return 옮긴다


def _링크발급(key: str = _열쇠) -> None:
    with storage_db.connect() as conn:
        assert share_store.insert_new(
            conn,
            key=key,
            company="카카오",
            job="마케팅",
            report_id="",
            now_iso=_시각,
        )


def _전환() -> None:
    paid_runtime.prepare_budget_state_machine_cutover()
    paid_runtime._seed_ledger()


def _확인단계(run_id: str, *, 통장: str = _열쇠, 원가: float = _확인원가):
    """`/confirm`의 회사 확인이 밟는 원장 전이만 그대로 밟는다.

    조사 이력(`start_run`/`finish_run`)은 «일부러» 만들지 않는다. 실제 확인
    단계도 만들지 않기 때문이다 — 그 사실이 이 결함의 뿌리다.
    """

    표 = paid_runtime._begin_paid_phase(
        run_id=run_id,
        phase=SPEND_PHASE_IDENTIFY,
        share_key=통장,
        cap_krw=_하루상한,
    )
    if 표 is None:
        return None
    with paid_runtime._activate_paid_provider(표):
        callbacks = attempt_context.current()
        attempt_id = callbacks.begin_attempt("anthropic", "identify", _확인원가)
        callbacks.heartbeat(attempt_id)
        callbacks.mark_dispatch_intent(attempt_id)
        callbacks.record_observation(
            attempt_id,
            ProviderObservation(
                TransportState.RESPONSE_RECEIVED,
                BillingDisposition.KNOWN_COST,
                원가,
                0.0,
                200,
                "",
                f"req-{run_id}",
            ),
        )
    paid_runtime._settle_paid_phase(표, amount_krw=원가, billing_uncertain=False)
    return 표


def _확인을_반복한다(횟수: int, *, 통장: str = _열쇠) -> int:
    """확인 단계를 요청 번호를 바꿔 가며 반복하고 «통과한 건수»를 돌려준다."""

    통과 = 0
    for 번호 in range(1, 횟수 + 1):
        if _확인단계(f"confirm-{통장[:8]}-{번호:03d}", 통장=통장) is None:
            break
        통과 += 1
    return 통과


def _본조사를_예약한다(run_id: str, *, 통장: str = _열쇠):
    return paid_runtime._begin_paid_phase(
        run_id=run_id,
        phase=SPEND_PHASE_PIPELINE,
        share_key=통장,
        cap_krw=_하루상한,
    )


def _누적바닥값() -> float:
    """예약을 커밋하는 자리가 실제로 쓰는 «지난 실측 원가»."""

    with storage_db.connect() as conn:
        return paid_runtime._link_total_budget_inputs(conn, _열쇠)[1]


def _안내숫자() -> float:
    """화면 안내와 사전 검사가 함께 쓰는 누적 사용액."""

    with storage_db.connect() as conn:
        return share_store.link_total_spent_krw(
            conn, key_hash=share_store.key_hash_of(_열쇠)
        )


def _오늘_통장노출() -> float:
    with storage_db.connect() as conn:
        return state_machine.load_exposure(
            conn,
            day=clock.today_kst(),
            bucket_id=spend_store.bucket_id(_열쇠),
        ).admission_exposure_krw


# ══════════════════════════════════════════════════════════
# ① 결함 그 자체 — 확인만 반복해 누적을 다 쓴 링크
# ══════════════════════════════════════════════════════════


def test_확인만_반복해_누적을_다_쓴_링크는_다음_날_본조사가_막힌다(
    날짜를_옮긴다,
) -> None:
    """★ 이 파일이 존재하는 이유. 수정 전에는 다음 날 900원이 통과했다."""

    _링크발급()
    _전환()

    통과 = _확인을_반복한다(_하루를_채우는_확인횟수 + 1)
    assert 통과 == _하루를_채우는_확인횟수, "하루 상한 전제가 달라졌다"
    assert _누적바닥값() == _누적상한

    날짜를_옮긴다(1)

    # 자정을 넘겼으니 «하루» 몫은 그대로 되살아난다 — 그 규칙은 바꾸지 않았다.
    assert _오늘_통장노출() == 0.0

    assert _본조사를_예약한다("day2-run") is None, (
        "확인 비용으로 누적을 다 쓴 링크에 본조사가 들어갔다"
    )


def test_확인_비용은_안내_숫자에도_그대로_보인다() -> None:
    """★ 안내와 차단이 다른 숫자를 보면 손님은 「열린다」고 읽고 막힌다."""

    _링크발급()
    _전환()

    _확인을_반복한다(_하루를_채우는_확인횟수)

    assert _안내숫자() == _누적상한
    assert _안내숫자() == _누적바닥값()


# ══════════════════════════════════════════════════════════
# ② 경계 — 정확히 상한, 부채만 있는 링크, 취소된 예약
# ══════════════════════════════════════════════════════════


def test_누적이_정확히_상한에_닿는_예약까지는_들어간다(날짜를_옮긴다) -> None:
    """★ 반대 방향 시험 — 다 막아 버리면 그냥 고장이다.

    확인 12건(1,200원) 뒤 남은 몫은 정확히 1,800원(본조사 예약액)이다. 본조사 한
    건은 들어가고, 그 결과 누적은 정확히 상한이 된다.
    """

    _링크발급()
    _전환()

    assert _확인을_반복한다(12) == 12
    assert _누적바닥값() == 1200.0

    날짜를_옮긴다(1)
    표 = _본조사를_예약한다("day2-fit")

    assert 표 is not None, "잔여가 정확히 1,800원인데 본조사가 막혔다"
    assert _안내숫자() == _누적상한


def test_상한을_단_1원이라도_넘기면_막힌다(날짜를_옮긴다) -> None:
    """★ 앞 시험과 «확인 한 건»만 다르다. 그 100원이 판단을 뒤집어야 한다."""

    _링크발급()
    _전환()

    assert _확인을_반복한다(13) == 13
    assert _누적바닥값() == 1300.0

    날짜를_옮긴다(1)

    assert _본조사를_예약한다("day2-over") is None, (
        "1,300 + 1,800 = 3,100원인데 본조사가 들어갔다"
    )


def test_부채만_남은_링크도_누적에_센다(날짜를_옮긴다) -> None:
    """★ 전송 의도까지 갔는데 응답을 못 본 돈은 «썼다고 보고» 세야 한다.

    확정 원가는 0원이지만 보수부채가 남는다. 이걸 안 세면 응답을 못 받는
    상황이 반복될수록 누적이 0원으로 보인다.
    """

    _링크발급()
    _전환()

    for 번호 in range(1, _하루를_채우는_확인횟수 + 1):
        표 = paid_runtime._begin_paid_phase(
            run_id=f"uncertain-{번호:03d}",
            phase=SPEND_PHASE_IDENTIFY,
            share_key=_열쇠,
            cap_krw=_하루상한,
        )
        assert 표 is not None
        with paid_runtime._activate_paid_provider(표):
            callbacks = attempt_context.current()
            attempt_id = callbacks.begin_attempt(
                "anthropic", "identify", _확인원가
            )
            callbacks.heartbeat(attempt_id)
            # 전송 의도만 남기고 관측 없이 끝낸다 → 보수부채로 닫힌다.
            callbacks.mark_dispatch_intent(attempt_id)
        paid_runtime._settle_paid_phase(
            표, amount_krw=0.0, billing_uncertain=True
        )

    with storage_db.connect() as conn:
        노출 = state_machine.load_bucket_lifetime_exposure(
            conn, bucket_id=spend_store.bucket_id(_열쇠)
        )
    assert 노출.known_cost_krw == 0.0
    assert 노출.liability_krw == _누적상한
    assert _누적바닥값() == _누적상한

    날짜를_옮긴다(1)

    assert _본조사를_예약한다("day2-debt") is None, (
        "보수부채만 3,000원인 링크에 본조사가 들어갔다"
    )


def test_취소된_예약은_누적에서_되돌아온다() -> None:
    """★ 시작만 하고 provider를 안 부른 예약이 영원히 누적을 먹으면 안 된다."""

    _링크발급()
    _전환()

    assert _확인을_반복한다(10) == 10
    취소전 = _누적바닥값()
    assert 취소전 == 1000.0

    표 = _본조사를_예약한다("cancel-me")
    assert 표 is not None
    assert _안내숫자() == 취소전 + _본조사예약

    paid_runtime._cancel_paid_phase(표)

    assert _누적바닥값() == 취소전
    assert _안내숫자() == 취소전


# ══════════════════════════════════════════════════════════
# ③ 다른 갈래는 그대로다
# ══════════════════════════════════════════════════════════


def test_사람_통장은_수명_누적을_보지_않는다(날짜를_옮긴다) -> None:
    """★ 「수명 전체」는 링크에만 있는 개념이다. 회원 통장에는 없다.

    회원 통장으로 같은 금액을 써도 다음 날 본조사는 하루 상한만 본다.
    """

    회원통장 = "user:friend@example.com"
    _링크발급()
    _전환()

    assert (
        _확인을_반복한다(_하루를_채우는_확인횟수, 통장=회원통장)
        == _하루를_채우는_확인횟수
    )

    날짜를_옮긴다(1)
    표 = _본조사를_예약한다("member-day2", 통장=회원통장)

    assert 표 is not None, "회원 통장에 없던 수명 상한이 생겼다"
