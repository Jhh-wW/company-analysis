"""모든 웹 응답에 공통으로 붙이는 브라우저 보안 경계."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from starlette.datastructures import MutableHeaders

ASGIApp = Callable[
    [
        dict[str, Any],
        Callable[..., Awaitable[dict]],
        Callable[..., Awaitable[None]],
    ],
    Awaitable[None],
]

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
                status = int(message.get("status", 0))
                content_type = headers.get("Content-Type", "").casefold()
                if path.startswith("/k/") and 300 <= status < 400:
                    # URL 자체가 capability인 최초 303은 다음 문서에 경로를 넘기지 않는다.
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
                if scope.get("scheme") == "https":
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

        await self.app(scope, receive, send_with_security_headers)
