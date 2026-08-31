"""넓은 공식 웹 수집 오케스트레이션 회귀시험 — 실제 접속은 하지 않는다.

전송 계층(`WideRawResponse`를 돌려주는 가짜 `transport`)만 주입해 확인한다.
IR PDF 위임은 이미 `test_ir_pdf.py`가 그 내부 로직을 검증하므로, 여기서는
`collect_official_ir_fragments` 자체를 가짜로 바꿔 «이 모듈이 결과를 올바르게
문서로 바꾸고 상한을 지키는지»만 확인한다.
"""

from __future__ import annotations

from src.features.homepage import wide_collect
from src.features.homepage.constants import (
    WIDE_MAX_PAGES,
    WIDE_MAX_SITEMAP_ENTRIES,
    WIDE_REQUIRED_SLOT_IDS,
    WIDE_REQUIRED_SLOT_IDS_BY_SECTION,
)
from src.features.homepage.ir_pdf import OfficialIrCollectResult
from src.features.homepage.wide_collect import collect_official_web_documents
from src.features.homepage.wide_evidence_mapping import to_evidence_mappings
from src.features.homepage.wide_fetch import WideRawResponse, WideTransportError
from src.features.homepage.wide_fragments import build_fragments, build_fragments_for_collection

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


def test_robots가_401이면_본문을_긁지_않는다():
    """P1-1(ROBOTS-EXPLICIT-DENIAL): 401은 인증 요구 — 빈 규칙으로 진행하면
    안 된다(전체 오케스트레이션 수준 회귀)."""
    pages = {
        "https://company.example/robots.txt": WideRawResponse(
            status=401, text="", effective_url="https://company.example/robots.txt", content_type=""
        ),
        "https://company.example/": _page(_body("루트 페이지 본문입니다"), "https://company.example/"),
    }
    site = _FakeWideSite(pages)

    result = _collect(site)

    assert result.documents == ()
    robots_attempts = [a for a in result.attempts if a.source_kind == "robots_txt"]
    assert len(robots_attempts) == 1
    assert robots_attempts[0].state == "FAILED"
    assert robots_attempts[0].reason_code == "robots_denied"
    assert "https://company.example/" not in site.calls


def test_robots가_403이면_본문을_긁지_않는다():
    pages = {
        "https://company.example/robots.txt": WideRawResponse(
            status=403, text="", effective_url="https://company.example/robots.txt", content_type=""
        ),
        "https://company.example/": _page(_body("루트 페이지 본문입니다"), "https://company.example/"),
    }
    site = _FakeWideSite(pages)

    result = _collect(site)

    assert result.documents == ()
    assert "https://company.example/" not in site.calls


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


# ── P0-1: 등록 도메인 판정이 TLD를 무시하면 안 된다 ─────────


def test_같은_핵심이름_다른_TLD_링크는_REQUIRED로_자동승격되지_않는다():
    """company.example과 company.net은 다른 회사가 등록할 수 있는 별개 도메인.

    수정 전에는 registrable_core_name이 접미사를 떼고 핵심 이름 한 칸만
    비교해 «company»가 같다는 이유로 REQUIRED 고신뢰 문서로 자동 승격됐다.
    """
    pages = {
        "https://company.example/robots.txt": _page(ROBOTS_ALLOW_ALL, "https://company.example/robots.txt", "text/plain"),
        "https://company.example/sitemap.xml": _missing("https://company.example/sitemap.xml"),
        "https://company.example/": _page(
            _body("루트 페이지 본문") + '<a href="https://company.net/">남의 도메인</a>',
            "https://company.example/",
        ),
        "https://company.net/robots.txt": _page(ROBOTS_ALLOW_ALL, "https://company.net/robots.txt", "text/plain"),
        "https://company.net/sitemap.xml": _missing("https://company.net/sitemap.xml"),
        "https://company.net/": _page(_body("남의 회사 본문입니다"), "https://company.net/"),
    }
    site = _FakeWideSite(pages)

    result = _collect(site)

    other_docs = [doc for doc in result.documents if "company.net" in doc.canonical_url]
    assert len(other_docs) == 1
    # 링크로만 발견된 후보이므로 OPTIONAL(«후보»)이어야 한다 — REQUIRED 자동승격 금지.
    assert other_docs[0].requirement == "OPTIONAL"


