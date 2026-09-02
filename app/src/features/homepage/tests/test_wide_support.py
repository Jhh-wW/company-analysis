"""넓은 공식 웹 수집의 지원 모듈(도메인·추출·전송 분류) 회귀시험 — 실제 접속 없음."""

from __future__ import annotations

import pytest

from src.features.homepage.wide_domain import (
    bind_linked_host,
    bind_registered_subdomain,
    bind_www_apex_alternate,
    canonicalize_url,
    is_excluded_linked_host,
    is_registered_subdomain,
    parse_official_origin,
    registrable_core_name,
    slot_ids_for_url,
    www_apex_alternate,
)
from src.features.homepage.wide_extract import (
    extract_inline_spa_ranges,
    extract_json_ld_ranges,
    extract_links,
    extract_usable_ranges,
    parse_sitemap_urls,
)
from urllib import robotparser

from src.features.homepage.wide_fetch import (
    WideRawResponse,
    WideRobotsPolicy,
    WideTransportError,
    classify_general_outcome,
    fetch_sitemap,
    robots_decision,
)


def _allow_all_robots_policy(host: str = "company.example") -> WideRobotsPolicy:
    parser = robotparser.RobotFileParser()
    parser.parse([])  # 빈 규칙 = 전부 허용
    return WideRobotsPolicy(host=host, parser=parser, outcome="proceed_parsed", reason_code="robots_ok")


# ── wide_domain ──────────────────────────────────────────


def test_등록도메인_핵심이름은_eTLD플러스1을_돌려준다():
    """P0-1: 접미사를 뗀 핵심 이름 한 칸이 아니라 접미사를 포함한 전체
    등록 도메인(eTLD+1)을 돌려줘야 서로 다른 TLD가 같다고 오판하지 않는다."""
    assert registrable_core_name("recruit.company.co.kr") == "company.co.kr"
    assert registrable_core_name("www.company.com") == "company.com"
    assert registrable_core_name("company.io") == "company.io"


def test_같은_등록도메인의_하위도메인은_참():
    assert is_registered_subdomain("company.com", "recruit.company.com")
    assert is_registered_subdomain("company.co.kr", "ir.company.co.kr")


def test_다른_등록도메인은_거짓():
    assert not is_registered_subdomain("company.com", "otherbrand.com")


# ── P0-1 공격 시험: 등록 도메인 판정이 TLD를 무시하지 않는지 ──────────


def test_같은_핵심이름_다른_TLD는_거짓():
    """company.com과 company.net은 전혀 다른 회사가 등록할 수 있는 별개 도메인."""
    assert not is_registered_subdomain("company.com", "company.net")
    assert not is_registered_subdomain("company.net", "company.com")


def test_co_kr과_com은_다른_등록도메인():
    assert not is_registered_subdomain("company.co.kr", "company.com")
    assert not is_registered_subdomain("company.com", "company.co.kr")


def test_공개접미사_목록_밖_TLD는_fail_closed():
    """목록에 없는 접미사는 등록 도메인 경계를 모르므로 같다고 주장하지 않는다."""
    assert registrable_core_name("company.zzzunknowntld") == ""
    # root도 candidate도 목록 밖 TLD면 같은 문자열이어도 참으로 단정하지 않는다.
    assert not is_registered_subdomain("company.zzzunknowntld", "sub.company.zzzunknowntld")


def test_example은_정본_공개접미사_목록에_없다():
    """정정 2: .example은 RFC 2606 예약 TLD로 시험 픽스처 전용이다 — 실제
    회사 도메인 커버리지 목록(SINGLE_LABEL_PUBLIC_SUFFIXES)과 절대 섞이지
    않아야, 나중에 이걸 실제 커버리지 항목으로 오해하지 않는다."""
    from src.features.homepage.constants import (
        SINGLE_LABEL_PUBLIC_SUFFIXES,
        TEST_FIXTURE_ONLY_SINGLE_LABEL_SUFFIXES,
    )

    assert "example" not in SINGLE_LABEL_PUBLIC_SUFFIXES
    assert "example" in TEST_FIXTURE_ONLY_SINGLE_LABEL_SUFFIXES
    # 그럼에도 판정 시점에는 둘을 함께 봐서 .example 픽스처가 실제 코드
    # 경로(등록 도메인 판정)를 그대로 지나간다.
    assert registrable_core_name("company.example") == "company.example"


