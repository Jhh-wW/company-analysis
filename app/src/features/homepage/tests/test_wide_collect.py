"""넓은 공식 웹 수집 오케스트레이션 회귀시험 — 실제 접속은 하지 않는다.

전송 계층(`WideRawResponse`를 돌려주는 가짜 `transport`)만 주입해 확인한다.
IR PDF 위임은 이미 `test_ir_pdf.py`가 그 내부 로직을 검증하므로, 여기서는
`collect_official_ir_fragments` 자체를 가짜로 바꿔 «이 모듈이 결과를 올바르게
문서로 바꾸고 상한을 지키는지»만 확인한다.
"""

from __future__ import annotations

import hashlib
import urllib.parse

import pytest

from src.features.homepage import wide_collect
from src.features.homepage.constants import (
    WIDE_MAX_HOSTS,
    WIDE_MAX_PAGES,
    WIDE_MAX_SITEMAP_ENTRIES,
    WIDE_COLLECTOR_VERSION,
    WIDE_PARSER_VERSION,
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
    # APEX-WWW-OFFICIAL-ROOT-GAP 이후 primary(company.example)와 apex/www
    # 짝(www.company.example)이 각각 robots를 따로 확인하므로 2건이다.
    # attempt는 생성 순서를 그대로 보존하므로 [0]이 항상 primary다.
    assert len(robots_attempts) == 2
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
    # apex/www 짝(www.company.example)도 별도로 robots를 확인하므로 2건이다
    # (그 짝은 pages에 없어 접속 자체가 실패 — robots_unreachable). [0]은
    # 생성 순서상 항상 primary(company.example)다.
    assert len(robots_attempts) == 2
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


@pytest.mark.parametrize(
    ("status", "expected_reason_code"),
    [
        (407, "robots_denied"),
        (408, "robots_transient"),
        (409, "robots_transient"),
        (429, "robots_transient"),
    ],
)
def test_robots_거부_일시장애_상태는_일반_전송_호출이_0회다(status, expected_reason_code):
    """P1(통합 담당 지시, 확장): robots_decision의 단위 분류
    (407·408·409·429 전부 blocked)를 상태마다 실제 수집 전체로 증명한다.
    「robots가 아닌」 전송 호출 수를 세어 정말 0인지 확인한다 — 특정 URL
    문자열이 calls 안에 없다는 것만으로는 다른 형태의 우회 호출을 놓칠 수
    있어, 통합 담당이 407·429 두 상태만으로는 증명이 불완전하다고 지적했다."""
    pages = {
        "https://company.example/robots.txt": WideRawResponse(
            status=status, text="", effective_url="https://company.example/robots.txt", content_type=""
        ),
        "https://company.example/": _page(_body("루트 페이지 본문입니다"), "https://company.example/"),
    }
    site = _FakeWideSite(pages)

    result = _collect(site)

    assert result.documents == ()
    non_robots_calls = [c for c in site.calls if not c.endswith("robots.txt")]
    assert non_robots_calls == [], f"status={status}: robots 아닌 전송 호출이 있었다: {non_robots_calls}"
    robots_attempt = next(a for a in result.attempts if a.source_kind == "robots_txt")
    assert robots_attempt.reason_code == expected_reason_code, f"status={status}"


def test_robots가_그밖의_4xx면_본문을_긁지_않는다():
    """400처럼 denied·transient·missing 어디에도 없는 4xx는 «명시적 부재로
    진행」이 아니라 차단이어야 한다."""
    pages = {
        "https://company.example/robots.txt": WideRawResponse(
            status=400, text="", effective_url="https://company.example/robots.txt", content_type=""
        ),
        "https://company.example/": _page(_body("루트 페이지 본문입니다"), "https://company.example/"),
    }
    site = _FakeWideSite(pages)

    result = _collect(site)

    assert result.documents == ()
    assert "https://company.example/" not in site.calls
    robots_attempt = next(a for a in result.attempts if a.source_kind == "robots_txt")
    assert robots_attempt.reason_code == "robots_unreachable"


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


def test_공식페이지의_외부_vendor_링크는_문서로_승격하거나_호출하지_않는다():
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
    assert brand_docs == []
    assert not any("brand-site.example" in url for url in site.calls)


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


def test_DART_공유호스트의_port와_회사경로를_버리지_않는다():
    base = "https://sites.example.com:8443"
    pages = {
        f"{base}/robots.txt": _page(ROBOTS_ALLOW_ALL, f"{base}/robots.txt", "text/plain"),
        f"{base}/sitemap.xml": _missing(f"{base}/sitemap.xml"),
        f"{base}/acme": _page(
            _body("에이씨미 회사 사업 소개")
            + '<a href="/acme/products">제품</a><a href="/other">다른 입주자</a>',
            f"{base}/acme",
        ),
        f"{base}/acme/products": _page(_body("에이씨미 제품과 고객"), f"{base}/acme/products"),
        f"{base}/other": _page(_body("다른 회사 본문"), f"{base}/other"),
    }
    site = _FakeWideSite(pages)
    ir_calls: list[str] = []

    def ir_html(url, *_args, **_kwargs):
        ir_calls.append(url)
        raise AssertionError("비기본 port를 HTTPS:443으로 바꿔 IR 호출하면 안 됩니다")

    result = _collect(
        site,
        root_homepage_url=f"{base}/acme",
        ir_html_fetch=ir_html,
    )

    assert f"{base}/acme" in site.calls
    assert f"{base}/" not in site.calls
    assert f"{base}/other" not in site.calls
    assert f"{base}/acme/products" in site.calls
    assert not any("www.sites.example.com" in url for url in site.calls)
    assert ir_calls == []
    assert {document.canonical_url for document in result.documents} == {
        f"{base}/acme",
        f"{base}/acme/products",
    }


def test_공유host_query_tenant는_시작값을_정확히_보존하고_다른입주자를_0회호출한다():
    base = "https://portal.example"
    target_root = f"{base}/view?tenant=ALPHA"
    target_child = f"{base}/view/about?tenant=ALPHA&page=2&utm_source=x"
    other_tenant = f"{base}/view/about?tenant=BETA"
    duplicate_tenant = f"{base}/view/about?tenant=ALPHA&tenant=BETA"
    injected_company = f"{base}/view/about?tenant=ALPHA&company=BETA"
    pages = {
        f"{base}/robots.txt": _page(
            ROBOTS_ALLOW_ALL, f"{base}/robots.txt", "text/plain"
        ),
        f"{base}/sitemap.xml": _missing(f"{base}/sitemap.xml"),
        target_root: _page(
            _body("2010년에 설립한 법인")
            + f'<a href="{target_child}">대상 회사</a>'
            + f'<a href="{other_tenant}">다른 입주자</a>'
            + f'<a href="{duplicate_tenant}">중복 tenant</a>'
            + f'<a href="{injected_company}">새 company</a>',
            target_root,
        ),
        target_child: _page(_body("주요 사업을 영위하는 전문기업"), target_child),
        other_tenant: _page(_body("다른 회사의 주요 사업"), other_tenant),
        duplicate_tenant: _page(_body("다른 회사의 중복 tenant"), duplicate_tenant),
        injected_company: _page(_body("다른 company 본문"), injected_company),
    }
    site = _FakeWideSite(pages)

    result = _collect(
        site,
        root_homepage_url=target_root,
        company_name="",
    )

    assert target_child in site.calls
    assert other_tenant not in site.calls
    assert duplicate_tenant not in site.calls
    assert injected_company not in site.calls
    assert all("tenant=BETA" not in document.canonical_url for document in result.documents)


def test_ref_scope는_변경과_누락을_0회호출하고_page_탐색만_허용한다():
    base = "https://portal.example"
    root = f"{base}/view?ref=ALPHA"
    allowed = f"{base}/view/about?ref=ALPHA&page=2"
    changed = f"{base}/view/about?ref=BETA"
    missing = f"{base}/view/about"
    pages = {
        f"{base}/robots.txt": _page(
            ROBOTS_ALLOW_ALL, f"{base}/robots.txt", "text/plain"
        ),
        f"{base}/sitemap.xml": _missing(f"{base}/sitemap.xml"),
        root: _page(
            _body("2010년에 설립한 법인")
            + f'<a href="{allowed}">다음 페이지</a>'
            + f'<a href="{changed}">다른 ref</a>'
            + f'<a href="{missing}">ref 누락</a>',
            root,
        ),
        allowed: _page(_body("주요 사업을 영위하는 전문기업"), allowed),
        changed: _page(_body("다른 회사 본문"), changed),
        missing: _page(_body("경계가 사라진 본문"), missing),
    }
    site = _FakeWideSite(pages)

    result = _collect(site, root_homepage_url=root, company_name="")

    assert allowed in site.calls
    assert changed not in site.calls
    assert missing not in site.calls
    assert any(
        document.canonical_url == f"{base}/view/about?page=2&ref=ALPHA"
        for document in result.documents
    )


def test_시작에_없던_tenant_query는_queue전_거절되어_0회호출된다():
    base = "https://portal.example"
    root = f"{base}/acme"
    injected = f"{base}/acme/about?tenant=BETA"
    pages = {
        f"{base}/robots.txt": _page(
            ROBOTS_ALLOW_ALL, f"{base}/robots.txt", "text/plain"
        ),
        f"{base}/sitemap.xml": _missing(f"{base}/sitemap.xml"),
        root: _page(
            _body("2010년에 설립한 법인")
            + f'<a href="{injected}">입주자 주입</a>',
            root,
        ),
        injected: _page(_body("다른 회사 본문"), injected),
    }
    site = _FakeWideSite(pages)

    _collect(site, root_homepage_url=root, company_name="")

    assert injected not in site.calls


def test_등록_하위도메인도_최초_DART_query_scope를_우회하지_못한다():
    base = "https://company.example"
    injected = "https://recruit.company.example/about?tenant=BETA"
    pages = {
        f"{base}/robots.txt": _page(
            ROBOTS_ALLOW_ALL, f"{base}/robots.txt", "text/plain"
        ),
        f"{base}/sitemap.xml": _missing(f"{base}/sitemap.xml"),
        f"{base}/": _page(
            _body("2010년에 설립한 법인")
            + f'<a href="{injected}">하위호스트 tenant 주입</a>',
            f"{base}/",
        ),
        injected: _page(_body("다른 입주자 본문"), injected),
    }
    site = _FakeWideSite(pages)

    _collect(site, company_name="")

    assert injected not in site.calls
    assert not any("recruit.company.example" in url for url in site.calls)


@pytest.mark.parametrize("attack_query", ("page=%FF", "page=%FE", "page=A;sort=x"))
def test_invalid_query는_canonicalize_robots_transport보다_먼저_거절된다(
    monkeypatch,
    attack_query,
):
    base = "https://company.example"
    injected = f"https://recruit.company.example/about?{attack_query}"
    pages = {
        f"{base}/robots.txt": _page(
            ROBOTS_ALLOW_ALL, f"{base}/robots.txt", "text/plain"
        ),
        f"{base}/sitemap.xml": _missing(f"{base}/sitemap.xml"),
        f"{base}/": _page(
            _body("2010년에 설립한 법인")
            + f'<a href="{injected}">공격 링크</a>',
            f"{base}/",
        ),
    }
    site = _FakeWideSite(pages)
    real_canonicalize = wide_collect.canonicalize_url

    def guarded_canonicalize(url, **kwargs):
        assert url != injected, "query 검사 전에 canonicalize_url이 호출됐습니다"
        return real_canonicalize(url, **kwargs)

    monkeypatch.setattr(wide_collect, "canonicalize_url", guarded_canonicalize)

    _collect(site, company_name="")

    assert injected not in site.calls
    assert not any("recruit.company.example" in url for url in site.calls)


@pytest.mark.parametrize("attack_query", ("tenant=%FF", "tenant=%FE", "tenant=A;page=2"))
def test_DART_시작_URL의_invalid_query는_robots와_본문을_0회호출한다(attack_query):
    site = _FakeWideSite({})

    result = _collect(
        site,
        root_homepage_url=f"https://portal.example/view?{attack_query}",
        company_name="",
    )

    assert result.documents == ()
    assert result.attempts == ()
    assert site.calls == []


def test_ref가_다른_두_scope는_저장문서_ID와_scope_digest가_서로_다르다():
    base = "https://portal.example"

    def collect_ref(ref: str):
        root = f"{base}/view?ref={ref}"
        pages = {
            f"{base}/robots.txt": _page(
                ROBOTS_ALLOW_ALL, f"{base}/robots.txt", "text/plain"
            ),
            f"{base}/sitemap.xml": _missing(f"{base}/sitemap.xml"),
            root: _page(_body("2010년에 설립한 법인"), root),
        }
        result = _collect(
            _FakeWideSite(pages), root_homepage_url=root, company_name=""
        )
        return next(document for document in result.documents if "portal.example" in document.canonical_url)

    alpha = collect_ref("ALPHA")
    beta = collect_ref("BETA")

    assert alpha.canonical_url.endswith("?ref=ALPHA")
    assert beta.canonical_url.endswith("?ref=BETA")
    assert alpha.document_id != beta.document_id
    assert alpha.identity_binding != beta.identity_binding


def test_v2_산출은_버전이_ID에_봉인되어_v1_따뜻한캐시와_섞이지_않는다():
    base = "https://company.example"
    pages = {
        f"{base}/robots.txt": _page(
            ROBOTS_ALLOW_ALL, f"{base}/robots.txt", "text/plain"
        ),
        f"{base}/sitemap.xml": _missing(f"{base}/sitemap.xml"),
        f"{base}/": _page(_body("2010년에 설립한 법인"), f"{base}/"),
    }

    first = _collect(_FakeWideSite(pages), company_name="")
    second = _collect(_FakeWideSite(pages), company_name="")
    first_document = next(
        document for document in first.documents if document.canonical_url == f"{base}/"
    )
    second_document = next(
        document for document in second.documents if document.canonical_url == f"{base}/"
    )
    legacy_v1_id = hashlib.sha256(f"{base}/".encode("utf-8")).hexdigest()

    assert WIDE_COLLECTOR_VERSION == "homepage-wide-collector/2"
    assert WIDE_PARSER_VERSION == "homepage-wide-parser/2"
    assert first_document.collector_version == WIDE_COLLECTOR_VERSION
    assert first_document.parser_version == WIDE_PARSER_VERSION
    assert first_document.document_id != legacy_v1_id
    assert first_document.document_id == second_document.document_id




def test_같은_host라도_scheme_port_path가_바뀐_redirect는_차단된다():
    base = "https://company.example"
    pages = {
        f"{base}/robots.txt": _page(ROBOTS_ALLOW_ALL, f"{base}/robots.txt", "text/plain"),
        f"{base}/sitemap.xml": _missing(f"{base}/sitemap.xml"),
        f"{base}/tenant": _page(
            _body("대상 회사 본문") + '<a href="/tenant/redir">이동</a>',
            f"{base}/tenant",
        ),
        f"{base}/tenant/redir": _page(
            _body("경계 밖 본문"), "http://company.example:8080/other"
        ),
    }
    site = _FakeWideSite(pages)

    result = _collect(site, root_homepage_url=f"{base}/tenant", company_name="")

    assert f"{base}/tenant/redir" in site.calls
    assert all(document.canonical_url != "http://company.example:8080/other" for document in result.documents)


def test_IR_HTML도_DART_회사경로_밖은_delegate를_호출하지_않는다():
    from src.features.homepage.ir_pdf import FetchedIrHtml

    base = "https://sites.example.com"
    pages = {
        f"{base}/robots.txt": _page(ROBOTS_ALLOW_ALL, f"{base}/robots.txt", "text/plain"),
        f"{base}/sitemap.xml": _missing(f"{base}/sitemap.xml"),
        f"{base}/tenant": _page(_body("대상 회사 소개"), f"{base}/tenant"),
    }
    site = _FakeWideSite(pages)
    ir_calls: list[str] = []

    def ir_html(url, _expected_hostname, _url_allowed):
        ir_calls.append(url)
        if url.endswith("/robots.txt"):
            return FetchedIrHtml(ROBOTS_ALLOW_ALL, url)
        if url == f"{base}/tenant":
            return FetchedIrHtml(
                '<html><body><a href="/other/investors">IR 자료</a></body></html>',
                url,
            )
        raise AssertionError("회사 경로 밖 IR HTML은 delegate 전에 막혀야 합니다")

    result = _collect(
        site,
        root_homepage_url=f"{base}/tenant",
        ir_html_fetch=ir_html,
    )

    assert ir_calls == [f"{base}/robots.txt", f"{base}/tenant"]
    assert all("/other/" not in document.canonical_url for document in result.documents)


def test_IR_PDF_redirect도_DART_회사경로_밖을_허용하지_않는다():
    from src.features.homepage.ir_pdf import FetchedIrHtml, FetchedIrPdf

    base = "https://sites.example.com"
    pages = {
        f"{base}/robots.txt": _page(ROBOTS_ALLOW_ALL, f"{base}/robots.txt", "text/plain"),
        f"{base}/sitemap.xml": _missing(f"{base}/sitemap.xml"),
        f"{base}/tenant": _page(_body("대상 회사 소개"), f"{base}/tenant"),
    }
    site = _FakeWideSite(pages)
    pdf_calls: list[str] = []
    outside_url = f"{base}/other/report.pdf"

    def ir_html(url, _expected_hostname, _url_allowed):
        if url.endswith("/robots.txt"):
            return FetchedIrHtml(ROBOTS_ALLOW_ALL, url)
        return FetchedIrHtml(
            '<html><body><a href="/tenant/report.pdf">2025 IR 보고서</a></body></html>',
            url,
        )

    def ir_pdf(url, _expected_hostname, _max_bytes, url_allowed):
        pdf_calls.append(url)
        assert url_allowed(url)
        assert not url_allowed(outside_url)
        return FetchedIrPdf(b"not-used", outside_url, "application/pdf")

    result = _collect(
        site,
        root_homepage_url=f"{base}/tenant",
        ir_html_fetch=ir_html,
        ir_pdf_fetch=ir_pdf,
    )

    assert pdf_calls == [f"{base}/tenant/report.pdf"]
    assert all("/other/" not in document.canonical_url for document in result.documents)


def test_같은_등록도메인_하위host도_WIDE_MAX_HOSTS를_넘어_조회하지_않는다():
    links = "".join(
        f'<a href="https://h{index}.company.example/about">하위 {index}</a>'
        for index in range(WIDE_MAX_HOSTS + 4)
    )
    pages = {
        "https://company.example/robots.txt": _page(
            ROBOTS_ALLOW_ALL, "https://company.example/robots.txt", "text/plain"
        ),
        "https://company.example/sitemap.xml": _missing(
            "https://company.example/sitemap.xml"
        ),
        "https://company.example/": _page(_body("회사 소개") + links, "https://company.example/"),
        "https://www.company.example/robots.txt": _missing(
            "https://www.company.example/robots.txt"
        ),
        "https://www.company.example/sitemap.xml": _missing(
            "https://www.company.example/sitemap.xml"
        ),
        "https://www.company.example/": _missing("https://www.company.example/"),
    }
    for index in range(WIDE_MAX_HOSTS + 4):
        host = f"h{index}.company.example"
        pages[f"https://{host}/robots.txt"] = _page(
            ROBOTS_ALLOW_ALL, f"https://{host}/robots.txt", "text/plain"
        )
        pages[f"https://{host}/sitemap.xml"] = _missing(f"https://{host}/sitemap.xml")
        pages[f"https://{host}/about"] = _page(_body(f"하위 {index} 회사 소개"), f"https://{host}/about")
    site = _FakeWideSite(pages)

    _collect(site, company_name="")

    robots_hosts = {
        urllib.parse.urlsplit(url).hostname
        for url in site.calls
        if url.endswith("/robots.txt")
    }
    assert len(robots_hosts) == WIDE_MAX_HOSTS


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
    assert other_docs == []
    assert not any("company.net" in url for url in site.calls)


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


# ── APEX-WWW-OFFICIAL-ROOT-GAP(통합 담당 지시) ────────


def test_apex가_사실상_www로만_운영되어도_www가_직접_방문되어_문서를_만든다():
    """DART가 apex(company.example)를 줬지만 실제 운영은 www.company.example
    뿐이면(apex 쪽은 접속 자체가 실패), redirect를 따라가는 대신 www를
    독립 후보로 직접 방문해 문서를 만들어야 한다 — 예전엔 apex 첫 페이지
    자체가 막혀 수집이 0건이었다."""
    pages = {
        # apex(company.example) 쪽은 robots.txt조차 pages에 없어 접속 자체가 실패한다.
        "https://www.company.example/robots.txt": _page(
            ROBOTS_ALLOW_ALL, "https://www.company.example/robots.txt", "text/plain"
        ),
        "https://www.company.example/sitemap.xml": _missing("https://www.company.example/sitemap.xml"),
        "https://www.company.example/": _page(_body("www 루트 페이지 본문"), "https://www.company.example/"),
    }
    site = _FakeWideSite(pages)

    result = _collect(site, root_homepage_url="company.example")

    assert result.documents  # 실제로 문서가 만들어졌는지 확인(공허한 통과 방지)
    www_doc = next(doc for doc in result.documents if doc.canonical_url == "https://www.company.example/")
    assert www_doc.requirement == "REQUIRED"


def test_www가_사실상_apex로만_운영되어도_apex가_직접_방문되어_문서를_만든다():
    """반대 방향 — DART가 www.company.example을 줬지만 실제 운영은
    apex(company.example)뿐이다."""
    pages = {
        "https://company.example/robots.txt": _page(ROBOTS_ALLOW_ALL, "https://company.example/robots.txt", "text/plain"),
        "https://company.example/sitemap.xml": _missing("https://company.example/sitemap.xml"),
        "https://company.example/": _page(_body("apex 루트 페이지 본문"), "https://company.example/"),
        # www 쪽은 robots.txt조차 pages에 없어 접속 자체가 실패한다.
    }
    site = _FakeWideSite(pages)

    result = _collect(site, root_homepage_url="www.company.example")

    assert result.documents
    apex_doc = next(doc for doc in result.documents if doc.canonical_url == "https://company.example/")
    assert apex_doc.requirement == "REQUIRED"


def test_apex_www_짝중_하나만_robots가_거부해도_다른_하나는_독립적으로_수집된다():
    """apex는 정상, www 짝은 robots가 거부(403) — www만 차단되고 apex는
    영향받지 않아야 한다(하나가 막혀도 다른 하나는 독립적으로 진행된다)."""
    pages = {
        "https://company.example/robots.txt": _page(ROBOTS_ALLOW_ALL, "https://company.example/robots.txt", "text/plain"),
        "https://company.example/sitemap.xml": _missing("https://company.example/sitemap.xml"),
        "https://company.example/": _page(_body("apex 루트 페이지 본문"), "https://company.example/"),
        "https://www.company.example/robots.txt": WideRawResponse(
            status=403, text="", effective_url="https://www.company.example/robots.txt", content_type=""
        ),
    }
    site = _FakeWideSite(pages)

    result = _collect(site)

    assert any(doc.canonical_url == "https://company.example/" for doc in result.documents)
    assert not any("www.company.example" in doc.canonical_url for doc in result.documents)
    assert "https://www.company.example/" not in site.calls


def test_apex에서_다른_등록도메인으로의_redirect는_apex_www_짝이_있어도_차단된다():
    """apex/www 짝 결속이 함께 있어도, 페이지 안에서 아예 다른 등록
    도메인으로 redirect되면 여전히 차단돼야 한다 — 앞서 고친 eTLD+1
    결함 수정이 이 기능으로 되돌아가면 안 된다."""
    pages = {
        "https://company.example/robots.txt": _page(ROBOTS_ALLOW_ALL, "https://company.example/robots.txt", "text/plain"),
        "https://company.example/sitemap.xml": _missing("https://company.example/sitemap.xml"),
        "https://company.example/": _page(
            _body("루트 페이지 본문") + '<a href="/redir">이동</a>', "https://company.example/"
        ),
        "https://company.example/redir": _page(_body("가짜 본문"), "https://evil.com/"),
        "https://www.company.example/robots.txt": _missing("https://www.company.example/robots.txt"),
    }
    site = _FakeWideSite(pages)

    result = _collect(site)

    assert not any("evil.com" in doc.canonical_url for doc in result.documents)
    assert "https://evil.com/" not in site.calls


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
        "https://company.example/careers": _page(
            _body("핵심가치와 일하는 방식"), "https://company.example/careers"
        ),
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


# ── 정정 1 최종판(P0, 결합 종단시험 실측): 광역 slot 주장은 상태와
#    무관하게 절대 REQUIRED가 될 수 없다 ─────────────────────────


def _assert_no_broad_required_slot_claim(attempts) -> None:
    """불변식(최종판): slot_ids가 허용 어휘 17개 전체(광역 fallback)인 attempt는
    상태(OK/MISSING/FAILED/TRUNCATED)와 무관하게 requirement가 반드시
    OPTIONAL이어야 한다 — REQUIRED는 0건이어야 한다.

    처음엔 「FAILED·TRUNCATED면 REQUIRED가 정확하다」로 정정했으나, 그건
    그 경로가 그 slot의 유일한 확인 경로일 때만 참이다. 웹 수집기는 17개
    slot 전부의 유일한 경로가 아니다(공시 문서 수집·페이지 유형이 좁힌
    경로가 따로 있다) — REQUIRED+광역으로 나가면 attempt 하나(예: IR PDF
    조회 FAILED)의 실패가 다른 소스가 채운 근거까지 UNKNOWN으로 끌어내린다
    (결합 종단시험에서 실측한 P0: IR FAILED attempt 하나 때문에
    9개 장 중 8개가 UNKNOWN, 최종 게이트 STOP_TRANSIENT_FAILURE로 떨어짐).
    """
    all_slots = set(WIDE_REQUIRED_SLOT_IDS)
    for attempt in attempts:
        if set(attempt.slot_ids) == all_slots:
            assert attempt.requirement == "OPTIONAL", (
                f"광역 REQUIRED 주장 위반: attempt_id={attempt.attempt_id} "
                f"source_kind={attempt.source_kind} state={attempt.state} "
                f"requirement={attempt.requirement}"
            )


def test_불변식_광역slot은_REQUIRED가_0건이다_정상수집():
    """가장 흔한 정상 수집 경로(robots ok, sitemap 없음, 루트 페이지 성공)만
    돌려도 위반이 없어야 한다 — 이게 바로 이 불변식이 막으려는 실사용 경로다."""
    pages = {
        "https://company.example/robots.txt": _page(ROBOTS_ALLOW_ALL, "https://company.example/robots.txt", "text/plain"),
        "https://company.example/sitemap.xml": _missing("https://company.example/sitemap.xml"),
        "https://company.example/": _page(_body("루트 페이지 본문"), "https://company.example/"),
    }
    site = _FakeWideSite(pages)
    result = _collect(site)
    assert result.attempts
    _assert_no_broad_required_slot_claim(result.attempts)


def test_불변식_광역slot은_REQUIRED가_0건이다_robots차단():
    """robots 조회 자체가 실패(FAILED)해도 광역 attempt는 REQUIRED가 아니다
    — 이 사례가 바로 결합 종단시험에서 실측한 P0(IR FAILED)와
    같은 원인이다."""
    site = _FakeWideSite({"https://company.example/": _page(_body("루트 페이지"), "https://company.example/")})
    result = _collect(site)
    assert result.attempts
    _assert_no_broad_required_slot_claim(result.attempts)


def test_불변식_광역slot은_REQUIRED가_0건이다_페이지수상한():
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
    _assert_no_broad_required_slot_claim(result.attempts)


def test_불변식_광역slot은_REQUIRED가_0건이다_sitemap_상한():
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
    _assert_no_broad_required_slot_claim(result.attempts)


def test_불변식_광역slot은_REQUIRED가_0건이다_ir_none_failed(monkeypatch):
    """★ 결합 종단시험에서 실측한 P0를 웹 수집기 단위에서 직접
    재현·고정한다 — IR PDF 조회 FAILED가 REQUIRED+광역으로 나가면 안 된다."""
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
    _assert_no_broad_required_slot_claim(result.attempts)


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


def test_robots_차단도_OPTIONAL이다():
    """★ 최종판(정정 1 재정정): robots는 17개 slot 전부의 유일한 확인
    경로가 아니므로, 실패(FAILED)했다고 REQUIRED로 올리면 안 된다 — 공시
    문서 수집·페이지 유형이 좁힌 경로가 이미 그 slot들을 따로 확인한다.
    실패 사실 자체는 reason_code(robots_unreachable)로 그대로 남는다."""
    site = _FakeWideSite({"https://company.example/": _page(_body("루트 페이지"), "https://company.example/")})
    result = _collect(site)
    robots_attempt = next(a for a in result.attempts if a.source_kind == "robots_txt")
    assert robots_attempt.state == "FAILED"
    assert robots_attempt.requirement == "OPTIONAL"


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


def test_유형_미상_페이지_실패도_OPTIONAL이다():
    """★ 최종판(정정 1 재정정): 페이지 fetch 자체가 실패(FAILED)해도, URL로
    유형을 못 알아낸 페이지는 여전히 OPTIONAL이어야 한다 — 예전엔 FAILED만
    예외로 REQUIRED를 유지했지만, 이 attempt는 애초에 어떤 slot의 유일한
    확인 경로도 아니었으므로 실패했다고 REQUIRED로 올라가면 안 된다."""
    pages = {
        "https://company.example/robots.txt": _page(ROBOTS_ALLOW_ALL, "https://company.example/robots.txt", "text/plain"),
        "https://company.example/sitemap.xml": _missing("https://company.example/sitemap.xml"),
        "https://company.example/": _page(
            _body("루트 페이지 본문") + '<a href="/xyz-unrelated">기타</a>', "https://company.example/"
        ),
        # "/xyz-unrelated"는 일부러 pages에 없어 접속 자체가 실패(FAILED)한다.
    }
    site = _FakeWideSite(pages)
    result = _collect(site)
    unmatched_attempt = next(
        a for a in result.attempts if a.state == "FAILED" and a.source_kind == "official_web_page"
    )
    assert set(unmatched_attempt.slot_ids) == set(WIDE_REQUIRED_SLOT_IDS)
    assert unmatched_attempt.requirement == "OPTIONAL"


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


def test_ir_failed도_OPTIONAL이다(monkeypatch):
    """★ 결합 종단시험에서 실측한 P0의 원인 그 자체 — IR PDF 조회가
    FAILED(일시 장애)로 실패했다고 REQUIRED+광역으로 나가면, 공시·페이지
    유형이 채운 다른 근거까지 소비 계약에서 UNKNOWN으로 끌려 내려간다.
    IR은 그 17개 slot 전부의 유일한 확인 경로가 아니므로 OPTIONAL이 맞다
    — 실패 사실은 reason_code(ir_pdf_failed)로 그대로 남아 진단에 쓰인다."""
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
    assert ir_attempt.reason_code == "ir_pdf_failed"
    assert ir_attempt.requirement == "OPTIONAL"


def test_blocked_호스트는_IR_시도_0회(monkeypatch):
    """웹 크롤 단계가 이미 이 host의 robots.txt를 확인 못했거나
    거부됐다고 판정했으면(``state.robots_policies[host].blocked``), IR PDF는
    같은 host를 다시 확인하지 않는다 — html_fetch/pdf_fetch가 그 host로 단
    한 번도 불리지 않는다. robots.txt 자체가 pages에 없어(가짜 접속 실패)
    두 후보 host(primary·www 별칭) 모두 웹 크롤 단계에서 blocked가 된다."""
    from src.features.homepage.ir_pdf import OfficialIrFetchError
    from src.shared.report_evidence.constants import SOURCE_KIND_ROBOTS_TXT

    ir_calls: list[str] = []

    def counting_ir_html(url: str, *_args, **_kwargs):
        ir_calls.append(url)
        raise OfficialIrFetchError("이 시험에서는 절대 불리면 안 됩니다")

    def counting_ir_pdf(*_args, **_kwargs):
        ir_calls.append("pdf")
        raise OfficialIrFetchError("이 시험에서는 절대 불리면 안 됩니다")

    site = _FakeWideSite({})  # robots.txt조차 없다 — 웹 크롤 robots 조회가 blocked로 끝난다
    result = _collect(site, ir_html_fetch=counting_ir_html, ir_pdf_fetch=counting_ir_pdf)

    assert ir_calls == []
    assert not [a for a in result.attempts if a.source_kind == "official_ir_pdf"]
    robots_attempts = [a for a in result.attempts if a.source_kind == SOURCE_KIND_ROBOTS_TXT]
    assert robots_attempts  # robots 판정 자체는 웹 크롤 단계 attempt로 남아 있다
    assert all(a.state == "FAILED" for a in robots_attempts)


# ── IR PDF 위임 ───────────────────────────────────────────


def test_공식_HTML_exact_외부_IR첨부는_낮은신뢰_provenance만_남기고_슬롯을_못채운다(
    monkeypatch,
):
    from src.shared.official_ir import IR_ATTACHMENT_URL_FIELD

    attachment_url = "https://cdn.vendor.example/reports/alpha-2026.pdf"

    def fake_collect_ir(homepage_url, **_kwargs):
        if homepage_url != "https://company.example/":
            return OfficialIrCollectResult(
                state="none", fragments=[], downloaded_pdf_bytes=0
            )
        return OfficialIrCollectResult(
            state="ok",
            fragments=[
                {
                    "종류": "공식 IR",
                    "원문": "주요 고객사에 서비스를 제공하고 구독료로 수익을 얻습니다.",
                    "출처": "https://company.example/ir/detail/1",
                    IR_ATTACHMENT_URL_FIELD: attachment_url,
                    "문서ID": "external-ir-1",
                    "문서명": "2026년 IR 자료",
                }
            ],
            downloaded_pdf_bytes=100,
        )

    monkeypatch.setattr(wide_collect, "collect_official_ir_fragments", fake_collect_ir)
    pages = {
        "https://company.example/robots.txt": _page(
            ROBOTS_ALLOW_ALL, "https://company.example/robots.txt", "text/plain"
        ),
        "https://company.example/sitemap.xml": _missing(
            "https://company.example/sitemap.xml"
        ),
        "https://company.example/": _page(
            _body("2010년에 설립한 법인"), "https://company.example/"
        ),
    }
    result = _collect(_FakeWideSite(pages))

    external_doc = next(
        document for document in result.documents
        if document.canonical_url == attachment_url
    )
    assert external_doc.requirement == "OPTIONAL"
    assert external_doc.source_tier == "TIER_3_TRUSTED"
    assert attachment_url in external_doc.identity_binding
    assert build_fragments(external_doc, company_id="c1") == ()


def test_외부_IR첨부_fetch는_발견된_exact_URL_한건만_허용하고_redirect를_거절한다():
    from src.features.homepage.ir_pdf import FetchedIrPdf, OfficialIrFetchError
    from src.features.homepage.wide_domain import parse_official_origin

    attachment_url = "https://cdn.vendor.example/reports/alpha.pdf"
    other_url = "https://cdn.vendor.example/reports/other.pdf"
    origin = parse_official_origin("https://company.example/")
    assert origin is not None

    def exact_delegate(url, expected_hostname, max_bytes, url_allowed):
        assert url == attachment_url
        assert expected_hostname == "cdn.vendor.example"
        assert max_bytes == 1024
        assert url_allowed(attachment_url)
        assert not url_allowed(other_url)
        return FetchedIrPdf(b"pdf", attachment_url, "application/pdf")

    checked = wide_collect._origin_checked_ir_pdf_fetch(origin, exact_delegate)
    fetched = checked(
        attachment_url,
        "cdn.vendor.example",
        1024,
        lambda value: value == attachment_url,
    )
    assert fetched.effective_url == attachment_url

    def redirect_delegate(url, expected_hostname, max_bytes, url_allowed):
        return FetchedIrPdf(b"pdf", other_url, "application/pdf")

    redirect_checked = wide_collect._origin_checked_ir_pdf_fetch(
        origin, redirect_delegate
    )
    with pytest.raises(OfficialIrFetchError, match="exact URL"):
        redirect_checked(
            attachment_url,
            "cdn.vendor.example",
            1024,
            lambda value: value == attachment_url,
        )


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
    """apex/www 짝(APEX-WWW-OFFICIAL-ROOT-GAP) 덕분에 IR 후보 호스트가
    company.example·www.company.example 둘이 된다 — 정확히 일치하는
    호스트만 «none」, 나머지는 「failed」로 갈라 두 상태가 실제로 각각
    다른 attempt에 남는지 확인한다."""
    def fake_collect_ir(homepage_url, **_kwargs):
        if homepage_url == "https://company.example/":
            return OfficialIrCollectResult(state="none", fragments=[], downloaded_pdf_bytes=0)
        return OfficialIrCollectResult(state="failed", fragments=[], downloaded_pdf_bytes=0)

    monkeypatch.setattr(wide_collect, "collect_official_ir_fragments", fake_collect_ir)

    pages = {
        "https://company.example/robots.txt": _page(ROBOTS_ALLOW_ALL, "https://company.example/robots.txt", "text/plain"),
        "https://company.example/sitemap.xml": _missing("https://company.example/sitemap.xml"),
        "https://company.example/": _page(_body("루트 페이지 본문"), "https://company.example/"),
        "https://www.company.example/robots.txt": _page(
            ROBOTS_ALLOW_ALL, "https://www.company.example/robots.txt", "text/plain"
        ),
        "https://www.company.example/sitemap.xml": _missing("https://www.company.example/sitemap.xml"),
        "https://www.company.example/": _missing("https://www.company.example/"),
    }
    site = _FakeWideSite(pages)

    result = _collect(site)

    ir_attempts = [a for a in result.attempts if a.source_kind == "official_ir_pdf"]
    # candidate_hosts는 root(primary)를 항상 먼저 두고 나머지는 알파벳순으로
    # 정렬한다(_run_ir_pdf_phase) — company.example이 [0], www...가 [1]이다.
    assert len(ir_attempts) == 2
    assert ir_attempts[0].state == "MISSING"
    assert ir_attempts[1].state == "FAILED"


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
        "https://company.example/careers": _page(
            _body("핵심가치와 일하는 방식"), "https://company.example/careers"
        ),
    }
    site = _FakeWideSite(pages)

    result = _collect(site, company_id=target_company_id)
    assert result.documents  # 실제로 문서가 만들어졌는지 확인(공허한 통과 방지)

    # 향후 결합부 권장 패턴 — 문서마다 company_id를 손으로
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

    mapped = to_evidence_mappings(result=result, fragments=fragments)

    assert mapped["company_id"] == target_company_id
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
