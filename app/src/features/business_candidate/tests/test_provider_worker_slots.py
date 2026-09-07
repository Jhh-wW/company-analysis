"""회사 검색 일꾼 수가 «동시에 쓰는 사람 수»를 못 따라가지 않는지 못 박는다.

★ 무엇이 문제였나 — 조사 자리는 다섯인데 회사 검색 일꾼은 셋이었다. 그래서
  다섯 분이 같은 순간에 회사 이름을 넣으면 두 분은 조사 자리가 남아 있는데도
  검색 단계에서 곧바로 거절당했다.

★ 여기서 세는 숫자는 «리터럴»이다. 상한 상수를 그대로 표본 크기로 쓰면,
  상한이 1로 내려가는 순간 경쟁 자체가 사라지고도 시험은 계속 초록불이 된다.
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor

from src.core.constants import MAX_CONCURRENT_RUNS
from src.features.business_candidate import logic
from src.features.business_candidate.constants import PROVIDER_WORKER_SLOTS


#: 동시에 검색을 누르는 사람 수. 상한과 같아야 «전원 통과»가 의미를 갖는다.
_동시검색 = 5
#: 상한을 한 명 넘긴 수. 이 한 명만 거절돼야 한다.
_초과요청 = 6
#: 일꾼이 붙잡혀 있는 동안 기다릴 최대 시간(초). 넘으면 시험이 매달리지 않고 깨진다.
_대기상한초 = 8.0


def _후보한줄() -> logic.RawBusinessCandidate:
    return logic.RawBusinessCandidate(
        candidate_name="(주)제이와이피엔터테인먼트",
        address="서울특별시 강동구 강동대로 205",
        homepage="https://www.jype.com/",
        source_label="공식 사업자 검색 API",
        source_url="https://www.jype.com/",
    )


class _붙잡는공급자:
    """풀어 줄 때까지 일꾼 자리를 붙잡고 있는 가짜 공급자."""

    costs_money = False
    provider_name = "테스트"

    def __init__(self, 들어옴: threading.Semaphore, 풀림: threading.Event) -> None:
        self._들어옴 = 들어옴
        self._풀림 = 풀림
        self._잠금 = threading.Lock()
        self.calls = 0

    def search(self, **_kwargs):
        with self._잠금:
            self.calls += 1
        self._들어옴.release()
        assert self._풀림.wait(timeout=_대기상한초), "가짜 공급자가 제때 풀리지 않았습니다"
        return [_후보한줄()]


def test_검색_일꾼_수는_조사_자리_수와_같다():
    """★ 값을 베껴 적으면 두 값이 조용히 어긋난다. 같다는 것 자체를 못 박는다."""
    assert PROVIDER_WORKER_SLOTS == 5
    assert PROVIDER_WORKER_SLOTS == MAX_CONCURRENT_RUNS


def test_다섯_명이_동시에_검색해도_아무도_거절되지_않는다(monkeypatch):
    """★ 여섯 번째 한 명만 거절된다 — 다섯 명은 전원 검색까지 도달한다."""
    monkeypatch.setattr(logic, "_RATE_HISTORY", type(logic._RATE_HISTORY)())
    # 앞선 timeout 시험의 worker가 아직 끝나지 않았더라도 이 경계 시험은
    # 자기 세마포어에서 정확히 다섯 자리를 재야 한다.
    monkeypatch.setattr(
        logic,
        "_PROVIDER_WORKER_SLOTS",
        threading.BoundedSemaphore(PROVIDER_WORKER_SLOTS),
    )
    들어옴 = threading.Semaphore(0)
    풀림 = threading.Event()
    공급자 = _붙잡는공급자(들어옴, 풀림)
    결과: list[logic.CandidateResolution] = []
    결과잠금 = threading.Lock()

    def 검색(번호: int) -> None:
        판정 = logic.resolve_candidates(
            공급자,
            company="JYP",
            address_hint="서울",
            # 횟수 제한은 사람마다 따로 센다. 같은 열쇠를 쓰면 일꾼이 아니라
            # 횟수 제한에 걸려 「무엇이 막았는가」가 흐려진다.
            rate_key=f"worker-slot-{번호}",
            now=float(번호),
            allow_paid_provider=True,
        )
        with 결과잠금:
            결과.append(판정)

    with ThreadPoolExecutor(max_workers=_초과요청) as 풀:
        보낸것 = [풀.submit(검색, 번호) for 번호 in range(_초과요청)]

        # ① 다섯 명이 «실제로» 일꾼을 붙잡을 때까지 기다린다.
        for _ in range(_동시검색):
            assert 들어옴.acquire(timeout=_대기상한초), "다섯 명이 동시에 못 들어갔습니다"

        # ② 자리가 꽉 찬 동안 남은 한 명은 곧바로 거절돼야 한다. 아직 시작도
        #    못 했다면 시작할 때까지 기다린다 — 자리는 계속 잡혀 있으므로
        #    이 손님이 통과해 버릴 길은 없다.
        마감 = time.monotonic() + _대기상한초
        while time.monotonic() < 마감:
            with 결과잠금:
                if 결과:
                    break
            time.sleep(0.01)
        with 결과잠금:
            거절수 = len(결과)
        assert 거절수 == 1, "자리가 꽉 찬 동안 남은 한 명이 거절되지 않았습니다"
        거절 = 결과[0]

        풀림.set()
        for 하나 in 보낸것:
            하나.result(timeout=_대기상한초)

    assert 거절.status is logic.ResolutionStatus.RATE_LIMITED
    assert 거절.provider_called is False
    assert 공급자.calls == _동시검색
    통과 = [판정 for 판정 in 결과 if 판정.status is logic.ResolutionStatus.OK]
    assert len(통과) == _동시검색


def test_일꾼_자리는_끝나면_돌려준다(monkeypatch):
    """★ 한 번 꽉 찬 뒤 영영 안 열리면 그건 더 나쁜 고장이다."""
    monkeypatch.setattr(logic, "_RATE_HISTORY", type(logic._RATE_HISTORY)())
    monkeypatch.setattr(
        logic,
        "_PROVIDER_WORKER_SLOTS",
        threading.BoundedSemaphore(PROVIDER_WORKER_SLOTS),
    )
    풀림 = threading.Event()
    풀림.set()
    공급자 = _붙잡는공급자(threading.Semaphore(0), 풀림)

    for 번호 in range(_초과요청):
        판정 = logic.resolve_candidates(
            공급자,
            company="JYP",
            address_hint="서울",
            rate_key=f"worker-reuse-{번호}",
            now=float(번호),
            allow_paid_provider=True,
        )
        assert 판정.status is logic.ResolutionStatus.OK

    assert 공급자.calls == _초과요청
