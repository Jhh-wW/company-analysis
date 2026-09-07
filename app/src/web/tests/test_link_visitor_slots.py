"""초대 링크 하나를 여러 분이 나눠 써도 «각자» 동시 자리를 갖는지 못 박는다.

★ 무엇이 문제였나 — 초대 링크의 동시 자리는 «열쇠 하나당 한 자리»였다.
  그래서 인사팀 다섯 분이 같은 QR을 함께 찍으면 한 분만 조사가 시작되고
  나머지 네 분은 「지금 다른 조사가 진행 중입니다」를 봤다. 링크는 한 사람이
  아니라 «한 회사»에 보내는 것이라 이건 제품이 아니라 고장이다.

★ 이 파일이 지키는 것
  ① 같은 링크라도 브라우저(방문자)가 다르면 동시에 자리를 잡는다.
  ② 한 사람이 두 건을 동시에 열지는 못한다.
  ③ 링크 하나가 서버 자리를 전부 먹지는 못한다.
  ④ **돈이 나가는 통장은 그대로 링크 하나다** — 자리만 나누고 예산은 안 나눈다.
  ⑤ 방문자 표에는 무작위 글자만 담는다 (첫 화면의 개인정보 약속).
  ⑥ 「자리가 없다」와 「돈이 없다」는 서로 다른 말을 한다.
"""

from __future__ import annotations

import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from src.core import clock
from src.features.auth import logic as auth_logic
from src.features.budget import spend_store
from src.features.budget.constants import (
    BUSY_MESSAGE,
    MAX_CONCURRENT_PER_LINK,
    MAX_CONCURRENT_PER_USER,
    MAX_CONCURRENT_RUNS,
    MAX_CONCURRENT_VISITORS_PER_LINK,
)
from src.features.pipeline.demo import DemoPipeline
from src.features.pipeline.port import CompanyCard
from src.features.sharelink import logic as share_logic
from src.features.sharelink import store as share_store
from src.features.sharelink import tracks as share_tracks
from src.features.sharelink.constants import (
    KEY_COOKIE_NAME,
    LINK_BUDGET_EXHAUSTED_MESSAGE,
    PER_LINK_DAILY_BUDGET_KRW,
    PUBLIC_BUCKET,
    VISITOR_COOKIE_MAX_AGE_SEC,
    VISITOR_COOKIE_NAME,
    VISITOR_ID_CHARS,
)
from src.features.storage import db as storage_db
from src.web import job_runtime, main, paid_runtime, request_helpers, runtime


_열쇠 = "c3d4e5f60718a1b2c3d4e5f60718a1b2"
_다른열쇠 = "0f1e2d3c4b5a69780f1e2d3c4b5a6978"
_경합대기상한초 = 5.0

#: 시험이 쓰는 방문자 표. 발급 함수를 그대로 쓰면 «모양 검사가 통과하는가»까지
#: 같이 시험되므로, 실제 브라우저가 들고 오는 것과 같은 값을 쓴다.
_방문자가 = share_logic.new_visitor_id()
_방문자나 = share_logic.new_visitor_id()


@pytest.fixture
def 손님() -> TestClient:
    """★ 반드시 `with` — 아니면 뒤에서 도는 조사가 요청 이벤트루프와 경합한다."""
    with TestClient(main.app, base_url="https://testserver") as client:
        yield client


def _링크발급(key: str = _열쇠, *, company: str = "우리엔") -> None:
    with storage_db.connect() as conn:
        share_store.insert_new(
            conn,
            key=key,
            company=company,
            job="영업",
            now_iso="2026-09-07T10:00:00",
        )


def _폼(회사: str = "우리엔") -> dict:
    return {"company": 회사, "job": "영업", "region": "서울", "posting_text": "x"}


