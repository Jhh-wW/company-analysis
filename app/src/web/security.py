"""웹 요청이 라우트 파서에 닿기 전 적용하는 작은 보안 경계."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from starlette.responses import PlainTextResponse

ASGIApp = Callable[
    [
        dict[str, Any],
        Callable[..., Awaitable[dict]],
        Callable[..., Awaitable[None]],
    ],
    Awaitable[None],
]

# URL 인코딩된 한글은 글자 하나가 여러 바이트로 늘어난다. 공고 원문 10만 자를
# 충분히 받되, 정상 폼이라고 보기 어려운 큰 본문은 파싱 전에 닫는다.
FORM_BODY_MAX_BYTES = 1 * 1024 * 1024
# 회사 분석 `/run`도 텍스트 폼뿐이다. 레거시 multipart 형식에만 큰 예외를 주면
# 사라진 이미지 입력을 통해 공개 경로가 불필요한 메모리를 받게 된다.
RUN_BODY_MAX_BYTES = FORM_BODY_MAX_BYTES

# 관리자 폼과 로그아웃은 큰 본문이 필요 없다.
DEFAULT_MUTATION_BODY_MAX_BYTES = 64 * 1024

COMPANY_MAX_CHARS = 120
JOB_MAX_CHARS = 80
REGION_MAX_CHARS = 120
POSTING_TEXT_MAX_CHARS = 100_000
LEGAL_NAME_MAX_CHARS = 200
ADDRESS_MAX_CHARS = 500
REFERENCE_MAX_CHARS = 256
ATTEMPT_TOKEN_MAX_CHARS = 128
NOTE_MAX_CHARS = 500
EMAIL_MAX_CHARS = 254
CSRF_TOKEN_MAX_CHARS = 128


class RequestBodyTooLarge(Exception):
    """스트리밍 중 요청 본문이 경로별 상한을 넘었다."""


def body_limit_for(scope: dict[str, Any]) -> int | None:
    """변경 요청에 적용할 상한. 읽기 요청은 본문을 쓰지 않으므로 건드리지 않는다."""
    if scope.get("type") != "http" or scope.get("method", "").upper() not in {
        "POST",
        "PUT",
        "PATCH",
        "DELETE",
    }:
        return None
    path = str(scope.get("path", ""))
    if path == "/run":
        return RUN_BODY_MAX_BYTES
    if path in {"/confirm", "/reject"}:
        return FORM_BODY_MAX_BYTES
    return DEFAULT_MUTATION_BODY_MAX_BYTES


def _content_length(scope: dict[str, Any]) -> int | None:
    for raw_name, raw_value in scope.get("headers", []):
        if raw_name.lower() != b"content-length":
            continue
        try:
            value = int(raw_value.decode("ascii"))
        except (UnicodeDecodeError, ValueError):
            return None
        return value if value >= 0 else None
    return None


class RequestBodyLimitMiddleware:
    """Content-Length가 없거나 거짓이어도 실제 ASGI body 조각을 세어 413으로 닫는다.

    ``BaseHTTPMiddleware``는 본문을 미리 버퍼링할 수 있어 쓰지 않는다. 이 순수 ASGI
    래퍼는 FastAPI의 form/multipart 파서가 실행되기 전에 ``receive``를 감싼다.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        limit = body_limit_for(scope)
        if limit is None:
            await self.app(scope, receive, send)
            return

        declared = _content_length(scope)
        if declared is not None and declared > limit:
            await self._reject(scope, receive, send)
            return

        seen = 0
        too_large = False
        pending_messages = []

        async def limited_receive():
            nonlocal seen, too_large
            message = await receive()
            if message.get("type") == "http.request":
                seen += len(message.get("body", b""))
                if seen > limit:
                    too_large = True
                    raise RequestBodyTooLarge
            return message

        async def tracked_send(message):
            # Starlette의 form parser는 receive 예외를 400으로 바꾼다. 상한을 넘긴
            # 사실을 우리가 이미 알면 그 안쪽 응답을 버리고 바깥에서 413을 보낸다.
            if too_large:
                return
            pending_messages.append(message)

        try:
            await self.app(scope, limited_receive, tracked_send)
        except RequestBodyTooLarge:
            too_large = True
        if too_large:
            await self._reject(scope, receive, send)
            return
        for message in pending_messages:
            await send(message)

    @staticmethod
    async def _reject(scope, receive, send) -> None:
        response = PlainTextResponse(
            "요청 내용이 너무 큽니다. 글자나 이미지 크기를 줄여주세요.",
            status_code=413,
        )
        await response(scope, receive, send)
