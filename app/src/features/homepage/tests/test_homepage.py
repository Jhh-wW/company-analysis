"""홈페이지 수집 시험 — 가짜 `fetch`만 쓴다. 실제 접속은 하지 않는다.

"""

from __future__ import annotations

import pytest

from src.features.homepage import safe_http
from src.features.composer.port import filing_meta_from_raw
from src.features.homepage.constants import (
    MAX_CHARS_PER_PAGE,
    MAX_PAGES,
    MAX_TOTAL_CHARS,
    MIN_FRAGMENT_CHARS,
)
from src.features.homepage.logic import (
    FetchedPage,
    HomepageCertNameMismatchError,
    HomepageFetchError,
    HomepageRobotsUnavailable,
    HomepageRobotsUnreachable,
    HomepageSecurityPolicyError,
    _collect_page,
    _cut_at_whitespace,
    collect_homepage_fragments,
    strip_html,
)
from src.features.pipeline.evidence_transport import typed_fragments_from_raw

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


# ── HTTPS 전면 실패 → HTTP 재시도 (P0: robots 캐시는 scheme까지 구분해야 한다) ──
#
# 독립 검토 실측: robots 캐시 키를 host만으로 두면, 이 HTTP 재시도가
# 같은 collect_homepage_fragments scope 안에서 돌기 때문에 HTTPS robots(허용)
# 판정을 그대로 물려받아 HTTP robots.txt(전면 차단)를 다시 확인하지 않는다 —
# 차단된 사이트를 평문으로 읽어버리는 사고다.


def test_https_전면실패후_http_재시도는_http_robots를_다시_조회하고_차단을_지킨다():
    """HTTPS robots는 허용, HTTPS 루트 페이지는 접속 실패, HTTP robots는 전면
    차단인 사이트 — HTTP 재시도가 HTTPS의 「허용」 판정을 캐시로 물려받지 않고
    HTTP robots.txt를 실제로 다시 조회해서 차단을 지켜야 한다."""
    https_root = "https://company.example"
    http_root = "http://company.example"
    calls: list[str] = []

    def fetch(url: str) -> str:
        calls.append(url)
        if url == f"{https_root}/robots.txt":
            return "User-agent: *\nAllow: /\n"
        if url == https_root:
            raise HomepageFetchError("가짜 접속 실패: 루트")
        if url == f"{http_root}/robots.txt":
            return "User-agent: *\nDisallow: /\n"
        if url == http_root:
            return (
                "<html><body><p>"
                + ("읽으면 안 되는 차단된 본문입니다. " * 10)
                + "</p></body></html>"
            )
        raise AssertionError(f"예상하지 않은 주소: {url}")

    result = collect_homepage_fragments(https_root, fetch=fetch)

    assert calls.count(f"{https_root}/robots.txt") == 1
    assert calls.count(f"{http_root}/robots.txt") == 1, (
        "HTTP robots.txt를 다시 조회하지 않았다 — HTTPS의 「허용」 판정이 HTTP로 "
        f"샜다(P0). 실제 호출 순서: {calls}"
    )
    assert result.state == "failed", (
        f"HTTP robots.txt가 전면 차단인데 state={result.state}로 성공 처리됨 "
        f"(detail={result.detail!r}) — robots 차단을 지키지 못했다(P0)."
    )
    assert http_root not in calls  # robots가 막았으니 본문은 요청되지 않아야 한다


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


# ── 인증서 이름 불일치 우회 (C안) ──────────
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
# 실측 배경: (주)진영의 경영철학은 `/company/overview.php`에 있는데
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

    ★ 삼성전자 실측 반례: `overview`를 1순위로 올렸더니
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


# ── 절단 경계 공백 ────────────────────────────────────────
#
# 글자 수로만 자르면 절단점이 공백이거나 낱말 중간일 수 있다. 공백에서 잘린
# 원문은 근거 transport의 원문 형식 검사(`값 != 값.strip()`이면 거절)에 걸려
# 조각이 typed 신원을 잃는다.

#: 가공 본문에서 쓰는 낱말 길이(첫 낱말 제외).
_TOKEN_CHARS = 6
#: 낱말 하나 + 그 뒤 공백 한 칸이 차지하는 간격.
_STRIDE_CHARS = _TOKEN_CHARS + 1
#: 첫 낱말이 이보다 짧으면 절단 자리를 옮기는 뜻이 흐려진다.
_MIN_LEAD_CHARS = 2
#: 낱말이 서로 달라야 「마지막 낱말이 온전한가」를 셀 수 있다.
_WORD_FILLERS = "가나다라마바사아자차"


