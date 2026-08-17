"""회사 홈페이지 주소를 «실제로 열리는 모양»으로 만든다 (문제로그 P-114).

★ 왜 필요한가 — 2026-08-16 사용자가 진영 홈페이지 링크를 눌렀더니 브라우저가
  통째로 막았다: **「연결이 비공개로 설정되어 있지 않습니다 · NET::ERR_CERT_AUTHORITY_INVALID」**.
  사용자 반응은 「이 회사 맞냐」였다 — **우리가 잘못된 회사를 찾아 준 것처럼 보였다.**

★ 실측으로 확인한 진짜 원인 (2026-08-16) —
    https://www.jyp21.co.kr  → CERTIFICATE_VERIFY_FAILED (브라우저가 막는다)
    http://www.jyp21.co.kr   → **200 OK · 제목 「JINYOUNG」**  ← 진영 맞다
  회사 사이트의 https 인증서가 부실할 뿐, 주소도 회사도 맞다.
  **그런데 우리가 `https://`를 앞에 붙여 링크를 걸고 있었다** (`confirm.html`).

★ 그래서 하는 일은 하나 — **먼저 열어 보고, 열리는 방식으로 건다.**
  ⚠️ 안 열리면 **링크를 안 건다**(빈 문자열). 눌러서 경고창을 보는 것보다
    글자로만 보이는 편이 낫다.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from functools import lru_cache
from typing import Final

#: 열어 보는 데 쓰는 시간 상한(초).
#: ⚠️ 이 확인은 «회사 확인 화면»을 그리기 전에 돈다. 길면 사용자가 기다린다.
PROBE_TIMEOUT_SEC: Final[float] = 2.5

#: 시도 순서. ★ https를 먼저 본다 — 되면 그게 맞다.
SCHEMES: Final[tuple[str, ...]] = ("https", "http")

#: 서버가 사람인 척 하는 요청만 받는 경우가 있다.
_HEADERS: Final[dict[str, str]] = {"User-Agent": "Mozilla/5.0"}


def bare_host(raw: str) -> str:
    """주소에서 «앞머리와 꼬리»를 떼어 맨몸 주소만 남긴다."""
    url = (raw or "").strip()
    for 앞머리 in ("https://", "http://"):
        if url.lower().startswith(앞머리):
            url = url[len(앞머리):]
    return url.strip().rstrip("/")


@lru_cache(maxsize=256)
def workable_url(raw: str) -> str:
    """실제로 열리는 주소를 돌려준다. 못 열면 빈 문자열.

    Args:
        raw: 전자공시 기업개황이 준 `hm_url` (앞머리가 있을 수도, 없을 수도 있다).

    Returns:
        `https://…` 또는 `http://…`. 둘 다 안 되면 `""`.

    ★ 결과를 기억해 둔다 — 같은 회사 화면을 다시 그릴 때마다 접속하면 느리다.
    ⚠️ 여기서 나는 예외를 밖으로 흘리지 않는다. 홈페이지 주소 하나 때문에
      «회사 확인 화면»이 통째로 안 뜨면 안 된다.
    """
    host = bare_host(raw)
    if not host:
        return ""
    for scheme in SCHEMES:
        url = f"{scheme}://{host}"
        try:
            request = urllib.request.Request(url, headers=_HEADERS)
            with urllib.request.urlopen(request, timeout=PROBE_TIMEOUT_SEC) as response:
                if 200 <= getattr(response, "status", 200) < 400:
                    return url
        except Exception:  # noqa: BLE001 — 인증서·시간초과·거부 전부 「이 방식은 안 됨」이다
            continue
    return ""
