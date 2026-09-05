"""회사 공식 IR PDF 수집기의 경계·상한·원문 위치 회귀시험."""

from __future__ import annotations

import hashlib
import io
import json
import os
import signal
import socket
import subprocess
import threading
import time
from types import SimpleNamespace
import urllib.error
import urllib.request
from urllib import robotparser

import pytest
import pypdf.filters
from pypdf._codecs._codecs import LzwCodec
from pypdf.errors import LimitReachedError
from reportlab.pdfgen.canvas import Canvas

from src.features.homepage import _ir_pdf_worker as ir_pdf_worker
from src.features.homepage import ir_pdf, safe_http
from src.features.homepage.constants import (
    MAX_IR_CHARS_PER_DOCUMENT,
    MAX_IR_CHARS_PER_PAGE,
    MAX_IR_DOCUMENTS,
    MAX_IR_PDF_BYTES,
    MAX_IR_PDF_PAGES,
)
from src.features.homepage.ir_pdf import (
    FetchedIrHtml,
    FetchedIrPdf,
    OfficialIrFetchError,
    collect_official_ir_fragments,
)
from src.features.homepage.safe_http import UnsafeHomepageUrlError
from src.shared.official_ir import (
    IR_ATTACHMENT_URL_FIELD,
    IR_COLLECTED_ON_FIELD,
    IR_DART_WWW_REDIRECT_FIELD,
    IR_DART_WWW_REDIRECT_FROM_FIELD,
    IR_DART_WWW_REDIRECT_TO_FIELD,
    IR_DART_WWW_REDIRECT_VALUE,
    IR_METADATA_VERIFICATION_FIELD,
    IR_METADATA_VERIFICATION_VALUE,
    IR_METADATA_VERIFICATION_VALUE_COVER,
    IR_REPORTING_PERIOD_FIELD,
    extract_official_ir_anchor_metadata,
    extract_official_ir_cover_metadata,
    official_ir_time_is_usable,
    verified_official_ir_fragment_is_usable,
)


ROOT = "https://company.example/"
ROBOTS = "https://company.example/robots.txt"


def test_JYP형_anchor의_exact_ISO날짜와_분기만_문서메타로_바꾼다():
    assert extract_official_ir_anchor_metadata(
        "26년 2분기 IR자료 2026-08-12"
    ) == ("2026-08-12", "2026-Q2")
    assert extract_official_ir_anchor_metadata(
        "25년 4분기 IR자료 2026-03-10"
    ) == ("2026-03-10", "2025-Q4")
    # 다른 날짜가 두 개면 최신일을 임의로 고르지 않는다.
    assert extract_official_ir_anchor_metadata(
        "26년 2분기 2026-08-12 수정 2026-08-13"
    ) == ("", "2026-Q2")


def test_FY26_Q2_영문라벨은_연간이_아니라_2분기로_읽는다():
    assert extract_official_ir_anchor_metadata(
        "DATA & MATERIALS FY26 Q2 Earnings Result 2026-08-12"
    ) == ("2026-08-12", "2026-Q2")


@pytest.mark.parametrize(
    "date_text",
    ("2026.03.12", "2026-03-12", "2026년 3월 12일", "2026. 3. 12"),
)
def test_IR표지의_날짜표현과_보고기간을_닫힌값으로_읽는다(date_text):
    assert extract_official_ir_cover_metadata(
        [f"가나다전자 2025년 4분기 실적 발표 발행일 {date_text}"]
    ) == ("2026-03-12", "2025-Q4")


def test_IR표지의_서로다른_무표식날짜는_임의로_고르지_않는다():
    assert extract_official_ir_cover_metadata(
        ["2025년 4분기 실적 발표 2026.03.12 수정 2026.03.13"]
    ) == ("", "2025-Q4")


def test_IR표지의_기준일은_사업연도힌트로만_쓰고_작성일을_발행일로_쓴다():
    assert extract_official_ir_cover_metadata(
        ["기준일 2025.12.31 / 작성 2026.02.10"]
    ) == ("2026-02-10", "2025-FY")


def test_IR표지메타는_앞의_두쪽만_읽는다():
    assert extract_official_ir_cover_metadata(
        [
            "게시일 2026.03.12 / FY2025",
            "가나다전자 실적 발표 자료",
            "게시일 2026.03.13 / FY2024",
        ]
    ) == ("2026-03-12", "2025-FY")


@pytest.mark.parametrize(
    "verification",
    (IR_METADATA_VERIFICATION_VALUE, IR_METADATA_VERIFICATION_VALUE_COVER),
)
def test_옛anchor와_새표지_메타표식은_옛IR조각관문을_통과한다(verification):
    pdf_url = "https://company.example/ir/report.pdf"
    fragment = {
        "종류": "공식 IR",
        "발행처": "company.example",
        "도메인근거SourceID": "dart-company-profile-00126380",
        "도메인근거원문": json.dumps({"hm_url": ROOT}),
        "출처": pdf_url,
        IR_ATTACHMENT_URL_FIELD: pdf_url,
        "문서일": "2026-03-12",
        IR_REPORTING_PERIOD_FIELD: "2025-Q4",
        IR_METADATA_VERIFICATION_FIELD: verification,
        IR_COLLECTED_ON_FIELD: "2026-09-05",
    }

    assert verified_official_ir_fragment_is_usable(
        fragment, reference_date="2026-09-05"
    )


def test_IR자료실은_주가페이지보다_탐색우선순위가_높다():
    links = [
        ir_pdf._DiscoveredLink("https://company.example/IR/Stock", "주가", ROOT),
        ir_pdf._DiscoveredLink(
            "https://company.example/ko/board/ir-data", "IR 자료", ROOT
        ),
    ]
    assert sorted(links, key=ir_pdf._link_priority)[0].url.endswith("/ir-data")


def test_IR기간종료_발행일_수집일_순서와_발행시차를_검증한다():
    assert official_ir_time_is_usable(
        published_at="2026-08-12",
        reporting_period="2026-Q2",
        reference_date="2026-08-24",
    )
    assert not official_ir_time_is_usable(
        published_at="2026-08-12",
        reporting_period="2099-Q4",
        reference_date="2026-08-24",
    )
    assert not official_ir_time_is_usable(
        published_at="2026-08-12",
        reporting_period="2000-Q1",
        reference_date="2026-08-24",
    )
    assert not official_ir_time_is_usable(
        published_at="2026-06-15",
        reporting_period="2026-Q2",
        reference_date="2026-08-24",
    )


def _pdf_bytes(*pages: str) -> bytes:
    output = io.BytesIO()
    canvas = Canvas(output, pageCompression=0)
    for text in pages:
        y = 800
        for line in text.split("\n"):
            canvas.drawString(40, y, line)
            y -= 24
        canvas.showPage()
    canvas.save()
    return output.getvalue()


class _FakeSite:
    def __init__(
        self,
        *,
        html: dict[str, str],
        pdf: dict[str, FetchedIrPdf] | None = None,
        robots_text: str = "",
    ) -> None:
        self.html = html
        self.pdf = pdf or {}
        self.robots_text = robots_text
        self.html_calls: list[str] = []
        self.pdf_calls: list[str] = []
        self.html_policy_calls: list[tuple[str, bool]] = []

    def fetch_html(
        self,
        url: str,
        expected_hostname: str,
        url_allowed: ir_pdf.UrlAllowPredicate | None,
    ) -> FetchedIrHtml:
        assert expected_hostname == "company.example"
        self.html_calls.append(url)
        self.html_policy_calls.append((url, url_allowed is not None))
        if url == ROBOTS:
            assert url_allowed is None
            return FetchedIrHtml(self.robots_text, url)
        assert url_allowed is not None and url_allowed(url)
        if url not in self.html:
            raise OfficialIrFetchError("가짜 HTML 없음")
        return FetchedIrHtml(self.html[url], url)

    def fetch_pdf(
        self,
        url: str,
        expected_hostname: str,
        max_bytes: int,
        url_allowed: ir_pdf.UrlAllowPredicate,
    ) -> FetchedIrPdf:
        assert expected_hostname == "company.example"
        assert 0 < max_bytes <= MAX_IR_PDF_BYTES
        assert url_allowed(url)
        self.pdf_calls.append(url)
        if url not in self.pdf:
            raise OfficialIrFetchError("가짜 PDF 없음")
        return self.pdf[url]