def test_대소문자는_구분하지_않는다():
    assert registrable_core_name("Company.COM") == registrable_core_name("company.com")
    assert is_registered_subdomain("COMPANY.COM", "recruit.company.com")


def test_후행_점은_무시한다():
    assert registrable_core_name("company.com.") == "company.com"


def test_IDN_퓨니코드는_크래시_없이_fail_closed():
    """공개 접미사 목록에 없는 퓨니코드 TLD는 예외 없이 판정 불가로 처리한다."""
    assert registrable_core_name("xn--e1aybc.xn--p1ai") == ""


def test_포트가_섞인_문자열은_크래시_없이_fail_closed():
    """이 함수에 도달하기 전 urlsplit().hostname이 포트를 떼지만, 방어 심층화로
    포트가 섞여 들어와도 예외 없이 다른 도메인이라고 오판하지 않아야 한다."""
    assert registrable_core_name("company.com:8080") == ""
    assert not is_registered_subdomain("company.com:8080", "company.com")


def test_userinfo_트릭은_urlsplit_hostname_단계에서_이미_제거된다():
    """evil.com@company.com처럼 URL에 사용자정보를 끼워 넣는 공격은
    urllib.parse.urlsplit(url).hostname이 진짜 호스트만 남기므로,
    wide_collect가 실제로 넘기는 host 값에는 '@' 앞부분이 없다(실측)."""
    import urllib.parse

    hostname = urllib.parse.urlsplit("http://evil.com@company.com/path").hostname
    assert hostname == "company.com"
    assert registrable_core_name(hostname) == "company.com"


def test_등록_하위도메인_결속은_결속근거를_남긴다():
    bound = bind_registered_subdomain("company.com", "recruit.company.com")
    assert bound is not None
    assert bound.is_high_confidence is True
    assert "company.com" in bound.identity_binding


def test_다른_등록도메인은_결속되지_않는다():
    assert bind_registered_subdomain("company.com", "otherbrand.com") is None


def test_공유플랫폼_하위host는_같은_eTLD의_다른host를_회사소유로_승격하지_않는다():
    assert bind_registered_subdomain("sites.google.com", "drive.google.com") is None
    assert bind_registered_subdomain("blog.naver.com", "news.naver.com") is None


# ── APEX-WWW-OFFICIAL-ROOT-GAP(통합 담당 지시, 2026-08-31) ────────


def test_www_apex_alternate은_www_접두사를_붙이거나_뗀다():
    assert www_apex_alternate("company.example") == "www.company.example"
    assert www_apex_alternate("www.company.example") == "company.example"


def test_www_apex_alternate은_등록도메인을_넘지_않는다():
    """다른 등록 도메인으로 새지 않는다 — «www. 접두사 유무」라는 좁은
    변형만 다룬다. 등록 도메인 전체를 폭넓게 허용하는 방향이 아니다."""
    for host in ("company.example", "www.company.example"):
        alternate = www_apex_alternate(host)
        assert registrable_core_name(alternate) == registrable_core_name(host)


def test_www_apex_alternate은_빈_문자열이면_None():
    assert www_apex_alternate("") is None


def test_bind_www_apex_alternate은_고신뢰_후보를_결속한다():
    bound = bind_www_apex_alternate("company.example")
    assert bound is not None
    assert bound.host == "www.company.example"
    assert bound.is_high_confidence is True
    assert "company.example" in bound.identity_binding


