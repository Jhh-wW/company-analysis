from __future__ import annotations

import re
import time

import pytest
from fastapi.testclient import TestClient

from src.core.constants import REPORT_GENERATION_EXECUTION_MAX_SEC
from src.features.budget.sharing import REPORT_LINK_MAX_AGE_DAYS
from src.features.auth import constants as auth_constants
from src.features.auth import google as auth_google
from src.features.auth import logic as auth_logic
from src.features.admin_dashboard import store as dashboard_store
from src.features.pipeline.demo import DemoPipeline
from src.features.pipeline.canonical_demo import (
    DEMO_COMPANY as CANONICAL_DEMO_COMPANY,
    build_demo_report,
)
from src.features.report_access import constants, logic
from src.features.report_access import store as report_access_store
from src.features.sharelink import allowlist
from src.features.storage import db as storage_db
from src.features.storage import reports as report_store
from src.web import job_runtime, main, runtime
from src.web.routers import auth as auth_router


def _start_public_demo(client: TestClient) -> tuple[str, object]:
    form = {"company": CANONICAL_DEMO_COMPANY, "region": "서울"}
    confirmed = client.post("/confirm", data=form)
    assert confirmed.status_code == 200
    match = re.search(
        r'name="paid_attempt_token" value="([^"]+)"', confirmed.text
    )
    assert match is not None
    started = client.post(
        "/run",
        data={**form, "paid_attempt_token": match.group(1)},
        follow_redirects=False,
    )
    assert started.status_code == 303
    return started.headers["location"].rsplit("/", 1)[-1], started


def _wait_finished(client: TestClient, report_id: str) -> None:
    for _ in range(300):
        response = client.get(f"/api/progress/{report_id}")
        assert response.status_code == 200
        if response.json()["finished"]:
            return
        time.sleep(0.005)
    pytest.fail("데모 보고서가 제한시간 안에 끝나지 않았습니다")


def test_새_PUBLIC_demo는_별도_cookie로_완주하고_ID만_아는_브라우저는_모두_차단된다(
    monkeypatch,
):
    monkeypatch.setattr(runtime, "_PIPELINE", DemoPipeline())
    owner = TestClient(main.app, base_url="https://testserver")
    stranger = TestClient(main.app, base_url="https://testserver")
    try:
        report_id, started = _start_public_demo(owner)
        set_cookie = started.headers["set-cookie"]
        assert constants.PUBLIC_GRANT_COOKIE_NAME in set_cookie
        assert "HttpOnly" in set_cookie
        assert "Secure" in set_cookie
        assert "SameSite=lax" in set_cookie
        assert "Path=/" in set_cookie
        assert constants.PUBLIC_GRANT_MAX_AGE_SEC == (
            REPORT_LINK_MAX_AGE_DAYS * 24 * 60 * 60
            + REPORT_GENERATION_EXECUTION_MAX_SEC
            + constants.PUBLIC_GRANT_COMMIT_MARGIN_SEC
            + constants.PUBLIC_GRANT_ADMISSION_MARGIN_SEC
        )
        assert f"Max-Age={constants.PUBLIC_GRANT_MAX_AGE_SEC}" in set_cookie
        raw_grant = owner.cookies.get(constants.PUBLIC_GRANT_COOKIE_NAME)
        assert raw_grant
        assert report_id not in raw_grant

        _wait_finished(owner, report_id)
        assert owner.get(f"/result/{report_id}").status_code == 200

        for path in (
            f"/result/{report_id}",
            f"/download/pdf/{report_id}",
            f"/progress/{report_id}",
            f"/api/progress/{report_id}",
        ):
            assert stranger.get(path, follow_redirects=False).status_code == 404

        # 메모리 Job이 사라진 재시작 모양에서도 DB grant가 결과 복구를 허용한다.
        job_runtime._JOBS.pop(report_id, None)
        recovered = owner.get(f"/result/{report_id}")
        assert recovered.status_code == 200
        assert CANONICAL_DEMO_COMPANY in recovered.text
    finally:
        owner.close()
        stranger.close()


