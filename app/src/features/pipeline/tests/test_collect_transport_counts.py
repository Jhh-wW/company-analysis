"""홈페이지·공식 IR PDF·광역 웹 세 수집기가 한 조사(scope) 안에서 host별
robots.txt 요청을 정확히 한 번만 하는지 확인하는 결합 카운터 시험(티켓 B2).

실측 결함: 세 수집기를 같은 회사 조사에서 차례로 불러도 각자 독립적으로
robots.txt를 확인했다 — 홈페이지+IR만 겹쳐도 apex·www 별칭까지 최대 4회,
광역 웹의 IR 위임까지 더하면 최악 16회. 이 시험은 세 수집기를 실제로
``request_deadline_scope`` 하나로 묶어 부르고, 요청 URL을 전부 하나의 로그에
기록해 host별 정확히 1회임을 잰다.

★ 실제 네트워크·DART·AI 호출은 0건이다. 세 수집기 각각의 Fetcher 타입에
  맞는 가짜 전송만 주입한다.
"""

from __future__ import annotations

import urllib.parse

from src.features.homepage.ir_pdf import (
    FetchedIrHtml,
    FetchedIrPdf,
    OfficialIrFetchError,
    collect_official_ir_fragments,
)
from src.features.homepage.logic import (
    FetchedPage,
    HomepageFetchError,
    HomepageRobotsUnavailable,
    collect_homepage_fragments,
)
from src.features.homepage.safe_http import request_deadline_scope
from src.features.homepage.wide_collect import collect_official_web_documents
from src.features.homepage.wide_fetch import WideRawResponse, WideTransportError

ROOT = "https://company.example"
ROBOTS_URL = f"{ROOT}/robots.txt"
ROBOTS_ALLOW_ALL = "User-agent: *\nAllow: /\n"
ROBOTS_DENY_ALL = "User-agent: *\nDisallow: /\n"
COMPANY_HOST = "company.example"


def _body(text: str) -> str:
    return "<html><body><main><p>" + (text + " ") * 10 + "</p></main></body></html>"


class _SharedFakeSite:
    """세 수집기가 함께 부를 가짜 전송 — 모든 요청 URL을 하나의 로그에 남긴다.

    ``pages``는 (URL → 본문) 매핑이다. robots.txt를 포함해 등록되지 않은
    URL은 전부 «접속 실패」로 취급한다 — 각 수집기가 기대하는 예외 타입으로
    변환해서 던진다(homepage.logic / ir_pdf / wide_fetch가 서로 다른 예외
    계층을 쓰기 때문).
    """

    def __init__(self, pages: dict[str, str]) -> None:
        self._pages = pages
        self.calls: list[str] = []

    # ── homepage.logic.Fetcher: (url) -> str ──
    def homepage_fetch(self, url: str) -> str:
        self.calls.append(url)
        if url not in self._pages:
            if url.endswith("/robots.txt"):
                raise HomepageRobotsUnavailable(f"가짜 robots.txt 없음: {url}")
            raise HomepageFetchError(f"가짜 접속 실패: {url}")
        return self._pages[url]

    # ── ir_pdf.IrHtmlFetcher: (url, expected_hostname, url_allowed) -> FetchedIrHtml ──
    def ir_html_fetch(self, url: str, _expected_hostname: str, _url_allowed) -> FetchedIrHtml:
        self.calls.append(url)
        if url not in self._pages:
            raise OfficialIrFetchError(f"가짜 IR HTML 없음: {url}")
        return FetchedIrHtml(self._pages[url], url)

    def ir_pdf_fetch(self, *_args: object, **_kwargs: object) -> FetchedIrPdf:
        raise OfficialIrFetchError("이 시험 픽스처에는 PDF 링크가 없습니다")

    # ── wide_fetch.RawWideTransport: (url, url_allowed) -> WideRawResponse ──
    def wide_transport(self, url: str, _url_allowed) -> WideRawResponse:
        self.calls.append(url)
        if url not in self._pages:
            raise WideTransportError(f"가짜 접속 실패: {url}")
        if url.endswith("/robots.txt"):
            content_type = "text/plain"
        elif url.endswith("/sitemap.xml"):
            content_type = "application/xml"
        else:
            content_type = "text/html"
        return WideRawResponse(
            status=200, text=self._pages[url], effective_url=url, content_type=content_type
        )