def test_dart_hm_url자체가_비어있으면_공식source부재가_확정되어_scope가_완전하다():
    result = collect_official_ir_fragments("", company_name="")

    assert result.state == "none"
    assert result.candidate_scope_complete is True
    assert "DART 기업개황" in result.detail


def test_dart_hm_url이_비어있지않지만_안전하지않으면_scope가_불완전하다():
    result = collect_official_ir_fragments(
        "http://127.0.0.1/private",
            company_name="Example Company",
    )

    assert result.state == "none"
    assert result.candidate_scope_complete is False


def test_dart_http_hm_url은_같은_host의_https로만_강제해_탐색한다():
    site = _FakeSite(html={ROOT: "<html><body>IR 자료 없음</body></html>"})

    result = collect_official_ir_fragments(
        "http://company.example/",
        company_name="Example Company",
        html_fetch=site.fetch_html,
        pdf_fetch=site.fetch_pdf,
    )

    assert result.state == "none"
    assert result.candidate_scope_complete is True
    assert ROOT in site.html_calls
    assert all(not url.startswith("http://") for url in site.html_calls)


def test_IR_HTML과_PDF는_수집전체_deadline과_DNS_cache를_공유한다():
    pdf_url = "https://company.example/ir/results.pdf"
    site = _FakeSite(
        html={ROOT: '<a href="/ir/results.pdf">IR PDF</a>'},
        pdf={
            pdf_url: FetchedIrPdf(
                _pdf_bytes(
                        "Example Company investor relations. Beta is a principal competitor."
                ),
                pdf_url,
                "application/pdf",
            )
        },
    )
    budget_ids: list[int] = []

    def html_fetch(*args, **kwargs):
        budget = safe_http._ACTIVE_DEADLINE.get()
        assert budget is not None
        budget_ids.append(id(budget))
        return site.fetch_html(*args, **kwargs)

    def pdf_fetch(*args, **kwargs):
        budget = safe_http._ACTIVE_DEADLINE.get()
        assert budget is not None
        budget_ids.append(id(budget))
        return site.fetch_pdf(*args, **kwargs)

    result = collect_official_ir_fragments(
        ROOT,
        company_name="Example Company",
        html_fetch=html_fetch,
        pdf_fetch=pdf_fetch,
    )

    assert result.state == "ok"
    assert len(budget_ids) >= 3
    assert len(set(budget_ids)) == 1


def test_같은_https호스트_ir_pdf를_페이지문단별_공식조각으로_보존한다():
    pdf_url = "https://company.example/ir/2026-results.pdf?download=1"
    content = _pdf_bytes(
            "Example Company investor relations.\nAlpha competes with Beta in memory products.",
        "Gamma is a peer in the mobility market.",
    )
    site = _FakeSite(
        html={
            ROOT: (
                '<a href="/ir/2026-results.pdf?download=1">'
                "2026 실적발표 PDF</a>"
            )
        },
        pdf={
            pdf_url: FetchedIrPdf(content, pdf_url, "application/pdf")
        },
    )

    result = collect_official_ir_fragments(
        "company.example",
        company_name="Example Company",
        html_fetch=site.fetch_html,
        pdf_fetch=site.fetch_pdf,
    )

    assert result.state == "ok"
    assert result.candidate_scope_complete is True
    assert {fragment["종류"] for fragment in result.fragments} == {"공식 IR"}
    assert {fragment["출처"] for fragment in result.fragments} == {pdf_url}
    assert {fragment["문서ID"] for fragment in result.fragments} == {
        hashlib.sha256(content).hexdigest()
    }
    assert {fragment["문서명"] for fragment in result.fragments} == {
        "2026 실적발표 PDF"
    }
    assert {fragment["후보출처검증"] for fragment in result.fragments} == {
        "https_exact_dart_host"
    }
    alpha = next(item for item in result.fragments if "Alpha" in item["원문"])
    gamma = next(item for item in result.fragments if "Gamma" in item["원문"])
    assert alpha["원문위치"].startswith("PDF p.1 ")
    assert gamma["원문위치"].startswith("PDF p.2 ")
    assert alpha["원문위치"].endswith("· pypdf 6.16.1")
    assert "Gamma" not in alpha["원문"]
    assert "Alpha" not in gamma["원문"]
    assert site.html_policy_calls[:2] == [(ROBOTS, False), (ROOT, True)]


def test_DART결속_IR상세페이지가_직접_건_CDN_PDF만_날짜기간과_함께_봉인한다():
    detail_url = "https://company.example/ko/board/ir-data/view/123"
    attachment_url = "https://d1meds70430yck.cloudfront.net/jyp/2q26.pdf"
    content = _pdf_bytes(
        "JYP Ent. investor relations. The company operates artist management services."
    )
    site = _FakeSite(
        html={
            ROOT: (
                '<a href="/ko/board/ir-data/view/123">'
                "26년 2분기 IR자료 2026-08-12</a>"
            ),
            detail_url: f'<a href="{attachment_url}">PDF download</a>',
        }
    )

    def fetch_pdf(
        url: str,
        expected_hostname: str,
        max_bytes: int,
        url_allowed: ir_pdf.UrlAllowPredicate,
    ) -> FetchedIrPdf:
        assert url == attachment_url
        assert expected_hostname == "d1meds70430yck.cloudfront.net"
        assert 0 < max_bytes <= MAX_IR_PDF_BYTES
        assert url_allowed(url)
        return FetchedIrPdf(content, attachment_url, "application/pdf")

    result = collect_official_ir_fragments(
        ROOT,
        company_name="JYP Ent.",
        html_fetch=site.fetch_html,
        pdf_fetch=fetch_pdf,
    )

    assert result.state == "ok"
    assert result.fragments
    assert {item["출처"] for item in result.fragments} == {detail_url}
    assert {item[IR_ATTACHMENT_URL_FIELD] for item in result.fragments} == {
        attachment_url
    }
    assert {item["문서일"] for item in result.fragments} == {"2026-08-12"}
    assert {item[IR_REPORTING_PERIOD_FIELD] for item in result.fragments} == {
        "2026-Q2"
    }
    assert {item[IR_METADATA_VERIFICATION_FIELD] for item in result.fragments} == {
        IR_METADATA_VERIFICATION_VALUE
    }


def test_DART_apex가_실패하면_제한된_www_exact_host에서_IR을_한번_더_확인한다():
    apex_root = "https://company.example/"
    alias_root = "https://www.company.example/"
    alias_robots = "https://www.company.example/robots.txt"
    pdf_url = "https://www.company.example/ir/2q26.pdf"
    content = _pdf_bytes(
        "Example Company investor relations. The company supplies semiconductor equipment."
    )
    html_calls: list[tuple[str, str]] = []

    def fetch_html(
        url: str,
        expected_hostname: str,
        url_allowed: ir_pdf.UrlAllowPredicate | None,
    ) -> FetchedIrHtml:
        html_calls.append((url, expected_hostname))
        if expected_hostname == "company.example":
            raise OfficialIrFetchError("apex는 www로만 서비스됨")
        assert expected_hostname == "www.company.example"
        if url == alias_robots:
            return FetchedIrHtml("", alias_robots)
        assert url == alias_root
        assert url_allowed is not None and url_allowed(url)
        return FetchedIrHtml(
            '<a href="/ir/2q26.pdf">26년 2분기 IR자료 2026-08-12</a>',
            alias_root,
        )

    def fetch_pdf(
        url: str,
        expected_hostname: str,
        max_bytes: int,
        url_allowed: ir_pdf.UrlAllowPredicate,
    ) -> FetchedIrPdf:
        assert url == pdf_url
        assert expected_hostname == "www.company.example"
        assert 0 < max_bytes <= MAX_IR_PDF_BYTES
        assert url_allowed(url)
        return FetchedIrPdf(content, pdf_url, "application/pdf")

    result = collect_official_ir_fragments(
        apex_root,
        company_name="Example Company",
        html_fetch=fetch_html,
        pdf_fetch=fetch_pdf,
        allow_dart_www_alias=True,
        www_redirect_probe=lambda _apex, alias: alias,
    )

    assert result.state == "ok"
    assert "제한된 www 별칭" in result.detail
    assert ("https://company.example/robots.txt", "company.example") in html_calls
    assert (alias_robots, "www.company.example") in html_calls
    assert {fragment["출처"] for fragment in result.fragments} == {pdf_url}
    assert {fragment[IR_ATTACHMENT_URL_FIELD] for fragment in result.fragments} == {
        pdf_url
    }
    assert {
        (
            fragment[IR_DART_WWW_REDIRECT_FIELD],
            fragment[IR_DART_WWW_REDIRECT_FROM_FIELD],
            fragment[IR_DART_WWW_REDIRECT_TO_FIELD],
        )
        for fragment in result.fragments
    } == {
        (
            IR_DART_WWW_REDIRECT_VALUE,
            "company.example",
            "www.company.example",
        )
    }