def test_sitemap의_다른_TLD_URL은_등록도메인_밖이라_따라가지_않는다():
    """sitemap.xml이 도메인군 밖(다른 TLD) URL을 적어도 자동으로 큐에 넣으면 안 된다."""
    sitemap_xml = (
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<url><loc>https://company.example/about</loc></url>"
        "<url><loc>https://company.net/hijack</loc></url>"
        "</urlset>"
    )
    pages = {
        "https://company.example/robots.txt": _page(ROBOTS_ALLOW_ALL, "https://company.example/robots.txt", "text/plain"),
        "https://company.example/sitemap.xml": _page(
            sitemap_xml, "https://company.example/sitemap.xml", "application/xml"
        ),
        "https://company.example/": _page(_body("루트 페이지 본문"), "https://company.example/"),
        "https://company.example/about": _page(_body("회사소개 본문"), "https://company.example/about"),
    }
    site = _FakeWideSite(pages)

    result = _collect(site)

    assert not any("company.net" in call for call in site.calls)
    assert not any("company.net" in doc.canonical_url for doc in result.documents)


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


# ── slot_ids 매핑 ────────────────────────────────────────


def test_채용_페이지는_culture_슬롯을_받는다():
    pages = {
        "https://company.example/robots.txt": _page(ROBOTS_ALLOW_ALL, "https://company.example/robots.txt", "text/plain"),
        "https://company.example/sitemap.xml": _missing("https://company.example/sitemap.xml"),
        "https://company.example/": _page(
            _body("루트 페이지 본문") + '<a href="/careers">채용</a>', "https://company.example/"
        ),
        "https://company.example/careers": _page(_body("채용 페이지 본문"), "https://company.example/careers"),
    }
    site = _FakeWideSite(pages)

    result = _collect(site)

    careers_attempt = next(
        a for a in result.attempts if a.source_kind == "official_recruit_page" and a.state == "OK"
    )
    assert careers_attempt.slot_ids == WIDE_REQUIRED_SLOT_IDS_BY_SECTION["culture"]


def test_회사소개_페이지는_identity와_competitive_position_슬롯을_받는다():
    pages = {
        "https://company.example/robots.txt": _page(ROBOTS_ALLOW_ALL, "https://company.example/robots.txt", "text/plain"),
        "https://company.example/sitemap.xml": _missing("https://company.example/sitemap.xml"),
        "https://company.example/": _page(
            _body("루트 페이지 본문") + '<a href="/about">회사소개</a>', "https://company.example/"
        ),
        "https://company.example/about": _page(_body("회사소개 본문"), "https://company.example/about"),
    }
    site = _FakeWideSite(pages)

    result = _collect(site)

    about_attempt = next(
        a
        for a in result.attempts
        if a.source_kind == "official_web_page"
        and a.state == "OK"
        and a.slot_ids == (
            WIDE_REQUIRED_SLOT_IDS_BY_SECTION["identity"]
            + WIDE_REQUIRED_SLOT_IDS_BY_SECTION["competitive_position"]
        )
    )
    assert "competitive_position:self_context" in about_attempt.slot_ids


def test_제품_페이지는_portfolio와_business_model_슬롯을_받는다():
    pages = {
        "https://company.example/robots.txt": _page(ROBOTS_ALLOW_ALL, "https://company.example/robots.txt", "text/plain"),
        "https://company.example/sitemap.xml": _missing("https://company.example/sitemap.xml"),
        "https://company.example/": _page(
            _body("루트 페이지 본문") + '<a href="/products">제품</a>', "https://company.example/"
        ),
        "https://company.example/products": _page(_body("제품 소개 본문"), "https://company.example/products"),
    }
    site = _FakeWideSite(pages)

    result = _collect(site)

    # ★ P0-2 이후 루트("/") attempt도 fallback으로 slot_ids 17개 전체를 받으므로
    #   "in" 느슨한 대조 대신 «정확히 이 페이지 유형의 슬롯 집합과 같다»로
    #   고른다 — 루트 attempt(전체 17개)와 절대 같을 수 없어 혼동되지 않는다.
    expected_slots = set(
        WIDE_REQUIRED_SLOT_IDS_BY_SECTION["portfolio"] + WIDE_REQUIRED_SLOT_IDS_BY_SECTION["business_model"]
    )
    products_attempt = next(
        a for a in result.attempts if a.state == "OK" and set(a.slot_ids) == expected_slots
    )
    assert set(products_attempt.slot_ids) == expected_slots


