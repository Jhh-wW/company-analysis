from __future__ import annotations

import json
from dataclasses import replace
from datetime import date
from typing import Any

import pytest

from src.features.company_performance.logic import build_three_year_table
from src.features.pipeline import canonical_report
from src.features.pipeline.canonical_report import (
    PublishBlockedError,
    WrittenClaim,
    assemble_report_draft,
    finalize_report,
    sections_from_picks,
)
from src.features.pipeline.port import Grade, Report, ReportTable
from src.features.provenance.citations import build_citations
from src.features.provenance.sources import (
    Source,
    evidence_text_hash,
    seal_collected_source,
)
from src.features.report_standard.publish import validate_publishable
from src.features.spanselect.canonical import CanonicalPick


JYP_COMPANY = "(주)제이와이피엔터테인먼트"
JYP_IDENTITY_EVIDENCE = (
    "사업의 개요 당사는 종합 엔터테인먼트 기업으로서 아티스트 발굴 및 육성, "
    "음반/음원/컨텐츠 기획 및 제작, 공연/출연 등 소속아티스트의 매니지먼트 "
    "관련 사업을 영위하고 있습니다.당사의 사업부문은 음반사업과 "
    "매니지먼트사업으로 분류하고 있습니다."
)
JYP_REVENUE_EVIDENCE = (
    "이러한 지속가능한 시스템을 기반으로 음반, 음원, 영상 컨텐츠 등을 기획, "
    "제작하여 유통하는 음악 및 영상 컨텐츠 사업, 그리고 공연, 출연 등 소속 "
    "아티스트의 용역활동을 통해 수익을 창출하는 매니지먼트 관련 사업을 글로벌 "
    "전역에서 영위하고 있습니다."
)


def _financial_row(
    account_id: str,
    account_name: str,
    current: str,
    previous: str,
    before_previous: str,
) -> dict[str, str]:
    return {
        "fs_div": "CFS",
        "sj_div": "IS",
        "account_id": account_id,
        "account_nm": account_name,
        "bsns_year": "2025",
        "reprt_code": "11011",
        "currency": "KRW",
        "thstrm_dt": "2025.01.01 ~ 2025.12.31",
        "thstrm_amount": current,
        "frmtrm_dt": "2024.01.01 ~ 2024.12.31",
        "frmtrm_amount": previous,
        "bfefrmtrm_dt": "2023.01.01 ~ 2023.12.31",
        "bfefrmtrm_amount": before_previous,
    }


def _jyp_financial_payload() -> dict[str, object]:
    """JYP 2025 사업보고서의 DART 연결 손익 원값만 고정한다."""

    return {
        "status": "000",
        "list": [
            _financial_row(
                "ifrs-full_Revenue",
                "매출액",
                "821854634614",
                "601787883332",
                "566500542871",
            ),
            _financial_row(
                "dart_OperatingIncomeLoss",
                "영업이익",
                "155246094076",
                "128262269240",
                "169443909949",
            ),
            _financial_row(
                "ifrs-full_ProfitLoss",
                "당기순이익",
                "160563476080",
                "97714913917",
                "105016740741",
            ),
        ],
    }


def _jyp_inputs() -> tuple[
    list[CanonicalPick],
    dict[int, dict[str, Any]],
    list[Source],
    ReportTable,
]:
    performance_table = build_three_year_table(
        _jyp_financial_payload(), cite="조각 3·재무"
    )
    assert performance_table is not None
    fragments: dict[int, dict[str, Any]] = {
        1: {"종류": "사업내용", "원문": JYP_IDENTITY_EVIDENCE},
        2: {"종류": "사업내용", "원문": JYP_REVENUE_EVIDENCE},
        3: {
            "종류": "재무",
            "원문": "주요계정(DART API): JYP Ent. 연결 손익계산서",
            "문서ID": "fnlttSinglAcnt.json",
            "근거원문": list(performance_table.evidence_rows),
        },
    }
    sources = build_citations(
        fragments,
        filing={
            "corp_name": JYP_COMPANY,
            "report_nm": "사업보고서 (2025.12)",
            "rcept_dt": "20260318",
            "rcept_no": "20260318001519",
        },
        collected_on=date(2026, 8, 23),
        company_publisher=JYP_COMPANY,
        selected_evidence_by_fragment={
            1: [JYP_IDENTITY_EVIDENCE],
            2: [JYP_REVENUE_EVIDENCE],
        },
    )
    picks = [
        CanonicalPick(
            "identity",
            JYP_IDENTITY_EVIDENCE,
            1,
            sid="1-1",
            claim_type="identity_summary",
        ),
        CanonicalPick(
            "business_model",
            JYP_REVENUE_EVIDENCE,
            2,
            sid="2-1",
            claim_type="revenue_model",
        ),
    ]
    return picks, fragments, sources, performance_table


