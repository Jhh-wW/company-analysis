"""합성 정상 proof와 실제 보존 문구의 회귀. 5차 typed 입력 완전 재현은 아니다."""

import datetime as dt
import hashlib
import json
from dataclasses import replace

import pytest

from src.features.composer.homepage_self_context import normalize_homepage_self_context
from src.features.composer.logic import (
    _compose_one_section, _sanitize_report_to_section_evidence, compose_sections, compose_selected_sections,
)
from src.features.composer.constants import SECTION_IDS
from src.features.composer.port import (
    CollectedFragment, ComposedReport, ComposedSection, ComposedSentence,
    SectionEvidencePacket, SectionEvidencePacketSet,
)
from src.features.composer.render import render_report
from src.features.composer.tests.review_evidence_fixture import review_items
from src.features.composer.verify import verify_report
from src.shared.company_identity import exact_company_name_key
from src.shared.report_evidence.identity_verified_web import (
    build_dart_filing_url_provenance, build_verified_dart_filing_official_web_binding,
)
from src.shared.report_quality.source_identity import document_identity_from_parts
from src.shared.report_evidence.institution_directory_attestation import (
    build_institution_directory_provenance, build_verified_institution_directory_binding,
    directory_search_url,
)
from src.shared.report_evidence.institution_directory_constants import DIRECTORY_ORIGIN


COMPANY = "가나다페이먼츠 주식회사"
SOURCE = "국내 PG 거래액 1위, 가맹점 수 100,000+ 가나다페이먼츠와 함께하는 가맹점"
CLAIM = "가나다페이먼츠는 공식 홈페이지에서 자사를 '국내 PG 거래액 1위' 사업자로 직접 표현하고 있다."
SELF_SLOT = "competitive_position:self_context"
STATED_SLOT = "competitive_position:stated_differentiator"
URL = "https://company.example/"


@pytest.fixture(autouse=True)
def _fixed_collection_day(monkeypatch):
    monkeypatch.setattr("src.features.composer.homepage_self_context.clock.today_kst", lambda: dt.date(2026, 10, 22))


def _fragment(*, company=COMPANY, source=SOURCE):
    # 테스트용 정상 builder만 사용한다. 실제 5차 proof나 서명을 재생성하지 않는다.
    provenance = build_dart_filing_url_provenance(
        company_id="01492697", url=URL, source_document_id="dart_audit_report:20260331000296",
        source_receipt_no="20260331000296", source_member_name="homepage.xml",
        source_location="raw_xml_chars:20-80", source_document_sha256="a" * 64,
        source_payload_sha256="b" * 64,
    )
    binding = build_verified_dart_filing_official_web_binding(
        provenance_value=provenance, company_id="01492697", company_name=company,
        company_registration_numbers=("1234567890",), candidate_url=URL,
        effective_urls=(URL,), scope_sha256="c" * 64, scope_allows=lambda value: value == URL,
        identity_evidence_sha256="a" * 64,
        matched_name_sha256=hashlib.sha256(exact_company_name_key(company).encode()).hexdigest(),
        registration_number_sha256=hashlib.sha256(b"1234567890").hexdigest(),
    )
    assert binding
    return CollectedFragment(
        "20", "typed-evidence-v1:test", source, source_url=URL, document_title=company,
        location="https://company.example/ · 목록 6번째 항목",
        document_identity=document_identity_from_parts(url=URL), document_content_sha256="d" * 64,
        supported_claim_slots=(SELF_SLOT,), formal_source_kind="official_identity_verified_web_page",
        source_document_id="homepage-root", source_publisher="company.example",
        identity_binding=binding, source_collected_on="2026-10-22",
    )


def _sentence(**changes):
    return replace(ComposedSentence(CLAIM, ("20",), "확인", planned_claim_slot=STATED_SLOT), **changes)


def test_정확한_홈페이지_소개는_원래_문맥칸으로만_운반하고_승인하지_않는다():
    sentence = _sentence()
    fragment = _fragment()
    actual = normalize_homepage_self_context(sentence, (fragment,), company_name=COMPANY)
    assert actual == replace(sentence, planned_claim_slot=SELF_SLOT)
    assert actual.verification_state == "unverified"
    assert fragment.supported_claim_slots == (SELF_SLOT,)