def test_뉴스룸_페이지는_future_strategy와_past_changes_슬롯을_받는다():
    pages = {
        "https://company.example/robots.txt": _page(ROBOTS_ALLOW_ALL, "https://company.example/robots.txt", "text/plain"),
        "https://company.example/sitemap.xml": _missing("https://company.example/sitemap.xml"),
        "https://company.example/": _page(
            _body("루트 페이지 본문") + '<a href="/news">뉴스룸</a>', "https://company.example/"
        ),
        "https://company.example/news": _page(_body("뉴스룸 본문"), "https://company.example/news"),
    }
    site = _FakeWideSite(pages)

    result = _collect(site)

    # ★ P0-2 이후 루트("/") attempt도 fallback으로 slot_ids 17개 전체를 받으므로
    #   "in" 느슨한 대조 대신 «정확히 이 페이지 유형의 슬롯 집합과 같다»로 고른다.
    expected_slots = set(
        WIDE_REQUIRED_SLOT_IDS_BY_SECTION["future_strategy"] + WIDE_REQUIRED_SLOT_IDS_BY_SECTION["past_changes"]
    )
    news_attempt = next(
        a for a in result.attempts if a.state == "OK" and set(a.slot_ids) == expected_slots
    )
    assert set(news_attempt.slot_ids) == expected_slots


# ── P0-2: 어떤 attempt도 slot_ids가 비어 있으면 안 된다 ─────


def test_어떤_attempt도_slot_ids가_비어있지_않다_정상수집():
    """정상적인 최소 수집 한 번만 돌려도 robots·sitemap·루트페이지(‘/’는
    페이지 유형 키워드에 안 걸린다) attempt가 전부 생긴다 — 전부 비어
    있으면 안 된다."""
    pages = {
        "https://company.example/robots.txt": _page(ROBOTS_ALLOW_ALL, "https://company.example/robots.txt", "text/plain"),
        "https://company.example/sitemap.xml": _missing("https://company.example/sitemap.xml"),
        "https://company.example/": _page(_body("루트 페이지 본문"), "https://company.example/"),
    }
    site = _FakeWideSite(pages)

    result = _collect(site)

    assert result.attempts  # 최소한 robots·sitemap·root 페이지 attempt가 있다
    for attempt in result.attempts:
        assert attempt.slot_ids, f"{attempt.attempt_id}({attempt.source_kind})의 slot_ids가 비어 있다"


def test_URL로_페이지유형을_못알아낸_루트페이지는_fallback_전체슬롯을_받는다():
    pages = {
        "https://company.example/robots.txt": _page(ROBOTS_ALLOW_ALL, "https://company.example/robots.txt", "text/plain"),
        "https://company.example/sitemap.xml": _missing("https://company.example/sitemap.xml"),
        "https://company.example/": _page(_body("루트 페이지 본문"), "https://company.example/"),
    }
    site = _FakeWideSite(pages)

    result = _collect(site)

    root_attempt = next(
        a for a in result.attempts if a.source_kind == "official_web_page" and a.state == "OK"
    )
    assert set(root_attempt.slot_ids) == set(WIDE_REQUIRED_SLOT_IDS)


def test_robots_실패_attempt는_전체슬롯_fallback을_받는다():
    pages = {
        "https://company.example/": _page(_body("루트 페이지"), "https://company.example/"),
    }
    site = _FakeWideSite(pages)  # robots.txt 자체가 없어 조회 실패(FAILED)

    result = _collect(site)

    robots_attempt = next(a for a in result.attempts if a.source_kind == "robots_txt")
    assert robots_attempt.state == "FAILED"
    assert set(robots_attempt.slot_ids) == set(WIDE_REQUIRED_SLOT_IDS)


def test_sitemap_없음_attempt도_전체슬롯_fallback을_받는다():
    pages = {
        "https://company.example/robots.txt": _page(ROBOTS_ALLOW_ALL, "https://company.example/robots.txt", "text/plain"),
        "https://company.example/sitemap.xml": _missing("https://company.example/sitemap.xml"),
        "https://company.example/": _page(_body("루트 페이지 본문"), "https://company.example/"),
    }
    site = _FakeWideSite(pages)

    result = _collect(site)

    sitemap_attempt = next(a for a in result.attempts if a.reason_code.startswith("sitemap_missing"))
    assert set(sitemap_attempt.slot_ids) == set(WIDE_REQUIRED_SLOT_IDS)


