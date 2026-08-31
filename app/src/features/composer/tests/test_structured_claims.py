from __future__ import annotations

import json
from dataclasses import replace

import pytest

from src.features.composer.port import (
    ComposedReport,
    ComposedSection,
    ComposedSentence,
    FilingMeta,
    PerformanceTable,
)
from src.features.composer.render import render_report
from src.features.composer.structured_claims import (
    append_past_changes_numeric_claims,
    build_past_changes_numeric_claims,
    enforce_public_numeric_safety,
)
from src.shared.report_quality.fact_binding import fact_evidence_binding
from src.shared.report_quality.numeric_validation import (
    validate_versioned_numeric_record,
)


def _table() -> PerformanceTable:
    payload = {
        "status": "000",
        "list": [
            {
                "fs_div": "CFS",
                "sj_div": "IS",
                "account_id": "ifrs-full_Revenue",
                "account_nm": "매출액",
                "bsns_year": "2025",
                "reprt_code": "11011",
                "currency": "KRW",
                "thstrm_dt": "2025.01.01 ~ 2025.12.31",
                "thstrm_amount": "1242800000000",
                "frmtrm_dt": "2024.01.01 ~ 2024.12.31",
                "frmtrm_amount": "1100000000000",
                "bfefrmtrm_dt": "2023.01.01 ~ 2023.12.31",
                "bfefrmtrm_amount": "1000000000000",
            },
            {
                "fs_div": "CFS",
                "sj_div": "IS",
                "account_id": "dart_OperatingIncomeLoss",
                "account_nm": "영업이익",
                "bsns_year": "2025",
                "reprt_code": "11011",
                "currency": "KRW",
                "thstrm_dt": "2025.01.01 ~ 2025.12.31",
                "thstrm_amount": "200000000000",
                "frmtrm_dt": "2024.01.01 ~ 2024.12.31",
                "frmtrm_amount": "150000000000",
                "bfefrmtrm_dt": "2023.01.01 ~ 2023.12.31",
                "bfefrmtrm_amount": "100000000000",
            },
        ],
    }
    evidence = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return PerformanceTable(
        caption="전자공시 최근 세 사업연도 연결 주요 실적",
        headers=("사업연도", "매출액", "영업이익"),
        rows=(
            ("2025", "12,428", "2,000"),
            ("2024", "11,000", "1,500"),
            ("2023", "10,000", "1,000"),
        ),
        unit="억원",
        cite="조각 9·재무",
        raw_rows=(
            ("2025", "1,242,800,000,000", "200,000,000,000"),
            ("2024", "1,100,000,000,000", "150,000,000,000"),
            ("2023", "1,000,000,000,000", "100,000,000,000"),
        ),
        scale_divisor="100000000",
        scale_places=0,
        evidence_rows=(evidence,) * 3,
        entity_scope="consolidated",
        raw_unit="원",
        unit_dimension="currency",
    )


def _fragments() -> dict[int, dict[str, str]]:
    return {
        9: {
            "종류": "재무",
            "원문": "주요계정(DART API): 매출액 1,242,800,000,000",
        }
    }


def _filing() -> FilingMeta:
    return FilingMeta(
        document_id="20260828000123",
        title="사업보고서",
        disclosed_at="2026-03-20",
    )


def test_누적_24_28퍼센트를_연평균으로_바꾸지_않는다() -> None:
    claims = build_past_changes_numeric_claims(_table(), _fragments(), _filing())

    assert len(claims) == 2
    assert "누적 증감률은 24.28%" in claims[0].text
    assert "연평균" not in claims[0].text
    assert claims[0].structured_claim is not None
    assert claims[0].structured_claim.formula == "rate"
    assert claims[0].structured_claim.period_start == "2023"
    assert claims[0].structured_claim.period_end == "2025"


def test_원값_범위_원payload_재무api신원중_하나라도_없으면_claim을_만들지_않는다() -> None:
    table = _table()

    assert build_past_changes_numeric_claims(
        replace(table, raw_rows=()), _fragments(), _filing()
    ) == ()
    assert build_past_changes_numeric_claims(
        replace(table, entity_scope=""), _fragments(), _filing()
    ) == ()
    assert build_past_changes_numeric_claims(
        replace(table, evidence_rows=()), _fragments(), _filing()
    ) == ()
    assert build_past_changes_numeric_claims(
        table,
        {9: {"종류": "재무", "원문": "사업보고서 안의 다른 재무 문장"}},
        _filing(),
    ) == ()


