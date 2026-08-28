"""모든 웹 응답에 공통으로 붙이는 브라우저 보안 경계."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import PlainTextResponse

from src.web import deployment_mode

ASGIApp = Callable[
    [
        dict[str, Any],
        Callable[..., Awaitable[dict]],
        Callable[..., Awaitable[None]],
    ],
    Awaitable[None],
]
HOST_INDEPENDENT_HEALTH_PATHS = frozenset({"/healthz", "/readyz"})
# URL 안에 권한을 담아 짧은 쿠키·세션으로 교환하는 입구다. 응답이 이동이든
# 거절 화면이든 원래 URL을 다음 same-origin 요청의 Referer로 보내면 접근 로그에
# root token·OAuth code/state·LINK가 다시 생길 수 있으므로 경로 전체를 닫는다.
SENSITIVE_EXCHANGE_PATHS = frozenset(
    {"/auth/callback", "/auth/local-demo/start"}
)

CSP_POLICY = (
    "default-src 'self'; "
    "base-uri 'self'; "
    "object-src 'none'; "
    "frame-ancestors 'none'; "
    "img-src 'self' data: blob:; "
    "style-src 'self' 'unsafe-inline'; "
    "script-src 'self' 'unsafe-inline'; "
    "connect-src 'self'; "
    "form-action 'self'"
)
HTML_REFERRER_POLICY = "same-origin"
CAPABILITY_REDIRECT_REFERRER_POLICY = "no-referrer"


class ResponseSecurityMiddleware:
    """캐시·클릭재킹·콘텐츠 형식 오해를 응답 한 곳에서 차단한다."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_security_headers(message):
            if message.get("type") == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Content-Security-Policy"] = CSP_POLICY
                headers["X-Content-Type-Options"] = "nosniff"
                headers["X-Frame-Options"] = "DENY"
                path = str(scope.get("path", ""))
                content_type = headers.get("Content-Type", "").casefold()
                if path.startswith("/k/") or path in SENSITIVE_EXCHANGE_PATHS:
                    # URL 자체가 capability인 교환 응답은 다음 문서에 원래 경로와
                    # query를 넘기지 않는다. 실패 응답도 같은 비밀 URL을 보존한다.
                    headers["Referrer-Policy"] = CAPABILITY_REDIRECT_REFERRER_POLICY
                elif content_type.startswith("text/html"):
                    # Chromium은 no-referrer 문서의 same-origin form POST Origin도
                    # `null`로 만든다. HTML form 문서는 tuple Origin을 보존해야 한다.
                    headers["Referrer-Policy"] = HTML_REFERRER_POLICY
                elif "Referrer-Policy" not in headers:
                    headers["Referrer-Policy"] = HTML_REFERRER_POLICY
                headers["Permissions-Policy"] = (
                    "camera=(), microphone=(), geolocation=()"
                )
                if (
                    scope.get("scheme") == "https"
                    or deployment_mode.fixed_public_https_origin()
                ):
                    headers["Strict-Transport-Security"] = "max-age=31536000"

                if not path.startswith("/static/"):
                    headers["Cache-Control"] = "private, no-store"
                    headers["Pragma"] = "no-cache"
                    vary = [
                        value.strip()
                        for value in headers.get("Vary", "").split(",")
                        if value.strip()
                    ]
                    if "cookie" not in {value.casefold() for value in vary}:
                        vary.append("Cookie")
                    headers["Vary"] = ", ".join(vary)
            await send(message)

        if (
            deployment_mode.render_admin_no_forwarded()
            and str(scope.get("path", "")) not in HOST_INDEPENDENT_HEALTH_PATHS
        ):
            host_values = Headers(scope=scope).getlist("host")
            if len(host_values) != 1 or not deployment_mode.configured_public_host_matches(
                host_values[0]
            ):
                response = PlainTextResponse("잘못된 요청입니다.", status_code=400)
                await response(scope, receive, send_with_security_headers)
                return

        await self.app(scope, receive, send_with_security_headers)
