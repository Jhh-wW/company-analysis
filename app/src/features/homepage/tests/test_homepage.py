"""홈페이지 수집 시험 — 가짜 `fetch`만 쓴다. 실제 접속은 하지 않는다.

정본: 확정/03_수집/2_규칙/01_소스정책.md · 확정/03_수집/1_흐름/02_실패처리.md
"""

from __future__ import annotations

import pytest

from src.features.homepage import safe_http
from src.features.homepage.constants import (
    MAX_CHARS_PER_PAGE,
    MAX_PAGES,
    MAX_TOTAL_CHARS,
)
from src.features.homepage.logic import (
    FetchedPage,
    HomepageCertNameMismatchError,
    HomepageFetchError,
    HomepageRobotsUnavailable,
    HomepageRobotsUnreachable,
    HomepageSecurityPolicyError,
    collect_homepage_fragments,
    strip_html,
)

ROOT = "http://example.com"


def _fake_fetch(pages: dict[str, str], fail: frozenset[str] = frozenset()) -> tuple:
    """가짜 접속 함수를 만든다.

    Args:
        pages: 주소 → HTML(또는 robots.txt 글자) 매핑. 없는 주소는 접속 실패로 본다.
        fail: 등록돼 있어도 일부러 실패시킬 주소 집합.

    Returns:
        (fetch 함수, 호출된 주소를 순서대로 담는 리스트). 리스트로 몇 번 불렸는지 확인한다.
    """
    calls: list[str] = []

    def fetch(url: str) -> str:
        calls.append(url)
        if url.endswith("/robots.txt") and url not in pages:
            raise HomepageRobotsUnavailable(f"가짜 robots.txt 없음: {url}")
        if url in fail or url not in pages:
            raise HomepageFetchError(f"가짜 접속 실패: {url}")
        return pages[url]

    return fetch, calls


# ── 정상 수집 ────────────────────────────────────────────


def test_정상_수집이면_ok와_조각을_돌려준다():
    pages = {
        ROOT: '<html><body><h1>회사소개</h1><p>' + ("우리는 좋은 회사입니다. " * 10) + '</p>'
        '<a href="/about">회사소개</a></body></html>',
        f"{ROOT}/about": (
            "<html><body><p>" + ("기술 중심 회사입니다. " * 10) + "</p></body></html>"
        ),
    }
    fetch, _calls = _fake_fetch(pages)

    result = collect_homepage_fragments(ROOT, fetch=fetch)

    assert result.state == "ok"
    assert len(result.fragments) == 2
    for frag in result.fragments:
        assert frag["종류"] == "홈페이지"
        assert frag["원문"]
        assert frag["출처"].startswith(ROOT)


def test_검증된_HTTPS_소형_사이트를_끝까지_읽으면_후보범위가_완전하다():
    root = "https://example.com"
    pages = {
        root: "<html><body><p>" + ("회사 소개 문단입니다. " * 10) + "</p></body></html>",
        f"{root}/robots.txt": "User-agent: *\nAllow: /\n",
    }

    def fetch(url: str) -> FetchedPage:
        if url not in pages:
            raise HomepageFetchError(f"가짜 접속 실패: {url}")
        return FetchedPage(html=pages[url], effective_url=url)

    result = collect_homepage_fragments(root, fetch=fetch)

    assert result.state == "ok"
    assert result.candidate_scope_complete is True


def test_machine_readable_단일_발행일을_홈페이지조각에_보존한다():
    root = "https://example.com/newsroom/competition"
    pages = {
        root: (
            '<meta property="article:published_time" '
            'content="2026-08-01T09:00:00+09:00">'
            "<p>" + ("당사의 경쟁사는 베타입니다. " * 8) + "</p>"
        ),
        "https://example.com/robots.txt": "User-agent: *\nAllow: /\n",
    }

    def fetch(url: str) -> FetchedPage:
        if url not in pages:
            raise HomepageFetchError(f"가짜 접속 실패: {url}")
        return FetchedPage(html=pages[url], effective_url=url)

    result = collect_homepage_fragments(root, fetch=fetch)

    assert result.state == "ok"
    assert result.fragments[0]["문서일"] == "2026-08-01"


