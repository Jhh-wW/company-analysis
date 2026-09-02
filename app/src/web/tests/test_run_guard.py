"""`/run`이 «돈과 횟수»를 실제로 막는지 못 박는다 (문제로그 P-92).

★ 이 시험이 지키는 것 — **인터넷에 올려도 돈이 무제한으로 새지 않는다.**
  `budget/logic.py`의 시험은 «판단»이 맞는지만 본다. 판단이 맞아도
  **`/run`에 안 걸려 있으면 아무 소용이 없다.** 그 연결을 여기서 본다.

★ 화면에서 버튼을 숨기는 것은 방어가 아니다 — 주소를 직접 부르면 그만이다.
  그래서 시험도 «화면을 거치지 않고» `/run`을 직접 부른다. 공격자와 같은 방식이다.
"""

from __future__ import annotations

import datetime as dt
import re
import time
import uuid

import pytest
from fastapi.testclient import TestClient

from src.core import clock
from src.features.auth import logic as auth_logic
from src.features.admin_dashboard import store as dashboard_store
from src.features.budget import logic as budget_logic
from src.features.budget import spend_store
from src.features.budget.constants import (
    MAX_CONCURRENT_PER_LINK,
    MAX_CONCURRENT_RUNS,
    RATE_MAX_RUNS,
)
from src.features.pipeline.port import CompanyCard
from src.features.sharelink import logic as share_logic
from src.features.sharelink import store as share_store
from src.features.sharelink import tracks as share_tracks
from src.features.sharelink.constants import (
    KEY_COOKIE_NAME,
    PER_LINK_DAILY_BUDGET_KRW,
    PUBLIC_BUCKET,
)
from src.features.storage import db as storage_db
from src.features.storage import constants as storage_constants

_열쇠 = "a1b2c3d4e5f60718a1b2c3d4e5f60718"
from src.features.pipeline.demo import DemoPipeline
from src.web import main
from src.web import job_runtime, paid_runtime, request_helpers, runtime


@pytest.fixture
def client() -> TestClient:
    return TestClient(main.app)


def _폼(회사: str = "우리엔") -> dict:
    """회사 분석의 원 입력 한 벌."""
    return {
        "company": 회사,
        "job": "영업",
        "region": "서울",
        "posting_text": "x",
    }


def _조사시작(
    client: TestClient,
    회사: str = "우리엔",
    *,
    headers: dict[str, str] | None = None,
):
    form = _폼(회사)
    link_key = client.cookies.get(KEY_COOKIE_NAME) or ""
    if link_key:
        form["csrf_token"] = auth_logic.csrf_token_for_session(link_key)
    if isinstance(runtime._PIPELINE, DemoPipeline):
        confirm = client.post("/confirm", data=form, headers=headers)
        token = re.search(
            r'name="paid_attempt_token" value="([^"]+)"', confirm.text
        )
        assert token is not None
        form["paid_attempt_token"] = token.group(1)
    else:
        token = uuid.uuid4().hex
        share_key = link_key or PUBLIC_BUCKET
        user_input = request_helpers.company_analysis_input(
            company=form["company"],
            region=form["region"],
        )
        job_runtime._PAID_ATTEMPTS[token] = job_runtime.PaidAttempt(
            token=token,
            run_id=f"run-guard-{token}",
            user_input=user_input,
            card=CompanyCard(
                legal_name=user_input.company,
                typed_name=user_input.company,
                address="서울",
                ceo="",
                founded="",
                ref="run-guard",
            ),
            share_key=share_key,
            bucket_id=spend_store.bucket_id(share_key),
            lookup_cost_krw=0.0,
            models=(),
            elapsed_sec=0.0,
            created_at=time.monotonic(),
        )
        form["paid_attempt_token"] = token
    return client.post(
        "/run", data=form, headers=headers, follow_redirects=False
    )


def _열쇠로_들어온다(client: TestClient) -> None:
    """열쇠 링크 손님으로 만든다 (하루 3,000원 갈래).

    ★ 열쇠 없이 들어온 손님은 이제 **상한이 0원**이라(P-95),
      「예산을 다 썼다」를 시험하려면 «몫이 있는 갈래»여야 한다.
    """
    with storage_db.connect() as conn:
        share_store.insert_new(
            conn, key=_열쇠, company="우리엔", job="영업",
            now_iso="2026-08-16T10:00:00",
        )
    client.cookies.set(KEY_COOKIE_NAME, _열쇠)