@pytest.mark.parametrize(
    ("effective_url", "expected"),
    [
        ("https://www.company.example/", "https://www.company.example/"),
        ("https://company.example/", ""),
        ("https://ir.company.example/", ""),
    ],
)
def test_apex_probe는_실제_www_최종host만_승인한다(
    monkeypatch: pytest.MonkeyPatch,
    effective_url: str,
    expected: str,
) -> None:
    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def geturl(self) -> str:
            return effective_url

    def fake_safe_urlopen(
        request: urllib.request.Request,
        *,
        timeout: float,
        url_allowed: ir_pdf.UrlAllowPredicate,
    ) -> _Response:
        assert request.full_url == "https://company.example/"
        assert timeout > 0
        assert url_allowed("https://company.example/")
        assert url_allowed("https://www.company.example/")
        assert not url_allowed("https://ir.company.example/")
        return _Response()

    monkeypatch.setattr(ir_pdf, "safe_urlopen", fake_safe_urlopen)

    assert ir_pdf.default_dart_www_redirect_probe(
        "https://company.example/",
        "https://www.company.example/",
    ) == expected


def test_apex_redirect_probe가_없으면_www가_직접열려도_수집하지않는다():
    html_calls: list[tuple[str, str]] = []

    def fetch_html(
        url: str,
        expected_hostname: str,
        _url_allowed: ir_pdf.UrlAllowPredicate | None,
    ) -> FetchedIrHtml:
        html_calls.append((url, expected_hostname))
        if expected_hostname == "company.example":
            raise OfficialIrFetchError("apex 실패")
        return FetchedIrHtml("<p>www 직접 성공</p>", url)

    result = collect_official_ir_fragments(
        "https://company.example/",
        company_name="Example Company",
        html_fetch=fetch_html,
        allow_dart_www_alias=True,
        www_redirect_probe=lambda _apex, _alias: "",
    )

    assert result.state == "failed"
    assert all(host == "company.example" for _url, host in html_calls)


def test_ir자료실_한단계를_거쳐_pdf링크를_찾는다():
    ir_page = "https://company.example/investors/results"
    pdf_url = "https://company.example/files/q2-presentation.pdf"
    content = _pdf_bytes(
            "Example Company investor relations. Beta is our principal competitor in this segment."
    )
    site = _FakeSite(
        html={
            ROOT: '<a href="/investors/results">투자정보</a>',
            ir_page: '<a href="/files/q2-presentation.pdf">2Q presentation</a>',
        },
        pdf={pdf_url: FetchedIrPdf(content, pdf_url, "application/pdf")},
    )

    result = collect_official_ir_fragments(
        ROOT,
        company_name="Example Company",
        html_fetch=site.fetch_html,
        pdf_fetch=site.fetch_pdf,
    )

    assert result.state == "ok"
    assert ir_page in site.html_calls
    assert site.pdf_calls == [pdf_url]


def test_공식_newsroom의_ir_pdf는_검색결과가_아니므로_허용한다():
    pdf_url = "https://company.example/newsroom/ir/results.pdf"
    content = _pdf_bytes(
            "Example Company investor relations. Example Company competes with Beta in this market."
    )
    site = _FakeSite(
        html={ROOT: '<a href="/newsroom/ir/results.pdf">IR results PDF</a>'},
        pdf={pdf_url: FetchedIrPdf(content, pdf_url, "application/pdf")},
    )

    result = collect_official_ir_fragments(
        ROOT,
        company_name="Example Company",
        html_fetch=site.fetch_html,
        pdf_fetch=site.fetch_pdf,
    )

    assert result.state == "ok"
    assert result.candidate_scope_complete is True
    assert site.pdf_calls == [pdf_url]


def test_외부호스트_http와_뉴스검색_pdf는_다운로드하지_않는다():
    site = _FakeSite(
        html={
            ROOT: """
                <a href="https://cdn.example/ir/results.pdf">외부 IR PDF</a>
                <a href="http://company.example/ir/results.pdf">HTTP IR PDF</a>
                <a href="/news/search-result.pdf">뉴스 검색 PDF</a>
                <a href="/files/manual.pdf">일반 설명서 PDF</a>
            """
        }
    )

    result = collect_official_ir_fragments(
        ROOT,
        company_name="Example Company",
        html_fetch=site.fetch_html,
        pdf_fetch=site.fetch_pdf,
    )

    assert result.state == "none"
    assert result.candidate_scope_complete is True
    assert site.pdf_calls == []


def test_robots가_막은_ir_pdf는_다운로드하지_않는다():
    pdf_url = "https://company.example/ir/results.pdf"
    site = _FakeSite(
        html={ROOT: '<a href="/ir/results.pdf">IR results</a>'},
        pdf={
            pdf_url: FetchedIrPdf(
                _pdf_bytes("Example Company investor relations. Beta competitor"),
                pdf_url,
                "application/pdf",
            )
        },
        robots_text="User-agent: *\nDisallow: /ir/",
    )

    result = collect_official_ir_fragments(
        ROOT,
        company_name="Example Company",
        html_fetch=site.fetch_html,
        pdf_fetch=site.fetch_pdf,
    )

    assert result.state == "failed"
    assert result.candidate_scope_complete is False
    assert "일부 실패" in result.detail
    assert site.pdf_calls == []


def test_robots_4xx_명시적_부재는_빈규칙으로_계속한다():
    calls: list[str] = []

    def fetch_html(
        url: str,
        _expected_hostname: str,
        _url_allowed: ir_pdf.UrlAllowPredicate | None,
    ) -> FetchedIrHtml:
        calls.append(url)
        if url == ROBOTS:
            raise ir_pdf.OfficialIrRobotsUnavailable("HTTP 404")
        return FetchedIrHtml("<html><body>IR 자료 없음</body></html>", url)

    result = collect_official_ir_fragments(
        ROOT,
        company_name="Example Company",
        html_fetch=fetch_html,
        pdf_fetch=_FakeSite(html={}).fetch_pdf,
    )

    assert result.state == "none"
    assert result.candidate_scope_complete is True
    assert calls == [ROBOTS, ROOT]


def test_robots_서버나_네트워크_장애는_전면허용하지_않는다():
    calls: list[str] = []

    def fetch_html(
        url: str,
        _expected_hostname: str,
        _url_allowed: ir_pdf.UrlAllowPredicate | None,
    ) -> FetchedIrHtml:
        calls.append(url)
        raise ir_pdf.OfficialIrRobotsUnreachable("HTTP 503")

    result = collect_official_ir_fragments(
        ROOT,
        company_name="Example Company",
        html_fetch=fetch_html,
        pdf_fetch=_FakeSite(html={}).fetch_pdf,
    )

    assert result.state == "failed"
    assert result.candidate_scope_complete is False
    assert calls == [ROBOTS]


@pytest.mark.parametrize(
    ("status", "error_type"),
    [
        (404, ir_pdf.OfficialIrRobotsUnavailable),
        (503, ir_pdf.OfficialIrRobotsUnreachable),
    ],
)
def test_기본_robots_fetch는_4xx와_5xx를_구분한다(
    monkeypatch: pytest.MonkeyPatch,
    status: int,
    error_type: type[Exception],
) -> None:
    def fail(*_args: object, **_kwargs: object) -> object:
        raise urllib.error.HTTPError(ROBOTS, status, "가짜 오류", {}, None)

    monkeypatch.setattr(ir_pdf, "safe_urlopen_exact_https_host", fail)

    with pytest.raises(error_type):
        ir_pdf.default_ir_html_fetch(ROBOTS, "company.example", None)


def test_ir_html_하위페이지_실패는_자료없음이_아니라_불완전실패다():
    site = _FakeSite(
        html={ROOT: '<a href="/investors/results">투자정보</a>'}
    )

    result = collect_official_ir_fragments(
        ROOT,
        company_name="Example Company",
        html_fetch=site.fetch_html,
        pdf_fetch=site.fetch_pdf,
    )

    assert result.state == "failed"
    assert "일부 실패 1개(HTML 탐색)" in result.detail


