"""기관 원문 발견부터 현재 상품 역할의 작성 입력까지 무료 결정론적 회귀."""
from __future__ import annotations

import hashlib
import json
import ssl
import urllib.parse
from dataclasses import replace
from pathlib import Path

import pytest
from lxml import html

from src.features.chapter_evidence.constants import CompanyType
from src.features.chapter_evidence.produce import produce_from_collection_envelopes
from src.features.composer import render as composer_render
from src.features.composer.port import filing_meta_from_raw
from src.features.composer.logic import build_section_prompt
from src.features.homepage.directory_discovery import DirectoryPage, discover_institution_homepage
from src.features.homepage.constants import WIDE_MAX_PAGES
from src.features.homepage.safe_http import UnsafeHomepageUrlError
from src.features.homepage.wide_collect import collect_official_web_documents
from src.features.homepage.wide_evidence_mapping import to_evidence_mappings
from src.features.homepage.wide_fetch import WideRawResponse
from src.features.homepage.wide_fragments import build_fragments_for_collection
from src.features.pipeline.evidence_transport import typed_fragments_from_raw
from src.features.pipeline.official_evidence_transport_adapter import merge_official_evidence_fragments
from src.features.provenance.sources import (
    full_typed_source_registry_problem, has_valid_provenance_seal, seal_collected_source,
)
from src.shared.report_evidence.identity_verified_web import build_dart_filing_url_provenance
from src.shared.report_evidence.institution_directory_attestation import (
    build_institution_directory_provenance, directory_search_url,
    parse_institution_directory_provenance, parse_verified_institution_directory_binding,
    institution_directory_binding_allows_url,
)
from src.shared.report_evidence.institution_directory_constants import DIRECTORY_ORIGIN
from src.shared.report_evidence.runtime_port import OfficialEvidenceCollectionResult

FIXTURES = Path(__file__).parent / "fixtures/institution_directory"
COMPANY_ID = "01492697"
COMPANY_NAME = "토스페이먼츠 주식회사"
COLLECTED_AT = "2026-10-21"
ROOT = "https://www.tosspayments.com/"
RECORD = DIRECTORY_ORIGIN + "/business/enterprise.do?lang=ko&id=536"
WIDGET = ROOT + "services/payment-widget"
BRANDPAY = ROOT + "services/brandpay"
ROBOTS = b"User-agent: *\nAllow: /\n"


def _fixture(name: str) -> bytes:
    return (FIXTURES / name).read_bytes()


class DirectorySite:
    def __init__(self, *, search: bytes | None = None, record: bytes | None = None,
                 robots: bytes = ROBOTS):
        self.calls: list[str] = []
        self.pages = {
            DIRECTORY_ORIGIN + "/robots.txt": DirectoryPage(200, DIRECTORY_ORIGIN + "/robots.txt", robots, "text/plain"),
            directory_search_url(COMPANY_NAME): DirectoryPage(200, directory_search_url(COMPANY_NAME), search if search is not None else _fixture("search.html")),
            RECORD: DirectoryPage(200, RECORD, record if record is not None else _fixture("record.html")),
        }

    def fetch(self, url: str) -> DirectoryPage:
        self.calls.append(url)
        return self.pages[url]


class CompanySite:
    def __init__(self, *, number: str = "411-86-01799", extra: str = "", robots: str = ROBOTS.decode()):
        self.calls: list[str] = []
        self.pages = {
            ROOT + "robots.txt": robots,
            ROOT: _fixture("company-root.html").decode().replace("411-86-01799", number).replace("</main>", extra + "</main>"),
            WIDGET: _fixture("widget.html").decode(),
            BRANDPAY: _fixture("brandpay.html").decode(),
        }

    def transport(self, url: str, url_allowed):
        self.calls.append(url)
        assert url_allowed(url)
        return WideRawResponse(status=200 if url in self.pages else 404,
            text=self.pages.get(url, ""), effective_url=url,
            content_type="text/plain" if url.endswith("robots.txt") else "text/html")


