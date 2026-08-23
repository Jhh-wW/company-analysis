"""출처 목록 시험 — 왕복(쓰기→읽기) 일치가 핵심이다.

정본: 확정/07_출력/2_규칙/01_배치와근거표기.md
"""

from __future__ import annotations

from dataclasses import replace
import hashlib
import hmac
import json

from src.features.provenance import sources as sources_module

import pytest

from src.shared.official_ir import IR_DART_WWW_REDIRECT_VALUE

from src.features.provenance.sources import (
    Source,
    SourceKind,
    count_missing_dates,
    evidence_text_hash,
    exact_evidence_text_hash,
    has_valid_provenance_seal,
    is_canonical_official_with_registry,
    official_domain_attestation_problem,
    official_web_currentness_is_usable,
    official_web_url_is_search_result,
    official_web_url_requires_document_date,
    parse_sources,
    render_sources,
    seal_collected_source,
    visible_citations,
)


@pytest.mark.parametrize(
    "url",
    [
        "https://company.example/newsdetail/1",
        "https://NEWS.company.example/about",
        "https://company.example/%256eewsroom/article",
        "https://company.example/?section=newsroom",
        "https://company.example/view?path=%252Fpress%252Frelease",
        "https://company.example/about?id=1",
        "https://company.example/company/2015/strategy",
        "https://company.example/search?q=competition",
        "https://company.example/results?keyword=competition",
        "https://company.example/find?query=competition",
    ],
)
def test_역사문서형_공식웹_URL은_host_path_query우회를_닫는다(url: str) -> None:
    assert official_web_url_requires_document_date(url)


def test_안정페이지와_language_query는_수집일현재확인을_유지한다() -> None:
    url = "https://company.example/company/about?lang=ko"

    assert not official_web_url_requires_document_date(url)
    assert official_web_currentness_is_usable(
        source_type="회사 공식 웹",
        url=url,
        collected_at="2026-08-23",
    )
    assert not official_web_currentness_is_usable(
        source_type="회사 공식 웹",
        url=url,
        published_at="2015-01-01",
        collected_at="2026-08-23",
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://search.company.example/about",
        "https://company.example/search/results",
        "https://company.example/?q=beta",
        "https://company.example/?%2571=beta",
    ],
)
def test_검색결과_URL은_최근문서일이_있어도_공개사실로_쓰지않는다(url: str) -> None:
    assert official_web_url_is_search_result(url)
    assert not official_web_currentness_is_usable(
        source_type="회사 공식 웹",
        url=url,
        published_at="2026-08-01",
        collected_at="2026-08-23",
        reference_date="2026-08-23",
    )


@pytest.mark.parametrize(
    ("published_at", "expected"),
    [
        ("", False),
        ("not-a-date", False),
        ("2027-01-01", False),
        ("2015-01-01", False),
        ("2026-08-01", True),
    ],
)
def test_역사문서형_공식웹은_검증된_최근문서일만_현재로_쓴다(
    published_at: str,
    expected: bool,
) -> None:
    assert official_web_currentness_is_usable(
        source_type="회사 공식 웹",
        url="https://company.example/newsroom/item",
        published_at=published_at,
        collected_at="2026-08-23",
    ) is expected

# 기획서 예시 그대로 — 줄이면 시험의 뜻이 없어진다.
공시_출처 = Source(
    number=2,
    kind=SourceKind.FILING,
    label="감사보고서 제16장 수익인식 주석",
    disclosed_at="2024-03-15",
    collected_at="2026-08-13",
)
뉴스_출처 = Source(
    number=5,
    kind=SourceKind.NEWS,
    label="OO경제",
    published_at="2025-03-12",
    domain="mk.co.kr",
)

기획서_예시_출처목록 = (
    "[출처]\n"
    " [2] 감사보고서 제16장 수익인식 주석\n"
    "     2024-03-15 공시 · 수집 2026-08-13\n"
    " [5] OO경제 2025-03-12  (mk.co.kr)"
)


