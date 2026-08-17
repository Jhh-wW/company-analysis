"""열쇠 링크를 «발급»한다 — 열쇠 만들기 · 주소 만들기 · QR 그리기 (문제로그 P-96).

★ 왜 따로 두나 — 「발급」은 저장(`store.py`)·판단(`logic.py`)과 다른 일이다.
  섞어 두면 시험에서 「무엇을 보는지」가 흐려진다.

★ QR은 **SVG 글자**로 만든다 (그림 파일이 아니라).
  · 화면에 바로 넣을 수 있고, 파일을 저장·관리할 필요가 없다
  · 크기를 키워도 안 깨진다 — **포트폴리오는 인쇄되기도 한다**
  · 의존성이 `segno` 하나뿐이고 그것도 순수 파이썬이라 배포가 가볍다
"""

from __future__ import annotations

import io
import secrets

import segno

from src.features.sharelink.constants import KEY_HEX_CHARS, KEY_PATH_PREFIX

#: QR 한 칸의 픽셀 크기. 화면·인쇄 둘 다 무난한 값.
_QR_SCALE = 5
#: QR 둘레 여백(칸 수). 표준은 4지만 화면에서는 2로도 잘 읽힌다.
_QR_BORDER = 2
#: 오류 정정 수준. `m` = 15%까지 가려져도 읽힌다.
#: ★ 인쇄본이 접히거나 더러워질 수 있어 «가장 낮은 수준(l)»은 쓰지 않는다.
_QR_ERROR = "m"


def new_key() -> str:
    """추측할 수 없는 새 열쇠를 만든다.

    Returns:
        16진수 열쇠 (기본 16자리 = 64비트).

    ★ `secrets`를 쓴다 — `random`은 예측 가능해서 열쇠에 쓰면 안 된다.
    """
    return secrets.token_hex(KEY_HEX_CHARS // 2)


def link_url(base_url: str, key: str) -> str:
    """인사팀에게 줄 «전체 주소»를 만든다.

    Args:
        base_url: 서비스 주소 (`https://example.com` 또는 `http://localhost:8000`).
        key: 열쇠.

    Returns:
        `https://example.com/k/<열쇠>`

    ★ 끝의 `/`를 떼고 붙인다 — 안 떼면 `//k/...`가 되어 주소가 깨진다.
    """
    return f"{base_url.rstrip('/')}{KEY_PATH_PREFIX}/{key}"


def qr_svg(url: str) -> str:
    """그 주소를 담은 QR을 SVG 글자로 만든다.

    Args:
        url: QR에 담을 주소.

    Returns:
        `<svg …>…</svg>` 글자. 화면에 그대로 넣으면 된다.

    ⚠️ **XML 선언(`<?xml …?>`)을 빼고 만든다** — HTML 한가운데에 그게 들어가면
      브라우저가 문서를 잘못 해석한다.
    """
    buffer = io.BytesIO()
    segno.make(url, error=_QR_ERROR).save(
        buffer,
        kind="svg",
        scale=_QR_SCALE,
        border=_QR_BORDER,
        xmldecl=False,          # ★ HTML 안에 넣을 것이라 XML 선언을 뺀다
        svgns=True,
        omitsize=False,
    )
    return buffer.getvalue().decode("utf-8")


def base_url_of(request_url: str) -> str:
    """지금 접속한 주소에서 «서비스 주소»만 뽑는다.

    Args:
        request_url: 요청 주소 전체 (`http://localhost:8000/admin/access` 등).

    Returns:
        `http://localhost:8000` 부분.

    ★ 왜 필요한가 — 링크를 발급할 때 「우리 서비스 주소가 뭔지」를 알아야 하는데,
      그걸 상수로 박아 두면 **배포한 뒤에 안 고쳐져서 링크가 localhost를 가리킨다.**
      지금 접속한 주소에서 뽑으면 배포하든 로컬이든 저절로 맞는다.
    """
    from urllib.parse import urlsplit  # noqa: PLC0415

    parts = urlsplit(request_url)
    if not parts.scheme or not parts.netloc:
        return ""
    return f"{parts.scheme}://{parts.netloc}"


#: 인터넷에 공개된 주소로 «안 보이는» 호스트들.
#: ★ 여기 있는 주소로 링크를 발급하면 **인사팀에게는 안 열린다.**
_LOCAL_HOSTS: tuple[str, ...] = ("localhost", "127.0.0.1", "0.0.0.0", "::1", "testserver")


def looks_deployed(url: str) -> bool:
    """이 주소가 «인터넷에 공개된» 주소로 보이는가.

    Args:
        url: 발급할 링크 주소.

    Returns:
        공개 주소로 보이면 True.

    ★ 왜 필요한가 — 내 컴퓨터 주소(`localhost`)로 링크를 발급해 포트폴리오에 넣으면
      **인사팀에게는 «안 열리는 링크»**가 된다. 그건 아무것도 없는 것보다 나쁘다.
    ★ 판정 기준 둘: ① `https`인가 ② 내 컴퓨터를 가리키는 이름이 아닌가.
      배포한 서비스는 사실상 전부 https다. 하나라도 어긋나면 경고한다.
    ⚠️ **확실한 판정이 아니다** — 사설망 주소(192.168.x.x)는 https일 수 있다.
      그래도 「경고를 안 띄우는 것」보다 「가끔 괜히 띄우는 것」이 낫다.
    """
    from urllib.parse import urlsplit  # noqa: PLC0415

    parts = urlsplit(url)
    if parts.scheme != "https":
        return False
    host = (parts.hostname or "").lower()
    return bool(host) and host not in _LOCAL_HOSTS