def _조사시작(client: TestClient, 회사: str = "우리엔"):
    """화면을 거치지 않고 조사 시작 경로를 그대로 부른다.

    ★ `test_run_guard.py`의 같은 이름 도우미와 같은 방식이다 — 데모 알맹이면
      `/confirm`이 내주는 표를 받아 `/run`에 넘기고, 돈이 드는 알맹이면
      그 표를 서버 메모리에 직접 심는다(파이프라인 0회·0원).
    ★ 막힌 곳이 어디든 «그때의 응답»을 그대로 돌려준다. 어느 단계에서 막혔는지가
      아니라 «무슨 말을 했는지»가 이 파일의 관심사다.
    """
    form = _폼(회사)
    link_key = client.cookies.get(KEY_COOKIE_NAME) or ""
    if link_key:
        form["csrf_token"] = auth_logic.csrf_token_for_session(link_key)
    if isinstance(runtime._PIPELINE, DemoPipeline):
        confirm = client.post("/confirm", data=form, follow_redirects=False)
        if confirm.status_code != 200:
            return confirm
        표 = re.search(r'name="paid_attempt_token" value="([^"]+)"', confirm.text)
        assert 표 is not None, confirm.text[:400]
        form["paid_attempt_token"] = 표.group(1)
    else:
        token = uuid.uuid4().hex
        share_key = link_key or PUBLIC_BUCKET
        user_input = request_helpers.company_analysis_input(
            company=form["company"], region=form["region"]
        )
        job_runtime._PAID_ATTEMPTS[token] = job_runtime.PaidAttempt(
            token=token,
            run_id=f"visitor-slot-{token}",
            user_input=user_input,
            card=CompanyCard(
                legal_name=user_input.company,
                typed_name=user_input.company,
                address="서울",
                ceo="",
                founded="",
                ref="visitor-slot",
            ),
            share_key=share_key,
            bucket_id=spend_store.bucket_id(share_key),
            lookup_cost_krw=0.0,
            models=(),
            elapsed_sec=0.0,
            created_at=time.monotonic(),
        )
        form["paid_attempt_token"] = token
    return client.post("/run", data=form, follow_redirects=False)


class _가짜진짜알맹이:
    """`DemoPipeline`이 아니어야 «돈이 드는» 것으로 본다."""

    def run(self, *args, **kwargs):  # pragma: no cover - 막혀서 안 불린다
        raise AssertionError("막혔어야 하는데 조사가 시작됐습니다")


# ══════════════════════════════════════════════════════════
# ① 상한 값 그 자체 — 리터럴로 못 박는다
# ══════════════════════════════════════════════════════════


def test_동시_자리_상한들은_정해진_숫자_그대로다():
    """★ 값을 다른 상수와 견주기만 하면 조용히 되돌아가도 시험이 통과한다.

    그래서 «숫자 그대로» 적는다. 값을 바꾸려면 이 줄을 같이 고쳐야 한다.
    """
    assert MAX_CONCURRENT_RUNS == 5
    assert MAX_CONCURRENT_PER_USER == 2
    assert MAX_CONCURRENT_PER_LINK == 1
    assert MAX_CONCURRENT_VISITORS_PER_LINK == 5


def test_링크_전체_상한은_서버_전체_상한을_넘지_않는다():
    """★ 넘으면 링크 상한이 아무것도 막지 못하는 죽은 값이 된다."""
    assert MAX_CONCURRENT_VISITORS_PER_LINK <= MAX_CONCURRENT_RUNS


# ══════════════════════════════════════════════════════════
# ② 자리는 «사람»마다, 통장은 «링크»마다
# ══════════════════════════════════════════════════════════


def test_같은_링크라도_방문자가_다르면_둘_다_자리를_잡는다():
    """★ 이 파일의 핵심. 예전에는 두 번째 분이 곧바로 거절당했다."""
    가 = paid_runtime._reserve_run_slot(
        share_tracks.Track.LINK, _열쇠, visitor_id=_방문자가
    )
    나 = paid_runtime._reserve_run_slot(
        share_tracks.Track.LINK, _열쇠, visitor_id=_방문자나
    )

    assert 가 and 나
    assert 가 != 나
    assert paid_runtime._RUNNING == 2


def test_같은_방문자의_두번째_요청은_거절한다():
    """★ 링크 통장은 여럿이 나눠 쓰는 돈이라, 한 사람이 두 자리를 쥐면 안 된다."""
    첫자리 = paid_runtime._reserve_run_slot(
        share_tracks.Track.LINK, _열쇠, visitor_id=_방문자가
    )

    assert 첫자리
    assert (
        paid_runtime._reserve_run_slot(
            share_tracks.Track.LINK, _열쇠, visitor_id=_방문자가
        )
        is None
    )

    paid_runtime._release_run_slot(첫자리)
    assert paid_runtime._RUNNING_BY_BUCKET == {}