def test_ir_html_탐색페이지_상한에_걸리면_완전한_none으로_판정하지않는다(
    monkeypatch,
):
    site = _FakeSite(
        html={ROOT: '<a href="/investors/results">투자정보</a>'}
    )
    monkeypatch.setattr(ir_pdf, "MAX_IR_DISCOVERY_PAGES", 1)

    result = collect_official_ir_fragments(
        ROOT,
        company_name="Example Company",
        html_fetch=site.fetch_html,
        pdf_fetch=site.fetch_pdf,
    )

    assert result.state == "failed"
    assert "탐색 상한 잘림" in result.detail


def test_html_최종redirect가_robots차단경로면_원문으로_쓰지않는다():
    robots_text = "User-agent: *\nDisallow: /private/"

    def fetch_html(
        url: str,
        expected_hostname: str,
        url_allowed: ir_pdf.UrlAllowPredicate | None,
    ) -> FetchedIrHtml:
        assert expected_hostname == "company.example"
        if url == ROBOTS:
            assert url_allowed is None
            return FetchedIrHtml(robots_text, url)
        assert url_allowed is not None and url_allowed(url)
        return FetchedIrHtml(
            '<a href="/ir/results.pdf">IR PDF</a>',
            "https://company.example/private/redirected.html",
        )

    result = collect_official_ir_fragments(
        ROOT,
        company_name="Example Company",
        html_fetch=fetch_html,
        pdf_fetch=lambda *_args: (_ for _ in ()).throw(
            AssertionError("PDF를 요청하면 안 됩니다")
        ),
    )

    assert result.state == "failed"
    assert result.fragments == []


def test_pdf_최종redirect가_robots차단경로면_바이트를_근거로_쓰지않는다():
    requested_url = "https://company.example/ir/results.pdf"
    blocked_url = "https://company.example/private/results.pdf"
    content = _pdf_bytes(
            "Example Company investor relations. Beta is a principal competitor."
    )
    site = _FakeSite(
        html={ROOT: '<a href="/ir/results.pdf">IR PDF</a>'},
        pdf={
            requested_url: FetchedIrPdf(content, blocked_url, "application/pdf")
        },
        robots_text="User-agent: *\nDisallow: /private/",
    )

    result = collect_official_ir_fragments(
        ROOT,
        company_name="Example Company",
        html_fetch=site.fetch_html,
        pdf_fetch=site.fetch_pdf,
    )

    assert result.state == "failed"
    assert result.fragments == []
    assert result.candidate_scope_complete is False


def test_pdf최종주소가_다른호스트면_가져온바이트도_버린다():
    pdf_url = "https://company.example/ir/results.pdf"
    site = _FakeSite(
        html={ROOT: '<a href="/ir/results.pdf">IR results</a>'},
        pdf={
            pdf_url: FetchedIrPdf(
                _pdf_bytes("Example Company investor relations. Beta competitor"),
                "https://cdn.example/results.pdf",
                "application/pdf",
            )
        },
    )

    result = collect_official_ir_fragments(
        ROOT,
        company_name="Example Company",
        html_fetch=site.fetch_html,
        pdf_fetch=site.fetch_pdf,
    )

    assert result.state == "failed"
    assert site.pdf_calls == [pdf_url]


@pytest.mark.parametrize(
    "invalid_case",
    ["매직", "MIME", "바이트"],
)
def test_mime_magic_바이트상한_하나라도_틀리면_pdf를_버린다(
    invalid_case: str,
):
    if invalid_case == "매직":
        content, content_type = b"not a pdf", "application/pdf"
    elif invalid_case == "MIME":
        content, content_type = (
                _pdf_bytes("Example Company investor relations. Beta competitor"),
            "application/octet-stream",
        )
    else:
        content, content_type = b"%PDF-" + b"x" * MAX_IR_PDF_BYTES, "application/pdf"
    pdf_url = "https://company.example/ir/results.pdf"
    site = _FakeSite(
        html={ROOT: '<a href="/ir/results.pdf">IR results</a>'},
        pdf={pdf_url: FetchedIrPdf(content, pdf_url, content_type)},
    )

    result = collect_official_ir_fragments(
        ROOT,
        company_name="Example Company",
        html_fetch=site.fetch_html,
        pdf_fetch=site.fetch_pdf,
    )

    assert result.state == "failed"
    assert result.fragments == []


def test_pdf문서수_상한만큼만_다운로드한다():
    urls = [f"https://company.example/ir/result-{index}.pdf" for index in range(6)]
    links = "".join(
        f'<a href="{url}">IR result {index}</a>'
        for index, url in enumerate(urls)
    )
    content = _pdf_bytes(
            "Example Company investor relations. Beta is a competitor in this market."
    )
    site = _FakeSite(
        html={ROOT: links},
        pdf={url: FetchedIrPdf(content, url, "application/pdf") for url in urls},
    )

    result = collect_official_ir_fragments(
        ROOT,
        company_name="Example Company",
        html_fetch=site.fetch_html,
        pdf_fetch=site.fetch_pdf,
    )

    assert result.state == "ok"
    assert len(site.pdf_calls) == MAX_IR_DOCUMENTS
    assert result.candidate_scope_complete is False


def test_pdf페이지_상한을_넘으면_일부페이지도_내보내지_않는다():
    pdf_url = "https://company.example/ir/too-many-pages.pdf"
    content = _pdf_bytes(
            *("Example Company investor relations. Beta competitor" for _ in range(MAX_IR_PDF_PAGES + 1))
    )
    site = _FakeSite(
        html={ROOT: '<a href="/ir/too-many-pages.pdf">IR results</a>'},
        pdf={pdf_url: FetchedIrPdf(content, pdf_url, "application/pdf")},
    )

    result = collect_official_ir_fragments(
        ROOT,
        company_name="Example Company",
        html_fetch=site.fetch_html,
        pdf_fetch=site.fetch_pdf,
    )

    assert result.state == "failed"
    assert result.fragments == []


def test_두번째페이지_파서실패시_첫페이지_일부결과도_버린다(monkeypatch):
    def fail_parse(_content: bytes):
        raise OfficialIrFetchError("두 번째 페이지 파서 실패")

    monkeypatch.setattr(ir_pdf, "_parse_pdf_with_timeout", fail_parse)
    pdf_url = "https://company.example/ir/broken.pdf"
    site = _FakeSite(
        html={ROOT: '<a href="/ir/broken.pdf">IR results</a>'},
        pdf={
            pdf_url: FetchedIrPdf(
                b"%PDF-fake parser input", pdf_url, "application/pdf"
            )
        },
    )

    result = collect_official_ir_fragments(
        ROOT,
        company_name="Example Company",
        html_fetch=site.fetch_html,
        pdf_fetch=site.fetch_pdf,
    )

    assert result.state == "failed"
    assert result.fragments == []


@pytest.mark.parametrize(
    "error_type",
    [RuntimeError, AttributeError, TypeError, ValueError],
)
def test_pdf문서별경계는_예상하지못한_수집기결함을_숨기지않는다(error_type):
    pdf_url = "https://company.example/ir/runtime-bug.pdf"
    site = _FakeSite(
        html={ROOT: '<a href="/ir/runtime-bug.pdf">IR results</a>'}
    )

    def broken_fetch(*_args: object, **_kwargs: object) -> FetchedIrPdf:
        raise error_type("가짜 수집기 결함")

    with pytest.raises(error_type, match="가짜 수집기 결함"):
        collect_official_ir_fragments(
            ROOT,
            company_name="Example Company",
            html_fetch=site.fetch_html,
            pdf_fetch=broken_fetch,
        )


def test_추출글자상한을_넘어도_문단경계를_섞지않고_상한에서_멈춘다(monkeypatch):
    first = "Company " + "A" * (MAX_IR_CHARS_PER_DOCUMENT + 1_000)
    second = "SECOND PARAGRAPH MUST NOT BE MERGED"

    monkeypatch.setattr(
        ir_pdf,
        "_parse_pdf_with_timeout",
        lambda _content: ir_pdf._ParsedPdf(
            pages=(f"{first}\n\n{second}",),
            extractor="pypdf 6.16.1",
            truncated_pages=frozenset(),
        ),
    )
    fetched = FetchedIrPdf(
        b"%PDF-long", "https://company.example/ir/long.pdf", "application/pdf"
    )

    extracted = ir_pdf._extract_pdf_fragments(
        fetched,
        source_url=fetched.effective_url,
        document_title="긴 IR 자료",
        remaining_total_chars=MAX_IR_CHARS_PER_DOCUMENT,
        identity_terms=frozenset({"company"}),
    )
    fragments = extracted.fragments

    assert sum(len(item["원문"]) for item in fragments) <= MAX_IR_CHARS_PER_DOCUMENT
    assert sum(len(item["원문"]) for item in fragments) <= MAX_IR_CHARS_PER_PAGE
    assert all("SECOND PARAGRAPH" not in item["원문"] for item in fragments)
    assert all("PDF p.1 1문단" in item["원문위치"] for item in fragments)
    assert extracted.truncated is True


