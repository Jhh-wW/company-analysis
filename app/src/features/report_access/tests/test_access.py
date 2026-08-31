from __future__ import annotations

import concurrent.futures
import datetime as dt
import hashlib
import sqlite3
import time

import pytest
from starlette.requests import Request

from src.core import clock
from src.features.admin_dashboard import store as dashboard_store
from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.report_access import constants, logic, store
from src.features.sharelink import allowlist
from src.features.sharelink import store as share_store
from src.features.sharelink.constants import KEY_COOKIE_NAME
from src.features.storage import db as storage_db


def _request(cookies: dict[str, str] | None = None) -> Request:
    cookie = "; ".join(f"{key}={value}" for key, value in (cookies or {}).items())
    headers = [(b"cookie", cookie.encode("ascii"))] if cookie else []
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "https",
            "path": "/result/test",
            "raw_path": b"/result/test",
            "query_string": b"",
            "headers": headers,
            "client": ("203.0.113.10", 44321),
            "server": ("testserver", 443),
        }
    )


def _grant(run_id: str, *, token: str = "", now: float | None = None):
    with storage_db.connect() as conn:
        return store.issue_and_bind(
            conn,
            existing_token=token,
            run_id=run_id,
            now=time.time() if now is None else now,
        )


def _seed_pre_cutover_report(
    conn: sqlite3.Connection,
    report_id: str,
    *,
    generated_at: str = "2026-08-01T10:00:00+09:00",
    created_at: str = "2026-08-01T10:00:00+09:00",
) -> None:
    conn.execute(
        """
        INSERT INTO reports
            (report_id, corp_id, job, payload_json, generated_at, created_at)
        VALUES (?, 'legacy-corp', '', '{}', ?, ?)
        """,
        (report_id, generated_at, created_at),
    )


def _rebuild_cutover_snapshot(conn: sqlite3.Connection) -> None:
    """운영 DB에 보고서가 먼저 있던 최초 배포 모양을 시험에서 재현한다."""

    conn.execute(f"DROP TABLE IF EXISTS {store.TABLE_LEGACY_RESOURCES}")
    conn.execute(f"DROP TABLE IF EXISTS {store.TABLE_CUTOVER}")
    store.ensure_schema(conn)


def test_주소만_아는_익명과_다른_PUBLIC은_교차열람할수없다():
    report_a = "a" * 32
    report_b = "b" * 32
    grant_a = _grant(report_a)
    grant_b = _grant(report_b)

    anonymous = logic.authorize_report_access(_request(), report_a)
    own = logic.authorize_report_access(
        _request({constants.PUBLIC_GRANT_COOKIE_NAME: grant_a.token}), report_a
    )
    crossed = logic.authorize_report_access(
        _request({constants.PUBLIC_GRANT_COOKIE_NAME: grant_a.token}), report_b
    )

    assert anonymous.allowed is False
    assert own == logic.AccessDecision(True, logic.AccessRole.PUBLIC, "public_grant")
    assert crossed.allowed is False
    assert grant_a.token != grant_b.token


def test_PUBLIC_한_브라우저는_자기_보고서_여러개를_재시작뒤에도_연다():
    report_a = "1" * 32
    report_b = "2" * 32
    grant = _grant(report_a)
    reused = _grant(report_b, token=grant.token)

    assert reused.reused is True
    assert reused.token == grant.token
    request = _request({constants.PUBLIC_GRANT_COOKIE_NAME: grant.token})
    assert logic.authorize_report_access(request, report_a).allowed is True
    assert logic.authorize_report_access(request, report_b).allowed is True

    # 프로세스 메모리를 전혀 쓰지 않고 새 readonly 연결로 다시 판정한다.
    with storage_db.connect_readonly_existing() as conn:
        assert conn is not None
        assert store.public_grant_allows(
            conn, raw_token=grant.token, locator=report_a
        )


def test_PUBLIC_보고서하나철회는_같은브라우저의_다른보고서를_닫지않는다():
    report_a = "a1" * 16
    report_b = "b2" * 16
    issued_at = 1_800_000_000.0
    grant = _grant(report_a, now=issued_at)
    _grant(report_b, token=grant.token, now=issued_at + 1)

    with storage_db.connect() as conn:
        assert store.revoke_resource(
            conn, locator=report_a, revoked_at=issued_at + 2
        ) == 1
        assert not store.public_grant_allows(
            conn,
            raw_token=grant.token,
            locator=report_a,
            now=issued_at + 3,
        )
        assert store.public_grant_allows(
            conn,
            raw_token=grant.token,
            locator=report_b,
            now=issued_at + 3,
        )
        # 자원 철회와 브라우저 grant 철회는 다른 사건이다. 마지막 결속을
        # 지워도 grant 행은 만료 정리 transaction까지 따로 살아 있다.
        assert store.revoke_resource(
            conn, locator=report_b, revoked_at=issued_at + 4
        ) == 1
        active = conn.execute(
            f"SELECT revoked_at FROM {store.TABLE_GRANTS} WHERE grant_hash = ?",
            (grant.grant_hash,),
        ).fetchone()
        assert active is not None and float(active[0]) == 0