def test_자리를_돌려주면_같은_방문자가_다시_잡는다():
    """★ 반대 경우 — 한 자리 규칙이 «영구 잠금»이 되면 그건 고장이다."""
    첫자리 = paid_runtime._reserve_run_slot(
        share_tracks.Track.LINK, _열쇠, visitor_id=_방문자가
    )
    assert 첫자리
    paid_runtime._release_run_slot(첫자리)

    다음자리 = paid_runtime._reserve_run_slot(
        share_tracks.Track.LINK, _열쇠, visitor_id=_방문자가
    )

    assert 다음자리
    paid_runtime._release_run_slot(다음자리)
    assert paid_runtime._RUNNING == 0
    assert paid_runtime._RUNNING_BY_BUCKET == {}


def test_정상마감과_강제종료가_경합해도_다음_방문자_자리를_풀지_않는다(
    monkeypatch,
):
    """같은 자리 이름의 확인·반환은 한 Job 안에서 원자적이어야 한다.

    ``slot_released``를 잠금 없이 확인하면 두 정리 경로가 모두 False를 본 뒤
    같은 이름을 두 번 반환할 수 있다. 두 번째 반환 직전에 다음 요청이 같은
    이름을 잡으면 그 새 자리까지 사라지는 ABA 경쟁이 된다.
    """
    반환들: list[str] = []

    class 경합소유자:
        def __init__(self) -> None:
            self.slot_bucket_id = "같은-방문자-자리"
            self.share_key = _열쇠
            self.slot_release_lock = threading.Lock()
            self._slot_released = False
            self._상태잠금 = threading.Lock()
            self._읽기수 = 0
            self.첫읽기 = threading.Event()
            self.둘째읽기 = threading.Event()

        @property
        def slot_released(self) -> bool:
            with self._상태잠금:
                관측값 = self._slot_released
                self._읽기수 += 1
                읽기번호 = self._읽기수
                if 읽기번호 == 1:
                    self.첫읽기.set()
                else:
                    self.둘째읽기.set()
            # 잠금이 빠지면 둘째 경로도 False를 읽도록 의도적으로 양보한다.
            if 읽기번호 == 1:
                self.둘째읽기.wait(timeout=0.1)
            return 관측값

        @slot_released.setter
        def slot_released(self, value: bool) -> None:
            self._slot_released = value

    소유자 = 경합소유자()
    monkeypatch.setattr(job_runtime, "_release_run_slot", 반환들.append)
    첫째 = threading.Thread(target=job_runtime._release_job_slot, args=(소유자,))
    둘째 = threading.Thread(target=job_runtime._release_job_slot, args=(소유자,))

    첫째.start()
    assert 소유자.첫읽기.wait(timeout=1)
    둘째.start()
    첫째.join(timeout=1)
    둘째.join(timeout=1)

    assert not 첫째.is_alive() and not 둘째.is_alive()
    assert 반환들 == ["같은-방문자-자리"]


def test_슬롯_소유표식이_없는_job은_같은_통장의_새_자리를_풀지_않는다():
    """비용 통장과 슬롯 소유권을 섞으면 오래된 정리가 새 자리를 지우는 ABA가 된다."""
    새자리 = paid_runtime._reserve_run_slot(share_tracks.Track.LINK, _열쇠)
    assert 새자리 == spend_store.bucket_id(_열쇠)

    소유하지_않은_job = job_runtime.Job(
        job_id="unowned-slot-cleanup",
        user_input=request_helpers.company_analysis_input(
            company="우리엔", region="서울"
        ),
        card=CompanyCard(
            legal_name="우리엔",
            typed_name="우리엔",
            address="서울",
            ceo="",
            founded="",
            ref="unowned-slot",
        ),
        share_key=_열쇠,
    )

    job_runtime._release_job_slot(소유하지_않은_job)

    assert paid_runtime._RUNNING == 1
    assert paid_runtime._RUNNING_BY_BUCKET == {새자리: 1}
    assert (
        paid_runtime._reserve_run_slot(share_tracks.Track.LINK, _열쇠) is None
    )
    paid_runtime._release_run_slot(새자리)


def test_슬롯_소유표식이_없는_유료job은_worker_admission을_받지_못한다():
    """비용 phase 통장이 있어도 실제 실행 자리를 예약했다는 뜻은 아니다."""
    job = job_runtime.Job(
        job_id="unowned-paid-admission",
        user_input=request_helpers.company_analysis_input(
            company="우리엔", region="서울"
        ),
        card=CompanyCard(
            legal_name="우리엔",
            typed_name="우리엔",
            address="서울",
            ceo="",
            founded="",
            ref="unowned-admission",
        ),
        share_key=_열쇠,
        paid_phase=paid_runtime.PaidPhase(
            run_id="unowned-paid-admission",
            phase="pipeline",
            day=clock.today_kst(),
            share_key=_열쇠,
            bucket_id=spend_store.bucket_id(_열쇠),
        ),
        is_paid=True,
    )

    assert not job_runtime._job_work_admitted(job)


