"""출처 목록 시험 — 왕복(쓰기→읽기) 일치가 핵심이다.

"""

from __future__ import annotations

from dataclasses import replace
import hashlib
import hmac
import json

from src.features.provenance import sources as sources_module
from src.features.company_comparison.official_sources import (
    bind_dart_profile_attestation,
)

import pytest

from src.shared.official_ir import IR_DART_WWW_REDIRECT_VALUE

from src.features.provenance.sources import (
    Source,
    SourceKind,
    count_missing_dates,
    evidence_text_hash,
    exact_evidence_text_hash,
    full_typed_source_registry_problem,
    has_valid_provenance_seal,
    is_canonical_official_with_registry,
    official_domain_attestation_problem,
    official_web_currentness_is_usable,
    official_web_url_is_search_result,
    official_web_url_requires_document_date,
    parse_sources,
    render_sources,
    seal_collected_source,
    stored_sources_seal_problem,
    visible_citations,
)
from src.shared.report_evidence.constants import (
    FORMAL_DOCUMENT_SOURCE_KINDS,
    OFFICIAL_WEB_SOURCE_KINDS,
    SOURCE_KIND_OFFICIAL_IDENTITY_VERIFIED_WEB_PAGE,
    SOURCE_KIND_OFFICIAL_IR_PDF,
    SOURCE_KIND_OFFICIAL_RECRUIT_PAGE,
)
from src.shared.report_evidence.identity_verified_web import (
    build_dart_filing_url_provenance,
    build_verified_dart_filing_official_web_binding,
)
from src.shared.report_evidence.profile_domain_attestation import (
    build_registered_subdomain_profile_attestation,
)
from src.shared.official_ir import IR_METADATA_VERIFICATION_VALUE


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

# 기준 문서 예시 그대로 — 줄이면 시험의 뜻이 없어진다.
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