def test_페이지수_상한_truncation도_전체슬롯_fallback을_받는다():
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

    truncated = next(a for a in result.attempts if a.reason_code == "truncated_page_cap")
    assert set(truncated.slot_ids) == set(WIDE_REQUIRED_SLOT_IDS)


def test_ir_attempt도_전체슬롯_fallback을_받는다(monkeypatch):
    def fake_collect_ir(homepage_url, **_kwargs):
        return OfficialIrCollectResult(state="none", fragments=[], downloaded_pdf_bytes=0)

    monkeypatch.setattr(wide_collect, "collect_official_ir_fragments", fake_collect_ir)

    pages = {
        "https://company.example/robots.txt": _page(ROBOTS_ALLOW_ALL, "https://company.example/robots.txt", "text/plain"),
        "https://company.example/sitemap.xml": _missing("https://company.example/sitemap.xml"),
        "https://company.example/": _page(_body("루트 페이지 본문"), "https://company.example/"),
    }
    site = _FakeWideSite(pages)

    result = _collect(site)

    ir_attempt = next(a for a in result.attempts if a.source_kind == "official_ir_pdf")
    assert set(ir_attempt.slot_ids) == set(WIDE_REQUIRED_SLOT_IDS)


# ── 정정 1(P0): REQUIRED + OK/MISSING이 광역 17-slot을 주장하면 안 된다 ──


def _assert_no_false_confirmation(attempts) -> None:
    """불변식: requirement가 REQUIRED이고 slot_ids가 허용 어휘 17개 전체(광역
    fallback)라면, state는 반드시 FAILED 또는 TRUNCATED여야 한다.

    OK·MISSING과 결합하면 「이 회사는 17개 slot 전부 근거가 없다」는 거짓
    확인(false confirmation)이 되어, 수집 사정(확인 못 함)과 회사 자료 부재
    (근거 없음)를 혼동하게 만든다(팀 리드 정정 1).
    """
    all_slots = set(WIDE_REQUIRED_SLOT_IDS)
    for attempt in attempts:
        if attempt.requirement == "REQUIRED" and set(attempt.slot_ids) == all_slots:
            assert attempt.state in ("FAILED", "TRUNCATED"), (
                f"거짓 확인 위반: attempt_id={attempt.attempt_id} "
                f"source_kind={attempt.source_kind} state={attempt.state}"
            )


def test_불변식_REQUIRED_광역slot은_FAILED_TRUNCATED와만_공존한다_정상수집():
    """가장 흔한 정상 수집 경로(robots ok, sitemap 없음, 루트 페이지 성공)만
    돌려도 위반이 없어야 한다 — 이게 바로 정정 1이 막으려는 실사용 경로다."""
    pages = {
        "https://company.example/robots.txt": _page(ROBOTS_ALLOW_ALL, "https://company.example/robots.txt", "text/plain"),
        "https://company.example/sitemap.xml": _missing("https://company.example/sitemap.xml"),
        "https://company.example/": _page(_body("루트 페이지 본문"), "https://company.example/"),
    }
    site = _FakeWideSite(pages)
    result = _collect(site)
    assert result.attempts
    _assert_no_false_confirmation(result.attempts)


def test_불변식_REQUIRED_광역slot은_FAILED_TRUNCATED와만_공존한다_robots차단():
    site = _FakeWideSite({"https://company.example/": _page(_body("루트 페이지"), "https://company.example/")})
    result = _collect(site)
    assert result.attempts
    _assert_no_false_confirmation(result.attempts)


def test_불변식_REQUIRED_광역slot은_FAILED_TRUNCATED와만_공존한다_페이지수상한():
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
    assert result.attempts
    _assert_no_false_confirmation(result.attempts)


def test_불변식_REQUIRED_광역slot은_FAILED_TRUNCATED와만_공존한다_sitemap_상한():
    entries = "".join(
        f"<url><loc>https://company.example/p{i}</loc></url>" for i in range(WIDE_MAX_SITEMAP_ENTRIES + 20)
    )
    sitemap_xml = '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + entries + "</urlset>"
    pages = {
        "https://company.example/robots.txt": _page(ROBOTS_ALLOW_ALL, "https://company.example/robots.txt", "text/plain"),
        "https://company.example/sitemap.xml": _page(
            sitemap_xml, "https://company.example/sitemap.xml", "application/xml"
        ),
        "https://company.example/": _page(_body("루트 페이지 본문"), "https://company.example/"),
    }
    site = _FakeWideSite(pages)
    result = _collect(site)
    assert result.attempts
    _assert_no_false_confirmation(result.attempts)


