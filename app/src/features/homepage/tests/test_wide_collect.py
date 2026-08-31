"""넓은 공식 웹 수집 오케스트레이션 회귀시험 — 실제 접속은 하지 않는다.

전송 계층(`WideRawResponse`를 돌려주는 가짜 `transport`)만 주입해 확인한다.
IR PDF 위임은 이미 `test_ir_pdf.py`가 그 내부 로직을 검증하므로, 여기서는
`collect_official_ir_fragments` 자체를 가짜로 바꿔 «이 모듈이 결과를 올바르게
문서로 바꾸고 상한을 지키는지»만 확인한다.
"""

from __future__ import annotations

from src.features.homepage import wide_collect
from src.features.homepage.constants import WIDE_MAX_PAGES, WIDE_MAX_SITEMAP_ENTRIES
from src.features.homepage.ir_pdf import OfficialIrCollectResult
from src.features.homepage.wide_collect import collect_official_web_documents
from src.features.homepage.wide_fetch import WideRawResponse, WideTransportError

ROBOTS_ALLOW_ALL = "User-agent: *\nAllow: /\n"


class _FakeWideSite:
    """가짜 전송 계층 — 호출된 URL을 기록하고, 리다이렉트도 흉내 낸다.

    `pages`에 없는 URL은 접속 실패(``WideTransportError``)로 본다.
    ``effective_url``이 요청 URL과 다른 호스트면 실제 safe_urlopen의 리다이렉트
    재검사처럼 호출자의 ``url_allowed``로 다시 검사한다.
    """

    def __init__(self, pages: dict[str, WideRawResponse]) -> None:
        self.pages = pages
        self.calls: list[str] = []

    def transport(self, url: str, url_allowed):
        self.calls.append(url)
        if url not in self.pages:
            raise WideTransportError(f"가짜 접속 실패: {url}")
        response = self.pages[url]
        if url_allowed is not None and not url_allowed(response.effective_url):
            raise WideTransportError(f"가짜 정책 차단: {url} -> {response.effective_url}")
        return response


def _page(text: str, url: str, content_type: str = "text/html") -> WideRawResponse:
    return WideRawResponse(status=200, text=text, effective_url=url, content_type=content_type)


def _missing(url: str) -> WideRawResponse:
    return WideRawResponse(status=404, text="", effective_url=url, content_type="")


def _no_ir(url: str, *_args, **_kwargs):
    from src.features.homepage.ir_pdf import FetchedIrHtml, OfficialIrFetchError

    if url.endswith("/robots.txt"):
        return FetchedIrHtml("", url)
    raise OfficialIrFetchError("가짜 IR HTML 없음")


def _no_ir_pdf(*_args, **_kwargs):
    from src.features.homepage.ir_pdf import OfficialIrFetchError

    raise OfficialIrFetchError("가짜 IR PDF 없음")


def _collect(site: _FakeWideSite, **overrides) -> object:
    kwargs = dict(
        company_id="c1",
        company_name="Example Company",
        root_homepage_url="company.example",
        collected_at="2026-08-31T00:00:00+00:00",
        transport=site.transport,
        ir_html_fetch=_no_ir,
        ir_pdf_fetch=_no_ir_pdf,
    )
    kwargs.update(overrides)
    return collect_official_web_documents(**kwargs)


def _body(text: str) -> str:
    return "<html><body><main><p>" + (text + " ") * 10 + "</p></main></body></html>"


# ── robots ────────────────────────────────────────────────


def test_robots_금지_경로는_수집하지_않는다():
    pages = {
        "https://company.example/robots.txt": _page(
            "User-agent: *\nDisallow: /private\n", "https://company.example/robots.txt", "text/plain"
        ),
        "https://company.example/sitemap.xml": _missing("https://company.example/sitemap.xml"),
        "https://company.example/": _page(
            _body("공개 페이지입니다") + '<a href="/private">비공개</a>',
            "https://company.example/",
        ),
        "https://company.example/private": _page(_body("비공개 내용"), "https://company.example/private"),
    }
    site = _FakeWideSite(pages)

    result = _collect(site)

    assert not any(doc.canonical_url.endswith("/private") for doc in result.documents)
    assert "https://company.example/private" not in site.calls


def test_robots_조회_실패시_본문을_긁지_않는다():
    pages = {
        "https://company.example/": _page(_body("루트 페이지"), "https://company.example/"),
    }
    site = _FakeWideSite(pages)  # robots.txt 자체가 pages에 없어 접속 실패

    result = _collect(site)

    assert result.documents == ()
    robots_attempts = [a for a in result.attempts if a.source_kind == "robots_txt"]
    assert len(robots_attempts) == 1
    assert robots_attempts[0].state == "FAILED"
    assert robots_attempts[0].reason_code == "robots_unreachable"
    assert "https://company.example/" not in site.calls  # 본문은 시도조차 하지 않는다