def test_cutover후_무grant_PUBLIC과_email_only_MEMBER는_자동승격하지않는다():
    """cutover 뒤 DB에 없는 비밀/subject를 추측해 권한으로 만들지 않는다.

    새 행은 과거 생성일을 흉내 내는 것만으로 legacy가 될 수 없다. PUBLIC 1/1,
    MEMBER 1/1이 네 공개 채널 모두에서 닫힌다.
    """

    public_report = "a0" * 16
    member_report = "b0" * 16
    member_email = "legacy-member@example.com"
    generated = build_demo_report()
    with storage_db.connect() as conn:
        for report_id in (public_report, member_report):
            report_store.save(
                conn, report_id, f"corp-{report_id[:2]}", generated.job, generated
            )
            dashboard_store.register_report(
                conn,
                report_id=report_id,
                corp_type=generated.corp_type,
                payload_json=report_store.report_to_json(generated),
                now_iso="2026-08-27T10:00:00+09:00",
            )
        assert allowlist.invite(
            conn,
            email=member_email,
            note="cutover 전 초대",
            now_iso="2026-08-27T10:00:00+09:00",
        )
        assert dashboard_store.reserve_member_run(
            conn,
            run_id=member_report,
            actor_email=member_email,
            day="2026-08-27",
            now_iso="2026-08-27T10:01:00+09:00",
        )
        # cutover 뒤 PUBLIC grant와 MEMBER subject 결속을 일부러 만들지 않는다.
        assert conn.execute(
            f"SELECT COUNT(*) FROM {report_access_store.TABLE_BINDINGS}"
        ).fetchone()[0] == 0
        assert conn.execute(
            f"SELECT COUNT(*) FROM {report_access_store.TABLE_MEMBER_BINDINGS}"
        ).fetchone()[0] == 0

    legacy_member = auth_logic.create_session(
        member_email,
        False,
        subject="google:current-session-cannot-prove-past-owner",
    )
    public_client = TestClient(main.app, base_url="https://testserver")
    member_client = TestClient(main.app, base_url="https://testserver")
    try:
        member_client.cookies.set(
            auth_constants.SESSION_COOKIE_NAME, legacy_member.token
        )
        for client, report_id in (
            (public_client, public_report),
            (member_client, member_report),
        ):
            statuses = [
                client.get(path, follow_redirects=False).status_code
                for path in (
                    f"/result/{report_id}",
                    f"/download/pdf/{report_id}",
                    f"/progress/{report_id}",
                    f"/api/progress/{report_id}",
                )
            ]
            assert statuses == [404, 404, 404, 404]
    finally:
        public_client.close()
        member_client.close()


def test_한_PUBLIC_브라우저의_두_보고서는_같은grant로_각각_열린다(monkeypatch):
    monkeypatch.setattr(runtime, "_PIPELINE", DemoPipeline())
    with TestClient(main.app, base_url="https://testserver") as owner:
        first, _ = _start_public_demo(owner)
        first_token = owner.cookies.get(constants.PUBLIC_GRANT_COOKIE_NAME)
        second, _ = _start_public_demo(owner)
        second_token = owner.cookies.get(constants.PUBLIC_GRANT_COOKIE_NAME)

        assert first != second
        assert first_token == second_token
        assert owner.get(f"/api/progress/{first}").status_code == 200
        assert owner.get(f"/api/progress/{second}").status_code == 200


def test_네_공개채널은_모두_같은_authorize_report_access를_먼저_호출한다(
    monkeypatch,
):
    report_id = "a" * 32
    calls: list[str] = []

    def deny(_request, locator, *, now=None):
        del now
        calls.append(locator)
        return logic.AccessDecision(False, None, "not_owner")

    monkeypatch.setattr(logic, "authorize_report_access", deny)
    with TestClient(main.app, base_url="https://testserver") as client:
        for path in (
            f"/result/{report_id}",
            f"/download/pdf/{report_id}",
            f"/progress/{report_id}",
            f"/api/progress/{report_id}",
        ):
            assert client.get(path, follow_redirects=False).status_code == 404
    assert calls == [report_id, report_id, report_id, report_id]


def test_narrow_admin도_매요청_현재_admin_session으로_공개채널을_지난다(
    monkeypatch,
):
    report_id = "b" * 32
    monkeypatch.setenv(auth_constants.ENV_BETA_ADMIN_ONLY, "1")
    monkeypatch.setenv(auth_constants.ENV_ADMIN_EMAILS, "admin@example.com")
    session = auth_logic.create_session(
        "admin@example.com", True, subject="google:narrow-current-admin"
    )
    with TestClient(main.app, base_url="https://testserver") as client:
        client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)
        responses = [
            client.get(path, follow_redirects=False)
            for path in (
                f"/result/{report_id}",
                f"/download/pdf/{report_id}",
                f"/progress/{report_id}",
                f"/api/progress/{report_id}",
            )
        ]
        assert all(response.status_code != 404 for response in responses)

        monkeypatch.setenv(auth_constants.ENV_ADMIN_EMAILS, "removed@example.com")
        assert all(
            client.get(path, follow_redirects=False).status_code == 303
            for path in (
                f"/result/{report_id}",
                f"/download/pdf/{report_id}",
                f"/progress/{report_id}",
                f"/api/progress/{report_id}",
            )
        )


