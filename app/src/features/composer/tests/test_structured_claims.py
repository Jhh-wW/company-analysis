from __future__ import annotations

import json
from dataclasses import replace
from decimal import Decimal

import pytest

from src.features.audit_financials.constants import AUDIT_REPORT_STATEMENT_SOURCE
from src.features.audit_financials.logic import parse_audit_financials
from src.features.composer.port import (
    ComposedReport,
    ComposedSection,
    ComposedSentence,
    FilingMeta,
    PerformanceTable,
    performance_table_from_report_table,
)
from src.features.composer.render import render_report
from src.features.composer import structured_claims
from src.features.composer.structured_claims import (
    append_past_changes_numeric_claims,
    build_past_changes_numeric_claims,
    enforce_public_numeric_safety,
    is_release_ready_numeric_sentence,
)
from src.features.pipeline.port import ReportTable
from src.shared.report_evidence.legacy_fragment_kinds import (
    LEGACY_KIND_AUDIT_FINANCIAL,
)
from src.shared.report_quality.fact_binding import (
    fact_evidence_binding,
    fact_primary_source_metadata_mismatches,
)
from src.shared.report_quality.numeric_validation import (
    validate_versioned_numeric_record,
)
from src.shared.report_evidence.policy import injected_slots_for


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
    assert fact.claim_slot == "past_changes:historical_performance"
    assert fact.metric == "매출액"
    assert fact.sign == "positive"
    assert fact.unit == "%"
    assert fact.unit_dimension == "percent"
    assert fact.formula == "rate"
    assert fact.source_host == "opendart.fss.or.kr"
    assert fact.source_document_id == "fnlttSinglAcnt.json"
    assert _filing().document_id not in fact.source_document_id
    assert fact.supporting_source_ids == [fact.source_id]
    assert fact.supporting_source_identities == [
        "document:opendart.fss.or.kr:fnlttsinglacnt.json"
    ]
    assert len(fact.supporting_evidence_hashes) == 1
    assert fact.supporting_evidence_hashes[0] in rendered.citations[0].exact_evidence_hashes
    assert validate_versioned_numeric_record(fact) == ()
    assert fact.evidence_binding == fact_evidence_binding(fact)


def test_주장범주는_공유해도_서로_다른_수치사실의_ID는_충돌하지_않는다() -> None:
    claims = build_past_changes_numeric_claims(_table(), _fragments(), _filing())

    assert len(claims) == 2
    assert {
        sentence.planned_claim_slot for sentence in claims
    } == {"past_changes:historical_performance"}
    assert len(
        {
            sentence.structured_claim.fact_id
            for sentence in claims
            if sentence.structured_claim is not None
        }
    ) == 2


def test_rendered_numeric_facts_preserve_canonical_source_metadata() -> None:
    """수치 검증을 실제 통과한 사실도 출처 종류를 별도 문구로 바꾸지 않는다."""

    composed = append_past_changes_numeric_claims(
        ComposedReport(sections=(ComposedSection("past_changes", ()),)),
        _table(), _fragments(), _filing(),
    )
    rendered = render_report(
        "테스트 주식회사", composed, _fragments(), _table(),
        as_of_date="2026-08-28", filing_meta=_filing(),
    )
    sources = {source.source_id: source for source in rendered.citations}

    assert len(rendered.fact_records) == 2
    for fact in rendered.fact_records:
        assert validate_versioned_numeric_record(fact) == ()
        assert fact_primary_source_metadata_mismatches(fact, sources[fact.source_id]) == ()
        assert fact.evidence_binding == fact_evidence_binding(fact)


def test_구조화실적_생산자는_정본이_맡긴_필수의미칸을_정확히_채운다() -> None:
    claims = build_past_changes_numeric_claims(_table(), _fragments(), _filing())

    assert claims
    assert {
        sentence.planned_claim_slot for sentence in claims
    } == set(injected_slots_for("past_changes"))


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
        "연결 매출액의 2023년부터 2025년까지 누적 증감률은 24.28%이다.",
        "연결 영업이익의 2023년부터 2025년까지 누적 증감률은 100.00%이다.",
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
        text="연결 매출액의 2023년부터 2025년까지 누적 증감률은 25.00%이다.",
    )
    report = ComposedReport(
        sections=(ComposedSection("past_changes", (tampered,)),),
        summary=(),
    )

    safe, filtering = enforce_public_numeric_safety(report)

    assert safe.sections[0].sentences == ()
    assert filtering.removed_total == 1


