from __future__ import annotations

from dataclasses import replace

import pytest

from src.features.business_candidate.dart_identity import DartCompanyRecord
from src.features.company_comparison.logic import (
    ComparisonBlockedError,
    OfficialCompanyBundle,
    build_competitive_position,
    discover_candidates,
)
from src.features.pipeline.port import FactRecord, Grade, Report, ReportSection
from src.features.provenance.sources import Source, SourceKind, evidence_text_hash
from src.features.report_standard.constants import CANONICAL_SCHEMA_VERSION
from src.features.report_standard.publish import _fact_problems


def _official_source() -> Source:
    evidence = (
        "알파는 베타와 전자 제조 고객 대상 반도체 검사장비 제품을 "
        "반도체 검사장비 시장에서 두고 경쟁 관계에 있다."
    )
    return Source(
        number=1,
        kind=SourceKind.FILING,
        label="알파 2025 사업보고서",
        disclosed_at="2026-03-15",
        collected_at="2026-08-19",
        source_id="source-alpha-official",
        title="알파 2025 사업보고서",
        publisher="주식회사 알파",
        host="dart.fss.or.kr",
        url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260315000001",
        document_id="20260315000001",
        location="사업의 내용 · 경쟁 현황",
        source_type="공식 공시",
        fact_status="공시 실제값",
        used_in=["identity"],
        evidence_hashes=[evidence_text_hash(evidence)],
    )


def _report(evidence: str = (
    "알파는 베타와 전자 제조 고객 대상 반도체 검사장비 제품을 "
    "반도체 검사장비 시장에서 두고 경쟁 관계에 있다."
)) -> Report:
    source = _official_source()
    fact = FactRecord(
        fact_id="fact-alpha-market",
        legal_entity="주식회사 알파",
        subject_scope="반도체 검사장비 시장",
        relationship_or_action="시장 참여",
        claim=evidence,
        claim_type="identity",
        section_owner="identity",
        time_state="standing",
        as_of="2025-12-31",
        source_id=source.source_id,
        source_type=source.source_type,
        source_title=source.title,
        source_publisher=source.publisher,
        source_host=source.host,
        source_url=source.url,
        source_document_id=source.document_id,
        location=source.location,
        status="verified",
        state_evidence=evidence,
        supports_causality=True,
    )
    return Report(
        company="주식회사 알파",
        job="",
        corp_type="상장사",
        grade=Grade.COMPLETE,
        sections=[
            ReportSection(
                cell="identity",
                title="기업 정체성",
                display_number="1",
                prose_lines=[(evidence, "1")],
                fact_ids=[fact.fact_id],
            )
        ],
        citations=[source],
        fact_records=[fact],
        schema_version=CANONICAL_SCHEMA_VERSION,
    )


def _row(
    account_id: str,
    account_nm: str,
    amount: str,
    *,
    period: str = "2025.01.01 ~ 2025.12.31",
    scope: str = "CFS",
) -> dict[str, str]:
    return {
        "account_id": account_id,
        "account_nm": account_nm,
        "sj_div": "IS",
        "fs_div": scope,
        "thstrm_dt": period,
        "thstrm_amount": amount,
        "bsns_year": period[:4],
        "reprt_code": "11011",
        "currency": "KRW",
    }


def _financials(
    *,
    period: str = "2025.01.01 ~ 2025.12.31",
    scale: int = 1,
    scope: str = "CFS",
    operating_amount: int | None = None,
):
    operating_value = 100 * scale if operating_amount is None else operating_amount
    return {
        "status": "000",
        "reprt_code": "11011",
        "list": [
            _row("ifrs-full_Revenue", "매출액", str(1000 * scale), period=period, scope=scope),
            _row("dart_OperatingIncomeLoss", "영업이익", str(operating_value), period=period, scope=scope),
            _row("ifrs-full_ProfitLoss", "당기순이익", str(80 * scale), period=period, scope=scope),
        ]
    }


def _bundle(
    corp_code: str,
    name: str,
    *,
    period: str = "2025.01.01 ~ 2025.12.31",
    scale: int = 1,
    scope: str = "CFS",
    operating_amount: int | None = None,
    official_text: str = (
        "전자 제조 고객 대상 반도체 검사장비 제품을 반도체 검사장비 시장에 공급한다. "
        "연결재무제표의 매출액과 영업이익을 공시한다."
    ),
) -> OfficialCompanyBundle:
    year = period[:4]
    return OfficialCompanyBundle(
        corp_code=corp_code,
        company_name=name,
        financials=_financials(
            period=period,
            scale=scale,
            scope=scope,
            operating_amount=operating_amount,
        ),
        filing={
            "report_nm": f"사업보고서 ({year}.12)",
            "rcept_no": f"{year}0315000001",
            "rcept_dt": f"{int(year) + 1}0315",
            "reprt_code": "11011",
        },
        official_text=official_text,
    )


