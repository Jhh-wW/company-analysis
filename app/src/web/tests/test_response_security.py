import pytest

from starlette.applications import Starlette
from starlette.responses import (
    HTMLResponse,
    JSONResponse,
    PlainTextResponse,
    RedirectResponse,
)
from starlette.routing import Route
from starlette.testclient import TestClient

from src.features.auth import constants as auth_constants
from src.features.pipeline.demo import DemoPipeline
from src.web import deployment_mode, main, runtime
from src.web.response_security import CSP_POLICY, ResponseSecurityMiddleware


async def _page(_request):
    return PlainTextResponse("ok", headers={"Vary": "Accept-Encoding"})


async def _form_page(_request):
    return HTMLResponse('<form method="post" action="/submit"></form>')


async def _health(_request):
    return JSONResponse({"status": "ok"})


async def _readiness(_request):
    return JSONResponse({"status": "ready"})


async def _redirect(_request):
    return RedirectResponse("/", status_code=303)


def _client(*, base_url: str = "http://testserver") -> TestClient:
    app = Starlette(
        routes=[
            Route("/", _page),
            Route("/form", _form_page),
            Route("/static/a.css", _page),
            Route("/healthz", _health),
            Route("/readyz", _readiness),
            Route("/k/{key}", _redirect),
            Route("/auth/callback", _redirect),
            Route("/auth/local-demo/start", _redirect),
            Route("/ordinary-redirect", _redirect),
        ]
    )
    app.add_middleware(ResponseSecurityMiddleware)
    return TestClient(app, base_url=base_url)


def test_개인화응답은_저장하지_않고_브라우저보안헤더를_붙인다():
    with _client(base_url="https://testserver") as client:
        response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-security-policy"] == CSP_POLICY
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["referrer-policy"] == "same-origin"
    assert response.headers["permissions-policy"] == (
        "camera=(), microphone=(), geolocation=()"
    )
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["vary"] == "Accept-Encoding, Cookie"


def test_POST_form_HTML문서는_same_origin_정책을_쓴다():
    with _client() as client:
        response = client.get("/form")

    assert response.headers["referrer-policy"] == "same-origin"