def test_방문자_다섯이_차면_여섯번째는_링크_상한으로_막힌다(monkeypatch):
    """★ 여섯 번째를 막은 것이 «링크 상한»인지 «서버 상한»인지 갈라 본다.

    두 상한이 지금 같은 값(5)이라, 그냥 여섯 명을 부르면 어느 쪽이 막았는지
    알 수 없다. 서버 상한만 잠깐 넉넉히 올려 두면 남는 것은 링크 상한뿐이다.
    같은 순간 «다른 링크»의 손님은 그대로 자리를 잡는다는 것까지 함께 본다.
    """
    monkeypatch.setattr(paid_runtime, "MAX_CONCURRENT_RUNS", 9)
    _링크발급()
    _링크발급(_다른열쇠, company="다른회사")

    자리들 = [
        paid_runtime._reserve_run_slot(
            share_tracks.Track.LINK, _열쇠, visitor_id=share_logic.new_visitor_id()
        )
        for _ in range(5)
    ]
    assert all(자리들)

    여섯째 = paid_runtime._reserve_run_slot(
        share_tracks.Track.LINK, _열쇠, visitor_id=share_logic.new_visitor_id()
    )
    다른링크 = paid_runtime._reserve_run_slot(
        share_tracks.Track.LINK, _다른열쇠, visitor_id=share_logic.new_visitor_id()
    )

    assert 여섯째 is None, "링크 하나가 서버 자리를 전부 먹으면 안 된다"
    assert 다른링크, "다른 링크 손님까지 같이 막으면 안 된다"

    for 자리 in [*자리들, 다른링크]:
        paid_runtime._release_run_slot(자리 or "")
    assert paid_runtime._RUNNING == 0


def test_방문자_쿠키를_동시에_바꿔도_링크_상한_다섯을_넘지_못한다(monkeypatch):
    """쿠키는 신원 증명이 아니므로 서버 상한은 임의 값 경쟁까지 막아야 한다."""
    요청수 = 20
    monkeypatch.setattr(paid_runtime, "MAX_CONCURRENT_RUNS", 요청수)
    출발 = threading.Barrier(요청수)
    방문자들 = [share_logic.new_visitor_id() for _ in range(요청수)]

    def 동시에잡기(방문자: str) -> str | None:
        출발.wait(timeout=_경합대기상한초)
        return paid_runtime._reserve_run_slot(
            share_tracks.Track.LINK,
            _열쇠,
            visitor_id=방문자,
        )

    with ThreadPoolExecutor(max_workers=요청수) as 풀:
        자리들 = list(풀.map(동시에잡기, 방문자들))

    잡힌자리 = [자리 for 자리 in 자리들 if 자리]
    assert len(잡힌자리) == MAX_CONCURRENT_VISITORS_PER_LINK
    assert paid_runtime._RUNNING == MAX_CONCURRENT_VISITORS_PER_LINK
    for 자리 in 잡힌자리:
        paid_runtime._release_run_slot(자리)
    assert paid_runtime._RUNNING == 0
    assert paid_runtime._RUNNING_BY_BUCKET == {}


def test_방문자_표가_없으면_예전처럼_링크_하나가_한_자리다():
    """★ 쿠키가 없는 옛 브라우저·직접 호출에서 아무것도 깨지지 않아야 한다."""
    첫자리 = paid_runtime._reserve_run_slot(share_tracks.Track.LINK, _열쇠)

    assert 첫자리 == spend_store.bucket_id(_열쇠)
    assert paid_runtime._reserve_run_slot(share_tracks.Track.LINK, _열쇠) is None


def test_모양이_틀린_방문자_표는_새_자리를_만들지_못한다():
    """모양이 깨진 입력은 방문자 슬롯 키가 아니라 보수적인 링크 키로 모은다."""
    첫자리 = paid_runtime._reserve_run_slot(
        share_tracks.Track.LINK, _열쇠, visitor_id="손으로-바꾼-값"
    )

    assert 첫자리 == spend_store.bucket_id(_열쇠)
    assert (
        paid_runtime._reserve_run_slot(
            share_tracks.Track.LINK, _열쇠, visitor_id="또-다른-이상한-값"
        )
        is None
    )


