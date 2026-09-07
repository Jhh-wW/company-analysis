"""같은 host의 http 리다이렉트를 https로 승격하는 규칙의 회귀시험.

실제 회사 홈페이지에서 관측한 «https → http(같은 host) → https» 연쇄를 그대로
재현한다. 네트워크는 열지 않는다 — DNS 응답과 프로토콜 처리기만 가짜로 바꾼다.
가짜 http 처리기는 호출되는 순간 실패로 단정하므로, 평문 요청이 한 번이라도
나가면 시험이 깨진다.
"""

from __future__ import annotations

import socket
import urllib.parse
import urllib.request
from email.message import Message

import pytest

from src.features.homepage import safe_http
from src.features.homepage.safe_http import UnsafeHomepageUrlError

_PUBLIC_IP = "93.184.216.34"
_HOST = "ganada-example.co.kr"
_APEX_URL = f"https://{_HOST}/"
_HTTP_HOP_URL = f"http://{_HOST}/en/main"
_FINAL_URL = f"https://{_HOST}/en/main"
_BODY_TEXT = "<html><body>가나다전자 공식 회사 소개</body></html>"
_REQUEST_TIMEOUT_SEC = 30.0


def _dns_answer(ip: str) -> list[tuple]:
    return [
        (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, 443))
    ]


class _FakeResponse:
    """urllib 처리기 사슬이 요구하는 최소 응답 객체."""

    def __init__(
        self,
        url: str,
        *,
        code: int,
        headers: dict[str, str],
        body: bytes = b"",
    ) -> None:
        self.code = code
        self.status = code
        self.msg = "Found" if code // 100 == 3 else "OK"
        self.url = url
        message = Message()
        for name, value in headers.items():
            message[name] = value
        self.headers = message
        self._body = body
        self._cursor = 0
        self.close_count = 0

    def info(self) -> Message:
        return self.headers

    def geturl(self) -> str:
        return self.url

    def getcode(self) -> int:
        return self.code

    def read1(self, size: int = -1) -> bytes:
        if size is None or size < 0:
            size = len(self._body) - self._cursor
        chunk = self._body[self._cursor : self._cursor + size]
        self._cursor += len(chunk)
        return chunk

    def read(self, size: int = -1) -> bytes:
        return self.read1(size)

    def close(self) -> None:
        self.close_count += 1


def _redirect_response(url: str, location: str) -> _FakeResponse:
    return _FakeResponse(url, code=302, headers={"Location": location})


def _html_response(url: str, text: str) -> _FakeResponse:
    body = text.encode("utf-8")
    return _FakeResponse(
        url,
        code=200,
        headers={
            "Content-Type": "text/html; charset=utf-8",
            "Content-Length": str(len(body)),
        },
        body=body,
    )


def _install_fake_transport(
    monkeypatch: pytest.MonkeyPatch,
    pages: dict[str, _FakeResponse],
) -> list[str]:
    """https만 응답하는 가짜 전송 계층을 깔고, 요청 URL 기록을 돌려준다."""

    requested: list[str] = []

    def https_open(_handler, request):
        requested.append(request.full_url)
        response = pages.get(request.full_url)
        if response is None:
            raise AssertionError(f"준비되지 않은 https 요청: {request.full_url}")
        return response

    def http_open(_handler, request):
        requested.append(request.full_url)
        raise AssertionError(f"평문 http 요청이 나갔습니다: {request.full_url}")

    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _dns_answer(_PUBLIC_IP))
    monkeypatch.setattr(safe_http._SafeHTTPSHandler, "https_open", https_open)
    monkeypatch.setattr(safe_http._SafeHTTPHandler, "http_open", http_open)
    return requested


def _same_origin_https_only(url: str) -> bool:
    """광역 수집 origin 경계와 같은 규칙 — 같은 host의 https만 허용한다."""

    parsed = urllib.parse.urlsplit(url)
    return parsed.scheme == "https" and (parsed.hostname or "") == _HOST