def _build_jyp_report(
    *,
    picks: list[CanonicalPick] | None = None,
    fragments: dict[int, dict[str, Any]] | None = None,
    sources: list[Source] | None = None,
    performance_table: ReportTable | None = None,
) -> Report:
    default_picks, default_fragments, default_sources, default_table = _jyp_inputs()
    picks = default_picks if picks is None else picks
    fragments = default_fragments if fragments is None else fragments
    sources = default_sources if sources is None else sources
    performance_table = default_table if performance_table is None else performance_table
    sections = sections_from_picks(
        picks,
        fragments,
        tables_by_section={"past_changes": [performance_table]},
    )
    written_claims = [
        WrittenClaim(
            section_id=pick.section_id,
            text=pick.sentence,
            cite=f"조각 {pick.fragment_id}·{fragments[pick.fragment_id]['종류']}",
            evidence=pick.sentence,
            fragment_id=pick.fragment_id,
            sid=pick.sid,
            claim_type=pick.claim_type,
        )
        for pick in picks
    ]
    steps: list[dict[str, Any]] = []
    draft = assemble_report_draft(
        company=JYP_COMPANY,
        corp_type="상장사",
        sections=sections,
        written_claims=written_claims,
        sources=sources,
        steps=steps,
        as_of_date="2026-08-23",
        analysis_period="2023~2025 완료 회계연도",
        latest_performance_period="2025년 공식 공시",
    )
    return finalize_report(
        draft,
        summary_ask=lambda *_args: ({"items": []}, {}),
        steps=steps,
    )


def test_JYP_실제원문과_3개년_DART값은_기존출고검증을_거쳐_보고서가_된다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_builder = canonical_report.build_published_report
    publish_calls = 0

    def tracked_builder(report: Report) -> Report:
        nonlocal publish_calls
        publish_calls += 1
        return original_builder(report)

    monkeypatch.setattr(canonical_report, "build_published_report", tracked_builder)

    report = _build_jyp_report()

    assert publish_calls == 1
    assert report.grade is Grade.PARTIAL
    assert validate_publishable(report).publishable is True
    sections = {section.cell: section for section in report.sections}
    assert {"identity", "business_model", "past_changes"}.issubset(sections)
    assert any(
        text == JYP_IDENTITY_EVIDENCE
        for text, _cite in sections["identity"].prose_lines
    )
    assert any(
        text == JYP_REVENUE_EVIDENCE
        for text, _cite in sections["business_model"].prose_lines
    )
    assert len(sections["past_changes"].tables) == 1
    table = sections["past_changes"].tables[0]
    assert table.headers == ["사업연도", "매출액", "영업이익", "당기순이익"]
    assert [row[0] for row in table.rows] == ["2025", "2024", "2023"]
    assert table.raw_rows[0] == [
        "2025",
        "821,854,634,614",
        "155,246,094,076",
        "160,563,476,080",
    ]
    assert {
        fact.claim_type
        for fact in report.fact_records
    }.issuperset({"identity_summary", "revenue_model", "historical_performance"})
    assert sorted(
        fact.fiscal_year
        for fact in report.fact_records
        if fact.claim_type == "historical_performance"
    ) == [2023, 2024, 2025]


def test_JYP_실제출처에_없는_허위문장_하나는_최소보고서도_출고하지_못한다() -> None:
    picks, fragments, sources, performance_table = _jyp_inputs()
    picks[1] = CanonicalPick(
        "business_model",
        "JYP는 2025년 세계 음반 시장의 구십구 퍼센트를 독점했다.",
        2,
        sid="2-1",
        claim_type="revenue_model",
    )

    with pytest.raises(PublishBlockedError):
        _build_jyp_report(
            picks=picks,
            fragments=fragments,
            sources=sources,
            performance_table=performance_table,
        )


def test_JYP_2023_DART원값이_빠지면_출처해시가_맞아도_출고하지_못한다() -> None:
    picks, fragments, sources, performance_table = _jyp_inputs()
    original_payload = performance_table.evidence_rows[0]
    tampered_payload = json.loads(original_payload)
    revenue_row = next(
        row
        for row in tampered_payload["list"]
        if row["account_id"] == "ifrs-full_Revenue"
    )
    del revenue_row["bfefrmtrm_amount"]
    tampered_evidence = json.dumps(
        tampered_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    tampered_table = replace(
        performance_table,
        evidence_rows=[original_payload, original_payload, tampered_evidence],
    )
    tampered_sources = [
        seal_collected_source(
            replace(
                source,
                evidence_hashes=sorted(
                    {*source.evidence_hashes, evidence_text_hash(tampered_evidence)}
                ),
                provenance_seal="",
            )
        )
        if source.number == 3
        else source
        for source in sources
    ]

    with pytest.raises(PublishBlockedError):
        _build_jyp_report(
            picks=picks,
            fragments=fragments,
            sources=tampered_sources,
            performance_table=tampered_table,
        )