def test_PUBLIC_token은_DB에_원문이_없고_만료와_철회가_즉시_닫힌다():
    report_id = "3" * 32
    issued_at = 1_800_000_000.0
    grant = _grant(report_id, now=issued_at)
    db_bytes = storage_db.default_db_path().read_bytes()
    assert grant.token.encode("ascii") not in db_bytes

    with storage_db.connect() as conn:
        assert store.public_grant_allows(
            conn,
            raw_token=grant.token,
            locator=report_id,
            now=grant.expires_at - 0.001,
        )
        assert not store.public_grant_allows(
            conn,
            raw_token=grant.token,
            locator=report_id,
            now=grant.expires_at,
        )
        assert store.revoke_grant(
            conn, grant_hash=grant.grant_hash, revoked_at=issued_at + 1
        )
        assert not store.public_grant_allows(
            conn,
            raw_token=grant.token,
            locator=report_id,
            now=issued_at + 2,
        )


def test_PUBLIC_grant는_최대작업뒤_시작하는_보고서60일보다_commit여유만큼_길다():
    from src.core.constants import REPORT_GENERATION_EXECUTION_MAX_SEC
    from src.features.budget.sharing import REPORT_LINK_MAX_AGE_DAYS

    issued_at = 1_800_000_000.0
    report_id = "0" * 32
    grant = _grant(report_id, now=issued_at)
    report_lifetime_sec = REPORT_LINK_MAX_AGE_DAYS * 24 * 60 * 60
    latest_delivery_expiry = (
        issued_at + REPORT_GENERATION_EXECUTION_MAX_SEC + report_lifetime_sec
    )
    expected_expiry = (
        latest_delivery_expiry + constants.PUBLIC_GRANT_COMMIT_MARGIN_SEC
    )
    assert grant.expires_at == expected_expiry
    with storage_db.connect() as conn:
        # 실제 보고서는 별도 Delivery 판정이 60일에 먼저 닫는다. grant가 이
        # 경계보다 조금 더 사는 것은 작업 시작/완료 시각 차이를 보완할 뿐이다.
        assert store.public_grant_allows(
            conn,
            raw_token=grant.token,
            locator=report_id,
            now=latest_delivery_expiry,
        )
        assert not store.public_grant_allows(
            conn,
            raw_token=grant.token,
            locator=report_id,
            now=expected_expiry,
        )


def test_PUBLIC_기존grant는_최대작업시간과_commit여유가_남을때만_재사용한다():
    from src.features.budget.constants import PAID_PHASE_LEASE_SEC

    assert constants.PUBLIC_GRANT_REUSE_MIN_REMAINING_SEC == (
        PAID_PHASE_LEASE_SEC + constants.PUBLIC_GRANT_COMMIT_MARGIN_SEC
    )
    conn = sqlite3.connect(":memory:")
    try:
        store.ensure_schema(conn)
        issued_at = 1_800_000_000.0
        first = store.issue_and_bind(
            conn,
            existing_token="",
            run_id="10" * 16,
            now=issued_at,
        )
        conn.commit()

        enough_at = (
            first.expires_at
            - constants.PUBLIC_GRANT_REUSE_MIN_REMAINING_SEC
            - 0.001
        )
        reused = store.issue_and_bind(
            conn,
            existing_token=first.token,
            run_id="11" * 16,
            now=enough_at,
        )
        conn.commit()
        assert reused.reused is True
        assert reused.token == first.token
        assert reused.expires_at == (
            enough_at + constants.PUBLIC_GRANT_MAX_AGE_SEC
        )

        # 경계와 같으면 ``commit 여유보다 더 많이`` 남았다는 보장이 없으므로
        # 기존 token을 억지로 연장하지 않고 새 PUBLIC grant를 발급한다. 이때
        # 같은 브라우저의 과거 결속은 새 token에도 복제한다.
        near = store.issue_and_bind(
            conn,
            existing_token="",
            run_id="12" * 16,
            now=enough_at + 1,
        )
        conn.commit()
        boundary_at = (
            near.expires_at
            - constants.PUBLIC_GRANT_REUSE_MIN_REMAINING_SEC
        )
        replaced = store.issue_and_bind(
            conn,
            existing_token=near.token,
            run_id="13" * 16,
            now=boundary_at,
        )
        assert replaced.reused is False
        assert replaced.token != near.token
        assert replaced.expires_at == (
            boundary_at + constants.PUBLIC_GRANT_MAX_AGE_SEC
        )
        conn.commit()
        assert store.public_grant_allows(
            conn,
            raw_token=replaced.token,
            locator="12" * 16,
            now=boundary_at,
        )
        assert store.public_grant_allows(
            conn,
            raw_token=replaced.token,
            locator="13" * 16,
            now=boundary_at,
        )
    finally:
        conn.close()


