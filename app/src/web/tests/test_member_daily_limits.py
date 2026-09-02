"""회원별 하루 한도가 «성공 건수와 비용 둘 다»에 실제로 닿는지 본다.

★ 반쪽이면 안 되는 이유 — 성공 건수만 회원값을 따르면, 한도를 1건으로 낮춘 친구도
  실패를 반복하며 하루 3,000원을 그대로 쓴다. 비용만 회원값을 따르면, 한도를 5건으로
  올린 친구가 4번째부터 「3건 다 썼다」로 막힌다. 두 갈래가 같이 움직여야 한다.

★ 비용 쪽 정본 자리는 **예약을 커밋하는 transaction**이다. 사전 확인만 바꾸면
  동시 요청이 옛 숫자를 공유한다. 그래서 이 시험은 `_track_of()`가 돌려준 상한이
  `paid_runtime._begin_paid_phase()`(→ `state_machine.begin_phase`)까지 그대로
  전달되는지를 그 함수로 직접 확인한다.

★ 기준값은 리터럴로 적는다 — 생산 상수를 가져와 자기 자신과 비교하면 값이 내려가는
  회귀를 못 잡는다. 지금 계약: 회원 기본 하루 3,000원 · 성공 3건 · 본조사 예약 900원.
"""

from __future__ import annotations

import asyncio
import hashlib
import re

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from src.core import clock
from src.features.admin_dashboard import store as dashboard_store
from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.budget.constants import SPEND_PHASE_PIPELINE
from src.features.observability import admin_audit_store
from src.features.pipeline.demo import DemoPipeline
from src.features.pipeline.port import CompanyCard, UserInput
from src.features.sharelink import allowlist as share_allow
from src.features.sharelink import tracks as share_tracks
from src.features.storage import db as storage_db
from src.web import (
    deployment_mode,
    job_runtime,
    main,
    paid_runtime,
    request_helpers,
    runtime,
)

_친구 = "friend@example.com"
_다른친구 = "other@example.com"
_시각 = "2026-09-02T10:00:00+09:00"

#: 본조사 한 번이 미리 잡는 예상액. `budget/constants.py`의 계약값과 같은 900원을
#: 리터럴로 적는다 — 상수를 import해 비교하면 그 값이 바뀌어도 시험이 조용히 따라간다.
_본조사_예약액 = 900.0


def _주체(email: str) -> str:
    digest = hashlib.sha256(email.lower().encode("utf-8")).hexdigest()[:24]
    return f"google:test-{digest}"


def _친구로_들어온_요청(email: str) -> Request:
    session = auth_logic.create_session(email, False, subject=_주체(email))
    쿠키 = f"{auth_constants.SESSION_COOKIE_NAME}={session.token}".encode()
    return Request({"type": "http", "headers": [(b"cookie", 쿠키)]})


def _초대한다(email: str) -> None:
    with storage_db.connect() as conn:
        assert share_allow.invite(conn, email=email, note="", now_iso=_시각)


def _한도를_정한다(email: str, *, 건수: int | None, 금액: float | None) -> None:
    with storage_db.connect() as conn:
        assert share_allow.set_limits(
            conn, email=email, daily_success_limit=건수, daily_budget_krw=금액,
            reason="시험", now_iso=_시각,
        )


def _본조사를_예약한다(run_id: str, *, bucket: str, cap: float | None) -> bool:
    """실제 유료 예약 커밋 자리를 그대로 통과시켜 본다. 거절되면 False."""
    ticket = paid_runtime._begin_paid_phase(
        run_id=run_id, phase=SPEND_PHASE_PIPELINE, share_key=bucket, cap_krw=cap
    )
    return ticket is not None


@pytest.fixture(autouse=True)
def _유료원장_준비():
    """새 attempt 원장을 켜 두고 오늘 치 메모리 장부를 갓 켠 서버처럼 만든다."""
    paid_runtime.prepare_budget_state_machine_cutover()
    paid_runtime._seed_ledger()
    yield


# ══════════════════════════════════════════════════════════
# ① ★ 핵심 — 성공 건수와 비용이 «둘 다» 회원값을 따른다
# ══════════════════════════════════════════════════════════