# ══════════════════════════════════════════════════════════
# is_valid — 뉴스는 보도일 · 도메인이 반드시 있어야 한다
# ══════════════════════════════════════════════════════════


def test_공시_출처는_날짜가_없어도_유효하다():
    assert Source(number=1, kind=SourceKind.FILING, label="사업보고서").is_valid is True


def test_뉴스는_보도일과_도메인이_있어야_유효하다():
    assert 뉴스_출처.is_valid is True


@pytest.mark.parametrize(
    "kwargs",
    [
        {"published_at": "", "domain": "mk.co.kr"},
        {"published_at": "2025-03-12", "domain": ""},
        {"published_at": "", "domain": ""},
    ],
)
def test_뉴스는_보도일이나_도메인이_빠지면_무효다(kwargs):
    source = Source(number=5, kind=SourceKind.NEWS, label="OO경제", **kwargs)
    assert source.is_valid is False


def test_번호가_0이하면_무효다():
    assert Source(number=0, kind=SourceKind.FILING, label="사업보고서").is_valid is False


def test_이름이_비어_있으면_무효다():
    assert Source(number=1, kind=SourceKind.FILING, label="  ").is_valid is False


def test_canonical_source_requires_identity_location_status_and_direct_url():
    source = Source(
        number=1,
        kind=SourceKind.FILING,
        label="2025 사업보고서",
        disclosed_at="2026-03-18",
        source_id="src-1",
        title="2025 사업보고서",
        publisher="가나다 주식회사",
        host="dart.fss.or.kr",
        url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=202603180001",
        document_id="202603180001",
        location="PDF p.12 사업의 내용",
        source_type="공식 공시",
        fact_status="실제",
        used_in=["identity"],
        evidence_hashes=[evidence_text_hash("가나다는 소재 기업이다")],
    )

    assert source.is_canonical_valid is True
    exact = exact_evidence_text_hash("가나다는 소재 기업이다")
    assert replace(source, exact_evidence_hashes=[exact]).is_canonical_valid is True
    assert (
        replace(source, exact_evidence_hashes=[exact, exact]).is_canonical_valid
        is False
    )
    assert replace(source, exact_evidence_hashes=["A" * 64]).is_canonical_valid is False
    source = seal_collected_source(source)
    assert source.is_canonical_official is True
    assert replace(source, publisher="").is_canonical_valid is False
    assert replace(source, location="").is_canonical_valid is False
    assert replace(source, url="검색결과").is_canonical_valid is False


def test_canonical_metadata_does_not_promote_external_news_to_official_evidence():
    source = Source(
        number=5,
        kind=SourceKind.NEWS,
        label="회사 전략 분석",
        published_at="2026-08-13",
        domain="news.example",
        source_id="src-news-5",
        title="회사 전략 분석",
        publisher="OO경제",
        host="news.example",
        url="https://news.example/5",
        document_id="article-5",
        location="기사 본문",
        source_type="공식 분석 기사",
        fact_status="보도 확인",
        evidence_hashes=[evidence_text_hash("회사의 전략을 분석했다")],
    )

    assert source.is_canonical_valid is True
    assert source.is_canonical_official is False