def test_PUBLIC_완성결속은_commit여유가_남은grant만_받는다():
    conn = sqlite3.connect(":memory:")
    try:
        store.ensure_schema(conn)
        checked_at = 1_800_000_100.0
        grant = store.issue_and_bind(
            conn,
            existing_token="",
            run_id="20" * 16,
            now=checked_at - 100,
        )
        conn.execute(
            f"UPDATE {store.TABLE_GRANTS} SET expires_at = ? "
            "WHERE grant_hash = ?",
            (
                checked_at + constants.PUBLIC_GRANT_COMMIT_MARGIN_SEC,
                grant.grant_hash,
            ),
        )
        conn.commit()

        with pytest.raises(store.PublicGrantBindingUnavailable, match="저장 여유"):
            store.bind_report(
                conn,
                run_id="20" * 16,
                report_id="21" * 16,
                now=checked_at,
            )
        conn.rollback()
        row = conn.execute(
            f"SELECT report_id FROM {store.TABLE_BINDINGS} WHERE run_id = ?",
            ("20" * 16,),
        ).fetchone()
        assert row is not None and str(row[0]) == ""
    finally:
        conn.close()


@pytest.mark.parametrize(
    "grant_state",
    ("expired", "revoked", "created_in_future"),
)
def test_PUBLIC_시간이모순되거나_철회된grant는_완성결속에서_다시거절한다(
    grant_state: str,
):
    conn = sqlite3.connect(":memory:")
    try:
        store.ensure_schema(conn)
        issued_at = 1_800_001_000.0
        checked_at = issued_at + 100
        grant = store.issue_and_bind(
            conn,
            existing_token="",
            run_id="30" * 16,
            now=issued_at,
        )
        if grant_state == "expired":
            conn.execute(
                f"UPDATE {store.TABLE_GRANTS} SET expires_at = ? "
                "WHERE grant_hash = ?",
                (checked_at, grant.grant_hash),
            )
        elif grant_state == "revoked":
            assert store.revoke_grant(
                conn,
                grant_hash=grant.grant_hash,
                revoked_at=checked_at - 1,
            )
        else:
            conn.execute(
                f"UPDATE {store.TABLE_GRANTS} SET created_at = ? "
                "WHERE grant_hash = ?",
                (checked_at + 1, grant.grant_hash),
            )
        conn.commit()

        with pytest.raises(store.PublicGrantBindingUnavailable):
            store.bind_report(
                conn,
                run_id="30" * 16,
                report_id="31" * 16,
                now=checked_at,
            )
        conn.rollback()
        assert conn.execute(
            f"SELECT report_id FROM {store.TABLE_BINDINGS} WHERE run_id = ?",
            ("30" * 16,),
        ).fetchone()[0] == ""
    finally:
        conn.close()


def test_PUBLIC_검사직후_만료될grant도_commit전에_결속하지않는다(monkeypatch):
    conn = sqlite3.connect(":memory:")
    try:
        store.ensure_schema(conn)
        checked_at = 1_800_002_000.0
        grant = store.issue_and_bind(
            conn,
            existing_token="",
            run_id="40" * 16,
            now=checked_at - 100,
        )
        # 예전의 단순 ``expires_at > now`` 검사라면 통과하지만, 저장 commit
        # 여유 안에 만료되는 행이다. bind_report가 호출 시각을 직접 다시 읽어
        # 이 검사-사용 경합을 닫는지 확인한다.
        conn.execute(
            f"UPDATE {store.TABLE_GRANTS} SET expires_at = ? "
            "WHERE grant_hash = ?",
            (
                checked_at + constants.PUBLIC_GRANT_COMMIT_MARGIN_SEC / 2,
                grant.grant_hash,
            ),
        )
        conn.commit()
        monkeypatch.setattr(store.time, "time", lambda: checked_at)

        with pytest.raises(store.PublicGrantBindingUnavailable):
            store.bind_report(
                conn,
                run_id="40" * 16,
                report_id="41" * 16,
            )
        conn.rollback()
        assert conn.execute(
            f"SELECT report_id FROM {store.TABLE_BINDINGS} WHERE run_id = ?",
            ("40" * 16,),
        ).fetchone()[0] == ""
    finally:
        conn.close()


def test_PUBLIC_같은run은_첫보고서에서_다른보고서로_바꿀수없다():
    conn = sqlite3.connect(":memory:")
    try:
        store.ensure_schema(conn)
        issued_at = 1_800_003_000.0
        store.issue_and_bind(
            conn,
            existing_token="",
            run_id="50" * 16,
            now=issued_at,
        )
        assert store.bind_report(
            conn,
            run_id="50" * 16,
            report_id="51" * 16,
            now=issued_at + 1,
        )
        conn.commit()

        with pytest.raises(store.ReportBindingConflict, match="두 보고서"):
            store.bind_report(
                conn,
                run_id="50" * 16,
                report_id="52" * 16,
                now=issued_at + 2,
            )
        conn.rollback()
        assert conn.execute(
            f"SELECT report_id FROM {store.TABLE_BINDINGS} WHERE run_id = ?",
            ("50" * 16,),
        ).fetchone()[0] == "51" * 16
    finally:
        conn.close()