def _no_ir(*args, **kwargs):
    from src.features.homepage.ir_pdf import OfficialIrFetchError
    raise OfficialIrFetchError("시험에는 IR 원문이 없습니다")


def _collect(directory: DirectorySite | None = None, company: CompanySite | None = None, **kwargs):
    directory = directory or DirectorySite()
    company = company or CompanySite()
    options = dict(company_id=COMPANY_ID, company_name=COMPANY_NAME, root_homepage_url="",
        collected_at=COLLECTED_AT, company_registration_numbers=("4118601799",),
        directory_fallback=True, directory_fetch=directory.fetch, transport=company.transport,
        ir_html_fetch=_no_ir, ir_pdf_fetch=_no_ir)
    options.update(kwargs)
    return collect_official_web_documents(**options)


def _discovery(site: DirectorySite):
    return discover_institution_homepage(company_id=COMPANY_ID, company_name=COMPANY_NAME,
        collected_at=COLLECTED_AT, fetch=site.fetch)


def _proof():
    return _discovery(DirectorySite()).candidates[0][1]


def _writer_input(result):
    fragments = build_fragments_for_collection(result)
    envelope = to_evidence_mappings(result=result, fragments=fragments)
    candidates = produce_from_collection_envelopes(company_id=COMPANY_ID,
        company_type=CompanyType.AUDIT_ONLY, collection_envelopes=(envelope,))
    official = OfficialEvidenceCollectionResult(company_id=COMPANY_ID, candidates=candidates)
    merged, _added = merge_official_evidence_fragments({}, official)
    converted = typed_fragments_from_raw(corp_id=COMPANY_ID, frags=merged,
        filing_meta=filing_meta_from_raw({"rcept_no": "20260315000123", "report_nm": "감사보고서 (2025.12)", "rcept_dt": "20260315"}))
    return fragments, merged, converted


def test_실제_회사명_검색표에서만_유일_상세를_발견하고_원문해시를_보존한다():
    site = DirectorySite()
    result = _discovery(site)
    assert result.state == "OK"
    assert site.calls == [DIRECTORY_ORIGIN + "/robots.txt", directory_search_url(COMPANY_NAME), RECORD]
    assert result.requests == 3
    candidate, value = result.candidates[0]
    proof = parse_institution_directory_provenance(value, reference_date=COLLECTED_AT)
    assert candidate == ROOT
    assert proof.information_basis == "institution_published_company_supplied_information"
    assert proof.search_payload_sha256 == hashlib.sha256(_fixture("search.html")).hexdigest()
    assert proof.record_payload_sha256 == hashlib.sha256(_fixture("record.html")).hexdigest()
    assert proof.company_name == COMPANY_NAME
    assert proof.declared_company_name == "토스페이먼츠"
    assert parse_institution_directory_provenance(value, reference_date="2026-10-23") is None
    assert parse_institution_directory_provenance(value, reference_date="2026-10-20") is None


@pytest.mark.parametrize("mutation", ["duplicate", "other_name", "pages", "snippet"])
def test_검색_동명이인_중복상세_미조회페이지_snippet은_닫힌_실패(mutation):
    tree = html.fromstring(_fixture("search.html").decode())
    if mutation == "duplicate":
        row = tree.xpath("//tbody/tr[.//a[contains(@href,'enterprise.do')]]")[0]
        row.getparent().append(html.fromstring(html.tostring(row)))
        tree.xpath("//em")[0].text = "3"
    elif mutation == "other_name":
        tree = html.fromstring(_fixture("search.html").decode().replace("토스페이먼츠", "토스페이먼츠대행"))
    elif mutation == "pages":
        tree.xpath("//em")[0].text = "15"
    else:
        tree = html.fromstring("<html><body>토스페이먼츠 홈페이지 https://www.tosspayments.com</body></html>")
    site = DirectorySite(search=html.tostring(tree, encoding="utf-8"))
    assert not _discovery(site).candidates
    assert RECORD not in site.calls