def _robots_hits_by_host(calls: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for url in calls:
        if not url.endswith("/robots.txt"):
            continue
        host = (urllib.parse.urlsplit(url).hostname or "").casefold()
        counts[host] = counts.get(host, 0) + 1
    return counts


def _run_all_three(site: _SharedFakeSite, *, company_name: str = "Example Company"):
    """홈페이지 → 공식 IR → 광역 웹 순서로, 같은 scope 안에서 세 수집기를 부른다."""

    with request_deadline_scope(5.0):
        homepage_result = collect_homepage_fragments(ROOT, fetch=site.homepage_fetch)
        ir_result = collect_official_ir_fragments(
            ROOT,
            company_name=company_name,
            html_fetch=site.ir_html_fetch,
            pdf_fetch=site.ir_pdf_fetch,
        )
        wide_result = collect_official_web_documents(
            company_id="c1",
            company_name=company_name,
            root_homepage_url=ROOT,
            collected_at="2026-08-31T00:00:00+00:00",
            transport=site.wide_transport,
            ir_html_fetch=site.ir_html_fetch,
            ir_pdf_fetch=site.ir_pdf_fetch,
        )
    return homepage_result, ir_result, wide_result


def test_한_scope_안에서_host별_robots_요청은_정확히_1회다() -> None:
    """정상 경로(robots 허용) — 홈페이지+IR+광역 세 곳이 합쳐 1회여야 한다.

    지금 코드는 홈페이지·IR·광역 web-crawl·광역 IR위임이 각자 독립적으로
    robots.txt를 확인해 4회가 나온다(티켓 B2 실측 최대치).
    """
    pages = {
        ROBOTS_URL: ROBOTS_ALLOW_ALL,
        ROOT: _body("공식 홈페이지 소개 문단"),
        f"{ROOT}/": _body("공식 홈페이지 소개 문단"),
    }
    site = _SharedFakeSite(pages)

    homepage_result, ir_result, wide_result = _run_all_three(site)

    assert homepage_result.state == "ok"
    assert ir_result.state in ("ok", "none")
    assert wide_result.documents or wide_result.attempts

    hits = _robots_hits_by_host(site.calls)
    assert hits.get(COMPANY_HOST, 0) == 1, (
        f"host={COMPANY_HOST} robots.txt 요청이 1회가 아님: "
        f"{[u for u in site.calls if u.endswith('robots.txt')]}"
    )


def test_robots가_차단하면_세_수집기_모두_본문을_요청하지_않는다() -> None:
    """robots 전체 차단 경로에서도 host별 robots 요청은 1회, 본문 요청은 0회."""
    pages = {ROBOTS_URL: ROBOTS_DENY_ALL}
    site = _SharedFakeSite(pages)

    homepage_result, ir_result, wide_result = _run_all_three(site)

    assert homepage_result.state == "failed"
    assert ir_result.state == "failed"

    hits = _robots_hits_by_host(site.calls)
    assert hits.get(COMPANY_HOST, 0) == 1, (
        f"host={COMPANY_HOST} robots.txt 요청이 1회가 아님: "
        f"{[u for u in site.calls if u.endswith('robots.txt')]}"
    )
    body_calls = [
        u
        for u in site.calls
        if (urllib.parse.urlsplit(u).hostname or "").casefold() == COMPANY_HOST
        and not u.endswith("/robots.txt")
        and not u.endswith("/sitemap.xml")
    ]
    assert body_calls == [], f"차단된 host에 본문 요청이 나감: {body_calls}"
