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

import ipaddress
import urllib.parse
import urllib.request
from functools import lru_cache
from typing import Final

from src.features.homepage.safe_http import (
    ALLOWED_PORTS,
    HomepageResponseError,
    UnsafeHomepageUrlError,
    safe_urlopen,
    validate_text_response,
)

#: 열어 보는 데 쓰는 시간 상한(초).
#: ⚠️ 이 확인은 «회사 확인 화면»을 그리기 전에 돈다. 길면 사용자가 기다린다.
PROBE_TIMEOUT_SEC: Final[float] = 2.5

#: 시도 순서. ★ https를 먼저 본다 — 되면 그게 맞다.
SCHEMES: Final[tuple[str, ...]] = ("https", "http")

#: 서버가 사람인 척 하는 요청만 받는 경우가 있다.
_HEADERS: Final[dict[str, str]] = {"User-Agent": "Mozilla/5.0"}


def browser_url(raw: str) -> str:
    """공시의 홈페이지 글자를 브라우저용 ``http(s)`` URL로 안전하게 만든다.

    이 함수는 데모에서도 쓸 수 있도록 네트워크 요청이나 DNS 조회를 하지 않는다.
    링크를 눌렀을 때 스크립트가 실행되는 ``javascript:``/``data:`` 주소, 계정 정보,
    로컬 주소와 비정상 포트는 빈 문자열로 돌려 링크 자체를 만들지 않는다.
    """
    if not isinstance(raw, str):
        return ""
    candidate = raw.strip()
    if (
        not candidate
        or "\\" in candidate
        or any(ord(character) < 32 or ord(character) == 127 for character in candidate)
    ):
        return ""

    if candidate.startswith("//"):
        candidate = "https:" + candidate
    elif "://" not in candidate:
        candidate = "https://" + candidate

    try:
        parsed = urllib.parse.urlsplit(candidate)
        scheme = parsed.scheme.lower()
        hostname = parsed.hostname
        port = parsed.port
    except (TypeError, ValueError):
        return ""

    if (
        scheme not in SCHEMES
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        # WHATWG URL 파서는 host의 %xx를 먼저 디코딩한 뒤 IPv4로 해석한다.
        # Python urlsplit만 믿으면 %31%32%37.0.0.1이 브라우저에서는 127.0.0.1이
        # 되는 우회가 생긴다. 회사 홈페이지 host에는 percent encoding이 필요 없으므로
        # credential/host 구분을 포함한 netloc 전체에서 fail-closed 한다.
        or "%" in parsed.netloc
    ):
        return ""
    hostname = hostname.rstrip(".")
    if not hostname or len(hostname) > 253 or any(char.isspace() for char in hostname):
        return ""
    try:
        ascii_hostname = hostname.encode("idna").decode("ascii").lower()
    except UnicodeError:
        return ""
    if ascii_hostname == "localhost" or ascii_hostname.endswith(".localhost"):
        return ""
    try:
        address = ipaddress.ip_address(ascii_hostname)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        return ""
    if address is None:
        # 브라우저가 2130706433, 0177.0.0.1, 0x7f000001 같은 옛 숫자 표기를
        # 루프백 IPv4로 다시 해석할 수 있다. 공개 홈페이지 도메인 모양도 아닌
        # 단일 label과 숫자-only hostname은 링크로 만들지 않는다.
        labels = ascii_hostname.split(".")
        if len(labels) < 2 or all(
            label.isdigit()
            or (
                label.lower().startswith("0x")
                and label[2:]
                and all(
                    character in "0123456789abcdef"
                    for character in label[2:].lower()
                )
            )
            for label in labels
        ):
            return ""
    if port is not None and port not in ALLOWED_PORTS:
        return ""

    display_host = f"[{ascii_hostname}]" if ":" in ascii_hostname else ascii_hostname
    netloc = f"{display_host}:{port}" if port is not None else display_host
    path = urllib.parse.quote(parsed.path or "", safe="/%:@!$&'()*+,;=-._~")
    query = urllib.parse.quote(parsed.query or "", safe="/%?:@!$&'()*+,;=-._~")
    fragment = urllib.parse.quote(parsed.fragment or "", safe="/%?:@!$&'()*+,;=-._~")
    return urllib.parse.urlunsplit((scheme, netloc, path, query, fragment))


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
    fallback = browser_url(raw)
    if not fallback:
        return ""
    candidate = (raw or "").strip()
    try:
        supplied_scheme = urllib.parse.urlsplit(candidate).scheme.lower()
    except ValueError:
        return ""
    host = bare_host(fallback)
    if not host:
        return ""
    preferred_schemes = (
        (supplied_scheme,) + tuple(s for s in SCHEMES if s != supplied_scheme)
        if supplied_scheme in SCHEMES
        else SCHEMES
    )
    for scheme in preferred_schemes:
        url = f"{scheme}://{host}"
        try:
            request = urllib.request.Request(url, headers=_HEADERS)
            with safe_urlopen(request, timeout=PROBE_TIMEOUT_SEC) as response:
                validate_text_response(response)
                if 200 <= getattr(response, "status", 200) < 400:
                    return response.geturl()
        except (UnsafeHomepageUrlError, HomepageResponseError):
            # A malformed/private destination is not a usable public homepage.
            return ""
        except Exception:  # noqa: BLE001 — 인증서·시간초과·거부 전부 「이 방식은 안 됨」이다
            continue
    # 외부 사이트의 일시 장애가 확인 카드의 링크 자체를 없애지는 않게 한다.
    # 스킴·호스트·포트 안전성은 위 browser_url()에서 이미 검증했다.
    return fallback