def test_MEMBER_완성결속은_PUBLIC_grant시간정책의_영향을받지않는다():
    conn = sqlite3.connect(":memory:")
    try:
        store.ensure_schema(conn)
        run_id = "60" * 16
        report_id = "61" * 16
        assert store.bind_member_run(
            conn,
            run_id=run_id,
            identity_subject="google:member-public-time-boundary",
            now=100,
        )
        conn.commit()

        assert store.bind_report(
            conn,
            run_id=run_id,
            report_id=report_id,
            now=10**15,
        )
        assert store.member_subject_allows(
            conn,
            identity_subject="google:member-public-time-boundary",
            locator=report_id,
        )
    finally:
        conn.close()


def test_같은run의_MEMBER와_PUBLIC_혼합소유는_아무행도_고치지않고_거절한다():
    conn = sqlite3.connect(":memory:")
    try:
        store.ensure_schema(conn)
        run_id = "70" * 16
        issued_at = 1_800_004_000.0
        public_grant = store.issue_and_bind(
            conn,
            existing_token="",
            run_id=run_id,
            now=issued_at,
        )
        assert store.bind_member_run(
            conn,
            run_id=run_id,
            identity_subject="google:mixed-owner-attack",
            now=issued_at,
        )
        assert store.revoke_grant(
            conn,
            grant_hash=public_grant.grant_hash,
            revoked_at=issued_at + 1,
        )
        conn.commit()

        with pytest.raises(store.MixedReportOwnershipConflict, match="동시에"):
            store.bind_report(
                conn,
                run_id=run_id,
                report_id="71" * 16,
                now=issued_at + 2,
            )
        conn.rollback()
        assert conn.execute(
            f"SELECT report_id FROM {store.TABLE_BINDINGS} WHERE run_id = ?",
            (run_id,),
        ).fetchone()[0] == ""
        assert conn.execute(
            f"SELECT report_id FROM {store.TABLE_MEMBER_BINDINGS} WHERE run_id = ?",
            (run_id,),
        ).fetchone()[0] == ""
    finally:
        conn.close()


def test_cutover전_PUBLIC만_원래60일_경계까지_ID호환하고_GET은_쓰지않는다(
    caplog,
):
    report_id = "01" * 16
    made = dt.datetime(2026, 8, 1, 10, 0, tzinfo=clock.KST)
    expected_expiry = dt.datetime(2026, 9, 30, 0, 0, tzinfo=clock.KST).timestamp()
    with storage_db.connect() as conn:
        _seed_pre_cutover_report(
            conn,
            report_id,
            generated_at=made.isoformat(),
            created_at=made.isoformat(),
        )
        _rebuild_cutover_snapshot(conn)
        cutover = conn.execute(
            f"SELECT cutover_at FROM {store.TABLE_CUTOVER} WHERE singleton=1"
        ).fetchone()[0]
        assert cutover == dt.datetime.fromisoformat(
            constants.LEGACY_COMPAT_CUTOVER_AT_ISO
        ).timestamp()
        legacy = store.legacy_access_for(
            conn, locator=report_id, now=expected_expiry - 0.001
        )
        assert legacy == store.LegacyAccess(
            store.LEGACY_AUDIENCE_PUBLIC, "", expected_expiry
        )

    path = storage_db.default_db_path()
    before = path.read_bytes()
    before_stat = path.stat()
    allowed = logic.authorize_report_access(
        _request(), report_id, now=expected_expiry - 0.001
    )
    after_stat = path.stat()
    assert allowed == logic.AccessDecision(
        True, logic.AccessRole.PUBLIC, "legacy_public_bearer"
    )
    assert "legacy report access compatibility used" in caplog.text
    assert report_id not in caplog.text
    assert hashlib.sha256(report_id.encode("ascii")).hexdigest() in caplog.text
    assert path.read_bytes() == before
    assert after_stat.st_mtime_ns == before_stat.st_mtime_ns
    assert logic.authorize_report_access(
        _request(), report_id, now=expected_expiry
    ).allowed is False


def test_cutover후_보고서는_schema재생성뒤에도_legacy로_늘어나지않는다():
    old_report = "02" * 16
    new_report = "03" * 16
    with storage_db.connect() as conn:
        _seed_pre_cutover_report(conn, old_report)
        _rebuild_cutover_snapshot(conn)
        assert conn.execute(
            f"SELECT COUNT(*) FROM {store.TABLE_LEGACY_RESOURCES}"
        ).fetchone()[0] == 1

        _seed_pre_cutover_report(
            conn,
            new_report,
            generated_at="2026-08-29T10:00:00+09:00",
            created_at="2026-08-29T10:00:00+09:00",
        )
        store.ensure_schema(conn)
        assert store.legacy_access_for(
            conn, locator=new_report, now=1_800_000_000
        ) is None

        # access schema 둘이 함께 이전판으로 되돌아온 최악의 재생성에서도 코드의
        # 고정 cutover 뒤 created_at은 snapshot에 들어가지 않는다.
        _rebuild_cutover_snapshot(conn)
        rows = conn.execute(
            f"SELECT report_id FROM {store.TABLE_LEGACY_RESOURCES}"
        ).fetchall()
        assert [tuple(row) for row in rows] == [(old_report,)]