def test_회원별_한도는_성공건수와_비용_둘_다_적용된다():
    """성공 2건에서 3번째가 거절되고, 하루 900원이면 본조사 두 번째가 거절된다."""
    _초대한다(_친구)
    _한도를_정한다(_친구, 건수=2, 금액=900.0)
    오늘 = clock.today_kst().isoformat()

    # (1) 비용 — 갈래 판정이 회원값을 상한으로 돌려준다.
    track, bucket, cap = request_helpers._track_of(_친구로_들어온_요청(_친구))
    assert track is share_tracks.Track.MEMBER
    assert bucket == f"user:{_친구}"
    assert cap == 900.0

    # (2) 비용 — 그 상한이 예약 커밋 자리까지 간다. 900원짜리 본조사는 한 번만
    #     들어가고, 두 번째는 상한을 넘으므로 거절된다(기본 3,000원이면 통과했을 것).
    assert _본조사를_예약한다("run-cost-0", bucket=bucket, cap=cap)
    assert not _본조사를_예약한다("run-cost-1", bucket=bucket, cap=cap)

    # (3) 성공 건수 — 2건까지만 예약된다.
    with storage_db.connect() as conn:
        for number in range(2):
            assert dashboard_store.reserve_member_run(
                conn, run_id=f"run-success-{number}", actor_email=_친구,
                day=오늘, now_iso=_시각,
            )
        assert not dashboard_store.reserve_member_run(
            conn, run_id="run-success-2", actor_email=_친구, day=오늘, now_iso=_시각,
        )


def test_한도를_안_정한_친구는_기존_3000원과_3건_그대로다():
    """★ 반대 경우 시험 — 회원별 한도를 넣었다고 기존 계약이 흔들리면 안 된다."""
    _초대한다(_친구)
    오늘 = clock.today_kst().isoformat()

    track, bucket, cap = request_helpers._track_of(_친구로_들어온_요청(_친구))
    assert track is share_tracks.Track.MEMBER
    assert cap == 3000.0

    # 3,000원이면 900원짜리 본조사가 세 번 들어가고 네 번째가 거절된다.
    for number in range(3):
        assert _본조사를_예약한다(f"run-cost-{number}", bucket=bucket, cap=cap)
    assert not _본조사를_예약한다("run-cost-3", bucket=bucket, cap=cap)

    with storage_db.connect() as conn:
        for number in range(3):
            assert dashboard_store.reserve_member_run(
                conn, run_id=f"run-success-{number}", actor_email=_친구,
                day=오늘, now_iso=_시각,
            )
        assert not dashboard_store.reserve_member_run(
            conn, run_id="run-success-3", actor_email=_친구, day=오늘, now_iso=_시각,
        )


def test_한도를_올린_친구는_비용도_성공건수도_같이_올라간다():
    _초대한다(_친구)
    _한도를_정한다(_친구, 건수=5, 금액=4500.0)
    오늘 = clock.today_kst().isoformat()

    _track, bucket, cap = request_helpers._track_of(_친구로_들어온_요청(_친구))
    assert cap == 4500.0

    # 4,500원이면 900원 본조사가 다섯 번 들어간다(3,000원이었다면 네 번째에서 막혔다).
    for number in range(5):
        assert _본조사를_예약한다(f"run-cost-{number}", bucket=bucket, cap=cap)
    assert not _본조사를_예약한다("run-cost-5", bucket=bucket, cap=cap)

    with storage_db.connect() as conn:
        for number in range(5):
            assert dashboard_store.reserve_member_run(
                conn, run_id=f"run-success-{number}", actor_email=_친구,
                day=오늘, now_iso=_시각,
            )
        assert not dashboard_store.reserve_member_run(
            conn, run_id="run-success-5", actor_email=_친구, day=오늘, now_iso=_시각,
        )


def test_다른_회원의_한도는_다른_회원에_영향_없다():
    _초대한다(_친구)
    _초대한다(_다른친구)
    _한도를_정한다(_친구, 건수=1, 금액=900.0)

    _t1, _b1, 낮춘쪽_상한 = request_helpers._track_of(_친구로_들어온_요청(_친구))
    _t2, _b2, 안건드린쪽_상한 = request_helpers._track_of(
        _친구로_들어온_요청(_다른친구)
    )

    assert 낮춘쪽_상한 == 900.0
    assert 안건드린쪽_상한 == 3000.0