def test_방문자가_달라도_비용_통장은_링크_하나다():
    """★ 자리만 나눈다 — 통장까지 나뉘면 링크 하나의 상한이 사람 수만큼 늘어난다."""
    가_자리, 가_링크 = paid_runtime._slot_names(
        share_tracks.Track.LINK, _열쇠, _방문자가
    )
    나_자리, 나_링크 = paid_runtime._slot_names(
        share_tracks.Track.LINK, _열쇠, _방문자나
    )

    assert 가_자리 != 나_자리
    assert 가_링크 == 나_링크 == spend_store.bucket_id(_열쇠)


def test_방문자_표는_로그인_통장을_가르지_않는다():
    """★ 갈래를 잘못 넓히면 로그인 사용자 한 명이 자리를 무한히 잡는다."""
    자리, 통장 = paid_runtime._slot_names(
        share_tracks.Track.MEMBER, "user:friend@example.com", _방문자가
    )

    assert 자리 == 통장 == spend_store.bucket_id("user:friend@example.com")


# ══════════════════════════════════════════════════════════
# ③ 로그인 계정은 두 자리까지
# ══════════════════════════════════════════════════════════


def test_같은_로그인_계정은_두_자리까지_잡고_세번째는_거절한다():
    """★ 검색해 두고 다른 창에서 결과를 여는 흔한 동작을 살린다.

    경계 그 자체를 본다 — 2번째는 잡히고 3번째는 거절된다.
    """
    통장 = "user:friend@example.com"
    자리들 = [
        paid_runtime._reserve_run_slot(share_tracks.Track.MEMBER, 통장)
        for _ in range(2)
    ]

    assert all(자리들)
    assert paid_runtime._reserve_run_slot(share_tracks.Track.MEMBER, 통장) is None

    for 자리 in 자리들:
        paid_runtime._release_run_slot(자리 or "")
    assert paid_runtime._RUNNING == 0


# ══════════════════════════════════════════════════════════
# ④ `/k/` 가 방문자 표를 실어 준다
# ══════════════════════════════════════════════════════════


def test_초대링크를_열면_방문자_표_쿠키를_받는다(손님: TestClient):
    _링크발급()

    열림 = 손님.get(f"/k/{_열쇠}", follow_redirects=False)

    # ★ `headers.items()`는 같은 이름의 헤더를 쉼표로 이어 붙여 하나로 준다 —
    #   그러면 두 쿠키가 한 줄이 되어 각 줄의 속성을 볼 수 없다.
    실린 = [
        값
        for 값 in 열림.headers.get_list("set-cookie")
        if 값.startswith(f"{VISITOR_COOKIE_NAME}=")
    ]
    assert len(실린) == 1
    쿠키 = 실린[0]
    assert "HttpOnly" in 쿠키
    assert "SameSite=lax" in 쿠키
    assert "Secure" in 쿠키
    assert f"Max-Age={VISITOR_COOKIE_MAX_AGE_SEC}" in 쿠키

    표 = 손님.cookies.get(VISITOR_COOKIE_NAME)
    assert 표 and share_logic.is_valid_visitor_id(표)


def test_같은_브라우저가_다시_들어와도_방문자_표는_그대로다(손님: TestClient):
    """★ 올 때마다 새 표를 주면 창을 하나 더 열 때마다 «다른 사람»이 된다."""
    _링크발급()

    손님.get(f"/k/{_열쇠}", follow_redirects=False)
    처음표 = 손님.cookies.get(VISITOR_COOKIE_NAME)
    손님.get(f"/k/{_열쇠}", follow_redirects=False)
    다시표 = 손님.cookies.get(VISITOR_COOKIE_NAME)

    assert 처음표 and 처음표 == 다시표


def test_방문자_표에는_무작위_글자만_들어_있다(손님: TestClient):
    """★ 첫 화면이 「개인정보는 수집하지 않습니다」라고 약속한다.

    표 안에 IP·브라우저 종류가 섞이면 그 약속이 거짓말이 된다.
    """
    _링크발급()
    브라우저 = "Mozilla/5.0 (Windows NT 10.0) TestBrowser/1.0"

    손님.get(
        f"/k/{_열쇠}",
        follow_redirects=False,
        headers={"User-Agent": 브라우저},
    )
    표 = 손님.cookies.get(VISITOR_COOKIE_NAME) or ""

    assert re.fullmatch(rf"[A-Za-z0-9_-]{{{VISITOR_ID_CHARS}}}", 표)
    assert "Mozilla" not in 표
    assert "TestBrowser" not in 표
    assert "testserver" not in 표
    for 조각 in ("127.0.0.1", "testclient", "10.0"):
        assert 조각 not in 표