기준_문서_예시_출처목록 = (
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
            # apex→www proof는 IR 전용이 아니다. 일반 웹·채용도 같은 수집
            # redirect 영수증을 타므로 fixture가 IR로 위장해 통과시키지 않는다.
            source_type="회사 공식 웹",
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


def test_DART_profile의_실제_하위도메인은_attester_hash와_Source_seal까지_검산한다(
) -> None:
    bound = bind_dart_profile_attestation(
        {},
        profile={
            "status": "000",
            "corp_code": "00126380",
            "corp_name": "가나다전자",
            "hm_url": "https://company.example/",
        },
        corp_code="00126380",
        company_name="가나다전자",
        collected_on="2026-09-04",
    )
    assert bound.attester is not None
    proof = build_registered_subdomain_profile_attestation(
        bound.attester.domain_attestation_evidence,
        source_url="https://recruit.company.example/jobs",
    )
    source = seal_collected_source(
        Source(
            number=1,
            kind=SourceKind.OTHER,
            label="가나다전자 채용",
            collected_at="2026-09-04",
            source_id="formal-recruit",
            title="인재 채용",
            publisher="가나다전자",
            host="recruit.company.example",
            url="https://recruit.company.example/jobs",
            document_id="recruit-jobs",
            location="채용 본문",
            source_type="회사 공식 웹",
            fact_status="기준일 현재 확인",
            evidence_hashes=[evidence_text_hash("가나다전자는 인재를 채용한다.")],
            exact_evidence_hashes=[
                exact_evidence_text_hash("가나다전자는 인재를 채용한다.")
            ],
            domain_attestation_source_id=bound.attester.source_id,
            domain_attestation_evidence=proof,
            formal_source_kind=SOURCE_KIND_OFFICIAL_RECRUIT_PAGE,
            identity_binding="DART 기업개황 root의 실제 등록 하위도메인",
            document_content_sha256="d" * 64,
        )
    )
    registry = (source, bound.attester)

    assert is_canonical_official_with_registry(source, registry)
    assert full_typed_source_registry_problem(
        source,
        registry,
        reference_date="2026-09-04",
    ) == ""

    sibling_proof = build_registered_subdomain_profile_attestation(
        bound.attester.domain_attestation_evidence,
        source_url="https://jobs.company.example/jobs",
    )
    tampered = seal_collected_source(
        replace(
            source,
            domain_attestation_evidence=sibling_proof,
            provenance_seal="",
        )
    )
    assert not is_canonical_official_with_registry(
        tampered,
        (tampered, bound.attester),
    )


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


def test_다른_값은_그대로고_도장만_어긋나도_공식_원문_판정이_내려간다() -> None:
    """도장 «자체»가 최종 판정을 지키는지 홀로 확인한다.

    ★ 왜 따로 필요한가 — 위 시험은 host·원문 조각을 함께 바꾼다. 그러면 도메인
      증명 검사가 먼저 걸려서, 최종 판정에서 도장 검사 한 줄을 통째로 빼도
      시험이 그대로 통과한다(실측). 여기서는 다른 값을 전부 그대로 두고 도장
      글자 하나만 어긋나게 해, 도장 검사만이 판정을 뒤집게 만든다.
    """

    evidence = "annual filing homepage https://company.example"
    filing = seal_collected_source(
        Source(
            number=1,
            kind=SourceKind.FILING,
            label="Annual filing",
            disclosed_at="2026-03-18",
            source_id="src-filing-seal-only",
            title="Annual filing",
            publisher="Example Corp",
            host="dart.fss.or.kr",
            url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=202603180099",
            document_id="202603180099",
            location="Company overview",
            source_type="공식 공시",
            fact_status="실제",
            evidence_hashes=[evidence_text_hash(evidence)],
        )
    )
    website = seal_collected_source(
        Source(
            number=2,
            kind=SourceKind.OTHER,
            label="Official website",
            collected_at="2026-08-20",
            source_id="src-web-seal-only",
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
            domain_attestation_evidence=evidence,
        )
    )
    assert is_canonical_official_with_registry(website, [filing, website]) is True

    head = website.provenance_seal
    tampered = replace(
        website,
        provenance_seal=("b" if head[0] == "a" else "a") + head[1:],
    )

    # 도장 밖의 값은 한 글자도 건드리지 않았다 — 다른 검사는 전부 그대로 통과한다.
    assert tampered.is_canonical_official is True
    assert official_domain_attestation_problem(tampered, [filing, tampered]) == ""
    assert has_valid_provenance_seal(tampered) is False
    assert is_canonical_official_with_registry(tampered, [filing, tampered]) is False


def test_도장이_없거나_어긋난_저장본_출처는_한_줄_사유로_걸러진다() -> None:
    """읽는 경계가 쓰는 도장 점검 — 「전부 비었으면 통과, 하나라도 있으면 전부」."""

    plain = Source(
        number=1,
        kind=SourceKind.FILING,
        label="사업보고서",
        disclosed_at="2026-03-15",
        source_id="stored-1",
        title="사업보고서",
        publisher="가나다 주식회사",
        host="dart.fss.or.kr",
        url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=202603150001",
        document_id="202603150001",
        location="II. 사업의 내용",
        source_type="공식 공시",
        fact_status="실제",
    )
    other = replace(plain, number=2, source_id="stored-2")

    # 도장 칸이 처음부터 빈 옛 저장본은 문제로 세지 않는다.
    assert stored_sources_seal_problem([plain, other]) == ""

    sealed = seal_collected_source(plain)
    sealed_other = seal_collected_source(other)
    assert stored_sources_seal_problem([sealed, sealed_other]) == ""

    # 저장된 뒤 값이 바뀌면 사유가 나온다.
    assert "2번" in stored_sources_seal_problem(
        [sealed, replace(sealed_other, host="news.example")]
    )
    # 한 줄만 도장을 지워 검사를 피해 가는 길도 막는다.
    assert "2번" in stored_sources_seal_problem(
        [sealed, replace(sealed_other, provenance_seal="")]
    )


def test_사용장_투영은_수집봉인을_깨지_않지만_출처번호는_깨진다() -> None:
    """used_in과 number를 구분해 봉인 후 변경의 정본 규칙을 잠근다."""

    source = seal_collected_source(
        Source(
            number=1,
            kind=SourceKind.FILING,
            label="사업보고서",
            disclosed_at="2026-03-15",
            source_id="stored-usage-projection",
            title="사업보고서",
            publisher="가나다 주식회사",
            host="dart.fss.or.kr",
            url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=202603150001",
            document_id="202603150001",
            location="II. 사업의 내용",
            source_type="공식 공시",
            fact_status="실제",
            evidence_hashes=["a" * 64],
        )
    )

    assert has_valid_provenance_seal(
        replace(source, used_in=["identity", "business_model"])
    )
    assert not has_valid_provenance_seal(replace(source, number=2))


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
# 쓰기 — 기준 문서 예시와 문자 그대로 같아야 한다
# ══════════════════════════════════════════════════════════


def test_기준_문서_예시와_렌더링_결과가_한_글자도_다르지_않다():
    assert render_sources([공시_출처, 뉴스_출처]) == 기준_문서_예시_출처목록


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
# 읽기 — 기준 문서 예시를 그대로 되읽는다
# ══════════════════════════════════════════════════════════


def test_기준_문서_예시를_파싱하면_두_출처가_나온다():
    parsed = parse_sources(기준_문서_예시_출처목록)
    assert len(parsed) == 2
    assert parsed[0] == 공시_출처
    assert parsed[1] == 뉴스_출처


def test_번호는_새로_매기지_않고_그대로_읽는다():
    """AI가 고른 조각 번호(2·5처럼 건너뛴 번호)를 그대로 보존한다."""
    parsed = parse_sources(기준_문서_예시_출처목록)
    assert [s.number for s in parsed] == [2, 5]


def test_출처가_없는_블록은_빈_목록이다():
    assert parse_sources("[출처]") == []


def test_출처_블록이_아닌_글자는_무시한다():
    잡음_섞인_글 = "아무 상관 없는 문장\n\n" + 기준_문서_예시_출처목록 + "\n뒤에 붙은 잡음"
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


def _formal_verified_web_binding(url: str) -> str:
    receipt = "20260315000123"
    provenance = build_dart_filing_url_provenance(
        company_id="00126380",
        url=url,
        source_document_id=f"dart_business_report:{receipt}",
        source_receipt_no=receipt,
        source_member_name="covers/homepage.xml",
        source_location="raw_xml_chars:10-40",
        source_document_sha256="a" * 64,
        source_payload_sha256="b" * 64,
    )
    return build_verified_dart_filing_official_web_binding(
        provenance_value=provenance,
        company_id="00126380",
        company_name="가나다전자",
        company_registration_numbers=("123-45-67890",),
        candidate_url=url,
        effective_urls=(url,),
        scope_sha256="c" * 64,
        scope_allows=lambda candidate: candidate == url,
        identity_evidence_sha256="d" * 64,
        matched_name_sha256=hashlib.sha256("가나다전자".encode()).hexdigest(),
        registration_number_sha256=hashlib.sha256(b"1234567890").hexdigest(),
    )


def _formal_source_registry(source_kind: str) -> tuple[Source, ...]:
    receipt = "20260315000123"
    if source_kind not in OFFICIAL_WEB_SOURCE_KINDS:
        source = seal_collected_source(
            Source(
                number=1,
                kind=SourceKind.FILING,
                label="2025 사업보고서",
                disclosed_at="2026-03-15",
                collected_at="2026-09-04",
                source_id=f"formal-{source_kind}",
                title="2025 사업보고서",
                publisher="가나다전자",
                host="dart.fss.or.kr",
                url=(
                    "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=" + receipt
                ),
                document_id=receipt,
                location="II. 사업의 내용",
                source_type="공식 공시",
                fact_status="공시 실제값",
                evidence_hashes=[evidence_text_hash("공시 원문")],
                exact_evidence_hashes=[exact_evidence_text_hash("공시 원문")],
                formal_source_kind=source_kind,
                identity_binding="typed-dart-company-binding",
                document_content_sha256="d" * 64,
            )
        )
        return (source,)

    url = (
        "https://company.example/ir/2026-q2.pdf"
        if source_kind == SOURCE_KIND_OFFICIAL_IR_PDF
        else "https://company.example/about"
    )
    if source_kind == SOURCE_KIND_OFFICIAL_IDENTITY_VERIFIED_WEB_PAGE:
        source = seal_collected_source(
            Source(
                number=1,
                kind=SourceKind.OTHER,
                label="회사 공식 홈페이지",
                collected_at="2026-09-04",
                source_id="formal-identity-web",
                title="회사 공식 홈페이지",
                publisher="가나다전자",
                host="company.example",
                url=url,
                document_id="identity-root",
                location="본문",
                source_type="회사 공식 웹",
                fact_status="기준일 현재 확인",
                evidence_hashes=[evidence_text_hash("공식 홈페이지 원문")],
                exact_evidence_hashes=[exact_evidence_text_hash("공식 홈페이지 원문")],
                formal_source_kind=source_kind,
                identity_binding=_formal_verified_web_binding(url),
                document_content_sha256="d" * 64,
            )
        )
        return (source,)

    profile_evidence = json.dumps(
        {
            "corp_code": "00126380",
            "corp_name": "가나다전자",
            "hm_url": "https://company.example/",
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    attestation_result = bind_dart_profile_attestation(
        {},
        profile={
            "status": "000",
            "corp_code": "00126380",
            "corp_name": "가나다전자",
            "hm_url": "https://company.example/",
        },
        corp_code="00126380",
        company_name="가나다전자",
        collected_on="2026-09-04",
    )
    assert attestation_result.attester is not None
    attester = seal_collected_source(
        replace(
            attestation_result.attester,
            number=30,
            provenance_seal="",
        )
    )
    assert attester.domain_attestation_evidence == profile_evidence
    is_ir = source_kind == SOURCE_KIND_OFFICIAL_IR_PDF
    source = seal_collected_source(
        Source(
            number=1,
            kind=SourceKind.OTHER,
            label="회사 공식 자료",
            collected_at="2026-09-04",
            published_at="2026-08-20" if is_ir else "",
            source_id=f"formal-{source_kind}",
            title="회사 공식 자료",
            publisher="가나다전자",
            host="company.example",
            url=url,
            document_id=f"{source_kind}:document",
            location="본문",
            source_type="회사 공식 IR" if is_ir else "회사 공식 웹",
            fact_status="공식 발행일·보고기간 확정" if is_ir else "기준일 현재 확인",
            evidence_hashes=[evidence_text_hash("회사 공식 원문")],
            exact_evidence_hashes=[exact_evidence_text_hash("회사 공식 원문")],
            domain_attestation_source_id=attester.source_id,
            domain_attestation_evidence=profile_evidence,
            reporting_period="2026-Q2" if is_ir else "",
            ir_metadata_verification=(
                IR_METADATA_VERIFICATION_VALUE if is_ir else ""
            ),
            attachment_url=url if is_ir else "",
            formal_source_kind=source_kind,
            identity_binding="DART 기업개황 홈페이지 주소(root)",
            document_content_sha256="d" * 64,
        )
    )
    return (source, attester)


@pytest.mark.parametrize("source_kind", sorted(FORMAL_DOCUMENT_SOURCE_KINDS))
def test_formal_전종류_Source가_같은_등록부_계약과_seal을_통과한다(
    source_kind: str,
) -> None:
    registry = _formal_source_registry(source_kind)

    assert full_typed_source_registry_problem(
        registry[0], registry, reference_date="2026-09-04"
    ) == ""


@pytest.mark.parametrize("source_kind", sorted(FORMAL_DOCUMENT_SOURCE_KINDS))
def test_formal_전종류의_전체문서지문은_삭제하거나_바꾸면_등록부에서_막힌다(
    source_kind: str,
) -> None:
    registry = _formal_source_registry(source_kind)
    source = registry[0]

    changed = replace(source, document_content_sha256="e" * 64)
    assert not has_valid_provenance_seal(changed)
    assert full_typed_source_registry_problem(
        changed,
        (changed, *registry[1:]),
        reference_date="2026-09-04",
    )

    removed = seal_collected_source(
        replace(source, document_content_sha256="", provenance_seal="")
    )
    assert has_valid_provenance_seal(removed)
    assert not removed.is_canonical_valid
    assert full_typed_source_registry_problem(
        removed,
        (removed, *registry[1:]),
        reference_date="2026-09-04",
    )

    padded = seal_collected_source(
        replace(
            source,
            document_content_sha256=f" {'d' * 64} ",
            provenance_seal="",
        )
    )
    assert not padded.is_canonical_valid
    assert full_typed_source_registry_problem(
        padded,
        (padded, *registry[1:]),
        reference_date="2026-09-04",
    )


@pytest.mark.parametrize("source_kind", sorted(FORMAL_DOCUMENT_SOURCE_KINDS))
def test_formal_전종류는_의미필드를_바꿔_재봉인해도_등록부_검증에서_막힌다(
    source_kind: str,
) -> None:
    registry = _formal_source_registry(source_kind)
    source = registry[0]
    if source_kind not in OFFICIAL_WEB_SOURCE_KINDS:
        tampered = replace(source, source_type="회사 공식 웹")
    elif source_kind == SOURCE_KIND_OFFICIAL_IDENTITY_VERIFIED_WEB_PAGE:
        tampered = replace(source, publisher="다른회사")
    elif source_kind == SOURCE_KIND_OFFICIAL_IR_PDF:
        tampered = replace(source, ir_metadata_verification="forged")
    else:
        tampered = replace(source, domain_attestation_evidence="변조된 근거")
    tampered = seal_collected_source(replace(tampered, provenance_seal=""))
    tampered_registry = (tampered, *registry[1:])

    assert full_typed_source_registry_problem(
        tampered, tampered_registry, reference_date="2026-09-04"
    )