def test_robots가_4xx면_빈_규칙으로_진행한다():
    pages = {
        "https://company.example/robots.txt": _missing("https://company.example/robots.txt"),
        "https://company.example/sitemap.xml": _missing("https://company.example/sitemap.xml"),
        "https://company.example/": _page(_body("루트 페이지 본문입니다"), "https://company.example/"),
    }
    site = _FakeWideSite(pages)

    result = _collect(site)

    assert any(doc.canonical_url == "https://company.example/" for doc in result.documents)


# ── 도메인군 ──────────────────────────────────────────────


def test_등록_하위도메인은_자동결속되어_REQUIRED_문서가_된다():
    pages = {
        "https://company.example/robots.txt": _page(ROBOTS_ALLOW_ALL, "https://company.example/robots.txt", "text/plain"),
        "https://company.example/sitemap.xml": _missing("https://company.example/sitemap.xml"),
        "https://company.example/": _page(
            _body("루트 페이지 본문") + '<a href="https://recruit.company.example/">채용</a>',
            "https://company.example/",
        ),
        "https://recruit.company.example/robots.txt": _missing(
            "https://recruit.company.example/robots.txt"
        ),
        "https://recruit.company.example/sitemap.xml": _missing(
            "https://recruit.company.example/sitemap.xml"
        ),
        "https://recruit.company.example/": _page(
            _body("채용 페이지 본문입니다"), "https://recruit.company.example/"
        ),
    }
    site = _FakeWideSite(pages)

    result = _collect(site)

    recruit_docs = [doc for doc in result.documents if "recruit.company.example" in doc.canonical_url]
    assert len(recruit_docs) == 1
    assert recruit_docs[0].requirement == "REQUIRED"
    assert recruit_docs[0].source_kind == "official_recruit_page"


def test_링크로_발견된_후보_호스트는_OPTIONAL_문서가_된다():
    pages = {
        "https://company.example/robots.txt": _page(ROBOTS_ALLOW_ALL, "https://company.example/robots.txt", "text/plain"),
        "https://company.example/sitemap.xml": _missing("https://company.example/sitemap.xml"),
        "https://company.example/": _page(
            _body("루트 페이지 본문") + '<a href="https://brand-site.example/">브랜드 사이트</a>',
            "https://company.example/",
        ),
        "https://brand-site.example/robots.txt": _missing("https://brand-site.example/robots.txt"),
        "https://brand-site.example/sitemap.xml": _missing("https://brand-site.example/sitemap.xml"),
        "https://brand-site.example/": _page(
            _body("브랜드 사이트 본문입니다"), "https://brand-site.example/"
        ),
    }
    site = _FakeWideSite(pages)

    result = _collect(site)

    brand_docs = [doc for doc in result.documents if "brand-site.example" in doc.canonical_url]
    assert len(brand_docs) == 1
    assert brand_docs[0].requirement == "OPTIONAL"
    assert "https://company.example/" in brand_docs[0].identity_binding


def test_도메인군_밖으로의_리다이렉트는_차단된다():
    pages = {
        "https://company.example/robots.txt": _page(ROBOTS_ALLOW_ALL, "https://company.example/robots.txt", "text/plain"),
        "https://company.example/sitemap.xml": _missing("https://company.example/sitemap.xml"),
        "https://company.example/": _page(
            _body("루트 페이지 본문") + '<a href="/redir">이동</a>',
            "https://company.example/",
        ),
        # /redir의 응답은 실제로는 evil.example로 리다이렉트된 것처럼 effective_url이 다르다.
        "https://company.example/redir": _page(
            _body("가짜 본문"), "https://evil.example/"
        ),
    }
    site = _FakeWideSite(pages)

    result = _collect(site)

    assert not any("evil.example" in doc.canonical_url for doc in result.documents)
    assert not any("evil.example" in doc.publisher for doc in result.documents)


def test_소셜_링크는_결속되지_않는다():
    pages = {
        "https://company.example/robots.txt": _page(ROBOTS_ALLOW_ALL, "https://company.example/robots.txt", "text/plain"),
        "https://company.example/sitemap.xml": _missing("https://company.example/sitemap.xml"),
        "https://company.example/": _page(
            _body("루트 페이지 본문") + '<a href="https://facebook.com/company">페이스북</a>',
            "https://company.example/",
        ),
    }
    site = _FakeWideSite(pages)

    result = _collect(site)

    assert not any("facebook.com" in doc.canonical_url for doc in result.documents)
    assert "https://facebook.com/company" not in site.calls


