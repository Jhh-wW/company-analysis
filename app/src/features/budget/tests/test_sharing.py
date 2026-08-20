"""공유 보고서 응답의 캐시·노출 방지 헤더."""

from src.features.budget.sharing import SHARED_LINK_HEADERS


def test_공유_보고서는_개인화_응답을_캐시하지_않는다():
    assert SHARED_LINK_HEADERS["Cache-Control"] == "private, no-store"
    assert SHARED_LINK_HEADERS["Pragma"] == "no-cache"
    assert SHARED_LINK_HEADERS["Vary"] == "Cookie"


def test_공유_보고서는_검색과_리퍼러_노출도_막는다():
    assert SHARED_LINK_HEADERS["X-Robots-Tag"] == "noindex, nofollow, noarchive"
    assert SHARED_LINK_HEADERS["Referrer-Policy"] == "no-referrer"
