"""컨테이너 내부의 고정 loopback readiness 계약만 확인한다."""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request


READY_PATH = "/readyz"
READY_PAYLOAD = {"status": "ready"}
MAX_RESPONSE_BYTES = 4096
REQUEST_TIMEOUT_SECONDS = 3.0


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """상대·절대 Location을 모두 따라가지 않는다."""

    def redirect_request(self, request, file_pointer, code, message, headers, url):
        return None


def _strict_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("중복 JSON 키")
        result[key] = value
    return result


def check(*, port: str, path: str = READY_PATH) -> bool:
    """환경 프록시·redirect 없이 exact readiness 응답만 통과시킨다."""

    try:
        normalized_port = int(port)
    except (TypeError, ValueError):
        return False
    if not 1 <= normalized_port <= 65535 or path != READY_PATH:
        return False

    request = urllib.request.Request(
        f"http://127.0.0.1:{normalized_port}{READY_PATH}",
        headers={"User-Agent": "container-healthcheck/2"},
    )
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}),
        _NoRedirectHandler(),
    )
    try:
        with opener.open(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            if response.status != 200:
                return False
            content_type = response.headers.get("Content-Type", "")
            if content_type.split(";", 1)[0].strip().lower() != "application/json":
                return False
            body = response.read(MAX_RESPONSE_BYTES + 1)
    except (OSError, ValueError, urllib.error.HTTPError, urllib.error.URLError):
        return False

    if not body or len(body) > MAX_RESPONSE_BYTES:
        return False
    try:
        payload = json.loads(body.decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeError, ValueError, json.JSONDecodeError):
        return False
    return payload == READY_PAYLOAD


def main() -> int:
    port = os.environ.get("PORT", "10000")
    path = os.environ.get("HEALTHCHECK_PATH", READY_PATH)
    if path != READY_PATH:
        print("상태 확인은 /readyz만 허용합니다.", file=sys.stderr)
        return 2
    return 0 if check(port=port, path=path) else 1


if __name__ == "__main__":
    raise SystemExit(main())