def test_bind_www_apex_alternate은_공개접미사_밖_TLD면_None():
    """fail-closed — root_host 자체의 등록 도메인을 판정할 수 없으면
    apex/www 짝도 만들지 않는다(등록 도메인 전체를 폭넓게 허용하지 않는다는
    원칙과 같은 맥락)."""
    assert bind_www_apex_alternate("company.zzzunknowntld") is None


def test_www_apex는_등록도메인_루트와_정확한_www에만_허용된다():
    assert www_apex_alternate("company.com") == "www.company.com"
    assert www_apex_alternate("www.company.com") == "company.com"
    assert www_apex_alternate("recruit.company.com") is None
    assert bind_www_apex_alternate("recruit.company.com") is None


def test_official_origin은_DART_scheme_port_path를_끝까지_보존한다():
    origin = parse_official_origin("https://sites.example.com:8443/acme?lang=ko")

    assert origin is not None
    assert origin.root_url == "https://sites.example.com:8443/acme?lang=ko"
    assert origin.robots_url == "https://sites.example.com:8443/robots.txt"
    assert origin.sitemap_url == "https://sites.example.com:8443/sitemap.xml"
    assert origin.allows_content_url("https://sites.example.com:8443/acme?lang=ko")
    assert origin.allows_content_url(
        "https://sites.example.com:8443/acme/products?lang=ko&utm_source=x"
    )
    assert not origin.allows_content_url("https://sites.example.com:8443/acme")
    assert not origin.allows_content_url("https://sites.example.com:8443/acme?lang=en")
    assert not origin.allows_content_url("https://sites.example.com:8443/other")
    assert not origin.allows_content_url("http://sites.example.com:8080/acme")


@pytest.mark.parametrize(
    ("start_url", "candidate_url", "allowed"),
    (
        ("https://portal.example/view?ref=ALPHA", "https://portal.example/view/about?ref=BETA", False),
        ("https://portal.example/view?ref=ALPHA", "https://portal.example/view/about", False),
        (
            "https://portal.example/view?ref=ALPHA",
            "https://portal.example/view/about?ref=ALPHA&page=2",
            True,
        ),
        (
            "https://portal.example/view?tenant=ALPHA",
            "https://portal.example/view/about?tenant=ALPHA&page=2&utm_source=x",
            True,
        ),
        (
            "https://portal.example/view?tenant=ALPHA",
            "https://portal.example/view/about?tenant=ALPHA&tenant=BETA",
            False,
        ),
        (
            "https://portal.example/view?tenant=ALPHA",
            "https://portal.example/view/about?tenant=ALPHA&company=BETA",
            False,
        ),
        (
            "https://portal.example/acme",
            "https://portal.example/acme/about?tenant=BETA",
            False,
        ),
    ),
)
def test_query_scope_red_cases는_시작값과_새키_allowlist를_엄격히_지킨다(
    start_url, candidate_url, allowed
):
    origin = parse_official_origin(start_url)

    assert origin is not None
    assert origin.allows_content_url(candidate_url) is allowed


@pytest.mark.parametrize(
    "url",
    (
        "https://portal.example/view?tenant=%FF",
        "https://portal.example/view?tenant=%FE",
        "https://portal.example/view?tenant=%",
        "https://portal.example/view?tenant=%G0",
        "https://portal.example/view?tenant=ALPHA;page=2",
    ),
)
def test_official_origin은_모호하거나_UTF8이_아닌_query를_fail_closed한다(url):
    assert parse_official_origin(url) is None


def test_query_scope는_서로_다른_invalid_UTF8을_대체문자로_축약하지_않는다():
    origin = parse_official_origin(
        "https://portal.example/view?tenant=%EF%BF%BD"
    )

    assert origin is not None
    assert not origin.allows_content_url(
        "https://portal.example/view/about?tenant=%FF"
    )
    assert not origin.allows_content_url(
        "https://portal.example/view/about?tenant=%FE"
    )