def _예산을_다_쓴다(monkeypatch, 통장: str = _열쇠,
                   금액: float = PER_LINK_DAILY_BUDGET_KRW) -> None:
    """그 통장의 «오늘 몫»을 다 쓴 상태로 만든다."""
    오늘 = clock.today_kst()
    다_쓴 = share_logic.add_spend(
        share_logic.DailySpend(day=오늘), 통장, 오늘, 금액
    )
    monkeypatch.setattr(paid_runtime, "_LINK_SPEND", 다_쓴)


def test_전역_점검_중에는_새_생성을_막는다(monkeypatch, tmp_path):
    monkeypatch.setenv(storage_constants.ENV_DB_PATH, str(tmp_path / "storage.db"))
    monkeypatch.setattr(runtime, "_PIPELINE", DemoPipeline())
    with storage_db.connect() as conn:
        dashboard_store.set_service_state(
            conn,
            status=dashboard_store.SERVICE_MAINTENANCE,
            cause="same root cause",
            impact="new reports blocked",
            next_action="manual restart only",
            actor_email="admin@example.com",
            now_iso="2026-08-22T10:00:00+09:00",
        )
    with TestClient(main.app) as isolated_client:
        response = _조사시작(isolated_client)

    assert response.status_code == 429
    assert "점검 중" in response.text


class _가짜진짜알맹이:
    """`DemoPipeline`이 아니어야 «돈이 드는» 것으로 본다."""

    def run(self, *args, **kwargs):                     # pragma: no cover - 안 부른다
        raise AssertionError("막혔어야 하는데 조사가 시작됐습니다")


# ══════════════════════════════════════════════════════════
# ① 예산 — 다 쓰면 «진짜 조사»를 막는다
# ══════════════════════════════════════════════════════════


def test_예산을_다_쓰면_조사를_거절한다(client: TestClient, monkeypatch):
    """★ P-92 그 자체. 이게 없으면 배포하는 순간 돈이 무제한으로 샌다."""
    monkeypatch.setattr(runtime, "_PIPELINE", _가짜진짜알맹이())
    _열쇠로_들어온다(client)
    _예산을_다_쓴다(monkeypatch)

    response = _조사시작(client)

    assert response.status_code == 429
    assert "이 링크로 돌릴 수 있는 새 조사를 모두 사용" in response.text
    # ★ 「이미 만든 보고서는 계속 열린다」를 «반드시» 같이 알린다 (2026-08-16 사용자 결정).
    #   안 알리면 그냥 막힌 줄 알고, 포트폴리오의 핵심을 못 본 채 떠난다.
    assert "이미 만들어 둔 보고서는" in response.text


def test_막혔을_때_고장이_아니라고_말한다(client: TestClient, monkeypatch):
    """★ 「오류」로 보이면 사용자는 못 쓰는 물건이라 판단하고 떠난다."""
    monkeypatch.setattr(runtime, "_PIPELINE", _가짜진짜알맹이())
    _열쇠로_들어온다(client)
    _예산을_다_쓴다(monkeypatch)

    text = _조사시작(client).text

    assert "고장이 아닙니다" in text
    assert "다른 회사 둘러보기" in text, "막다른 길을 만들면 안 된다"


def test_원장이_깨져_막혔을_때는_고장이_아니라고_말하지_않는다(
    client: TestClient, monkeypatch
):
    """★ 2026-08-28. 「고장이 아닙니다」를 «모든» 차단에 붙이고 있었다.

    비용 원장을 못 읽어 막힌 사용자도 그 말을 봤다 — **사실이 아니다.**
    그건 사람이 원장을 확인해야 풀리는 진짜 고장이고, 그때까지 새 조사는 안 된다.
    게다가 문의 번호가 없어 **사용자가 이 순간을 신고할 방법이 없었다.**
    """
    monkeypatch.setattr(runtime, "_PIPELINE", _가짜진짜알맹이())
    _열쇠로_들어온다(client)
    monkeypatch.setattr(paid_runtime, "_BUDGET_STORE_HEALTHY", False)

    response = _조사시작(client)
    text = response.text

    assert response.status_code == 429
    assert "고장이 아닙니다" not in text, "★ 고장인데 아니라고 말하고 있다"
    assert "새 조사가 멈췄습니다" in text, "제목이 「잠시 기다려」면 기다리면 풀린다고 읽힌다"
    assert "잠시 기다려" not in text
    assert "문의 번호" in text, "★ 신고할 번호가 없으면 관리자에게 안 닿는다"
    # 막다른 길은 여전히 만들지 않는다.
    assert "다른 회사 둘러보기" in text
    # ★ 같은 말을 두 번 하지 않는다 (2026-08-28 — 세 번 하고 있었다).
    assert text.count("관리자가 확인해야 다시 열립니다") == 1
    assert text.count("그대로 열립니다") == 1
    # ★ 없는 기능을 가리키지 않는다 — 첫 화면에 「회사 버튼」은 없다(실측).
    assert "회사 버튼" not in text