def test_cutover전_LINK는_ID_bearer_PUBLIC으로_승격되지않는다():
    report_id = "04" * 16
    with storage_db.connect() as conn:
        _seed_pre_cutover_report(conn, report_id)
        assert share_store.insert_new(
            conn,
            key="ab" * 16,
            company="LINK 회사",
            job="",
            report_id=report_id,
            now_iso="2026-08-01T10:00:00+09:00",
        )
        _rebuild_cutover_snapshot(conn)
        assert store.legacy_access_for(
            conn, locator=report_id, now=1_787_000_000
        ) is None

    assert logic.authorize_report_access(_request(), report_id).allowed is False
    assert logic.authorize_report_access(
        _request({KEY_COOKIE_NAME: "ab" * 16}), report_id
    ).role is logic.AccessRole.LINK


def test_cutover전_MEMBER는_유효초대와_같은email_session만_호환한다():
    report_id = "05" * 16
    owner_email = "legacy-owner@example.com"
    owner = auth_logic.create_session(
        owner_email, False, subject="google:legacy-owner-current"
    )
    stranger = auth_logic.create_session(
        "stranger@example.com", False, subject="google:legacy-owner-stranger"
    )
    with storage_db.connect() as conn:
        _seed_pre_cutover_report(conn, report_id)
        assert allowlist.invite(
            conn,
            email=owner_email,
            note="기존 MEMBER",
            now_iso="2026-08-01T10:00:00+09:00",
        )
        assert allowlist.invite(
            conn,
            email="stranger@example.com",
            note="다른 MEMBER",
            now_iso="2026-08-01T10:00:00+09:00",
        )
        assert dashboard_store.reserve_member_run(
            conn,
            run_id=report_id,
            actor_email=owner_email,
            day="2026-08-01",
            now_iso="2026-08-01T10:00:00+09:00",
        )
        assert dashboard_store.settle_member_run(
            conn,
            run_id=report_id,
            succeeded=True,
            report_id=report_id,
            now_iso="2026-08-01T10:01:00+09:00",
        )
        _rebuild_cutover_snapshot(conn)

    owner_request = _request(
        {auth_constants.SESSION_COOKIE_NAME: owner.token}
    )
    stranger_request = _request(
        {auth_constants.SESSION_COOKIE_NAME: stranger.token}
    )
    assert logic.authorize_report_access(
        owner_request, report_id, now=1_787_000_000
    ) == logic.AccessDecision(
        True, logic.AccessRole.MEMBER, "legacy_member_email"
    )
    assert logic.authorize_report_access(
        stranger_request, report_id, now=1_787_000_000
    ).allowed is False
    with storage_db.connect() as conn:
        assert allowlist.revoke(conn, owner_email)
    assert logic.authorize_report_access(
        owner_request, report_id, now=1_787_000_000
    ).allowed is False


def test_재로그인_MEMBER의_legacy_email소유권은_sub로_forward이관된다():
    report_id = "06" * 16
    email = "forward@example.com"
    with storage_db.connect() as conn:
        _seed_pre_cutover_report(conn, report_id)
        assert allowlist.invite(
            conn,
            email=email,
            note="forward migration",
            now_iso="2026-08-01T10:00:00+09:00",
        )
        assert dashboard_store.reserve_member_run(
            conn,
            run_id=report_id,
            actor_email=email,
            day="2026-08-01",
            now_iso="2026-08-01T10:00:00+09:00",
        )
        assert dashboard_store.settle_member_run(
            conn,
            run_id=report_id,
            succeeded=True,
            report_id=report_id,
            now_iso="2026-08-01T10:01:00+09:00",
        )
        _rebuild_cutover_snapshot(conn)
        assert store.migrate_legacy_member_bindings(
            conn,
            member_email=email,
            identity_subject="google:stable-forward-owner",
            now=1_787_000_000,
        ) == 1
        row = conn.execute(
            f"SELECT report_id, subject_hash FROM {store.TABLE_MEMBER_BINDINGS} "
            "WHERE run_id = ?",
            (report_id,),
        ).fetchone()
        assert tuple(row) == (
            report_id,
            store.subject_hash("google:stable-forward-owner"),
        )

    # 이관 뒤 이메일이 바뀌어도 같은 공급자 sub가 정식 소유권이다.
    renamed = auth_logic.create_session(
        "forward-renamed@example.com",
        False,
        subject="google:stable-forward-owner",
    )
    with storage_db.connect() as conn:
        assert allowlist.invite(
            conn,
            email="forward-renamed@example.com",
            note="변경된 이메일",
            now_iso="2026-08-28T20:10:00+09:00",
        )
    assert logic.authorize_report_access(
        _request({auth_constants.SESSION_COOKIE_NAME: renamed.token}),
        report_id,
        now=1_787_000_000,
    ).reason == "member_owner"