CATALOG = (
    DartCompanyRecord("00000001", "주식회사 알파", stock_code="000001"),
    DartCompanyRecord("00000002", "주식회사 베타", stock_code="000002"),
    DartCompanyRecord("00000003", "주식회사 감마", stock_code="000003"),
)


def test_정상_양사공식원문은_동일조건_비교사실을_만든다() -> None:
    report = _report()
    self_bundle = _bundle("00000001", "주식회사 알파", scale=2)
    comparator = _bundle(
        "00000002",
        "주식회사 베타",
        scale=1,
        operating_amount=50,
    )

    result = build_competitive_position(
        report,
        self_bundle=self_bundle,
        catalog=CATALOG,
        fetch_comparator=lambda record: comparator if record.corp_code == "00000002" else None,
        collected_on="2026-08-19",
    )

    assert result.section.cell == "competitive_position"
    assert len(result.facts) == 2
    assert len(result.sources) == 2
    assert {fact.comparison_metric for fact in result.facts} == {
        "영업이익률",
        "매출 규모",
    }
    assert {source.publisher for source in result.sources} == {
        "주식회사 알파",
        "주식회사 베타",
    }
    for fact in result.facts:
        assert fact.comparison_target == "주식회사 베타"
        assert "ifrs-full_Revenue" in fact.comparison_definition
        assert set(fact.comparison_conditions) == {
            "customer", "product", "market", "self_period", "comparator_period",
            "self_definition", "comparator_definition", "self_accounting_scope",
            "comparator_accounting_scope",
        }
        assert fact.comparison_period == "2025-01-01~2025-12-31"
        assert fact.comparison_scope == "연결재무제표(CFS)"
        assert fact.comparator_source_id != fact.source_id
        assert "경쟁우위" in fact.limitations
        if fact.comparison_metric == "영업이익률":
            assert fact.display_value == "자사 10.0%; 비교사 5.0%; 차이 5.0%p"
        else:
            assert fact.display_value == "2.0배"
            assert "경쟁우위 판정이 아니다" in fact.claim
        fact_errors = _fact_problems(
            fact,
            {source.source_id: source for source in result.sources},
        )
        assert fact_errors == [], fact_errors


def test_규모만_같은_조건이어도_수익성_차이가_없으면_9장을_차단한다() -> None:
    with pytest.raises(ComparisonBlockedError, match="영업이익률 차이"):
        build_competitive_position(
            _report(),
            self_bundle=_bundle("00000001", "주식회사 알파", scale=2),
            catalog=CATALOG,
            fetch_comparator=lambda _record: _bundle(
                "00000002",
                "주식회사 베타",
                scale=1,
            ),
            collected_on="2026-08-19",
        )


def test_경쟁관계_근거가_없으면_후보를_임의로_고르지_않는다() -> None:
    report = _report("알파는 베타에 반도체 검사장비를 납품한다.")

    assert discover_candidates(report, CATALOG, self_corp_code="00000001") == ()
    with pytest.raises(ComparisonBlockedError, match="경쟁 관계"):
        build_competitive_position(
            report,
            self_bundle=_bundle("00000001", "주식회사 알파"),
            catalog=CATALOG,
            fetch_comparator=lambda _record: _bundle("00000002", "주식회사 베타"),
            collected_on="2026-08-19",
        )


def test_뉴스나_외부분석의_경쟁사_언급은_후보근거로_쓰지_않는다() -> None:
    report = _report()
    external = replace(
        _official_source(),
        kind=SourceKind.NEWS,
        source_type="외부 분석",
        publisher="가상리서치",
        published_at="2026-08-18",
        domain="research.example.com",
    )
    report = replace(report, citations=[external])

    assert discover_candidates(report, CATALOG, self_corp_code="00000001") == ()


@pytest.mark.parametrize(
    "fetcher",
    [
        lambda _record: None,
        lambda _record: _bundle("00000002", "주식회사 베타", official_text=""),
    ],
)
def test_한쪽_공식근거가_없으면_9장을_차단한다(fetcher) -> None:
    with pytest.raises(ComparisonBlockedError, match="비교사 공식 원문"):
        build_competitive_position(
            _report(),
            self_bundle=_bundle("00000001", "주식회사 알파"),
            catalog=CATALOG,
            fetch_comparator=fetcher,
            collected_on="2026-08-19",
        )


def test_기간이_다르면_같은_계정이어도_비교하지_않는다() -> None:
    with pytest.raises(ComparisonBlockedError, match="지표 정의·기간·연결범위"):
        build_competitive_position(
            _report(),
            self_bundle=_bundle("00000001", "주식회사 알파"),
            catalog=CATALOG,
            fetch_comparator=lambda _record: _bundle(
                "00000002",
                "주식회사 베타",
                period="2024.01.01 ~ 2024.12.31",
            ),
            collected_on="2026-08-19",
        )