def test_불변식_REQUIRED_광역slot은_FAILED_TRUNCATED와만_공존한다_ir_none_failed(monkeypatch):
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
    assert result.attempts
    _assert_no_false_confirmation(result.attempts)


def test_robots_성공은_OPTIONAL로_낮아진다():
    """robots.txt를 성공적으로 읽었다는 사실 자체는 어떤 slot 근거의 유무도
    말해주지 않는다 — REQUIRED로 나가면 광역 fallback과 결합해 거짓 확인이 된다."""
    pages = {
        "https://company.example/robots.txt": _page(ROBOTS_ALLOW_ALL, "https://company.example/robots.txt", "text/plain"),
        "https://company.example/sitemap.xml": _missing("https://company.example/sitemap.xml"),
        "https://company.example/": _page(_body("루트 페이지 본문"), "https://company.example/"),
    }
    site = _FakeWideSite(pages)
    result = _collect(site)
    robots_attempt = next(a for a in result.attempts if a.source_kind == "robots_txt")
    assert robots_attempt.state == "OK"
    assert robots_attempt.requirement == "OPTIONAL"


def test_robots_차단은_REQUIRED를_유지한다():
    """실패는 「이것 때문에 확인 못 했다」는 정확한 뜻이라 REQUIRED를 유지해야
    소비측이 UNKNOWN으로 안전하게 이어간다."""
    site = _FakeWideSite({"https://company.example/": _page(_body("루트 페이지"), "https://company.example/")})
    result = _collect(site)
    robots_attempt = next(a for a in result.attempts if a.source_kind == "robots_txt")
    assert robots_attempt.state == "FAILED"
    assert robots_attempt.requirement == "REQUIRED"


def test_sitemap_성공은_OPTIONAL로_낮아진다():
    pages = {
        "https://company.example/robots.txt": _page(ROBOTS_ALLOW_ALL, "https://company.example/robots.txt", "text/plain"),
        "https://company.example/sitemap.xml": _page(
            '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
            "<url><loc>https://company.example/about</loc></url></urlset>",
            "https://company.example/sitemap.xml",
            "application/xml",
        ),
        "https://company.example/": _page(_body("루트 페이지 본문"), "https://company.example/"),
        "https://company.example/about": _page(_body("회사소개 본문"), "https://company.example/about"),
    }
    site = _FakeWideSite(pages)
    result = _collect(site)
    sitemap_attempt = next(a for a in result.attempts if a.reason_code == "sitemap_ok")
    assert sitemap_attempt.requirement == "OPTIONAL"


def test_sitemap_없음도_OPTIONAL로_낮아진다():
    pages = {
        "https://company.example/robots.txt": _page(ROBOTS_ALLOW_ALL, "https://company.example/robots.txt", "text/plain"),
        "https://company.example/sitemap.xml": _missing("https://company.example/sitemap.xml"),
        "https://company.example/": _page(_body("루트 페이지 본문"), "https://company.example/"),
    }
    site = _FakeWideSite(pages)
    result = _collect(site)
    sitemap_attempt = next(a for a in result.attempts if a.reason_code.startswith("sitemap_missing"))
    assert sitemap_attempt.requirement == "OPTIONAL"


def test_유형_미상_페이지_성공은_OPTIONAL로_낮아진다():
    """루트("/")처럼 URL로 페이지 유형을 못 알아낸 페이지가 성공(OK)했으면,
    build_fragments도 조각을 하나도 만들지 않으므로 이 attempt는 어떤 slot의
    REQUIRED 근거 경로도 아니다."""
    pages = {
        "https://company.example/robots.txt": _page(ROBOTS_ALLOW_ALL, "https://company.example/robots.txt", "text/plain"),
        "https://company.example/sitemap.xml": _missing("https://company.example/sitemap.xml"),
        "https://company.example/": _page(_body("루트 페이지 본문"), "https://company.example/"),
    }
    site = _FakeWideSite(pages)
    result = _collect(site)
    root_attempt = next(a for a in result.attempts if a.source_kind == "official_web_page" and a.state == "OK")
    assert set(root_attempt.slot_ids) == set(WIDE_REQUIRED_SLOT_IDS)
    assert root_attempt.requirement == "OPTIONAL"

    # 문서 자체의 requirement(등록 하위도메인 여부)는 attempt와 무관하게
    # 그대로 REQUIRED다 — attempt 전용 판단이 document까지 새어나가면 안 된다.
    root_document = next(doc for doc in result.documents if doc.canonical_url == "https://company.example/")
    assert root_document.requirement == "REQUIRED"


