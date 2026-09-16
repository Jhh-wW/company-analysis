"""장별 최소 몫(page quota)과 회사 철학·윤리 경로의 슬롯 배정 회귀시험.

★ 왜 이 파일이 있나 (2026-09-17 실측 — 코스닥 소프트웨어사 운영 실행):
  한 사이트의 IR 공시 목록이 하위 링크를 계속 낳아 12쪽 예산 중 10쪽을
  ``/ir/…`` 한 갈래가 다 썼다. 그 10쪽은 전부 future_strategy·past_changes
  한 묶음으로만 분류돼, 8장(culture) 재료가 실린 CEO 인사말·윤리규범
  페이지는 후보 52개 중 15·16번째라 한 번도 열리지 않았다. 게다가 그
  페이지들은 «열렸더라도» identity·competitive_position 슬롯만 받아
  8장 칸으로는 한 조각도 가지 못했다. 두 결함을 각각 못 박는다.

접속은 하지 않는다 — 가짜 전송 계층만 주입한다. 실제 회사 사이트를 부르는
시험은 ``local_integration`` 마커를 달아 기본 묶음에서 빠진다.
"""

from __future__ import annotations

import os

import pytest

from src.features.homepage import wide_collect
from src.features.homepage.constants import WIDE_MAX_PAGES
from src.features.homepage.wide_collect import collect_official_web_documents
from src.features.homepage.wide_domain import classify_official_page_url
from src.features.homepage.wide_fetch import WideRawResponse, WideTransportError
from src.features.homepage.wide_fragments import build_fragments
from src.features.homepage.wide_types import WideDocumentIdentity

ROBOTS_ALLOW_ALL = "User-agent: *\nAllow: /\n"
_ROOT = "https://company.example"
_SHA = "a" * 64
_COMPANY_ID = "c1"

#: 8장 재료가 실린 두 경로. 운영 실측에서 예산 밖으로 밀렸던 바로 그 유형이다.
_CEO_PAGE = f"{_ROOT}/company/ceo-message/"
_ETHIC_PAGE = f"{_ROOT}/company/ethical/"
#: 근거가 될 수 없어 몫을 받으면 안 되는 경로(장을 알아낼 수 없는 URL).
_UNRELATED_PAGES = (
    f"{_ROOT}/support/contact/",
    f"{_ROOT}/privacy/",
    f"{_ROOT}/support/demo/",
)


class _FakeSite:
    """호출 URL을 기록하는 가짜 전송 계층. 없는 URL은 접속 실패로 본다."""

    def __init__(self, pages: dict[str, WideRawResponse]) -> None:
        self.pages = pages
        self.calls: list[str] = []

    def transport(self, url: str, url_allowed=None) -> WideRawResponse:
        self.calls.append(url)
        if url not in self.pages:
            raise WideTransportError(f"가짜 접속 실패: {url}")
        response = self.pages[url]
        if url_allowed is not None and not url_allowed(response.effective_url):
            raise WideTransportError(f"가짜 정책 차단: {url}")
        return response


def _page(text: str, url: str, content_type: str = "text/html") -> WideRawResponse:
    return WideRawResponse(
        status=200, text=text, effective_url=url, content_type=content_type
    )


def _body(text: str, links: tuple[str, ...] = ()) -> str:
    anchors = "".join(f'<a href="{href}">링크</a>' for href in links)
    return (
        "<html><body><main><p>"
        + (text + " ") * 10
        + "</p>"
        + anchors
        + "</main></body></html>"
    )


def _no_ir(url: str, *_args, **_kwargs):
    from src.features.homepage.ir_pdf import FetchedIrHtml, OfficialIrFetchError

    if url.endswith("/robots.txt"):
        return FetchedIrHtml("", url)
    raise OfficialIrFetchError("가짜 IR HTML 없음")


def _no_ir_pdf(*_args, **_kwargs):
    from src.features.homepage.ir_pdf import OfficialIrFetchError

    raise OfficialIrFetchError("가짜 IR PDF 없음")


