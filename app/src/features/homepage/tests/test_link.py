"""확인 카드에 노출하는 회사 홈페이지 링크의 회귀시험."""

from src.features.homepage.link import browser_url
from src.features.pipeline.demo import DemoPipeline
from src.features.pipeline.port import UserInput


def test_스킴없는_하이브_홈페이지는_https_링크가_된다():
    assert browser_url("hybecorp.com") == "https://hybecorp.com"


def test_데모_하이브_확인카드도_클릭할_URL을_준다():
    card = DemoPipeline().find_company(
        UserInput("하이브", "매니지먼트", "서울 용산구", "")
    )
    assert card is not None
    assert card.homepage == "hybecorp.com"
    assert card.homepage_url == "https://hybecorp.com"


def test_http와_https는_경로까지_정규화한다():
    assert browser_url("HTTP://EXAMPLE.COM/about us?q=채용") == (
        "http://example.com/about%20us?q=%EC%B1%84%EC%9A%A9"
    )


def test_스크립트와_계정정보_로컬주소는_링크를_만들지_않는다():
    unsafe = (
        "javascript:alert(1)",
        "data:text/html,x",
        "https://user:password@example.com",
        "http://127.0.0.1",
        "http://localhost",
        "http://2130706433",
        "http://0177.0.0.1",
        "http://0x7f000001",
        "http://%31%32%37.0.0.1",
        "http://127%2e0%2e0%2e1",
        "http://%31%39%32.168.0.1",
        "http://%6c%6f%63%61%6c%68%6f%73%74",
        "http://user%40name@example.com",
        "http://example.com%2f@127.0.0.1",
        "https://example.com:22",
        "https://example.com\\@evil.test",
        "https://example.com\n.evil.test",
    )
    assert all(browser_url(url) == "" for url in unsafe)


# ── 어느 스킴으로 여느냐 (문제로그 P-114 · 진영 자체서명 인증서) ──
#
# 실제 접속은 하지 않는다. `link.safe_urlopen`을 가짜로 바꿔 끼워
# 「https는 죽고 http는 산다」 같은 상황을 만들어 낸다.

import ssl
from contextlib import contextmanager

import pytest

from src.features.homepage import link as homepage_link


class _가짜응답:
    def __init__(self, url: str, status: int = 200) -> None:
        self._url = url
        self.status = status
        self.headers = {"Content-Type": "text/html; charset=utf-8"}

    def geturl(self) -> str:
        return self._url


def _스킴별_결과(monkeypatch: pytest.MonkeyPatch, 살아있는_스킴: set[str]) -> list[str]:
    """주어진 스킴만 200을 주는 가짜 접속기를 끼우고, 시도 순서를 돌려준다."""
    시도: list[str] = []

    @contextmanager
    def 가짜_urlopen(request, timeout=None, **_kwargs):
        url = request.full_url
        시도.append(url)
        if url.split("://", 1)[0] not in 살아있는_스킴:
            raise ssl.SSLCertVerificationError(
                "[SSL: CERTIFICATE_VERIFY_FAILED] self-signed certificate"
            )
        yield _가짜응답(url)

    monkeypatch.setattr(homepage_link, "safe_urlopen", 가짜_urlopen)
    homepage_link.workable_url.cache_clear()
    return 시도


def test_스킴없는_주소는_https가_죽으면_http로_연다(monkeypatch: pytest.MonkeyPatch):
    """(주)진영 재현 — 자체서명 인증서라 https가 통째로 막힌다.

    ★ 이게 깨지면 홈페이지 조각이 0개가 되고 8장 인재상 재료가 사라진다.
    """
    시도 = _스킴별_결과(monkeypatch, 살아있는_스킴={"http"})

    assert homepage_link.workable_url("www.jyp21.co.kr") == "http://www.jyp21.co.kr"
    assert 시도 == ["https://www.jyp21.co.kr", "http://www.jyp21.co.kr"], (
        "https를 먼저 시도한 뒤 http로 넘어가야 합니다"
    )
    homepage_link.workable_url.cache_clear()


def test_https가_멀쩡하면_그대로_https로_연다(monkeypatch: pytest.MonkeyPatch):
    """무회귀 못 — 이미 잘 열리던 주소를 http로 내리면 안 된다."""
    시도 = _스킴별_결과(monkeypatch, 살아있는_스킴={"https", "http"})

    assert homepage_link.workable_url("https://hybecorp.com") == "https://hybecorp.com"
    assert homepage_link.workable_url("hybecorp.com") == "https://hybecorp.com"
    assert all(url.startswith("https://") for url in 시도), (
        f"https가 살아 있는데 http를 시도했습니다: {시도}"
    )
    homepage_link.workable_url.cache_clear()


def test_둘다_안_열리면_https_주소를_그대로_돌려준다(monkeypatch: pytest.MonkeyPatch):
    """★ 무회귀의 핵심 — 빈 문자열이 아니라 https 주소가 와야
    수집기가 오늘처럼 «접속 실패»를 «자료 없음»과 구분해 보고할 수 있다.
    """
    _스킴별_결과(monkeypatch, 살아있는_스킴=set())

    assert homepage_link.workable_url("www.down.example") == "https://www.down.example"
    homepage_link.workable_url.cache_clear()