def test_query_download_endpoint도_pdf표지와_최종mime으로_수집한다():
    pdf_url = "https://company.example/download?id=42&format=pdf"
    content = _pdf_bytes(
            "Example Company investor relations. Beta is a principal competitor."
    )
    site = _FakeSite(
        html={ROOT: '<a href="/download?id=42&format=pdf">2Q IR PDF</a>'},
        pdf={pdf_url: FetchedIrPdf(content, pdf_url, "application/pdf")},
    )

    result = collect_official_ir_fragments(
        ROOT,
        company_name="Example Company",
        html_fetch=site.fetch_html,
        pdf_fetch=site.fetch_pdf,
    )

    assert result.state == "ok"
    assert site.pdf_calls == [pdf_url]


def test_앞네페이지에_법인명이나_별칭이_없으면_공식ir로_승격하지않는다():
    pdf_url = "https://company.example/ir/unbound.pdf"
    content = _pdf_bytes(
        "Unrelated investor presentation.",
        "Beta is a principal competitor.",
        "Still unrelated on page three.",
        "Still unrelated on page four.",
        "Company appears too late on page five.",
    )
    site = _FakeSite(
        html={ROOT: '<a href="/ir/unbound.pdf">IR PDF</a>'},
        pdf={pdf_url: FetchedIrPdf(content, pdf_url, "application/pdf")},
    )

    result = collect_official_ir_fragments(
        ROOT,
        company_name="Example Company",
        html_fetch=site.fetch_html,
        pdf_fetch=site.fetch_pdf,
    )

    assert result.state == "failed"
    assert result.fragments == []


def test_앞네페이지의_법인별칭도_대상회사_결속으로_인정한다():
    pdf_url = "https://company.example/ir/alias.pdf"
    content = _pdf_bytes(
        "ACME investor relations. Beta is a principal competitor."
    )
    site = _FakeSite(
        html={ROOT: '<a href="/ir/alias.pdf">IR PDF</a>'},
        pdf={pdf_url: FetchedIrPdf(content, pdf_url, "application/pdf")},
    )

    result = collect_official_ir_fragments(
        ROOT,
        company_name="Company Incorporated",
        company_aliases=("ACME",),
        html_fetch=site.fetch_html,
        pdf_fetch=site.fetch_pdf,
    )

    assert result.state == "ok"


def test_JYP처럼_네번째페이지에_처음_법인명이_나와도_결속한다(monkeypatch):
    monkeypatch.setattr(
        ir_pdf,
        "_parse_pdf_with_timeout",
        lambda _content: ir_pdf._ParsedPdf(
            pages=(
                "Investor Relations",
                "Disclaimer and preliminary financial information.",
                "2026 Q2 Financial Results",
                "2026 Q2 Results JYP Investor Relations official company performance.",
            ),
            extractor="pypdf 6.16.1",
            truncated_pages=frozenset(),
        ),
    )
    terms = ir_pdf._company_identity_terms(
        "JYP Ent.",
        ("JYP Entertainment Corporation",),
        homepage_hostname="www.jype.com",
    )

    extracted = ir_pdf._extract_pdf_fragments(
        FetchedIrPdf(
            b"%PDF-jyp-layout",
            "https://www.jype.com/ir/jyp-layout.pdf",
            "application/pdf",
        ),
        source_url="https://www.jype.com/ir/jyp-layout.pdf",
        document_title="26Q2 IR PDF",
        remaining_total_chars=12_000,
        identity_terms=terms,
    )

    assert extracted.fragments


def test_짧은_JYP_약자는_DART공식도메인과_맞을때만_법인으로_결속한다():
    matched = ir_pdf._company_identity_terms(
        "JYP Ent.",
        ("JYP Entertainment Corporation",),
        homepage_hostname="www.jype.com",
    )
    unrelated = ir_pdf._company_identity_terms(
        "SK Innovation Co., Ltd.",
        ("SK",),
        homepage_hostname="www.skinnovation.com",
    )
    ordinary_word = ir_pdf._company_identity_terms(
        "The Example Co.",
        (),
        homepage_hostname="www.theco.com",
    )
    tld_only = ir_pdf._company_identity_terms(
        "XYZ Inc.",
        (),
        homepage_hostname="brand.xyz",
    )

    assert ir_pdf._identity_matches(("JYP Investor Relations",), matched)
    assert not ir_pdf._identity_matches(("SK Investor Relations",), unrelated)
    assert not ir_pdf._identity_matches(("The unrelated market report",), ordinary_word)
    assert not ir_pdf._identity_matches(("XYZ Investor Relations",), tld_only)


def test_OCR없이_추출글자0인_pdf는_failed_incomplete로_닫는다():
    pdf_url = "https://company.example/ir/blank.pdf"
    content = _pdf_bytes("")
    site = _FakeSite(
        html={ROOT: '<a href="/ir/blank.pdf">IR PDF</a>'},
        pdf={pdf_url: FetchedIrPdf(content, pdf_url, "application/pdf")},
    )

    result = collect_official_ir_fragments(
        ROOT,
        company_name="Example Company",
        html_fetch=site.fetch_html,
        pdf_fetch=site.fetch_pdf,
    )

    assert result.state == "failed"
    assert result.candidate_scope_complete is False
    assert "일부 실패 1개" in result.detail


def test_모든문단이_최소글자미만인_pdf도_failed_incomplete로_닫는다():
    pdf_url = "https://company.example/ir/short.pdf"
    content = _pdf_bytes("Company")
    site = _FakeSite(
        html={ROOT: '<a href="/ir/short.pdf">IR PDF</a>'},
        pdf={pdf_url: FetchedIrPdf(content, pdf_url, "application/pdf")},
    )

    result = collect_official_ir_fragments(
        ROOT,
        company_name="Example Company",
        html_fetch=site.fetch_html,
        pdf_fetch=site.fetch_pdf,
    )

    assert result.state == "failed"
    assert result.fragments == []
    assert result.candidate_scope_complete is False


def test_같은_content_sha_pdf는_한번만_근거로_쓴다():
    first_url = "https://company.example/ir/a.pdf"
    second_url = "https://company.example/ir/b.pdf"
    content = _pdf_bytes(
            "Example Company investor relations. Beta is a principal competitor."
    )
    site = _FakeSite(
        html={
            ROOT: (
                '<a href="/ir/a.pdf">IR A PDF</a>'
                '<a href="/ir/b.pdf">IR B PDF</a>'
            )
        },
        pdf={
            first_url: FetchedIrPdf(content, first_url, "application/pdf"),
            second_url: FetchedIrPdf(content, second_url, "application/pdf"),
        },
    )

    result = collect_official_ir_fragments(
        ROOT,
        company_name="Example Company",
        html_fetch=site.fetch_html,
        pdf_fetch=site.fetch_pdf,
    )

    assert result.state == "ok"
    assert {fragment["출처"] for fragment in result.fragments} == {first_url}
    assert "같은 콘텐츠 SHA-256 중복 1개 제외" in result.detail


def test_성공문서와_실패문서가_섞이면_ok와_부분실패_detail을_함께_남긴다():
    good_url = "https://company.example/ir/a-good.pdf"
    bad_url = "https://company.example/ir/b-bad.pdf"
    good = _pdf_bytes(
            "Example Company investor relations. Beta is a principal competitor."
    )
    site = _FakeSite(
        html={
            ROOT: (
                '<a href="/ir/a-good.pdf">IR good PDF</a>'
                '<a href="/ir/b-bad.pdf">IR bad PDF</a>'
            )
        },
        pdf={
            good_url: FetchedIrPdf(good, good_url, "application/pdf"),
            bad_url: FetchedIrPdf(b"not-pdf", bad_url, "application/pdf"),
        },
    )

    result = collect_official_ir_fragments(
        ROOT,
        company_name="Example Company",
        html_fetch=site.fetch_html,
        pdf_fetch=site.fetch_pdf,
    )

    assert result.state == "ok"
    assert result.candidate_scope_complete is False
    assert "일부 실패 1개" in result.detail