@pytest.mark.parametrize(
    "path",
    (
        "/k/" + "a" * 32 + "?from=messenger",
        "/auth/callback?code=authorization-secret&state=" + "s" * 43,
        "/auth/local-demo/start?token=" + "ab" * 32,
    ),
)
def test_민감한_교환주소는_다음요청에_원래_URL을_Referer로_넘기지않는다(path):
    with _client() as client:
        response = client.get(path, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["referrer-policy"] == "no-referrer"


def test_일반_이동응답은_기존_same_origin_정책을_유지한다():
    with _client() as client:
        response = client.get("/ordinary-redirect", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["referrer-policy"] == "same-origin"


def test_https에서만_hsts를_붙인다():
    with _client() as client:
        http_response = client.get("/")
    with _client(base_url="https://testserver") as client:
        https_response = client.get("/")

    assert "strict-transport-security" not in http_response.headers
    assert https_response.headers["strict-transport-security"] == "max-age=31536000"


@pytest.mark.parametrize(
    "contract",
    (
        deployment_mode.RENDER_ADMIN_DEMO_NO_FORWARDED_CONTRACT,
        deployment_mode.RENDER_ADMIN_REAL_NO_FORWARDED_CONTRACT,
    ),
)
def test_좁은Render계약은_내부HTTP에서도_고정HTTPS출처의_HSTS를_붙인다(
    monkeypatch, contract,
):
    monkeypatch.setenv(
        deployment_mode.ENV_DEPLOYMENT_RUNTIME_CONTRACT,
        contract,
    )
    monkeypatch.setenv(deployment_mode.ENV_PUBLIC_ORIGIN, "https://demo.example")

    with _client(base_url="http://render-internal:10000") as client:
        response = client.get("/", headers={"Host": "demo.example"})

    assert response.status_code == 200
    assert response.headers["strict-transport-security"] == "max-age=31536000"


@pytest.mark.parametrize(
    "contract",
    (
        deployment_mode.RENDER_ADMIN_DEMO_NO_FORWARDED_CONTRACT,
        deployment_mode.RENDER_ADMIN_REAL_NO_FORWARDED_CONTRACT,
    ),
)
def test_좁은Render계약은_Host를_PUBLIC_ORIGIN에_고정하고_forwarded는_무시한다(
    monkeypatch, contract,
):
    monkeypatch.setenv(
        deployment_mode.ENV_DEPLOYMENT_RUNTIME_CONTRACT,
        contract,
    )
    monkeypatch.setenv(deployment_mode.ENV_PUBLIC_ORIGIN, "https://demo.example")

    with _client(base_url="http://render-internal:10000") as client:
        accepted = client.get(
            "/",
            headers={
                "Host": "demo.example",
                "X-Forwarded-Host": "attacker.example",
                "X-Forwarded-Proto": "http",
            },
        )
        rejected = client.get(
            "/",
            headers={
                "Host": "attacker.example",
                "X-Forwarded-Host": "demo.example",
                "X-Forwarded-Proto": "https",
            },
        )
        duplicate = client.get(
            "/",
            headers=[
                ("Host", "demo.example"),
                ("Host", "attacker.example"),
            ],
        )

    assert accepted.status_code == 200
    assert rejected.status_code == 400
    assert duplicate.status_code == 400


@pytest.mark.parametrize(
    "contract",
    (
        deployment_mode.RENDER_ADMIN_DEMO_NO_FORWARDED_CONTRACT,
        deployment_mode.RENDER_ADMIN_REAL_NO_FORWARDED_CONTRACT,
    ),
)
def test_좁은Render계약의_loopback_상태확인은_Host고정과_충돌하지않는다(
    monkeypatch, contract,
):
    monkeypatch.setenv(
        deployment_mode.ENV_DEPLOYMENT_RUNTIME_CONTRACT,
        contract,
    )
    monkeypatch.setenv(deployment_mode.ENV_PUBLIC_ORIGIN, "https://demo.example")

    with _client(base_url="http://127.0.0.1:10000") as client:
        health = client.get("/healthz")
        readiness = client.get("/readyz")

    assert health.status_code == 200
    assert health.json() == {"status": "ok"}
    assert readiness.status_code == 200
    assert readiness.json() == {"status": "ready"}


def test_기존모드는_PUBLIC_ORIGIN만으로_내부HTTP_HSTS나_Host고정을_켜지않는다(
    monkeypatch,
):
    monkeypatch.setenv(deployment_mode.ENV_PUBLIC_ORIGIN, "https://demo.example")
    monkeypatch.delenv(
        deployment_mode.ENV_DEPLOYMENT_RUNTIME_CONTRACT, raising=False
    )

    with _client(base_url="http://render-internal:10000") as client:
        response = client.get("/", headers={"Host": "attacker.example"})

    assert response.status_code == 200
    assert "strict-transport-security" not in response.headers


def test_정적파일의_캐시정책은_덮어쓰지_않는다():
    with _client() as client:
        response = client.get("/static/a.css")

    assert "cache-control" not in response.headers
    assert response.headers["vary"] == "Accept-Encoding"
    assert response.headers["x-content-type-options"] == "nosniff"


def _assert_main_security_headers(response) -> None:
    assert response.headers["content-security-policy"] == CSP_POLICY
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["cache-control"] == "private, no-store"
    assert "cookie" in response.headers["vary"].lower()


def test_실제앱의_정상_없는주소_큰요청에도_보안헤더가_붙는다(monkeypatch):
    monkeypatch.setattr(runtime, "_PIPELINE", DemoPipeline())
    with TestClient(main.app, base_url="https://testserver") as client:
        responses = [
            client.get("/"),
            client.get("/없는-주소"),
            client.post(
                "/admin/invite",
                content=b"x" * (64 * 1024 + 1),
                headers={"Content-Type": "application/octet-stream"},
            ),
        ]

    assert [response.status_code for response in responses] == [200, 404, 413]
    for response in responses:
        _assert_main_security_headers(response)
        assert response.headers["strict-transport-security"] == "max-age=31536000"


def test_관리자게이트가_직접_보내는_이동응답도_보안헤더가_붙는다(monkeypatch):
    monkeypatch.setattr(runtime, "_PIPELINE", DemoPipeline())
    monkeypatch.setenv(auth_constants.ENV_BETA_ADMIN_ONLY, "1")
    with TestClient(main.app, base_url="https://testserver") as client:
        response = client.get("/", follow_redirects=False)

    assert response.status_code == 303
    _assert_main_security_headers(response)