def test_MEMBER_forward이관_중간실패는_subject행을_하나도남기지않는다(
    monkeypatch,
):
    report_id = "07" * 16
    email = "rollback@example.com"
    with storage_db.connect() as conn:
        _seed_pre_cutover_report(conn, report_id)
        assert allowlist.invite(
            conn,
            email=email,
            note="rollback",
            now_iso="2026-08-01T10:00:00+09:00",
        )
        assert dashboard_store.reserve_member_run(
            conn,
            run_id=report_id,
            actor_email=email,
            day="2026-08-01",
            now_iso="2026-08-01T10:00:00+09:00",
        )
        assert dashboard_store.settle_member_run(
            conn,
            run_id=report_id,
            succeeded=True,
            report_id=report_id,
            now_iso="2026-08-01T10:01:00+09:00",
        )
        _rebuild_cutover_snapshot(conn)

        def fail_bind_report(*_args, **_kwargs):
            raise RuntimeError("시험용 report 결속 실패")

        monkeypatch.setattr(store, "bind_report", fail_bind_report)
        with pytest.raises(RuntimeError, match="시험용"):
            store.migrate_legacy_member_bindings(
                conn,
                member_email=email,
                identity_subject="google:rollback-owner",
                now=1_787_000_000,
            )
        assert conn.execute(
            f"SELECT COUNT(*) FROM {store.TABLE_MEMBER_BINDINGS} "
            "WHERE run_id = ?",
            (report_id,),
        ).fetchone()[0] == 0


def test_MEMBER는_현재초대와_자기_run_결속을_둘다_요구한다():
    run_a = "4" * 32
    run_b = "5" * 32
    member_a = auth_logic.create_session(
        "a@example.com", False, subject="google:member-a"
    )
    member_b = auth_logic.create_session(
        "b@example.com", False, subject="google:member-b"
    )
    with storage_db.connect() as conn:
        for email in ("a@example.com", "b@example.com"):
            assert allowlist.invite(
                conn, email=email, note="", now_iso="2026-08-28T10:00:00+09:00"
            )
        assert dashboard_store.reserve_member_run(
            conn,
            run_id=run_a,
            actor_email="a@example.com",
            day="2026-08-28",
            now_iso="2026-08-28T10:00:00+09:00",
        )
        assert store.bind_member_run(
            conn, run_id=run_a, identity_subject="google:member-a"
        )
        assert dashboard_store.reserve_member_run(
            conn,
            run_id=run_b,
            actor_email="b@example.com",
            day="2026-08-28",
            now_iso="2026-08-28T10:00:00+09:00",
        )
        assert store.bind_member_run(
            conn, run_id=run_b, identity_subject="google:member-b"
        )

    request_a = _request({auth_constants.SESSION_COOKIE_NAME: member_a.token})
    request_b = _request({auth_constants.SESSION_COOKIE_NAME: member_b.token})
    assert logic.authorize_report_access(request_a, run_a).role is logic.AccessRole.MEMBER
    assert logic.authorize_report_access(request_a, run_b).allowed is False
    assert logic.authorize_report_access(request_b, run_a).allowed is False

    with storage_db.connect() as conn:
        assert allowlist.revoke(conn, "a@example.com")
    assert logic.authorize_report_access(request_a, run_a).allowed is False


def test_이메일만_같은_legacy_MEMBER행과_재할당계정은_권한이_아니다():
    run_id = "a" * 32
    original = auth_logic.create_session(
        "reused@example.com", False, subject="google:original-person"
    )
    reassigned = auth_logic.create_session(
        "reused@example.com", False, subject="google:different-person"
    )
    with storage_db.connect() as conn:
        assert allowlist.invite(
            conn,
            email="reused@example.com",
            note="",
            now_iso="2026-08-28T10:00:00+09:00",
        )
        # cutover 전 이메일-only 사용량 행: subject 결속은 일부러 만들지 않는다.
        assert dashboard_store.reserve_member_run(
            conn,
            run_id=run_id,
            actor_email="reused@example.com",
            day="2026-08-28",
            now_iso="2026-08-28T10:00:00+09:00",
        )

    for session in (original, reassigned):
        decision = logic.authorize_report_access(
            _request({auth_constants.SESSION_COOKIE_NAME: session.token}), run_id
        )
        assert decision.allowed is False


def test_MEMBER는_이메일이_바뀌어도_같은_OAuth_subject면_소유권을_유지한다():
    run_id = "b" * 32
    old = auth_logic.create_session(
        "old@example.com", False, subject="google:stable-person"
    )
    renamed = auth_logic.create_session(
        "new@example.com", False, subject="google:stable-person"
    )
    with storage_db.connect() as conn:
        assert allowlist.invite(
            conn, email="old@example.com", note="", now_iso="2026-08-28T10:00:00+09:00"
        )
        assert allowlist.invite(
            conn, email="new@example.com", note="", now_iso="2026-08-28T10:00:00+09:00"
        )
        assert dashboard_store.reserve_member_run(
            conn,
            run_id=run_id,
            actor_email="old@example.com",
            day="2026-08-28",
            now_iso="2026-08-28T10:00:00+09:00",
        )
        assert store.bind_member_run(
            conn, run_id=run_id, identity_subject="google:stable-person"
        )

    assert logic.authorize_report_access(
        _request({auth_constants.SESSION_COOKIE_NAME: old.token}), run_id
    ).allowed
    assert logic.authorize_report_access(
        _request({auth_constants.SESSION_COOKIE_NAME: renamed.token}), run_id
    ).allowed


