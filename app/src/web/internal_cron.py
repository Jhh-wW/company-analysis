"""백업·정기작업 내부 경로가 공유하는 인증 응답 경계."""

from __future__ import annotations

import hmac

from fastapi import Request
from fastapi.responses import JSONResponse


def has_valid_bearer(request: Request, expected: str) -> bool:
    """Authorization 값이 exact Bearer 형식이고 비밀이 일치하는지 판정한다."""
    authorization = request.headers.get("Authorization", "")
    scheme, separator, supplied = authorization.partition(" ")
    if not separator or scheme.lower() != "bearer" or not supplied:
        return False
    try:
        supplied_bytes = supplied.encode("utf-8")
        expected_bytes = expected.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return hmac.compare_digest(supplied_bytes, expected_bytes)


def unauthorized_response() -> JSONResponse:
    """내부 경로의 인증 실패 형식을 한곳에서 유지한다."""
    return JSONResponse(
        {"status": "unauthorized"},
        status_code=401,
        headers={"WWW-Authenticate": "Bearer"},
    )