def test_percent_encoding한_세미콜론은_값으로_명확하므로_허용한다():
    origin = parse_official_origin(
        "https://portal.example/view?tenant=ALPHA%3BBETA"
    )

    assert origin is not None
    assert origin.allows_content_url(
        "https://portal.example/view/about?tenant=ALPHA%3BBETA&page=2"
    )


def test_official_origin은_0번_port를_기본_port로_둔갑시키지_않는다():
    assert parse_official_origin("https://company.example:0/tenant") is None

    origin = parse_official_origin("https://company.example/tenant")

    assert origin is not None
    assert not origin.allows_content_url("https://company.example:0/tenant")


def test_공유host의_루트_파일은_host_전체로_권한을_넓히지_않는다():
    origin = parse_official_origin("https://sites.example.com/acme.html")

    assert origin is not None
    assert origin.allows_content_url("https://sites.example.com/acme.html")
    assert not origin.allows_content_url("https://sites.example.com/acme.html/other")
    assert not origin.allows_content_url("https://sites.example.com/other.html")


@pytest.mark.parametrize(
    ("status", "expected_reason"),
    [
        (404, "sitemap_missing_404"),
        (410, "sitemap_missing_410"),
        (407, "sitemap_denied_407"),
        (408, "sitemap_transient_408"),
        (409, "sitemap_transient_409"),
        (429, "sitemap_transient_429"),
        (500, "sitemap_failed_500"),
    ],
)
def test_sitemap은_404_410만_missing이고_권한_일시장애_서버오류를_분리한다(
    status, expected_reason
):
    response = WideRawResponse(
        status=status,
        text="",
        effective_url="https://company.example/sitemap.xml",
        content_type="",
    )

    text, reason = fetch_sitemap(
        scheme="https",
        host="company.example",
        fetch=lambda _url, _allowed: response,
        robots=_allow_all_robots_policy(),
        max_bytes=1024,
    )

    assert text == ""
    assert reason == expected_reason


def test_소셜호스트는_링크_후보_결속에서_제외된다():
    assert is_excluded_linked_host("www.facebook.com")
    bound = bind_linked_host(
        source_page_url="https://company.com/about",
        discovered_url="https://facebook.com/company",
        candidate_host="facebook.com",
    )
    assert bound is None


def test_일반_외부_링크는_공식페이지가_가리켜도_후보host로_결속하지_않는다():
    bound = bind_linked_host(
        source_page_url="https://company.com/about",
        discovered_url="https://brand-site.example/",
        candidate_host="brand-site.example",
    )
    assert bound is None


def test_canonicalize_url은_fragment를_없앤다():
    assert canonicalize_url("https://Company.com/about#section") == "https://company.com/about"


def test_canonicalize_url은_추적파라미터를_뺀다():
    result = canonicalize_url("https://company.com/about?utm_source=x&id=1&gclid=y")
    assert result == "https://company.com/about?id=1"


def test_canonicalize_url은_쿼리_순서를_정렬한다():
    left = canonicalize_url("https://company.com/about?b=2&a=1")
    right = canonicalize_url("https://company.com/about?a=1&b=2")
    assert left == right


@pytest.mark.parametrize(
    "url",
    (
        "https://company.example/about?tenant=%FF",
        "https://company.example/about?tenant=%FE",
        "https://company.example/about?tenant=A;page=2",
    ),
)
def test_canonicalize_url은_모호하거나_UTF8이_아닌_query를_거절한다(url):
    with pytest.raises(ValueError):
        canonicalize_url(url)


def test_slot_ids_for_url은_경로_키워드로_매칭한다():
    assert slot_ids_for_url("https://company.example/careers") == (
        "culture:work_principle",
        "culture:verified_case",
    )