def test_연결과_별도_범위가_다르면_비교하지_않는다() -> None:
    with pytest.raises(ComparisonBlockedError, match="지표 정의·기간·연결범위"):
        build_competitive_position(
            _report(),
            self_bundle=_bundle("00000001", "주식회사 알파", scope="CFS"),
            catalog=CATALOG,
            fetch_comparator=lambda _record: _bundle(
                "00000002",
                "주식회사 베타",
                scope="OFS",
            ),
            collected_on="2026-08-19",
        )


def test_자사_공식원문이_없어도_한쪽자료로_비교하지_않는다() -> None:
    with pytest.raises(ComparisonBlockedError, match="자사 공식 원문"):
        build_competitive_position(
            _report(),
            self_bundle=replace(_bundle("00000001", "주식회사 알파"), official_text=""),
            catalog=CATALOG,
            fetch_comparator=lambda _record: _bundle("00000002", "주식회사 베타"),
            collected_on="2026-08-19",
        )


@pytest.mark.parametrize(
    "mutation",
    [
        "status",
        "missing_top_report_code",
        "report_code",
        "currency",
        "conflicting_row",
        "conflicting_account_name",
    ],
)
def test_상태_보고서코드_통화_충돌행이_모호하면_비교를_차단한다(
    mutation: str,
) -> None:
    comparator = _bundle(
        "00000002", "주식회사 베타", scale=1, operating_amount=50
    )
    payload = {
        **dict(comparator.financials or {}),
        "list": [dict(row) for row in (comparator.financials or {}).get("list", [])],
    }
    if mutation == "status":
        payload["status"] = "013"
    elif mutation == "missing_top_report_code":
        payload.pop("reprt_code")
    elif mutation == "report_code":
        payload["list"][0]["reprt_code"] = "11013"
    elif mutation == "currency":
        payload["list"][0]["currency"] = "USD"
    else:
        conflict = dict(payload["list"][0])
        conflict["thstrm_amount"] = "9999"
        if mutation == "conflicting_account_name":
            conflict["account_nm"] = "영업수익"
        payload["list"].append(conflict)

    with pytest.raises(ComparisonBlockedError):
        build_competitive_position(
            _report(),
            self_bundle=_bundle("00000001", "주식회사 알파", scale=2),
            catalog=CATALOG,
            fetch_comparator=lambda _record: replace(
                comparator, financials=payload
            ),
            collected_on="2026-08-19",
        )


def test_양사_공식원문에_같은_고객제품시장이_없으면_비교를_차단한다() -> None:
    comparator = _bundle(
        "00000002",
        "주식회사 베타",
        operating_amount=50,
        official_text="공식 재무제표의 수치만 공시한다.",
    )
    with pytest.raises(ComparisonBlockedError, match="고객·제품·시장"):
        build_competitive_position(
            _report(),
            self_bundle=_bundle("00000001", "주식회사 알파", scale=2),
            catalog=CATALOG,
            fetch_comparator=lambda _record: comparator,
            collected_on="2026-08-19",
        )


def test_일반_축_단어만_같으면_동일_비교범위로_승격하지_않는다() -> None:
    generic = "고객 대상 제품을 시장에 공급한다. 연결재무제표 수치를 공시한다."
    with pytest.raises(ComparisonBlockedError, match="고객·제품·시장"):
        build_competitive_position(
            _report(),
            self_bundle=_bundle(
                "00000001", "주식회사 알파", scale=2, official_text=generic
            ),
            catalog=CATALOG,
            fetch_comparator=lambda _record: _bundle(
                "00000002", "주식회사 베타", official_text=generic
            ),
            collected_on="2026-08-19",
        )


def test_비교사실_해시는_공개문장이_아니라_실제_양사_payload에_결속된다() -> None:
    result = build_competitive_position(
        _report(),
        self_bundle=_bundle("00000001", "주식회사 알파", scale=2),
        catalog=CATALOG,
        fetch_comparator=lambda _record: _bundle(
            "00000002", "주식회사 베타", operating_amount=50
        ),
        collected_on="2026-08-19",
    )
    sources = {source.source_id: source for source in result.sources}
    for fact in result.facts:
        assert evidence_text_hash(fact.state_evidence) in sources[fact.source_id].evidence_hashes
        assert evidence_text_hash(fact.comparator_state_evidence) in sources[
            fact.comparator_source_id
        ].evidence_hashes
        assert evidence_text_hash(fact.claim) not in sources[fact.source_id].evidence_hashes