def test_비용_예약은_회원마다_다른_통장에서_따로_센다():
    """한 친구가 자기 몫을 다 써도 옆 친구의 예약은 그대로 열린다."""
    _초대한다(_친구)
    _초대한다(_다른친구)
    _한도를_정한다(_친구, 건수=3, 금액=900.0)

    _t1, 낮춘쪽_통장, 낮춘쪽_상한 = request_helpers._track_of(
        _친구로_들어온_요청(_친구)
    )
    _t2, 옆_통장, 옆_상한 = request_helpers._track_of(_친구로_들어온_요청(_다른친구))

    assert _본조사를_예약한다("run-a", bucket=낮춘쪽_통장, cap=낮춘쪽_상한)
    assert not _본조사를_예약한다("run-b", bucket=낮춘쪽_통장, cap=낮춘쪽_상한)
    assert _본조사를_예약한다("run-c", bucket=옆_통장, cap=옆_상한)


# ══════════════════════════════════════════════════════════
# ② 다른 갈래는 그대로 — 회원 한도가 새어 나가지 않는다
# ══════════════════════════════════════════════════════════


def test_명단_밖_로그인은_회원_한도를_읽지_않고_0원인_손님이다():
    """★ 그대로 — 로그인만 한 사람에게 회원 한도가 붙으면 안 된다."""
    track, _bucket, cap = request_helpers._track_of(
        _친구로_들어온_요청("stranger@example.com")
    )

    assert track is share_tracks.Track.PUBLIC
    assert cap == 0.0


def test_철회한_친구는_한도가_남아_있어도_회원이_아니다():
    """뺀 사람의 한도 행은 남지만 갈래는 PUBLIC이라 상한이 0원이다."""
    _초대한다(_친구)
    _한도를_정한다(_친구, 건수=9, 금액=9000.0)
    with storage_db.connect() as conn:
        assert share_allow.revoke(conn, _친구, now_iso=_시각)

    track, _bucket, cap = request_helpers._track_of(_친구로_들어온_요청(_친구))

    assert track is share_tracks.Track.PUBLIC
    assert cap == 0.0


def _막힌_화면_글자(email: str) -> str:
    """실행 시작 전 사전 확인(_guard_run)이 돌려준 화면의 글자."""
    막힌화면 = request_helpers._guard_run(_친구로_들어온_요청(email))
    assert 막힌화면 is not None
    return 막힌화면.body.decode("utf-8")


def _성공을_다_쓴다(email: str, 건수: int) -> None:
    오늘 = clock.today_kst().isoformat()
    with storage_db.connect() as conn:
        for number in range(건수):
            assert dashboard_store.reserve_member_run(
                conn, run_id=f"{email}-{number}", actor_email=email,
                day=오늘, now_iso=_시각,
            )


def test_한도를_다_쓴_친구는_사전_확인에서_자기_한도로_안내받는다():
    """★ 사전 확인 겹을 «따로» 지킨다.

    예약 자리(reserve_member_run)만 고치면 이 겹의 안내 문구가 옛 3건으로 남아
    한도를 7건으로 올린 친구가 「3건 다 썼다」는 틀린 말을 본다.
    """
    _초대한다(_친구)
    _한도를_정한다(_친구, 건수=7, 금액=None)
    _성공을_다_쓴다(_친구, 7)

    글자 = _막힌_화면_글자(_친구)

    assert "오늘 성공한 보고서 7건을 모두 사용했습니다" in 글자
    assert "3건을 모두 사용했습니다" not in 글자


def test_한도를_안_정한_친구는_사전_확인에서_기존_3건으로_안내받는다():
    """★ 반대 경우 시험 — 회원별 한도를 넣었다고 기존 안내가 바뀌면 안 된다."""
    _초대한다(_친구)
    _성공을_다_쓴다(_친구, 3)

    글자 = _막힌_화면_글자(_친구)

    assert "오늘 성공한 보고서 3건을 모두 사용했습니다" in 글자


def test_한도가_남은_친구는_사전_확인을_통과한다():
    """막는 겹이 «항상» 막으면 시험이 초록이어도 기능이 죽은 것이다."""
    _초대한다(_친구)
    _한도를_정한다(_친구, 건수=7, 금액=None)
    _성공을_다_쓴다(_친구, 6)

    assert request_helpers._guard_run(_친구로_들어온_요청(_친구)) is None