# ── sitemap ───────────────────────────────────────────────


def test_sitemap_상한_도달시_TRUNCATED():
    entries = "".join(
        f"<url><loc>https://company.example/p{i}</loc></url>" for i in range(WIDE_MAX_SITEMAP_ENTRIES + 20)
    )
    sitemap_xml = (
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + entries + "</urlset>"
    )
    pages = {
        "https://company.example/robots.txt": _page(ROBOTS_ALLOW_ALL, "https://company.example/robots.txt", "text/plain"),
        "https://company.example/sitemap.xml": _page(
            sitemap_xml, "https://company.example/sitemap.xml", "application/xml"
        ),
        "https://company.example/": _page(_body("루트 페이지 본문"), "https://company.example/"),
    }
    site = _FakeWideSite(pages)

    result = _collect(site)

    sitemap_attempts = [a for a in result.attempts if a.reason_code == "sitemap_ok"]
    assert len(sitemap_attempts) == 1
    assert sitemap_attempts[0].state == "TRUNCATED"


# ── 페이지·바이트 상한 ───────────────────────────────────


def test_페이지_수_상한을_넘지_않는다():
    links = "".join(f'<a href="/page{i}">페이지{i}</a>' for i in range(WIDE_MAX_PAGES + 10))
    pages = {
        "https://company.example/robots.txt": _page(ROBOTS_ALLOW_ALL, "https://company.example/robots.txt", "text/plain"),
        "https://company.example/sitemap.xml": _missing("https://company.example/sitemap.xml"),
        "https://company.example/": _page(_body("루트 페이지 본문") + links, "https://company.example/"),
    }
    for i in range(WIDE_MAX_PAGES + 10):
        url = f"https://company.example/page{i}"
        pages[url] = _page(_body(f"페이지{i} 본문"), url)
    site = _FakeWideSite(pages)

    result = _collect(site)

    page_calls = [c for c in site.calls if not c.endswith("robots.txt") and not c.endswith("sitemap.xml")]
    assert len(page_calls) <= WIDE_MAX_PAGES
    assert any(a.state == "TRUNCATED" and a.reason_code == "truncated_page_cap" for a in result.attempts)


def test_바이트_상한_도달시_TRUNCATED():
    huge_text = "본문 문단입니다. " * 400_000  # 약 4.8MB
    pages = {
        "https://company.example/robots.txt": _page(ROBOTS_ALLOW_ALL, "https://company.example/robots.txt", "text/plain"),
        "https://company.example/sitemap.xml": _missing("https://company.example/sitemap.xml"),
        "https://company.example/": _page(
            "<html><body><main><p>" + huge_text + "</p></main></body>"
            '<a href="/page2">2</a></html>',
            "https://company.example/",
        ),
        "https://company.example/page2": _page(
            "<html><body><main><p>" + huge_text + "</p></main></body></html>",
            "https://company.example/page2",
        ),
    }
    site = _FakeWideSite(pages)

    result = _collect(site)

    assert any(a.state == "TRUNCATED" and a.reason_code == "truncated_byte_cap" for a in result.attempts)


# ── 문서 내용 ─────────────────────────────────────────────


def test_json_ld와_inline_데이터가_usable_ranges에_포함된다():
    html = (
        "<html><head>"
        '<script type="application/ld+json">{"@type": "Organization", "description": "우리는 예시 산업의 선두 회사입니다"}</script>'
        '<script id="__NEXT_DATA__" type="application/json">{"props": {"pageProps": {"tagline": "혁신을 만드는 사람들 문구"}}}</script>'
        "</head><body><main><p>" + ("기본 본문입니다. " * 10) + "</p></main></body></html>"
    )
    pages = {
        "https://company.example/robots.txt": _page(ROBOTS_ALLOW_ALL, "https://company.example/robots.txt", "text/plain"),
        "https://company.example/sitemap.xml": _missing("https://company.example/sitemap.xml"),
        "https://company.example/": _page(html, "https://company.example/"),
    }
    site = _FakeWideSite(pages)

    result = _collect(site)

    assert len(result.documents) == 1
    joined = " ".join(result.documents[0].usable_ranges)
    assert "예시 산업의 선두" in joined
    assert "혁신을 만드는 사람들" in joined
    assert "기본 본문입니다" in joined