def test_서로충돌하는_발행일은_홈페이지조각에_쓰지않는다():
    raw = (
        '<meta property="article:published_time" content="2026-08-01">'
        '<meta itemprop="datePublished" content="2026-08-02">'
        "<p>" + ("회사 공식 소개 문장입니다. " * 8) + "</p>"
    )
    fragments: list[dict[str, str]] = []

    from src.features.homepage import logic as homepage_logic

    homepage_logic._collect_page(
        "https://example.com/newsroom/conflict",
        raw,
        fragments,
        set(),
        0,
        final_url_verified=True,
    )

    assert fragments and "문서일" not in fragments[0]


def test_홈페이지_모든_fetch는_수집전체_deadline과_DNS_cache를_공유한다():
    root = "https://example.com"
    pages = {
        root: (
            "<html><body><p>" + ("회사 소개 문단입니다. " * 10) + "</p>"
            '<a href="/about">회사소개</a></body></html>'
        ),
        f"{root}/about": "<p>" + ("기술 소개 문단입니다. " * 10) + "</p>",
        f"{root}/robots.txt": "User-agent: *\nAllow: /\n",
    }
    budget_ids: list[int] = []

    def fetch(url: str) -> FetchedPage:
        budget = safe_http._ACTIVE_DEADLINE.get()
        assert budget is not None
        budget_ids.append(id(budget))
        return FetchedPage(html=pages[url], effective_url=url)

    result = collect_homepage_fragments(root, fetch=fetch)

    assert result.state == "ok"
    assert len(budget_ids) >= 3
    assert len(set(budget_ids)) == 1


def test_스킴이_없는_주소도_받는다():
    pages = {ROOT: "<html><body><p>" + ("소개 문단입니다. " * 10) + "</p></body></html>"}
    fetch, _calls = _fake_fetch(pages)

    result = collect_homepage_fragments("example.com", fetch=fetch)

    assert result.state == "ok"


def test_홈페이지_주소가_비어있으면_none():
    fetch, calls = _fake_fetch({})

    result = collect_homepage_fragments("", fetch=fetch)

    assert result.state == "none"
    assert calls == []  # 주소가 없으면 접속 자체를 시도하지 않는다


# ── 접속 실패 (⚠️ 못 가져옴) ─────────────────────────────


def test_루트_접속_실패는_failed로_돌아온다():
    fetch, _calls = _fake_fetch({}, fail=frozenset({ROOT}))

    result = collect_homepage_fragments(ROOT, fetch=fetch)

    assert result.state == "failed"
    assert result.fragments == []
    assert "접속 실패" in result.detail
    assert result.candidate_scope_complete is False


def test_DART_apex의_실제_www이동만_재수집하고_조각에_검증표식을_붙인다():
    apex = "https://jype.com"
    alias = "https://www.jype.com/"
    pages = {
        f"{alias}robots.txt": FetchedPage(
            html="User-agent: *\nAllow: /\n",
            effective_url=f"{alias}robots.txt",
        ),
        alias: FetchedPage(
            html=(
                "<html><body><p>"
                + ("JYP 공식 회사소개입니다. " * 10)
                + "</p></body></html>"
            ),
            effective_url=alias,
        ),
    }
    calls: list[str] = []
    probe_calls: list[tuple[str, str]] = []

    def fetch(url: str) -> str | FetchedPage:
        calls.append(url)
        if url == f"{apex}/robots.txt":
            raise HomepageRobotsUnavailable("apex robots 없음")
        if url == apex:
            raise HomepageSecurityPolicyError("apex 본문은 www로 이동")
        if url in pages:
            return pages[url]
        raise AssertionError(f"예상하지 않은 주소: {url}")

    def probe(apex_url: str, alias_url: str) -> str:
        probe_calls.append((apex_url, alias_url))
        return alias_url

    result = collect_homepage_fragments(
        "jype.com",
        fetch=fetch,
        allow_dart_www_alias=True,
        www_redirect_probe=probe,
    )

    assert result.state == "ok"
    assert probe_calls == [("jype.com", alias)]
    assert calls.count(alias) == 1
    assert "검증된 www 별칭" in result.detail
    assert result.fragments[0]["후보출처검증"] == "https_exact_dart_host"
    assert result.fragments[0]["DARTwww리다이렉트검증"] == "https_apex_to_www_redirect"
    assert result.fragments[0]["DARTwww원본host"] == "jype.com"
    assert result.fragments[0]["DARTwww최종host"] == "www.jype.com"

    from src.features.company_comparison.official_sources import (
        bind_dart_profile_attestation,
    )

    bound = bind_dart_profile_attestation(
        {1: result.fragments[0]},
        profile={
            "status": "000",
            "corp_code": "00258689",
            "corp_name": "JYP Ent.",
            "hm_url": "jype.com",
        },
        corp_code="00258689",
        company_name="JYP Ent.",
        collected_on="2026-08-24",
    )
    assert bound.fragments[1]["도메인근거SourceID"] == (
        "dart-company-profile-00258689"
    )


