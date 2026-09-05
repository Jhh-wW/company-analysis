"""넓은 공식 웹 수집 오케스트레이션 회귀시험 — 실제 접속은 하지 않는다.

전송 계층(`WideRawResponse`를 돌려주는 가짜 `transport`)만 주입해 확인한다.
IR PDF 위임은 이미 `test_ir_pdf.py`가 그 내부 로직을 검증하므로, 여기서는
`collect_official_ir_fragments` 자체를 가짜로 바꿔 «이 모듈이 결과를 올바르게
문서로 바꾸고 상한을 지키는지»만 확인한다.
"""

from __future__ import annotations

import hashlib
import urllib.parse
from dataclasses import replace

import pytest

from src.features.chapter_evidence.constants import CompanyType
from src.features.chapter_evidence.produce import produce_from_collection_envelopes
from src.features.company_comparison.official_sources import (
    dart_profile_attestation_material,
)
from src.features.composer import render as composer_render
from src.features.composer.port import filing_meta_from_raw
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
from src.features.pipeline.evidence_transport import build_section_evidence_packet_set
from src.features.pipeline.official_evidence_transport_adapter import (
    merge_official_evidence_fragments,
)
from src.features.provenance.sources import (
    ensure_dart_profile_attesters,
    full_typed_source_registry_problem,
    has_valid_provenance_seal,
    seal_collected_source,
)
from src.shared.report_evidence.legacy_fragment_kinds import LEGACY_FRAGMENT_KINDS
from src.shared.report_evidence.runtime_port import OfficialEvidenceCollectionResult
from src.web.official_evidence_adapter import (
    provenance_documents_from_wide_envelope,
)
from src.shared.report_evidence.identity_verified_web import (
    build_dart_filing_url_provenance,
    parse_verified_dart_filing_subdomain_binding,
)

ROBOTS_ALLOW_ALL = "User-agent: *\nAllow: /\n"


def _dart_provenance(candidate: str) -> tuple[str, str]:
    receipt = "20260000000001"
    return (
        candidate,
        build_dart_filing_url_provenance(
            company_id="c1",
            url=candidate,
            source_document_id=f"dart_audit_report:{receipt}",
            source_receipt_no=receipt,
            source_member_name="cover.xml",
            source_location="raw_xml_chars:10-40",
            source_document_sha256="a" * 64,
            source_payload_sha256="b" * 64,
        ),
    )


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
        # 이 파일의 기존 단위시험은 root 결속 이후 robots·scope·상한을 따로
        # 검증한다. 정식 운영의 root 신원 gate는 아래 전용 공격시험과 adapter
        # 통합시험에서 실제로 켠다.
        root_identity_verification_required=False,
    )
    kwargs.update(overrides)
    return collect_official_web_documents(**kwargs)


def _body(text: str) -> str:
    return "<html><body><main><p>" + (text + " ") * 10 + "</p></main></body></html>"


def _identity_body(text: str, *, number: str = "123-45-67890", extra: str = "") -> str:
    """본문과 footer의 DART 신원 이중 표식을 함께 가진 공식 후보 페이지."""

    return (
        "<html><body><main><p>"
        + (text + " ") * 10
        + "</p>"
        + extra
        + "</main><footer>주식회사 와이즐리컴퍼니 · 사업자등록번호 "
        + number
        + "</footer></body></html>"
    )


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
    # apex/www 짝 결속 이후 primary(company.example)와 apex/www
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
    """robots 401은 인증 요구다 — 빈 규칙으로 진행하면 안 된다
    (수집 흐름 전체를 통과시키는 회귀 방지)."""
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
    """robots_decision의 단위 분류(407·408·409·429 전부 blocked)를
    상태마다 실제 수집 전체로 증명한다.
    「robots가 아닌」 전송 호출 수를 세어 정말 0인지 확인한다 — 특정 URL
    문자열이 calls 안에 없다는 것만으로는 다른 형태의 우회 호출을 놓칠 수
    있어, 407·429 두 상태만으로는 증명이 불완전하다."""
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


def test_DART_root의_채용하위도메인_조각은_낮은수준_Source계약과_변조검사를_통과한다():
    """하위도메인 proof의 packet·Source 단위계약과 부정 대조만 격리한다.

    여기의 다른 장 legacy 조각은 packet 생성에 필요한 명시적 단위 fixture다.
    실제 collector→공개 FULL 성공과 자동 attester 생성은 수동 보충이 없는
    ``web/tests/test_public_boundary_full_evidence_e2e.py``가 소유한다.
    """

    profile = {
        "status": "000",
        "corp_code": "00126380",
        "corp_name": "가나다전자",
        "hm_url": "https://company.example/",
    }
    attestation_id, attestation_evidence = dart_profile_attestation_material(
        profile=profile,
        corp_code="00126380",
        company_name="가나다전자",
    )
    root_html = (
        "<html><body><main><p>가나다전자는 반도체 검사 장비를 제조하고 "
        "기업 고객에게 판매하여 매출을 얻습니다.</p>"
        '<a href="https://recruit.company.example/jobs">채용</a>'
        "</main><footer>가나다전자 · 사업자등록번호 123-45-67890</footer>"
        "</body></html>"
    )
    recruit_html = (
        "<html><body><main><p>가나다전자는 책임을 핵심가치와 일하는 방식으로 "
        "정하고 협업 프로젝트 사례를 운영해 개선한 기록을 공개합니다. "
        "가나다전자는 책임을 핵심가치와 일하는 방식으로 정합니다.</p>"
        "</main></body></html>"
    )
    pages = {
        "https://company.example/robots.txt": _missing(
            "https://company.example/robots.txt"
        ),
        "https://company.example/sitemap.xml": _missing(
            "https://company.example/sitemap.xml"
        ),
        "https://company.example/": _page(root_html, "https://company.example/"),
        "https://recruit.company.example/robots.txt": _missing(
            "https://recruit.company.example/robots.txt"
        ),
        "https://recruit.company.example/sitemap.xml": _missing(
            "https://recruit.company.example/sitemap.xml"
        ),
        "https://recruit.company.example/jobs": _page(
            recruit_html,
            "https://recruit.company.example/jobs",
        ),
    }
    result = collect_official_web_documents(
        company_id="00126380",
        company_name="가나다전자",
        root_homepage_url="https://company.example/",
        company_registration_numbers=("123-45-67890",),
        collected_at="2026-09-04",
        domain_attestation_source_id=attestation_id,
        domain_attestation_evidence=attestation_evidence,
        transport=_FakeWideSite(pages).transport,
        ir_html_fetch=_no_ir,
        ir_pdf_fetch=_no_ir_pdf,
    )
    wide_fragments = build_fragments_for_collection(result)
    envelope = to_evidence_mappings(result=result, fragments=wide_fragments)
    candidates = produce_from_collection_envelopes(
        company_id="00126380",
        company_type=CompanyType.AUDIT_ONLY,
        collection_envelopes=(envelope,),
    )
    official = OfficialEvidenceCollectionResult(
        company_id="00126380",
        candidates=candidates,
    )
    legacy = {
        number: {"종류": kind, "원문": f"{kind}의 독립 회귀용 공식 원문입니다."}
        for number, kind in enumerate(sorted(LEGACY_FRAGMENT_KINDS), start=1)
    }
    merged, _added = merge_official_evidence_fragments(legacy, official)
    packet_set = build_section_evidence_packet_set(
        corp_id="00126380",
        source_generation_sha256=official.source_snapshot_sha256,
        frags=merged,
        filing_meta=filing_meta_from_raw(
            {
                "rcept_no": "20260315000123",
                "report_nm": "사업보고서 (2025.12)",
                "rcept_dt": "20260315",
            }
        ),
    )
    recruit_fragments = {
        fragment
        for packet in packet_set.packets
        for fragment in packet.fragments
        if "recruit.company.example" in fragment.source_url
    }
    assert recruit_fragments
    fragment = sorted(recruit_fragments, key=lambda item: item.fragment_id)[0]
    meta = composer_render._fragment_metas((fragment,))[0]  # noqa: SLF001
    source = composer_render._build_source(  # noqa: SLF001
        meta,
        int(fragment.fragment_id),
        "가나다전자",
        ["culture"],
    )
    registry = ensure_dart_profile_attesters(
        (source,),
        company_name="가나다전자",
    )
    assert len(registry) == 2
    attester = next(item for item in registry if item.provenance_role == "attestation_only")

    assert source.host == "recruit.company.example"
    assert has_valid_provenance_seal(source)
    assert full_typed_source_registry_problem(
        source,
        registry,
        reference_date="2026-09-04",
    ) == ""

    tampered = seal_collected_source(
        replace(
            source,
            url="https://jobs.company.example/jobs",
            host="jobs.company.example",
            provenance_seal="",
        )
    )
    assert full_typed_source_registry_problem(
        tampered,
        (tampered, attester),
        reference_date="2026-09-04",
    )
    with pytest.raises(ValueError, match="중복 source_id"):
        ensure_dart_profile_attesters((source, source), company_name="가나다전자")
    forged_proof = seal_collected_source(
        replace(source, domain_attestation_evidence="{}", provenance_seal="")
    )
    with pytest.raises(ValueError, match="의존성이 손상"):
        ensure_dart_profile_attesters(
            (forged_proof,),
            company_name="가나다전자",
        )


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


