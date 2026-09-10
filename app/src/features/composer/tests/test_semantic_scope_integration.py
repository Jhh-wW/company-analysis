"""같은 검수 호출의 승인도 원문의 계획·조건·공식문화 범위를 넘을 수 없다."""

from hashlib import sha256
import json
import re

import pytest

from src.features.composer.constants import SECTION_GUIDES
from src.features.composer.pipeline import _apply_generation_quality_label
from src.features.composer.port import CollectedFragment, ComposedReport, ComposedSection, ComposedSentence, FlowRow
from src.features.composer.review_outcomes import final_review_outcomes
from src.features.composer.structured_claims import NumericSafetyFiltering
from src.features.composer.tests.review_evidence_fixture import review_items
from src.features.composer.verify import verify_report, verify_sentences
from src.features.pipeline.port import Grade, Report
from src.shared.report_quality.generation import GenerationQualityObservation


CASES = (
    (
        "competitive_position", "stated_differentiator", "planned_claim_asserted",
        "회사는 공급망금융시장을 선도해나가고자 합니다.",
        "회사는 '공급망금융시장을 선도'한다고 밝혔다.",
        "회사는 공급망금융시장을 선도해나가고자 합니다.",
    ),
    (
        "operations_partners", "operating_role", "scope_condition_unbound",
        "한빛상품 | 신용등급 QX+ 이상 기업을 대상으로 하는 상품",
        "기업금융 채널은 신용등급 QX+ 이상 기업을 대상으로 한다.",
        "한빛상품은 신용등급 QX+ 이상 기업을 대상으로 한다.",
    ),
    (
        "culture", "decision_process", "culture_evidence_scope_mismatch",
        "회사는 신규 서비스를 출시하여 고객 접점을 확대했다.",
        "신규 서비스를 출시한 것은 조직의 의사결정 방식을 보여준다.",
        "회사는 신규 서비스를 출시하여 고객 접점을 확대했다.",
    ),
)


def _approver(calls, verdict="참"):
    def ask(prompt):
        calls.append(prompt)
        # 기존 골든 fixture 독자는 본문 앞의 등급 줄을 받지 않으므로 시험
        # 어댑터에서 그 표시만 건너뛴다. 실제 검수 입력은 calls에 보존한다.
        items = review_items(re.sub(r"(?m)^  등급: [^\n]+\n", "", prompt))
        assert items, "실제 검수 프롬프트에서 후보를 읽지 못했습니다"
        return json.dumps({"판정": [
            {"번호": item.number, "장": item.section, "근거": list(item.citations), "결과": verdict}
            for item in items
        ]}, ensure_ascii=False)
    return ask


@pytest.mark.parametrize("section,slot,reason,source,bad,good", CASES, ids=("plan", "scope", "culture"))
@pytest.mark.parametrize("grouped", (False, True))
@pytest.mark.parametrize("diagnostics_enabled", (False, True))
@pytest.mark.parametrize("verdict", ("참", "애매"))
def test_approved_wrong_scope_is_removed_with_one_call(
    section, slot, reason, source, bad, good, grouped, diagnostics_enabled, verdict,
):
    if section == "culture":
        # 긍정 대조는 다른 출처의 실제 조직 절차를 사용한다. 사업 출시 자체를
        # 문화 장에 승인해 이 검사의 범위를 넓혀 해석하지 않는다.
        good = "회사의 의사결정 절차는 담당자가 제안하고 위원회가 승인하는 방식이다."
    fragments = (
        CollectedFragment("1", "사업내용", source),
        CollectedFragment("2", "공식자료", good, document_title="공식 안내"),
    )
    report = ComposedReport((ComposedSection(section, (
        ComposedSentence(bad, ("1",), "확인", planned_claim_slot=f"{section}:{slot}"),
        ComposedSentence(good, ("2",), "확인", planned_claim_slot=f"{section}:{slot}"),
    )),))
    diagnostics = [] if diagnostics_enabled else None
    calls = []
    verified = verify_report(
        report, fragments, None, _approver(calls, verdict), diagnostics=diagnostics,
        allowed_fragment_ids_by_section={section: frozenset({"1", "2"})} if grouped else None,
    )
    assert [sentence.text for sentence in verified.sections[0].sentences] == [good]
    assert len(calls) == 1
    assert SECTION_GUIDES[section] in calls[0]
    if diagnostics_enabled:
        closed = final_review_outcomes(verified, diagnostics)
        assert len(closed) == 1
        assert closed[0]["reason_code"] == reason
        assert closed[0]["candidate_sha256"] == sha256(bad.encode()).hexdigest()
        assert bad not in repr(closed) and source not in repr(closed)