@pytest.mark.parametrize(
    "probe_result",
    ("", "https://ir.jype.com/", "https://www.attacker.example/"),
)
def test_검증표식이_없거나_임의_subdomain이면_www를_재수집하지_않는다(
    probe_result: str,
):
    apex = "https://jype.com"
    calls: list[str] = []

    def fetch(url: str) -> str:
        calls.append(url)
        if url == f"{apex}/robots.txt":
            raise HomepageRobotsUnavailable("apex robots 없음")
        if url == apex:
            raise HomepageSecurityPolicyError("apex 본문 실패")
        raise AssertionError(f"검증되지 않은 별칭을 요청하면 안 됩니다: {url}")

    result = collect_homepage_fragments(
        "jype.com",
        fetch=fetch,
        allow_dart_www_alias=True,
        www_redirect_probe=lambda _apex, _alias: probe_result,
    )

    assert result.state == "failed"
    assert all("www.jype.com" not in url for url in calls)
    assert result.fragments == []


def test_낱장_페이지_실패는_전체_실패로_보지_않는다():
    """루트는 됐는데 하위 페이지 하나가 실패해도 루트 조각은 살아남는다."""
    pages = {
        ROOT: "<html><body><p>" + ("루트 소개 문단입니다. " * 10) + "</p>"
        '<a href="/about">회사소개</a></body></html>',
    }
    fetch, _calls = _fake_fetch(pages, fail=frozenset({f"{ROOT}/about"}))

    result = collect_homepage_fragments(ROOT, fetch=fetch)

    assert result.state == "ok"
    assert len(result.fragments) == 1
    assert result.candidate_scope_complete is False


# ── robots.txt 금지 ──────────────────────────────────────


def test_robots_금지_경로는_건너뛴다():
    pages = {
        ROOT: "<html><body><p>" + ("루트 소개 문단입니다. " * 10) + "</p>"
        '<a href="/about">회사소개</a></body></html>',
        f"{ROOT}/robots.txt": "User-agent: *\nDisallow: /about\n",
        f"{ROOT}/about": (
            "<html><body><p>" + ("금지된 페이지입니다. " * 10) + "</p></body></html>"
        ),
    }
    fetch, calls = _fake_fetch(pages)

    result = collect_homepage_fragments(ROOT, fetch=fetch)

    assert result.state == "ok"
    assert all("/about" not in frag["출처"] for frag in result.fragments)
    assert f"{ROOT}/about" not in calls  # 금지된 페이지는 아예 요청하지 않는다


def test_robots_4xx로_명시적_부재면_빈_규칙으로_계속한다():
    """HTTP 4xx로 robots 부재가 확인된 경우에만 빈 규칙으로 진행한다."""
    pages = {
        ROOT: "<html><body><p>" + ("루트 소개 문단입니다. " * 10) + "</p></body></html>",
    }
    fetch, _calls = _fake_fetch(pages)  # robots.txt 라우트를 아예 등록하지 않음 = 접속 실패

    result = collect_homepage_fragments(ROOT, fetch=fetch)

    assert result.state == "ok"


def test_robots를_본문보다_먼저_확인한다():
    pages = {
        ROOT: "<html><body><p>" + ("회사 소개 문단입니다. " * 10) + "</p></body></html>",
        f"{ROOT}/robots.txt": "User-agent: *\nAllow: /\n",
    }
    fetch, calls = _fake_fetch(pages)

    result = collect_homepage_fragments(ROOT, fetch=fetch)

    assert result.state == "ok"
    assert calls[:2] == [f"{ROOT}/robots.txt", ROOT]


def test_robots_서버나_네트워크_장애면_본문을_요청하지_않는다():
    calls: list[str] = []

    def fetch(url: str) -> str:
        calls.append(url)
        if url.endswith("/robots.txt"):
            raise HomepageRobotsUnreachable("HTTP 503")
        raise AssertionError("robots 확인 실패 뒤 본문을 요청하면 안 됩니다")

    result = collect_homepage_fragments(ROOT, fetch=fetch)

    assert result.state == "failed"
    assert result.candidate_scope_complete is False
    assert calls == [f"{ROOT}/robots.txt"]