@pytest.mark.parametrize(
    ("kind", "source_type"),
    [
        (SourceKind.OTHER, "회사 공식 IR"),
        (SourceKind.OTHER, "회사 공식 웹"),
        (SourceKind.OTHER, "공식 파트너 자료"),
        (SourceKind.OTHER, "규제기관 공식 자료"),
        (SourceKind.FILING, "공식 재무 API"),
    ],
)
def test_explicit_official_source_classes_are_eligible(kind, source_type):
    is_filing = kind is SourceKind.FILING
    host = "dart.fss.or.kr" if is_filing else "official.example"
    document_id = "doc-3"
    attestation_evidence = "사업보고서 회사 개요: 홈페이지 https://official.example"
    url = (
        f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={document_id}"
        if is_filing
        else "https://official.example/3"
    )
    source = Source(
        number=3,
        kind=kind,
        label="공식 원문",
        collected_at="2026-08-13",
        source_id="src-official-3",
        title="공식 원문",
        publisher="공식 발행 주체",
        host=host,
        url=url,
        document_id=document_id,
        location="본문",
        source_type=source_type,
        fact_status="실제",
        evidence_hashes=[evidence_text_hash("확인된 공식 사실")],
        domain_attestation_source_id=""
        if is_filing
        else "src-domain-attestation",
        domain_attestation_evidence="" if is_filing else attestation_evidence,
    )

    source = seal_collected_source(source)
    assert source.is_canonical_official is True
    if not is_filing:
        attester = Source(
            number=30,
            kind=SourceKind.FILING,
            label="공식 발행 주체 사업보고서",
            disclosed_at="2026-03-18",
            source_id="src-domain-attestation",
            title="공식 발행 주체 사업보고서",
            publisher="공식 발행 주체",
            host="dart.fss.or.kr",
            url=(
                "https://dart.fss.or.kr/dsaf001/main.do?"
                "rcpNo=20260318000030"
            ),
            document_id="20260318000030",
            location="I. 회사의 개요",
            source_type="공식 공시",
            fact_status="실제",
            evidence_hashes=[evidence_text_hash(attestation_evidence)],
        )
        attester = seal_collected_source(attester)
        assert is_canonical_official_with_registry(source, [source, attester]) is True


def test_official_other_domain_requires_independent_filing_evidence_binding():
    evidence = "사업보고서 회사 개요: 홈페이지 https://company.example"
    filing = Source(
        number=1,
        kind=SourceKind.FILING,
        label="2025 사업보고서",
        disclosed_at="2026-03-18",
        source_id="src-filing",
        title="2025 사업보고서",
        publisher="가나다 주식회사",
        host="dart.fss.or.kr",
        url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=202603180001",
        document_id="202603180001",
        location="I. 회사의 개요",
        source_type="공식 공시",
        fact_status="실제",
        evidence_hashes=[evidence_text_hash(evidence)],
    )
    website = Source(
        number=2,
        kind=SourceKind.OTHER,
        label="회사 소개",
        collected_at="2026-08-20",
        source_id="src-website",
        title="회사 소개",
        publisher="가나다 주식회사",
        host="company.example",
        url="https://company.example/about",
        document_id="about",
        location="About",
        source_type="회사 공식 웹",
        fact_status="현재",
        evidence_hashes=[evidence_text_hash("회사는 센서를 만든다")],
        domain_attestation_source_id=filing.source_id,
        domain_attestation_evidence=evidence,
    )

    filing = seal_collected_source(filing)
    website = seal_collected_source(website)
    assert website.is_canonical_official is True
    assert official_domain_attestation_problem(website, [filing, website]) == ""
    assert is_canonical_official_with_registry(website, [filing, website]) is True


