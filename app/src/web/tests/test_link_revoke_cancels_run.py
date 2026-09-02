"""G-S11 — 초대 링크가 조사 도중 닫히면 다음 «유료 단계»에 들어가지 않는다.

관리자가 링크를 닫아도 이미 돌고 있는 조사는 그대로 끝까지 돈을 쓰던 것이 이 시험이
막는 결함이다. 시작할 때 한 번 본 판정을 재사용하지 않고 유료 단계마다 저장소를 다시
읽는지까지 본다.

★ 실제 AI·네트워크는 한 번도 부르지 않는다. 유료 단계 경계(`ensure_paid_phase`)만
  여러 번 여는 가짜 조사를 쓰고, 그 경계 통과 횟수를 세어 「새 유료 호출 0」을 단정한다.
★ `run_job`·`begin_phase`를 통째로 바꾸지 않는다 — 진짜 `PaidPhase`·`job_runtime`이 돈다.
★ `TestClient`를 `with`로 연다. 그러지 않으면 요청마다 event loop가 닫히면서 배경
  조사가 임의로 취소돼(실측: `_run_job`의 취소 처리가 `abandon()`을 부른다) 이 시험이
  보려는 판정 자체가 안 돈다.
"""

from __future__ import annotations

import contextlib
import hashlib
import re
import sqlite3
import time
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from src.core import clock
from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.budget import spend_store
from src.features.pipeline.port import (
    CompanyCard,
    CompanyLookupResult,
    Outcome,
    RunResult,
    UserInput,
)
from src.features.sharelink import allowlist as share_allow
from src.features.sharelink import store as share_store
from src.features.sharelink.constants import KEY_COOKIE_NAME
from src.features.storage import db as storage_db
from src.shared import generation_coordination
from src.web import job_runtime, main, runtime
from src.web.tests._visible_text import visible_text

_LINK = "c1d2e3f4a5b60718c1d2e3f4a5b60718"
_FORM = {
    "company": "가나다전자",
    "job": "영업",
    "region": "서울",
    "posting_text": "채용 공고 원문",
}
_식별비용 = 10.0
_단계비용 = 30.0

#: 사용자 화면에 절대 나오면 안 되는 내부 용어. 소문자로 낮춰 비교한다.
_금지어 = (
    "link",
    "revoke",
    "hash",
    "capability",
    "fail-closed",
    "fail closed",
    "철회",
    "해시",
    "토큰",
    "phase",
    "share_key",
)


class 링크단계가짜조사:
    """유료 단계를 여러 번 여는 무과금 가짜 본조사.

    `generation_coordination.ensure_paid_phase()`가 실제 운영 pipeline에서 유료 단계
    직전에 불리는 자리다(`features/pipeline/real.py`의 세 곳). 그 자리를 그대로 흉내 내
    「다음 단계에서 멈추는가」를 본다. provider(AI)는 부르지 않으므로 돈을 쓰지 않는다.
    """

    supports_deferred_paid_phase = True

    def __init__(self, *, 단계수: int = 3, 단계전=None) -> None:
        self.단계수 = 단계수
        self.단계전 = 단계전
        self.lookup_calls = 0
        #: 훅을 통과해 실제로 «들어간» 유료 단계 수 = 새 유료 호출 수
        self.유료단계_진입 = 0
        #: 링크를 닫은 순간까지 들어간 유료 단계 수
        self.닫힌시점_진입: int | None = None

    def find_company_metered(self, user_input: UserInput) -> CompanyLookupResult:
        self.lookup_calls += 1
        return CompanyLookupResult(
            card=CompanyCard(
                legal_name=user_input.company,
                typed_name=user_input.company,
                address="서울",
                ceo="대표",
                founded="20200101",
                ref="corp-001",
            ),
            cost_krw=_식별비용,
            model="lookup-model",
        )

    def run(self, user_input: UserInput, card: CompanyCard, tell) -> RunResult:
        del user_input, card
        # 부분 지문이면 single-flight를 우회한다 — lease 없이 유료 단계만 연다.
        generation_coordination.coordinate(
            corp_id="",
            cache_namespace=None,
            preflight_identity_digest="",
        )
        for 단계 in range(self.단계수):
            if self.단계전 is not None:
                self.단계전(단계, self)
            generation_coordination.ensure_paid_phase()
            self.유료단계_진입 += 1
            tell("generate")
        return RunResult(
            outcome=Outcome.GATE_STOPPED,
            cost_krw=_단계비용 * self.유료단계_진입,
            model="pipeline-model",
        )