def test_다른_등록도메인도_DART_법인명과_사업자번호를_실제본문에서_확인하면_수집한다():
    """회사별 도메인 allowlist 없이 동일한 신원 규칙으로 새 자사몰을 살린다."""

    root = "https://old-company.example"
    shop = "https://wise-shop.example"
    pages = {
        f"{root}/robots.txt": _page(ROBOTS_ALLOW_ALL, f"{root}/robots.txt", "text/plain"),
        f"{root}/sitemap.xml": _missing(f"{root}/sitemap.xml"),
        f"{root}/": _page(
            _body("과거 회사 안내 페이지") + f'<a href="{shop}/">새 공식 자사몰</a>',
            f"{root}/",
        ),
        "https://www.old-company.example/robots.txt": _missing(
            "https://www.old-company.example/robots.txt"
        ),
        "https://www.old-company.example/sitemap.xml": _missing(
            "https://www.old-company.example/sitemap.xml"
        ),
        "https://www.old-company.example/": _missing(
            "https://www.old-company.example/"
        ),
        f"{shop}/robots.txt": _missing(f"{shop}/robots.txt"),
        f"{shop}/": _page(
            _identity_body(
                "면도용품과 생활용품을 제조 및 판매하는 주요 사업을 영위합니다",
                extra='<a href="/products">제품군</a>',
            ),
            f"{shop}/",
        ),
        f"{shop}/products": _page(
            _body("대표 제품과 제품군을 개인 고객에게 판매해 매출을 얻습니다"),
            f"{shop}/products",
        ),
    }
    site = _FakeWideSite(pages)

    result = _collect(
        site,
        company_name="주식회사 와이즐리컴퍼니",
        company_aliases=("Wisely Co., Ltd.",),
        company_registration_numbers=("123-45-67890",),
        root_homepage_url=root,
    )

    urls = {document.canonical_url for document in result.documents}
    assert f"{shop}/" in urls
    assert f"{shop}/products" in urls
    cross_documents = [
        document for document in result.documents if "wise-shop.example" in document.canonical_url
    ]
    assert cross_documents
    assert all(document.requirement == "OPTIONAL" for document in cross_documents)
    assert all(document.source_tier == "TIER_3_TRUSTED" for document in cross_documents)
    assert all("등록번호" in document.identity_binding for document in cross_documents)
    cross_ids = {document.document_id for document in cross_documents}
    assert not any(
        fragment.document_id in cross_ids
        for fragment in build_fragments_for_collection(result)
    )


@pytest.mark.parametrize(
    ("candidate_html", "wrong_reason"),
    (
        (
            _body("주식회사 와이즐리컴퍼니의 상품을 소개합니다"),
            "회사명만 일치",
        ),
        (
            _identity_body("생활용품 판매", number="999-99-99999"),
            "다른 사업자번호",
        ),
        (
            _identity_body("생활용품 판매").replace(
                "주식회사 와이즐리컴퍼니", "주식회사 다른컴퍼니"
            ),
            "다른 법인명",
        ),
    ),
)
def test_공식root가_직접_링크해도_강한_신원_둘중_하나가_없으면_승격하지_않는다(
    candidate_html: str,
    wrong_reason: str,
):
    del wrong_reason  # parametrized case 이름을 읽기 쉽게 남기는 설명값
    root = "https://old-company.example"
    candidate = "https://vendor.example"
    pages = {
        f"{root}/robots.txt": _missing(f"{root}/robots.txt"),
        f"{root}/sitemap.xml": _missing(f"{root}/sitemap.xml"),
        f"{root}/": _page(
            _body("회사 안내") + f'<a href="{candidate}/">외부 링크</a>', f"{root}/"
        ),
        "https://www.old-company.example/robots.txt": _missing(
            "https://www.old-company.example/robots.txt"
        ),
        "https://www.old-company.example/sitemap.xml": _missing(
            "https://www.old-company.example/sitemap.xml"
        ),
        "https://www.old-company.example/": _missing(
            "https://www.old-company.example/"
        ),
        f"{candidate}/robots.txt": _missing(f"{candidate}/robots.txt"),
        f"{candidate}/": _page(candidate_html, f"{candidate}/"),
    }
    site = _FakeWideSite(pages)

    result = _collect(
        site,
        company_name="주식회사 와이즐리컴퍼니",
        company_registration_numbers=("123-45-67890",),
        root_homepage_url=root,
    )

    assert f"{candidate}/" in site.calls, "격리 exact 후보 한 건은 실제 본문을 확인한다"
    assert not any("vendor.example" in doc.canonical_url for doc in result.documents)
    mismatch = [
        attempt
        for attempt in result.attempts
        if attempt.reason_code == "cross_domain_identity_mismatch"
    ]
    assert mismatch and all(attempt.requirement == "OPTIONAL" for attempt in mismatch)