# ══════════════════════════════════════════════════════════
# 비상장 회사 — 감사보고서 «평문» 손익계산서로 만든 2개년 표
# ══════════════════════════════════════════════════════════
#
# ★ 실측 결함 S5 (2026-09-22 뤼튼 3쪽·2026-09-18 메디라인 3쪽) — 표에는 두 해
#   수치가 다 있는데 4장 「3개년 주요 변화」에 변화 문장이 한 줄도 없었다.
#   주요계정 API 응답이 없는 회사의 표는 감사보고서 평문에서 만들어지는데,
#   구조화 claim 생산기가 API payload 조각만 근거로 인정했기 때문이다.
# ★ 아래 원문은 운영 파서(`parse_audit_financials`)에 그대로 넣어 표를 만든다.
#   표를 손으로 지어내면 실제 조각에서만 깨지는 결함을 시험이 못 잡는다.
#   그 결과 행은 실제 PDF 4장 표와 같은 값이다(뤼튼 471.2/-588.5/-581.2 ·
#   30.7/-301.6/-302.4, 메디라인 424.8/28.7/20.8 · 397.4/34.1/23.4).

_WRTN_RECEIPT_NUMBER = "20260414000008"
_MEDILINE_RECEIPT_NUMBER = "20260401000496"

_WRTN_STATEMENT = """감 사 보 고 서

손 익 계 산 서
제 5 기 2025.01.01 부터 2025.12.31 까지
제 4 기 2024.01.01 부터 2024.12.31 까지
회사명                                        (단위: 원)
과        목                  제 5 (당) 기            제 4 (전) 기
Ⅰ. 영업수익              47,117,211,348           3,073,716,215
Ⅱ. 영업비용             105,969,364,757          33,236,422,254
Ⅲ. 영업손실              58,852,153,409          30,162,706,039
Ⅳ. 당기순손실            58,121,775,636          30,242,954,858

재 무 상 태 표
"""

_MEDILINE_STATEMENT = """감 사 보 고 서

손 익 계 산 서
제 31 기 2025.01.01 부터 2025.12.31 까지
제 30 기 2024.01.01 부터 2024.12.31 까지
회사명                                        (단위: 원)
과        목               제 31 (당) 기            제 30 (전) 기
Ⅰ. 매출액               42,483,833,889          39,741,597,397
Ⅱ. 매출원가             31,205,118,402          29,118,774,215
Ⅲ. 매출총이익           11,278,715,487          10,622,823,182
Ⅳ. 판매비와관리비         8,407,691,294           7,210,265,121
Ⅴ. 영업이익              2,871,024,193           3,412,558,061
Ⅵ. 당기순이익            2,083,445,112           2,338,901,774

재 무 상 태 표
"""


def _audit_case(
    statement: str,
    *,
    fragment_number: int,
    receipt_number: str,
) -> tuple[PerformanceTable, dict[int, dict[str, str]], FilingMeta]:
    """운영 파서로 만든 감사보고서 표·인용 조각·공시 신원 한 벌."""

    cite = f"조각 {fragment_number}·{LEGACY_KIND_AUDIT_FINANCIAL}"
    parsed = parse_audit_financials(statement, cite=cite)
    assert parsed.performance_table is not None, parsed.diagnostic_reason
    assert parsed.evidence is not None
    table = performance_table_from_report_table(
        ReportTable(**parsed.performance_table.to_report_table_payload())
    )
    # real.py `_build_performance_table_with_audit_fallback`가 만드는 조각과
    # 같은 열쇠다. 조각 원문은 표 근거 payload의 원문 구간 그 자체다.
    fragments = {
        fragment_number: {
            "종류": LEGACY_KIND_AUDIT_FINANCIAL,
            "원문": parsed.evidence.excerpt,
            "문서ID": receipt_number,
            "문서명": "감사보고서 (2025.12)",
            "문서일": "2026-04-14",
            "원문위치": parsed.evidence.location,
        }
    }
    filing = FilingMeta(
        document_id=receipt_number,
        title="감사보고서 (2025.12)",
        disclosed_at="2026-04-14",
    )
    return table, fragments, filing


