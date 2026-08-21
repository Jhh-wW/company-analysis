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
from src.features.pipeline.port import ReportSection, ReportTable
from src.features.provenance.sources import Source, SourceKind, evidence_text_hash
from src.features.spanselect.canonical import (
    CanonicalPick,
    historical_performance_basis_sid,
)


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


def test_majority_vote_agrees_each_structured_field_without_exact_object_match() -> None:
    base = CanonicalPick(
        "future_strategy",
        "가나다는 2027년 AlphaX 생산 설비 가동을 계획했다.",
        1,
        sid="1-1",
        claim_type="future_plan",
        subject_label="AlphaX 생산 설비",
        plan_status="announced",
        plan_timing="2027년",
        plan_execution_signal="생산 설비 가동",
    )
    rounds = [
        [base],
        [replace(base, plan_timing="2027년 중")],
        [replace(base, plan_execution_signal="AlphaX 생산 설비 가동")],
    ]

    assert majority_picks(rounds) == [base]


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
        "identity", evidence, 1, sid="identity-1", claim_type="identity_summary"
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
        claim_type="identity_summary",
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


def test_customer_market_metadata_stays_bound_from_pick_to_fact() -> None:
    evidence = "가나다는 중국에서 제품을 판매해 매출을 얻는다."
    pick = CanonicalPick(
        "business_model",
        evidence,
        1,
        sid="business-1",
        claim_type="customer_market",
        subject_label="중국",
        market_stage="",
        market_observation="중국에서 제품을 판매",
    )
    sections = sections_from_picks(
        [pick], {1: {"종류": "사업내용", "원문": evidence}}
    )
    claim = WrittenClaim(
        "business_model",
        evidence,
        "조각 1·사업내용",
        evidence,
        1,
        sid=pick.sid,
        claim_type=pick.claim_type,
        subject_label=pick.subject_label,
        market_stage=pick.market_stage,
        market_observation=pick.market_observation,
    )

    report = assemble_report(
        company="가나다",
        corp_type="상장사",
        sections=sections,
        written_claims=[claim],
        sources=[_source(1, evidence)],
        summary_ask=lambda *_args: ({"items": []}, {}),
        steps=[],
        as_of_date="2026-08-19",
        analysis_period="2023~2025 완료 회계연도",
        latest_performance_period="2025년 공식 공시",
        publish=False,
    )

    assert len(report.fact_records) == 1
    fact = report.fact_records[0]
    assert fact.claim_type == "customer_market"
    assert fact.market_stage == ""
    assert fact.market_observation == "중국에서 제품을 판매"
    assert fact.market_priority == ""


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
        claim_type="identity_summary",
    )
    sections = sections_from_picks([pick], fragments)
    claim = WrittenClaim(
        "identity",
        evidence,
        "조각 1·뉴스",
        evidence,
        1,
        sid="1-1",
        claim_type="identity_summary",
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


def test_assembly_resolves_public_performance_reference_to_verified_table_fact() -> None:
    interpretation_evidence = (
        "가나다는 2025년 SmartX 매출이 2024년보다 증가했다고 밝혔다."
    )
    financial_payload = '{"status":"000","source":"DART"}'
    performance_table = ReportTable(
        caption="전자공시 최근 세 사업연도 연결 주요 실적 (단위: 억원)",
        headers=["사업연도", "매출액"],
        rows=[["2025", "120"], ["2024", "100"], ["2023", "90"]],
        cite="조각 2·재무",
        numeric=True,
        raw_rows=[
            ["2025", "12000000000"],
            ["2024", "10000000000"],
            ["2023", "9000000000"],
        ],
        scale_divisor="100000000",
        scale_places=0,
        display_unit="억원",
        evidence_rows=[financial_payload, financial_payload, financial_payload],
    )
    reference = historical_performance_basis_sid(2025)
    section = ReportSection(
        cell="past_changes",
        title="3개년 주요 변화와 실행",
        tables=[performance_table],
    )
    interpretation = WrittenClaim(
        section_id="past_changes",
        text=interpretation_evidence,
        cite="조각 1·MD&A",
        evidence=interpretation_evidence,
        fragment_id=1,
        sid="1-1",
        claim_type="change_interpretation",
        basis_sids=(reference,),
    )

    report = assemble_report(
        company="가나다",
        corp_type="상장사",
        sections=[section],
        written_claims=[interpretation],
        sources=[_source(1, interpretation_evidence), _source(2, financial_payload)],
        summary_ask=lambda *_args: ({"items": []}, {}),
        steps=[],
        as_of_date="2026-08-19",
        analysis_period="2023~2025 완료 회계연도",
        latest_performance_period="2025년 공식 공시",
        publish=False,
    )

    interpretation_fact = next(
        fact
        for fact in report.fact_records
        if fact.claim_type == "change_interpretation"
    )
    performance_fact = next(
        fact
        for fact in report.fact_records
        if fact.claim_type == "historical_performance" and fact.fiscal_year == 2025
    )
    assert interpretation_fact.basis_fact_ids == [performance_fact.fact_id]
    assert reference not in interpretation_fact.basis_fact_ids


def test_assembly_drops_entire_basis_link_when_internal_or_unknown_reference_is_mixed() -> None:
    evidence = "가나다는 2025년 SmartX 매출이 증가했다고 밝혔다."
    reference = historical_performance_basis_sid(2025)
    table = ReportTable(
        caption="전자공시 최근 세 사업연도 연결 주요 실적 (단위: 억원)",
        headers=["사업연도", "매출액"],
        rows=[["2025", "120"]],
        cite="조각 2·재무",
        numeric=True,
        raw_rows=[["2025", "12000000000"]],
        scale_divisor="100000000",
        scale_places=0,
        display_unit="억원",
        evidence_rows=["DART 원 payload"],
    )
    claim = WrittenClaim(
        "past_changes",
        evidence,
        "조각 1·MD&A",
        evidence,
        1,
        sid="1-1",
        claim_type="change_interpretation",
        basis_sids=(reference, "fact-internal-value"),
    )
    report = assemble_report(
        company="가나다",
        corp_type="상장사",
        sections=[ReportSection("past_changes", "과거", tables=[table])],
        written_claims=[claim],
        sources=[_source(1, evidence), _source(2, "DART 원 payload")],
        summary_ask=lambda *_args: ({"items": []}, {}),
        steps=[],
        as_of_date="2026-08-19",
        analysis_period="2023~2025 완료 회계연도",
        latest_performance_period="2025년 공식 공시",
        publish=False,
    )

    interpretation_fact = next(
        fact
        for fact in report.fact_records
        if fact.claim_type == "change_interpretation"
    )
    assert interpretation_fact.basis_fact_ids == []
