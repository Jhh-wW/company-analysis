"""실제10의 참 해석을 제약하되 정확한 실적표·후속 문장은 보존하는 회귀."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from src.features.composer.financial_interpretation import financial_interpretation_problem
from src.features.composer.financial_interpretation_constants import (
    FINANCIAL_COMPARISON_BASIS_MISMATCH, FINANCIAL_INTERPRETATION_REASONS,
    NET_LOSS_CAUSE_UNBOUND,
)
from src.features.composer.grounding_rewrite_constants import grounding_reason_text
from src.features.composer.port import (
    CollectedFragment, ComposedReport, ComposedSection, ComposedSentence,
    PerformanceTable, filing_meta_from_raw,
)
from src.features.composer.render import render_report
from src.features.composer.tests.review_evidence_fixture import review_items
from src.features.composer.verify import (
    _apply_grounding, _is_grounding_rewrite_target, verify_report,
)
from src.shared.report_quality.review_diagnostics import observed_review_outcomes
from src.shared.report_quality.fact_binding import fact_primary_source_metadata_mismatches


SAVED = json.loads((Path(__file__).parent / "fixtures/tenth_financial_interpretation.json")
                   .read_text(encoding="utf-8"))
SOURCE = SAVED["source"]
CLAIM = next(item["text"] for item in SAVED["claims"] if item["number"] == 26)
ROW = next(row for row in SAVED["review_rows"] if row["번호"] == 26)


def test_actual_original_claim_and_true_review_are_rejected_without_changing_proof():
    before = deepcopy(SAVED)
    assert hashlib.sha256(SOURCE.encode()).hexdigest() == SAVED["source_sha256"]
    diagnostics, problems = [], {}
    verdicts = _apply_grounding(json.dumps({"판정": [ROW]}, ensure_ascii=False),
        {26: "참"}, {26: (CLAIM, {"97": SOURCE})}, diagnostics=diagnostics,
        diagnostic_contexts={26: ("past_changes", "본문", CLAIM)},
        grounding_problems=problems, baseline_date="2026-10-23")
    assert verdicts[26] == "근거결속실패"
    assert problems[26] == FINANCIAL_COMPARISON_BASIS_MISMATCH
    outcome, = observed_review_outcomes(diagnostics)
    assert outcome["reason_code"] == FINANCIAL_COMPARISON_BASIS_MISMATCH
    assert outcome["verification_items"] == ("인과 관계",)
    assert outcome["candidate_sha256"] == hashlib.sha256(CLAIM.encode()).hexdigest()
    assert SAVED == before


@pytest.mark.parametrize("text", [
    CLAIM,
    "영업손실이 지속된 것은 영업비용 총액이 영업수익 증가폭보다 컸기 때문이다.",
    "영업손실은 매출액 증가율보다 영업비용 금액이 높았기 때문으로 볼 수 있다.",
    "영업손실은 영업수익의 증가액에 비해 영업비용의 총규모가 컸기 때문이었다.",
    "영업손실이 지속된 것은 영업수익 증가폭보다 영업비용 총액이 컸기 때문이며, 이것이 순손실의 원인은 아니다.",
])
def test_level_and_change_comparison_cannot_explain_a_loss(text):
    assert financial_interpretation_problem(text, {"97": SOURCE}) == FINANCIAL_COMPARISON_BASIS_MISMATCH


@pytest.mark.parametrize("text", [
    "당기순손실은 영업비용이 영업수익을 초과했기 때문이다.",
    "영업손실과 순손실이 발생한 이유는 영업비용 총액이 영업수익 총액보다 컸기 때문이다.",
    "영업비용 증가 때문에 당기순손실이 발생하였다.",
    "매출 감소가 당기순손실의 원인이다.",
])
def test_operating_arithmetic_alone_does_not_establish_net_loss_cause(text):
    assert financial_interpretation_problem(text, {"97": SOURCE}) == NET_LOSS_CAUSE_UNBOUND


@pytest.mark.parametrize("text", [
    "영업손실은 영업수익 총액보다 영업비용 총액이 컸기 때문이다.",
    "영업수익 증가액보다 영업비용 증가액이 작았기 때문에 영업손실이 축소되었다.",
    "매출액 증가율보다 영업비용 증가율이 낮았기 때문에 영업손실이 축소되었다.",
    "영업손실이 지속된 것은 영업수익 증가폭보다 영업비용 총액이 컸기 때문이라고 볼 수 없다.",
    "영업비용이 순손실의 원인은 아니다.",
    "영업수익 증가폭보다 영업비용 총액이 크지만, 두 값만으로 영업손실의 원인을 알 수 없다.",
    "영업수익 증가폭보다 영업비용 총액이 크다는 사실은 영업손실의 원인을 설명하지 않는다.",
    "영업수익 증가폭보다 영업비용 총액이 컸기 때문에 영업손실이 발생한 것은 아니다.",
    "영업비용 총액은 영업수익 증가폭보다 컸으며 영업손실이 발생했다.",
    "당기순손실은 영업비용과 금융비용을 포함한 비용이 수익을 초과했기 때문이다.",
    "당기순손실은 영업비용 증가와 법인세 비용 때문이었다.",
    "영업손실과 당기순손실이 전기보다 줄었다.",
    *SAVED["canonical_performance_prose"],
])
def test_valid_comparison_change_denial_and_nonoperating_explanation_stay_with_review(text):
    assert financial_interpretation_problem(text, {"97": SOURCE}) == ""


def test_direct_source_cause_is_preserved_but_conditional_or_foreign_proof_is_not_borrowed():
    text = "영업비용 증가 때문에 당기순손실이 발생하였다."
    assert financial_interpretation_problem(text, {"1": text}) == ""
    assert financial_interpretation_problem(text, {"1": "만약 " + text}) == NET_LOSS_CAUSE_UNBOUND
    claim = "영업비용 증가 때문에 당기순손실이 지속되었다."
    proof = {"관계": [{"유형": "인과", "근거": "1", "원문": text,
                     "원인": "영업비용 증가", "결과": "당기순손실"}]}
    assert financial_interpretation_problem(claim, {"1": text}, proof) == ""
    assert financial_interpretation_problem(claim, {"2": text}, proof) == NET_LOSS_CAUSE_UNBOUND
    bad = deepcopy(proof)
    bad["관계"][0]["원문"] = "영업비용 증가 때문에 영업손실이 발생하였다."
    assert financial_interpretation_problem(claim, {"1": text}, bad) == NET_LOSS_CAUSE_UNBOUND


@pytest.mark.parametrize("grade", ["확인", "해석"])
@pytest.mark.parametrize("reason", FINANCIAL_INTERPRETATION_REASONS)
def test_invalid_cause_never_adds_a_rewrite_call_and_has_a_readable_reason(grade, reason):
    sentence = ComposedSentence(CLAIM, ("97",), grade)
    assert not _is_grounding_rewrite_target(sentence, "past_changes", reason)
    assert grounding_reason_text(reason) == FINANCIAL_INTERPRETATION_REASONS[reason]


def test_review_render_keeps_performance_table_and_valid_neighbor_without_rewrite():
    # 실제 원문과 수치표를 사용하며 후속 양성 문장의 검수는 기술적 대역이다.
    # 실제26의 승인 내용만 그대로 소비하고 새 모델 승인을 주장하지 않는다.
    neighbor = "영업손실과 당기순손실이 모두 발생하였다."
    fragment = CollectedFragment("97", "재무", SOURCE,
        document_date="2026-03-31", document_title="감사보고서 (2025.12)",
        source_document_id="20260331000296", reporting_period="2025-12-31",
        source_publisher="토스페이먼츠 주식회사")
    t = SAVED["performance_table"]
    table = PerformanceTable(t["caption"], tuple(t["headers"]),
        tuple(map(tuple, t["rows"])), unit=t["display_unit"], cite=t["cite"],
        raw_rows=tuple(map(tuple, t["raw_rows"])), scale_divisor=t["scale_divisor"],
        scale_places=t["scale_places"], entity_scope=t["entity_scope"],
        raw_unit=t["raw_unit"], unit_dimension=t["unit_dimension"])
    report = ComposedReport((ComposedSection("past_changes", (
        ComposedSentence(CLAIM, ("97",), "해석"),
        ComposedSentence(neighbor, ("97",), "확인",
                         planned_claim_slot="past_changes:historical_performance"),
    )),))
    calls = []

    def review(prompt):
        calls.append(prompt)
        assert len(calls) == 1
        rows = []
        for item in review_items(prompt):
            assert item.citations == ("97",)
            rows.append(dict(ROW, 번호=item.number) if item.text == CLAIM else
                        {"번호": item.number, "결과": "참", "근거": ["97"], "검증근거": {}})
        return json.dumps({"판정": rows}, ensure_ascii=False)

    diagnostics = []
    checked = verify_report(report, (fragment,), table, review,
        baseline_date="2026-10-23", diagnostics=diagnostics)
    assert len(calls) == 1
    assert [s.text for s in checked.sections[0].sentences] == [neighbor]
    metadata = filing_meta_from_raw({"rcept_no": "20260331000296", "rcept_dt": "20260331",
                                    "report_nm": "감사보고서 (2025.12)"})
    public = render_report("토스페이먼츠", checked, (fragment,), table,
        as_of_date="2026-10-23", filing_meta=metadata)
    section = next(s for s in public.sections if s.cell == "past_changes")
    assert section.tables[0].rows == [list(row) for row in table.rows]
    assert section.tables[0].raw_rows == [list(row) for row in table.raw_rows]
    assert CLAIM not in str(section.prose_lines)
    assert neighbor in str(section.prose_lines)
    neighbor_fact, = (fact for fact in public.fact_records if fact.claim == neighbor)
    own_source, = (source for source in public.citations if source.source_id == neighbor_fact.source_id)
    assert fact_primary_source_metadata_mismatches(neighbor_fact, own_source) == ()
    assert not any(fact.claim == CLAIM for fact in public.fact_records)
    assert observed_review_outcomes(diagnostics)[0]["reason_code"] == FINANCIAL_COMPARISON_BASIS_MISMATCH