def test_전송계층이_범위밖_redirect를_놓쳐도_신원검증_경계가_다시_거절한다():
    """법인명+번호를 복사한 외부 본문이 원래 후보 host를 결속할 수 없다."""

    candidate = "https://official-candidate.example/company"
    attacker = "https://attacker.example/copied-company"
    calls: list[str] = []

    def unsafe_transport(url: str, _url_allowed):
        # 의도적으로 url_allowed를 무시하는 결함 있는 transport 대역이다.
        # 조립 경계도 effective_url을 독립 확인해야 이 회귀를 막는다.
        calls.append(url)
        if url == "https://official-candidate.example/robots.txt":
            return _missing(url)
        if url == candidate:
            return _page(
                _identity_body("복사한 회사 소개와 대표 제품 정보"),
                attacker,
            )
        raise WideTransportError(f"예상 밖 호출: {url}")

    result = collect_official_web_documents(
        company_id="c1",
        company_name="주식회사 와이즐리컴퍼니",
        root_homepage_url="",
        collected_at="2026-08-31T00:00:00+00:00",
        company_registration_numbers=("123-45-67890",),
        official_candidate_urls=(candidate,),
        root_identity_verification_required=True,
        transport=unsafe_transport,
        ir_html_fetch=_no_ir,
        ir_pdf_fetch=_no_ir_pdf,
    )

    assert candidate in calls
    assert result.documents == ()
    assert any(
        attempt.reason_code == "redirect_scope_mismatch"
        and attempt.state == "FAILED"
        for attempt in result.attempts
    )


def test_DART_sidecar_provenance만으로는_타사페이지를_공식으로_승격하지_않는다():
    candidate = "https://directory.example/company/wisely"
    pages = {
        "https://directory.example/robots.txt": _missing(
            "https://directory.example/robots.txt"
        ),
        candidate: _page(
            _identity_body("다른 회사 정보", number="999-99-99999"),
            candidate,
        ),
    }
    site = _FakeWideSite(pages)

    result = collect_official_web_documents(
        company_id="c1",
        company_name="주식회사 와이즐리컴퍼니",
        root_homepage_url="",
        collected_at="2026-08-31T00:00:00+00:00",
        company_registration_numbers=("123-45-67890",),
        official_candidate_provenance=(_dart_provenance(candidate),),
        root_identity_verification_required=True,
        transport=site.transport,
        ir_html_fetch=_no_ir,
        ir_pdf_fetch=_no_ir_pdf,
    )

    assert candidate in site.calls
    assert result.documents == ()
    assert any(
        attempt.reason_code == "cross_domain_identity_mismatch"
        for attempt in result.attempts
    )


def test_DART_sidecar로_검증한_root가_직접건_채용하위도메인은_strict_proof를_이어받는다():
    root = "https://company.example/"
    recruit = "https://recruit.company.example/jobs"
    pages = {
        "https://company.example/robots.txt": _missing(
            "https://company.example/robots.txt"
        ),
        root: _page(
            _identity_body(
                "공식 회사 안내",
                extra=f'<a href="{recruit}">채용</a>',
            ),
            root,
        ),
        "https://recruit.company.example/robots.txt": _missing(
            "https://recruit.company.example/robots.txt"
        ),
        "https://recruit.company.example/sitemap.xml": _missing(
            "https://recruit.company.example/sitemap.xml"
        ),
        recruit: _page(
            _body(
                "핵심가치와 일하는 방식을 협업 프로젝트 사례에 적용해 개선했습니다"
            ),
            recruit,
        ),
    }
    result = collect_official_web_documents(
        company_id="c1",
        company_name="주식회사 와이즐리컴퍼니",
        root_homepage_url="",
        collected_at="2026-09-04",
        company_registration_numbers=("123-45-67890",),
        official_candidate_provenance=(_dart_provenance(root),),
        root_identity_verification_required=True,
        transport=_FakeWideSite(pages).transport,
        ir_html_fetch=_no_ir,
        ir_pdf_fetch=_no_ir_pdf,
    )

    recruit_document = next(
        document
        for document in result.documents
        if document.canonical_url == recruit
    )
    proof = parse_verified_dart_filing_subdomain_binding(
        recruit_document.identity_binding
    )
    assert proof is not None
    assert proof.source_url == recruit
    assert recruit_document.source_kind == "official_recruit_page"
    assert recruit_document.requirement == "REQUIRED"
    assert recruit_document.source_tier == "TIER_1_OFFICIAL"
    assert any(
        fragment.document_id == recruit_document.document_id
        and "culture:work_principle" in fragment.covered_slot_ids
        for fragment in build_fragments_for_collection(result)
    )


@pytest.mark.parametrize("missing", ("name", "registration_number"))
def test_DARTproof가_있어도_법인명과_등록번호중_하나가_다르면_승격하지_않는다(
    missing: str,
):
    candidate = "https://wise-shop.example/"
    body = _identity_body(
        "대표 제품과 판매 채널을 운영합니다",
        number=("999-99-99999" if missing == "registration_number" else "123-45-67890"),
    )
    if missing == "name":
        body = body.replace("주식회사 와이즐리컴퍼니", "주식회사 다른컴퍼니")
    pages = {
        "https://wise-shop.example/robots.txt": _missing(
            "https://wise-shop.example/robots.txt"
        ),
        candidate: _page(body, candidate),
    }
    site = _FakeWideSite(pages)

    result = collect_official_web_documents(
        company_id="c1",
        company_name="주식회사 와이즐리컴퍼니",
        root_homepage_url="",
        collected_at="2026-08-31T00:00:00+00:00",
        company_registration_numbers=("123-45-67890",),
        official_candidate_provenance=(_dart_provenance(candidate),),
        root_identity_verification_required=True,
        transport=site.transport,
        ir_html_fetch=_no_ir,
        ir_pdf_fetch=_no_ir_pdf,
    )

    assert result.documents == ()
    assert any(
        attempt.reason_code == "cross_domain_identity_mismatch"
        for attempt in result.attempts
    )


def test_공유제3자host는_DARTproof와_복사신원이_있어도_조회하지_않는다():
    candidate = "https://blog.naver.com/wisely"
    pages = {}
    site = _FakeWideSite(pages)

    result = collect_official_web_documents(
        company_id="c1",
        company_name="주식회사 와이즐리컴퍼니",
        root_homepage_url="",
        collected_at="2026-08-31T00:00:00+00:00",
        company_registration_numbers=("123-45-67890",),
        official_candidate_provenance=(_dart_provenance(candidate),),
        root_identity_verification_required=True,
        transport=site.transport,
        ir_html_fetch=_no_ir,
        ir_pdf_fetch=_no_ir_pdf,
    )

    assert result.documents == ()
    assert build_fragments_for_collection(result) == ()
    assert site.calls == []


