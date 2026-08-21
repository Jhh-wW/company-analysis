"""Render cron에서 웹 서비스의 인증된 외부 백업을 한 번 요청한다."""

from __future__ import annotations

import json
import os
import sys
from typing import Final, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


ENV_TRIGGER_URL: Final[str] = "BACKUP_TRIGGER_URL"
ENV_TRIGGER_SECRET: Final[str] = "BACKUP_TRIGGER_SECRET"
ENDPOINT_PATH: Final[str] = "/internal/backup/run"
MIN_SECRET_BYTES: Final[int] = 32
TIMEOUT_SEC: Final[int] = 300
MAX_RESPONSE_BYTES: Final[int] = 64 * 1024


class TriggerError(RuntimeError):
    """백업 요청이 성공·검증 응답까지 끝나지 않았다."""


class _FailClosedRedirectHandler(HTTPRedirectHandler):
    """Bearer 자격 증명이 후속 URL로 전달되지 않도록 리디렉션을 거부한다."""

    def redirect_request(
        self,
        request,
        fp,
        code,
        msg,
        headers,
        newurl,
    ):
        return None


def _config_from_env() -> tuple[str, str]:
    url = os.environ.get(ENV_TRIGGER_URL, "").strip()
    secret = os.environ.get(ENV_TRIGGER_SECRET, "").strip()
    try:
        parsed = urlsplit(url)
    except ValueError as exc:
        raise TriggerError("BACKUP_TRIGGER_URL 형식이 올바르지 않습니다") from exc
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path != ENDPOINT_PATH
    ):
        raise TriggerError(
            "BACKUP_TRIGGER_URL은 /internal/backup/run으로 끝나는 HTTPS 주소여야 합니다"
        )
    if len(secret.encode("utf-8")) < MIN_SECRET_BYTES:
        raise TriggerError(
            f"BACKUP_TRIGGER_SECRET은 {MIN_SECRET_BYTES}바이트 이상이어야 합니다"
        )
    return url, secret


def trigger_once(url: str, secret: str) -> dict:
    request = Request(
        url,
        data=b"",
        method="POST",
        headers={
            "Authorization": f"Bearer {secret}",
            "Accept": "application/json",
            "User-Agent": "company-analysis-backup-cron/1",
        },
    )
    try:
        opener = build_opener(_FailClosedRedirectHandler())
        with opener.open(request, timeout=TIMEOUT_SEC) as response:  # noqa: S310
            status = int(getattr(response, "status", 0))
            payload_bytes = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as exc:
        raise TriggerError(f"백업 서버가 HTTP {exc.code}을 반환했습니다") from exc
    except (OSError, URLError) as exc:
        raise TriggerError("백업 서버에 안전하게 연결하지 못했습니다") from exc
    if status != 200:
        raise TriggerError(f"백업 서버가 HTTP {status}을 반환했습니다")
    if len(payload_bytes) > MAX_RESPONSE_BYTES:
        raise TriggerError("백업 서버 응답이 허용 크기를 넘었습니다")
    try:
        payload = json.loads(payload_bytes.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise TriggerError("백업 서버 응답을 확인할 수 없습니다") from exc
    if not isinstance(payload, dict) or payload.get("status") != "ok":
        raise TriggerError("백업 서버가 완료 상태를 반환하지 않았습니다")
    digest = str(payload.get("sha256", ""))
    if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
        raise TriggerError("백업 서버의 SHA-256 응답이 올바르지 않습니다")
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if argv:
        print("인자는 받지 않습니다.", file=sys.stderr)
        return 2
    try:
        url, secret = _config_from_env()
        payload = trigger_once(url, secret)
    except TriggerError as exc:
        print(f"외부 백업 실패: {exc}", file=sys.stderr)
        return 1
    print(
        "외부 백업 완료: "
        f"object={payload.get('object_key', '')} "
        f"sha256={payload['sha256']} "
        f"deleted={int(payload.get('deleted_objects', 0))}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
