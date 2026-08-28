"""인증정보가 있는 engine 외부 API의 공통 redirect 계약."""

from __future__ import annotations

import io
import sys
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core import credentialed_http


def test_redirect_handler는_인증정보를_새_host로_복사하지_않는다() -> None:
    request = urllib.request.Request(
        "https://provider.example/data?api_key=secret",
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


def test_최종응답_URL은_요청한_고정_endpoint와_정확히_같아야_한다() -> None:
    response = type(
        "RedirectedResponse",
        (),
        {"geturl": lambda self: "https://attacker.example/collect"},
    )()

    with pytest.raises(credentialed_http.CredentialedHTTPContractError):
        credentialed_http.require_exact_response_url(
            response,
            expected_url="https://provider.example/data",
        )