def test_서로_다른_브라우저는_서로_다른_표를_받는다():
    """★ 같은 표를 주면 두 분이 한 자리를 두고 다투게 된다."""
    _링크발급()

    with TestClient(main.app, base_url="https://testserver") as 가:
        가.get(f"/k/{_열쇠}", follow_redirects=False)
        가표 = 가.cookies.get(VISITOR_COOKIE_NAME)
    with TestClient(main.app, base_url="https://testserver") as 나:
        나.get(f"/k/{_열쇠}", follow_redirects=False)
        나표 = 나.cookies.get(VISITOR_COOKIE_NAME)

    assert 가표 and 나표 and 가표 != 나표


# ══════════════════════════════════════════════════════════
# ⑤ 배선 — 화면 경로가 실제로 방문자 표를 쓴다
# ══════════════════════════════════════════════════════════


def test_한_방문자가_도는_동안_다른_방문자는_조사를_시작한다(monkeypatch):
    """★ 상한 함수만 고치고 화면 경로에 안 넘기면 아무것도 안 바뀐다.

    그래서 «실제 요청»으로 확인한다 — 앞사람 자리를 진짜 예약으로 채우고,
    같은 사람은 막히고 다른 사람은 통과하는지 본다.
    """
    monkeypatch.setattr(runtime, "_PIPELINE", DemoPipeline())
    _링크발급()

    with TestClient(main.app, base_url="https://testserver") as 앞사람:
        with TestClient(main.app, base_url="https://testserver") as 뒷사람:
            앞사람.get(f"/k/{_열쇠}", follow_redirects=False)
            뒷사람.get(f"/k/{_열쇠}", follow_redirects=False)
            앞자리 = paid_runtime._reserve_run_slot(
                share_tracks.Track.LINK,
                _열쇠,
                visitor_id=앞사람.cookies.get(VISITOR_COOKIE_NAME) or "",
            )
            assert 앞자리

            막힘 = _조사시작(앞사람)
            통과 = _조사시작(뒷사람)

    assert 막힘.status_code == 429
    assert BUSY_MESSAGE in 막힘.text
    assert 통과.status_code == 303


# ══════════════════════════════════════════════════════════
# ⑥ 「자리가 없다」와 「돈이 없다」는 다른 말이다
# ══════════════════════════════════════════════════════════


def test_자리가_없을_때는_진행_중이라고_말한다(monkeypatch, 손님: TestClient):
    monkeypatch.setattr(runtime, "_PIPELINE", DemoPipeline())
    _링크발급()
    손님.get(f"/k/{_열쇠}", follow_redirects=False)
    자리 = paid_runtime._reserve_run_slot(
        share_tracks.Track.LINK,
        _열쇠,
        visitor_id=손님.cookies.get(VISITOR_COOKIE_NAME) or "",
    )
    assert 자리

    막힘 = _조사시작(손님)

    assert 막힘.status_code == 429
    assert BUSY_MESSAGE in 막힘.text
    assert LINK_BUDGET_EXHAUSTED_MESSAGE not in 막힘.text


def test_예산이_없을_때는_자리_핑계를_대지_않는다(monkeypatch, 손님: TestClient):
    """★ 「1~2분 뒤에 다시」는 기다리면 열릴 때만 참이다.

    예산이 소진된 손님에게 그 말을 하면 헛되이 기다리게 한다. 자리는 다섯이
    비어 있는데도 막혔다면 이유는 돈이고, 화면도 그렇게 말해야 한다.
    """
    monkeypatch.setattr(runtime, "_PIPELINE", _가짜진짜알맹이())
    _링크발급()
    손님.get(f"/k/{_열쇠}", follow_redirects=False)
    오늘 = clock.today_kst()
    monkeypatch.setattr(
        paid_runtime,
        "_LINK_SPEND",
        share_logic.add_spend(
            share_logic.DailySpend(day=오늘), _열쇠, 오늘, PER_LINK_DAILY_BUDGET_KRW
        ),
    )

    막힘 = _조사시작(손님)

    assert paid_runtime._RUNNING == 0, "자리는 비어 있는 상황이어야 한다"
    assert 막힘.status_code == 429
    assert "이 링크로 돌릴 수 있는 새 조사를 모두 사용" in 막힘.text
    assert BUSY_MESSAGE not in 막힘.text