def _실행번호(꼬리: str) -> str:
    """실행 번호는 32자리 16진수여야 한다 — `bind_member_run`이 그 모양만 받는다."""
    return f"{꼬리 * 31}f"


def _회원_실행_요청(email: str) -> Request:
    """실제 `/run` 경계가 받는 모양 그대로, 그 친구의 로그인 쿠키를 실은 요청."""
    session = auth_logic.create_session(email, False, subject=_주체(email))
    쿠키 = f"{auth_constants.SESSION_COOKIE_NAME}={session.token}".encode()
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": "/run",
            "raw_path": b"/run",
            "query_string": b"",
            "headers": [(b"cookie", 쿠키)],
            "client": ("testclient", 50000),
            "server": ("testserver", 80),
        }
    )


def _예약_커밋까지_가는_실행(email: str, *, run_id: str):
    """예약 커밋 지점(reserve_member_run)을 실제로 지나는 실행 시작 경계.

    사전 확인(_guard_run)이 아니라 **Job 등록 직전의 예약 자리**가 거절할 때
    무슨 화면이 나오는지를 본다. 두 자리는 서로 다른 함수라 문구도 따로 박혀 있었다.
    """

    async def 시나리오():
        job_runtime._start_job_runtime()
        return await job_runtime._start_with_reserved_slot(
            request=_회원_실행_요청(email),
            original_input=UserInput(company="회사", job="직무", region="서울"),
            card=CompanyCard(
                legal_name="회사",
                typed_name="회사",
                address="서울",
                ceo="대표",
                founded="20200101",
                ref="demo-ref",
            ),
            posting_images=[],
            posting_image_consent=False,
            is_paid=False,
            resolved_track=(share_tracks.Track.MEMBER, f"user:{email}", 3000.0),
            run_id=run_id,
            upfront_cost=0.0,
            upfront_models=(),
            upfront_elapsed=0.0,
            slot_bucket_id="reserved-slot",
        )

    try:
        return asyncio.run(시나리오())
    finally:
        job_runtime._start_job_runtime()


def test_한도_초과_안내문은_회원값을_말한다():
    """★ 예약 자리의 안내문도 그 친구의 한도를 말해야 한다.

    사전 확인은 이미 회원값을 쓰지만(위 시험), 예약 자리의 문구가 옛 3건으로
    남아 있으면 한도를 7건으로 올린 친구가 경쟁에서 밀렸을 때 「3건 다 썼다」는
    틀린 말을 본다. 관리자는 화면대로 또 올리게 된다.
    """
    _초대한다(_친구)
    _한도를_정한다(_친구, 건수=7, 금액=None)
    _성공을_다_쓴다(_친구, 7)

    응답 = _예약_커밋까지_가는_실행(_친구, run_id=_실행번호("1"))
    글자 = 응답.body.decode("utf-8")

    assert "오늘 성공한 보고서 7건을 모두 사용했습니다" in 글자
    assert "3건을 모두 사용했습니다" not in 글자


def test_한도를_안_정한_친구는_예약_자리에서도_기존_3건으로_안내받는다():
    """★ 반대 경우 시험 — 회원별 한도를 넣었다고 기존 안내가 바뀌면 안 된다."""
    _초대한다(_친구)
    _성공을_다_쓴다(_친구, 3)

    응답 = _예약_커밋까지_가는_실행(_친구, run_id=_실행번호("2"))

    assert "오늘 성공한 보고서 3건을 모두 사용했습니다" in 응답.body.decode("utf-8")


def test_한도가_남은_친구는_예약_자리를_통과한다():
    """막는 자리가 «항상» 막으면 시험이 초록이어도 기능이 죽은 것이다."""
    _초대한다(_친구)
    _한도를_정한다(_친구, 건수=7, 금액=None)
    _성공을_다_쓴다(_친구, 6)

    실행번호 = _실행번호("3")
    응답 = _예약_커밋까지_가는_실행(_친구, run_id=실행번호)

    assert "모두 사용했습니다" not in 응답.body.decode("utf-8")
    # 예약 자리를 실제로 지났는지는 append-only 사건으로 본다. 최종 상태로 보면
    # 배경 조사가 실패해 반환(returned)되는 시점에 따라 값이 달라진다.
    with storage_db.connect() as conn:
        상태들 = [
            str(row[0])
            for row in conn.execute(
                f"SELECT state FROM {dashboard_store.TABLE_MEMBER_USAGE_EVENTS} "
                "WHERE run_id = ? ORDER BY id",
                (실행번호,),
            )
        ]
    assert 상태들[:1] == [dashboard_store.MEMBER_USAGE_RESERVED]