def _crowded_site() -> _FakeSite:
    """한 갈래(IR 목록)가 예산을 다 먹을 수 있는 사이트를 그대로 흉내 낸다.

    IR 목록이 하위 공시 페이지를 계속 낳는 구조가 핵심이다 — 실제 사이트도
    ``/ir/`` → ``/ir/electronic/`` → 공시 20건으로 이어졌다.
    """

    disclosure_urls = tuple(
        f"{_ROOT}/ir/electronic/disclosure-{index:02d}/"
        for index in range(WIDE_MAX_PAGES * 2)
    )
    pages: dict[str, WideRawResponse] = {
        f"{_ROOT}/robots.txt": _page(
            ROBOTS_ALLOW_ALL, f"{_ROOT}/robots.txt", "text/plain"
        ),
        f"{_ROOT}/sitemap.xml": WideRawResponse(
            status=404, text="", effective_url=f"{_ROOT}/sitemap.xml", content_type=""
        ),
        f"{_ROOT}/": _page(
            _body(
                "회사 소개 첫 화면입니다",
                (
                    "/ir/",
                    "/ir/electronic/",
                    "/company/ceo-message/",
                    "/company/ethical/",
                    *(f"/support/contact/",),
                    "/privacy/",
                    "/support/demo/",
                ),
            ),
            f"{_ROOT}/",
        ),
        f"{_ROOT}/ir/": _page(
            _body("투자정보 목록입니다", ("/ir/electronic/",)), f"{_ROOT}/ir/"
        ),
        f"{_ROOT}/ir/electronic/": _page(
            _body("전자공고 목록입니다", disclosure_urls), f"{_ROOT}/ir/electronic/"
        ),
        _CEO_PAGE: _page(
            _body('"열심히 일하지 말자"라는 사훈 아래 자기주도적으로 일합니다'),
            _CEO_PAGE,
        ),
        _ETHIC_PAGE: _page(
            _body("임직원은 법과 원칙을 준수하고 윤리규범을 지킵니다"), _ETHIC_PAGE
        ),
    }
    for url in disclosure_urls:
        pages[url] = _page(_body("공시 원문 안내입니다"), url)
    for url in _UNRELATED_PAGES:
        pages[url] = _page(_body("문의와 개인정보 안내입니다"), url)
    return _FakeSite(pages)


def _collect(site: _FakeSite):
    return collect_official_web_documents(
        company_id=_COMPANY_ID,
        company_name="Example Company",
        root_homepage_url=_ROOT,
        collected_at="2026-09-17T00:00:00+00:00",
        transport=site.transport,
        ir_html_fetch=_no_ir,
        ir_pdf_fetch=_no_ir_pdf,
        root_identity_verification_required=False,
    )


def _fetched_pages(site: _FakeSite) -> list[str]:
    return [
        url
        for url in site.calls
        if not url.endswith("/robots.txt") and not url.endswith("/sitemap.xml")
    ]


# ── 재현: 8장 페이지가 예산 안으로 들어온다 ──────────────────


def test_한_갈래가_예산을_독점해도_8장_페이지를_읽는다():
    site = _crowded_site()

    _collect(site)

    fetched = _fetched_pages(site)
    assert _CEO_PAGE in fetched
    assert _ETHIC_PAGE in fetched
    # 예산 자체는 그대로다 — 몫은 «순서»만 바꾸고 상한을 늘리지 않는다.
    assert len(fetched) <= WIDE_MAX_PAGES


def test_음성대조_장별_몫을_끄면_8장_페이지가_예산_밖으로_밀린다(monkeypatch):
    """몫이 «실제로» 이 결과를 만드는지 확인하는 음성 대조.

    몫을 0으로 끄면 예전 동작(순수 우선순위)으로 돌아가고, 그때는 IR 갈래가
    예산을 다 먹어 8장 페이지가 한 쪽도 안 열려야 한다. 이 시험이 실패하면
    위 시험의 초록불은 몫이 아니라 다른 이유로 난 것이다.
    """

    monkeypatch.setattr(wide_collect, "WIDE_SECTION_PAGE_QUOTA", 0)
    site = _crowded_site()

    _collect(site)

    fetched = _fetched_pages(site)
    assert _CEO_PAGE not in fetched
    assert _ETHIC_PAGE not in fetched
    assert any("/ir/electronic/disclosure-" in url for url in fetched)


# ── 음성: 몫이 엉뚱한 페이지를 끌어오지 않는다 ────────────────


def test_장을_알_수_없는_페이지는_몫을_받지_않는다():
    site = _crowded_site()

    _collect(site)

    fetched = _fetched_pages(site)
    # 장을 못 알아낸 URL은 몫 대상이 아니므로, 장이 있는 후보가 아직
    # 몫을 못 채운 동안에는 한 쪽도 먼저 읽히지 않는다.
    assert [url for url in fetched if url in _UNRELATED_PAGES] == []


def test_한_장은_몫을_채운_뒤에야_더_가져간다():
    site = _crowded_site()

    _collect(site)

    fetched = _fetched_pages(site)
    first_extra_disclosure = next(
        index
        for index, url in enumerate(fetched)
        if "/ir/electronic/disclosure-" in url
    )
    # IR 갈래는 제 몫(목록 2쪽)을 쓴 뒤 멈추고, 다른 장이 몫을 챙긴 다음에야
    # 예산을 더 쓴다. 순서가 뒤집히면 예전처럼 한 갈래가 예산을 독점한다.
    assert first_extra_disclosure > fetched.index(_CEO_PAGE)
    assert first_extra_disclosure > fetched.index(_ETHIC_PAGE)


def test_남의_host는_문화_어휘_경로여도_계속_차단된다():
    site = _crowded_site()
    outsider = "https://other-company.example/company/ethical/"
    site.pages[f"{_ROOT}/"] = _page(
        _body("첫 화면", ("/company/ceo-message/", outsider)), f"{_ROOT}/"
    )
    site.pages[outsider] = _page(_body("남의 회사 윤리규범"), outsider)

    _collect(site)

    assert outsider not in site.calls
    assert "other-company.example" not in " ".join(site.calls)