def test_pdf합계바이트_상한을_두번째요청에도_전달하고_정확히_추적한다(
    monkeypatch,
):
    urls = (
        "https://company.example/ir/a.pdf",
        "https://company.example/ir/b.pdf",
    )
    site = _FakeSite(
        html={
            ROOT: "".join(f'<a href="{url}">IR PDF</a>' for url in urls)
        }
    )
    limits: list[int] = []

    monkeypatch.setattr(ir_pdf, "MAX_IR_PDF_BYTES", 10)
    monkeypatch.setattr(ir_pdf, "MAX_IR_TOTAL_PDF_BYTES", 15)
    monkeypatch.setattr(
        ir_pdf,
        "_extract_pdf_fragments",
        lambda *args, **kwargs: ir_pdf._ExtractedDocument([]),
    )

    def fetch_pdf(
        url: str,
        expected_hostname: str,
        max_bytes: int,
        url_allowed: ir_pdf.UrlAllowPredicate,
    ) -> FetchedIrPdf:
        assert expected_hostname == "company.example"
        assert url_allowed(url)
        limits.append(max_bytes)
        suffix = b"a" if url.endswith("a.pdf") else b"b"
        return FetchedIrPdf(
            b"%PDF-" + suffix * (max_bytes - 5), url, "application/pdf"
        )

    result = collect_official_ir_fragments(
        ROOT,
        company_name="Example Company",
        html_fetch=site.fetch_html,
        pdf_fetch=fetch_pdf,
    )

    assert limits == [10, 5]
    assert result.downloaded_pdf_bytes == 15


def test_격리pdf파서는_10초_초과시_강제실패한다(monkeypatch):
    def time_out(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="pdf-worker", timeout=10)

    monkeypatch.setattr(ir_pdf, "_run_pdf_worker_bounded", time_out)

    with pytest.raises(OfficialIrFetchError, match="10초 상한 초과"):
        ir_pdf._parse_pdf_with_timeout(b"%PDF-timeout")


class _FakeBoundedWorkerProcess:
    def __init__(self, output: bytes, *, running: bool) -> None:
        self.stdout = io.BytesIO(output)
        self.returncode: int | None = None if running else 0
        self.kill_calls = 0
        self.wait_calls: list[float | None] = []
        self._done = threading.Event()
        if not running:
            self._done.set()

    def poll(self) -> int | None:
        return self.returncode if self._done.is_set() else None

    def kill(self) -> None:
        self.kill_calls += 1
        self.returncode = -9
        self._done.set()

    def wait(self, timeout: float | None = None) -> int:
        self.wait_calls.append(timeout)
        if not self._done.wait(timeout):
            raise subprocess.TimeoutExpired(cmd="가짜 PDF 워커", timeout=timeout)
        assert self.returncode is not None
        return self.returncode


def test_부모worker_runner는_reader시작실패에도_worker를_kill하고_reap한다(
    monkeypatch,
):
    process = _FakeBoundedWorkerProcess(b"", running=True)

    class _StartFailingThread:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def start(self) -> None:
            raise RuntimeError("가짜 reader 시작 실패")

        def is_alive(self) -> bool:
            return False

    monkeypatch.setattr(
        ir_pdf.subprocess,
        "Popen",
        lambda *_args, **_kwargs: process,
    )
    monkeypatch.setattr(ir_pdf.threading, "Thread", _StartFailingThread)

    with pytest.raises(OSError, match="reader를 시작하지 못했습니다"):
        ir_pdf._run_pdf_worker_bounded(
            ["python", "worker.py"],
            b"%PDF-reader-start-failure",
            timeout=0.5,
            max_output_bytes=8,
            creation_flags=0,
        )

    assert process.kill_calls == 1
    assert process.wait_calls == [ir_pdf.IR_PDF_WORKER_REAP_TIMEOUT_SEC]
    assert process.poll() == -9


def test_부모worker_runner는_stdout상한한바이트에서_즉시kill하고_reap한다(
    monkeypatch,
):
    process = _FakeBoundedWorkerProcess(b"x" * 9, running=True)
    captured: dict[str, object] = {}

    def fake_popen(command: list[str], **kwargs: object):
        captured["command"] = command
        captured.update(kwargs)
        input_file = kwargs["stdin"]
        assert hasattr(input_file, "read")
        assert input_file.read() == b"%PDF-bounded"
        input_file.seek(0)
        captured["cwd_exists"] = os.path.isdir(str(kwargs["cwd"]))
        return process

    monkeypatch.setattr(ir_pdf.subprocess, "Popen", fake_popen)

    completed = ir_pdf._run_pdf_worker_bounded(
        ["python", "worker.py"],
        b"%PDF-bounded",
        timeout=0.5,
        max_output_bytes=8,
        creation_flags=0,
    )

    assert completed.stdout == b"x" * 9
    assert completed.returncode == -9
    assert process.kill_calls == 1
    assert process.wait_calls == [0.5]
    assert captured["cwd_exists"] is True
    assert captured["close_fds"] is True
    assert captured["stdout"] is subprocess.PIPE
    environment = captured["env"]
    assert isinstance(environment, dict)
    assert environment["TEMP"] == captured["cwd"]
    assert environment["TMP"] == captured["cwd"]
    assert "ANTHROPIC_API_KEY" not in environment


def test_부모worker_runner는_timeout도_kill_wait_join후_예외를_낸다(monkeypatch):
    process = _FakeBoundedWorkerProcess(b"", running=True)
    monkeypatch.setattr(
        ir_pdf.subprocess,
        "Popen",
        lambda *_args, **_kwargs: process,
    )

    with pytest.raises(subprocess.TimeoutExpired):
        ir_pdf._run_pdf_worker_bounded(
            ["python", "worker.py"],
            b"%PDF-timeout",
            timeout=0.01,
            max_output_bytes=8,
            creation_flags=0,
        )

    assert process.kill_calls == 1
    assert process.wait_calls == [
        0.01,
        ir_pdf.IR_PDF_WORKER_REAP_TIMEOUT_SEC,
    ]
    assert process.poll() == -9


def test_부모worker_runner는_kill실패를_무기한wait로_숨기지않는다(monkeypatch):
    class _KillFailingProcess(_FakeBoundedWorkerProcess):
        def kill(self) -> None:
            self.kill_calls += 1
            raise PermissionError("가짜 TerminateProcess 실패")

    process = _KillFailingProcess(b"", running=True)
    read_fd, write_fd = os.pipe()
    blocking_stdout = os.fdopen(read_fd, "rb")
    process.stdout = blocking_stdout
    monkeypatch.setattr(
        ir_pdf.subprocess,
        "Popen",
        lambda *_args, **_kwargs: process,
    )

    errors: list[BaseException] = []
    finished = threading.Event()

    def run_worker() -> None:
        try:
            ir_pdf._run_pdf_worker_bounded(
                ["python", "worker.py"],
                b"%PDF-kill-failure",
                timeout=0.01,
                max_output_bytes=8,
                creation_flags=0,
            )
        except BaseException as exc:  # noqa: BLE001 - 교착 회귀의 결과를 부모에서 검증
            errors.append(exc)
        finally:
            finished.set()

    caller = threading.Thread(target=run_worker, daemon=True)
    caller.start()
    completed_within_bound = finished.wait(timeout=0.25)
    os.close(write_fd)
    caller.join(timeout=1.0)
    time.sleep(0.02)
    blocking_stdout.close()

    assert completed_within_bound is True
    assert len(errors) == 1
    assert isinstance(errors[0], OSError)
    assert "강제 종료하지 못했습니다" in str(errors[0])
    assert process.kill_calls >= 1
    assert process.wait_calls == [0.01]


class _FakeResource:
    RLIM_INFINITY = -1
    RLIMIT_AS = 1
    RLIMIT_CPU = 2

    def __init__(self) -> None:
        self.limits = {
            self.RLIMIT_AS: (self.RLIM_INFINITY, self.RLIM_INFINITY),
            self.RLIMIT_CPU: (self.RLIM_INFINITY, self.RLIM_INFINITY),
        }

    def getrlimit(self, kind: int) -> tuple[int, int]:
        return self.limits[kind]

    def setrlimit(self, kind: int, limits: tuple[int, int]) -> None:
        self.limits[kind] = limits


