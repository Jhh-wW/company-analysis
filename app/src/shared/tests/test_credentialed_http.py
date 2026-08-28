import io
import urllib.request
from email.message import Message

import pytest

from src.shared import credentialed_http


class _Response:
    def __init__(self, body: bytes, *, content_length: str | None = None) -> None:
        self.body = body
        self.read_sizes: list[int] = []
        self.headers = Message()
        if content_length is not None:
            self.headers["Content-Length"] = content_length

    def read(self, amount: int) -> bytes:
        self.read_sizes.append(amount)
        return self.body[:amount]


def test_redirect_handler는_인증헤더가_새_host로_복사되는_요청을_만들지_않는다() -> None:
    request = urllib.request.Request(
        "https://provider.example/userinfo",
        headers={"Authorization": "Bearer secret"},
    )

    redirected = credentialed_http.NoCredentialRedirect().redirect_request(
        request,
        io.BytesIO(),
        302,
        "Found",
        {"Location": "https://attacker.example/collect"},
        "https://attacker.example/collect",
    )

    assert redirected is None


def test_선언값과_실제_body를_같은_고정상한으로_막는다() -> None:
    declared = _Response(b"small", content_length="100")
    actual = _Response(b"0123456789")

    with pytest.raises(credentialed_http.CredentialedHTTPContractError):
        credentialed_http.read_limited_bytes(declared, max_bytes=8)
    with pytest.raises(credentialed_http.CredentialedHTTPContractError):
        credentialed_http.read_limited_bytes(actual, max_bytes=8)

    assert declared.read_sizes == []
    assert actual.read_sizes == [9]


def test_응답의_최종_URL도_고정_endpoint와_정확히_같아야_한다() -> None:
    response = type(
        "RedirectedResponse",
        (),
        {"geturl": lambda self: "https://attacker.example/collect"},
    )()

    with pytest.raises(credentialed_http.CredentialedHTTPContractError):
        credentialed_http.require_exact_response_url(
            response,
            expected_url="https://provider.example/userinfo",
        )