def test_기관발견과_온사이트_이중신원_정상builder_경로도_문맥인용을_보존한다():
    # 회사와 HTML·지문 모두 합성이다. 실제 5차 proof의 재구성·재승인이 아니다.
    record_url = DIRECTORY_ORIGIN + "/business/enterprise.do?lang=ko&id=1"
    search = (f'<div class="filter_result_total"><em>1</em></div><table><caption>지정기업 목록</caption>'
        f'<thead><tr><th>기업명</th></tr></thead><tbody><tr><td><a href="{record_url}">{COMPANY}</a>'
        '</td></tr></tbody></table>').encode()
    record = (f'<table><caption>기업정보</caption><tbody><tr><th>기업명</th><td>{COMPANY}</td></tr>'
        f'<tr><th>홈페이지 주소</th><td><a href="{URL}">{URL}</a></td></tr></tbody></table>').encode()
    provenance = build_institution_directory_provenance(company_id="01492697", company_name=COMPANY,
        search_url=directory_search_url(COMPANY), search_body=search, record_url=record_url,
        record_body=record, fetched_on="2026-10-22")
    onsite_identity = f"{COMPANY} 사업자등록번호 123-45-67890"
    binding = build_verified_institution_directory_binding(
        provenance_value=provenance, company_id="01492697", company_name=COMPANY,
        company_registration_numbers=("1234567890",), candidate_url=URL, effective_urls=(URL,),
        scope_sha256="c" * 64, scope_allows=lambda value: value == URL,
        identity_evidence_sha256=hashlib.sha256(onsite_identity.encode()).hexdigest(),
        matched_name_sha256=hashlib.sha256(exact_company_name_key(COMPANY).encode()).hexdigest(),
        registration_number_sha256=hashlib.sha256(b"1234567890").hexdigest(), collected_at="2026-10-22")
    assert binding.startswith("institution-directory-official-web-v1:")
    sentence = _sentence()
    fragment = replace(_fragment(), identity_binding=binding)
    assert normalize_homepage_self_context(sentence, (fragment,), company_name=COMPANY) == replace(
        sentence, planned_claim_slot=SELF_SLOT)


@pytest.mark.parametrize("text", [
    CLAIM.replace("직접 표현하고 있다", "소개한다"),
    CLAIM.replace("직접 표현하고 있다", "직접 소개하고 있다"),
    CLAIM.replace("직접 표현하고 있다", "표현한다"),
    CLAIM.replace("'", '"'),
    CLAIM.replace("'국내 PG 거래액 1위'", "‘국내 PG 거래액 1위’"),
    CLAIM.replace("가나다페이먼츠는", "가나다페이먼츠 주식회사는"),
    CLAIM.replace("사업자로 직접 표현하고 있다", "로 소개한다"),
])
def test_회사귀속과_정확인용을_유지한_작성표현변형도_보존한다(text):
    sentence = _sentence(text=text)
    assert normalize_homepage_self_context(sentence, (_fragment(),), company_name=COMPANY) == replace(
        sentence, planned_claim_slot=SELF_SLOT)


def test_실제_보존문구도_합성정상신원에서_문맥인용으로만_운반된다():
    # 실제 5차 조각20 문구만 사용한다. 아래 proof·문서지문은 합성 시험 재료다.
    company = "토스페이먼츠 주식회사"
    source = "국내 PG 거래액 1위, 가맹점 수 100,000+ 토스페이먼츠와 함께하는 가맹점"
    claim = "토스페이먼츠는 공식 홈페이지에서 자사를 '국내 PG 거래액 1위' 사업자로 직접 표현하고 있다."
    sentence = _sentence(text=claim)
    actual = normalize_homepage_self_context(sentence,
        (_fragment(company=company, source=source),), company_name=company)
    assert actual == replace(sentence, planned_claim_slot=SELF_SLOT)


@pytest.mark.parametrize("collected", ["2026-10-22T00:00:00+09:00", "2026-10-21T15:00:00+00:00"])
def test_시간대있는_수집시각도_같은_한국날짜로_검산한다(collected):
    sentence = _sentence()
    actual = normalize_homepage_self_context(sentence,
        (replace(_fragment(), source_collected_on=collected),), company_name=COMPANY)
    assert actual.planned_claim_slot == SELF_SLOT


@pytest.mark.parametrize("changes", [
    {"text": "가나다페이먼츠는 국내 PG 거래액 1위 사업자다."},
    {"text": CLAIM.replace("자사를", "제휴사를")},
    {"text": CLAIM.replace("가나다페이먼츠는", "다른회사는")},
    {"text": CLAIM.replace("국내 PG 거래액 1위", "세계 PG 거래액 1위")},
    {"text": CLAIM.replace("직접 표현하고 있다.", "소개하며 타사보다 우수하다.")},
    {"grade": "해석"}, {"verification_state": "verified"},
    {"citations": ("20", "21")}, {"planned_claim_slot": ""},
])
def test_객관순위나_주체범위추가나_기존승인을_문맥인용으로_구제하지_않는다(changes):
    sentence = _sentence(**changes)
    assert normalize_homepage_self_context(sentence, (_fragment(),), company_name=COMPANY) is sentence