@pytest.mark.parametrize("mutation", ["duplicate_name", "wrong_name", "different_href", "private", "http"])
def test_상세_모호성_잘못된_회사나_URL은_후보를_만들지_않는다(mutation):
    raw = _fixture("record.html").decode()
    if mutation == "duplicate_name":
        tree = html.fromstring(raw)
        row = tree.xpath("//tr[th='기업명']")[0]
        row.getparent().append(html.fromstring(html.tostring(row)))
        raw = html.tostring(tree, encoding="unicode")
    elif mutation == "wrong_name":
        raw = raw.replace("토스페이먼츠", "다른회사")
    elif mutation == "different_href":
        raw = raw.replace('href="https://www.tosspayments.com"', 'href="https://other.example"')
    elif mutation == "private":
        raw = raw.replace("https://www.tosspayments.com", "https://127.0.0.1")
    else:
        raw = raw.replace("https://www.tosspayments.com", "http://www.tosspayments.com")
    assert not _discovery(DirectorySite(record=raw.encode())).candidates


@pytest.mark.parametrize("error", [ssl.SSLCertVerificationError("TLS 불일치"), UnsafeHomepageUrlError("사설주소 차단")])
def test_TLS_사설주소_안전전송_실패는_우회하지_않는다(error):
    def blocked(url):
        raise error
    result = discover_institution_homepage(company_id=COMPANY_ID, company_name=COMPANY_NAME,
        collected_at=COLLECTED_AT, fetch=blocked)
    assert result.state == "FAILED" and not result.candidates and result.requests == 1


@pytest.mark.parametrize("robots", [b"User-agent: *\nDisallow: /\n", b"User-agent: *\nDisallow: /business/enterprise.do\n"])
def test_기관_robots_금지시_대상_원문을_호출하지_않는다(robots):
    site = DirectorySite(robots=robots)
    result = _discovery(site)
    assert not result.candidates
    assert RECORD not in site.calls


def test_기관_응답_URL_변경과_robots_부재는_닫힌_실패():
    site = DirectorySite()
    url = DIRECTORY_ORIGIN + "/robots.txt"
    for response in (DirectoryPage(404, url, b""), DirectoryPage(200, url + "?forged=1", ROBOTS)):
        site.pages[url] = response
        assert not _discovery(site).candidates


@pytest.mark.parametrize("body", [b" \n ", b"<!-- no page -->"])
@pytest.mark.parametrize("stage", ["search", "record"])
def test_HTTP성공이어도_파싱불가능한_기관원문은_FAILED로_끝난다(body, stage):
    result = _discovery(DirectorySite(**{stage: body}))
    assert result.state == "FAILED" and not result.candidates


@pytest.mark.parametrize("field", ["search_payload_sha256", "record_payload_sha256", "url", "record_url", "company_id"])
def test_발견_영수증_해시_URL_회사값_변조는_거절한다(field):
    value = _proof()
    prefix, payload = value.split(":", 1)
    envelope = json.loads(payload)
    envelope["payload"][field] += "0"
    forged = prefix + ":" + json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    assert parse_institution_directory_provenance(forged) is None


@pytest.mark.parametrize("kwargs", [{"root_homepage_url": ROOT}, {"official_candidate_urls": (ROOT,)},
    {"official_candidate_provenance": ((ROOT, "invalid-proof"),)}, {"company_registration_numbers": ()}])
def test_DART_발견이_있거나_이중신원이_불가능하면_기관_fallback은_호출하지_않는다(kwargs):
    site = DirectorySite()
    _collect(site, **kwargs)
    assert site.calls == []


@pytest.mark.parametrize("number", ["111-11-11111", ""])
def test_법인명만_복제하거나_등록번호를_위조한_홈페이지는_상품근거0(number):
    result = _collect(company=CompanySite(number=number))
    assert not result.documents
    assert not build_fragments_for_collection(result)