def test_slot_ids_for_url은_하위도메인_키워드로도_매칭한다():
    assert slot_ids_for_url("https://recruit.company.example/") == (
        "culture:work_principle",
        "culture:verified_case",
    )


def test_slot_ids_for_url은_등록도메인_자체_낱말에_오탐하지_않는다():
    """company.example 같은 도메인이 «about/company» 범주를 우연히 채가면 안 된다.

    (실측 회귀 — 실제로 이 테스트가 처음 실패해서 host-label 대조로 고쳤다.)
    """
    assert slot_ids_for_url("https://company.example/random-page") == ()


def test_slot_ids_for_url은_알수없는_페이지면_빈_튜플():
    assert slot_ids_for_url("https://company.example/xyz-unrelated") == ()


# ── wide_extract ─────────────────────────────────────────


def test_usable_ranges는_nav_footer를_뺀다():
    html = (
        "<html><body>"
        "<nav>메뉴 링크 모음</nav>"
        "<main><p>" + ("본문 문단입니다. " * 8) + "</p></main>"
        "<footer>저작권 안내 문구</footer>"
        "</body></html>"
    )
    ranges, _title = extract_usable_ranges(html)
    joined = " ".join(ranges)
    assert "본문 문단입니다" in joined
    assert "메뉴 링크 모음" not in joined
    assert "저작권 안내 문구" not in joined


def test_usable_ranges는_title을_뽑는다():
    html = "<html><head><title>회사소개</title></head><body><p>" + ("내용 " * 20) + "</p></body></html>"
    _ranges, title = extract_usable_ranges(html)
    assert title == "회사소개"


def test_너무_짧은_구간은_버린다():
    html = "<html><body><p>짧음</p></body></html>"
    ranges, _title = extract_usable_ranges(html)
    assert ranges == ()


def test_링크_추출은_문서_확장자를_뺀다():
    html = '<a href="/about">a</a><a href="/report.pdf">b</a><a href="javascript:void(0)">c</a>'
    links = extract_links(html, "https://company.com/")
    assert "https://company.com/about" in links
    assert not any(link.endswith(".pdf") for link in links)
    assert not any("javascript" in link for link in links)


def test_json_ld에서_문자열값만_뽑는다():
    html = (
        '<script type="application/ld+json">'
        '{"@type": "Organization", "name": "예시 회사", "url": "https://company.com"}'
        "</script>"
    )
    ranges = extract_json_ld_ranges(html)
    assert "예시 회사" in ranges
    assert not any(value.startswith("http") for value in ranges)


def test_json_ld_파싱실패는_조용히_건너뛴다():
    html = '<script type="application/ld+json">{잘못된 json}</script>'
    assert extract_json_ld_ranges(html) == ()


def test_next_data_인라인값을_뽑는다():
    html = (
        '<script id="__NEXT_DATA__" type="application/json">'
        '{"props": {"pageProps": {"description": "우리 회사 제품 소개 문구입니다"}}}'
        "</script>"
    )
    ranges = extract_inline_spa_ranges(html)
    assert any("제품 소개" in value for value in ranges)


def test_next_data_파싱실패는_조용히_건너뛴다():
    html = '<script id="__NEXT_DATA__" type="application/json">{망가진}</script>'
    assert extract_inline_spa_ranges(html) == ()


def test_sitemap_urls_파싱():
    xml = (
        '<?xml version="1.0"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
        "<url><loc>https://company.com/about</loc></url>"
        "<url><loc>https://company.com/careers</loc></url>"
        "</urlset>"
    )
    urls = parse_sitemap_urls(xml)
    assert urls == ("https://company.com/about", "https://company.com/careers")


def test_sitemap_형식이_깨지면_빈_튜플():
    assert parse_sitemap_urls("<not valid xml") == ()


# ── wide_fetch 분류 ──────────────────────────────────────


def _response(status: int) -> WideRawResponse:
    return WideRawResponse(status=status, text="", effective_url="https://company.com/x", content_type="")