def test_robots가_루트를_막으면_본문을_요청하지_않는다():
    pages = {
        f"{ROOT}/robots.txt": "User-agent: *\nDisallow: /\n",
        ROOT: "<html><body>읽으면 안 되는 본문</body></html>",
    }
    fetch, calls = _fake_fetch(pages)

    result = collect_homepage_fragments(ROOT, fetch=fetch)

    assert result.state == "failed"
    assert result.candidate_scope_complete is False
    assert calls == [f"{ROOT}/robots.txt"]


def test_robots가_리다이렉트_도착_경로를_막으면_본문으로_승격하지_않는다():
    root = "https://example.com"
    calls: list[str] = []

    def fetch(url: str) -> str | FetchedPage:
        calls.append(url)
        if url.endswith("/robots.txt"):
            return "User-agent: *\nDisallow: /private\n"
        if url == root:
            return FetchedPage(
                html="<html><body>차단 경로 본문</body></html>",
                effective_url=f"{root}/private",
            )
        raise AssertionError(f"예상하지 않은 주소: {url}")

    result = collect_homepage_fragments(root, fetch=fetch)

    assert result.state == "failed"
    assert result.fragments == []
    assert result.candidate_scope_complete is False


def test_하위링크도_허용경로에서_금지경로로_이동하면_본문을_버린다():
    root = "https://example.com"
    about = f"{root}/about"
    pages = {
        f"{root}/robots.txt": "User-agent: *\nDisallow: /private\n",
        root: FetchedPage(
            html=(
                "<html><body><p>"
                + ("공식 루트 소개 문단입니다. " * 10)
                + '</p><a href="/about">회사 소개</a></body></html>'
            ),
            effective_url=root,
        ),
        about: FetchedPage(
            html="<html><body>금지된 도착 경로의 본문</body></html>",
            effective_url=f"{root}/private",
        ),
    }
    calls: list[str] = []

    def fetch(url: str) -> str | FetchedPage:
        calls.append(url)
        value = pages.get(url)
        if value is None:
            raise AssertionError(f"예상하지 않은 주소: {url}")
        return value

    result = collect_homepage_fragments(root, fetch=fetch)

    assert result.state == "ok"
    assert len(result.fragments) == 1
    assert all("private" not in item["출처"] for item in result.fragments)
    assert result.candidate_scope_complete is False
    assert calls == [f"{root}/robots.txt", root, about]


# ── 상한 ─────────────────────────────────────────────────


def test_최대_페이지_수를_넘지_않는다():
    links = "".join(f'<a href="/page{i}">페이지{i}</a>' for i in range(20))
    pages = {ROOT: f"<html><body><p>루트</p>{links}</body></html>"}
    for i in range(20):
        pages[f"{ROOT}/page{i}"] = (
            "<html><body><p>" + (f"페이지{i} 내용입니다. " * 10) + "</p></body></html>"
        )
    fetch, calls = _fake_fetch(pages)

    result = collect_homepage_fragments(ROOT, fetch=fetch)

    page_calls = [c for c in calls if not c.endswith("robots.txt")]
    assert len(page_calls) <= MAX_PAGES
    assert len(result.fragments) <= MAX_PAGES
    assert result.candidate_scope_complete is False


def test_IR자료실의_두번째_링크까지_따라가_최신_실적을_일반_IR페이지보다_먼저_읽는다():
    root = (
        "<html><body><p>홈.</p>"
        '<a href="/IR/Stock">주가</a>'
        '<a href="/ko/board/ir-data">IR 자료실</a>'
        "</body></html>"
    )
    board = (
        "<html><body><p>IR 자료 목록입니다.</p>"
        '<a href="/ko/board/ir-data/2026-q2">2026년 2분기 실적</a>'
        "</body></html>"
    )
    detail = "<html><body><p>" + ("2026년 2분기 매출과 영업이익 공식 자료입니다. " * 8) + "</p></body></html>"
    stock = "<html><body><p>" + ("주가 정보 페이지입니다. " * 8) + "</p></body></html>"
    pages = {
        ROOT: root,
        f"{ROOT}/ko/board/ir-data": board,
        f"{ROOT}/ko/board/ir-data/2026-q2": detail,
        f"{ROOT}/IR/Stock": stock,
    }
    fetch, calls = _fake_fetch(pages)

    result = collect_homepage_fragments(ROOT, fetch=fetch)

    assert result.state == "ok"
    detail_url = f"{ROOT}/ko/board/ir-data/2026-q2"
    assert detail_url in calls
    assert calls.index(detail_url) < calls.index(f"{ROOT}/IR/Stock")
    assert any(fragment["출처"] == detail_url for fragment in result.fragments)


