"""내부 cron 인증 경계의 단위 시험."""

from starlette.requests import Request

from src.web import internal_cron


def _request(authorization: str | None) -> Request:
    headers = []
    if authorization is not None:
        headers.append((b"authorization", authorization.encode("ascii")))
    return Request({"type": "http", "method": "POST", "path": "/", "headers": headers})


def _raw_request(authorization: bytes) -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [(b"authorization", authorization)],
        }
    )


def test_exact_bearer만_인증한다() -> None:
    expected = "s" * 32

    assert internal_cron.has_valid_bearer(_request(f"Bearer {expected}"), expected)
    assert not internal_cron.has_valid_bearer(_request(None), expected)
    assert not internal_cron.has_valid_bearer(_request(expected), expected)
    assert not internal_cron.has_valid_bearer(_request(f"Basic {expected}"), expected)
    assert not internal_cron.has_valid_bearer(_request("Bearer wrong"), expected)


def test_인증실패_응답은_일관된_bearer_challenge를_준다() -> None:
    response = internal_cron.unauthorized_response()

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.body == b'{"status":"unauthorized"}'


def test_비ASCII_bearer도_예외없이_상수시간_bytes비교뒤_401로_닫는다() -> None:
    request = _raw_request(b"Bearer \xe9")

    valid = internal_cron.has_valid_bearer(request, "s" * 32)
    response = None if valid else internal_cron.unauthorized_response()

    assert valid is False
    assert response is not None and response.status_code == 401