def _csrf설치(client: TestClient) -> None:
    """분석 폼 CSRF 입구를 정상 폼처럼 지난다."""

    if getattr(client, "_gs11_csrf", False):
        return
    original_post = client.post

    def post_with_csrf(url, *args, **kwargs):
        if url in {"/confirm", "/reject", "/run"}:
            data = dict(kwargs.pop("data", {}) or {})
            secret = (
                client.cookies.get(auth_constants.SESSION_COOKIE_NAME)
                or client.cookies.get(KEY_COOKIE_NAME)
                or ""
            )
            if secret:
                data.setdefault(
                    "csrf_token", auth_logic.csrf_token_for_session(secret)
                )
            kwargs["data"] = data
        return original_post(url, *args, **kwargs)

    client.post = post_with_csrf
    client._gs11_csrf = True


@contextlib.contextmanager
def _링크손님(key: str = _LINK) -> Iterator[TestClient]:
    """살아 있는 초대 링크로 들어온 손님 하나."""

    with TestClient(main.app) as client:
        with storage_db.connect() as conn:
            assert (
                share_store.insert_new(
                    conn,
                    key=key,
                    company="가나다전자",
                    job="영업",
                    now_iso="2026-08-17T10:00:00",
                )
                is True
            )
        client.cookies.set(KEY_COOKIE_NAME, key)
        _csrf설치(client)
        yield client


@contextlib.contextmanager
def _회원손님(email: str) -> Iterator[TestClient]:
    """명단에 있는 회원 하나. 링크 없이 들어온다."""

    with TestClient(main.app) as client:
        with storage_db.connect() as conn:
            share_allow.invite(
                conn,
                email=email,
                note="G-S11 시험",
                now_iso="2026-08-17T10:00:00",
            )
        subject = "google:test-" + hashlib.sha256(
            email.lower().encode("utf-8")
        ).hexdigest()[:24]
        session = auth_logic.create_session(email, False, subject=subject)
        client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)
        _csrf설치(client)
        yield client


def _링크를_닫는다(key: str = _LINK) -> None:
    with storage_db.connect() as conn:
        assert share_store.delete(conn, key) is True


def _링크_기간을_넘긴다(key: str = _LINK) -> None:
    with storage_db.connect() as conn:
        assert (
            share_store.set_expires_at(
                conn,
                key_hash=share_store.key_hash_of(key),
                expires_at=clock.today_kst().isoformat(),
            )
            is True
        )


def _확인값(html: str) -> str:
    token = re.search(r'name="paid_attempt_token" value="([^"]+)"', html)
    assert token is not None, "확인 화면에서 일회용 토큰을 찾지 못했습니다"
    return token.group(1)


def _조사를_돌린다(client: TestClient) -> str:
    """회사 확인 → 본조사 시작 → 끝날 때까지 기다린 뒤 분석 ID를 준다."""

    확인 = client.post("/confirm", data=_FORM, follow_redirects=False)
    assert 확인.status_code == 200, 확인.text
    폼 = {
        **_FORM,
        "paid_attempt_token": _확인값(확인.text),
        "posting_image_consent": "yes",
    }
    시작 = client.post("/run", data=폼, follow_redirects=False)
    assert 시작.status_code == 303, 시작.text
    job_id = 시작.headers["location"].rsplit("/", 1)[-1]
    # ★ `/api/progress`로 기다리지 않는다. 링크를 닫으면 그 손님은 열람 권한부터
    #   잃어(`report_access`) 진행 API가 404를 준다 — 조사가 끝났는지와 무관하다.
    for _ in range(1000):
        job = job_runtime._JOBS.get(job_id)
        if job is not None and job.finished:
            return job_id
        time.sleep(0.01)
    raise AssertionError("가짜 본조사가 끝나지 않았습니다")