@pytest.mark.parametrize(
    ("hm_url", "website_host", "extra_field", "redirect_marker", "expected"),
    [
        ("jype.com", "jype.com", False, False, True),
        ("http://jype.com", "jype.com", False, False, True),
        ("https://jype.com", "jype.com", False, False, True),
        ("https://jype.com", "www.jype.com", False, False, False),
        ("https://jype.com", "www.jype.com", False, True, True),
        ("https://jype.com", "ir.jype.com", False, True, False),
        ("https://user@jype.com", "jype.com", False, False, False),
        ("https://jype.com:8443", "jype.com", False, False, False),
        ("http://127.0.0.1", "127.0.0.1", False, False, False),
        ("jype.com", "jype.com", True, False, False),
    ],
)
def test_DART_profile_JSON만_scheme없는_공식host를_안전하게_증명한다(
    hm_url: str,
    website_host: str,
    extra_field: bool,
    redirect_marker: bool,
    expected: bool,
) -> None:
    profile = {
        "corp_code": "00000001",
        "corp_name": "주식회사 알파",
        "hm_url": hm_url,
    }
    if extra_field:
        profile["stock_code"] = "000001"
    evidence = json.dumps(
        profile,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    attester = seal_collected_source(
        Source(
            number=30,
            kind=SourceKind.FILING,
            label="주식회사 알파 OpenDART 기업개황",
            collected_at="2026-08-23",
            source_id="dart-company-profile-00000001",
            title="OpenDART 기업개황",
            publisher="주식회사 알파",
            host="opendart.fss.or.kr",
            url=(
                "https://opendart.fss.or.kr/api/company.json?"
                "corp_code=00000001"
            ),
            document_id="00000001",
            location="기업개황 API · corp_code/corp_name/hm_url",
            source_type="규제기관 공식 자료",
            fact_status="기준일 현재 확인",
            evidence_hashes=[evidence_text_hash(evidence)],
        )
    )
    website = seal_collected_source(
        Source(
            number=2,
            kind=SourceKind.OTHER,
            label="주식회사 알파 공식 웹",
            collected_at="2026-08-23",
            source_id="source-alpha-web",
            title="회사 소개",
            publisher="주식회사 알파",
            host=website_host,
            url=f"https://{website_host}/about",
            document_id="about",
            location="/about",
            source_type="회사 공식 IR" if redirect_marker else "회사 공식 웹",
            fact_status="기준일 현재 확인",
            evidence_hashes=[evidence_text_hash("당사는 장비를 공급한다.")],
            domain_attestation_source_id=attester.source_id,
            domain_attestation_evidence=evidence,
            domain_redirect_verification=(
                IR_DART_WWW_REDIRECT_VALUE if redirect_marker else ""
            ),
            domain_redirect_from_host="jype.com" if redirect_marker else "",
            domain_redirect_to_host=website_host if redirect_marker else "",
        )
    )

    assert is_canonical_official_with_registry(website, [website, attester]) is expected


def test_declared_official_other_cannot_self_promote_or_swap_to_fake_domain():
    evidence = "사업보고서 회사 개요: 홈페이지 https://company.example"
    filing = Source(
        number=1,
        kind=SourceKind.FILING,
        label="사업보고서",
        disclosed_at="2026-03-18",
        source_id="src-filing",
        title="사업보고서",
        publisher="가나다",
        host="kind.krx.co.kr",
        url=(
            "https://kind.krx.co.kr/external/2026/03/18/000001/"
            "202603180001/11011.htm"
        ),
        document_id="202603180001",
        location="회사 개요",
        source_type="공식 공시",
        fact_status="실제",
        evidence_hashes=[evidence_text_hash(evidence)],
    )
    website = Source(
        number=2,
        kind=SourceKind.OTHER,
        label="회사 웹",
        collected_at="2026-08-20",
        source_id="src-web",
        title="회사 웹",
        publisher="가나다",
        host="company.example",
        url="https://company.example/about",
        document_id="about",
        location="About",
        source_type="회사 공식 웹",
        fact_status="현재",
        evidence_hashes=[evidence_text_hash("회사 소개")],
        domain_attestation_source_id=filing.source_id,
        domain_attestation_evidence=evidence,
    )

    filing = seal_collected_source(filing)
    website = seal_collected_source(website)

    no_attestation = replace(
        website,
        domain_attestation_source_id="",
        domain_attestation_evidence="",
    )
    assert no_attestation.is_canonical_official is False

    forged = replace(
        website,
        host="news.example",
        url="https://news.example/fake-story",
        document_id="fake-story",
    )
    assert forged.is_canonical_official is True
    assert is_canonical_official_with_registry(forged, [filing, forged]) is False
    assert "정확한 host URL" in official_domain_attestation_problem(
        forged, [filing, forged]
    )

    self_attested = replace(
        website,
        domain_attestation_source_id=website.source_id,
        domain_attestation_evidence="회사 소개 https://company.example",
        evidence_hashes=[
            evidence_text_hash("회사 소개"),
            evidence_text_hash("회사 소개 https://company.example"),
        ],
    )
    assert is_canonical_official_with_registry(self_attested, [self_attested]) is False


def test_provenance_seal_rejects_coordinated_source_and_attestation_tampering():
    original_evidence = "annual filing homepage https://company.example"
    forged_evidence = "annual filing homepage https://news.example"
    filing = seal_collected_source(
        Source(
            number=1,
            kind=SourceKind.FILING,
            label="Annual filing",
            disclosed_at="2026-03-18",
            source_id="src-filing-sealed",
            title="Annual filing",
            publisher="Example Corp",
            host="dart.fss.or.kr",
            url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=202603180099",
            document_id="202603180099",
            location="Company overview",
            source_type="공식 공시",
            fact_status="실제",
            evidence_hashes=[evidence_text_hash(original_evidence)],
        )
    )
    website = seal_collected_source(
        Source(
            number=2,
            kind=SourceKind.OTHER,
            label="Official website",
            collected_at="2026-08-20",
            source_id="src-web-sealed",
            title="Official website",
            publisher="Example Corp",
            host="company.example",
            url="https://company.example/about",
            document_id="about",
            location="About",
            source_type="회사 공식 웹",
            fact_status="현재",
            evidence_hashes=[evidence_text_hash("Example Corp overview")],
            domain_attestation_source_id=filing.source_id,
            domain_attestation_evidence=original_evidence,
        )
    )

    assert is_canonical_official_with_registry(website, [filing, website]) is True

    forged_filing = replace(
        filing,
        evidence_hashes=[
            *filing.evidence_hashes,
            evidence_text_hash(forged_evidence),
        ],
    )
    forged_website = replace(
        website,
        host="news.example",
        url="https://news.example/fake-story",
        document_id="fake-story",
        domain_attestation_evidence=forged_evidence,
    )

    assert has_valid_provenance_seal(forged_filing) is False
    assert has_valid_provenance_seal(forged_website) is False
    assert (
        is_canonical_official_with_registry(
            forged_website, [forged_filing, forged_website]
        )
        is False
    )


def test_기본_citation_HMAC은_legacy_payload와_byte_호환이다() -> None:
    source = Source(
        number=8,
        kind=SourceKind.FILING,
        label="사업보고서",
        disclosed_at="2026-03-15",
        collected_at="2026-08-23",
        source_id="legacy-source-8",
        title="사업보고서",
        publisher="주식회사 알파",
        host="dart.fss.or.kr",
        url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260315000001",
        document_id="20260315000001",
        location="사업의 내용",
        source_type="공식 공시",
        fact_status="공시 실제값",
        evidence_hashes=[evidence_text_hash("원문")],
    )
    legacy_payload = {
        "number": source.number,
        "kind": source.kind.value,
        "label": source.label,
        "disclosed_at": source.disclosed_at,
        "collected_at": source.collected_at,
        "published_at": source.published_at,
        "domain": source.domain,
        "source_id": source.source_id,
        "title": source.title,
        "publisher": source.publisher,
        "host": source.host,
        "url": source.url,
        "document_id": source.document_id,
        "location": source.location,
        "source_type": source.source_type,
        "fact_status": source.fact_status,
        "evidence_hashes": sorted(source.evidence_hashes),
        "domain_attestation_source_id": source.domain_attestation_source_id,
        "domain_attestation_evidence": source.domain_attestation_evidence,
    }
    encoded = json.dumps(
        legacy_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    legacy_seal = hmac.new(
        sources_module._PROVENANCE_SEAL_KEY,
        encoded,
        hashlib.sha256,
    ).hexdigest()

    assert seal_collected_source(source).provenance_seal == legacy_seal
    assert has_valid_provenance_seal(
        replace(source, provenance_seal=legacy_seal)
    )


def test_exact_원문해시는_대소문자를_보존하고_HMAC에_결속된다() -> None:
    upper = exact_evidence_text_hash("US is our competitor.")
    lower = exact_evidence_text_hash("us is our competitor.")

    assert upper != lower
    assert evidence_text_hash("US is our competitor.") == evidence_text_hash(
        "us is our competitor."
    )

    source = seal_collected_source(
        Source(
            number=9,
            kind=SourceKind.FILING,
            label="경쟁 현황",
            evidence_hashes=[evidence_text_hash("US is our competitor.")],
            exact_evidence_hashes=[upper],
            provenance_role="attestation_only",
        )
    )

    assert has_valid_provenance_seal(source)
    assert not has_valid_provenance_seal(
        replace(source, exact_evidence_hashes=[lower])
    )
    assert not has_valid_provenance_seal(
        replace(source, provenance_role="citation")
    )


def test_공식IR_실제첨부URL은_HMAC에_결속된다() -> None:
    source = seal_collected_source(
        Source(
            number=9,
            kind=SourceKind.OTHER,
            label="26년 2분기 IR자료",
            attachment_url="https://cdn.example/alpha-q2.pdf",
        )
    )

    assert has_valid_provenance_seal(source)
    assert not has_valid_provenance_seal(
        replace(source, attachment_url="https://cdn.example/changed.pdf")
    )


@pytest.mark.parametrize(
    ("raw", "normalized"),
    [
        ("K supplier", "k supplier"),
        ("ſ market", "s market"),
        ("ﬁ product", "fi product"),
        ("Alpha  Beta", "Alpha Beta"),
    ],
)
def test_exact_원문해시는_casefold_유니코드와_내부공백_충돌을_구분한다(
    raw: str,
    normalized: str,
) -> None:
    assert evidence_text_hash(raw) == evidence_text_hash(normalized)
    assert exact_evidence_text_hash(raw) != exact_evidence_text_hash(normalized)


def test_attestation_only_역할은_seal에_결속되고_모든_공개목록에서_숨는다() -> None:
    citation = Source(number=1, kind=SourceKind.FILING, label="공개 사업보고서")
    attester = seal_collected_source(
        Source(
            number=2,
            kind=SourceKind.FILING,
            label="내부 OpenDART 기업개황",
            provenance_role="attestation_only",
        )
    )

    assert visible_citations([citation, attester, object()]) == [citation]
    assert "공개 사업보고서" in render_sources([citation, attester])
    assert "내부 OpenDART 기업개황" not in render_sources([citation, attester])
    assert has_valid_provenance_seal(attester)
    assert not has_valid_provenance_seal(
        replace(attester, provenance_role="citation")
    )


# ══════════════════════════════════════════════════════════
# 쓰기 — 기획서 예시와 문자 그대로 같아야 한다
# ══════════════════════════════════════════════════════════


def test_기획서_예시와_렌더링_결과가_한_글자도_다르지_않다():
    assert render_sources([공시_출처, 뉴스_출처]) == 기획서_예시_출처목록


def test_공시일만_있으면_공시일만_적는다():
    source = Source(
        number=1, kind=SourceKind.FILING, label="사업보고서", disclosed_at="2024-03-15"
    )
    assert render_sources([source]) == "[출처]\n [1] 사업보고서\n     2024-03-15 공시"


def test_수집일만_있으면_수집일만_적는다():
    source = Source(
        number=1, kind=SourceKind.FILING, label="사업보고서", collected_at="2026-08-13"
    )
    assert render_sources([source]) == "[출처]\n [1] 사업보고서\n     수집 2026-08-13"


def test_검증된_공식IR은_발행일_기준기간_확인일을_왕복한다():
    source = Source(
        number=1,
        kind=SourceKind.OTHER,
        label="26년 2분기 IR자료",
        collected_at="2026-08-24",
        published_at="2026-08-12",
        source_type="회사 공식 IR",
        reporting_period="2026-Q2",
    )

    rendered = render_sources([source])

    assert rendered == (
        "[출처]\n"
        " [1] 26년 2분기 IR자료\n"
        "     발행 2026-08-12 · 기준 2026-Q2 · 확인 2026-08-24"
    )
    assert parse_sources(rendered) == [source]


def test_날짜가_전혀_없으면_둘째_줄이_없다():
    source = Source(number=1, kind=SourceKind.OTHER, label="회사 홈페이지")
    assert render_sources([source]) == "[출처]\n [1] 회사 홈페이지"


def test_빈_목록은_머리말만_낸다():
    assert render_sources([]) == "[출처]"


# ══════════════════════════════════════════════════════════
# 읽기 — 기획서 예시를 그대로 되읽는다
# ══════════════════════════════════════════════════════════


def test_기획서_예시를_파싱하면_두_출처가_나온다():
    parsed = parse_sources(기획서_예시_출처목록)
    assert len(parsed) == 2
    assert parsed[0] == 공시_출처
    assert parsed[1] == 뉴스_출처


def test_번호는_새로_매기지_않고_그대로_읽는다():
    """AI가 고른 조각 번호(2·5처럼 건너뛴 번호)를 그대로 보존한다."""
    parsed = parse_sources(기획서_예시_출처목록)
    assert [s.number for s in parsed] == [2, 5]


def test_출처가_없는_블록은_빈_목록이다():
    assert parse_sources("[출처]") == []


def test_출처_블록이_아닌_글자는_무시한다():
    잡음_섞인_글 = "아무 상관 없는 문장\n\n" + 기획서_예시_출처목록 + "\n뒤에 붙은 잡음"
    parsed = parse_sources(잡음_섞인_글)
    assert len(parsed) == 2


# ══════════════════════════════════════════════════════════
# ★ 왕복(쓰기 → 읽기) 일치 — 이 시험이 이 파일의 핵심이다
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    "source",
    [
        공시_출처,
        뉴스_출처,
        Source(
            number=1, kind=SourceKind.FILING, label="사업보고서", disclosed_at="2024-03-15"
        ),
        Source(
            number=3, kind=SourceKind.FILING, label="사업보고서", collected_at="2026-08-13"
        ),
        Source(number=9, kind=SourceKind.OTHER, label="회사 홈페이지"),
    ],
)
def test_출처_하나를_쓰고_다시_읽으면_같다(source):
    round_tripped = parse_sources(render_sources([source]))
    assert round_tripped == [source]


def test_출처_목록_전체를_쓰고_다시_읽으면_같다():
    원본 = [
        공시_출처,
        뉴스_출처,
        Source(number=7, kind=SourceKind.OTHER, label="채용공고 원문"),
    ]
    왕복 = parse_sources(render_sources(원본))
    assert 왕복 == 원본


def test_두_번_왕복해도_더_이상_바뀌지_않는다():
    """render→parse→render→parse — 두 번째 왕복부터는 반드시 안정돼야 한다."""
    원본 = [공시_출처, 뉴스_출처]
    한번 = parse_sources(render_sources(원본))
    두번 = parse_sources(render_sources(한번))
    assert 한번 == 두번 == 원본


# ══════════════════════════════════════════════════════════
# 출처·수집일 누락 집계 (C3)
# ══════════════════════════════════════════════════════════


def test_날짜가_다_있으면_누락_0건이다():
    assert count_missing_dates([공시_출처]) == 0


def test_공시일이나_수집일이_하나라도_없으면_누락으로_센다():
    반쪽 = Source(
        number=1, kind=SourceKind.FILING, label="사업보고서", disclosed_at="2024-03-15"
    )
    assert count_missing_dates([반쪽]) == 1


def test_뉴스는_공시_날짜_누락_집계에서_빠진다():
    """뉴스의 보도일 검사는 is_valid가 이미 한다 — 이중으로 세지 않는다."""
    assert count_missing_dates([뉴스_출처]) == 0