def test_같은_host_http_리다이렉트는_https로_승격돼_최종_문서를_읽는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """실측 연쇄(apex → http 언어경로 → https)에서 본문까지 도달해야 한다."""

    requested = _install_fake_transport(
        monkeypatch,
        {
            _APEX_URL: _redirect_response(_APEX_URL, _HTTP_HOP_URL),
            _FINAL_URL: _html_response(_FINAL_URL, _BODY_TEXT),
        },
    )

    response = safe_http.safe_urlopen(
        urllib.request.Request(_APEX_URL),
        timeout=_REQUEST_TIMEOUT_SEC,
        url_allowed=_same_origin_https_only,
    )

    assert response.geturl() == _FINAL_URL
    assert safe_http.read_limited_text(
        response, timeout=_REQUEST_TIMEOUT_SEC
    ) == _BODY_TEXT
    # http로는 한 번도 나가지 않았고, 승격된 https가 그 자리를 대신했다.
    assert requested == [_APEX_URL, _FINAL_URL]
    assert not any(url.startswith("http://") for url in requested)


def test_승격된_hop도_호출자_경로_정책을_다시_통과해야_한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """승격은 scheme만 올린다 — 경로 허용 규칙을 건너뛰지 않는다."""

    _install_fake_transport(
        monkeypatch,
        {_APEX_URL: _redirect_response(_APEX_URL, _HTTP_HOP_URL)},
    )

    checked: list[str] = []

    def only_root(url: str) -> bool:
        checked.append(url)
        return url == _APEX_URL

    with pytest.raises(UnsafeHomepageUrlError):
        safe_http.safe_urlopen(
            urllib.request.Request(_APEX_URL),
            timeout=_REQUEST_TIMEOUT_SEC,
            url_allowed=only_root,
        )

    # 「막혔다」만으로는 부족하다 — 승격된 https 주소를 대상으로 막혔는지까지 본다.
    # 규칙이 꺼져 있으면 여기 마지막 값이 http 주소라 이 단정이 깨진다.
    assert checked[-1] == _FINAL_URL


def test_최초_url도_호출자_경로_정책을_dns보다_먼저_통과해야_한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """정책 밖 최초 URL이 redirect 없이 한 번 연결되는 우회를 막는다."""

    dns_called = False

    def forbidden_dns(*_args, **_kwargs):
        nonlocal dns_called
        dns_called = True
        raise AssertionError("차단된 최초 URL에 DNS 조회가 나갔습니다")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden_dns)

    with pytest.raises(UnsafeHomepageUrlError):
        safe_http.safe_urlopen(
            urllib.request.Request(_APEX_URL),
            timeout=_REQUEST_TIMEOUT_SEC,
            url_allowed=lambda _url: False,
        )

    assert dns_called is False


def test_다른_host로의_http_리다이렉트는_승격하지_않고_거부된다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _dns_answer(_PUBLIC_IP))
    handler = safe_http._SafeRedirectHandler(url_allowed=_same_origin_https_only)

    with pytest.raises(UnsafeHomepageUrlError):
        handler.redirect_request(
            urllib.request.Request(_APEX_URL),
            None,
            302,
            "Found",
            {},
            "http://other-ganada-example.co.kr/en/main",
        )


@pytest.mark.parametrize(
    "redirect_url",
    [
        "http://other-ganada-example.co.kr/en/main",
        f"http://{_HOST}:80/en/main",
    ],
)
def test_https에서_승격할_수_없는_http로는_dns전에_내려가지_않는다(
    monkeypatch: pytest.MonkeyPatch,
    redirect_url: str,
) -> None:
    dns_called = False

    def forbidden_dns(*_args, **_kwargs):
        nonlocal dns_called
        dns_called = True
        raise AssertionError("평문 redirect의 DNS 조회가 나가면 안 됩니다")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden_dns)
    handler = safe_http._SafeRedirectHandler()

    with pytest.raises(UnsafeHomepageUrlError, match="평문 HTTP"):
        handler.redirect_request(
            urllib.request.Request(_APEX_URL),
            None,
            302,
            "Found",
            {},
            redirect_url,
        )

    assert dns_called is False