def test_Linux_worker는_pypdf_import전_RLIMIT_AS_CPU를_hard_limit으로_건다():
    fake = _FakeResource()

    ir_pdf_worker._configure_posix_resource_limits(
        256 * 1024 * 1024,
        8,
        resource_module=fake,
    )

    assert fake.limits[fake.RLIMIT_AS] == (256 * 1024 * 1024,) * 2
    assert fake.limits[fake.RLIMIT_CPU] == (8, 9)


def test_worker_main은_OS상한을_적용한뒤에만_pypdf를_import한다(monkeypatch):
    events: list[str] = []
    monkeypatch.setattr(
        ir_pdf_worker,
        "_arguments",
        lambda: SimpleNamespace(
            max_address_space_bytes=256 * 1024 * 1024,
            max_cpu_seconds=8,
            max_output_bytes=4 * 1024 * 1024,
            resource_policy="strict",
            expected_version="expected",
        ),
    )
    monkeypatch.setattr(
        ir_pdf_worker,
        "_configure_os_resource_limits",
        lambda *_args, **_kwargs: events.append("limits") or "applied",
    )
    monkeypatch.setattr(
        ir_pdf_worker,
        "_load_pdf_runtime",
        lambda: (
            events.append("pypdf") or SimpleNamespace(__version__="mismatch"),
            object(),
            object(),
        ),
    )
    monkeypatch.setattr(
        ir_pdf_worker,
        "_emit",
        lambda _payload, **_kwargs: None,
    )

    assert ir_pdf_worker.main() == 0
    assert events == ["limits", "pypdf"]


def test_worker_JSON직렬화는_UTF8_byte상한을_넘기기전에_중단한다():
    payload = {"state": "ok", "pages": ["가" * 8]}
    encoded = ir_pdf_worker._encode_payload_bounded(
        payload,
        max_output_bytes=128,
    )

    assert json.loads(encoded.decode("utf-8")) == payload
    with pytest.raises(ir_pdf_worker._WorkerOutputLimitError):
        ir_pdf_worker._encode_payload_bounded(
            payload,
            max_output_bytes=len(encoded) - 1,
        )


def test_Windows_job설정실패는_strict에서차단하고_명시적local에서만_fallback한다(
    monkeypatch,
):
    def fail_job(*_args, **_kwargs):
        raise OSError("중첩 Job Object 차단")

    monkeypatch.setattr(
        ir_pdf_worker,
        "_configure_windows_job_limits",
        fail_job,
    )

    with pytest.raises(OSError):
        ir_pdf_worker._configure_os_resource_limits(
            256 * 1024 * 1024,
            8,
            resource_policy=ir_pdf_worker.RESOURCE_POLICY_STRICT,
            platform_name="nt",
        )
    assert ir_pdf_worker._configure_os_resource_limits(
        256 * 1024 * 1024,
        8,
        resource_policy=ir_pdf_worker.RESOURCE_POLICY_LOCAL_WINDOWS,
        platform_name="nt",
    ) == ir_pdf_worker.RESOURCE_LIMITS_LOCAL_WINDOWS_FALLBACK


@pytest.mark.parametrize(
    ("deployment_mode", "expected"),
    [
        ("", "strict"),
        ("local", "local-windows"),
        ("production", "strict"),
    ],
)
def test_Windows_배포와_로컬_worker정책은_명시적으로_분리된다(
    deployment_mode: str,
    expected: str,
):
    assert ir_pdf._pdf_worker_resource_policy(
        platform_name="nt",
        deployment_mode=deployment_mode,
    ) == expected
    assert ir_pdf._pdf_worker_resource_policy(
        platform_name="posix",
        deployment_mode=deployment_mode,
    ) == "strict"


@pytest.mark.parametrize(
    ("failure_kind", "message"),
    [
        ("resource_limit_setup_failed", "OS 자원 상한 설정 실패"),
        ("resource_limit_exceeded", "OS 자원 상한 초과"),
    ],
)
def test_worker_자원상한_설정실패와_초과를_부모가_구분한다(
    monkeypatch,
    failure_kind: str,
    message: str,
):
    payload = json.dumps(
        {"state": "failed", "failure_kind": failure_kind, "detail": "닫힌 실패"}
    ).encode("utf-8")
    monkeypatch.setattr(
        ir_pdf,
        "_run_pdf_worker_bounded",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            args=[], returncode=0, stdout=payload
        ),
    )

    with pytest.raises(OfficialIrFetchError, match=message):
        ir_pdf._parse_pdf_with_timeout(b"%PDF-resource")


def _successful_worker_process() -> subprocess.CompletedProcess[bytes]:
    payload = json.dumps(
        {
            "state": "ok",
            "pages": ["Company investor relations"],
            "extractor": "pypdf 6.16.1",
            "truncated_pages": [],
            "resource_limits": "applied",
        }
    ).encode("utf-8")
    return subprocess.CompletedProcess(args=[], returncode=0, stdout=payload)


def test_pdf_worker명령은_isolated_no_bytecode와_출력상한을_고정한다(monkeypatch):
    captured: dict[str, object] = {}

    def successful(command: list[str], content: bytes, **kwargs: object):
        captured["command"] = command
        captured["content"] = content
        captured.update(kwargs)
        return _successful_worker_process()

    monkeypatch.setattr(ir_pdf, "_run_pdf_worker_bounded", successful)

    parsed = ir_pdf._parse_pdf_with_timeout(b"%PDF-command")

    command = captured["command"]
    assert isinstance(command, list)
    assert command[1:3] == ["-I", "-B"]
    assert command[command.index("--max-output-bytes") + 1] == str(
        ir_pdf.MAX_IR_WORKER_OUTPUT_BYTES
    )
    assert captured["max_output_bytes"] == ir_pdf.MAX_IR_WORKER_OUTPUT_BYTES
    assert captured["content"] == b"%PDF-command"
    assert parsed.pages == ("Company investor relations",)


def test_pdf_worker동시호출은_프로세스당_하나만_실행한다(monkeypatch):
    entered = threading.Event()
    unblock = threading.Event()
    lock = threading.Lock()
    active = 0
    max_active = 0
    calls = 0

    def controlled_run(*_args, **_kwargs):
        nonlocal active, max_active, calls
        with lock:
            calls += 1
            active += 1
            max_active = max(max_active, active)
            if calls == 1:
                entered.set()
        if calls == 1:
            assert unblock.wait(timeout=1)
        with lock:
            active -= 1
        return _successful_worker_process()

    monkeypatch.setattr(ir_pdf, "_run_pdf_worker_bounded", controlled_run)
    results: list[object] = []

    def parse() -> None:
        try:
            results.append(ir_pdf._parse_pdf_with_timeout(b"%PDF-concurrent"))
        except Exception as exc:  # pragma: no cover - assertion below exposes it
            results.append(exc)

    first = threading.Thread(target=parse)
    second = threading.Thread(target=parse)
    first.start()
    assert entered.wait(timeout=1)
    second.start()
    time.sleep(0.05)
    assert calls == 1
    unblock.set()
    first.join(timeout=2)
    second.join(timeout=2)

    assert max_active == 1
    assert calls == 2
    assert len(results) == 2
    assert all(isinstance(result, ir_pdf._ParsedPdf) for result in results)


def test_pdf_worker슬롯_대기timeout은_자식프로세스를_시작하지않는다(monkeypatch):
    class NoSlot:
        def acquire(self, *, timeout: int) -> bool:
            assert timeout == 1
            return False

        def release(self) -> None:
            raise AssertionError("얻지 못한 permit을 반환했습니다")

    monkeypatch.setattr(ir_pdf, "_PDF_WORKER_SLOTS", NoSlot())
    monkeypatch.setattr(
        ir_pdf,
        "_run_pdf_worker_bounded",
        lambda *_args, **_kwargs: pytest.fail("슬롯 없이 worker를 시작했습니다"),
    )

    with pytest.raises(OfficialIrFetchError, match="동시 실행 상한 대기 초과"):
        ir_pdf._parse_pdf_with_timeout(b"%PDF-no-slot")


