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
from src.web import main
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


def _assert_access_unknown(response) -> None:
    _assert_503_alert(response)
    assert "확인 불가" in response.text
    assert "오늘 실제 지출" not in response.text
    assert "최악의 하루 지출" not in response.text
    assert "구성상 차단 기준 합계" not in response.text
    assert 'action="/admin/link/new"' not in response.text
    assert 'action="/admin/invite"' not in response.text
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
    """1회성 텍스트 응답에서 raw capability와 안전한 관리 식별자를 분리한다."""

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "attachment" in response.headers["content-disposition"]
    found = re.search(r"/k/([0-9a-f]{32})(?:\s|$)", response.text)
    assert found is not None
    raw_key = found.group(1)
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

    _assert_access_unknown(response)
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


def test_발급하면_raw주소를_딱한번_텍스트파일로_내려준다(admin: TestClient):
    response = admin.post(
        "/admin/link/new", data={"company": "카카오", "job": "마케팅"},
        follow_redirects=False,
    )

    raw_key, key_hash = _issued_link(response)
    assert f"/k/{raw_key}" in response.text
    assert raw_key not in key_hash
    assert "no-referrer" == response.headers["referrer-policy"]


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
    assert opened.headers["location"] == f"/result/{report_id}"


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
    assert attached.headers["location"] == f"/admin/link/{key_hash}"
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


def test_시작보고서는_지원회사_꼬리표와_달라도_새링크와_기존링크에_연결된다(
    admin: TestClient,
):
    report_id = _보고서를_만든다(admin)  # canonical 진영 보고서
    created = admin.post(
        "/admin/link/new",
        data={"company": "카카오"},
        follow_redirects=False,
    )
    key, key_hash = _issued_link(created)

    new_link = admin.post(
        "/admin/link/new",
        data={"company": "카카오", "report_reference": report_id},
        follow_redirects=False,
    )
    existing_link = admin.post(
        "/admin/link/report",
        data={"key": key_hash, "report_reference": report_id},
        follow_redirects=False,
    )

    second_key, _second_hash = _issued_link(new_link)
    assert existing_link.status_code == 303
    with storage_db.connect() as conn:
        assert share_store.load(conn, key).report_id == report_id
        assert share_store.load(conn, second_key).report_id == report_id


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
    assert closed.headers["location"] == f"/admin/link/{key_hash}"
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
    assert admin.get(f"/k/{key}", follow_redirects=False).headers["location"] == (
        f"/result/{report_id}"
    )
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
    invited = admin.post(
        "/admin/invite", data={"email": "Friend@Gmail.com", "note": "스터디"},
        follow_redirects=False,
    )
    assert invited.status_code == 303
    with storage_db.connect() as conn:
        assert share_allow.is_allowed(conn, "friend@gmail.com"), "대소문자를 맞춰야 한다"

    revoked = admin.post(
        "/admin/revoke",
        data={"email": "friend@gmail.com"},
        follow_redirects=False,
    )
    assert revoked.status_code == 303

    with storage_db.connect() as conn:
        assert not share_allow.is_allowed(conn, "friend@gmail.com")


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

    # 링크 1개(3,000) + 관리자(5,000) = 8,000원. MEMBER는 비용이 아니라
    # 성공 보고서 3건으로 별도 제한하므로 금액 합계에 넣지 않는다.
    assert "호출 전 예상비용 차단 기준 합계" in text
    assert "8,000원" in text
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
    assert "하루 성공 3건" in text
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
    assert created.text.strip() == f"https://demo.example/k/{raw_key}"

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
    assert legacy.status_code == 303
    assert legacy.headers["location"] == f"/admin/link/{key_hash}"
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

    assert created.text.strip() == f"https://demo.example/k/{raw_key}"
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

    assert created.text.strip() == f"/k/{raw_key}"
    assert "evil.example" not in created.text
    assert raw_key not in detail.text
    assert "evil.example" not in detail.text
    assert "<svg" not in detail.text


def test_없는_링크는_목록으로_보낸다(admin: TestClient):
    response = admin.get("/admin/link/" + "f" * 32, follow_redirects=False)

    assert response.headers["location"] == "/admin/access"


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
    assert f'href="/admin/link/report/{report_id}"' in detail.text
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
