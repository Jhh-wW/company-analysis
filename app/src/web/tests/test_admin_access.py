"""초대·링크 관리 화면이 실제로 도는지 못 박는다 (문제로그 P-96).

★ 이 화면이 왜 필요했나 — 열쇠 링크와 초대 명단은 코드로는 완성이었는데
  **발급하려면 DB에 손으로 넣어야** 했다. 사용자 입장에서는 «없는 기능»이나 같다.

★ 여기서 지키는 것
  ① **관리자만** 들어온다 — 화면에서 버튼을 숨기는 건 방어가 아니다
  ② 발급·삭제·초대·취소가 «실제로» DB에 반영된다
  ③ 실제 지출과 호출 전 예상비용 차단 기준을 과장 없이 구분한다
  ④ **localhost 주소로 발급하면 경고한다** — 그대로 포폴에 넣으면 죽은 링크가 된다
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from src.features.budget.constants import SPEND_PHASE_PIPELINE
from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.pipeline.demo import DemoPipeline
from src.features.pipeline.canonical_demo import (
    DEMO_COMPANY as CANONICAL_DEMO_COMPANY,
)
from src.features.sharelink import allowlist as share_allow
from src.features.sharelink import store as share_store
from src.features.sharelink.constants import KEY_HEX_CHARS
from src.features.storage import db as storage_db
from src.features.storage import reports as report_store
from src.web import deployment_mode, main
from src.web import job_runtime, runtime
from src.web.routers import admin as admin_router
from src.web.routers import reports as reports_router


@pytest.fixture
def client():
    runtime._PIPELINE = DemoPipeline()
    with TestClient(main.app) as client:
        yield client


@pytest.fixture
def admin(client: TestClient) -> TestClient:
    """관리자로 로그인한 손님."""
    session = auth_logic.create_session("admin@example.com", True)
    client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)
    csrf = auth_logic.csrf_token_for_session(session.token)
    original_post = client.post

    def post_with_csrf(url, *args, **kwargs):
        data = dict(kwargs.pop("data", {}) or {})
        data.setdefault("csrf_token", csrf)
        return original_post(url, *args, data=data, **kwargs)

    client.post = post_with_csrf
    client._csrf_for_test = csrf
    return client


def _assert_503_alert(response) -> None:
    assert response.status_code == 503
    assert "no-store" in response.headers["cache-control"].split(", ")
    assert response.headers["x-request-id"]
    assert 'role="alert"' in response.text


def _assert_access_unknown(response, *, revocation_available: bool = False) -> None:
    _assert_503_alert(response)
    assert "확인 불가" in response.text
    assert "오늘 실제 지출" not in response.text
    assert "최악의 하루 지출" not in response.text
    assert "구성상 차단 기준 합계" not in response.text
    assert 'action="/admin/link/new"' not in response.text
    assert 'action="/admin/invite"' not in response.text
    if revocation_available:
        assert 'action="/admin/revoke"' in response.text
        assert "비상 철회만 가능" in response.text
    else:
        assert 'action="/admin/revoke"' not in response.text


def _security_audit_events(caplog) -> list[dict[str, str]]:
    events: list[dict[str, str]] = []
    for record in caplog.records:
        if record.name != "security.admin_audit":
            continue
        message = record.getMessage()
        assert message.startswith("admin_audit ")
        events.append(json.loads(message.removeprefix("admin_audit ")))
    return events


def _durable_audit_rows() -> list[dict[str, str]]:
    with storage_db.connect() as conn:
        rows = conn.execute(
            f"""
            SELECT event_time, request_id, actor_id, action, target_id,
                   outcome, reason_code
              FROM {admin_router._ADMIN_AUDIT_TABLE}
             ORDER BY id
            """
        ).fetchall()
    return [dict(row) for row in rows]


def _issued_link(response) -> tuple[str, str]:
    """1회성 발급 «화면»에서 raw capability와 안전한 관리 식별자를 분리한다.

    ★ 2026-09-02 기대값 이전 — 응답이 텍스트 파일 첨부에서 HTML 1장으로 바뀌었다.
      전달 형태만 바뀌었고 「원문은 이 응답에만 있다」는 성질은 그대로다.
      그래서 여기서 «화면에 딱 한 번만» 나온다는 것을 함께 못 박는다.
    """

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "content-disposition" not in response.headers
    found = re.search(r"/k/([0-9a-f]{32})(?:[\s<\"]|$)", response.text)
    assert found is not None
    raw_key = found.group(1)
    assert response.text.count(raw_key) == 1
    key_hash = response.headers["x-link-identifier"]
    assert key_hash == share_store.key_hash_of(raw_key)
    return raw_key, key_hash


# ══════════════════════════════════════════════════════════
# ① 관리자만 들어온다
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "경로", ["/admin/access", "/admin/link/" + "a" * 32]
)
def test_로그인_안_하면_관리_화면을_못_본다(client: TestClient, 경로: str):
    response = client.get(경로, follow_redirects=False)

    assert response.status_code in (302, 303, 307), "막혀야 한다"


@pytest.mark.parametrize(
    "경로",
    [
        "/admin/link/new",
        "/admin/link/report",
        "/admin/link/delete",
        "/admin/invite",
        "/admin/revoke",
    ],
)
def test_로그인_안_하면_바꾸지도_못한다(client: TestClient, 경로: str):
    """★ 화면을 막는 것과 «바꾸는 것»을 막는 것은 다른 일이다."""
    response = client.post(
        경로,
        data={"company": "x", "job": "y", "key": "a" * 32, "email": "a@b.c"},
        follow_redirects=False,
    )

    assert response.status_code in (302, 303, 307)


def test_관리자는_들어온다(admin: TestClient):
    assert admin.get("/admin/access").status_code == 200


def test_관리자라도_CSRF토큰이_없거나_다른_출처면_변경을_막는다(admin: TestClient):
    missing = admin.request(
        "POST", "/admin/link/new", data={"company": "x", "job": "y"}
    )
    wrong_origin = admin.request(
        "POST",
        "/admin/link/new",
        data={
            "company": "x",
            "job": "y",
            "csrf_token": admin._csrf_for_test,
        },
        headers={"Origin": "https://attacker.example"},
    )
    null_origin = admin.request(
        "POST",
        "/admin/link/new",
        data={
            "company": "x",
            "job": "y",
            "csrf_token": admin._csrf_for_test,
        },
        headers={"Origin": "null"},
    )

    assert missing.status_code == 403
    assert wrong_origin.status_code == 403
    assert null_origin.status_code == 403


def test_관리자_same_origin과_HMAC_CSRF는_정상변경을_허용한다(
    admin: TestClient,
):
    response = admin.request(
        "POST",
        "/admin/invite",
        data={
            "email": "origin-contract@example.com",
            "csrf_token": admin._csrf_for_test,
        },
        headers={"Origin": "http://testserver:80"},
        follow_redirects=False,
    )

    assert response.status_code == 303


def test_권한거절과_CSRF거절을_비식별_구조화감사로_남긴다(
    admin: TestClient, caplog
):
    caplog.set_level(logging.INFO, logger="security.admin_audit")

    session_name = auth_constants.SESSION_COOKIE_NAME
    session_token = admin.cookies.get(session_name)
    admin.cookies.delete(session_name)
    denied = admin.get(
        "/admin/access",
        headers={"X-Request-ID": "request/with spaces"},
        follow_redirects=False,
    )
    admin.cookies.set(session_name, session_token)
    csrf_denied = admin.request(
        "POST",
        "/admin/invite",
        data={"email": "private.person@example.com", "csrf_token": "wrong-secret"},
        follow_redirects=False,
    )

    assert denied.status_code == 303
    assert csrf_denied.status_code == 403
    events = _security_audit_events(caplog)
    assert any(
        event["outcome"] == "denied"
        and event["reason_code"] == "authorization_denied"
        for event in events
    )
    assert any(
        event["action"] == "admin.member.invite"
        and event["outcome"] == "denied"
        and event["reason_code"] == "csrf_denied"
        for event in events
    )
    serialized = "\n".join(record.getMessage() for record in caplog.records)
    assert "private.person@example.com" not in serialized
    assert "wrong-secret" not in serialized
    assert "request/with spaces" not in serialized


@pytest.mark.parametrize("failure", ["link_list", "ledger", "budget_health"])
def test_접근목록이나_비용정본_장애는_0원과_빈목록으로_보이지않는다(
    admin: TestClient, monkeypatch, failure: str
):
    with storage_db.connect() as conn:
        share_store.insert_new(
            conn,
            key="a" * 32,
            company="실제 저장 회사",
            job="영업",
            now_iso="2026-08-18T10:00:00+09:00",
        )
        share_allow.invite(
            conn,
            email="stored@example.com",
            note="",
            now_iso="2026-08-18T10:00:00+09:00",
        )

    if failure == "link_list":
        monkeypatch.setattr(
            admin_router.share_store,
            "list_all",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                sqlite3.OperationalError("private database detail")
            ),
        )
    elif failure == "ledger":
        monkeypatch.setattr(
            admin_router.spend_store,
            "load_day",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                sqlite3.OperationalError("private ledger detail")
            ),
        )
    else:
        monkeypatch.setattr(admin_router.paid_runtime, "_BUDGET_STORE_HEALTHY", False)

    response = admin.get("/admin/access")

    _assert_access_unknown(
        response, revocation_available=failure in {"ledger", "budget_health"}
    )
    assert "private database detail" not in response.text
    assert "private ledger detail" not in response.text


@pytest.mark.parametrize("failure", ["link_list", "ledger", "budget_health"])
def test_접근정본이_비정상이면_예산노출을_바꾸는_쓰기까지_막는다(
    admin: TestClient, monkeypatch, failure: str
):
    if failure == "link_list":
        monkeypatch.setattr(
            admin_router.share_store,
            "list_all",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                sqlite3.OperationalError("unavailable links")
            ),
        )
    elif failure == "ledger":
        monkeypatch.setattr(
            admin_router.spend_store,
            "load_day",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                sqlite3.OperationalError("unavailable ledger")
            ),
        )
    else:
        monkeypatch.setattr(admin_router.paid_runtime, "_BUDGET_STORE_HEALTHY", False)

    response = admin.post(
        "/admin/invite",
        data={"email": "blocked@example.com"},
        follow_redirects=False,
    )

    _assert_access_unknown(response)
    with storage_db.connect() as conn:
        assert share_allow.load(conn, "blocked@example.com") is None


def test_노션전송은_CSRF누락과_다른출처를_export전에_막는다(
    admin: TestClient, monkeypatch
):
    export_calls: list[bool] = []

    def forbidden_export(*_args, **_kwargs):
        export_calls.append(True)
        raise AssertionError("CSRF 검증 전에 노션 export를 호출하면 안 됩니다")

    monkeypatch.setattr(reports_router, "send_report_to_notion", forbidden_export)

    missing = admin.request("POST", "/notion/not-a-job", data={})
    wrong_origin = admin.request(
        "POST",
        "/notion/not-a-job",
        data={"csrf_token": admin._csrf_for_test},
        headers={"Origin": "https://attacker.example"},
    )

    assert missing.status_code == 403
    assert wrong_origin.status_code == 403
    assert export_calls == []


# ══════════════════════════════════════════════════════════
# ② 발급·삭제가 실제로 반영된다
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "contract",
    (
        deployment_mode.RENDER_ADMIN_DEMO_NO_FORWARDED_CONTRACT,
        deployment_mode.RENDER_ADMIN_REAL_NO_FORWARDED_CONTRACT,
    ),
)
def test_좁은Render관리자운영판은_링크와_MEMBER를_발급하지않는다(
    admin: TestClient, monkeypatch, contract
):
    monkeypatch.setenv(
        deployment_mode.ENV_DEPLOYMENT_RUNTIME_CONTRACT,
        contract,
    )
    monkeypatch.setenv(deployment_mode.ENV_PUBLIC_ORIGIN, "https://demo.example")
    with storage_db.connect() as conn:
        before = len(share_store.list_all(conn))

    link_response = admin.post(
        "/admin/link/new",
        data={"company": "카카오", "job": "마케팅"},
        headers={"Host": "demo.example"},
        follow_redirects=False,
    )
    member_response = admin.post(
        "/admin/invite",
        data={"email": "member@example.com"},
        headers={"Host": "demo.example"},
        follow_redirects=False,
    )

    with storage_db.connect() as conn:
        after = len(share_store.list_all(conn))
        member = share_allow.load(conn, "member@example.com")
    assert link_response.status_code == 404
    assert link_response.text == "찾을 수 없습니다."
    assert member_response.status_code == 409
    assert "이 운영판에서는 친구를 초대할 수 없습니다." in member_response.text
    assert after == before
    assert member is None


def test_링크를_발급하면_저장된다(admin: TestClient):
    response = admin.post(
        "/admin/link/new",
        data={"company": "카카오", "job": "마케팅", "note": "하반기 공채"},
        follow_redirects=False,
    )

    key, key_hash = _issued_link(response)
    with storage_db.connect() as conn:
        link = share_store.load(conn, key)
    assert link is not None
    assert len(key) == KEY_HEX_CHARS == 32
    assert link.key_hash == key_hash
    assert (link.company, link.job, link.note) == ("카카오", "마케팅", "하반기 공채")


def test_발급열쇠가_충돌하면_기존링크를_덮지않고_새열쇠로_재시도한다(
    admin: TestClient, monkeypatch
):
    collision = "c" * 32
    replacement = "d" * 32
    with storage_db.connect() as conn:
        share_store.insert_new(
            conn,
            key=collision,
            company="기존회사",
            job="기존직무",
            now_iso="2026-08-01T10:00:00",
        )
    keys = iter([collision, replacement])
    monkeypatch.setattr(admin_router.share_issue, "new_key", lambda: next(keys))

    response = admin.post(
        "/admin/link/new",
        data={"company": "새회사", "job": "새직무"},
        follow_redirects=False,
    )

    key, key_hash = _issued_link(response)
    assert key == replacement
    assert key_hash == share_store.key_hash_of(replacement)
    with storage_db.connect() as conn:
        old = share_store.load(conn, collision)
        new = share_store.load(conn, replacement)
    assert (old.company, old.job) == ("기존회사", "기존직무")
    assert (new.company, new.job) == ("새회사", "새직무")


def test_발급열쇠_충돌이_계속되면_기존링크를_보존하고_503으로_알린다(
    admin: TestClient, monkeypatch
):
    collision = "e" * 32
    with storage_db.connect() as conn:
        share_store.insert_new(
            conn,
            key=collision,
            company="기존회사",
            job="기존직무",
            now_iso="2026-08-01T10:00:00",
        )
    monkeypatch.setattr(admin_router.share_issue, "new_key", lambda: collision)

    response = admin.post(
        "/admin/link/new",
        data={"company": "새회사", "job": "새직무"},
        follow_redirects=False,
    )

    assert response.status_code == 503
    assert 'role="alert"' in response.text
    with storage_db.connect() as conn:
        links = share_store.list_all(conn)
    assert len(links) == 1
    assert (links[0].company, links[0].job) == ("기존회사", "기존직무")


def test_발급하면_raw주소를_딱한번_화면으로_보여준다(admin: TestClient):
    """★ 2026-09-02 기대값 이전 — 텍스트 파일 첨부 → 주소·QR 화면 1장.

    Referrer-Policy 기대값도 함께 옮긴다. HTML 응답은 공용 미들웨어가
    `same-origin`으로 고정한다(`response_security.py`, form POST의 Origin이
    `null`이 되는 것을 막기 위함). 이 문서의 주소 `/admin/links/new`에는
    비밀이 없으므로 referer로 원문이 새지 않는다.
    """
    response = admin.post(
        "/admin/link/new", data={"company": "카카오", "job": "마케팅"},
        follow_redirects=False,
    )

    raw_key, key_hash = _issued_link(response)
    assert f"/k/{raw_key}" in response.text
    assert raw_key not in key_hash
    assert raw_key not in str(response.url)
    assert "same-origin" == response.headers["referrer-policy"]


def test_관리자_링크와_초대_생성시각은_offset포함_KST다(
    admin: TestClient,
    monkeypatch,
):
    fixed = "2026-08-21T00:30:00+09:00"
    monkeypatch.setattr(admin_router.clock, "iso_now_kst", lambda: fixed)

    created = admin.post(
        "/admin/link/new",
        data={"company": "KST 회사", "note": "자정 뒤"},
        follow_redirects=False,
    )
    invited = admin.post(
        "/admin/invite",
        data={"email": "kst@example.com", "note": "자정 뒤"},
        follow_redirects=False,
    )

    key, _key_hash = _issued_link(created)
    with storage_db.connect() as conn:
        link = share_store.load(conn, key)
        member = share_allow.load(conn, "kst@example.com")
    assert created.status_code == 200
    assert invited.status_code == 303
    assert link is not None and link.created_at == fixed
    assert member is not None and member.invited_at == fixed


def test_관리자_시각표시는_저장된_UTC를_KST로_바꾼다(admin: TestClient):
    key = "d" * 32
    with storage_db.connect() as conn:
        share_store.insert_new(
            conn,
            key=key,
            company="UTC 저장 회사",
            job="",
            now_iso="2026-08-19T15:30:00Z",
        )
        assert share_store.mark_opened(
            conn, key, "2026-08-19T15:31:00+00:00"
        )
        share_allow.invite(
            conn,
            email="utc@example.com",
            note="UTC 저장",
            now_iso="2026-08-19T15:32:00Z",
        )

    listing = admin.get("/admin/access")
    detail = admin.get(f"/admin/link/{share_store.key_hash_of(key)}")

    assert "발급 2026-08-20 00:30 (한국시간)" in listing.text
    assert "최초 2026-08-20 00:31 (한국시간)" in listing.text
    assert "최근 2026-08-20 00:31 (한국시간)" in listing.text
    assert "2026-08-20 00:32 (한국시간)" in listing.text
    assert "2026-08-20 00:30 (한국시간)" in detail.text
    assert detail.text.count("2026-08-20 00:31 (한국시간)") == 3


def _보고서를_만든다(admin: TestClient) -> str:
    form = {
        "company": CANONICAL_DEMO_COMPANY,
        "region": "인천",
    }
    confirm = admin.post("/confirm", data=form)
    token = re.search(
        r'name="paid_attempt_token" value="([^"]+)"', confirm.text
    )
    assert token is not None
    run = admin.post(
        "/run",
        data={**form, "paid_attempt_token": token.group(1)},
        follow_redirects=False,
    )
    assert run.status_code == 303
    report_id = run.headers["location"].rsplit("/", 1)[-1]
    for _ in range(200):
        if admin.get(f"/api/progress/{report_id}").json()["finished"]:
            break
    assert admin.get(f"/result/{report_id}").status_code == 200
    return report_id


def test_결과주소를_붙여_링크를_만들면_받은사람이_보고서로_바로간다(
    admin: TestClient,
):
    """★ 기대값 이전(G-S6·D-G10) — 도착지가 결과에서 첫 화면(랜딩)으로 바뀌었다.

    「받은 사람」은 관리자가 아니라 로그인하지 않은 인사팀이다. 그래서 도착지는
    관리자 손님으로 확인하고, 실제로 보고서를 한 번에 여는지는 열쇠만 가진
    별도 손님으로 확인한다.
    """
    report_id = _보고서를_만든다(admin)
    created = admin.post(
        "/admin/link/new",
        data={
            "company": CANONICAL_DEMO_COMPANY,
            "job": "",
            "report_reference": f"http://testserver/result/{report_id}",
        },
        follow_redirects=False,
    )

    key, _key_hash = _issued_link(created)
    with storage_db.connect() as conn:
        assert share_store.load(conn, key).report_id == report_id
    opened = admin.get(f"/k/{key}", follow_redirects=False)
    assert opened.headers["location"] == "/"
    # 공유 쿠키는 배포 기본값대로 Secure다 — 실제 브라우저처럼 HTTPS로 왕복시킨다.
    with TestClient(main.app, base_url="https://testserver") as 받은사람:
        받은사람.get(f"/k/{key}", follow_redirects=False)
        랜딩 = 받은사람.get("/")
    assert f'href="/result/{report_id}"' in 랜딩.text
    assert f"{CANONICAL_DEMO_COMPANY} 보고서 보기" in 랜딩.text


@pytest.mark.parametrize(
    ("reference", "message"),
    [
        ("잘못된 값", "32자리 보고서 ID"),
        ("a" * 32, "해당 보고서를 찾을 수 없습니다"),
    ],
)
def test_이상하거나_없는_보고서는_링크에_연결하지않고_이유를_보여준다(
    admin: TestClient, reference: str, message: str
):
    response = admin.post(
        "/admin/link/new",
        data={
            "company": CANONICAL_DEMO_COMPANY,
            "job": "",
            "report_reference": reference,
        },
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert message in response.text
    assert 'role="alert"' in response.text
    with storage_db.connect() as conn:
        assert share_store.list_all(conn) == []


def test_기존_링크에도_보고서를_연결하고_다시_해제할수있다(admin: TestClient):
    report_id = _보고서를_만든다(admin)
    created = admin.post(
        "/admin/link/new",
        data={"company": CANONICAL_DEMO_COMPANY, "job": ""},
        follow_redirects=False,
    )
    key, key_hash = _issued_link(created)

    attached = admin.post(
        "/admin/link/report",
        data={"key": key_hash, "report_reference": f"/result/{report_id}"},
        follow_redirects=False,
    )
    assert attached.status_code == 303
    assert attached.headers["location"] == f"/admin/links/{key_hash}"
    with storage_db.connect() as conn:
        assert share_store.load(conn, key).report_id == report_id

    detached = admin.post(
        "/admin/link/report",
        data={"key": key_hash, "report_reference": ""},
        follow_redirects=False,
    )
    assert detached.status_code == 303
    with storage_db.connect() as conn:
        assert share_store.load(conn, key).report_id == ""


def test_기존_링크에도_없는_보고서를_연결할수없다(admin: TestClient):
    created = admin.post(
        "/admin/link/new",
        data={"company": CANONICAL_DEMO_COMPANY, "job": ""},
        follow_redirects=False,
    )
    key, key_hash = _issued_link(created)

    response = admin.post(
        "/admin/link/report",
        data={"key": key_hash, "report_reference": "a" * 32},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "해당 보고서를 찾을 수 없습니다" in response.text
    with storage_db.connect() as conn:
        assert share_store.load(conn, key).report_id == ""


def test_지원회사_꼬리표는_받은사람의_분석_대상을_묶지_않는다(
    admin: TestClient,
):
    """★ 2026-09-02 기대값 이전 — 앞선 시험은 「꼬리표와 다른 회사 보고서도
    링크에 묶인다」를 지켰다. 사용자 결정(D-G3)으로 그 동작을 뒤집었다.
    실수로 다른 회사 보고서를 고정하면 받은 사람이 엉뚱한 보고서를 보기 때문이다.

    다만 원래 이 시험이 지키려던 «진짜» 성질은 따로 있다 — 회사·직무는 권한
    범위가 아니라 전달 맥락 꼬리표라는 것. 그 성질은 링크를 연 사람이 다른
    회사를 자유롭게 분석할 수 있다는 것으로 그대로 지킨다.
    묶기 거부 쪽은 `test_다른_회사_보고서는_링크에_묶이지_않는다`가 이어받는다.
    """
    created = admin.post(
        "/admin/link/new",
        data={"company": "카카오"},
        follow_redirects=False,
    )
    key, _key_hash = _issued_link(created)

    opened = admin.get(f"/k/{key}", follow_redirects=False)
    시작화면 = admin.get("/")

    # 꼬리표가 「카카오」여도 카카오 전용 화면으로 가두지 않는다 — 아무나 고를 수
    # 있는 입력 화면으로 보낸다.
    assert opened.status_code == 303
    assert opened.headers["location"] == "/"
    assert 시작화면.status_code == 200
    assert 'name="company"' in 시작화면.text
    assert 'value="카카오"' not in 시작화면.text
    with storage_db.connect() as conn:
        assert share_store.load(conn, key).report_id == ""


def test_기간이_지난_보고서는_새링크와_기존링크_어느쪽에도_연결할수없다(
    admin: TestClient, monkeypatch
):
    report_id = _보고서를_만든다(admin)
    created = admin.post(
        "/admin/link/new",
        data={"company": CANONICAL_DEMO_COMPANY, "job": ""},
        follow_redirects=False,
    )
    key, key_hash = _issued_link(created)
    monkeypatch.setattr(job_runtime, "_link_expired", lambda _report: True)

    new_link = admin.post(
        "/admin/link/new",
        data={
            "company": CANONICAL_DEMO_COMPANY,
            "job": "",
            "report_reference": report_id,
        },
        follow_redirects=False,
    )
    existing_link = admin.post(
        "/admin/link/report",
        data={"key": key_hash, "report_reference": report_id},
        follow_redirects=False,
    )

    assert new_link.status_code == existing_link.status_code == 400
    assert "공유 기간이 지난 보고서" in new_link.text
    assert "공유 기간이 지난 보고서" in existing_link.text
    with storage_db.connect() as conn:
        links = share_store.list_all(conn)
        assert len(links) == 1
        assert share_store.load(conn, key).report_id == ""


def test_공백뿐인_회사로는_링크를_만들지않는다(admin: TestClient):
    response = admin.post(
        "/admin/link/new",
        data={"company": "   "},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert "회사 이름을 입력" in response.text
    with storage_db.connect() as conn:
        assert share_store.list_all(conn) == []


def test_링크를_닫을_수_있다(admin: TestClient):
    """철회는 권한만 닫고 링크·접속·생성 이력을 물리 삭제하지 않는다."""
    created = admin.post(
        "/admin/link/new", data={"company": "카카오", "job": "마케팅"},
        follow_redirects=False,
    )
    key, key_hash = _issued_link(created)
    with storage_db.connect() as conn:
        issued = share_store.load(conn, key)
        assert issued is not None
        assert share_store.mark_opened(
            conn, key, issued.created_at
        )
        assert share_store.start_run(
            conn,
            key=key,
            run_id="run-before-web-revoke",
            started_at="2026-08-21T14:01:00+09:00",
            input_company="네이버",
            confirmed_company="NAVER",
            company_id="00266961",
        )
        assert share_store.finish_run(
            conn,
            run_id="run-before-web-revoke",
            status=share_store.RUN_STATUS_STOPPED,
            finished_at="2026-08-21T14:02:00+09:00",
            stop_step="pipeline",
            stop_reason="evidence_gate_stopped",
        )

    closed = admin.post(
        "/admin/link/delete", data={"key": key_hash}, follow_redirects=False
    )

    with storage_db.connect() as conn:
        link = share_store.load(conn, key)
        events = share_store.list_open_events_by_hash(conn, key_hash)
        runs = share_store.list_runs_by_hash(conn, key_hash)
    assert closed.status_code == 303
    assert closed.headers["location"] == f"/admin/links/{key_hash}"
    assert link is not None
    assert link.is_revoked
    assert len(events) == 1
    assert [run.run_id for run in runs] == ["run-before-web-revoke"]


@pytest.mark.parametrize("failure", ["exception", "no-op"])
def test_링크철회를_확인할수없으면_성공으로_리디렉션하지않는다(
    admin: TestClient, monkeypatch, failure: str
):
    created = admin.post(
        "/admin/link/new", data={"company": "카카오", "job": "마케팅"},
        follow_redirects=False,
    )
    key, key_hash = _issued_link(created)

    if failure == "exception":
        def broken_delete(*_args, **_kwargs):
            raise OSError("write failed")

        monkeypatch.setattr(share_store, "delete_by_hash", broken_delete)
    else:
        monkeypatch.setattr(
            share_store, "delete_by_hash", lambda *_args, **_kwargs: False
        )

    response = admin.post(
        "/admin/link/delete", data={"key": key_hash}, follow_redirects=False
    )

    assert response.status_code == 503
    assert 'role="alert"' in response.text
    assert "아직 살아 있" in response.text or "여전히 사용" in response.text
    with storage_db.connect() as conn:
        assert share_store.load(conn, key) is not None


def test_회사링크를_닫아도_이미전달된_독립결과주소는_60일정책을_따른다(
    admin: TestClient,
):
    report_id = _보고서를_만든다(admin)
    created = admin.post(
        "/admin/link/new",
        data={
            "company": CANONICAL_DEMO_COMPANY,
            "job": "",
            "report_reference": report_id,
        },
        follow_redirects=False,
    )
    key, key_hash = _issued_link(created)
    # 기대값 이전(G-S6·D-G10): `/k/`는 이제 결과가 아니라 첫 화면으로 보낸다.
    # 이 시험의 대상은 링크를 닫은 뒤에도 «독립 결과 주소»가 60일 정책을 따르는지다.
    assert admin.get(f"/k/{key}", follow_redirects=False).headers["location"] == "/"
    detail_before_close = admin.get(f"/admin/link/{key_hash}")
    assert "보고서 생성 후 60일까지" in detail_before_close.text

    closed = admin.post(
        "/admin/link/delete", data={"key": key_hash}, follow_redirects=False
    )

    assert closed.status_code == 303
    assert admin.get(f"/k/{key}", follow_redirects=False).headers["location"].startswith(
        "/?share_status="
    )
    assert admin.get(f"/result/{report_id}").status_code == 200


def test_친구를_초대하고_뺄_수_있다(admin: TestClient):
    member_session = auth_logic.create_session("friend@gmail.com", False)
    invited = admin.post(
        "/admin/invite", data={
            "email": "Friend@Gmail.com", "display_name": "김민지", "note": "스터디"
        },
        follow_redirects=False,
    )
    assert invited.status_code == 303
    with storage_db.connect() as conn:
        assert share_allow.is_allowed(conn, "friend@gmail.com"), "대소문자를 맞춰야 한다"
        member = share_allow.load(conn, "friend@gmail.com")
        assert member is not None and member.display_name == "김민지"

    revoked = admin.post(
        "/admin/revoke",
        data={"email": "friend@gmail.com"},
        follow_redirects=False,
    )
    assert revoked.status_code == 303

    with storage_db.connect() as conn:
        assert not share_allow.is_allowed(conn, "friend@gmail.com")
        profiles = share_allow.list_profiles(conn)
        assert profiles[0].display_name == "김민지"
    assert auth_logic.get_session(member_session.token) is None

    reinvited = admin.post(
        "/admin/invite",
        data={"email": "friend@gmail.com", "display_name": "김민지", "note": "재초대"},
        follow_redirects=False,
    )
    assert reinvited.status_code == 303
    with storage_db.connect() as conn:
        member = share_allow.load(conn, "friend@gmail.com")
        assert member is not None and member.note == "재초대"


@pytest.mark.parametrize("failure", ["budget_health", "ledger_read"])
def test_비용원장장애에도_비상화면에서_LINK와_MEMBER를_원자철회한다(
    admin: TestClient, monkeypatch, failure: str
):
    created = admin.post(
        "/admin/link/new",
        data={"company": "카카오", "job": "마케팅"},
        follow_redirects=False,
    )
    raw_key, key_hash = _issued_link(created)
    invited = admin.post(
        "/admin/invite",
        data={"email": "emergency@example.com", "display_name": "비상 철회 대상"},
        follow_redirects=False,
    )
    assert invited.status_code == 303
    member_session = auth_logic.create_session("emergency@example.com", False)

    if failure == "budget_health":
        monkeypatch.setattr(admin_router.paid_runtime, "_BUDGET_STORE_HEALTHY", False)
    else:
        monkeypatch.setattr(
            admin_router.spend_store,
            "load_day",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(
                sqlite3.OperationalError("private ledger detail")
            ),
        )

    emergency = admin.get("/admin/access")
    _assert_access_unknown(emergency, revocation_available=True)
    assert 'action="/admin/links/revoke"' in emergency.text
    assert 'name="key" value="' + key_hash + '"' in emergency.text
    assert 'action="/admin/link/new"' not in emergency.text
    assert 'action="/admin/invite"' not in emergency.text

    revoked_member = admin.post(
        "/admin/revoke",
        data={"email": "emergency@example.com"},
        follow_redirects=False,
    )
    revoked_link = admin.post(
        "/admin/link/delete",
        data={"key": key_hash},
        follow_redirects=False,
    )

    assert revoked_member.status_code == 303
    assert revoked_link.status_code == 303
    with storage_db.connect() as conn:
        assert not share_allow.is_allowed(conn, "emergency@example.com")
        link = share_store.load(conn, raw_key)
        assert link is not None and link.is_revoked
    assert auth_logic.get_session(member_session.token) is None
    actions = [row["action"] for row in _durable_audit_rows()]
    assert "admin.member.revoke" in actions
    assert "admin.link.revoke" in actions


def test_철회_감사정본실패는_LINK와_MEMBER권한_세션을_함께_rollback한다(
    admin: TestClient, monkeypatch
):
    created = admin.post(
        "/admin/link/new",
        data={"company": "카카오", "job": "마케팅"},
        follow_redirects=False,
    )
    raw_key, key_hash = _issued_link(created)
    assert admin.post(
        "/admin/invite",
        data={"email": "atomic@example.com"},
        follow_redirects=False,
    ).status_code == 303
    member_session = auth_logic.create_session("atomic@example.com", False)

    def fail_success_audit(*_args, **_kwargs):
        raise sqlite3.OperationalError("private durable audit failure")

    monkeypatch.setattr(admin_router, "_queue_committed_change", fail_success_audit)
    revoked_member = admin.post(
        "/admin/revoke",
        data={"email": "atomic@example.com"},
        follow_redirects=False,
    )
    revoked_link = admin.post(
        "/admin/link/delete",
        data={"key": key_hash},
        follow_redirects=False,
    )

    assert revoked_member.status_code == 503
    assert revoked_link.status_code == 503
    assert "private durable audit failure" not in revoked_member.text
    assert "private durable audit failure" not in revoked_link.text
    with storage_db.connect() as conn:
        assert share_allow.is_allowed(conn, "atomic@example.com")
        link = share_store.load(conn, raw_key)
        assert link is not None and not link.is_revoked
    assert auth_logic.get_session(member_session.token) is not None
    actions = [row["action"] for row in _durable_audit_rows()]
    assert "admin.member.revoke" not in actions
    assert "admin.link.revoke" not in actions


def test_이상한_이메일은_안_들어간다(admin: TestClient):
    response = admin.post(
        "/admin/invite", data={"email": "골뱅이없음"}, follow_redirects=False
    )

    _assert_503_alert(response)
    with storage_db.connect() as conn:
        assert share_allow.list_all(conn) == []


@pytest.mark.parametrize("failure", ["exception", "no_change"])
def test_초대_예외나_행미변경은_성공리디렉션하지않는다(
    admin: TestClient, monkeypatch, failure: str
):
    if failure == "exception":
        def fail_invite(*_args, **_kwargs):
            raise sqlite3.OperationalError("SECRET invite failure\nforged")

        monkeypatch.setattr(admin_router.share_allow, "invite", fail_invite)
    else:
        with storage_db.connect() as conn:
            share_allow.invite(
                conn,
                email="friend@example.com",
                note="original",
                now_iso="2026-08-18T10:00:00+09:00",
            )

    response = admin.post(
        "/admin/invite",
        data={"email": "friend@example.com", "note": "changed"},
        follow_redirects=False,
    )

    _assert_503_alert(response)
    assert "SECRET invite failure" not in response.text
    with storage_db.connect() as conn:
        stored = share_allow.load(conn, "friend@example.com")
    if failure == "exception":
        assert stored is None
    else:
        assert stored is not None and stored.note == "original"


@pytest.mark.parametrize("failure", ["exception", "no_change"])
def test_초대철회_예외나_행미변경은_성공리디렉션하지않는다(
    admin: TestClient, monkeypatch, failure: str
):
    if failure == "exception":
        with storage_db.connect() as conn:
            share_allow.invite(
                conn,
                email="friend@example.com",
                note="keep",
                now_iso="2026-08-18T10:00:00+09:00",
            )

        def fail_revoke(*_args, **_kwargs):
            raise sqlite3.OperationalError("SECRET revoke failure\nforged")

        monkeypatch.setattr(admin_router.share_allow, "revoke", fail_revoke)

    response = admin.post(
        "/admin/revoke",
        data={"email": "friend@example.com"},
        follow_redirects=False,
    )

    _assert_503_alert(response)
    assert "SECRET revoke failure" not in response.text
    with storage_db.connect() as conn:
        stored = share_allow.load(conn, "friend@example.com")
    if failure == "exception":
        assert stored is not None and stored.note == "keep"
    else:
        assert stored is None


def test_관리자_변경은_같은_transaction의_append_only_감사행과_함께_commit된다(
    admin: TestClient, caplog
):
    caplog.set_level(logging.INFO, logger="security.admin_audit")
    report_id = _보고서를_만든다(admin)

    created = admin.post(
        "/admin/link/new",
        data={"company": CANONICAL_DEMO_COMPANY},
        headers={"X-Request-ID": "audit/workflow 01"},
        follow_redirects=False,
    )
    key, key_hash = _issued_link(created)
    attached = admin.post(
        "/admin/link/report",
        data={"key": key_hash, "report_reference": report_id},
        follow_redirects=False,
    )
    invited = admin.post(
        "/admin/invite",
        data={
            "email": "private.audit.person@example.com",
            "note": "SECRET NOTE\nforged-log",
        },
        follow_redirects=False,
    )
    revoked_member = admin.post(
        "/admin/revoke",
        data={"email": "private.audit.person@example.com"},
        follow_redirects=False,
    )
    revoked_link = admin.post(
        "/admin/link/delete", data={"key": key_hash}, follow_redirects=False
    )

    assert created.status_code == 200
    assert {
        attached.status_code,
        invited.status_code,
        revoked_member.status_code,
        revoked_link.status_code,
    } == {303}
    rows = _durable_audit_rows()
    assert [row["action"] for row in rows] == [
        "admin.link.create",
        "admin.link.report",
        "admin.member.invite",
        "admin.member.revoke",
        "admin.link.revoke",
    ]
    assert {row["outcome"] for row in rows} == {"success"}
    assert all(row["event_time"].endswith("+09:00") for row in rows)
    assert all(row["request_id"] and len(row["request_id"]) <= 64 for row in rows)
    assert all(row["actor_id"].startswith("user:") for row in rows)
    assert all("@" not in row["target_id"] for row in rows)
    assert "private.audit.person@example.com" not in json.dumps(rows)
    assert key not in json.dumps(rows)
    assert report_id not in json.dumps(rows)

    mirrored = _security_audit_events(caplog)
    final_outcomes = {
        event["outcome"]
        for event in mirrored
        if event["action"].startswith("admin.")
    }
    assert {"allowed", "attempt", "success"} <= final_outcomes
    serialized = "\n".join(record.getMessage() for record in caplog.records)
    assert "private.audit.person@example.com" not in serialized
    assert "SECRET NOTE" not in serialized
    assert "forged-log" not in serialized
    assert key not in serialized
    assert report_id not in serialized

    with storage_db.connect() as conn:
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute(
                f"UPDATE {admin_router._ADMIN_AUDIT_TABLE} SET outcome='forged'"
            )
        with pytest.raises(sqlite3.DatabaseError):
            conn.execute(f"DELETE FROM {admin_router._ADMIN_AUDIT_TABLE}")


def test_commit경계_실패는_변경과_success감사를_모두_rollback한다(
    admin: TestClient, monkeypatch, caplog
):
    caplog.set_level(logging.INFO, logger="security.admin_audit")
    real_storage_db = admin_router.storage_db
    real_connect = real_storage_db.connect

    class FailingCommitConnection(sqlite3.Connection):
        def commit(self) -> None:
            raise sqlite3.OperationalError(
                "SECRET commit detail\nforged-success"
            )

    @contextmanager
    def fail_at_commit_boundary(*args, **kwargs):
        assert not args and not kwargs
        path = real_storage_db.default_db_path()
        conn = sqlite3.connect(
            str(path),
            timeout=5.0,
            factory=FailingCommitConnection,
        )
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            real_storage_db._ensure_schema(conn)
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    monkeypatch.setattr(
        admin_router,
        "storage_db",
        SimpleNamespace(connect=fail_at_commit_boundary),
    )
    response = admin.post(
        "/admin/invite",
        data={"email": "rollback@example.com"},
        follow_redirects=False,
    )

    _assert_access_unknown(response)
    monkeypatch.setattr(admin_router, "storage_db", real_storage_db)
    with real_connect() as conn:
        assert share_allow.load(conn, "rollback@example.com") is None
        audit_table = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (admin_router._ADMIN_AUDIT_TABLE,),
        ).fetchone()
        audit_count = (
            conn.execute(
                f"SELECT COUNT(*) FROM {admin_router._ADMIN_AUDIT_TABLE}"
            ).fetchone()[0]
            if audit_table is not None
            else 0
        )
    assert audit_count == 0

    events = [
        event
        for event in _security_audit_events(caplog)
        if event["action"] == "admin.member.invite"
    ]
    assert "attempt" in {event["outcome"] for event in events}
    assert "failed" in {event["outcome"] for event in events}
    assert "success" not in {event["outcome"] for event in events}
    serialized = "\n".join(record.getMessage() for record in caplog.records)
    assert "rollback@example.com" not in serialized
    assert "SECRET commit detail" not in serialized
    assert "forged-success" not in serialized


def test_외부감사_mirror장애에도_원자commit된_정본감사행은_남는다(
    admin: TestClient, monkeypatch
):
    real_emit = admin_router.admin_audit.emit

    def fail_success_mirror(request, *, action, target, outcome, reason):
        if outcome == "success":
            raise OSError("SECRET external audit sink")
        return real_emit(
            request,
            action=action,
            target=target,
            outcome=outcome,
            reason=reason,
        )

    monkeypatch.setattr(admin_router.admin_audit, "emit", fail_success_mirror)
    response = admin.post(
        "/admin/invite",
        data={"email": "durable@example.com"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    with storage_db.connect() as conn:
        assert share_allow.load(conn, "durable@example.com") is not None
    rows = _durable_audit_rows()
    assert len(rows) == 1
    assert rows[0]["action"] == "admin.member.invite"
    assert rows[0]["outcome"] == "success"


# ══════════════════════════════════════════════════════════
# ③ ★ 실제 지출과 호출 전 예상비용 차단 기준을 구분한다
# ══════════════════════════════════════════════════════════


def test_호출전_예상비용_차단기준을_실제청구_최댓값으로_과장하지않는다(
    admin: TestClient,
):
    admin.post("/admin/link/new", data={"company": "카카오", "job": "마케팅"})
    admin.post("/admin/invite", data={"email": "f@g.com"})

    text = admin.get("/admin/access").text

    # 링크 1개(3,000) + MEMBER 1명(3,000) + 관리자(5,000) = 11,000원.
    # MEMBER에는 이 금액 상한과 성공 3건 제한이 함께 적용된다.
    assert "호출 전 예상비용 차단 기준 합계" in text
    assert "11,000원" in text
    assert "친구 1명 × 3,000원" in text
    assert "성공 보고서 3건" in text
    assert "기준에 닿으면 새 호출을 차단" in text
    assert "실제 청구의 최댓값이 아닙니다" in text
    assert "토큰 집계와 가격·환율 차이" in text
    assert "최악의 하루 지출" not in text


def test_비용카드는_요청에서_한번_캡처한_KST_원장기준일을_명시한다(
    admin: TestClient,
    monkeypatch,
):
    kst_today = dt.date(2026, 9, 1)
    calls: list[dt.date] = []

    def fixed_today() -> dt.date:
        calls.append(kst_today)
        return kst_today

    monkeypatch.setattr(admin_router.clock, "today_kst", fixed_today)

    response = admin.get("/admin/access")
    compact = " ".join(response.text.split())

    assert response.status_code == 200
    assert "오늘 나간 돈 <span class=\"muted\">— 2026-09-01 (한국시간)</span>" in compact
    assert calls == [kst_today]


def test_실제비용이_예상과_차단기준을_넘으면_금액과_overrun을_숨기지않는다(
    admin: TestClient,
):
    today = admin_router.clock.today_kst()
    with storage_db.connect() as conn:
        admin_router.spend_store.ensure_schema(conn)
        assert admin_router.spend_store.begin_inflight(
            conn,
            run_id="admin-ui-overrun",
            phase=SPEND_PHASE_PIPELINE,
            day=today,
            bucket="user:admin@example.com",
            started_at="2026-08-18T10:00:00+09:00",
            requested_cost_krw=100.0,
            cap_krw=10_000.0,
        )
        assert admin_router.spend_store.finish_inflight(
            conn,
            run_id="admin-ui-overrun",
            phase=SPEND_PHASE_PIPELINE,
            day=today,
            bucket="user:admin@example.com",
            cost_krw=6_000.0,
            created_at="2026-08-18T10:01:00+09:00",
        )

    response = admin.get("/admin/access")
    compact = " ".join(response.text.split())

    assert response.status_code == 200
    assert 'role="status"' in response.text
    assert "오늘 실제 지출은 <strong>6,000원</strong>" in compact
    assert "차단 기준 합계보다 <strong>1,000원</strong>" in compact
    assert "정산은 <strong>1건</strong>" in compact
    assert "관측 차액은 <strong>5,900원</strong>" in compact
    assert "최악의 하루 지출" not in response.text


def test_관리자_첫화면은_승인된_운영대시보드로_연결된다(
    admin: TestClient,
):
    text = admin.get("/admin").text

    assert "운영 대시보드" in text
    assert "하루 3,000원 입장 기준 + 성공 3건" in text
    assert "최악의 하루 지출" not in text


def test_전체_상한이_없다는_사실을_숨기지_않는다(admin: TestClient):
    """★ 위험을 «없앤» 게 아니라 본인이 감수하기로 한 것이다 — 계속 보여준다."""
    assert "전체 상한이 없습니다" in admin.get("/admin/access").text


# ══════════════════════════════════════════════════════════
# ④ raw LINK는 한 번만 전달하고 관리 화면에는 지문과 이력만 남긴다
# ══════════════════════════════════════════════════════════


def test_raw_LINK는_일회성응답외_DB_HTML_로그에_남지않는다(
    admin: TestClient, monkeypatch, caplog
):
    raw_key = "0123456789abcdef0123456789abcdef"
    monkeypatch.setenv("SHARE_PUBLIC_BASE_URL", "https://demo.example")
    monkeypatch.setattr(admin_router.share_issue, "new_key", lambda: raw_key)
    caplog.set_level(logging.INFO)

    created = admin.post(
        "/admin/link/new", data={"company": "카카오", "job": "마케팅"},
        follow_redirects=False,
    )
    issued_key, key_hash = _issued_link(created)
    assert issued_key == raw_key
    # ★ 기대값 이전 — 본문 전체가 주소이던 텍스트 응답에서 주소 칸 1개로.
    assert _issued_url_from_screen(created) == f"https://demo.example/k/{raw_key}"

    listing = admin.get("/admin/access")
    detail = admin.get(f"/admin/link/{key_hash}")
    with storage_db.connect() as conn:
        database_dump = "\n".join(conn.iterdump())
        stored = share_store.load_by_hash(conn, key_hash)
    logs = "\n".join(record.getMessage() for record in caplog.records)

    assert stored is not None and stored.key_hash == key_hash
    assert raw_key not in database_dump
    assert raw_key not in listing.text
    assert raw_key not in detail.text
    assert raw_key not in logs
    assert f"/k/{raw_key}" not in listing.text + detail.text
    assert "<svg" not in detail.text

    # httpx의 클라이언트측 요청 로그는 서버 로그가 아니므로 별도 계약으로 분리한다.
    caplog.clear()
    legacy = admin.get(f"/admin/link/{raw_key}", follow_redirects=False)
    assert legacy.status_code == 404
    assert "location" not in legacy.headers
    assert raw_key not in legacy.text


def test_일회성_발급주소도_악성_Host보다_설정된_origin을_쓴다(
    admin: TestClient, monkeypatch
):
    monkeypatch.setenv("SHARE_PUBLIC_BASE_URL", "https://demo.example")
    created = admin.post(
        "/admin/link/new", data={"company": "카카오", "job": "마케팅"},
        headers={"Host": "evil.example", "X-Forwarded-Host": "also-evil.example"},
        follow_redirects=False,
    )
    raw_key, key_hash = _issued_link(created)
    detail = admin.get(f"/admin/link/{key_hash}", headers={"Host": "evil.example"})

    # ★ 기대값 이전 — 텍스트 본문 전체 비교 → 발급 화면의 주소 칸 비교.
    assert _issued_url_from_screen(created) == f"https://demo.example/k/{raw_key}"
    assert "evil.example" not in created.text
    assert raw_key not in detail.text
    assert "evil.example" not in detail.text
    assert "<svg" not in detail.text


@pytest.mark.parametrize(
    "configured",
    ["", "http://demo.example", "https://demo.example/path"],
)
def test_정본공개주소가_없거나_잘못되면_악성Host를_발급주소로_쓰지않는다(
    admin: TestClient, monkeypatch, configured: str
):
    if configured:
        monkeypatch.setenv("SHARE_PUBLIC_BASE_URL", configured)
    else:
        monkeypatch.delenv("SHARE_PUBLIC_BASE_URL", raising=False)
        monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    created = admin.post(
        "/admin/link/new",
        data={"company": "카카오", "job": "마케팅"},
        headers={"Host": "evil.example"},
        follow_redirects=False,
    )
    raw_key, key_hash = _issued_link(created)
    detail = admin.get(f"/admin/link/{key_hash}", headers={"Host": "evil.example"})

    # ★ 기대값 이전 — 텍스트 본문 전체 비교 → 발급 화면의 주소 칸 비교.
    assert _issued_url_from_screen(created) == f"/k/{raw_key}"
    # 공개 주소가 없으면 「이 컴퓨터에서만 열린다」고 화면에서 알린다.
    assert "이 주소는 지금 이 컴퓨터에서만 열립니다" in created.text
    assert "evil.example" not in created.text
    assert raw_key not in detail.text
    assert "evil.example" not in detail.text
    assert "<svg" not in detail.text


def test_없는_링크는_목록으로_보낸다(admin: TestClient):
    response = admin.get("/admin/link/" + "f" * 32, follow_redirects=False)

    assert response.status_code == 404
    assert "location" not in response.headers


def test_구형_LINK_관리주소는_해시만_신형으로_보내고_raw는_거절한다(
    admin: TestClient, monkeypatch
):
    raw_key = "0f1e2d3c4b5a69780f1e2d3c4b5a6978"
    key_hash = share_store.key_hash_of(raw_key)
    with storage_db.connect() as conn:
        assert share_store.insert_new(
            conn,
            key=raw_key,
            company="카카오",
            job="마케팅",
            now_iso="2026-08-23T10:00:00+09:00",
        )

    canonical = admin.get(f"/admin/link/{key_hash}", follow_redirects=False)
    assert canonical.status_code == 303
    assert canonical.headers["location"] == f"/admin/links/{key_hash}"

    def unexpected_lookup(*_args, **_kwargs):
        raise AssertionError("raw LINK를 DB에서 조회하면 안 됩니다")

    monkeypatch.setattr(share_store, "load", unexpected_lookup)
    monkeypatch.setattr(share_store, "load_by_hash", unexpected_lookup)
    for path in (f"/admin/link/{raw_key}", f"/admin/links/{raw_key}"):
        rejected = admin.get(path, follow_redirects=False)
        assert rejected.status_code == 404
        assert "location" not in rejected.headers


def test_raw_LINK는_신형_보고서연결과_철회_POST에서도_거절한다(
    admin: TestClient,
):
    raw_key = "abcdef0123456789abcdef0123456789"
    key_hash = share_store.key_hash_of(raw_key)
    with storage_db.connect() as conn:
        assert share_store.insert_new(
            conn,
            key=raw_key,
            company="카카오",
            job="마케팅",
            now_iso="2026-08-23T10:00:00+09:00",
        )

    report_change = admin.post(
        "/admin/links/report",
        data={"key": raw_key, "report_reference": ""},
        follow_redirects=False,
    )
    revoke = admin.post(
        "/admin/links/revoke",
        data={"key": raw_key},
        follow_redirects=False,
    )

    assert report_change.status_code == 400
    assert revoke.status_code == 400
    with storage_db.connect() as conn:
        stored = share_store.load_by_hash(conn, key_hash)
    assert stored is not None
    assert stored.report_id == ""
    assert not stored.is_revoked


def test_관리화면은_누구를_봤다고_과장하지않고_요청지표만_말한다(
    admin: TestClient,
):
    admin.post("/admin/link/new", data={"company": "카카오", "job": "마케팅"})

    text = admin.get("/admin/access").text

    assert "누가 언제 열어봤는지" not in text
    assert "LINK 접속 기록" in text
    assert "메신저 미리보기" in text


def test_관리자_LINK이력은_완료상태_비용_출고해시와_보고서링크를_보인다(
    admin: TestClient, monkeypatch
):
    raw_key = "abcdef0123456789abcdef0123456789"
    key_hash = share_store.key_hash_of(raw_key)
    report_id = "2" * 32
    with storage_db.connect() as conn:
        assert share_store.insert_new(
            conn,
            key=raw_key,
            company="카카오",
            job="마케팅",
            now_iso="2026-08-21T15:00:00+09:00",
        )
        assert share_store.start_run(
            conn,
            key=raw_key,
            run_id="completed-link-run",
            started_at="2026-08-21T15:01:00+09:00",
            input_company="네이버",
            confirmed_company="NAVER",
            company_id="00266961",
        )
        assert share_store.finish_run(
            conn,
            run_id="completed-link-run",
            status=share_store.RUN_STATUS_AWAITING_RELEASE,
            finished_at="2026-08-21T15:02:00+09:00",
            report_id=report_id,
            internal_ai_cost_krw=321,
            customer_charge_krw=0,
        )
        assert share_store.mark_released(
            conn,
            report_id=report_id,
            pdf_sha256="a" * 64,
            release_sha256="b" * 64,
            released_at="2026-08-21T15:03:00+09:00",
            customer_charge_krw=990,
        )

    monkeypatch.setattr(
        admin_router.report_store,
        "load",
        lambda _conn, stored_id: object() if stored_id == report_id else None,
    )
    detail = admin.get(f"/admin/link/{key_hash}")

    assert detail.status_code == 200
    assert "NAVER" in detail.text
    assert "완료" in detail.text
    assert "AI 원가 321원" in detail.text
    assert "고객 청구 990원" in detail.text
    assert f'href="/admin/reports/{report_id}"' in detail.text
    assert "b" * 64 in detail.text
    assert raw_key not in detail.text


@pytest.mark.parametrize(
    ("report_id", "expected"),
    [("a" * 32, "찾을 수 없음")],
)
def test_없는_연결보고서는_목록과_상세에서_재연결필요로_보인다(
    admin: TestClient, report_id: str, expected: str
):
    key = "f" * 32
    with storage_db.connect() as conn:
        share_store.insert_new(
            conn,
            key=key,
            company="카카오",
            job="마케팅",
            report_id=report_id,
            now_iso="2026-08-18T10:00:00",
        )

    listing = admin.get("/admin/access")
    detail = admin.get(f"/admin/link/{share_store.key_hash_of(key)}")

    assert expected in listing.text
    assert "재연결 필요" in listing.text
    assert "저장소에서 찾을 수 없음" in detail.text
    assert f'href="/result/{report_id}"' not in detail.text


def test_만료된_연결보고서는_목록과_상세에서_사전에_표시한다(
    admin: TestClient, monkeypatch
):
    report_id = _보고서를_만든다(admin)
    created = admin.post(
        "/admin/link/new",
        data={
            "company": CANONICAL_DEMO_COMPANY,
            "job": "",
            "report_reference": report_id,
        },
        follow_redirects=False,
    )
    _key, key_hash = _issued_link(created)
    monkeypatch.setattr(job_runtime, "_link_expired", lambda _report: True)

    listing = admin.get("/admin/access")
    detail = admin.get(f"/admin/link/{key_hash}")

    assert "만료됨" in listing.text
    assert "재연결 필요" in listing.text
    assert "공유 기간 만료" in detail.text
    assert f'href="/result/{report_id}"' not in detail.text


# ══════════════════════════════════════════════════════════
# ⑤ 발급 결과는 주소와 QR을 한 화면에서 한 번만 보여준다
# ══════════════════════════════════════════════════════════


def _issued_url_from_screen(response) -> str:
    """발급 결과 화면에서 «한 번만» 보이는 주소 칸의 글자를 꺼낸다."""
    found = re.search(r'id="issued-link-url"[^>]*>([^<]+)<', response.text)
    assert found is not None, "발급 결과 화면에 주소 칸이 없습니다"
    return found.group(1).strip()


def test_발급응답은_QR_SVG와_주소를_한번만_보여주고_no_store다(
    admin: TestClient, monkeypatch
):
    """★ 텍스트 파일 첨부 대신 화면 1장 — 지금 저장하지 않으면 되찾을 수 없다.

    열쇠 원문은 DB에 없으므로(`store.py`는 해시만 저장) QR은 «발급 순간»에만
    만들 수 있다. 그래서 주소와 QR이 같은 응답에 함께 실려야 한다.
    """
    raw_key = "0123456789abcdef0123456789abcdef"
    monkeypatch.setenv("SHARE_PUBLIC_BASE_URL", "https://demo.example")
    monkeypatch.setattr(admin_router.share_issue, "new_key", lambda: raw_key)

    created = admin.post(
        "/admin/link/new",
        data={"company": "카카오", "job": "마케팅"},
        follow_redirects=False,
    )

    assert created.status_code == 200
    assert created.headers["content-type"].startswith("text/html")
    assert "content-disposition" not in created.headers
    assert "no-store" in created.headers["cache-control"].split(", ")
    assert created.headers["x-link-identifier"] == share_store.key_hash_of(raw_key)

    issued_url = f"https://demo.example/k/{raw_key}"
    assert _issued_url_from_screen(created) == issued_url
    # 원문은 화면에 «한 번만» 나온다 — 회수 지점을 늘리면 지울 곳도 늘어난다.
    assert created.text.count(raw_key) == 1
    # QR은 그 주소 «그대로»를 담아야 한다. 다른 주소를 그리면 받은 사람이 못 연다.
    assert admin_router.share_issue.qr_svg(issued_url) in created.text
    assert "이 화면을 닫으면" in created.text


def test_발급뒤_목록과_상세와_DB와_로그에_원문이_없다(
    admin: TestClient, monkeypatch, caplog
):
    """★ 화면을 HTML로 바꿔도 「원문은 이 응답에만」이라는 성질은 그대로다."""
    raw_key = "89abcdef0123456789abcdef01234567"
    monkeypatch.setenv("SHARE_PUBLIC_BASE_URL", "https://demo.example")
    monkeypatch.setattr(admin_router.share_issue, "new_key", lambda: raw_key)
    caplog.set_level(logging.INFO)

    created = admin.post(
        "/admin/link/new",
        data={"company": "카카오", "job": "마케팅"},
        follow_redirects=False,
    )
    assert created.status_code == 200
    assert created.headers["content-type"].startswith("text/html")
    key_hash = share_store.key_hash_of(raw_key)

    listing = admin.get("/admin/access")
    detail = admin.get(f"/admin/link/{key_hash}")
    with storage_db.connect() as conn:
        database_dump = "\n".join(conn.iterdump())
        stored = share_store.load_by_hash(conn, key_hash)
    logs = "\n".join(record.getMessage() for record in caplog.records)

    assert stored is not None and stored.key_hash == key_hash
    for 남은곳, 글자 in (
        ("DB", database_dump),
        ("목록", listing.text),
        ("상세", detail.text),
        ("로그", logs),
    ):
        assert raw_key not in 글자, f"{남은곳}에 링크 원문이 남았습니다"
        assert f"/k/{raw_key}" not in 글자
    # 원문이 없으니 QR도 되살아나지 않는다 — 관리 화면에는 그릴 재료가 없다.
    assert "<svg" not in detail.text
    assert "<svg" not in listing.text


def test_실제_발급화면을_릴리스_수락시험_파서가_읽는다(
    admin: TestClient, monkeypatch
):
    """★ 두 파일이 같은 앵커 이름을 쓰는지 «실제 렌더 결과»로 못 박는다.

    릴리스 수락시험(`release_acceptance/logic.py`)은 실서버를 띄우는 별도 러너라
    이 화면이 바뀌어도 pytest 가 알려주지 않는다. 그래서 여기서 이어 붙인다.
    앵커 id를 한쪽에서만 바꾸면 이 시험이 깨진다.
    """
    from src.features.release_acceptance import (  # noqa: PLC0415
        logic as acceptance_logic,
    )

    raw_key = "fedcba9876543210fedcba9876543210"
    monkeypatch.setenv("SHARE_PUBLIC_BASE_URL", "https://demo.example")
    monkeypatch.setattr(admin_router.share_issue, "new_key", lambda: raw_key)

    created = admin.post(
        "/admin/link/new",
        data={"company": "카카오", "job": "마케팅"},
        follow_redirects=False,
    )

    assert created.status_code == 200
    읽은주소 = acceptance_logic.extract_issued_share_url(created.text)
    assert 읽은주소 == f"https://demo.example/k/{raw_key}"
    assert 읽은주소 == _issued_url_from_screen(created)
    assert acceptance_logic.ISSUED_SHARE_URL_ANCHOR_ID == "issued-link-url"


def test_옛_계약에서는_발급이_여전히_404다(admin: TestClient, monkeypatch):
    """대조군 — 결과 화면을 바꿔도 좁은 운영판의 발급 차단은 그대로다."""
    monkeypatch.setenv(
        deployment_mode.ENV_DEPLOYMENT_RUNTIME_CONTRACT,
        deployment_mode.RENDER_ADMIN_REAL_NO_FORWARDED_CONTRACT,
    )
    monkeypatch.setenv(deployment_mode.ENV_PUBLIC_ORIGIN, "https://demo.example")

    blocked = admin.post(
        "/admin/link/new",
        data={"company": "카카오", "job": "마케팅"},
        headers={"Host": "demo.example"},
        follow_redirects=False,
    )

    assert blocked.status_code == 404
    assert blocked.text == "찾을 수 없습니다."
    assert "<svg" not in blocked.text
    assert "/k/" not in blocked.text
    with storage_db.connect() as conn:
        assert share_store.list_all(conn) == []


# ══════════════════════════════════════════════════════════
# ⑥ 링크에 묶는 보고서는 그 링크의 회사와 같아야 한다
# ══════════════════════════════════════════════════════════


def _회사만_바꿔_보고서를_복사한다(
    source_report_id: str,
    *,
    report_id: str,
    company: str,
    company_id: str = "",
) -> str:
    """이미 만든 데모 보고서를 복사해 «회사 표시명·고유번호만» 바꿔 저장한다.

    ★ 동명 회사 상황은 실제 조사로는 만들 수 없어서(데모 회사는 하나뿐) 저장소에
      직접 만든다. 본문은 건드리지 않으므로 판정에 쓰이는 값만 달라진다.
    """
    with storage_db.connect() as conn:
        원본 = report_store.load(conn, source_report_id)
        assert 원본 is not None
        report_store.save(
            conn,
            report_id,
            company_id,
            "",
            replace(원본, company=company, company_id=company_id),
        )
    return report_id


def test_다른_회사_보고서는_링크에_묶이지_않는다(admin: TestClient):
    """★ 「카카오 지원용 링크를 눌렀더니 다른 회사 보고서」를 서버가 막는다.

    관리자가 회사명을 보고 눈으로 거르는 것은 방어가 아니다 — 실수 한 번이면
    받은 사람이 엉뚱한 회사 보고서를 본다.
    """
    report_id = _보고서를_만든다(admin)  # (주)진영 보고서
    created = admin.post(
        "/admin/link/new", data={"company": "카카오"}, follow_redirects=False
    )
    key, key_hash = _issued_link(created)

    새링크 = admin.post(
        "/admin/link/new",
        data={"company": "카카오", "report_reference": report_id},
        follow_redirects=False,
    )
    기존링크 = admin.post(
        "/admin/link/report",
        data={"key": key_hash, "report_reference": report_id},
        follow_redirects=False,
    )

    assert 새링크.status_code == 400
    assert 기존링크.status_code == 400
    for 응답 in (새링크, 기존링크):
        assert "다른 회사" in 응답.text
        assert CANONICAL_DEMO_COMPANY in 응답.text
        assert 'role="alert"' in 응답.text
    with storage_db.connect() as conn:
        # 두 번째 발급은 저장 자체가 없었다 — 링크는 처음 것 하나뿐이다.
        assert len(share_store.list_all(conn)) == 1
        assert share_store.load(conn, key).report_id == ""


def test_같은_회사는_묶인다(admin: TestClient):
    """대조군 — 막는 것만 확인하면 「전부 막는 코드」와 구별되지 않는다.

    법인격 표기 차이((주)·주식회사·공백)는 같은 회사로 본다.
    """
    report_id = _보고서를_만든다(admin)  # (주)진영 보고서

    for 표기 in (CANONICAL_DEMO_COMPANY, "진영", "주식회사 진영"):
        created = admin.post(
            "/admin/link/new",
            data={"company": 표기, "report_reference": report_id},
            follow_redirects=False,
        )
        key, key_hash = _issued_link(created)
        with storage_db.connect() as conn:
            assert share_store.load(conn, key).report_id == report_id, 표기

        떼었다 = admin.post(
            "/admin/link/report",
            data={"key": key_hash, "report_reference": ""},
            follow_redirects=False,
        )
        다시_붙였다 = admin.post(
            "/admin/link/report",
            data={"key": key_hash, "report_reference": report_id},
            follow_redirects=False,
        )
        assert 떼었다.status_code == 303
        assert 다시_붙였다.status_code == 303, 표기
        with storage_db.connect() as conn:
            assert share_store.load(conn, key).report_id == report_id, 표기


def test_법인격_토큰이_이름_중간에_끼어도_다른_회사로_본다(admin: TestClient):
    """★ 「질원」과 「질주식회사원」은 고유번호가 다른 별개 회사다.

    법인격 표기를 «단어 경계 없이» 지우면 두 이름이 같은 값이 되어 통과한다.
    첫 결속에는 대조할 고유번호가 없어 이 이름 검사가 유일한 방어선이다.
    """
    base = _보고서를_만든다(admin)
    질주식회사원 = _회사만_바꿔_보고서를_복사한다(
        base, report_id="c" * 31 + "3", company="질주식회사원", company_id="00111111"
    )

    새링크 = admin.post(
        "/admin/link/new",
        data={"company": "질원", "report_reference": 질주식회사원},
        follow_redirects=False,
    )

    assert 새링크.status_code == 400
    assert "다른 회사" in 새링크.text
    assert "질주식회사원" in 새링크.text
    with storage_db.connect() as conn:
        assert share_store.list_all(conn) == []


def test_앞뒤_법인격_표기만_벗긴다(admin: TestClient):
    """대조군 — 「같은 회사인데 막힌다」로 기울지 않는다는 것도 같이 지킨다."""
    base = _보고서를_만든다(admin)
    하이브 = _회사만_바꿔_보고서를_복사한다(
        base, report_id="d" * 31 + "4", company="주식회사 하이브"
    )

    for 같은회사 in ("하이브", "하이브(주)", "㈜하이브", "하이브 주식회사", "(주) 하이브"):
        created = admin.post(
            "/admin/link/new",
            data={"company": 같은회사, "report_reference": 하이브},
            follow_redirects=False,
        )
        key, _key_hash = _issued_link(created)
        with storage_db.connect() as conn:
            assert share_store.load(conn, key).report_id == 하이브, 같은회사

    for 다른회사 in ("하이브미디어", "하이", "질원"):
        거부 = admin.post(
            "/admin/link/new",
            data={"company": 다른회사, "report_reference": 하이브},
            follow_redirects=False,
        )
        assert 거부.status_code == 400, 다른회사
        assert "다른 회사" in 거부.text, 다른회사


def test_결속_보고서를_읽지_못하면_연결을_거부한다(
    admin: TestClient, monkeypatch
):
    """★ 「확인 못 했다」는 「같은 회사다」가 아니다.

    링크에 이미 묶인 보고서를 못 읽으면 고유번호를 대조할 수 없다. 그때 조용히
    이름 검사로 되돌아가면(fail-open) 동명 다른 법인이 그대로 들어온다.
    """
    report_id = _보고서를_만든다(admin)
    created = admin.post(
        "/admin/link/new",
        data={
            "company": CANONICAL_DEMO_COMPANY,
            "report_reference": report_id,
        },
        follow_redirects=False,
    )
    key, key_hash = _issued_link(created)
    바꿀보고서 = _회사만_바꿔_보고서를_복사한다(
        report_id, report_id="e" * 31 + "5", company=CANONICAL_DEMO_COMPANY
    )

    원래_load = admin_router.report_store.load
    남은_실패 = [1]

    def 결속보고서_읽기가_한번_실패한다(conn, 찾는_id):
        # 결속 보고서의 첫 조회만 깨뜨린다 — 거부 화면 자체는 그려져야
        # 「저장소 장애 503」이 아니라 「연결 거부 400」임을 볼 수 있다.
        if 찾는_id == report_id and 남은_실패[0]:
            남은_실패[0] -= 1
            raise sqlite3.DatabaseError("결속 보고서를 읽지 못했습니다")
        return 원래_load(conn, 찾는_id)

    monkeypatch.setattr(
        admin_router.report_store, "load", 결속보고서_읽기가_한번_실패한다
    )

    거부 = admin.post(
        "/admin/link/report",
        data={"key": key_hash, "report_reference": 바꿀보고서},
        follow_redirects=False,
    )

    assert 거부.status_code == 400
    assert "보고서 정보를 확인할 수 없어 연결하지 않았습니다" in 거부.text
    assert 남은_실패[0] == 0, "결속 보고서를 읽으려는 시도 자체가 없었습니다"
    with storage_db.connect() as conn:
        assert share_store.load(conn, key).report_id == report_id


def test_동명_회사는_corp_id로_구분한다(admin: TestClient):
    """이름이 같아도 고유번호가 다르면 다른 회사다.

    링크 자체에는 고유번호 열이 없다. 그래서 「이 링크가 지금 가리키는 회사」의
    고유번호는 이미 묶여 있는 보고서에서 읽는다.
    """
    base = _보고서를_만든다(admin)
    법인_A = _회사만_바꿔_보고서를_복사한다(
        base, report_id="a" * 31 + "1", company="한빛", company_id="00126380"
    )
    법인_B = _회사만_바꿔_보고서를_복사한다(
        base, report_id="b" * 31 + "2", company="한빛", company_id="00999999"
    )

    created = admin.post(
        "/admin/link/new",
        data={"company": "한빛", "report_reference": 법인_A},
        follow_redirects=False,
    )
    key, key_hash = _issued_link(created)
    with storage_db.connect() as conn:
        assert share_store.load(conn, key).report_id == 법인_A

    다른법인 = admin.post(
        "/admin/link/report",
        data={"key": key_hash, "report_reference": 법인_B},
        follow_redirects=False,
    )
    assert 다른법인.status_code == 400
    assert "이름은 같지만" in 다른법인.text
    with storage_db.connect() as conn:
        assert share_store.load(conn, key).report_id == 법인_A

    같은법인 = admin.post(
        "/admin/link/report",
        data={"key": key_hash, "report_reference": 법인_A},
        follow_redirects=False,
    )
    assert 같은법인.status_code == 303
    with storage_db.connect() as conn:
        assert share_store.load(conn, key).report_id == 법인_A