def test_등록번호를_못받으면_예전처럼_다른_등록도메인을_0회호출한다():
    root = "https://old-company.example"
    candidate = "https://wise-shop.example"
    pages = {
        f"{root}/robots.txt": _missing(f"{root}/robots.txt"),
        f"{root}/sitemap.xml": _missing(f"{root}/sitemap.xml"),
        f"{root}/": _page(
            _body("회사 안내") + f'<a href="{candidate}/">새 자사몰</a>', f"{root}/"
        ),
        "https://www.old-company.example/robots.txt": _missing(
            "https://www.old-company.example/robots.txt"
        ),
        "https://www.old-company.example/sitemap.xml": _missing(
            "https://www.old-company.example/sitemap.xml"
        ),
        "https://www.old-company.example/": _missing(
            "https://www.old-company.example/"
        ),
    }
    site = _FakeWideSite(pages)

    _collect(
        site,
        company_name="주식회사 와이즐리컴퍼니",
        company_registration_numbers=(),
        root_homepage_url=root,
    )

    assert not any("wise-shop.example" in call for call in site.calls)


def test_hm_url이_비어도_출처있는_후보가_강한_신원을_통과하면_수집한다():
    candidate = "https://wise-shop.example"
    pages = {
        f"{candidate}/robots.txt": _missing(f"{candidate}/robots.txt"),
        f"{candidate}/": _page(
            _identity_body("대표 제품군을 개인 고객에게 판매해 매출을 얻습니다"),
            f"{candidate}/",
        ),
    }
    site = _FakeWideSite(pages)

    result = _collect(
        site,
        root_homepage_url="",
        company_name="주식회사 와이즐리컴퍼니",
        company_registration_numbers=("1234567890",),
        official_candidate_urls=(f"{candidate}/",),
    )

    assert {document.canonical_url for document in result.documents} == {f"{candidate}/"}
    assert any(
        attempt.reason_code == "cross_domain_identity_verified"
        for attempt in result.attempts
    )
    assert result.documents[0].source_tier == "TIER_3_TRUSTED"
    assert build_fragments_for_collection(result) == ()


def test_같은_host의_첫후보가_실패해도_뒤의_명시_회사소개_URL을_검증한다():
    """첫 URL만 기억하면 자료가 있는데도 내부 수집기가 없다고 오판한다."""

    origin = "https://wise-shop.example"
    first = f"{origin}/about"
    second = f"{origin}/company-info"
    robots = f"{origin}/robots.txt"
    pages = {
        robots: _missing(robots),
        first: _page(_body("제품 안내만 있고 법인 식별정보는 없는 화면"), first),
        second: _page(
            _identity_body("생활용품 대표 제품군과 주요 사업을 안내합니다"),
            second,
        ),
    }
    site = _FakeWideSite(pages)

    result = _collect(
        site,
        root_homepage_url="",
        company_name="주식회사 와이즐리컴퍼니",
        company_registration_numbers=("1234567890",),
        official_candidate_urls=(first, second),
    )

    assert site.calls == [robots, first, second]
    assert {document.canonical_url for document in result.documents} == {second}
    assert any(
        attempt.reason_code == "cross_domain_identity_mismatch"
        for attempt in result.attempts
    )
    assert any(
        attempt.reason_code == "cross_domain_identity_verified"
        for attempt in result.attempts
    )


def test_hm_url과_출처있는_후보가_둘다_없으면_추측_URL을_만들거나_호출하지_않는다():
    site = _FakeWideSite({})

    result = _collect(
        site,
        root_homepage_url="",
        company_name="주식회사 와이즐리컴퍼니",
        company_registration_numbers=("1234567890",),
        official_candidate_urls=(),
    )

    assert result.documents == ()
    assert result.attempts == ()
    assert site.calls == []