def test_company_호스트와_링크_10개에서도_핵심_4쪽을_6쪽_안에_읽는다():
    """호스트의 company 글자와 IR 링크가 핵심 경로 순위를 오염하지 않는다."""
    root = "http://company.example"
    paths = (
        "aaa-random-1",
        "aaa-random-2",
        "IR/Stock",
        "IR/Financial",
        "products",
        "technology",
        "about",
        "business",
        "vision",
        "view?section=press",
    )
    links = "".join(f'<a href="/{path}">{path}</a>' for path in paths)
    pages = {root: f"<html><body><p>JYP 홈.</p>{links}</body></html>"}
    for path in paths:
        pages[f"{root}/{path}"] = (
            "<html><body><p>" + (f"{path} 공식 내용입니다. " * 10) + "</p></body></html>"
        )
    fetch, calls = _fake_fetch(pages)

    result = collect_homepage_fragments(root, fetch=fetch)

    page_calls = [call for call in calls if not call.endswith("robots.txt")]
    assert len(page_calls) == MAX_PAGES
    essential_urls = (
        f"{root}/about",
        f"{root}/business",
        f"{root}/vision",
        f"{root}/view?section=press",
    )
    assert all(url in page_calls for url in essential_urls)
    assert f"{root}/aaa-random-1" not in page_calls
    assert all(
        any(fragment["출처"] == url for fragment in result.fragments)
        for url in essential_urls
    )


def test_JYP_실제_메뉴에서는_회사소개와_IR핵심이_6쪽_안에_남는다():
    """브랜드 landing만 올리고 JYP 하위 메뉴가 예산을 독점하지 않는다."""
    root = "https://www.jype.com"
    paths = (
        "ko/JYP",
        "ko/JYP/History",
        "ko/JYP/Notice",
        "ko/JYP/Contact",
        "ko/Artist",
        "ko/Artist/Album",
        "ko/Artist/Video",
        "ko/Sustainability/ESGStrategy",
        "ko/Sustainability/ESGFactBook",
        "ko/Sustainability/ESGReporting",
        "ko/IR/Stock",
        "ko/IR/DividendStatus",
        "ko/IR/ShareholdersMeeting",
        "ko/IR/Financial",
        "ko/IR/Disclosure",
        "ko/board/ir-data",
        "ko/board/ir-news",
        "ko/IR/IRInquiry",
    )
    links = "".join(f'<a href="/{path}">{path}</a>' for path in paths)
    pages = {root: f"<html><body><p>JYP 홈.</p>{links}</body></html>"}
    for path in paths:
        pages[f"{root}/{path}"] = (
            "<html><body><p>" + (f"{path} 공식 내용입니다. " * 10) + "</p></body></html>"
        )
    fetch, calls = _fake_fetch(pages)

    result = collect_homepage_fragments(root, fetch=fetch)

    page_calls = [call for call in calls if not call.endswith("robots.txt")]
    assert len(page_calls) == MAX_PAGES
    assert f"{root}/ko/JYP" in page_calls
    assert f"{root}/ko/JYP/History" not in page_calls
    assert {
        f"{root}/ko/board/ir-data",
        f"{root}/ko/board/ir-news",
    } & set(page_calls)
    assert any(
        fragment["출처"] == f"{root}/ko/JYP" for fragment in result.fragments
    )


def test_전체_글자수_상한을_넘지_않는다():
    """페이지마다 MAX_CHARS_PER_PAGE에 가깝게 채워 실제로 상한에 걸리는지 본다."""

    def page_html(tag: str) -> str:
        return f"<html><body><p>{(tag + '내용 ') * 800}</p></body></html>"  # 3000자 훌쩍 넘음

    links = "".join(f'<a href="/p{i}">페이지{i}</a>' for i in range(5))
    pages = {ROOT: f"<html><body><p>{('루트내용 ') * 800}</p>{links}</body></html>"}
    for i in range(5):
        pages[f"{ROOT}/p{i}"] = page_html(f"고유{i}")
    fetch, _calls = _fake_fetch(pages)

    result = collect_homepage_fragments(ROOT, fetch=fetch)

    total = sum(len(f["원문"]) for f in result.fragments)
    assert total <= MAX_TOTAL_CHARS
    # 상한 «근처»까지는 채웠는지 — 그냥 우연히 안 넘긴 게 아니라 실제로 잘랐는지 확인
    assert total > MAX_TOTAL_CHARS - MAX_CHARS_PER_PAGE
    assert result.candidate_scope_complete is False