def test_막는_자리와_말하는_자리는_같은_문장을_쓴다():
    """두 자리에 같은 문장이 따로 박혀 있으면 한쪽만 고쳐진다 (P-83과 같은 함정)."""
    assert (
        request_helpers.member_success_limit_message(7)
        == "오늘 성공한 보고서 7건을 모두 사용했습니다. 내일 다시 시도해 주세요."
    )
    assert "3건" in request_helpers.member_success_limit_message(3)
    assert "20건" in request_helpers.member_success_limit_message(20)


def test_예약액_계약은_본조사_900원_그대로다():
    """상한 경계 시험이 기대는 값이 바뀌면 위 시험들의 뜻도 바뀐다."""
    from src.features.budget.constants import PAID_PHASE_PROVIDER_BUDGET_KRW

    assert PAID_PHASE_PROVIDER_BUDGET_KRW[SPEND_PHASE_PIPELINE] == _본조사_예약액


# ══════════════════════════════════════════════════════════
# ③ 관리자 화면에서 한 명만 바꾼다 — 권한·CSRF·감사·입력 검증
# ══════════════════════════════════════════════════════════

_한도경로 = f"/admin/members/{_친구}/limit"
#: 위험 동작의 이유는 20자 이상이어야 한다. 길이 규칙
#: 자체는 `test_admin_dangerous_actions.py`가 보고, 여기서는 규칙에 맞는 글로
#: «한도가 실제로 바뀌는지»만 본다.
_한도이유 = "면접 준비 기간이라 하루 조사 건수를 늘려 달라는 요청"
_되돌림이유 = "면접 준비 기간이 끝나 원래 기본값으로 되돌립니다"


@pytest.fixture
def client():
    runtime._PIPELINE = DemoPipeline()
    with TestClient(main.app) as test_client:
        yield test_client



# ══════════════════════════════════════════════════════════
# 위험 동작 확인 단계 — 시험이 실제 확인 화면을 거치게 한다
# ══════════════════════════════════════════════════════════


def _확인화면_경로(url: str, data: dict) -> str:
    """이 POST 앞에 서 있는 확인 화면의 주소. 확인이 필요 없으면 빈 글자."""

    if url == "/admin/links/revoke":
        return f"/admin/links/{data.get('key', '')}/revoke"
    if url == "/admin/revoke":
        return f"/admin/members/{data.get('email', '')}/remove"
    if url.endswith("/extend") or url.endswith("/limit"):
        return url
    return ""


def _확인표(client: TestClient, url: str, data: dict) -> str:
    """확인 화면을 «실제로 열어» 1회용 표를 받아 온다.

    ★ 표를 지어내지 않는다 — 화면이 안 주면 빈 글자이고, 그 요청은 서버가
      그대로 거절한다. 확인 단계 자체가 지켜지는지는 전용 시험
      `test_admin_dangerous_actions.py`가 본다.
    """

    경로 = _확인화면_경로(url, data)
    if not 경로:
        return ""
    화면 = client.get(경로)
    찾음 = re.search(r'name="confirm_token" value="([0-9a-f]+)"', 화면.text)
    return 찾음.group(1) if 찾음 else ""


@pytest.fixture
def admin(client: TestClient) -> TestClient:
    """관리자로 로그인한 손님. POST에 CSRF 표를 기본으로 붙인다."""
    session = auth_logic.create_session("admin@example.com", True)
    client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)
    csrf = auth_logic.csrf_token_for_session(session.token)
    원래_post = client.post

    def csrf가_붙은_post(url, *args, **kwargs):
        data = dict(kwargs.pop("data", {}) or {})
        data.setdefault("csrf_token", csrf)
        # 위험 동작은 확인 화면을 거쳐야 실행된다. 브라우저가 하는 것과
        # 같은 두 단계를 여기서 그대로 밟는다.
        if "confirm_token" not in data:
            표 = _확인표(client, url, data)
            if 표:
                data["confirm_token"] = 표
        return 원래_post(url, *args, data=data, **kwargs)

    client.post = csrf가_붙은_post
    client._csrf_for_test = csrf
    return client