def test_200은_OK로_분류된다():
    state, reason = classify_general_outcome(_response(200), None)
    assert (state, reason) == ("OK", "page_ok")


def test_404는_MISSING으로_분류된다():
    state, reason = classify_general_outcome(_response(404), None)
    assert state == "MISSING"


def test_410도_MISSING으로_분류된다():
    state, reason = classify_general_outcome(_response(410), None)
    assert state == "MISSING"


def test_500은_FAILED로_분류된다():
    state, reason = classify_general_outcome(_response(500), None)
    assert state == "FAILED"


def test_시간초과는_FAILED로_분류된다():
    state, reason = classify_general_outcome(None, WideTransportError("HTTPError: timed out"))
    assert state == "FAILED"
    assert reason == "network_failed"


def test_DNS_실패_메시지는_MISSING으로_분류된다():
    error = WideTransportError("UnsafeHomepageUrlError: 호스트 이름을 찾지 못했습니다")
    state, reason = classify_general_outcome(None, error)
    assert state == "MISSING"
    assert reason == "dns_missing"


def test_robots_200은_규칙을_해석해_진행한다():
    outcome, _reason = robots_decision(_response(200), None)
    assert outcome == "proceed_parsed"


def test_robots_404는_빈_규칙으로_진행한다():
    outcome, _reason = robots_decision(_response(404), None)
    assert outcome == "proceed_empty_rules"


# ── P1-1 공격 시험: robots 401/403은 명시적 거부이지 「없음」이 아니다 ──


def test_robots_401은_차단된다():
    """ROBOTS-EXPLICIT-DENIAL: 401은 인증 요구 — «robots가 없다»가 아니다."""
    outcome, reason = robots_decision(_response(401), None)
    assert outcome == "blocked"
    assert reason == "robots_denied"


def test_robots_403은_차단된다():
    outcome, reason = robots_decision(_response(403), None)
    assert outcome == "blocked"
    assert reason == "robots_denied"


def test_robots_404는_여전히_빈_규칙으로_진행한다():
    """404·410처럼 진짜 «없음»을 뜻하는 4xx는 fail-closed 대상이 아니다."""
    outcome, reason = robots_decision(_response(404), None)
    assert outcome == "proceed_empty_rules"
    assert reason == "robots_missing"


def test_robots_410도_여전히_빈_규칙으로_진행한다():
    outcome, _reason = robots_decision(_response(410), None)
    assert outcome == "proceed_empty_rules"


def test_robots_500은_차단된다():
    outcome, _reason = robots_decision(_response(500), None)
    assert outcome == "blocked"


def test_robots_전송실패는_차단된다():
    outcome, _reason = robots_decision(None, WideTransportError("시간초과"))
    assert outcome == "blocked"


# ── P1(통합 담당 지시, 2026-08-31): robots 상태 계약을 정확히 좁힌다 ──
# 「명시적 부재」는 404·410만 인정한다. 그 밖의 4xx는 원인별로
# blocked(robots_denied/robots_transient/robots_unreachable)로 나눈다.


def test_robots_407도_명시적_거부로_차단된다():
    """407(프록시 인증)도 401/403과 같은 «명시적 거부» — robots가 없다는
    뜻이 아니다."""
    outcome, reason = robots_decision(_response(407), None)
    assert outcome == "blocked"
    assert reason == "robots_denied"


def test_robots_408은_일시장애로_차단된다():
    outcome, reason = robots_decision(_response(408), None)
    assert outcome == "blocked"
    assert reason == "robots_transient"


def test_robots_409는_일시장애로_차단된다():
    outcome, reason = robots_decision(_response(409), None)
    assert outcome == "blocked"
    assert reason == "robots_transient"