def test_MEMBER는_남의report에_설문이나_오류차단을_쓸수없다():
    report_a = "c" * 32
    report_b = "d" * 32
    generated = build_demo_report()
    with storage_db.connect() as conn:
        for report_id in (report_a, report_b):
            report_store.save(
                conn, report_id, f"corp-{report_id[0]}", generated.job, generated
            )
            dashboard_store.register_report(
                conn,
                report_id=report_id,
                corp_type=generated.corp_type,
                payload_json=report_store.report_to_json(generated),
                now_iso="2026-08-28T10:00:00+09:00",
            )
        for email in ("a@example.com", "b@example.com"):
            assert allowlist.invite(
                conn, email=email, note="", now_iso="2026-08-28T10:00:00+09:00"
            )
        for report_id, email, subject in (
            (report_a, "a@example.com", "google:post-owner-a"),
            (report_b, "b@example.com", "google:post-owner-b"),
        ):
            assert dashboard_store.reserve_member_run(
                conn,
                run_id=report_id,
                actor_email=email,
                day="2026-08-28",
                now_iso="2026-08-28T10:01:00+09:00",
            )
            assert report_access_store.bind_member_run(
                conn, run_id=report_id, identity_subject=subject
            )
            assert report_access_store.bind_report(
                conn,
                run_id=report_id,
                report_id=report_id,
                delivery_expires_at=None,
            )
            assert dashboard_store.settle_member_run(
                conn,
                run_id=report_id,
                succeeded=True,
                report_id=report_id,
                now_iso="2026-08-28T10:02:00+09:00",
            )

    member_a = auth_logic.create_session(
        "a@example.com", False, subject="google:post-owner-a"
    )
    csrf = auth_logic.csrf_token_for_session(member_a.token)
    with TestClient(main.app, base_url="https://testserver") as client:
        client.cookies.set(auth_constants.SESSION_COOKIE_NAME, member_a.token)
        crossed_survey = client.post(
            f"/reports/{report_b}/survey",
            data={
                "rating": "5",
                "overall_feedback": "남의 보고서 변경",
                "business_distinction": "교차 변경",
                "csrf_token": csrf,
            },
            follow_redirects=False,
        )
        crossed_error = client.post(
            f"/reports/{report_b}/errors",
            data={
                "area": "표",
                "reason": "남의 보고서 차단 시도",
                "csrf_token": csrf,
            },
            follow_redirects=False,
        )
        own_survey = client.post(
            f"/reports/{report_a}/survey",
            data={
                "rating": "5",
                "overall_feedback": "내 보고서",
                "business_distinction": "소유권 확인",
                "csrf_token": csrf,
            },
            follow_redirects=False,
        )

    assert crossed_survey.status_code == 403
    assert crossed_error.status_code == 403
    assert own_survey.status_code == 303
    with storage_db.connect() as conn:
        assert not dashboard_store.report_is_blocked(conn, report_b)
        assert all(
            item.report_id != report_b
            for item in dashboard_store.list_open_errors(conn)
        )
        assert conn.execute(
            f"SELECT COUNT(*) FROM {dashboard_store.TABLE_SURVEYS} "
            "WHERE report_id = ?",
            (report_b,),
        ).fetchone()[0] == 0


def test_OAuth_callback은_확인된_MEMBER_sub를_legacy이관에_전달한다(monkeypatch):
    session = auth_logic.create_session(
        "oauth-forward@example.com",
        False,
        subject="google:oauth-forward-owner",
    )
    login_result = auth_google.LoginResult(
        email=session.email,
        is_admin=False,
        session=session,
    )
    received: list[tuple[str, str]] = []
    monkeypatch.setattr(
        auth_router.oauth_state_store,
        "consume_state",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        auth_router.auth_google,
        "handle_callback",
        lambda *_args, **_kwargs: login_result,
    )

    def migrate(_conn, *, member_email, identity_subject, now):
        assert now > 0
        received.append((member_email, identity_subject))
        return 0

    monkeypatch.setattr(
        auth_router.report_access_store,
        "migrate_legacy_member_bindings",
        migrate,
    )
    returned = auth_router._consume_state_and_handle_callback(
        code="fake-code",
        state="s" * auth_constants.STATE_TOKEN_CHARS,
        expected="s" * auth_constants.STATE_TOKEN_CHARS,
        previous_session_token=None,
        provider_deadline_monotonic=time.monotonic() + 10,
    )
    assert returned is login_result
    assert received == [(session.email, session.subject)]


def test_OAuth_legacy이관실패는_새session을_브라우저에줄수없게_폐기한다(
    monkeypatch,
):
    session = auth_logic.create_session(
        "oauth-fail@example.com",
        False,
        subject="google:oauth-fail-owner",
    )
    login_result = auth_google.LoginResult(
        email=session.email,
        is_admin=False,
        session=session,
    )
    monkeypatch.setattr(
        auth_router.oauth_state_store,
        "consume_state",
        lambda *_args, **_kwargs: True,
    )
    monkeypatch.setattr(
        auth_router.auth_google,
        "handle_callback",
        lambda *_args, **_kwargs: login_result,
    )
    monkeypatch.setattr(
        auth_router.report_access_store,
        "migrate_legacy_member_bindings",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("DB 실패")),
    )
    with pytest.raises(auth_router._OAuthLegacyMigrationUnavailable):
        auth_router._consume_state_and_handle_callback(
            code="fake-code",
            state="t" * auth_constants.STATE_TOKEN_CHARS,
            expected="t" * auth_constants.STATE_TOKEN_CHARS,
            previous_session_token=None,
            provider_deadline_monotonic=time.monotonic() + 10,
        )
    assert auth_logic.get_session(session.token) is None
