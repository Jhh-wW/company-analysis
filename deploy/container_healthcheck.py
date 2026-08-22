"""컨테이너 내부에서 readiness HTTP 경로만 확인한다."""

from __future__ import annotations

import os
import sys
import urllib.error
import urllib.request


def main() -> int:
    port = os.environ.get("PORT", "10000")
    path = os.environ.get("HEALTHCHECK_PATH", "/readyz")
    if not path.startswith("/") or "\r" in path or "\n" in path:
        print("상태 확인 경로가 올바르지 않습니다.", file=sys.stderr)
        return 2

    request = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}",
        headers={"User-Agent": "container-healthcheck/1"},
    )
    try:
        with urllib.request.urlopen(request, timeout=3.0) as response:
            return 0 if response.status == 200 else 1
    except (OSError, ValueError, urllib.error.URLError):
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
