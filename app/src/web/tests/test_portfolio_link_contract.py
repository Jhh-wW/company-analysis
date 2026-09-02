"""포트폴리오 링크 계약(render-portfolio-link-v1)이 admin.py·main.py의 코드를
전혀 고치지 않고도 친구 입구(LINK 발급·초대·``/k/``)를 연다는 것과, 그러면서도
Host 고정·CSRF Origin 고정 같은 보안판정은 옛 관리자 계약과 똑같이 켜져 있다는
것을 함께 못 박는다.

정본: G-S1 배포 계약 신설 티켓. ``routers/admin.py``·``routers/analysis.py``는
이 작업의 소유권 밖이라 **읽기만** 했다 — 아래 시험은 그 두 파일을 한 글자도
바꾸지 않고도 새 계약에서 다른 결과가 나온다는 것으로 그 사실을 증명한다.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.pipeline.demo import DemoPipeline
from src.features.sharelink import allowlist as share_allow
from src.features.sharelink import store as share_store
from src.features.sharelink.constants import KEY_COOKIE_NAME
from src.features.storage import db as storage_db
from src.web import deployment_mode, main, runtime

SESSION = auth_constants.SESSION_COOKIE_NAME
PUBLIC_ORIGIN = "https://portfolio.example"
HOST = "portfolio.example"


def _enable_portfolio_link(monkeypatch) -> None:
    monkeypatch.setenv(
        deployment_mode.ENV_DEPLOYMENT_RUNTIME_CONTRACT,
        deployment_mode.RENDER_PORTFOLIO_LINK_CONTRACT,
    )
    monkeypatch.setenv(deployment_mode.ENV_PUBLIC_ORIGIN, PUBLIC_ORIGIN)
    monkeypatch.setenv(auth_constants.ENV_BETA_ADMIN_ONLY, "1")


@pytest.fixture
def client():
    runtime._PIPELINE = DemoPipeline()
    # ★ https 기본 — 이 계약은 fixed_public_https_origin()이 켜져 있어 발급 쿠키가
    #   Secure로 나간다. http://testserver로 두면 그 쿠키가 다음 요청에 안 실려서
    #   테스트가 «시험 코드 버그»로 로그인 화면에 튕긴다(실측: Path=/;Secure).
    with TestClient(main.app, base_url="https://testserver") as test_client:
        yield test_client


@pytest.fixture
def admin(client: TestClient) -> TestClient:
    """관리자로 로그인하고, 이 계약이 요구하는 Host·Origin을 기본으로 붙인 손님."""
    session = auth_logic.create_session("admin@example.com", True)
    client.cookies.set(SESSION, session.token)
    csrf = auth_logic.csrf_token_for_session(session.token)
    original_post = client.post
    original_get = client.get

    def _with_pinned_headers(kwargs: dict) -> dict:
        headers = dict(kwargs.pop("headers", {}) or {})
        headers.setdefault("Host", HOST)
        kwargs["headers"] = headers
        return kwargs

    def post_with_csrf(url, *args, **kwargs):
        data = dict(kwargs.pop("data", {}) or {})
        data.setdefault("csrf_token", csrf)
        kwargs = _with_pinned_headers(kwargs)
        kwargs["headers"].setdefault("Origin", PUBLIC_ORIGIN)
        return original_post(url, *args, data=data, **kwargs)

    def get_pinned(url, *args, **kwargs):
        kwargs = _with_pinned_headers(kwargs)
        return original_get(url, *args, **kwargs)

    client.post = post_with_csrf
    client.get = get_pinned
    client._csrf_for_test = csrf
    # 중복 헤더처럼 dict로 옮기면 뭉개지는 원시 요청을 위해 감싸지 않은 원본도 남긴다.
    client._raw_get = original_get
    client._raw_post = original_post
    return client


# ══════════════════════════════════════════════════════════
# ① admin.py를 고치지 않고도 LINK 발급·초대가 열린다
# ══════════════════════════════════════════════════════════


def test_포트폴리오_계약은_LINK_발급을_admin_py_수정없이_연다(
    admin: TestClient, monkeypatch
):
    _enable_portfolio_link(monkeypatch)
    with storage_db.connect() as conn:
        before = len(share_store.list_all(conn))

    response = admin.post(
        "/admin/link/new",
        data={"company": "카카오", "job": "마케팅"},
        follow_redirects=False,
    )

    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    assert "/k/" in response.text
    with storage_db.connect() as conn:
        after = len(share_store.list_all(conn))
    assert after == before + 1


def test_포트폴리오_계약은_친구_초대를_admin_py_수정없이_연다(
    admin: TestClient, monkeypatch
):
    _enable_portfolio_link(monkeypatch)

    response = admin.post(
        "/admin/invite",
        data={"email": "friend@example.com"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/access"
    with storage_db.connect() as conn:
        member = share_allow.load(conn, "friend@example.com")
    assert member is not None


def test_같은_시험을_옛_관리자_실제분석_계약에서_돌리면_여전히_막힌다(
    admin: TestClient, monkeypatch
):
    """대조군 — 위 두 시험이 «계약을 안 바꿔도 원래 열려 있던 것»이 아님을 보인다."""
    monkeypatch.setenv(
        deployment_mode.ENV_DEPLOYMENT_RUNTIME_CONTRACT,
        deployment_mode.RENDER_ADMIN_REAL_NO_FORWARDED_CONTRACT,
    )
    monkeypatch.setenv(deployment_mode.ENV_PUBLIC_ORIGIN, PUBLIC_ORIGIN)
    monkeypatch.setenv(auth_constants.ENV_BETA_ADMIN_ONLY, "1")

    link_response = admin.post(
        "/admin/link/new",
        data={"company": "카카오", "job": "마케팅"},
        follow_redirects=False,
    )
    member_response = admin.post(
        "/admin/invite",
        data={"email": "friend@example.com"},
        follow_redirects=False,
    )

    assert link_response.status_code == 404
    assert member_response.status_code == 409


# ══════════════════════════════════════════════════════════
# ② main.py를 고치지 않고도 /k/ 입구와 LINK 쿠키가 열린다
# ══════════════════════════════════════════════════════════


def test_포트폴리오_계약은_k_입구를_main_py_수정없이_연다(
    client: TestClient, monkeypatch
):
    _enable_portfolio_link(monkeypatch)
    key = "f" * 32
    with storage_db.connect() as conn:
        assert share_store.insert_new(
            conn,
            key=key,
            company="카카오",
            job="마케팅",
            now_iso="2026-09-01T10:00:00",
        )

    opened = client.get(f"/k/{key}", headers={"Host": HOST}, follow_redirects=False)

    assert opened.status_code == 303
    assert opened.headers["location"] == "/"
    assert KEY_COOKIE_NAME in opened.cookies

    home = client.get("/", headers={"Host": HOST}, follow_redirects=False)
    assert home.status_code == 200


def test_옛_관리자_계약에서는_같은_k_입구가_로그인으로_막힌다(
    client: TestClient, monkeypatch
):
    """대조군 — /k/가 원래부터 열려 있던 게 아니라 이 계약이라서 열린 것임을 보인다."""
    monkeypatch.setenv(
        deployment_mode.ENV_DEPLOYMENT_RUNTIME_CONTRACT,
        deployment_mode.RENDER_ADMIN_REAL_NO_FORWARDED_CONTRACT,
    )
    monkeypatch.setenv(deployment_mode.ENV_PUBLIC_ORIGIN, PUBLIC_ORIGIN)
    monkeypatch.setenv(auth_constants.ENV_BETA_ADMIN_ONLY, "1")
    key = "e" * 32
    with storage_db.connect() as conn:
        assert share_store.insert_new(
            conn,
            key=key,
            company="카카오",
            job="마케팅",
            now_iso="2026-09-01T10:00:00",
        )

    opened = client.get(f"/k/{key}", headers={"Host": HOST}, follow_redirects=False)

    assert opened.status_code == 303
    assert opened.headers["location"] == "/auth/login"


# ══════════════════════════════════════════════════════════
# ③ 그래도 Host 고정·CSRF Origin 고정은 옛 계약과 똑같이 켜져 있다
#    (render_admin_no_forwarded()가 아니라 render_pinned_origin_no_forwarded()로
#    옮긴 이유 — 이 계약도 forwarded 헤더를 안 믿는 같은 실행 모델이다)
# ══════════════════════════════════════════════════════════


def test_포트폴리오_계약도_잘못된_Host는_400으로_막는다(
    client: TestClient, monkeypatch
):
    _enable_portfolio_link(monkeypatch)

    spoofed = client.get(
        "/", headers={"Host": "attacker.example"}, follow_redirects=False
    )
    duplicate = client.get(
        "/",
        headers=[("Host", HOST), ("Host", "attacker.example")],
        follow_redirects=False,
    )

    assert spoofed.status_code == 400
    assert duplicate.status_code == 400


def test_포트폴리오_계약도_CSRF_Origin_불일치_POST를_거부한다(
    admin: TestClient, monkeypatch
):
    _enable_portfolio_link(monkeypatch)

    wrong_origin = admin.post(
        "/admin/invite",
        data={"email": "friend@example.com"},
        headers={"Origin": "https://attacker.example"},
        follow_redirects=False,
    )

    assert wrong_origin.status_code == 403
    with storage_db.connect() as conn:
        assert share_allow.load(conn, "friend@example.com") is None


def test_포트폴리오_계약도_중복_Origin_POST를_거부한다(
    admin: TestClient, monkeypatch
):
    """dict 헤더로는 중복을 못 만들어 원본 client.post로 직접 두 Origin을 보낸다."""
    _enable_portfolio_link(monkeypatch)

    duplicate_origin = admin._raw_post(
        "/admin/invite",
        data={"email": "friend3@example.com", "csrf_token": admin._csrf_for_test},
        headers=[
            ("Host", HOST),
            ("Origin", PUBLIC_ORIGIN),
            ("Origin", "https://attacker.example"),
        ],
        follow_redirects=False,
    )

    assert duplicate_origin.status_code == 403
    with storage_db.connect() as conn:
        assert share_allow.load(conn, "friend3@example.com") is None