def _fixture_word(index: int, length: int) -> str:
    """길이가 정확히 `length`이고 서로 구분되는 가공 낱말을 만든다."""

    number = str(index)
    if len(number) >= length:
        return number[-length:]
    filler = _WORD_FILLERS[index % len(_WORD_FILLERS)]
    return filler * (length - len(number)) + number


def _spaced_text(*, lead_chars: int, total_chars: int) -> str:
    """공백 자리를 계산할 수 있는 가공 본문을 만든다.

    공백은 `lead_chars`, `lead_chars + _STRIDE_CHARS`, ... 자리에 온다.
    첫 낱말 길이를 바꾸면 절단점이 공백·낱말 중간 중 어디에 걸리는지 고를 수 있다.
    """

    words = [_fixture_word(0, lead_chars)]
    length = lead_chars
    index = 1
    while length < total_chars:
        words.append(_fixture_word(index, _TOKEN_CHARS))
        length += _STRIDE_CHARS
        index += 1
    return " ".join(words)


def _lead_for_space_at(position: int) -> int:
    """공백이 정확히 `position` 자리에 오도록 첫 낱말 길이를 고른다."""

    lead = position % _STRIDE_CHARS
    return lead if lead >= _MIN_LEAD_CHARS else lead + _STRIDE_CHARS


def _page_html(text: str) -> str:
    return f"<html><body><p>{text}</p></body></html>"


def _collect_one(
    text: str,
    *,
    total_chars: int = 0,
    page_url: str = f"{ROOT}/about",
) -> tuple[list[dict[str, str]], int]:
    """`_collect_page`를 운영과 같은 인자로 한 번 부른다."""

    raw_html = _page_html(text)
    assert strip_html(raw_html) == text, "가공 본문이 HTML 정리를 거치며 바뀌었습니다"
    fragments: list[dict[str, str]] = []
    seen_text: set[str] = set()
    total = _collect_page(page_url, raw_html, fragments, seen_text, total_chars)
    return fragments, total


def _assert_clean_cut(kept: str, *, source: str, limit: int) -> None:
    """자른 결과가 공백을 남기지 않고 낱말도 쪼개지 않았는지 본다."""

    assert kept == kept.strip(), "자른 원문 끝(또는 앞)에 공백이 남았습니다"
    assert len(kept) <= limit
    assert source.startswith(kept), "자른 결과가 원문의 앞부분이 아닙니다"
    assert kept.split()[-1] in source.split(), "마지막 낱말이 중간에서 잘렸습니다"


@pytest.mark.parametrize(
    ("text", "limit", "expected"),
    [
        ("가나다 라마", 3, "가나다"),  # 절단점 바로 뒤가 반각 공백
        ("가나 다라마", 3, "가나"),  # 절단점 앞이 반각 공백 — 그대로 두면 공백이 남는다
        ("가나\n다라마", 3, "가나"),  # 줄바꿈 경계
        ("가나\u3000다라마", 3, "가나"),  # 전각 공백 경계
        ("가나다 라마바사", 5, "가나다"),  # 낱말 중간 — 앞 공백까지 되감는다
        ("가나다라마", 5, "가나다라마"),  # 정확히 limit
        (" 가나다 ", 10, "가나다"),  # limit 미만이면 strip만
        ("가나다라마바", 4, "가나다라"),  # 공백 없는 한 덩어리
    ],
)
def test_공백경계_되감기가_경계마다_공백없는_결과를_준다(
    text: str, limit: int, expected: str
) -> None:
    kept = _cut_at_whitespace(text, limit)

    assert kept == expected
    assert kept == kept.strip()
    assert len(kept) <= limit


@pytest.mark.parametrize(
    ("lead_chars", "설명"),
    [
        (_lead_for_space_at(MAX_CHARS_PER_PAGE), "절단점 바로 뒤가 공백"),
        (_lead_for_space_at(MAX_CHARS_PER_PAGE - 1), "절단점 앞이 공백"),
        (_TOKEN_CHARS, "절단점이 낱말 중간"),
    ],
)
def test_페이지_상한_절단이_공백이나_낱말을_남기지_않는다(
    lead_chars: int, 설명: str
) -> None:
    text = _spaced_text(lead_chars=lead_chars, total_chars=MAX_CHARS_PER_PAGE * 2)
    assert len(text) > MAX_CHARS_PER_PAGE, "가공 본문이 페이지 상한을 넘지 않습니다"

    fragments, total = _collect_one(text)

    assert len(fragments) == 1, 설명
    kept = fragments[0]["원문"]
    assert len(kept) < len(text), "실제로 자르지 않았습니다"
    _assert_clean_cut(kept, source=text, limit=MAX_CHARS_PER_PAGE)
    assert total == len(kept)