def test_고장으로_보는_차단_종류가_그대로다() -> None:
    """★ 여기에 「예산 소진」을 넣으면 정상 동작을 고장이라고 말하게 된다."""
    assert request_helpers.THROTTLE_FAULT_KINDS == frozenset(
        {"budget-store", "budget-unresolved", "member-usage-store"}
    )


def test_데모는_예산을_다_써도_돈다(client: TestClient, monkeypatch):
    """★ 반대 방향 — 데모는 0원인데 막으면 «공짜 화면»이 멈춘다."""
    monkeypatch.setattr(runtime, "_PIPELINE", DemoPipeline())
    _열쇠로_들어온다(client)
    _예산을_다_쓴다(monkeypatch, 금액=99999.0)

    assert _조사시작(client).status_code == 303


def test_날이_바뀌면_예산이_되살아난다(client: TestClient, monkeypatch):
    """어제 다 썼다고 오늘까지 막히면 안 된다."""
    monkeypatch.setattr(runtime, "_PIPELINE", DemoPipeline())
    _열쇠로_들어온다(client)
    어제 = clock.today_kst() - dt.timedelta(days=1)
    다_쓴_장부 = share_logic.add_spend(
        share_logic.DailySpend(day=어제), _열쇠, 어제, PER_LINK_DAILY_BUDGET_KRW
    )
    monkeypatch.setattr(paid_runtime, "_LINK_SPEND", 다_쓴_장부)

    assert _조사시작(client).status_code == 303


# ══════════════════════════════════════════════════════════
# ② 횟수 — 몰아치면 잠깐 쉬게 한다
# ══════════════════════════════════════════════════════════


def test_짧은_시간에_몰아치면_막는다(client: TestClient, monkeypatch):
    monkeypatch.setattr(runtime, "_PIPELINE", DemoPipeline())

    코드 = [_조사시작(client).status_code for _ in range(RATE_MAX_RUNS + 1)]

    assert 코드[:RATE_MAX_RUNS] == [303] * RATE_MAX_RUNS
    assert 코드[-1] == 429


def test_전달_IP를_바꿔도_같은_권한통장의_횟수제한을_우회하지_못한다(
    client: TestClient, monkeypatch
):
    monkeypatch.setattr(runtime, "_PIPELINE", DemoPipeline())
    codes = []
    for index in range(RATE_MAX_RUNS + 1):
        response = _조사시작(
            client,
            headers={"X-Forwarded-For": f"203.0.113.{index + 1}"},
        )
        codes.append(response.status_code)

    assert codes[:RATE_MAX_RUNS] == [303] * RATE_MAX_RUNS
    assert codes[-1] == 429
    assert len(paid_runtime._RATE_HISTORY.starts) == 1


def test_막힌_뒤에도_화면_보기는_된다(client: TestClient, monkeypatch):
    """★ 조사를 막는 것과 «이미 만든 보고서를 보는 것»은 다른 일이다."""
    monkeypatch.setattr(runtime, "_PIPELINE", DemoPipeline())
    for _ in range(RATE_MAX_RUNS + 1):
        _조사시작(client)

    assert client.get("/").status_code == 200


# ══════════════════════════════════════════════════════════
# ③ 동시 실행 — 예산이 «넘치는 폭»을 묶는다
# ══════════════════════════════════════════════════════════


def test_동시_실행이_꽉_차면_막는다(client: TestClient, monkeypatch):
    monkeypatch.setattr(runtime, "_PIPELINE", DemoPipeline())
    monkeypatch.setattr(paid_runtime, "_RUNNING", MAX_CONCURRENT_RUNS)

    response = _조사시작(client)

    assert response.status_code == 429
    assert "진행 중" in response.text


def test_서로_다른_로그인_사용자_다섯명까지_자리를_잡는다():
    자리 = [
        paid_runtime._reserve_run_slot(
            share_tracks.Track.MEMBER, f"user:member-{index}@example.com"
        )
        for index in range(MAX_CONCURRENT_RUNS)
    ]

    assert all(자리)
    assert paid_runtime._reserve_run_slot(
        share_tracks.Track.MEMBER, "user:sixth@example.com"
    ) is None

    for bucket_id in 자리:
        paid_runtime._release_run_slot(bucket_id or "")
    assert paid_runtime._RUNNING == 0
    assert paid_runtime._RUNNING_BY_BUCKET == {}