def test_canonical_url_정규화로_추적파라미터만_다른_링크는_중복제거된다():
    html = (
        _body("루트 페이지 본문입니다")
        + '<a href="/about?utm_source=news">소개1</a>'
        + '<a href="/about?utm_source=blog">소개2</a>'
    )
    about_html = _body("회사소개 본문입니다")
    pages = {
        "https://company.example/robots.txt": _page(ROBOTS_ALLOW_ALL, "https://company.example/robots.txt", "text/plain"),
        "https://company.example/sitemap.xml": _missing("https://company.example/sitemap.xml"),
        "https://company.example/": _page(html, "https://company.example/"),
        "https://company.example/about?utm_source=news": _page(
            about_html, "https://company.example/about?utm_source=news"
        ),
    }
    site = _FakeWideSite(pages)

    result = _collect(site)

    about_docs = [doc for doc in result.documents if doc.canonical_url.startswith("https://company.example/about")]
    assert len(about_docs) == 1
    assert about_docs[0].canonical_url == "https://company.example/about"


def test_같은_내용_다른_URL은_내용해시로_중복제거된다():
    same_text = _body("완전히 같은 본문 내용입니다")
    pages = {
        "https://company.example/robots.txt": _page(ROBOTS_ALLOW_ALL, "https://company.example/robots.txt", "text/plain"),
        "https://company.example/sitemap.xml": _missing("https://company.example/sitemap.xml"),
        "https://company.example/": _page(
            same_text + '<a href="/mirror">거울본</a>', "https://company.example/"
        ),
        "https://company.example/mirror": _page(same_text, "https://company.example/mirror"),
    }
    site = _FakeWideSite(pages)

    result = _collect(site)

    assert len(result.documents) == 1


# ── IR PDF 위임 ───────────────────────────────────────────


def test_ir_pdf는_3건_상한을_넘지_않는다(monkeypatch):
    def fake_collect_ir(homepage_url, **_kwargs):
        host = homepage_url.split("://", 1)[1].rstrip("/")
        fragments = [
            {
                "종류": "공식 IR",
                "원문": f"{host} 문서 {i} 본문 내용입니다",
                "출처": f"https://{host}/ir/doc{i}.pdf",
                "문서ID": f"{host}-doc{i}",
                "문서명": f"{host} 보고서 {i}",
            }
            for i in range(3)
        ]
        return OfficialIrCollectResult(state="ok", fragments=fragments, downloaded_pdf_bytes=1000)

    monkeypatch.setattr(wide_collect, "collect_official_ir_fragments", fake_collect_ir)

    pages = {
        "https://company.example/robots.txt": _page(ROBOTS_ALLOW_ALL, "https://company.example/robots.txt", "text/plain"),
        "https://company.example/sitemap.xml": _missing("https://company.example/sitemap.xml"),
        "https://company.example/": _page(_body("루트 페이지 본문"), "https://company.example/"),
    }
    site = _FakeWideSite(pages)

    result = _collect(site)

    ir_docs = [doc for doc in result.documents if doc.source_kind == "official_ir_pdf"]
    assert len(ir_docs) == 3


def test_ir_pdf_none과_failed는_MISSING과_FAILED로_분리된다(monkeypatch):
    def fake_collect_ir(homepage_url, **_kwargs):
        if "company.example" in homepage_url:
            return OfficialIrCollectResult(state="none", fragments=[], downloaded_pdf_bytes=0)
        return OfficialIrCollectResult(state="failed", fragments=[], downloaded_pdf_bytes=0)

    monkeypatch.setattr(wide_collect, "collect_official_ir_fragments", fake_collect_ir)

    pages = {
        "https://company.example/robots.txt": _page(ROBOTS_ALLOW_ALL, "https://company.example/robots.txt", "text/plain"),
        "https://company.example/sitemap.xml": _missing("https://company.example/sitemap.xml"),
        "https://company.example/": _page(_body("루트 페이지 본문"), "https://company.example/"),
    }
    site = _FakeWideSite(pages)

    result = _collect(site)

    ir_attempts = [a for a in result.attempts if a.source_kind == "official_ir_pdf"]
    assert len(ir_attempts) == 1
    assert ir_attempts[0].state == "MISSING"


# ── company_id 전달 ──────────────────────────────────────


def test_company_id는_문서에_그대로_전달된다():
    pages = {
        "https://company.example/robots.txt": _page(ROBOTS_ALLOW_ALL, "https://company.example/robots.txt", "text/plain"),
        "https://company.example/sitemap.xml": _missing("https://company.example/sitemap.xml"),
        "https://company.example/": _page(_body("루트 페이지 본문"), "https://company.example/"),
    }
    site = _FakeWideSite(pages)

    result = _collect(site, company_id="dart-00012345")

    assert result.documents
    assert all(doc.company_id == "dart-00012345" for doc in result.documents)


def test_홈페이지_주소가_비어있으면_빈_결과():
    site = _FakeWideSite({})

    result = _collect(site, root_homepage_url="")

    assert result.documents == ()
    assert result.attempts == ()