def _링크이력(job_id: str):
    with storage_db.connect() as conn:
        return share_store.load_run(conn, job_id)


def _닫을_단계(단계번호: int, 닫기):
    """지정한 단계 «직전»에 링크를 닫는 콜백을 만든다."""

    def 콜백(단계: int, pipeline: 링크단계가짜조사) -> None:
        if 단계 == 단계번호 and pipeline.닫힌시점_진입 is None:
            닫기()
            pipeline.닫힌시점_진입 = pipeline.유료단계_진입

    return 콜백


def _읽기_세는_대역(monkeypatch) -> dict[str, int]:
    """훅이 저장소를 몇 번 다시 읽는지 세는 얇은 spy(진짜 읽기를 그대로 부른다)."""

    읽기수 = {"횟수": 0}
    원본 = share_store.load_by_hash

    def 세는_읽기(conn, key_hash):
        읽기수["횟수"] += 1
        return 원본(conn, key_hash)

    monkeypatch.setattr(share_store, "load_by_hash", 세는_읽기)
    return 읽기수


def test_철회된_링크의_진행중_실행은_다음_유료단계에서_멈춘다(monkeypatch) -> None:
    pipeline = 링크단계가짜조사(단계전=_닫을_단계(1, _링크를_닫는다))
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)

    with _링크손님() as client:
        job_id = _조사를_돌린다(client)

    # 시작 시점에는 살아 있던 링크다. 단계마다 다시 읽지 않으면 3단계가 다 돈다.
    assert pipeline.유료단계_진입 == 1, "닫힌 링크인데 다음 유료 단계가 그대로 실행됐다"
    result = job_runtime._JOBS[job_id].result
    assert result.outcome is Outcome.FAILED
    assert result.message == job_runtime.LINK_REVOKED_RUN_STOPPED_MESSAGE
    assert _링크이력(job_id).stop_reason == job_runtime.LINK_STOP_REASON_REVOKED


def test_만료된_링크도_다음_유료단계에서_멈춘다(monkeypatch) -> None:
    pipeline = 링크단계가짜조사(단계전=_닫을_단계(1, _링크_기간을_넘긴다))
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)

    with _링크손님() as client:
        job_id = _조사를_돌린다(client)

    assert pipeline.유료단계_진입 == 1
    result = job_runtime._JOBS[job_id].result
    assert result.outcome is Outcome.FAILED
    assert result.message == job_runtime.LINK_EXPIRED_RUN_STOPPED_MESSAGE
    assert _링크이력(job_id).stop_reason == job_runtime.LINK_STOP_REASON_EXPIRED


def test_멈춘_뒤_새_유료_호출이_0이다(monkeypatch) -> None:
    pipeline = 링크단계가짜조사(단계수=5, 단계전=_닫을_단계(2, _링크를_닫는다))
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)

    with _링크손님() as client:
        _조사를_돌린다(client)

    assert pipeline.닫힌시점_진입 == 2, "링크를 닫기 전 단계 수를 잘못 쟀다"
    assert pipeline.유료단계_진입 == pipeline.닫힌시점_진입, (
        "링크가 닫힌 뒤에도 새 유료 호출이 있었다"
    )


