from __future__ import annotations

from dataclasses import replace

import pytest

from src.features.pipeline.canonical_report import (
    PublishBlockedError,
    WrittenClaim,
    assemble_report,
    majority_picks,
    sections_from_picks,
)
from src.features.provenance.sources import Source, SourceKind, evidence_text_hash
from src.features.spanselect.canonical import CanonicalPick


def _source(number: int, evidence: str = "공식 원문") -> Source:
    document_id = f"202608130000{number:02d}"
    return Source(
        number=number,
        kind=SourceKind.FILING,
        label=f"공식 자료 {number}",
        disclosed_at="2026-08-13",
        source_id=f"source-{number}",
        title=f"공식 자료 {number}",
        publisher="가나다 주식회사",
        host="dart.fss.or.kr",
        url=f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={document_id}",
        document_id=document_id,
        location=f"본문 {number}",
        source_type="공식 공시",
        fact_status="실제",
        evidence_hashes=[evidence_text_hash(evidence)],
    )


def test_majority_vote_drops_cross_section_tie() -> None:
    same = CanonicalPick("identity", "가나다는 소재 기업이다.", 1)
    rounds = [
        [same, CanonicalPick("portfolio", "제품 A를 판매한다.", 2)],
        [same, CanonicalPick("business_model", "제품 A를 판매한다.", 2)],
        [CanonicalPick("identity", "다른 문장", 3)],
    ]

    assert majority_picks(rounds) == [same]


def test_assemble_report_locks_visible_claims_to_sources() -> None:
    fragments = {
        1: {"종류": "사업내용", "원문": "가나다는 산업용 소재 기업이다."},
        2: {"종류": "사업내용", "원문": "가나다는 고객에게 소재를 판매한다."},
        3: {"종류": "사업내용", "원문": "가나다는 제품 A를 판매한다."},
        4: {"종류": "MD&A", "원문": "2025년 설비 A 도입을 완료했다."},
    }
    picks = [
        CanonicalPick("identity", fragments[1]["원문"], 1),
        CanonicalPick("business_model", fragments[2]["원문"], 2),
        CanonicalPick("portfolio", fragments[3]["원문"], 3),
        CanonicalPick("past_changes", fragments[4]["원문"], 4),
    ]
    sections = sections_from_picks(picks, fragments)
    claims = [
        WrittenClaim(item.section_id, item.sentence, f"조각 {item.fragment_id}·{fragments[item.fragment_id]['종류']}", item.sentence, item.fragment_id)
        for item in picks
    ]
    calls = 0

    def ask(_prompt, _schema, _max_tokens):
        nonlocal calls
        calls += 1
        if calls == 1:
            return {
                "items": [
                    {"section_id": "identity", "text": "회사의 산업 내 역할이 분명하다"},
                    {"section_id": "business_model", "text": "고객과 수익 경로가 연결된다"},
                    {"section_id": "portfolio", "text": "현재 판매 제품의 역할이 드러난다"},
                ]
            }, {}
        return {"판정": [{"번호": 1, "근거에있다": True}, {"번호": 2, "근거에있다": True}, {"번호": 3, "근거에있다": True}]}, {}

    report = assemble_report(
        company="가나다",
        corp_type="상장사",
        sections=sections,
        written_claims=claims,
        sources=[_source(i, fragments[i]["원문"]) for i in range(1, 5)],
        summary_ask=ask,
        steps=[],
        as_of_date="2026-08-19",
        analysis_period="2023~2025 완료 회계연도",
        latest_performance_period="2025년 공식 공시",
        publish=False,
    )

    assert report.schema_version == "company-report-v3-canonical"
    assert [section.cell for section in report.sections] == [
        "identity",
        "business_model",
        "portfolio",
        "past_changes",
    ]
    assert len(report.fact_records) == 4
    assert all(fact.status == "verified" for fact in report.fact_records)
    assert all(fact.verification_status == "verified" for fact in report.fact_records)
    assert all(fact.evidence_binding for fact in report.fact_records)
    assert all(source.evidence_hashes for source in report.citations)
    assert report.job == ""
    assert report.requirements == []


def test_assembly_never_self_registers_written_claim_as_source_evidence() -> None:
    evidence = "가나다는 산업용 소재 기업이다."
    pick = CanonicalPick(
        "identity", evidence, 1, sid="identity-1", claim_type="official_identity"
    )
    sections = sections_from_picks(
        [pick], {1: {"종류": "사업내용", "원문": evidence}}
    )
    claim = WrittenClaim(
        "identity",
        evidence,
        "조각 1·사업내용",
        evidence,
        1,
        sid="identity-1",
        claim_type="official_identity",
    )
    source = _source(1, "서로 다른 실제 원문")

    report = assemble_report(
        company="가나다",
        corp_type="상장사",
        sections=sections,
        written_claims=[claim],
        sources=[source],
        summary_ask=lambda *_args: ({"items": []}, {}),
        steps=[],
        as_of_date="2026-08-19",
        analysis_period="2023~2025 완료 회계연도",
        latest_performance_period="2025년 공식 공시",
        publish=False,
    )

    assert report.fact_records == []
    assert report.citations == []
    assert evidence_text_hash(evidence) not in source.evidence_hashes


def test_assemble_report_rejects_duplicate_source_numbers_before_mapping() -> None:
    duplicate = replace(_source(2), number=1)

    with pytest.raises(PublishBlockedError, match="출처 번호"):
        assemble_report(
            company="가나다",
            corp_type="상장사",
            sections=[],
            written_claims=[],
            sources=[_source(1), duplicate],
            summary_ask=lambda *_args: ({"items": []}, {}),
            steps=[],
            as_of_date="2026-08-19",
            analysis_period="2023~2025 완료 회계연도",
            latest_performance_period="2025년 공식 공시",
        )


def test_complete_news_metadata_is_not_promoted_to_a_draft_fact() -> None:
    evidence = "가나다는 소재 기업이라고 외부 매체가 분석했다."
    fragments = {1: {"종류": "뉴스", "원문": evidence}}
    pick = CanonicalPick(
        "identity",
        evidence,
        1,
        sid="1-1",
        claim_type="official_identity",
    )
    sections = sections_from_picks([pick], fragments)
    claim = WrittenClaim(
        "identity",
        evidence,
        "조각 1·뉴스",
        evidence,
        1,
        sid="1-1",
        claim_type="official_identity",
    )
    news = Source(
        number=1,
        kind=SourceKind.NEWS,
        label="회사 전략 분석",
        published_at="2026-08-13",
        domain="news.example",
        source_id="news-1",
        title="회사 전략 분석",
        publisher="OO경제",
        host="news.example",
        url="https://news.example/1",
        document_id="article-1",
        location="기사 본문",
        source_type="공식 분석 기사",
        fact_status="실제",
        evidence_hashes=[evidence_text_hash(evidence)],
    )
    assert news.is_canonical_valid is True
    assert news.is_canonical_official is False

    report = assemble_report(
        company="가나다",
        corp_type="상장사",
        sections=sections,
        written_claims=[claim],
        sources=[news],
        summary_ask=lambda *_args: ({"items": []}, {}),
        steps=[],
        as_of_date="2026-08-19",
        analysis_period="2023~2025 완료 회계연도",
        latest_performance_period="2025년 공식 공시",
        publish=False,
    )

    assert report.sections == []
    assert report.fact_records == []
    assert report.citations == []