def test_유형이_잡히는_페이지_성공은_REQUIRED를_유지한다():
    """slot_ids가 실제로 좁혀진(비어 있지 않은) 경우는 광역 fallback이 아니므로
    정정 1과 무관하다 — 등록 하위도메인 페이지는 여전히 REQUIRED다."""
    pages = {
        "https://company.example/robots.txt": _page(ROBOTS_ALLOW_ALL, "https://company.example/robots.txt", "text/plain"),
        "https://company.example/sitemap.xml": _missing("https://company.example/sitemap.xml"),
        "https://company.example/": _page(
            _body("루트 페이지 본문") + '<a href="/about">회사소개</a>', "https://company.example/"
        ),
        "https://company.example/about": _page(_body("회사소개 본문"), "https://company.example/about"),
    }
    site = _FakeWideSite(pages)
    result = _collect(site)
    # ★ 루트("/") attempt도 광역 17-slot(OPTIONAL)을 받으므로 "in" 느슨한
    #   대조 대신 «about 페이지의 좁혀진 3-slot 집합과 정확히 같다»로 고른다.
    expected_slots = set(
        WIDE_REQUIRED_SLOT_IDS_BY_SECTION["identity"] + WIDE_REQUIRED_SLOT_IDS_BY_SECTION["competitive_position"]
    )
    about_attempt = next(a for a in result.attempts if set(a.slot_ids) == expected_slots)
    assert about_attempt.requirement == "REQUIRED"
    assert set(about_attempt.slot_ids) != set(WIDE_REQUIRED_SLOT_IDS)


def test_ir_none은_OPTIONAL로_낮아진다(monkeypatch):
    def fake_collect_ir(homepage_url, **_kwargs):
        return OfficialIrCollectResult(state="none", fragments=[], downloaded_pdf_bytes=0)

    monkeypatch.setattr(wide_collect, "collect_official_ir_fragments", fake_collect_ir)
    pages = {
        "https://company.example/robots.txt": _page(ROBOTS_ALLOW_ALL, "https://company.example/robots.txt", "text/plain"),
        "https://company.example/sitemap.xml": _missing("https://company.example/sitemap.xml"),
        "https://company.example/": _page(_body("루트 페이지 본문"), "https://company.example/"),
    }
    site = _FakeWideSite(pages)
    result = _collect(site)
    ir_attempt = next(a for a in result.attempts if a.source_kind == "official_ir_pdf")
    assert ir_attempt.state == "MISSING"
    assert ir_attempt.requirement == "OPTIONAL"


def test_ir_failed는_REQUIRED를_유지한다(monkeypatch):
    def fake_collect_ir_failed(homepage_url, **_kwargs):
        return OfficialIrCollectResult(state="failed", fragments=[], downloaded_pdf_bytes=0)

    monkeypatch.setattr(wide_collect, "collect_official_ir_fragments", fake_collect_ir_failed)
    pages = {
        "https://company.example/robots.txt": _page(ROBOTS_ALLOW_ALL, "https://company.example/robots.txt", "text/plain"),
        "https://company.example/sitemap.xml": _missing("https://company.example/sitemap.xml"),
        "https://company.example/": _page(_body("루트 페이지 본문"), "https://company.example/"),
    }
    site = _FakeWideSite(pages)
    result = _collect(site)
    ir_attempt = next(a for a in result.attempts if a.source_kind == "official_ir_pdf")
    assert ir_attempt.state == "FAILED"
    assert ir_attempt.requirement == "REQUIRED"


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


# ── 계약 generation=8: 모든 attempt·fragment가 대상 회사 company_id를 갖는다 ──