def test_이미_끝난_단계의_비용은_그대로_남는다(monkeypatch) -> None:
    pipeline = 링크단계가짜조사(단계전=_닫을_단계(2, _링크를_닫는다))
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)

    with _링크손님() as client:
        job_id = _조사를_돌린다(client)

    assert pipeline.유료단계_진입 == 2
    # 회사 식별에 이미 쓴 10원은 되돌리지 않는다.
    assert _링크이력(job_id).internal_ai_cost_krw == pytest.approx(_식별비용)
    with storage_db.connect() as conn:
        snapshot = spend_store.load_day(conn, clock.today_kst())
        unresolved = spend_store.load_unresolved_day(conn, clock.today_kst())
    assert snapshot.by_run[job_id] >= _식별비용
    # 이미 연 유료 단계의 예약은 0원으로 지우지 않고 미확정으로 남긴다.
    assert spend_store.bucket_id(_LINK) in unresolved


def test_링크_상태를_못_읽으면_유료단계에_들어가지_않되_닫혔다고_말하지_않는다(
    monkeypatch,
) -> None:
    깨진다 = {"켜짐": False}
    원본 = share_store.load_by_hash

    def 깨진_읽기(conn, key_hash):
        if 깨진다["켜짐"]:
            raise sqlite3.OperationalError("시험용 저장소 읽기 실패")
        return 원본(conn, key_hash)

    def 저장소를_깨뜨린다(단계: int, pipeline: 링크단계가짜조사) -> None:
        if 단계 == 1:
            깨진다["켜짐"] = True
            pipeline.닫힌시점_진입 = pipeline.유료단계_진입

    pipeline = 링크단계가짜조사(단계전=저장소를_깨뜨린다)
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)
    monkeypatch.setattr(share_store, "load_by_hash", 깨진_읽기)

    with _링크손님() as client:
        job_id = _조사를_돌린다(client)

    assert pipeline.유료단계_진입 == 1, "상태를 모르는데 다음 유료 단계에 들어갔다"
    result = job_runtime._JOBS[job_id].result
    assert result.outcome is Outcome.FAILED
    assert result.message == job_runtime.LINK_STATE_UNKNOWN_RUN_STOPPED_MESSAGE
    # 「닫혔다」고 단정하지 않는다 — 모른다고만 말한다.
    assert "중단되어" not in result.message
    assert "기간이 지나" not in result.message
    assert _링크이력(job_id).stop_reason == job_runtime.LINK_STOP_REASON_UNKNOWN


def test_링크_없는_job은_훅이_아무것도_하지_않는다(monkeypatch) -> None:
    읽기수 = _읽기_세는_대역(monkeypatch)
    pipeline = 링크단계가짜조사()
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)

    with _회원손님("member-gs11@example.com") as client:
        job_id = _조사를_돌린다(client)

    job = job_runtime._JOBS[job_id]
    assert job.share_link_hash == ""
    assert 읽기수["횟수"] == 0, "링크 없는 갈래에서 훅이 저장소를 읽었다"
    assert pipeline.유료단계_진입 == 3
    assert job.result.outcome is Outcome.GATE_STOPPED
    assert job.result.message == ""


@pytest.mark.parametrize("닫는_단계", [0, 1, 2, 3])
def test_링크_상태는_유료단계마다_다시_읽는다(monkeypatch, 닫는_단계: int) -> None:
    """어느 단계에서 닫아도 «그 단계»에서 멈춘다 = 시작 판정을 재사용하지 않는다.

    시작 시점 판정 한 벌을 재사용하면 어느 경우든 4단계가 다 돌고, 캐시를 넣으면
    닫힌 뒤의 단계가 통과한다. 저장소 읽기 횟수를 세지 않는다 — 예산 검사도 같은
    행을 읽어서(`paid_runtime._link_total_budget_inputs`) 읽기 수는 증거가 못 된다.
    """

    pipeline = 링크단계가짜조사(단계수=4, 단계전=_닫을_단계(닫는_단계, _링크를_닫는다))
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)

    with _링크손님() as client:
        _조사를_돌린다(client)

    assert pipeline.닫힌시점_진입 == 닫는_단계
    assert pipeline.유료단계_진입 == 닫는_단계


