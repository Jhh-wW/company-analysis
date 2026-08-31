"""넓은 공식 웹 수집의 지원 모듈(도메인·추출·전송 분류) 회귀시험 — 실제 접속 없음."""

from __future__ import annotations

from src.features.homepage.wide_domain import (
    bind_linked_host,
    bind_registered_subdomain,
    canonicalize_url,
    is_excluded_linked_host,
    is_registered_subdomain,
    registrable_core_name,
    slot_ids_for_url,
)
from src.features.homepage.wide_extract import (
    extract_inline_spa_ranges,
    extract_json_ld_ranges,
    extract_links,
    extract_usable_ranges,
    parse_sitemap_urls,
)
from src.features.homepage.wide_fetch import (
    WideRawResponse,
    WideTransportError,
    classify_general_outcome,
    robots_decision,
)


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


def test_소셜호스트는_링크_후보_결속에서_제외된다():
    assert is_excluded_linked_host("www.facebook.com")
    bound = bind_linked_host(
        source_page_url="https://company.com/about",
        discovered_url="https://facebook.com/company",
        candidate_host="facebook.com",
    )
    assert bound is None


def test_링크_후보_결속은_출처_페이지와_링크를_함께_남긴다():
    bound = bind_linked_host(
        source_page_url="https://company.com/about",
        discovered_url="https://brand-site.example/",
        candidate_host="brand-site.example",
    )
    assert bound is not None
    assert bound.is_high_confidence is False
    assert "https://company.com/about" in bound.identity_binding
    assert "https://brand-site.example/" in bound.identity_binding


def test_canonicalize_url은_fragment를_없앤다():
    assert canonicalize_url("https://Company.com/about#section") == "https://company.com/about"


def test_canonicalize_url은_추적파라미터를_뺀다():
    result = canonicalize_url("https://company.com/about?utm_source=x&id=1&gclid=y")
    assert result == "https://company.com/about?id=1"


def test_canonicalize_url은_쿼리_순서를_정렬한다():
    left = canonicalize_url("https://company.com/about?b=2&a=1")
    right = canonicalize_url("https://company.com/about?a=1&b=2")
    assert left == right


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


def test_robots_401도_빈_규칙으로_진행한다():
    outcome, _reason = robots_decision(_response(401), None)
    assert outcome == "proceed_empty_rules"


def test_robots_500은_차단된다():
    outcome, _reason = robots_decision(_response(500), None)
    assert outcome == "blocked"


def test_robots_전송실패는_차단된다():
    outcome, _reason = robots_decision(None, WideTransportError("시간초과"))
    assert outcome == "blocked"