@pytest.mark.parametrize("changes", [
    {"identity_binding": ""}, {"identity_binding": "directory-url-only"},
    {"source_url": "https://other.example/"}, {"source_url": "http://company.example/"},
    {"source_url": "https://company.example/customers"},
    {"source_url": "https://company.example/products"},
    {"source_url": "https://company.example/?review=1"},
    {"formal_source_kind": "official_web_page"}, {"formal_source_kind": "news"},
    {"source_collected_on": "2026-10-21"}, {"source_collected_on": "2026-10-23"},
    {"source_collected_on": ""}, {"document_content_sha256": ""},
    {"source_collected_on": "2026-10-22T00:00:00"},
    {"source_collected_on": "2026-10-22-invalid"},
    {"source_document_id": ""}, {"supported_claim_slots": ()},
    {"document_identity": "document:other.example:root"},
    {"text": SOURCE.replace("가나다페이먼츠와", "다른회사와")},
    {"text": SOURCE + "라는 사용자 후기다."},
    {"text": SOURCE + "가 되는 것이 목표다."},
    {"text": SOURCE.replace("국내 PG 거래액", "국내 제품 거래액")},
    {"text": SOURCE.replace("국내 PG 거래액", "가맹점 고객 거래액")},
    {"text": SOURCE.replace("국내 PG 거래액", "국내 브랜드페이 거래액")},
    {"text": SOURCE.replace("1위,", "1위(일부 상품 기준),")},
])
def test_공식이중검증_당일_회사전체_원문_경계가_없으면_그대로_제외대상이다(changes):
    sentence = _sentence()
    fragment = replace(_fragment(), **changes)
    assert normalize_homepage_self_context(sentence, (fragment,), company_name=COMPANY) is sentence


def test_원문회사명이_맞아도_다른법인의_정상proof를_빌리지_못한다():
    sentence = _sentence()
    fragment = replace(_fragment(), identity_binding=_fragment(company="다른회사 주식회사").identity_binding)
    assert normalize_homepage_self_context(sentence, (fragment,), company_name=COMPANY) is sentence


def test_검수전_실제연결에서_정확문구만_원래지원칸에_남는다():
    fragment = _fragment()
    response = json.dumps({"문장들": [{"글": CLAIM, "인용": ["20"], "등급": "확인", "주장슬롯": STATED_SLOT}]}, ensure_ascii=False)
    section = _compose_one_section("competitive_position", "", lambda _: response,
        source_fragments=(fragment,), company_name=COMPANY)
    sanitized = _sanitize_report_to_section_evidence(
        ComposedReport((section,)), {"competitive_position": frozenset({"20"})},
        supported_claim_slots_by_fragment_id={"20": frozenset({SELF_SLOT})},
        enforce_declared_claim_slot_support=True,
    )
    assert len(sanitized.sections[0].sentences) == 1
    sentence = sanitized.sections[0].sentences[0]
    assert sentence.text == CLAIM and sentence.planned_claim_slot == SELF_SLOT
    assert sentence.verification_state == "unverified"


def test_전체작성_호출부도_회사명과_원래근거를_전달한다():
    fragment = _fragment()
    response = json.dumps({"문장들": [{"글": CLAIM, "인용": ["20"], "등급": "확인", "주장슬롯": STATED_SLOT}]}, ensure_ascii=False)
    report = compose_sections(COMPANY, (fragment,), None, lambda _: response)
    section = next(item for item in report.sections if item.section_id == "competitive_position")
    assert section.sentences[0].planned_claim_slot == SELF_SLOT


def test_선택장작성_호출부도_회사명을_전달하고_지원칸_경계를_지킨다():
    fragment = _fragment()
    generation = "a" * 64
    packets = SectionEvidencePacketSet("01492697", generation, tuple(
        SectionEvidencePacket("01492697", generation, section_id,
            (fragment,) if section_id == "competitive_position" else (
                CollectedFragment(str(index), "공식 자료", "공식 원문", source_url=f"https://example.com/{index}",
                    document_identity=f"document:example.com:{index}", document_content_sha256=f"{index:064x}"),))
        for index, section_id in enumerate(SECTION_IDS, start=1)))
    response = json.dumps({"문장들": [{"글": CLAIM, "인용": ["20"], "등급": "확인", "주장슬롯": STATED_SLOT}]}, ensure_ascii=False)
    report = compose_selected_sections(COMPANY, None, lambda _: response,
        section_evidence_packets=packets, section_ids=("competitive_position",))
    assert len(report.sections[0].sentences) == 1
    assert report.sections[0].sentences[0].planned_claim_slot == SELF_SLOT


@pytest.mark.parametrize("verdict", ["참", "거짓"])
def test_독립검수가_새로_승인한_경우에만_남는다(verdict):
    fragment = _fragment()
    sentence = normalize_homepage_self_context(_sentence(), (fragment,), company_name=COMPANY)
    def ask(prompt):
        return json.dumps({"판정": [{"번호": item.number, "장": item.section,
            "근거": list(item.citations), "결과": verdict, "검증근거": {}}
            for item in review_items(prompt)]}, ensure_ascii=False)
    result = verify_report(ComposedReport((ComposedSection("competitive_position", (sentence,)),)),
        (fragment,), None, ask, allow_sentence_rewrite=False)
    assert bool(result.sections[0].sentences) is (verdict == "참")
    if verdict == "참":
        assert result.sections[0].sentences[0].verification_state == "verified"
        rendered = render_report(COMPANY, result, (fragment,), None, as_of_date="2026-10-22")
        assert len(rendered.fact_records) == 1
        fact = rendered.fact_records[0]
        assert fact.claim_slot == SELF_SLOT and fact.claim == CLAIM
        assert fact.source_date == "2026-10-22"
        assert rendered.citations[0].published_at == ""
        assert rendered.citations[0].collected_at == "2026-10-22"