def test_화면_문구에_내부_용어가_없다(monkeypatch) -> None:
    """링크가 살아 있는 «상태 확인 불가» 갈래로 실제 화면까지 그려 본다.

    철회·만료 갈래는 손님이 열람 권한 자체를 잃어(`report_access._link_owns`) 결과
    화면 대신 열람 불가 안내를 본다 — 그 사실은 아래 시험이 따로 못 박는다.
    """

    깨진다 = {"켜짐": False}
    원본 = share_store.load_by_hash

    def 깨진_읽기(conn, key_hash):
        if 깨진다["켜짐"]:
            raise sqlite3.OperationalError("시험용 저장소 읽기 실패")
        return 원본(conn, key_hash)

    def 저장소를_깨뜨린다(단계: int, pipeline: 링크단계가짜조사) -> None:
        del pipeline
        if 단계 == 1:
            깨진다["켜짐"] = True

    pipeline = 링크단계가짜조사(단계전=저장소를_깨뜨린다)
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)
    monkeypatch.setattr(share_store, "load_by_hash", 깨진_읽기)

    with _링크손님() as client:
        job_id = _조사를_돌린다(client)
        깨진다["켜짐"] = False
        화면 = client.get(f"/result/{job_id}")

    assert 화면.status_code == 200
    보이는글자 = visible_text(화면.text)
    assert job_runtime.LINK_STATE_UNKNOWN_RUN_STOPPED_MESSAGE in 보이는글자
    낮춘화면 = 보이는글자.lower()
    for 금지 in _금지어:
        assert 금지 not in 낮춘화면, f"사용자 화면에 내부 용어 {금지!r}가 보인다"
    for 문구 in (
        job_runtime.LINK_REVOKED_RUN_STOPPED_MESSAGE,
        job_runtime.LINK_EXPIRED_RUN_STOPPED_MESSAGE,
        job_runtime.LINK_STATE_UNKNOWN_RUN_STOPPED_MESSAGE,
    ):
        낮춘문구 = 문구.lower()
        for 금지 in _금지어:
            assert 금지 not in 낮춘문구, f"안내 문구에 내부 용어 {금지!r}가 있다"
        assert len(문구) <= 60, f"안내는 짧아야 한다: {문구!r}"


def test_닫힌_링크_손님은_결과화면_대신_열람불가_안내를_본다(monkeypatch) -> None:
    """실측 기록 — 링크를 닫으면 그 손님은 결과 주소부터 열 수 없다.

    그래서 「멈췄습니다」 안내는 손님 화면이 아니라 관리자 화면·이력에서 읽힌다.
    이 시험은 그 사실을 못 박아, 나중에 손님에게 사유를 보여 주기로 정하면 여기부터
    빨간불이 나게 한다.
    """

    pipeline = 링크단계가짜조사(단계전=_닫을_단계(1, _링크를_닫는다))
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)

    with _링크손님() as client:
        job_id = _조사를_돌린다(client)
        화면 = client.get(f"/result/{job_id}")

    assert 화면.status_code == 404
    보이는글자 = visible_text(화면.text)
    assert job_runtime.LINK_REVOKED_RUN_STOPPED_MESSAGE not in 보이는글자
    낮춘화면 = 보이는글자.lower()
    for 금지 in _금지어:
        assert 금지 not in 낮춘화면, f"열람 불가 안내에 내부 용어 {금지!r}가 보인다"


def test_관리자_이력_사유코드는_ASCII다() -> None:
    for 코드 in (
        job_runtime.LINK_STOP_REASON_REVOKED,
        job_runtime.LINK_STOP_REASON_EXPIRED,
        job_runtime.LINK_STOP_REASON_UNKNOWN,
    ):
        assert 코드.isascii(), f"감사행 CHECK는 ASCII만 받는다: {코드!r}"
        assert re.fullmatch(r"[A-Za-z0-9_.:-]+", 코드), 코드