def test_culture_owner_still_applies_without_planned_slot_or_diagnostic_sink():
    bad = CASES[2][4]
    report = ComposedReport((ComposedSection("culture", (
        ComposedSentence(bad, ("1",), "확인"),
    )),))
    calls = []
    checked = verify_report(report, (CollectedFragment("1", "사업내용", CASES[2][3]),),
                            None, _approver(calls))
    assert checked.sections[0].sentences == ()
    assert len(calls) == 1


@pytest.mark.parametrize("section,slot,reason,source,bad,good", CASES, ids=("plan", "scope", "culture"))
def test_summary_recheck_preserves_scope_guards(section, slot, reason, source, bad, good):
    calls = []
    diagnostics = []
    checked = verify_sentences(
        (ComposedSentence(bad, ("1",), "확인", planned_claim_slot=f"{section}:{slot}"),),
        (CollectedFragment("1", "사업내용", source),), None, _approver(calls),
        diagnostics=diagnostics,
    )
    assert checked == ()
    assert len(calls) == 1
    assert diagnostics[0]["section_id"] == "summary"
    assert diagnostics[0]["reason_code"] == reason


def test_scope_notice_does_not_call_the_issue_numeric_failure_or_data_absence():
    observation = GenerationQualityObservation(
        mode="shadow", contract_version="report-quality-v1", quality_grade="완성",
        safety_decision="공개 허용", publication_grade="완성", release_allowed=True,
        quality_shortfalls=(), safety_problems=(), substantive_claims=40,
        verified_claims=40, verified_ratio="1", document_sources=8,
    )
    report = Report(company="시험기업", job="", corp_type="상장사", grade=Grade.COMPLETE, sections=[])
    diagnostics = [{"kind": "본문", "reason_code": case[2]} for case in CASES]
    labelled = _apply_generation_quality_label(
        report, observation, NumericSafetyFiltering(), review_diagnostics=diagnostics,
    )
    notices = [reason for reason in labelled.shortfall_reasons if "범위가 일치" in reason]
    assert len(notices) == 1 and "본문 3개" in notices[0]
    assert "자료 자체가 없다는 뜻은 아닙니다" in notices[0]
    assert not any("숫자·날짜 문장의 항목" in reason for reason in labelled.shortfall_reasons)


@pytest.mark.parametrize("verdict", ("참", "애매"))
@pytest.mark.parametrize("diagnostics_enabled", (False, True))
def test_grouped_flow_scope_uses_original_cells_and_closed_diagnostics(verdict, diagnostics_enabled):
    row = FlowRow(("기업금융 채널", "신용등급 QX+ 이상 기업 대상", "대출 제공"), ("1",))
    source = "한빛상품 | 신용등급 QX+ 이상 기업을 대상으로 하는 대출상품"
    report = ComposedReport((ComposedSection("operations_partners", (), flow_rows=(row,)),))
    diagnostics = [] if diagnostics_enabled else None
    calls = []
    checked = verify_report(
        report, (CollectedFragment("1", "공시", source),), None, _approver(calls, verdict),
        allowed_fragment_ids_by_section={"operations_partners": frozenset({"1"})},
        diagnostics=diagnostics,
    )
    assert checked.sections[0].flow_rows == ()
    assert len(calls) == 1
    if diagnostics_enabled:
        closed = final_review_outcomes(checked, diagnostics)
        assert len(closed) == 1
        assert closed[0]["reason_code"] == "scope_condition_unbound"
        assert closed[0]["candidate_sha256"] == sha256(" ".join(row.cells).encode()).hexdigest()
        assert source not in repr(closed)


def test_grouped_flow_preserves_the_named_product_and_its_own_condition():
    row = FlowRow(("한빛상품", "신용등급 QX+ 이상 기업 대상", "대출 제공"), ("1",))
    source = "한빛상품 | 신용등급 QX+ 이상 기업을 대상으로 하는 대출상품"
    report = ComposedReport((ComposedSection("operations_partners", (), flow_rows=(row,)),))
    calls = []
    checked = verify_report(
        report, (CollectedFragment("1", "공시", source),), None, _approver(calls),
        allowed_fragment_ids_by_section={"operations_partners": frozenset({"1"})},
    )
    assert checked.sections[0].flow_rows == (row,)
    assert len(calls) == 1