def _wrtn_case() -> tuple[PerformanceTable, dict[int, dict[str, str]], FilingMeta]:
    return _audit_case(
        _WRTN_STATEMENT,
        fragment_number=28,
        receipt_number=_WRTN_RECEIPT_NUMBER,
    )


def _mediline_case() -> tuple[PerformanceTable, dict[int, dict[str, str]], FilingMeta]:
    return _audit_case(
        _MEDILINE_STATEMENT,
        fragment_number=59,
        receipt_number=_MEDILINE_RECEIPT_NUMBER,
    )


def test_감사보고서_평문표가_만든_원문종류는_정본과_같다() -> None:
    """composer가 다시 적은 값과 audit_financials 정본이 갈라지지 않게 잠근다."""

    assert (
        structured_claims.AUDIT_STATEMENT_EVIDENCE_SOURCE
        == AUDIT_REPORT_STATEMENT_SOURCE
    )


def test_감사보고서_평문표도_변화_문장_세_개를_만든다() -> None:
    table, fragments, filing = _wrtn_case()

    claims = build_past_changes_numeric_claims(table, fragments, filing)

    assert [sentence.text for sentence in claims] == [
        "별도 매출액의 2024년부터 2025년까지 누적 증감률은 1432.91%이다.",
        "별도 영업이익은 2024년 -301.6억원에서 2025년 -588.5억원으로 손실이 늘었다.",
        "별도 당기순이익은 2024년 -302.4억원에서 2025년 -581.2억원으로 손실이 늘었다.",
    ]
    assert [
        sentence.structured_claim.formula
        for sentence in claims
        if sentence.structured_claim is not None
    ] == ["rate", "signed_change", "signed_change"]
    assert {
        sentence.planned_claim_slot for sentence in claims
    } == {"past_changes:historical_performance"}
    assert {sentence.citations for sentence in claims} == {("28",)}
    assert len(
        {
            sentence.structured_claim.fact_id
            for sentence in claims
            if sentence.structured_claim is not None
        }
    ) == 3
    for sentence in claims:
        assert sentence.structured_claim is not None
        assert sentence.structured_claim.source_identity == (
            f"document:dart.fss.or.kr:{_WRTN_RECEIPT_NUMBER}"
        )
        assert is_release_ready_numeric_sentence(
            sentence, section_id="past_changes"
        )


def test_감사보고서_2개년_표에서_증감률_세_문장이_나온다() -> None:
    table, fragments, filing = _mediline_case()

    claims = build_past_changes_numeric_claims(table, fragments, filing)

    assert [sentence.text for sentence in claims] == [
        "별도 매출액의 2024년부터 2025년까지 누적 증감률은 6.90%이다.",
        "별도 영업이익의 2024년부터 2025년까지 누적 증감률은 -15.87%이다.",
        "별도 당기순이익의 2024년부터 2025년까지 누적 증감률은 -10.92%이다.",
    ]
    for sentence in claims:
        assert sentence.structured_claim is not None
        assert sentence.structured_claim.formula == "rate"
        assert is_release_ready_numeric_sentence(
            sentence, section_id="past_changes"
        )