# ── 빈 페이지 ─────────────────────────────────────────────


def test_빈_페이지면_none으로_돌아온다():
    pages = {ROOT: "<html><body><script>var x = 1;</script></body></html>"}
    fetch, _calls = _fake_fetch(pages)

    result = collect_homepage_fragments(ROOT, fetch=fetch)

    assert result.state == "none"
    assert result.fragments == []


# ── HTML 태그 제거 ────────────────────────────────────────


def test_HTML_태그와_스크립트를_제거한다():
    raw = (
        "<html><head><style>body{color:red}</style></head>"
        "<body><h1>제목</h1><p>본문 &amp; 내용</p>"
        "<script>alert('x')</script></body></html>"
    )

    text = strip_html(raw)

    assert "<" not in text
    assert "script" not in text.lower()
    assert "color:red" not in text
    assert "제목" in text
    assert "본문 & 내용" in text


# ── 중복 제거 ─────────────────────────────────────────────


def test_같은_내용은_중복_제거한다():
    """서로 다른 두 주소가 완전히 같은 본문을 돌려주면 조각은 하나만 남는다."""
    mirrored = "<html><body><p>" + ("완전히 같은 문단입니다. " * 10) + "</p></body></html>"
    pages = {
        # 루트 자체 문구는 MIN_FRAGMENT_CHARS보다 짧게 둬 루트가 조각으로 안 잡히게 한다
        ROOT: '<html><body><p>루트.</p>'
        '<a href="/page-a">A</a><a href="/page-b">B</a></body></html>',
        f"{ROOT}/page-a": mirrored,
        f"{ROOT}/page-b": mirrored,
    }
    fetch, _calls = _fake_fetch(pages)

    result = collect_homepage_fragments(ROOT, fetch=fetch)

    assert len(result.fragments) == 1
    assert result.fragments[0]["출처"] in (f"{ROOT}/page-a", f"{ROOT}/page-b")


# ── 우선순위 ─────────────────────────────────────────────


def test_회사소개_페이지를_우선한다():
    links = (
        '<a href="/random1">아무거나1</a>'
        '<a href="/about">회사소개</a>'
        '<a href="/random2">아무거나2</a>'
    )
    pages = {ROOT: f"<html><body><p>루트</p>{links}</body></html>"}
    for path in ("random1", "random2", "about"):
        pages[f"{ROOT}/{path}"] = (
            "<html><body><p>" + (f"{path} 내용입니다. " * 10) + "</p></body></html>"
        )
    fetch, calls = _fake_fetch(pages)

    collect_homepage_fragments(ROOT, fetch=fetch)

    page_calls = [c for c in calls if not c.endswith("robots.txt")]
    # 루트(0) 다음으로 /about이 /random1·/random2보다 먼저 불려야 한다.
    assert page_calls.index(f"{ROOT}/about") < page_calls.index(f"{ROOT}/random1")
    assert page_calls.index(f"{ROOT}/about") < page_calls.index(f"{ROOT}/random2")


# ── 확장자 필터 ───────────────────────────────────────────


def test_문서_확장자_링크는_따라가지_않는다():
    pages = {
        ROOT: "<html><body><p>" + ("루트 소개 문단입니다. " * 10) + "</p>"
        '<a href="/brochure.pdf">회사소개서</a></body></html>',
    }
    fetch, calls = _fake_fetch(pages)

    collect_homepage_fragments(ROOT, fetch=fetch)

    assert f"{ROOT}/brochure.pdf" not in calls


# ── 인증서 이름 불일치 우회 (C안, 문제로그 P-46) ──────────
#
# 진짜 접속 없이, fetch·lookup_cert_names를 전부 가짜로 주입해 시험한다.


