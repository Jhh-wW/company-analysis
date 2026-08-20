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
import ipaddress
import secrets
from urllib.parse import urlsplit

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
        16진수 열쇠 (기본 32자리 = 128비트).

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
    """일반 URL에서 origin을 분리하는 하위 호환 유틸리티.

    Args:
        request_url: 요청 주소 전체 (`http://localhost:8000/admin/access` 등).

    Returns:
        `http://localhost:8000` 부분.

    ⚠️ 요청 URL은 ``Host`` 헤더의 영향을 받으므로 capability QR·외부 복사용 주소에
      이 함수 결과를 직접 사용하면 안 된다. 그 용도는 ``canonical_public_base_url``
      또는 엄격한 로컬 전용 ``safe_local_base_url``을 쓴다.
    """
    parts = urlsplit(request_url)
    if not parts.scheme or not parts.netloc:
        return ""
    return f"{parts.scheme}://{parts.netloc}"


#: 인터넷에 공개된 주소로 «안 보이는» 호스트들.
#: ★ 여기 있는 주소로 링크를 발급하면 **인사팀에게는 안 열린다.**
_LOCAL_HOSTS: tuple[str, ...] = ("localhost", "127.0.0.1", "0.0.0.0", "::1", "testserver")


def _is_local_host(host: str) -> bool:
    normalized = (host or "").lower().rstrip(".")
    if normalized in _LOCAL_HOSTS or normalized.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(normalized)
    except ValueError:
        return False
    return address.is_loopback or address.is_unspecified


def _is_public_host(host: str) -> bool:
    normalized = (host or "").lower().rstrip(".")
    if not normalized or _is_local_host(normalized):
        return False
    try:
        # 사설·link-local·예약 IP는 외부 포트폴리오 주소가 아니다.
        return ipaddress.ip_address(normalized).is_global
    except ValueError:
        return True


def canonical_public_base_url(value: str) -> str:
    """설정에서 받은 공개 HTTPS origin만 정규화한다.

    요청의 ``Host``나 ``X-Forwarded-*``는 받지 않는다. 사용자 정보·경로·쿼리·
    fragment가 붙었거나 로컬 호스트인 값도 거절해 QR에 공격자 origin이 섞이지 않게
    한다. 잘못된 설정은 빈 문자열로 fail-closed 한다.
    """
    raw = (value or "").strip()
    try:
        parts = urlsplit(raw)
        # 잘못된 포트(``:abc``)도 여기서 예외를 내므로 미리 검증한다.
        _ = parts.port
    except (TypeError, ValueError):
        return ""
    if (
        parts.scheme.lower() != "https"
        or not parts.netloc
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.path not in {"", "/"}
        or parts.query
        or parts.fragment
        or not _is_public_host(parts.hostname)
    ):
        return ""
    return f"https://{parts.netloc.rstrip('/')}"


def safe_local_base_url(request_url: str) -> str:
    """로컬 데모에서만 loopback 요청 origin을 절대 주소로 쓴다.

    배포 origin은 반드시 설정값을 써야 한다. 따라서 공격자가 ``Host: evil.example``
    를 넣어도 이 함수는 빈 값을 돌려주며, 외부 복사용 URL과 QR은 만들어지지 않는다.
    ``testserver``는 격리 TestClient 전용 호스트라 로컬 범주에 포함한다.
    """
    try:
        parts = urlsplit((request_url or "").strip())
        _ = parts.port
    except (TypeError, ValueError):
        return ""
    host = (parts.hostname or "").lower().rstrip(".")
    if (
        parts.scheme.lower() != "http"
        or not _is_local_host(host)
        or host == "testserver"
        or parts.username is not None
        or parts.password is not None
    ):
        return ""
    return f"http://{parts.netloc}"


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
    return _is_public_host(host)