def _저장된_한도(email: str) -> tuple[int | None, float | None, str]:
    with storage_db.connect() as conn:
        저장된 = share_allow.load(conn, email)
    assert 저장된 is not None
    return (
        저장된.daily_success_limit,
        저장된.daily_budget_krw,
        저장된.limit_reason,
    )


def _한도_감사행() -> list[tuple[str, str]]:
    with storage_db.connect() as conn:
        rows = conn.execute(
            f"SELECT target_id, reason_code FROM "
            f"{admin_audit_store.TABLE_ADMIN_AUDIT_EVENTS} "
            "WHERE action = 'admin.member.limit' ORDER BY id"
        ).fetchall()
    return [(str(row[0]), str(row[1])) for row in rows]


def test_관리자가_한_친구의_한도만_바꾸고_감사행_하나를_남긴다(admin: TestClient):
    _초대한다(_친구)
    _초대한다(_다른친구)

    응답 = admin.post(
        _한도경로,
        data={
            "daily_success_limit": "7",
            "daily_budget_krw": "4500",
            "reason": _한도이유,
        },
        follow_redirects=False,
    )

    assert 응답.status_code == 303
    assert 응답.headers["location"] == "/admin/members"
    assert _저장된_한도(_친구) == (7, 4500.0, _한도이유)
    assert _저장된_한도(_다른친구) == (None, None, "")
    assert len(_한도_감사행()) == 1
    assert _한도_감사행()[0][1] == "limit_changed"


def test_기본한도_변경은_다음날도_유지된다(admin: TestClient):
    """★ 영구 값이다 — 오늘만 쓰고 사라지는 보너스가 아니다."""
    _초대한다(_친구)
    admin.post(
        _한도경로,
        data={
            "daily_success_limit": "5",
            "daily_budget_krw": "4000",
            "reason": _한도이유,
        },
        follow_redirects=False,
    )

    with storage_db.connect() as conn:
        for number in range(5):
            assert dashboard_store.reserve_member_run(
                conn, run_id=f"오늘-{number}", actor_email=_친구,
                day="2026-09-02", now_iso=_시각,
            )
        assert not dashboard_store.reserve_member_run(
            conn, run_id="오늘-5", actor_email=_친구, day="2026-09-02", now_iso=_시각,
        )
        for number in range(5):
            assert dashboard_store.reserve_member_run(
                conn, run_id=f"내일-{number}", actor_email=_친구,
                day="2026-09-03", now_iso=_시각,
            )
        assert not dashboard_store.reserve_member_run(
            conn, run_id="내일-5", actor_email=_친구, day="2026-09-03", now_iso=_시각,
        )

    assert _저장된_한도(_친구)[:2] == (5, 4000.0)


def test_두_칸을_비우면_기본값으로_되돌린다(admin: TestClient):
    _초대한다(_친구)
    admin.post(
        _한도경로,
        data={
            "daily_success_limit": "9",
            "daily_budget_krw": "9000",
            "reason": _한도이유,
        },
        follow_redirects=False,
    )

    응답 = admin.post(
        _한도경로,
        data={
            "daily_success_limit": "",
            "daily_budget_krw": "",
            "reason": _되돌림이유,
        },
        follow_redirects=False,
    )

    assert 응답.status_code == 303
    assert _저장된_한도(_친구) == (None, None, _되돌림이유)
    _t, _b, cap = request_helpers._track_of(_친구로_들어온_요청(_친구))
    assert cap == 3000.0


@pytest.mark.parametrize(
    ("건수", "금액"),
    [("0", "1000"), ("21", "1000"), ("3", "-1"), ("3", "20001"), ("셋", "1000")],
)
def test_범위_밖_값은_400이고_아무것도_저장되지_않는다(
    admin: TestClient, 건수: str, 금액: str
):
    """★ 이유는 «규칙에 맞는» 글이어야 이 시험이 범위 검사를 잰다.

    짧은 이유를 쓰면 이유 하한 검사(20자)가 범위 검사보다 앞에서 400을
    돌려주고, 그러면 범위 조건을 통째로 무력화해도 이 시험이 그대로 초록이다
    (reviewer-gs9 P1, 실측: `allowlist.py`의 성공 건수 범위 조건을 끄고도
    5 passed). 막는 자리를 재려면 그 앞의 관문을 다 통과시켜야 한다.
    """

    _초대한다(_친구)

    응답 = admin.post(
        _한도경로,
        data={
            "daily_success_limit": 건수,
            "daily_budget_krw": 금액,
            "reason": _한도이유,
        },
        follow_redirects=False,
    )

    assert 응답.status_code == 400
    assert _저장된_한도(_친구) == (None, None, "")