#: 전체 상한의 잔여가 낱말 «중간»에 걸리도록 고른 잔여 글자 수.
_REMAINING_MID_WORD_CHARS = _STRIDE_CHARS * 70 + 3


def test_전체_상한_잔여_절단도_낱말_경계에서_멈춘다() -> None:
    """앞선 쪽들이 예산을 거의 다 쓴 마지막 쪽의 잔여 절단."""

    text = _spaced_text(lead_chars=_TOKEN_CHARS, total_chars=MAX_CHARS_PER_PAGE * 2)
    page_text = _cut_at_whitespace(text, MAX_CHARS_PER_PAGE)
    assert not page_text[_REMAINING_MID_WORD_CHARS].isspace()
    assert not page_text[_REMAINING_MID_WORD_CHARS - 1].isspace()
    used = MAX_TOTAL_CHARS - _REMAINING_MID_WORD_CHARS

    fragments, total = _collect_one(text, total_chars=used)

    assert len(fragments) == 1
    kept = fragments[0]["원문"]
    assert len(kept) < _REMAINING_MID_WORD_CHARS, "잔여 절단이 일어나지 않았습니다"
    _assert_clean_cut(kept, source=text, limit=_REMAINING_MID_WORD_CHARS)
    assert total == used + len(kept)
    assert total <= MAX_TOTAL_CHARS


def test_되감아_조각하한_아래로_내려가면_조각을_만들지_않는다() -> None:
    """되감은 결과가 토막이면 싣지 않는다 — 하한을 우회하는 조각을 막는다."""

    head_chars = MIN_FRAGMENT_CHARS // 2
    remaining = MIN_FRAGMENT_CHARS + head_chars
    text = (
        _fixture_word(0, head_chars)
        + " "
        + _fixture_word(1, MIN_FRAGMENT_CHARS * 3)
    )
    assert MIN_FRAGMENT_CHARS <= len(text) <= MAX_CHARS_PER_PAGE
    used = MAX_TOTAL_CHARS - remaining

    fragments, total = _collect_one(text, total_chars=used)

    assert fragments == []
    assert total == used


#: 근거 transport 검사에 넣을 가공 회사 고유번호(여덟 자리).
_TRANSPORT_CORP_ID = "00126380"
_TRANSPORT_FILING_META = filing_meta_from_raw(
    {
        "rcept_no": "20260315000123",
        "report_nm": "사업보고서 (2025.12)",
        "rcept_dt": "20260315",
    }
)


def test_절단된_홈페이지_조각이_근거_transport의_원문검사를_통과한다() -> None:
    """운영 변환 함수를 그대로 불러 「원문 형식」 거절이 없는지 본다."""

    frags: dict[int, dict[str, str]] = {}
    for number, lead_chars in enumerate(
        (
            _lead_for_space_at(MAX_CHARS_PER_PAGE),
            _lead_for_space_at(MAX_CHARS_PER_PAGE - 1),
            _TOKEN_CHARS,
        ),
        start=1,
    ):
        text = _spaced_text(lead_chars=lead_chars, total_chars=MAX_CHARS_PER_PAGE * 2)
        fragments, _total = _collect_one(text, page_url=f"{ROOT}/about{number}")
        assert len(fragments) == 1
        frags[number] = fragments[0]

    conversion = typed_fragments_from_raw(
        corp_id=_TRANSPORT_CORP_ID,
        frags=frags,
        filing_meta=_TRANSPORT_FILING_META,
    )

    assert conversion.carried_raw_reasons == ()
    assert conversion.carried_raw_count == 0
    assert conversion.skipped_empty_count == 0
    assert conversion.legacy_count == len(frags)
    assert len(conversion.fragments) == len(frags)


def test_절단없는_짧은_페이지는_원문이_그대로_실린다() -> None:
    """대조군 — 상한에 걸리지 않는 쪽은 이 변경으로 결과가 바뀌지 않는다."""

    text = _spaced_text(
        lead_chars=_TOKEN_CHARS, total_chars=MIN_FRAGMENT_CHARS * 4
    )
    assert len(text) < MAX_CHARS_PER_PAGE

    fragments, total = _collect_one(text)

    assert len(fragments) == 1
    assert fragments[0]["원문"] == text
    assert total == len(text)