@pytest.mark.parametrize(
    "tampered",
    [
        pytest.param(
            lambda table: replace(
                table,
                raw_rows=(
                    ("2025", "9,999,999,999,999", "200,000,000,000"),
                    *table.raw_rows[1:],
                ),
            ),
            id="raw-value",
        ),
        pytest.param(
            lambda table: replace(
                table,
                rows=(
                    ("2025", "99,999", "2,000"),
                    *table.rows[1:],
                ),
            ),
            id="display-value",
        ),
        pytest.param(
            lambda table: replace(
                table,
                headers=("사업연도", "영업이익", "매출액"),
            ),
            id="metric-order",
        ),
        pytest.param(
            lambda table: replace(table, entity_scope="separate"),
            id="scope",
        ),
        pytest.param(
            lambda table: replace(table, raw_unit="USD"),
            id="unit",
        ),
        pytest.param(
            lambda table: replace(
                table,
                raw_rows=(
                    ("2026", *table.raw_rows[0][1:]),
                    *table.raw_rows[1:],
                ),
                rows=(
                    ("2026", *table.rows[0][1:]),
                    *table.rows[1:],
                ),
            ),
            id="period",
        ),
    ],
)
def test_DART_원문과_지표_기간_범위_단위_값이_다르면_VERIFIED로_승격하지_않는다(
    tampered,
) -> None:
    assert build_past_changes_numeric_claims(
        tampered(_table()), _fragments(), _filing()
    ) == ()


def test_재무api를_선택된_사업보고서_접수번호로_꾸미지_않는다() -> None:
    claims = build_past_changes_numeric_claims(_table(), _fragments(), None)

    assert claims
    structured = claims[0].structured_claim
    assert structured is not None
    assert "opendart.fss.or.kr:fnlttsinglacnt.json" in structured.source_identity
    assert _filing().document_id not in structured.source_identity


def test_decimal_지수가_너무_커도_보고서_전체를_죽이지_않는다() -> None:
    table = _table()
    extreme = replace(
        table,
        raw_rows=(("2025", "1E+999999999"), ("2023", "1")),
        rows=(("2025", "매우 큼"), ("2023", "1")),
        evidence_rows=("DART 재무 API 원 payload",) * 2,
    )

    assert build_past_changes_numeric_claims(extreme, _fragments(), _filing()) == ()


def test_음수_기준이나_0교차는_누적_증감률_claim으로_만들지_않는다() -> None:
    table = _table()
    sign_crossing = replace(
        table,
        raw_rows=(("2025", "100"), ("2023", "-100")),
        rows=(("2025", "100"), ("2023", "-100")),
        evidence_rows=("DART 재무 API 원 payload",) * 2,
    )

    assert (
        build_past_changes_numeric_claims(sign_crossing, _fragments(), _filing())
        == ()
    )


def test_구조화_공개문장과_factrecord와_원문지문이_한번에_결속된다() -> None:
    base = ComposedReport(
        sections=(ComposedSection("past_changes", ()),),
        summary=(),
    )
    composed = append_past_changes_numeric_claims(
        base, _table(), _fragments(), _filing()
    )

    rendered = render_report(
        "테스트 주식회사",
        composed,
        _fragments(),
        _table(),
        as_of_date="2026-08-28",
        filing_meta=_filing(),
    )

    assert len(rendered.fact_records) == 2
    fact = rendered.fact_records[0]
    assert rendered.sections[0].fact_ids == [
        item.fact_id for item in rendered.fact_records
    ]
    assert fact.claim in rendered.sections[0].prose_lines[0][0]
    assert fact.claim_slot == "past_changes:cumulative_change"
    assert fact.metric == "매출액"
    assert fact.sign == "positive"
    assert fact.unit == "%"
    assert fact.unit_dimension == "percent"
    assert fact.formula == "rate"
    assert fact.source_host == "opendart.fss.or.kr"
    assert fact.source_document_id == "fnlttSinglAcnt.json"
    assert _filing().document_id not in fact.source_document_id
    assert validate_versioned_numeric_record(fact) == ()
    assert fact.evidence_binding == fact_evidence_binding(fact)