def test_robots_429는_일시장애로_차단된다():
    """429(속도 제한)를 «robots 없음»으로 읽으면 안 된다 — 이 회사가
    robots를 공개하지 않는다는 뜻이 아니라 일시적으로 못 받은 것이다."""
    outcome, reason = robots_decision(_response(429), None)
    assert outcome == "blocked"
    assert reason == "robots_transient"


def test_robots_그밖의_4xx는_도달불가로_차단된다():
    """denied·transient·missing 목록 어디에도 없는 4xx(예: 400·402·405·406)는
    robots_unreachable로 차단한다 — «명시적 부재로 진행」이 아니다."""
    for status in (400, 402, 405, 406, 411, 451):
        outcome, reason = robots_decision(_response(status), None)
        assert outcome == "blocked", f"status={status}"
        assert reason == "robots_unreachable", f"status={status}"


# ── P1-2 공격 시험: sitemap 바이트 상한이 문자 상한이면 안 된다 ──────


def _sitemap_response(text: str, host: str = "company.example") -> WideRawResponse:
    return WideRawResponse(
        status=200,
        text=text,
        effective_url=f"https://{host}/sitemap.xml",
        content_type="application/xml",
    )


def test_한글_sitemap은_문자수가_아니라_바이트수로_잘린다():
    """ROOT CAUSE: 한글 한 글자는 UTF-8에서 최대 3바이트라, 예전엔
    text[:max_bytes]가 «문자 수»를 잘라서 선언한 바이트 상한을 최대 3배
    넘을 수 있었다."""
    # 한글 5글자(각 3바이트) = 15바이트. max_bytes=10이면 3글자(9바이트)까지만 담아야 한다.
    korean_text = "가나다라마"
    assert len(korean_text.encode("utf-8")) == 15

    result_text, reason = fetch_sitemap(
        scheme="https",
        host="company.example",
        fetch=lambda url, url_allowed: _sitemap_response(korean_text),
        robots=_allow_all_robots_policy(),
        max_bytes=10,
    )

    assert reason == "sitemap_ok"
    assert len(result_text.encode("utf-8")) <= 10
    assert result_text == "가나다"  # 9바이트 — 그다음 글자(3바이트)를 넣으면 12바이트라 상한 초과


def test_바이트_경계가_멀티바이트_문자_중간이어도_예외없이_안전하게_자른다():
    """max_bytes가 한글 한 글자의 중간(예: 2바이트째)에서 끊겨도 깨진 바이트
    시퀀스로 예외를 던지지 않고 조용히 버려야 한다."""
    korean_text = "가나다라마"  # "가" 하나가 3바이트

    result_text, _reason = fetch_sitemap(
        scheme="https",
        host="company.example",
        fetch=lambda url, url_allowed: _sitemap_response(korean_text),
        robots=_allow_all_robots_policy(),
        max_bytes=2,  # "가"(3바이트)의 중간에서 끊긴다
    )

    assert len(result_text.encode("utf-8")) <= 2
    assert result_text == ""  # 불완전한 앞 2바이트는 버려진다(예외 없음)


def test_ascii_sitemap은_기존과_동일하게_바이트수로_자른다():
    """영문 등 1바이트 문자는 문자수=바이트수라 예전 동작과 결과가 같아야 한다
    (회귀 없음을 확인)."""
    ascii_text = "abcdefghij"  # 10바이트

    result_text, _reason = fetch_sitemap(
        scheme="https",
        host="company.example",
        fetch=lambda url, url_allowed: _sitemap_response(ascii_text),
        robots=_allow_all_robots_policy(),
        max_bytes=5,
    )

    assert result_text == "abcde"


def test_max_bytes보다_짧은_sitemap은_그대로_반환된다():
    short_text = "짧은 문서"
    result_text, _reason = fetch_sitemap(
        scheme="https",
        host="company.example",
        fetch=lambda url, url_allowed: _sitemap_response(short_text),
        robots=_allow_all_robots_policy(),
        max_bytes=1_000_000,
    )
    assert result_text == short_text
