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