def test_평문_조각에_원수치가_없으면_claim을_만들지_않는다(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """★ 표와 조각이 다른 원문이면 「근거 있는 숫자」가 아니다."""

    table, fragments, filing = _wrtn_case()
    other = dict(fragments[28])
    other["원문"] = other["원문"].replace("47,117,211,348", "47,117,211,000")

    with caplog.at_level("WARNING"):
        claims = build_past_changes_numeric_claims(
            table, {28: other}, filing
        )

    assert claims == ()
    assert any(
        "claim 을 만들지 못했습니다" in record.getMessage()
        for record in caplog.records
    )


def test_공시_접수번호가_없으면_감사보고서_claim을_만들지_않는다() -> None:
    """원문 주소를 만들 수 없으면 렌더러도 같은 문서 신원을 못 만든다."""

    table, fragments, filing = _wrtn_case()

    assert build_past_changes_numeric_claims(table, fragments, None) == ()
    assert (
        build_past_changes_numeric_claims(
            table, fragments, replace(filing, document_id="2026041400")
        )
        == ()
    )


def test_부호_변화_문장도_표시값만_바꾸면_공개본에서_뺀다() -> None:
    table, fragments, filing = _wrtn_case()
    sentence = build_past_changes_numeric_claims(table, fragments, filing)[1]
    tampered = replace(
        sentence,
        text=(
            "별도 영업이익은 2024년 -301.6억원에서 "
            "2025년 -88.5억원으로 손실이 줄었다."
        ),
    )
    report = ComposedReport(
        sections=(ComposedSection("past_changes", (tampered,)),),
        summary=(),
    )

    safe, filtering = enforce_public_numeric_safety(report)

    assert safe.sections[0].sentences == ()
    assert filtering.removed_total == 1


@pytest.mark.parametrize(
    ("start_value", "end_value", "expected"),
    [
        pytest.param(
            Decimal("-301.6"),
            Decimal("-588.5"),
            "별도 영업이익은 2024년 -301.6억원에서 2025년 -588.5억원으로 "
            "손실이 늘었다.",
            id="손실-확대",
        ),
        pytest.param(
            Decimal("-588.5"),
            Decimal("-301.6"),
            "별도 영업이익은 2024년 -588.5억원에서 2025년 -301.6억원으로 "
            "손실이 줄었다.",
            id="손실-축소",
        ),
        pytest.param(
            Decimal("-301.6"),
            Decimal("0.0"),
            "별도 영업이익은 2024년 -301.6억원에서 2025년 0.0억원으로 "
            "손실이 사라졌다.",
            id="손실-소멸",
        ),
        pytest.param(
            Decimal("-301.6"),
            Decimal("12.4"),
            "별도 영업이익은 2024년 -301.6억원에서 2025년 12.4억원으로 "
            "흑자로 돌아섰다.",
            id="흑자-전환",
        ),
        pytest.param(
            Decimal("30.7"),
            Decimal("-588.5"),
            "별도 영업이익은 2024년 30.7억원에서 2025년 -588.5억원으로 "
            "적자로 돌아섰다.",
            id="적자-전환",
        ),
        pytest.param(
            Decimal("0.0"),
            Decimal("-588.5"),
            "별도 영업이익은 2024년 0.0억원에서 2025년 -588.5억원으로 "
            "적자로 돌아섰다.",
            id="기준값-0-적자-전환",
        ),
        pytest.param(
            Decimal("0.0"),
            Decimal("471.2"),
            "별도 영업이익은 2024년 0.0억원에서 2025년 471.2억원이 됐다.",
            id="기준값-0",
        ),
        pytest.param(Decimal("-301.6"), Decimal("-301.6"), "", id="변화-없음"),
        pytest.param(Decimal("0.0"), Decimal("0.0"), "", id="0-유지"),
    ],
)
def test_부호_구간_문장은_방향마다_정해진_한_문장이다(
    start_value: Decimal,
    end_value: Decimal,
    expected: str,
) -> None:
    assert (
        structured_claims._signed_change_claim_text(
            entity_scope="separate",
            metric="영업이익",
            period_start="2024",
            period_end="2025",
            start_value=start_value,
            end_value=end_value,
            display_unit="억원",
            display_places=1,
        )
        == expected
    )


@pytest.mark.parametrize(
    ("start_raw", "end_raw", "expected", "incorrect_tail"),
    [
        pytest.param(
            "-30,162,706,039",
            "0",
            "별도 영업이익은 2024년 -301.6억원에서 2025년 0.0억원으로 손실이 사라졌다.",
            "흑자로 돌아섰다.",
            id="손실-소멸",
        ),
        pytest.param(
            "-30,162,706,039",
            "1,240,000,000",
            "별도 영업이익은 2024년 -301.6억원에서 2025년 12.4억원으로 흑자로 돌아섰다.",
            "손실이 사라졌다.",
            id="흑자-전환",
        ),
        pytest.param(
            "0",
            "-58,852,153,409",
            "별도 영업이익은 2024년 0.0억원에서 2025년 -588.5억원으로 적자로 돌아섰다.",
            "손실이 사라졌다.",
            id="기준값-0-적자-전환",
        ),
    ],
)
def test_손익_0_경계도_원문표와_결속되며_다른_방향으로_바꾸면_제외한다(
    start_raw: str,
    end_raw: str,
    expected: str,
    incorrect_tail: str,
) -> None:
    # 실제 PDF 픽스처는 보존하고, 0의 열 위치가 명확한 경계 시험용 표를 만든다.
    header = _WRTN_STATEMENT.split("과        목", 1)[0]
    statement = (
        f"<TABLE><TR><TD>{header}</TD></TR>\n"
        "<TR><TD>영업수익</TD><TD>47,117,211,348</TD><TD>3,073,716,215</TD></TR>\n"
        f"<TR><TD>영업이익</TD><TD>{end_raw}</TD><TD>{start_raw}</TD></TR>\n"
        "<TR><TD>당기순손실</TD><TD>58,121,775,636</TD><TD>30,242,954,858</TD></TR>\n"
        "</TABLE>"
    )
    table, fragments, filing = _audit_case(
        statement,
        fragment_number=28,
        receipt_number=_WRTN_RECEIPT_NUMBER,
    )
    sentence = next(
        sentence
        for sentence in build_past_changes_numeric_claims(table, fragments, filing)
        if sentence.structured_claim is not None
        and sentence.structured_claim.metric == "영업이익"
    )

    assert sentence.text == expected
    assert sentence.structured_claim is not None
    assert sentence.structured_claim.formula == "signed_change"
    assert is_release_ready_numeric_sentence(sentence, section_id="past_changes")
    report = ComposedReport(
        sections=(ComposedSection("past_changes", (sentence,)),),
        summary=(),
    )
    safe, filtering = enforce_public_numeric_safety(report)
    assert safe.sections[0].sentences == (sentence,)
    assert filtering.removed_total == 0

    # 숫자를 보존해도 방향어가 결속의 원래 두 값과 맞지 않으면 탈락해야 한다.
    incorrect_text = expected.split("으로 ", 1)[0] + "으로 " + incorrect_tail
    tampered = replace(sentence, text=incorrect_text)
    tampered_report = replace(
        report, sections=(ComposedSection("past_changes", (tampered,)),),
    )
    safe, filtering = enforce_public_numeric_safety(tampered_report)
    assert safe.sections[0].sentences == ()
    assert filtering.removed_total == 1


def test_감사보고서_claim이_렌더_FactRecord와_원문지문까지_결속된다() -> None:
    """★ 문장은 만들어지고 FactRecord만 조용히 사라지는 상태를 막는다.

    렌더러는 조각으로 만든 Source의 문서 신원과 claim의 신원이 «글자까지»
    같을 때만 사실을 발급한다. 두 신원 규칙이 갈라지면 이 시험이 깨진다.
    """

    table, fragments, filing = _wrtn_case()
    base = ComposedReport(
        sections=(ComposedSection("past_changes", ()),),
        summary=(),
    )
    composed = append_past_changes_numeric_claims(
        base, table, fragments, filing
    )

    rendered = render_report(
        "테스트 주식회사",
        composed,
        fragments,
        table,
        as_of_date="2026-09-22",
        filing_meta=filing,
    )

    assert len(rendered.fact_records) == 3
    assert [fact.formula for fact in rendered.fact_records] == [
        "rate",
        "signed_change",
        "signed_change",
    ]
    for fact in rendered.fact_records:
        assert fact.claim_slot == "past_changes:historical_performance"
        assert fact.source_host == "dart.fss.or.kr"
        assert fact.source_document_id == _WRTN_RECEIPT_NUMBER
        assert fact.supporting_source_identities == [
            f"document:dart.fss.or.kr:{_WRTN_RECEIPT_NUMBER}"
        ]
        assert fact.supporting_evidence_hashes[0] in (
            rendered.citations[0].exact_evidence_hashes
        )
        assert validate_versioned_numeric_record(fact) == ()
        assert fact.evidence_binding == fact_evidence_binding(fact)
    loss_fact = rendered.fact_records[1]
    assert loss_fact.raw_value == "start=-301.6 | end=-588.5"
    assert loss_fact.unit == "억원"
    assert loss_fact.display_value == "-286.9"
