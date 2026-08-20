"""출처 목록 시험 — 왕복(쓰기→읽기) 일치가 핵심이다.

정본: 확정/07_출력/2_규칙/01_배치와근거표기.md
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from src.features.provenance.sources import (
    Source,
    SourceKind,
    count_missing_dates,
    evidence_text_hash,
    has_valid_provenance_seal,
    is_canonical_official_with_registry,
    official_domain_attestation_problem,
    parse_sources,
    render_sources,
    seal_collected_source,
)

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