def test_링크_동시_실행_상한은_리터럴_1이다():
    """★ D-G9(2026-09-02) — 초대 링크 하나가 동시에 쥐는 자리는 «한 자리»다.

    누적 3,000원짜리 통장에 900원 본조사를 세 건 동시에 여는 것은 의미가 없다.
    리터럴 1로 못 박는다 — 상한을 다른 상수와 견주면 값이 조용히 3으로
    되돌아가도 시험이 그대로 통과한다.
    """
    assert MAX_CONCURRENT_PER_LINK == 1


def test_같은_초대링크는_한_자리만_잡고_두번째부터_거절한다():
    """★ 상한 경계 그 자체 — 1번째는 잡히고 2번째는 거절된다.

    D-G9(2026-09-02)로 기대값을 3→1로 이전했다.
    이전 이름: `test_같은_초대링크는_세명까지_자리를_잡는다`.
    """
    첫자리 = paid_runtime._reserve_run_slot(share_tracks.Track.LINK, _열쇠)

    assert 첫자리
    assert paid_runtime._reserve_run_slot(share_tracks.Track.LINK, _열쇠) is None

    paid_runtime._release_run_slot(첫자리 or "")
    assert paid_runtime._RUNNING == 0
    assert paid_runtime._RUNNING_BY_BUCKET == {}


def test_자리를_돌려주면_같은_링크가_다시_한_자리를_잡는다():
    """★ 음성 대조 — 1로 줄인 것이 «영구 잠금»이 되면 그건 고장이다."""
    첫자리 = paid_runtime._reserve_run_slot(share_tracks.Track.LINK, _열쇠)
    assert 첫자리
    paid_runtime._release_run_slot(첫자리 or "")

    다음자리 = paid_runtime._reserve_run_slot(share_tracks.Track.LINK, _열쇠)
    assert 다음자리

    paid_runtime._release_run_slot(다음자리 or "")
    assert paid_runtime._RUNNING == 0
    assert paid_runtime._RUNNING_BY_BUCKET == {}


# ══════════════════════════════════════════════════════════
# ④ 거절당한 요청은 횟수를 안 깎는다
# ══════════════════════════════════════════════════════════


def test_예산으로_거절당해도_횟수는_안_깎인다(client: TestClient, monkeypatch):
    """★ 합쳐서 세면 「돈도 안 썼는데 차단」이 된다."""
    monkeypatch.setattr(runtime, "_PIPELINE", _가짜진짜알맹이())
    _열쇠로_들어온다(client)
    _예산을_다_쓴다(monkeypatch)

    for _ in range(RATE_MAX_RUNS + 3):
        _조사시작(client)

    assert paid_runtime._RATE_HISTORY.starts == {}


# ══════════════════════════════════════════════════════════
# ⑤ 메모리 청소 — `_JOBS`가 영원히 쌓이지 않는다
# ══════════════════════════════════════════════════════════


def test_끝난_지_오래된_조사는_치운다():
    """★ 안 치우면 데모(공짜)에서도 서버가 죽을 때까지 쌓인다."""
    from src.features.pipeline.port import CompanyCard, UserInput

    job_runtime._JOBS.clear()
    낡은 = job_runtime.Job(
        job_id="old1",
        user_input=UserInput(company="a", job="b", region=""),
        card=CompanyCard(legal_name="a", typed_name="a", address="", ceo="", founded=""),
        finished=True,
        finished_at=0.0,
    )
    낡은.finished_at = 100.0
    job_runtime._JOBS["old1"] = 낡은

    job_runtime._sweep_jobs(100.0 + 3601)

    assert "old1" not in job_runtime._JOBS


def test_돌고_있는_조사는_안_치운다():
    """★ 돌고 있는 것을 치우면 사용자의 진행 화면이 통째로 사라진다."""
    from src.features.pipeline.port import CompanyCard, UserInput

    job_runtime._JOBS.clear()
    도는중 = job_runtime.Job(
        job_id="run1",
        user_input=UserInput(company="a", job="b", region=""),
        card=CompanyCard(legal_name="a", typed_name="a", address="", ceo="", founded=""),
        finished=False,
    )
    job_runtime._JOBS["run1"] = 도는중

    job_runtime._sweep_jobs(999999.0)

    assert "run1" in job_runtime._JOBS