def test_인증서_이름이_같은_회사면_그_이름으로_재시도해_성공한다():
    """로보스타 사례 — robostar.co.kr 인증서엔 www.robostar.com 이 적혀 있다.

    핵심 이름("robostar")이 같으므로 그 이름으로 재시도해 성공해야 한다.
    """
    original = "http://robostar.co.kr"
    retried_url = "http://www.robostar.com"
    pages = {
        retried_url: "<html><body><p>" + ("로보스타 소개입니다. " * 10) + "</p></body></html>",
    }

    def fetch(url: str) -> str:
        if url.endswith("/robots.txt"):
            return ""
        if url == original:
            raise HomepageCertNameMismatchError(
                "인증서 이름 불일치: robostar.co.kr", host="robostar.co.kr"
            )
        if url in pages:
            return pages[url]
        raise HomepageFetchError(f"가짜 접속 실패: {url}")

    def lookup_cert_names(_url: str) -> list[str]:
        return ["www.robostar.com", "robostar.com"]

    result = collect_homepage_fragments(
        original, fetch=fetch, lookup_cert_names=lookup_cert_names
    )

    assert result.state == "ok"
    assert result.fragments[0]["출처"] == retried_url  # 출처는 «실제로 읽은 주소»
    assert original in result.detail  # 원래 주소가 기록에 남는다
    assert retried_url in result.detail


def test_인증서_이름이_다른_회사면_재시도하지_않고_실패로_남는다():
    """하이브 사례 — hiveoil.co.kr 인증서엔 호스팅 업체 도메인 realserver2.com 이 적혀 있다.

    핵심 이름이 다르므로(hiveoil ≠ realserver2) 절대 따라가면 안 된다. ★ 가장 중요한 시험.
    """
    original = "http://hiveoil.co.kr"

    def fetch(url: str) -> str:
        if url.endswith("/robots.txt"):
            return ""
        if url == original:
            raise HomepageCertNameMismatchError(
                "인증서 이름 불일치: hiveoil.co.kr", host="hiveoil.co.kr"
            )
        raise AssertionError(f"따라가면 안 되는 주소로 접속을 시도함: {url}")

    def lookup_cert_names(_url: str) -> list[str]:
        return ["realserver2.com"]

    result = collect_homepage_fragments(
        original, fetch=fetch, lookup_cert_names=lookup_cert_names
    )

    assert result.state == "failed"
    assert result.fragments == []


def test_이름_불일치가_아닌_인증서_오류는_재시도하지_않는다():
    """만료·자체서명 등은 일반 HomepageFetchError로 온다 — 이름 조회 자체를 시도하면 안 된다."""
    original = "http://expired.example.com"
    lookup_calls: list[str] = []

    def fetch(_url: str) -> str:
        if _url.endswith("/robots.txt"):
            raise HomepageRobotsUnavailable("HTTP 404")
        raise HomepageFetchError("인증서가 만료됨(이름 불일치 아님)")

    def lookup_cert_names(url: str) -> list[str]:
        lookup_calls.append(url)
        return ["expired.example.com"]

    result = collect_homepage_fragments(
        original, fetch=fetch, lookup_cert_names=lookup_cert_names
    )

    assert result.state == "failed"
    assert lookup_calls == []  # 이름 조회 자체를 시도하지 않았다


def test_재시도도_실패하면_failed로_남는다():
    original = "http://robostar.co.kr"

    def fetch(url: str) -> str:
        if url.endswith("/robots.txt"):
            return ""
        if url == original:
            raise HomepageCertNameMismatchError(
                "인증서 이름 불일치", host="robostar.co.kr"
            )
        raise HomepageFetchError(f"재시도 주소 접속도 실패: {url}")

    def lookup_cert_names(_url: str) -> list[str]:
        return ["www.robostar.com"]

    result = collect_homepage_fragments(
        original, fetch=fetch, lookup_cert_names=lookup_cert_names
    )

    assert result.state == "failed"
    assert result.fragments == []


def test_재시도는_한_번만_일어난다():
    original = "http://robostar.co.kr"
    retried_url = "http://www.robostar.com"
    calls: list[str] = []

    def fetch(url: str) -> str:
        calls.append(url)
        if url.endswith("/robots.txt"):
            return ""
        if url == original:
            raise HomepageCertNameMismatchError(
                "인증서 이름 불일치", host="robostar.co.kr"
            )
        raise HomepageFetchError("재시도도 실패")

    def lookup_cert_names(_url: str) -> list[str]:
        return ["www.robostar.com"]

    collect_homepage_fragments(original, fetch=fetch, lookup_cert_names=lookup_cert_names)

    assert calls.count(original) == 1
    assert calls.count(retried_url) == 1  # 재시도는 정확히 1번만