def test_LINK는_최초보고서와_자기_run만_열고_철회하면_즉시_닫힌다():
    key_a = "a1" * 16
    key_b = "b2" * 16
    baked_a = "6" * 32
    run_a = "7" * 32
    run_b = "8" * 32
    with storage_db.connect() as conn:
        assert share_store.insert_new(
            conn,
            key=key_a,
            company="A",
            job="",
            report_id=baked_a,
            now_iso="2026-08-28T10:00:00+09:00",
        )
        assert share_store.insert_new(
            conn,
            key=key_b,
            company="B",
            job="",
            report_id="9" * 32,
            now_iso="2026-08-28T10:00:00+09:00",
        )
        assert share_store.start_run(
            conn,
            key=key_a,
            run_id=run_a,
            started_at="2026-08-28T10:01:00+09:00",
            input_company="A",
            confirmed_company="A",
            company_id="A-id",
        )
        assert share_store.start_run(
            conn,
            key=key_b,
            run_id=run_b,
            started_at="2026-08-28T10:01:00+09:00",
            input_company="B",
            confirmed_company="B",
            company_id="B-id",
        )

    request_a = _request({KEY_COOKIE_NAME: key_a})
    assert logic.authorize_report_access(request_a, baked_a).role is logic.AccessRole.LINK
    assert logic.authorize_report_access(request_a, run_a).role is logic.AccessRole.LINK
    assert logic.authorize_report_access(request_a, run_b).allowed is False
    with storage_db.connect() as conn:
        assert share_store.delete(
            conn, key_a, revoked_at="2026-08-28T10:02:00+09:00"
        )
    assert logic.authorize_report_access(request_a, baked_a).allowed is False
    assert logic.authorize_report_access(request_a, run_a).allowed is False


def test_ADMIN은_매요청_현재목록을_다시보고_제거즉시_닫힌다(monkeypatch):
    report_id = "c" * 32
    monkeypatch.setenv(auth_constants.ENV_ADMIN_EMAILS, "admin@example.com")
    admin = auth_logic.create_session(
        "admin@example.com", True, subject="google:current-admin"
    )
    request = _request({auth_constants.SESSION_COOKIE_NAME: admin.token})
    assert logic.authorize_report_access(request, report_id).role is logic.AccessRole.ADMIN

    monkeypatch.setenv(auth_constants.ENV_ADMIN_EMAILS, "")
    assert logic.authorize_report_access(request, report_id).allowed is False


def test_휴지통은_모든_역할보다_먼저_공개채널을_닫는다(monkeypatch):
    report_id = "d" * 32
    monkeypatch.setenv(auth_constants.ENV_ADMIN_EMAILS, "admin@example.com")
    admin = auth_logic.create_session(
        "admin@example.com", True, subject="google:trash-admin"
    )
    with storage_db.connect() as conn:
        assert dashboard_store.trash_report(
            conn,
            report_id=report_id,
            actor_email="admin@example.com",
            reason="중복",
            now_iso="2026-08-28T10:00:00+09:00",
        )
    decision = logic.authorize_report_access(
        _request({auth_constants.SESSION_COOKIE_NAME: admin.token}), report_id
    )
    assert decision == logic.AccessDecision(False, None, "resource_revoked")


def test_GET_권한판정은_DB_파일과_행을_전혀_바꾸지않는다():
    report_id = "e" * 32
    grant = _grant(report_id)
    path = storage_db.default_db_path()
    before_bytes = path.read_bytes()
    before_stat = path.stat()

    decision = logic.authorize_report_access(
        _request({constants.PUBLIC_GRANT_COOKIE_NAME: grant.token}), report_id
    )

    after_stat = path.stat()
    assert decision.allowed is True
    assert path.read_bytes() == before_bytes
    assert after_stat.st_mtime_ns == before_stat.st_mtime_ns
    assert after_stat.st_size == before_stat.st_size


def test_같은_PUBLIC_grant의_동시_run결속은_모두_보존된다():
    first = "f" * 32
    grant = _grant(first)
    run_ids = tuple(f"{index:032x}" for index in range(1, 9))

    def bind(run_id: str) -> bool:
        with storage_db.connect() as conn:
            issued = store.issue_and_bind(
                conn,
                existing_token=grant.token,
                run_id=run_id,
            )
            return issued.reused

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        assert all(pool.map(bind, run_ids))

    with storage_db.connect_readonly_existing() as conn:
        assert conn is not None
        assert all(
            store.public_grant_allows(
                conn, raw_token=grant.token, locator=run_id
            )
            for run_id in run_ids
        )