def test_이유가_비면_400이고_한도가_안_바뀐다(admin: TestClient):
    """왜 올렸는지 없으면 나중에 되돌릴지 판단할 근거가 없다."""
    _초대한다(_친구)

    응답 = admin.post(
        _한도경로,
        data={"daily_success_limit": "5", "daily_budget_krw": "1000", "reason": "  "},
        follow_redirects=False,
    )

    assert 응답.status_code == 400
    assert _저장된_한도(_친구) == (None, None, "")


def test_명단에_없는_사람의_한도는_바꿀_수_없다(admin: TestClient):
    응답 = admin.post(
        "/admin/members/ghost@example.com/limit",
        data={
            "daily_success_limit": "5",
            "daily_budget_krw": "1000",
            "reason": _한도이유,
        },
        follow_redirects=False,
    )

    assert 응답.status_code == 503
    with storage_db.connect() as conn:
        assert share_allow.load(conn, "ghost@example.com") is None


def test_CSRF_표가_틀리면_한도가_안_바뀐다(admin: TestClient):
    _초대한다(_친구)

    응답 = admin.request(
        "POST",
        _한도경로,
        data={
            "daily_success_limit": "5",
            "daily_budget_krw": "1000",
            "reason": "시험",
            "csrf_token": "wrong-secret",
        },
        follow_redirects=False,
    )

    assert 응답.status_code == 403
    assert _저장된_한도(_친구) == (None, None, "")
    assert _한도_감사행() == []


def test_관리자가_아니면_한도를_못_바꾼다(client: TestClient):
    """★ 로그인만 한 사람이 자기 한도를 스스로 올리면 안 된다."""
    _초대한다(_친구)
    session = auth_logic.create_session(_친구, False, subject=_주체(_친구))
    client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)

    응답 = client.post(
        _한도경로,
        data={
            "daily_success_limit": "20",
            "daily_budget_krw": "20000",
            "reason": "내가 나에게",
            "csrf_token": auth_logic.csrf_token_for_session(session.token),
        },
        follow_redirects=False,
    )

    # 거절도 303이지만 «성공한 변경»의 도착지(/admin/members)로는 안 보낸다.
    assert 응답.headers.get("location") != "/admin/members"
    assert _저장된_한도(_친구) == (None, None, "")
    assert _한도_감사행() == []


def test_옛_좁은_운영판_계약에서는_한도_변경이_초대와_똑같이_막힌다(
    admin: TestClient, monkeypatch
):
    """★ 반대 경우 시험 — 친구 초대가 막힌 배포판에서는 한도 변경도 같이 막힌다."""
    _초대한다(_친구)
    monkeypatch.setenv(
        deployment_mode.ENV_DEPLOYMENT_RUNTIME_CONTRACT,
        deployment_mode.RENDER_ADMIN_REAL_NO_FORWARDED_CONTRACT,
    )
    monkeypatch.setenv(deployment_mode.ENV_PUBLIC_ORIGIN, "https://demo.example")

    초대_응답 = admin.post(
        "/admin/invite",
        data={"email": "newbie@example.com"},
        headers={"Host": "demo.example"},
        follow_redirects=False,
    )
    한도_응답 = admin.post(
        _한도경로,
        data={"daily_success_limit": "5", "daily_budget_krw": "1000", "reason": "시험"},
        headers={"Host": "demo.example"},
        follow_redirects=False,
    )

    # 한도 변경은 초대와 «같은 관문»을 쓴다 — 한쪽만 열려 있으면 안 된다.
    assert 초대_응답.status_code == 409
    assert 한도_응답.status_code == 409
    assert _저장된_한도(_친구) == (None, None, "")
    assert _한도_감사행() == []