def test_www_접두사_차이만_있어도_같은_회사로_본다():
    original = "http://www.example.co.kr"
    retried_url = "http://example.co.kr"
    pages = {
        retried_url: "<html><body><p>" + ("예시 회사 소개입니다. " * 10) + "</p></body></html>",
    }

    def fetch(url: str) -> str:
        if url.endswith("/robots.txt"):
            return ""
        if url == original:
            raise HomepageCertNameMismatchError(
                "인증서 이름 불일치", host="www.example.co.kr"
            )
        if url in pages:
            return pages[url]
        raise HomepageFetchError(f"가짜 접속 실패: {url}")

    def lookup_cert_names(_url: str) -> list[str]:
        return ["example.co.kr"]

    result = collect_homepage_fragments(
        original, fetch=fetch, lookup_cert_names=lookup_cert_names
    )

    assert result.state == "ok"
    assert result.fragments[0]["출처"] == retried_url


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))


# ── 8장「인재상과 일하는 방식」재료 우선순위 ────────────────
#
# 실측 배경(2026-08-25): (주)진영의 경영철학은 `/company/overview.php`에 있는데
# `company`로만 걸려 후보 42개 중 18번째였고 6쪽 예산 밖으로 밀렸다.
# 아래 두 시험은 그 순서를 못 박는다 — 하나는 「들어와야 한다」, 하나는
# 「그렇다고 아무 overview나 올리면 안 된다」는 반대쪽 못이다.


def _priority_pages(root: str, paths: tuple[str, ...]) -> dict[str, str]:
    """루트에서 주어진 경로들로 링크가 나가는 가짜 사이트를 만든다."""
    links = "".join(f'<a href="{path}">{path}</a>' for path in paths)
    pages = {root: f"<html><body><p>루트 소개 문단입니다.</p>{links}</body></html>"}
    for path in paths:
        pages[f"{root}{path}"] = (
            "<html><body><p>" + (f"{path} 내용입니다. " * 10) + "</p></body></html>"
        )
    return pages


def test_경영철학_페이지를_연혁_조직도보다_먼저_읽는다():
    """경영철학·핵심가치·인재상은 6쪽 예산 안에 들어와야 한다.

    ★ 진영 실측 재현: `/company/overview`가 `/company/history`·`/company/ci`와
      같은 순위(`company`)면 알파벳순으로 밀려 예산 밖으로 나간다.
    """
    paths = (
        "/company/ci",
        "/company/bi",
        "/company/history",
        "/company/organization",
        "/company/overview",
        "/esg/business_ethics",
        "/ko/인재상",
        "/ko/핵심가치",
    )
    fetch, calls = _fake_fetch(_priority_pages(ROOT, paths))

    collect_homepage_fragments(ROOT, fetch=fetch)

    page_calls = [c for c in calls if not c.endswith("robots.txt")]
    읽은_경로 = set(page_calls)
    for 재료 in ("/company/overview", "/esg/business_ethics", "/ko/인재상", "/ko/핵심가치"):
        assert f"{ROOT}{재료}" in 읽은_경로, (
            f"8장 재료 {재료} 가 {MAX_PAGES}쪽 예산 밖으로 밀렸습니다"
        )
    # 연혁·CI·BI·조직도는 8장 재료에 자리를 내준다 — 예산이 6쪽뿐이기 때문이다.
    for 뒷순위 in ("/company/history", "/company/ci", "/company/bi"):
        if f"{ROOT}{뒷순위}" in 읽은_경로:
            assert page_calls.index(f"{ROOT}/company/overview") < page_calls.index(
                f"{ROOT}{뒷순위}"
            ), f"{뒷순위} 를 경영철학보다 먼저 읽었습니다"


def test_회사와_무관한_overview는_회사소개보다_앞서지_않는다():
    """맨몸 `overview`를 맨 앞에 두면 안 된다는 반대쪽 못.

    ★ 삼성전자 실측 반례(2026-08-25): `overview`를 1순위로 올렸더니
      `/sustainability/accessibility/overview/`가 예산을 다 먹고, 경영이념
      (인재제일·최고지향·변화선도·정도경영·상생추구)이 실린
      `/about-us/brand-identity/brand-story/`를 놓쳤다.
    """
    paths = (
        "/sustainability/accessibility/overview",
        "/about-us/brand-identity",
    )
    fetch, calls = _fake_fetch(_priority_pages(ROOT, paths))

    collect_homepage_fragments(ROOT, fetch=fetch)

    page_calls = [c for c in calls if not c.endswith("robots.txt")]
    assert page_calls.index(f"{ROOT}/about-us/brand-identity") < page_calls.index(
        f"{ROOT}/sustainability/accessibility/overview"
    ), "회사소개(about)보다 접근성 overview를 먼저 읽으면 경영이념을 놓칩니다"