def test_홈페이지_robots_금지시_상품_본문은_읽지_않는다():
    company = CompanySite(robots="User-agent: *\nDisallow: /\n")
    result = _collect(company=company)
    assert not result.documents
    assert WIDGET not in company.calls


def test_실제_상품명_기능_고객과_계보가_writer입력과_봉인까지_도달한다():
    company = CompanySite(extra='<a href="https://api.tosspayments.com/services/payment-widget">상품</a><a href="https://external.example/services/product">상품</a>')
    result = _collect(company=company)
    documents = {document.canonical_url: document for document in result.documents}
    assert {WIDGET, BRANDPAY} <= set(documents)
    assert all(urllib.parse.urlsplit(url).netloc == "www.tosspayments.com" for url in company.calls)
    for document in documents.values():
        assert document.requirement == "REQUIRED" and document.source_tier == "TIER_1_OFFICIAL"
        assert parse_verified_institution_directory_binding(document.identity_binding)
    fragments, merged, converted = _writer_input(result)
    roles = [fragment for fragment in fragments if fragment.slot_id == "portfolio:product_role"]
    assert any("UI는 가맹점" in fragment.text for fragment in roles)
    assert any("브랜드페이는 누구나" in fragment.text for fragment in roles)
    assert not any("국가/상품/고객" in fragment.text or "해외카드" in fragment.text for fragment in roles)
    assert converted.typed_count == len(merged) and converted.carried_raw_count == 0
    assert any("결제위젯" in raw["문서명"] and "UI는 가맹점" in raw["원문"] for raw in merged.values())
    prompt = build_section_prompt(COMPANY_NAME, "portfolio", converted.fragments, None, show_supported_claim_slots=True)
    assert "결제위젯" in prompt and "UI는 가맹점" in prompt and "브랜드페이는 누구나" in prompt
    assert "국가/상품/고객" not in prompt and "해외카드" not in prompt
    role = next(fragment for fragment in converted.fragments if fragment.source_url == BRANDPAY and "브랜드페이는 누구나" in fragment.text)
    meta = composer_render._fragment_metas((role,))[0]
    source = composer_render._build_source(meta, int(role.fragment_id), COMPANY_NAME, ["portfolio"])
    assert has_valid_provenance_seal(source)
    assert full_typed_source_registry_problem(source, (source,), reference_date=COLLECTED_AT) == ""
    assert not has_valid_provenance_seal(replace(source, url="https://external.example/product"))
    forged = seal_collected_source(replace(source, url="https://api.tosspayments.com/product", host="api.tosspayments.com", provenance_seal=""))
    assert full_typed_source_registry_problem(forged, (forged,), reference_date=COLLECTED_AT)
    assert not institution_directory_binding_allows_url(source.identity_binding, source_url=source.url, company_id="00000001")


def test_DART_공시_발견도_같은_상품_DOM의_예정기능을_현재_role로_승격하지_않는다():
    receipt = "20260000000001"
    proof = build_dart_filing_url_provenance(company_id=COMPANY_ID, url=ROOT,
        source_document_id=f"dart_audit_report:{receipt}", source_receipt_no=receipt,
        source_member_name="cover.xml", source_location="raw_xml_chars:10-40",
        source_document_sha256="a" * 64, source_payload_sha256="b" * 64)
    result = _collect(official_candidate_provenance=((ROOT, proof),))
    roles = [fragment for fragment in build_fragments_for_collection(result) if fragment.slot_id == "portfolio:product_role"]
    assert any("UI는 가맹점" in fragment.text for fragment in roles)
    assert any("브랜드페이는 누구나" in fragment.text for fragment in roles)
    assert not any("국가/상품/고객" in fragment.text or "해외카드" in fragment.text for fragment in roles)


