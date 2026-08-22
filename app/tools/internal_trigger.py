"""내부 운영 경로를 호출하는 도구의 공통 HTTP 보안 경계."""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from typing import Final
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


MIN_SECRET_BYTES: Final[int] = 32
TIMEOUT_SEC: Final[int] = 300
MAX_RESPONSE_BYTES: Final[int] = 64 * 1024

OpenerFactory = Callable[..., object]


class TriggerError(RuntimeError):
    """내부 운영 요청이 안전한 완료 응답까지 끝나지 않았다."""


class FailClosedRedirectHandler(HTTPRedirectHandler):
    """Bearer 자격 증명이 후속 URL로 전달되지 않도록 리디렉션을 거부한다."""

    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def load_exact_https_config(
    *,
    url_env: str,
    secret_env: str,
    endpoint_path: str,
) -> tuple[str, str]:
    """환경변수에서 exact HTTPS URL과 충분히 긴 비밀을 읽는다."""
    url = os.environ.get(url_env, "").strip()
    secret = os.environ.get(secret_env, "").strip()
    try:
        parsed = urlsplit(url)
    except ValueError as exc:
        raise TriggerError(f"{url_env} 형식이 올바르지 않습니다") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path != endpoint_path
    ):
        raise TriggerError(
            f"{url_env}은 {endpoint_path}로 끝나는 HTTPS 주소여야 합니다"
        )
    if len(secret.encode("utf-8")) < MIN_SECRET_BYTES:
        raise TriggerError(
            f"{secret_env}은 {MIN_SECRET_BYTES}바이트 이상이어야 합니다"
        )
    return url, secret


def post_json(
    *,
    url: str,
    secret: str,
    service_name: str,
    user_agent: str,
    headers: Mapping[str, str] | None = None,
    opener_factory: OpenerFactory = build_opener,
) -> dict:
    """리디렉션을 거부하고 크기가 제한된 JSON 객체만 돌려준다."""
    request_headers = {
        "Authorization": f"Bearer {secret}",
        "Accept": "application/json",
        "User-Agent": user_agent,
    }
    if headers:
        request_headers.update(headers)
    request = Request(url, data=b"", method="POST", headers=request_headers)
    try:
        opener = opener_factory(FailClosedRedirectHandler())
        with opener.open(request, timeout=TIMEOUT_SEC) as response:  # noqa: S310
            status = int(getattr(response, "status", 0))
            payload_bytes = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        raise TriggerError(
            f"{service_name} 서버가 HTTP {exc.code}을 반환했습니다"
        ) from exc
    except (OSError, URLError) as exc:
        raise TriggerError(f"{service_name} 서버에 안전하게 연결하지 못했습니다") from exc
    if status != 200:
        raise TriggerError(f"{service_name} 서버가 HTTP {status}을 반환했습니다")
    if len(payload_bytes) > MAX_RESPONSE_BYTES:
        raise TriggerError(f"{service_name} 서버 응답이 허용 크기를 넘었습니다")
    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise TriggerError(f"{service_name} 서버 응답을 확인할 수 없습니다") from exc
    if not isinstance(payload, dict):
        raise TriggerError(f"{service_name} 서버가 JSON 객체를 반환하지 않았습니다")
    return payload