def test_서로다른_자사몰_채용_블로그도_같은_신원규칙으로만_수집한다():
    root = "https://old-company.example"
    channels = {
        "https://wise-shop.example/": "주력 제품군과 서비스를 개인 고객에게 판매합니다",
        "https://wise-careers.example/careers": "핵심가치를 적용해 고객 경험을 개선한 프로젝트 사례를 완료했습니다",
        "https://wise-story.example/blog": "신제품 출시 전략과 향후 계획을 발표했습니다",
    }
    links = "".join(f'<a href="{url}">공식 채널</a>' for url in channels)
    pages = {
        f"{root}/robots.txt": _missing(f"{root}/robots.txt"),
        f"{root}/sitemap.xml": _missing(f"{root}/sitemap.xml"),
        f"{root}/": _page(_body("회사 안내") + links, f"{root}/"),
        "https://www.old-company.example/robots.txt": _missing(
            "https://www.old-company.example/robots.txt"
        ),
        "https://www.old-company.example/sitemap.xml": _missing(
            "https://www.old-company.example/sitemap.xml"
        ),
        "https://www.old-company.example/": _missing(
            "https://www.old-company.example/"
        ),
    }
    for url, text in channels.items():
        parsed = urllib.parse.urlsplit(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        pages[f"{origin}/robots.txt"] = _missing(f"{origin}/robots.txt")
        pages[url] = _page(_identity_body(text), url)
    site = _FakeWideSite(pages)

    result = _collect(
        site,
        company_name="주식회사 와이즐리컴퍼니",
        company_registration_numbers=("123-45-67890",),
        root_homepage_url=root,
    )

    collected = {document.canonical_url for document in result.documents}
    assert set(channels) <= collected
    assert next(doc for doc in result.documents if "careers" in doc.canonical_url).source_kind == (
        "official_identity_verified_web_page"
    )


@pytest.mark.parametrize(
    "candidate",
    (
        "https://blog.naver.com/wisely",
        "https://facebook.com/wisely",
    ),
)
def test_강한_신원값이_있어도_공유플랫폼_소셜은_후보조회조차_하지_않는다(
    candidate: str,
):
    site = _FakeWideSite({})

    result = _collect(
        site,
        root_homepage_url="",
        company_name="주식회사 와이즐리컴퍼니",
        company_registration_numbers=("1234567890",),
        official_candidate_urls=(candidate,),
    )

    assert result.documents == ()
    assert site.calls == []


def test_DART의_낡은_HTTP후보는_같은_host_path_query의_HTTPS만_확인한다():
    old_candidate = "http://wise-shop.example/company?tenant=wisely"
    https_candidate = "https://wise-shop.example/company?tenant=wisely"
    robots = "https://wise-shop.example/robots.txt"
    pages = {
        robots: _missing(robots),
        https_candidate: _page(
            _identity_body("생활용품 대표 제품군을 개인 고객에게 판매합니다"),
            https_candidate,
        ),
    }
    site = _FakeWideSite(pages)

    result = _collect(
        site,
        root_homepage_url="",
        company_name="주식회사 와이즐리컴퍼니",
        company_registration_numbers=("1234567890",),
        official_candidate_urls=(old_candidate,),
    )

    assert site.calls == [robots, https_candidate]
    assert old_candidate not in site.calls
    assert {document.canonical_url for document in result.documents} == {
        https_candidate
    }


@pytest.mark.parametrize("promote_verified_root", (True, False))
@pytest.mark.parametrize(
    ("requested_path", "effective_path"),
    (("/company/", "/company/careers"),),
)
def test_신원검증후_redirect는_실제_landing_URL로_문서_attempt_조각을_한번만_분류한다(
    promote_verified_root: bool,
    requested_path: str,
    effective_path: str,
) -> None:
    origin = "https://redirect-kind.example"
    requested = f"{origin}{requested_path}"
    effective = f"{origin}{effective_path}"
    pages = {
        f"{origin}/robots.txt": _missing(f"{origin}/robots.txt"),
        f"{origin}/sitemap.xml": _missing(f"{origin}/sitemap.xml"),
        requested: _page(
            _identity_body(
                "핵심가치와 일하는 방식을 제품 사업과 고객 경험에 적용합니다"
            ),
            effective,
        ),
    }
    site = _FakeWideSite(pages)
    result = _collect(
        site,
        root_homepage_url=requested if promote_verified_root else "",
        official_candidate_urls=() if promote_verified_root else (requested,),
        company_name="주식회사 와이즐리컴퍼니",
        company_registration_numbers=("1234567890",),
        root_identity_verification_required=True,
    )

    document = next(item for item in result.documents if item.canonical_url == effective)
    attempt = next(
        item
        for item in result.attempts
        if item.reason_code
        in {"root_identity_verified", "cross_domain_identity_verified"}
    )
    expected = wide_collect.classify_official_page_url(effective)
    assert attempt.slot_ids == expected.slot_ids
    if promote_verified_root:
        assert document.source_kind == expected.source_kind
        assert attempt.source_kind == expected.source_kind
    else:
        assert document.source_kind == "official_identity_verified_web_page"
        assert attempt.source_kind == "official_identity_verified_web_page"
    fragments = build_fragments_for_collection(result)
    if promote_verified_root:
        assert fragments
        assert {
            slot for fragment in fragments for slot in fragment.covered_slot_ids
        } <= set(expected.slot_ids)
    else:
        # 강한 DART 계보가 없는 cross-domain 신원 일치는 감사 후보일 뿐
        # Writer로 올리지 않는 기존 비용·신뢰 정책을 유지한다.
        assert fragments == ()


def test_재할당된_DART_root는_타사문구가_풍부해도_근거와_하위링크를_0건으로_막는다():
    root = "https://reassigned.example"
    product = f"{root}/products"
    pages = {
        f"{root}/robots.txt": _missing(f"{root}/robots.txt"),
        f"{root}/": _page(
            _identity_body(
                "대표 제품과 제품군을 개인 고객에게 판매해 매출을 얻고 "
                "핵심가치와 차별화 경쟁력으로 향후 전략을 추진합니다",
                number="999-99-99999",
                extra=f'<a href="{product}">제품</a>',
            ).replace("주식회사 와이즐리컴퍼니", "주식회사 다른컴퍼니"),
            f"{root}/",
        ),
        product: _page(_body("타사 대표 제품과 핵심 제품군"), product),
    }
    site = _FakeWideSite(pages)

    result = _collect(
        site,
        company_name="주식회사 와이즐리컴퍼니",
        company_registration_numbers=("1234567890",),
        root_homepage_url=root,
        root_identity_verification_required=True,
    )

    assert result.documents == ()
    assert product not in site.calls
    assert any(
        attempt.reason_code == "root_identity_mismatch"
        and attempt.documents_seen == 0
        for attempt in result.attempts
    )


def test_DART_root도_법인명과_등록번호가_함께_맞은뒤에만_REQUIRED가_된다():
    root = "https://verified-root.example"
    pages = {
        f"{root}/robots.txt": _missing(f"{root}/robots.txt"),
        f"{root}/": _page(
            _identity_body("2018년에 설립해 생활용품을 제조 및 판매하는 주요 사업을 영위합니다"),
            f"{root}/",
        ),
        f"{root}/sitemap.xml": _missing(f"{root}/sitemap.xml"),
    }
    site = _FakeWideSite(pages)

    result = _collect(
        site,
        company_name="주식회사 와이즐리컴퍼니",
        company_registration_numbers=("1234567890",),
        root_homepage_url=root,
        root_identity_verification_required=True,
    )

    document = next(doc for doc in result.documents if doc.canonical_url == f"{root}/")
    assert document.source_kind == "official_web_page"
    assert document.requirement == "REQUIRED"
    assert "등록번호 이중 검증" in document.identity_binding
    assert any(
        attempt.reason_code == "root_identity_verified"
        for attempt in result.attempts
    )


def test_DART_root와_same_origin_개인정보페이지에_신원이_나뉘어도_검증한다():
    root = "https://split-identity.example"
    privacy = f"{root}/privacy"
    attacker = "https://attacker.example/privacy"
    pages = {
        f"{root}/robots.txt": _missing(f"{root}/robots.txt"),
        f"{root}/": _page(
            _body("주식회사 와이즐리컴퍼니 회사 소개와 생활용품 주요 사업")
            + f'<a href="{privacy}">개인정보처리방침</a>'
            + f'<a href="{attacker}">외부 개인정보 안내</a>',
            f"{root}/",
        ),
        privacy: _page(
            _body("개인정보 보호와 고객 정보 처리 원칙을 안내합니다")
            + "<footer>사업자등록번호 123-45-67890</footer>",
            privacy,
        ),
        f"{root}/sitemap.xml": _missing(f"{root}/sitemap.xml"),
    }
    site = _FakeWideSite(pages)

    result = _collect(
        site,
        company_name="주식회사 와이즐리컴퍼니",
        company_registration_numbers=("1234567890",),
        root_homepage_url=root,
        root_identity_verification_required=True,
    )

    urls = {document.canonical_url for document in result.documents}
    assert f"{root}/" in urls
    assert privacy in urls
    assert attacker not in site.calls, "교차 origin 본문을 신원 보조 페이지로 읽지 않는다"
    assert all(document.source_tier == "TIER_1_OFFICIAL" for document in result.documents)
    assert any(
        attempt.reason_code == "root_identity_verified"
        for attempt in result.attempts
    )


def test_root_신원보조탐색은_닫힌_페이지수_상한을_넘지_않는다():
    root = "https://bounded-identity.example"
    supplement_urls = tuple(
        f"{root}/{name}"
        for name in ("company", "about", "privacy", "legal")
    )
    links = "".join(f'<a href="{url}">{url}</a>' for url in supplement_urls)
    pages = {
        f"{root}/robots.txt": _missing(f"{root}/robots.txt"),
        f"{root}/": _page(
            "<html><main>주식회사 와이즐리컴퍼니</main>" + links + "</html>",
            f"{root}/",
        ),
        **{
            url: _page("<html><footer>번호 없는 안내</footer></html>", url)
            for url in supplement_urls
        },
    }
    site = _FakeWideSite(pages)

    result = _collect(
        site,
        company_name="주식회사 와이즐리컴퍼니",
        company_registration_numbers=("1234567890",),
        root_homepage_url=root,
        root_identity_verification_required=True,
    )

    # 결속에 성공하면 그 뒤 일반 수집이 남은 same-origin 페이지도 읽는다.
    # 이 시험의 주제는 «신원 확인 단계»의 3쪽 상한이므로, 결속 직후 시작되는
    # sitemap 탐색 앞까지만 잘라서 센다.
    identity_phase_calls = site.calls[: site.calls.index(f"{root}/sitemap.xml")]
    called_supplements = [
        url for url in identity_phase_calls if url in supplement_urls
    ]
    assert len(called_supplements) == 3
    assert supplement_urls[3] not in called_supplements
    # 읽은 3쪽에 등록번호가 없어도 DART hm_url host는 법인명만으로 결속한다
    # (2026-09-05 완화). 이 완화 전에는 여기서 문서가 0건이었다.
    assert any(
        attempt.reason_code == "root_identity_name_only"
        for attempt in result.attempts
    )


def test_등록번호가_없는_DART_root는_정식모드에서_네트워크_0회로_fail_closed한다():
    site = _FakeWideSite({})

    result = _collect(
        site,
        company_name="주식회사 와이즐리컴퍼니",
        company_registration_numbers=(),
        root_homepage_url="https://unknown-owner.example/",
        root_identity_verification_required=True,
    )

    assert result.documents == ()
    assert site.calls == []
    assert [attempt.reason_code for attempt in result.attempts] == [
        "root_identity_unverifiable"
    ]


@pytest.mark.parametrize(
    "candidate",
    (
        "http://wise-shop.example:8080/company",
        "https://user:secret@wise-shop.example/company",
        "ftp://wise-shop.example/company",
    ),
)
def test_HTTP승격도_사용자정보_비표준포트_다른프로토콜은_0회호출한다(
    candidate: str,
):
    site = _FakeWideSite({})

    result = _collect(
        site,
        root_homepage_url="",
        company_name="주식회사 와이즐리컴퍼니",
        company_registration_numbers=("1234567890",),
        official_candidate_urls=(candidate,),
    )

    assert result.documents == ()
    assert result.attempts == ()
    assert site.calls == []


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


# ──: 등록 도메인 판정이 TLD를 무시하면 안 된다 ─────────


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


# ── apex·www 짝 결속 ──────────────────────────────


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

    # ★ 루트("/") attempt도 fallback으로 slot_ids 17개 전체를 받으므로
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

    # ★ 루트("/") attempt도 fallback으로 slot_ids 17개 전체를 받으므로
    #   "in" 느슨한 대조 대신 «정확히 이 페이지 유형의 슬롯 집합과 같다»로 고른다.
    expected_slots = set(
        WIDE_REQUIRED_SLOT_IDS_BY_SECTION["future_strategy"] + WIDE_REQUIRED_SLOT_IDS_BY_SECTION["past_changes"]
    )
    news_attempt = next(
        a for a in result.attempts if a.state == "OK" and set(a.slot_ids) == expected_slots
    )
    assert set(news_attempt.slot_ids) == expected_slots


# ──: 어떤 attempt도 slot_ids가 비어 있으면 안 된다 ─────


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


@pytest.mark.parametrize(
    ("requested_path", "effective_path", "body", "expected_kind", "expected_slots"),
    (
        (
            "/about",
            "/careers",
            "핵심가치를 적용해 협업 프로젝트를 운영하고 개선한 사례입니다.",
            "official_recruit_page",
            set(WIDE_REQUIRED_SLOT_IDS_BY_SECTION["culture"]),
        ),
        (
            "/careers",
            "/about",
            "2010년에 설립해 산업 장비를 제조하고 시장 점유율을 높였습니다.",
            "official_web_page",
            set(
                WIDE_REQUIRED_SLOT_IDS_BY_SECTION["identity"]
                + WIDE_REQUIRED_SLOT_IDS_BY_SECTION["competitive_position"]
            ),
        ),
    ),
)
def test_성공_redirect는_effective_URL하나로_문서_attempt_fragment종류를_정한다(
    requested_path,
    effective_path,
    body,
    expected_kind,
    expected_slots,
):
    requested_url = "https://company.example" + requested_path
    effective_url = "https://company.example" + effective_path
    pages = {
        "https://company.example/robots.txt": _page(
            ROBOTS_ALLOW_ALL,
            "https://company.example/robots.txt",
            "text/plain",
        ),
        "https://company.example/sitemap.xml": _missing(
            "https://company.example/sitemap.xml"
        ),
        "https://company.example/": _page(
            _body("루트 페이지 본문") + f'<a href="{requested_path}">이동</a>',
            "https://company.example/",
        ),
        requested_url: _page(_body(body), effective_url),
    }

    result = _collect(_FakeWideSite(pages))
    document = next(doc for doc in result.documents if doc.canonical_url == effective_url)
    assert document.source_kind == expected_kind
    fragments = tuple(
        fragment
        for fragment in build_fragments_for_collection(result)
        if fragment.document_id == document.document_id
    )
    assert fragments
    assert {
        slot
        for fragment in fragments
        for slot in fragment.covered_slot_ids
    } <= expected_slots
    attempt = next(
        attempt
        for attempt in result.attempts
        if attempt.source_kind == expected_kind
        and set(attempt.slot_ids) == expected_slots
        and attempt.documents_seen == 1
    )
    assert attempt.state == "OK"


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
    envelope = to_evidence_mappings(
        result=result,
        fragments=build_fragments_for_collection(result),
    )
    audit_row = next(
        row
        for row in envelope["provenance_documents"]
        if row["document_id"] == external_doc.document_id
    )
    assert audit_row["canonical_url"] == attachment_url
    assert audit_row["content_sha256"] == external_doc.content_sha256
    assert not any(
        row["document_id"] == external_doc.document_id
        for row in envelope["documents"]
    )


def test_공식host_IR도_날짜와_기간이_없으면_provenance_only로_격리되어_packet을_막지않는다(
    monkeypatch,
):
    """실제 wide 생산부터 packet까지 손으로 IR 근거를 보충하지 않는다."""

    from src.shared.official_ir import IR_ATTACHMENT_URL_FIELD

    company_id = "00126380"
    company_name = "가나다전자"
    root_url = "https://company.example/"
    ir_url = "https://company.example/ir/no-date.pdf"
    attestation_id, attestation_evidence = dart_profile_attestation_material(
        profile={
            "status": "000",
            "corp_code": company_id,
            "corp_name": company_name,
            "hm_url": root_url,
        },
        corp_code=company_id,
        company_name=company_name,
    )

    def fake_collect_ir(homepage_url, **_kwargs):
        if homepage_url != root_url:
            return OfficialIrCollectResult(
                state="none", fragments=[], downloaded_pdf_bytes=0
            )
        return OfficialIrCollectResult(
            state="ok",
            fragments=[
                {
                    "종류": "공식 IR",
                    "원문": "가나다전자는 기업 고객에게 장비를 판매해 매출을 얻습니다.",
                    "출처": ir_url,
                    IR_ATTACHMENT_URL_FIELD: ir_url,
                    "문서ID": "same-host-ir-no-date",
                    "문서명": "IR 사업 자료",
                    # 문서일·기준기간·anchor 검증값이 실제로 없다.
                }
            ],
            downloaded_pdf_bytes=100,
        )

    monkeypatch.setattr(wide_collect, "collect_official_ir_fragments", fake_collect_ir)
    pages = {
        "https://company.example/robots.txt": _page(
            ROBOTS_ALLOW_ALL,
            "https://company.example/robots.txt",
            "text/plain",
        ),
        "https://company.example/sitemap.xml": _missing(
            "https://company.example/sitemap.xml"
        ),
        root_url: _page(
            _body("가나다전자는 장비를 제조하고 기업 고객에게 판매해 매출을 얻습니다."),
            root_url,
        ),
    }
    result = collect_official_web_documents(
        company_id=company_id,
        company_name=company_name,
        root_homepage_url=root_url,
        collected_at="2026-09-04",
        domain_attestation_source_id=attestation_id,
        domain_attestation_evidence=attestation_evidence,
        root_identity_verification_required=False,
        transport=_FakeWideSite(pages).transport,
        ir_html_fetch=_no_ir,
        ir_pdf_fetch=_no_ir_pdf,
    )
    ir_document = next(doc for doc in result.documents if doc.canonical_url == ir_url)
    assert ir_document.requirement == "OPTIONAL"
    assert ir_document.source_tier == "TIER_3_TRUSTED"
    assert build_fragments(ir_document, company_id=company_id) == ()
    ir_attempt = next(
        attempt
        for attempt in result.attempts
        if attempt.reason_code == "official_ir_writer_metadata_incomplete"
    )
    assert ir_attempt.documents_seen == 1

    fragments = build_fragments_for_collection(result)
    envelope = to_evidence_mappings(result=result, fragments=fragments)
    assert not any(
        row["document_id"] == ir_document.document_id
        for row in envelope["documents"]
    )
    assert envelope["provenance_documents"][0]["canonical_url"] == ir_url
    candidates = produce_from_collection_envelopes(
        company_id=company_id,
        company_type=CompanyType.AUDIT_ONLY,
        collection_envelopes=(envelope,),
    )
    official = OfficialEvidenceCollectionResult(
        company_id=company_id,
        candidates=candidates,
        provenance_documents=provenance_documents_from_wide_envelope(
            envelope,
            company_id=company_id,
        ),
    )
    legacy = {
        number: {"종류": kind, "원문": f"{kind}의 독립 packet 바탕 원문입니다."}
        for number, kind in enumerate(sorted(LEGACY_FRAGMENT_KINDS), start=1)
    }
    merged, _added = merge_official_evidence_fragments(legacy, official)
    assert merged
    assert all(str(row.get("출처") or "") != ir_url for row in merged.values())
    packet_set = build_section_evidence_packet_set(
        corp_id=company_id,
        source_generation_sha256=official.source_snapshot_sha256,
        frags=merged,
        filing_meta=filing_meta_from_raw(
            {
                "rcept_no": "20260315000123",
                "report_nm": "사업보고서 (2025.12)",
                "rcept_dt": "20260315",
            }
        ),
    )
    assert packet_set.packets
    assert official.independent_document_count == len(
        {
            document.content_sha256
            for candidate in official.candidates
            for document in candidate.documents
        }
    )


def test_실제_IR_parser는_URL의_ir글자와_무관하게_본문의_여러장슬롯을_살린다():
    """실제 HTML→PDF parser 결과를 URL 힌트가 과거·미래 장으로 자르지 않는다."""

    import io

    from pathlib import Path

    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen.canvas import Canvas

    from src.features.homepage.ir_pdf import FetchedIrHtml, FetchedIrPdf

    company_id = "00126380"
    company_name = "Example Company"
    root_url = "https://company.example/"
    pdf_url = "https://company.example/ir/2026-q2.pdf"
    profile = {
        "status": "000",
        "corp_code": company_id,
        "corp_name": company_name,
        "hm_url": root_url,
    }
    attestation_id, attestation_evidence = dart_profile_attestation_material(
        profile=profile,
        corp_code=company_id,
        company_name=company_name,
    )
    pdf_buffer = io.BytesIO()
    font_name = "WideCollectKoreanIr"
    font_path = (
        Path(__file__).resolve().parents[2]
        / "export_pdf"
        / "fonts"
        / "Freesentation-Regular.ttf"
    )
    pdfmetrics.registerFont(TTFont(font_name, str(font_path)))
    canvas = Canvas(pdf_buffer, pageCompression=0, invariant=1)
    ir_sentences = (
        "Example Company는 기업 고객사에 반도체 검사 솔루션을 제공하고 장비 판매로 매출을 얻습니다.",
        "Example Company는 반도체 공정 추적 장비를 핵심 제품으로 두고 제품별 매출 비중을 공개합니다.",
        "원재료 가격 상승으로 Example Company의 원가 부담이 커졌고, 이에 대응해 당사가 공급처를 다변화했습니다.",
    )
    for sentence in ir_sentences:
        canvas.setFont(font_name, 12)
        canvas.drawString(40, 800, sentence)
        canvas.showPage()
    canvas.save()
    pdf_bytes = pdf_buffer.getvalue()

    def ir_html(url, expected_hostname, url_allowed):
        if url.endswith("/robots.txt"):
            return FetchedIrHtml("", url)
        if expected_hostname == "company.example" and url == root_url:
            assert url_allowed(url)
            return FetchedIrHtml(
                '<a href="/ir/2026-q2.pdf">26년 2분기 IR자료 2026-08-12</a>',
                root_url,
            )
        raise wide_collect.OfficialIrFetchError("가짜 IR HTML 없음")

    def ir_pdf(url, expected_hostname, max_bytes, url_allowed):
        assert url == pdf_url
        assert expected_hostname == "company.example"
        assert len(pdf_bytes) <= max_bytes
        assert url_allowed(url)
        return FetchedIrPdf(pdf_bytes, pdf_url, "application/pdf")

    pages = {
        "https://company.example/robots.txt": _missing(
            "https://company.example/robots.txt"
        ),
        "https://company.example/sitemap.xml": _missing(
            "https://company.example/sitemap.xml"
        ),
        root_url: _page(_body("Example Company official business"), root_url),
    }
    result = collect_official_web_documents(
        company_id=company_id,
        company_name=company_name,
        root_homepage_url=root_url,
        collected_at="2026-09-04",
        domain_attestation_source_id=attestation_id,
        domain_attestation_evidence=attestation_evidence,
        root_identity_verification_required=False,
        transport=_FakeWideSite(pages).transport,
        ir_html_fetch=ir_html,
        ir_pdf_fetch=ir_pdf,
    )
    ir_document = next(
        document
        for document in result.documents
        if document.source_kind == "official_ir_pdf"
    )
    assert ir_document.canonical_url == pdf_url
    assert ir_document.requirement == "REQUIRED"
    assert ir_document.source_tier == "TIER_1_OFFICIAL"
    wide_fragments = build_fragments_for_collection(result)
    ir_fragments = [
        fragment
        for fragment in wide_fragments
        if fragment.document_id == ir_document.document_id
    ]
    covered = {
        slot_id
        for fragment in ir_fragments
        for slot_id in fragment.covered_slot_ids
    }
    assert {
        "business_model:customer_type",
        "business_model:revenue_model",
        "business_model:value_exchange",
        "portfolio:product_role",
        "portfolio:revenue_link",
        "current_challenges:issue",
        "current_challenges:response",
    } <= covered
    assert {fragment.text for fragment in ir_fragments} == set(ir_sentences)


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
    """apex/www 짝 결속 덕분에 IR 후보 호스트가
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


# ── DART hm_url host의 이름-단독 결속 ───────────────────────


def test_DART_root는_등록번호가_홈페이지에_없어도_법인명만으로_결속한다():
    """DART가 등록한 홈페이지 host는 이름만 맞으면 공식 웹으로 결속한다."""

    root = "https://name-only-root.example"
    pages = {
        f"{root}/robots.txt": _missing(f"{root}/robots.txt"),
        f"{root}/sitemap.xml": _missing(f"{root}/sitemap.xml"),
        f"{root}/": _page(
            _body(
                "주식회사 와이즐리컴퍼니 · 생활용품을 제조 및 판매하는 "
                "주요 사업을 영위합니다"
            ),
            f"{root}/",
        ),
    }
    site = _FakeWideSite(pages)

    result = _collect(
        site,
        company_name="주식회사 와이즐리컴퍼니",
        company_registration_numbers=("1234567890",),
        root_homepage_url=root,
        root_identity_verification_required=True,
    )

    document = next(doc for doc in result.documents if doc.canonical_url == f"{root}/")
    assert document.source_kind == "official_web_page"
    assert document.requirement == "REQUIRED"
    assert "등록번호 미게시" in document.identity_binding
    assert "이중 검증" not in document.identity_binding
    assert any(
        attempt.reason_code == "root_identity_name_only"
        for attempt in result.attempts
    )
    assert not any(
        attempt.reason_code == "root_identity_mismatch"
        for attempt in result.attempts
    )


def test_같은_페이지도_교차도메인이면_이름만으로는_결속하지_않는다():
    """DART hm_url host가 아닌 후보는 법인명+등록번호 이중 검증을 유지한다."""

    candidate = "https://name-only-cross.example/"
    pages = {
        "https://name-only-cross.example/robots.txt": _missing(
            "https://name-only-cross.example/robots.txt"
        ),
        candidate: _page(
            _body(
                "주식회사 와이즐리컴퍼니 · 생활용품을 제조 및 판매하는 "
                "주요 사업을 영위합니다"
            ),
            candidate,
        ),
    }
    site = _FakeWideSite(pages)

    result = _collect(
        site,
        company_name="주식회사 와이즐리컴퍼니",
        company_registration_numbers=("1234567890",),
        root_homepage_url="",
        official_candidate_urls=(candidate,),
        root_identity_verification_required=True,
    )

    assert result.documents == ()
    assert any(
        attempt.reason_code == "cross_domain_identity_mismatch"
        for attempt in result.attempts
    )
    assert not any(
        attempt.reason_code == "root_identity_name_only"
        for attempt in result.attempts
    )


def test_보조_신원페이지_3쪽에도_번호가_없으면_법인명만으로_결속한다():
    """(주)인이지 실측형 회귀 — 보강 조회는 성공했는데 번호가 0건인 경우."""

    root = "https://ineeji-shaped.example"
    supplement_paths = (
        "/html/about/company.php",
        "/html/guide/privacy.php",
        "/html/guide/terms.php",
    )
    links = "".join(
        f'<a href="{root}{path}">회사 정보</a>' for path in supplement_paths
    )
    pages = {
        f"{root}/robots.txt": _missing(f"{root}/robots.txt"),
        f"{root}/sitemap.xml": _missing(f"{root}/sitemap.xml"),
        f"{root}/": _page(
            _body("주식회사 와이즐리컴퍼니 회사 소개와 생활용품 주요 사업")
            + links,
            f"{root}/",
        ),
    }
    for path in supplement_paths:
        url = f"{root}{path}"
        pages[url] = _page(
            _body("회사 정보와 이용 약관을 안내합니다"),
            url,
        )
    site = _FakeWideSite(pages)

    result = _collect(
        site,
        company_name="주식회사 와이즐리컴퍼니",
        company_registration_numbers=("1234567890",),
        root_homepage_url=root,
        root_identity_verification_required=True,
    )

    assert all(f"{root}{path}" in site.calls for path in supplement_paths)
    assert any(
        attempt.reason_code == "root_identity_name_only"
        for attempt in result.attempts
    )
    document = next(doc for doc in result.documents if doc.canonical_url == f"{root}/")
    assert "등록번호 미게시" in document.identity_binding


def test_보조_신원페이지_조회가_실패하면_이름만으로_결속하지_않는다():
    """자료를 다 못 본 상태에서 완화 경로를 열지 않는다."""

    root = "https://ineeji-broken.example"
    supplement = f"{root}/company"
    pages = {
        f"{root}/robots.txt": _missing(f"{root}/robots.txt"),
        f"{root}/sitemap.xml": _missing(f"{root}/sitemap.xml"),
        f"{root}/": _page(
            _body("주식회사 와이즐리컴퍼니 회사 소개와 생활용품 주요 사업")
            + f'<a href="{supplement}">회사 소개</a>',
            f"{root}/",
        ),
        # supplement 자체는 pages에 없어 접속 실패가 된다.
    }
    site = _FakeWideSite(pages)

    result = _collect(
        site,
        company_name="주식회사 와이즐리컴퍼니",
        company_registration_numbers=("1234567890",),
        root_homepage_url=root,
        root_identity_verification_required=True,
    )

    assert result.documents == ()
    assert any(
        attempt.reason_code == "root_identity_supplement_failed"
        for attempt in result.attempts
    )
    assert not any(
        attempt.reason_code == "root_identity_name_only"
        for attempt in result.attempts
    )