def test_만료grant와binding은_FK가_꺼져도_발급transaction에서_같이_정리된다():
    conn = sqlite3.connect(":memory:")
    try:
        store.ensure_schema(conn)
        conn.commit()
        conn.execute("PRAGMA foreign_keys=OFF")
        old_hash = hashlib.sha256(b"old-token").hexdigest()
        conn.execute(
            f"INSERT INTO {store.TABLE_GRANTS} VALUES (?, 10, 20, 0)",
            (old_hash,),
        )
        conn.execute(
            f"INSERT INTO {store.TABLE_BINDINGS} VALUES (?, ?, '', 10)",
            (old_hash, "c" * 32),
        )
        conn.commit()

        issued = store.issue_and_bind(
            conn, existing_token="", run_id="d" * 32, now=20
        )
        assert issued.token
        assert conn.execute(
            f"SELECT COUNT(*) FROM {store.TABLE_GRANTS} WHERE grant_hash=?",
            (old_hash,),
        ).fetchone()[0] == 0
        assert conn.execute(
            f"SELECT COUNT(*) FROM {store.TABLE_BINDINGS} WHERE grant_hash=?",
            (old_hash,),
        ).fetchone()[0] == 0
    finally:
        conn.close()


def test_DB_trigger와_정책이_active_grant와_브라우저binding_상한을_강제한다():
    conn = sqlite3.connect(":memory:")
    try:
        store.ensure_schema(conn)
        for index in range(constants.PUBLIC_ACTIVE_GRANT_LIMIT):
            conn.execute(
                f"INSERT INTO {store.TABLE_GRANTS} VALUES (?, 100, 1000, 0)",
                (f"{index:064x}",),
            )
        with pytest.raises(sqlite3.IntegrityError, match="capacity"):
            conn.execute(
                f"INSERT INTO {store.TABLE_GRANTS} VALUES (?, 100, 1000, 0)",
                ("f" * 64,),
            )
        with pytest.raises(RuntimeError, match="수용량"):
            store.issue_and_bind(
                conn, existing_token="", run_id="e" * 32, now=500
            )

        # 별도 DB에서 한 grant의 결속 상한도 trigger가 우회 삽입을 막는다.
        second = sqlite3.connect(":memory:")
        try:
            store.ensure_schema(second)
            grant_hash = "1" * 64
            second.execute(
                f"INSERT INTO {store.TABLE_GRANTS} VALUES (?, 100, 1000, 0)",
                (grant_hash,),
            )
            for index in range(constants.PUBLIC_BINDINGS_PER_GRANT_LIMIT):
                second.execute(
                    f"INSERT INTO {store.TABLE_BINDINGS} VALUES (?, ?, '', 100)",
                    (grant_hash, f"{index:032x}"),
                )
            with pytest.raises(sqlite3.IntegrityError, match="capacity"):
                second.execute(
                    f"INSERT INTO {store.TABLE_BINDINGS} VALUES (?, ?, '', 100)",
                    (grant_hash, "f" * 32),
                )
        finally:
            second.close()
    finally:
        conn.close()


def test_초판_NO_ACTION_FK는_행을_보존해_CASCADE로_전환한다():
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute(
            f"""CREATE TABLE {store.TABLE_GRANTS} (
                grant_hash TEXT PRIMARY KEY, created_at REAL NOT NULL,
                expires_at REAL NOT NULL, revoked_at REAL NOT NULL DEFAULT 0)"""
        )
        conn.execute(
            f"""CREATE TABLE {store.TABLE_BINDINGS} (
                grant_hash TEXT NOT NULL, run_id TEXT NOT NULL,
                report_id TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL,
                PRIMARY KEY(grant_hash, run_id),
                FOREIGN KEY(grant_hash) REFERENCES {store.TABLE_GRANTS}(grant_hash))"""
        )
        grant_hash = "2" * 64
        conn.execute(
            f"INSERT INTO {store.TABLE_GRANTS} VALUES (?, 100, 1000, 0)",
            (grant_hash,),
        )
        conn.execute(
            f"INSERT INTO {store.TABLE_BINDINGS} VALUES (?, ?, '', 100)",
            (grant_hash, "3" * 32),
        )

        store.ensure_schema(conn)

        assert conn.execute(
            f"SELECT grant_hash, run_id FROM {store.TABLE_BINDINGS}"
        ).fetchall() == [(grant_hash, "3" * 32)]
        fk = conn.execute(
            f"PRAGMA foreign_key_list({store.TABLE_BINDINGS})"
        ).fetchone()
        assert str(fk[6]).upper() == "CASCADE"
    finally:
        conn.close()


def test_schema_registry는_report_access를_운영복구목록에_포함한다():
    from src.core.persistent_schema import PERSISTENT_SCHEMA_BOOTSTRAPS

    matches = [
        item
        for item in PERSISTENT_SCHEMA_BOOTSTRAPS
        if item.module_name == "src.features.report_access.store"
    ]
    assert len(matches) == 1