def test_회사_서비스_이름을_바꿔도_동일한_기관_원문계약으로_발견한다():
    search = _fixture("search.html").replace("토스페이먼츠".encode(), "가나다장비".encode())
    record = _fixture("record.html").replace("토스페이먼츠".encode(), "가나다장비".encode()).replace(b"www.tosspayments.com", b"ganada.example")
    proof = build_institution_directory_provenance(company_id="00001234", company_name="주식회사 가나다장비",
        search_url=directory_search_url("주식회사 가나다장비"), search_body=search,
        record_url=RECORD, record_body=record, fetched_on=COLLECTED_AT)
    assert parse_institution_directory_provenance(proof).url == "https://ganada.example/"


@pytest.mark.parametrize("heading,prefix,enrollment,expected", [
    ("곧 출시", "", "누구나", False), ("현재 제공", "", "누구나", True),
    ("현재 제공", "사내 직원은 ", "사내 직원만", False),
    ("현재 제공", "타사에서 제공하는 ", "누구나", False),
])
def test_교육상품_보조추출도_현재와_예정_사내_타사_DOM을_구분한다(heading, prefix, enrollment, expected):
    company = CompanySite()
    title = "초보 개발자와 실무자를 위한 고객 서비스 데이터 분석 및 자동화 무료 실습 교육 과정"
    future = prefix + "데이터 분석 도구를 활용해 고객과 서비스 데이터를 분석하는 실습형 무료 교육 과정입니다."
    company.pages[WIDGET] = f"<html><head><title>{title}</title></head><body><main><h1>{title}</h1><p>수강 신청은 {enrollment} 홈페이지에서 할 수 있으며 고객과 서비스 데이터를 다루는 모든 실무자를 환영합니다.</p><section><h2>{heading}</h2><p>{future}</p></section></main></body></html>"
    result = _collect(company=company)
    document = next(doc for doc in result.documents if doc.canonical_url == WIDGET)
    assert document.product_role_context_checked
    assert bool(document.current_product_role_ranges) == expected
    assert any(fragment.document_id == document.document_id and "portfolio:product_role" in fragment.covered_slot_ids
        for fragment in build_fragments_for_collection(result)) == expected


@pytest.mark.parametrize("products", [True, False])
def test_일반_산업상품은_블로그포화에도_먼저_발견하며_상품이_없으면_블로그를_읽는다(products):
    company = CompanySite()
    company.pages[ROOT] = company.pages[ROOT].replace('<a href="/services/payment-widget">결제위젯 알아보기</a><a href="/services/brandpay">브랜드페이 알아보기</a>', '')
    urls = []
    for index in range(20):
        url = ROOT + f"blog/article-{index}"
        urls.append(url)
        company.pages[url] = f"<html><body><main><p>이 회사는 고객에게 장비를 판매하고 유지보수 서비스를 제공하여 매출을 얻으며 {index}번째 사례를 소개합니다.</p></main></body></html>"
    product_urls = [ROOT + f"products/inspection-{index}" for index in range(3)] if products else []
    for index, url in enumerate(product_urls):
        urls.append(url)
        company.pages[url] = f"<html><head><title>검사장비{index} | 제조사</title></head><body><main><section><h2>제품 기능</h2><p>검사장비{index}는 제조업 고객이 공정 데이터를 조회하고 제품의 검사 업무를 자동으로 처리할 수 있도록 제공하는 솔루션입니다.</p></section></main></body></html>"
    company.pages[ROOT + "sitemap.xml"] = '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + ''.join(f"<url><loc>{url}</loc></url>" for url in urls) + '</urlset>'
    result = _collect(company=company)
    seen = {doc.canonical_url for doc in result.documents}
    assert set(product_urls) <= seen
    assert any("/blog/" in url for url in seen)
    assert len([url for url in company.calls if not url.endswith(("robots.txt", "sitemap.xml"))]) <= WIDE_MAX_PAGES