def test_정확한_host_처리기도_같은_host_http를_https로_승격한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """공식 IR 경로의 exact-host 처리기도 같은 규칙을 쓴다."""

    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _dns_answer(_PUBLIC_IP))
    handler = safe_http._ExactHttpsHostRedirectHandler(_HOST)

    redirected = handler.redirect_request(
        urllib.request.Request(_APEX_URL),
        None,
        302,
        "Found",
        {},
        _HTTP_HOP_URL,
    )

    assert redirected is not None
    assert redirected.full_url == _FINAL_URL


@pytest.mark.parametrize(
    "redirect_url",
    [
        f"https://{_HOST}:8443/en/main",
        f"https://{_HOST}:8080/en/main",
        f"https://user:secret@{_HOST}/en/main",
        f"ftp://{_HOST}/en/main",
        f"file://{_HOST}/en/main",
        f"//{_HOST}/en/main",
    ],
)
def test_정확한_host라도_기본_https_origin을_벗어나면_dns전에_거부한다(
    monkeypatch: pytest.MonkeyPatch,
    redirect_url: str,
) -> None:
    dns_called = False

    def forbidden_dns(*_args, **_kwargs):
        nonlocal dns_called
        dns_called = True
        raise AssertionError("차단된 IR origin에 DNS 조회가 나갔습니다")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden_dns)

    # redirect뿐 아니라 최초 IR URL도 같은 exact-origin 경계를 통과해야 한다.
    # scheme-relative 주소는 urllib.Request 자체가 생성 전에 거부하므로 redirect
    # 처리기 계약만 확인한다.
    if not redirect_url.startswith("//"):
        with pytest.raises(UnsafeHomepageUrlError):
            safe_http.safe_urlopen_exact_https_host(
                urllib.request.Request(redirect_url),
                timeout=_REQUEST_TIMEOUT_SEC,
                expected_hostname=_HOST,
            )

    handler = safe_http._ExactHttpsHostRedirectHandler(_HOST)

    with pytest.raises(UnsafeHomepageUrlError):
        handler.redirect_request(
            urllib.request.Request(_APEX_URL),
            None,
            302,
            "Found",
            {},
            redirect_url,
        )

    assert dns_called is False


@pytest.mark.parametrize(
    ("current_url", "new_url"),
    [
        # 다른 host
        (_APEX_URL, "http://other-ganada-example.co.kr/en/main"),
        # http 목적지에 포트를 명시한 경우 — https로 바꾸면 다른 포트가 된다
        (_APEX_URL, f"http://{_HOST}:8080/en/main"),
        (_APEX_URL, f"http://{_HOST}:80/en/main"),
        # 원본 https가 기본 포트가 아닌 경우
        (f"https://{_HOST}:8443/", _HTTP_HOP_URL),
        # 계정 정보가 붙은 목적지 — 조용히 떼어내지 않는다
        (_APEX_URL, f"http://user:secret@{_HOST}/en/main"),
        # 애초에 http가 아닌 목적지
        (_APEX_URL, _FINAL_URL),
        # 원본이 https가 아닌 경우
        (f"http://{_HOST}/", _HTTP_HOP_URL),
    ],
)
def test_승격_규칙은_같은_host_기본포트_평문만_대상으로_한다(
    current_url: str, new_url: str
) -> None:
    assert safe_http._https_upgraded_same_host_url(current_url, new_url) == ""


def test_승격은_같은_경로와_질의를_그대로_옮긴다() -> None:
    upgraded = safe_http._https_upgraded_same_host_url(
        _APEX_URL, f"http://{_HOST}/en/main?lang=ko#top"
    )

    assert upgraded == f"https://{_HOST}/en/main?lang=ko#top"