def test_모든_attempt는_대상_회사_company_id를_갖는다():
    """robots·sitemap·페이지 attempt 전부가 이 수집 실행이 대상으로 한
    회사 값을 실어야 한다 — 문서가 하나도 안 만들어져도(예: robots 차단)
    attempt 자체는 남으므로 그 attempt에도 대상 회사가 찍혀야 한다."""
    links = "".join(f'<a href="/page{i}">페이지{i}</a>' for i in range(3))
    pages = {
        "https://company.example/robots.txt": _page(ROBOTS_ALLOW_ALL, "https://company.example/robots.txt", "text/plain"),
        "https://company.example/sitemap.xml": _missing("https://company.example/sitemap.xml"),
        "https://company.example/": _page(_body("루트 페이지 본문") + links, "https://company.example/"),
    }
    for i in range(3):
        url = f"https://company.example/page{i}"
        pages[url] = _page(_body(f"페이지{i} 본문"), url)
    site = _FakeWideSite(pages)

    result = _collect(site, company_id="dart-00012345")

    assert result.attempts  # 최소 robots·sitemap·페이지 attempt가 있다
    assert all(a.company_id == "dart-00012345" for a in result.attempts)


def test_robots_차단으로_문서가_0건이어도_attempt의_company_id는_대상_회사다():
    site = _FakeWideSite({"https://company.example/": _page(_body("루트 페이지"), "https://company.example/")})

    result = _collect(site, company_id="dart-99999999")

    assert result.documents == ()
    assert result.attempts
    assert all(a.company_id == "dart-99999999" for a in result.attempts)
    # 계약 gen=8 마지막 고리 — 문서가 0건이라 company_id를 역산할 곳이 없어도
    # 결과 자신은 대상 회사를 잃지 않는다.
    assert result.company_id == "dart-99999999"


def test_홈페이지_주소가_비어있어도_결과는_대상_회사를_싣는다():
    """documents·attempts가 둘 다 0건인 가장 극단적인 경우도 결과 자신의
    company_id는 남아야 한다."""
    site = _FakeWideSite({})

    result = _collect(site, root_homepage_url="", company_id="dart-88888888")

    assert result.documents == ()
    assert result.attempts == ()
    assert result.company_id == "dart-88888888"


def test_전체_파이프라인_fragment와_attempt_모두_대상_회사_company_id를_갖는다():
    """수집(wide_collect) → 조각화(build_fragments_for_collection) →
    변환(to_evidence_mappings) 전체를 실제로 이어 돌려, 최종 산출의
    documents·fragments·attempts 전부가 같은 대상 회사 company_id를
    갖는지 왕복으로 고정한다."""
    target_company_id = "target-co"
    pages = {
        "https://company.example/robots.txt": _page(ROBOTS_ALLOW_ALL, "https://company.example/robots.txt", "text/plain"),
        "https://company.example/sitemap.xml": _missing("https://company.example/sitemap.xml"),
        "https://company.example/": _page(
            _body("루트 페이지 본문") + '<a href="/careers">채용</a>', "https://company.example/"
        ),
        "https://company.example/careers": _page(_body("채용 페이지 본문"), "https://company.example/careers"),
    }
    site = _FakeWideSite(pages)

    result = _collect(site, company_id=target_company_id)
    assert result.documents  # 실제로 문서가 만들어졌는지 확인(공허한 통과 방지)

    # 운영 호출부 패턴(팀 리드 2026-08-31 지시) — 문서마다 company_id를 손으로
    # 옮겨 적지 않고, 수집 결과 자신에서 한 번만 꺼내는 편의 함수를 쓴다.
    fragments = build_fragments_for_collection(result)
    assert fragments  # 실제로 조각이 만들어졌는지 확인(공허한 통과 방지)

    # 왕복 — 저수준 build_fragments를 문서마다 같은 company_id로 부른 것과 동일하다.
    manual_fragments = tuple(
        fragment
        for document in result.documents
        for fragment in build_fragments(document, company_id=target_company_id)
    )
    assert fragments == manual_fragments

    mapped = to_evidence_mappings(documents=result.documents, fragments=fragments, attempts=result.attempts)

    assert mapped["documents"]
    assert all(doc["company_id"] == target_company_id for doc in mapped["documents"])
    assert mapped["fragments"]
    assert all(frag["company_id"] == target_company_id for frag in mapped["fragments"])
    assert mapped["attempts"]
    assert all(att["company_id"] == target_company_id for att in mapped["attempts"])


def test_홈페이지_주소가_비어있으면_빈_결과():
    site = _FakeWideSite({})

    result = _collect(site, root_homepage_url="")

    assert result.documents == ()
    assert result.attempts == ()