def test_주장범주는_공유해도_서로_다른_수치사실의_ID는_충돌하지_않는다() -> None:
    claims = build_past_changes_numeric_claims(_table(), _fragments(), _filing())

    assert len(claims) == 2
    assert {
        sentence.planned_claim_slot for sentence in claims
    } == {"past_changes:cumulative_change"}
    assert len(
        {
            sentence.structured_claim.fact_id
            for sentence in claims
            if sentence.structured_claim is not None
        }
    ) == 2


def test_수치원문이_바뀌면_같은_범주여도_사실_ID가_바뀐다() -> None:
    original = build_past_changes_numeric_claims(_table(), _fragments(), _filing())
    changed_payload = json.loads(_table().evidence_rows[0])
    changed_payload["list"][0]["thstrm_amount"] = "1300000000000"
    changed_evidence = json.dumps(
        changed_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    changed_table = replace(
        _table(),
        rows=(
            ("2025", "13,000", "2,000"),
            ("2024", "11,000", "1,500"),
            ("2023", "10,000", "1,000"),
        ),
        raw_rows=(
            ("2025", "1,300,000,000,000", "200,000,000,000"),
            ("2024", "1,100,000,000,000", "150,000,000,000"),
            ("2023", "1,000,000,000,000", "100,000,000,000"),
        ),
        evidence_rows=(changed_evidence,) * 3,
    )
    changed = build_past_changes_numeric_claims(
        changed_table, _fragments(), _filing()
    )

    assert original[0].structured_claim is not None
    assert changed[0].structured_claim is not None
    assert (
        original[0].structured_claim.fact_id
        != changed[0].structured_claim.fact_id
    )


def test_미결속_AI_수치문장은_빼고_프로그램_누적claim은_남긴다() -> None:
    wrong = ComposedSentence(
        text="2년 누적 24.28%를 연평균 25% 이상으로 해석할 수 있습니다.",
        citations=("9",),
        grade="해석",
        verification_state="verified",
    )
    base = ComposedReport(
        sections=(ComposedSection("past_changes", (wrong,)),),
        summary=(),
    )
    with_claim = append_past_changes_numeric_claims(
        base, _table(), _fragments(), _filing()
    )

    safe, filtering = enforce_public_numeric_safety(with_claim)

    texts = [sentence.text for sentence in safe.sections[0].sentences]
    assert wrong.text not in texts
    assert texts == [
        "연결 매출액의 2023년부터 2025년까지 누적 증감률은 24.28%입니다.",
        "연결 영업이익의 2023년부터 2025년까지 누적 증감률은 100.00%입니다.",
    ]
    assert filtering.removed_section_counts == (("past_changes", 1),)


def test_한글로_숫자를_쓴_미결속_해석도_공개본에서_뺀다() -> None:
    report = ComposedReport(
        sections=(
            ComposedSection(
                "past_changes",
                (
                    ComposedSentence(
                        text="매출이 두 배로 늘었다고 해석할 수 있습니다.",
                        citations=("9",),
                        grade="해석",
                    ),
                    ComposedSentence(
                        text="공식 자료에서 성장 흐름이 확인됩니다.",
                        citations=("9",),
                        grade="확인",
                    ),
                ),
            ),
        ),
        summary=(),
    )

    safe, filtering = enforce_public_numeric_safety(report)

    assert [item.text for item in safe.sections[0].sentences] == [
        "공식 자료에서 성장 흐름이 확인됩니다."
    ]
    assert filtering.removed_section_counts == (("past_changes", 1),)


def test_결속값과_다르게_공개문장만_25퍼센트로_바꾸면_제외한다() -> None:
    sentence = build_past_changes_numeric_claims(
        _table(), _fragments(), _filing()
    )[0]
    tampered = replace(
        sentence,
        text="연결 매출액의 2023년부터 2025년까지 누적 증감률은 25.00%입니다.",
    )
    report = ComposedReport(
        sections=(ComposedSection("past_changes", (tampered,)),),
        summary=(),
    )

    safe, filtering = enforce_public_numeric_safety(report)

    assert safe.sections[0].sentences == ()
    assert filtering.removed_total == 1