# ── 매핑: 사훈·윤리 문장이 8장 칸으로 간다 ────────────────────


def _document(canonical_url: str, ranges: tuple[str, ...]) -> WideDocumentIdentity:
    return WideDocumentIdentity(
        company_id=_COMPANY_ID,
        document_id="d1",
        canonical_url=canonical_url,
        source_kind=classify_official_page_url(canonical_url).source_kind,
        publisher="company.example",
        title="제목",
        published_on="",
        collected_at="2026-09-17T00:00:00+00:00",
        content_sha256=_SHA,
        identity_binding="root",
        usable_ranges=ranges,
        collector_version="v1",
        parser_version="v1",
        requirement="REQUIRED",
        source_tier="TIER_1_OFFICIAL",
    )


@pytest.mark.parametrize(
    ("url", "text"),
    [
        (
            _CEO_PAGE,
            '비아이 예시는 "열심히 일하지 말자!"라는 사훈 아래, '
            "자기주도적이며 창의적으로 일하는 조직 문화를 만들어왔습니다.",
        ),
        (
            _ETHIC_PAGE,
            "임직원은 법과 원칙을 준수하고 높은 윤리적 가치관을 가지고 "
            "올바른 의사결정을 하도록 합니다.",
        ),
        (
            f"{_ROOT}/company/philosophy/",
            "핵심가치를 기준으로 일하는 방식을 정합니다.",
        ),
    ],
)
def test_회사_철학_윤리_경로의_문장은_culture_칸으로_간다(url: str, text: str):
    fragments = build_fragments(_document(url, (text,)), company_id=_COMPANY_ID)

    covered = {
        slot_id for fragment in fragments for slot_id in fragment.covered_slot_ids
    }
    assert "culture:work_principle" in covered


def test_회사_철학_경로도_회사소개_슬롯을_그대로_유지한다():
    document = _document(
        _CEO_PAGE,
        ("2005년 설립한 데이터 분석 전문기업이며 사훈을 지켜왔습니다.",),
    )

    covered = {
        slot_id
        for fragment in build_fragments(document, company_id=_COMPANY_ID)
        for slot_id in fragment.covered_slot_ids
    }
    assert "identity:corporate_identity" in covered
    assert "identity:business_definition" in covered


def test_회사_철학_경로는_채용_페이지_종류가_되지_않는다():
    classification = classify_official_page_url(_CEO_PAGE)

    assert classification.source_kind == "official_web_page"
    assert classify_official_page_url(f"{_ROOT}/careers/").source_kind == (
        "official_recruit_page"
    )


def test_음성_원칙_낱말이_없으면_culture_조각을_만들지_않는다():
    document = _document(
        _ETHIC_PAGE,
        ("문의는 아래 이메일로 연락주시기 바랍니다.",),
    )

    covered = {
        slot_id
        for fragment in build_fragments(document, company_id=_COMPANY_ID)
        for slot_id in fragment.covered_slot_ids
    }
    assert "culture:work_principle" not in covered


# ── 실제 사이트 (기본 묶음에서 제외) ─────────────────────────


@pytest.mark.local_integration
def test_local_integration_실제_사이트에서_8장_페이지를_읽는다():
    """실제 회사 홈페이지 하나로 끝까지 확인한다.

    대상 주소는 환경변수로 받는다 — 특정 회사·도메인을 저장소에 적지 않기
    위해서다. 설정하지 않으면 건너뛴다.

        HOMEPAGE_LOCAL_INTEGRATION_ROOT_URL=https://<회사 홈페이지>/
        HOMEPAGE_LOCAL_INTEGRATION_COMPANY_NAME=<법인명>
    """

    root_url = os.environ.get("HOMEPAGE_LOCAL_INTEGRATION_ROOT_URL", "").strip()
    company_name = os.environ.get(
        "HOMEPAGE_LOCAL_INTEGRATION_COMPANY_NAME", ""
    ).strip()
    if not root_url or not company_name:
        pytest.skip("HOMEPAGE_LOCAL_INTEGRATION_* 환경변수가 없습니다")

    result = collect_official_web_documents(
        company_id="local-integration",
        company_name=company_name,
        root_homepage_url=root_url,
        collected_at="2026-09-17T00:00:00+00:00",
        ir_html_fetch=_no_ir,
        ir_pdf_fetch=_no_ir_pdf,
        root_identity_verification_required=False,
    )

    sections = {
        slot_id.split(":", 1)[0]
        for document in result.documents
        for slot_id in classify_official_page_url(document.canonical_url).slot_ids
    }
    # 한 갈래가 예산을 독점하지 않았다는 최소 조건 — 장이 둘 이상이어야 한다.
    assert len(sections) >= 2, sections