def test_pdf_worker시작실패뒤에도_permit을_반환한다(monkeypatch):
    semaphore = threading.BoundedSemaphore(1)
    calls = 0

    def fail_then_succeed(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("시작 실패")
        return _successful_worker_process()

    monkeypatch.setattr(ir_pdf, "_PDF_WORKER_SLOTS", semaphore)
    monkeypatch.setattr(ir_pdf, "_run_pdf_worker_bounded", fail_then_succeed)

    with pytest.raises(OfficialIrFetchError, match="시작하지 못했습니다"):
        ir_pdf._parse_pdf_with_timeout(b"%PDF-first")
    parsed = ir_pdf._parse_pdf_with_timeout(b"%PDF-second")

    assert parsed.pages == ("Company investor relations",)
    assert calls == 2


def test_POSIX_CPU와_메모리_limit종료코드를_자원초과로_분류한다():
    assert ir_pdf._worker_exit_was_resource_limit(
        -getattr(signal, "SIGXCPU", 24), platform_name="posix"
    )
    assert ir_pdf._worker_exit_was_resource_limit(-9, platform_name="posix")


def test_격리worker는_lzw와_runlength폭탄도_같은스트림상한에서_차단한다():
    originals = {
        name: getattr(pypdf.filters, name)
        for name in ir_pdf_worker._REQUIRED_FILTER_CAPS
    }
    try:
        ir_pdf_worker._configure_filter_caps(8)
        assert all(
            getattr(pypdf.filters, name) == 8
            for name in ir_pdf_worker._REQUIRED_FILTER_CAPS
        )

        lzw_bomb = LzwCodec().encode(b"A" * 64)
        with pytest.raises(LimitReachedError):
            pypdf.filters.LZWDecode.decode(lzw_bomb)
        with pytest.raises(LimitReachedError):
            pypdf.filters.RunLengthDecode.decode(b"\xf6A\x80")
    finally:
        for name, value in originals.items():
            setattr(pypdf.filters, name, value)


def test_격리worker에_필수스트림상한_하나라도_없으면_버전계약실패다(
    monkeypatch,
):
    monkeypatch.delattr(pypdf.filters, "LZW_MAX_OUTPUT_LENGTH")

    with pytest.raises(RuntimeError, match="계약이 완전하지 않습니다"):
        ir_pdf_worker._configure_filter_caps(8)


def test_짧은영문법인별칭은_다른단어_일부와_겹쳐도_인정하지않는다():
    assert ir_pdf._identity_matches(("Market risk presentation",), frozenset({"sk"})) is False
    assert ir_pdf._identity_matches(("SK investor presentation",), frozenset({"sk"})) is True


def test_짧은_임의alias만_있는_pdf는_DART법인으로_결속하지않는다():
    terms = ir_pdf._company_identity_terms(
        "ACME Holdings Corporation",
        ("AI",),
    )

    assert not ir_pdf._identity_matches(("2020 AI Market Presentation",), terms)
    assert ir_pdf._identity_matches(
        ("ACME Holdings 2026 Investor Presentation",), terms
    )


def test_짧은_DART별칭도_principal공식명없이_단독결속하지않는다():
    terms = ir_pdf._company_identity_terms("SK Innovation Co., Ltd.", ("SK",))

    assert not ir_pdf._identity_matches(("SK investor presentation",), terms)
    assert ir_pdf._identity_matches(("SK Innovation investor presentation",), terms)


def test_짧은_DART법인명_core도_무관한_PDF를_결속하지않는다():
    terms = ir_pdf._company_identity_terms("주식회사 AI", ())

    assert not ir_pdf._identity_matches(("2020 AI Market Presentation",), terms)
    assert ir_pdf._identity_matches(("주식회사 AI 투자자 설명자료",), terms)


@pytest.mark.parametrize("company_name", ["AI", "US", "IT"])
def test_짧은_ASCII_DART공식명도_일반단어_PDF와_결속하지않는다(
    company_name: str,
) -> None:
    terms = ir_pdf._company_identity_terms(company_name, ())

    assert not terms.principal
    assert not ir_pdf._identity_matches(
        (f"2020 {company_name} Market Presentation",),
        terms,
    )


def test_일반어_공식별칭은_길어도_법인결속을_대체하지않는다():
    terms = ir_pdf._company_identity_terms("ACME Holdings Corporation", ("Market",))

    assert not ir_pdf._identity_matches(("Global Market Presentation",), terms)


def test_DART_영문공식명의_법적접미사제거_core도_결속에_쓴다():
    terms = ir_pdf._company_identity_terms(
        "삼성전자",
        ("SAMSUNG ELECTRONICS CO., LTD.",),
    )

    assert ir_pdf._identity_matches(
        ("Samsung Electronics Investor Presentation",),
        terms,
    )


class _FakePdfResponse:
    def __init__(self, body: bytes, headers: dict[str, str]) -> None:
        self.body = body
        self.headers = headers
        self.offset = 0
        self.read_sizes: list[int] = []

    def read1(self, size: int) -> bytes:
        self.read_sizes.append(size)
        chunk = self.body[self.offset : self.offset + size]
        self.offset += len(chunk)
        return chunk


def test_pdf선언크기가_남은합계상한보다_크면_본문을_읽기전에_거부한다():
    response = _FakePdfResponse(
        b"%PDF-not-read",
        {
            "Content-Type": "application/pdf",
            "Content-Length": "9",
        },
    )

    with pytest.raises(safe_http.HomepageResponseError, match="너무 큽니다"):
        ir_pdf._read_limited_pdf(response, timeout=1, max_bytes=8)

    assert response.read_sizes == []


def test_pdf실제본문도_남은합계상한_한바이트초과에서_멈춘다():
    response = _FakePdfResponse(
        b"%PDF-1234",
        {"Content-Type": "application/pdf"},
    )

    with pytest.raises(safe_http.HomepageResponseError, match="너무 큽니다"):
        ir_pdf._read_limited_pdf(response, timeout=1, max_bytes=8)

    assert sum(response.read_sizes) == 9


def _dns_answer(ip: str) -> list[tuple]:
    return [
        (
            socket.AF_INET,
            socket.SOCK_STREAM,
            socket.IPPROTO_TCP,
            "",
            (ip, 443),
        )
    ]


@pytest.mark.parametrize(
    "redirect_url",
    [
        "https://cdn.example/ir/results.pdf",
        "http://company.example/ir/results.pdf",
    ],
)
def test_pdf리다이렉트는_다른호스트와_https강등을_연결전에_거부한다(
    monkeypatch, redirect_url: str
):
    dns_calls: list[str] = []

    def fake_dns(host: str, *args, **kwargs):
        dns_calls.append(host)
        return _dns_answer("93.184.216.34")

    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        fake_dns,
    )
    handler = safe_http._ExactHttpsHostRedirectHandler("company.example")

    with pytest.raises(UnsafeHomepageUrlError):
        handler.redirect_request(
            urllib.request.Request(ROOT),
            None,
            302,
            "Found",
            {},
            redirect_url,
        )
    assert dns_calls == []


@pytest.mark.parametrize(
    ("start_url", "blocked_url"),
    [
        (
            "https://company.example/investors/results",
            "https://company.example/private/results",
        ),
        (
            "https://company.example/ir/results.pdf",
            "https://company.example/private/results.pdf",
        ),
    ],
)
def test_html과_pdf_redirect는_robots차단경로를_dns전에_거부한다(
    monkeypatch,
    start_url: str,
    blocked_url: str,
):
    robots = robotparser.RobotFileParser()
    robots.parse(["User-agent: *", "Disallow: /private/"])
    dns_calls: list[str] = []

    def fake_dns(host: str, *args, **kwargs):
        dns_calls.append(host)
        return _dns_answer("93.184.216.34")

    monkeypatch.setattr(socket, "getaddrinfo", fake_dns)
    handler = safe_http._ExactHttpsHostRedirectHandler(
        "company.example",
        url_allowed=lambda url: robots.can_fetch("GiupBunseokBot/1.0", url),
    )

    with pytest.raises(UnsafeHomepageUrlError, match="허용 규칙"):
        handler.redirect_request(
            urllib.request.Request(start_url),
            None,
            302,
            "Found",
            {},
            blocked_url,
        )

    assert dns_calls == []


def test_pdf초기주소가_사설망이면_소켓을열기전에_거부한다():
    with pytest.raises(UnsafeHomepageUrlError):
        safe_http.safe_urlopen_exact_https_host(
            urllib.request.Request("https://127.0.0.1/ir/results.pdf"),
            timeout=1,
            expected_hostname="127.0.0.1",
        )
