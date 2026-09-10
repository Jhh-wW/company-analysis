"""원문 범위 통합을 다른 담당자가 검토하는 소수의 오프라인 반례."""
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import re

import pytest

from src.features.composer.diagram_check import check_diagrams
from src.features.composer.grounding import grounding_problem
from src.features.composer.modality_guard import modality_problem
from src.features.composer.pipeline import _apply_generation_quality_label
from src.features.composer.port import CollectedFragment, ComposedReport, ComposedSection, ComposedSentence, FlowRow
from src.features.composer.review_outcomes import final_review_outcomes
from src.features.composer.structured_claims import NumericSafetyFiltering
from src.features.composer.tests.review_evidence_fixture import review_items
from src.features.composer.verify import verify_report, verify_sentences
from src.features.pipeline.port import Grade, Report
from src.shared.report_quality.generation import GenerationQualityObservation

ARTIFACT = Path(__file__).with_name("semantic_scope_public_cases.json")
PLAN_SOURCE = "당행은 공급망금융시장을 선도할 계획이다."
PLAN_BAD = "당행은 공급망금융시장을 선도한다."
CULTURE_SOURCE = "회사는 신규 서비스를 출시했다."
CULTURE_BAD = "신규 서비스 출시는 조직의 의사결정 방식을 보여준다."
CULTURE_GOOD = "공동 검토가 조직의 의사결정 방식을 보여준다."
CULTURE_OFFICIAL = "당사의 의사결정 절차는 담당자 제안과 위원회 공동 검토 후 승인이다."


def _approver(calls, verdict="참"):
    def ask(prompt):
        calls.append(prompt)
        items = review_items(re.sub(r"(?m)^  등급: [^\n]+\n", "", prompt))
        assert items, "실제 입력에서 검수 후보를 찾지 못했습니다"
        return json.dumps({"판정": [
            # 기존 읽기 도우미가 두 번째 인용부터 앞 공백을 남긴다. 응답 ID 표기만 바로잡는다.
            {"번호": item.number, "장": item.section,
             "근거": [citation.strip().removeprefix("조각 ").strip() for citation in item.citations], "결과": verdict}
            for item in items
        ]}, ensure_ascii=False)
    return ask


def _actual_inputs():
    cases = json.loads(ARTIFACT.read_text(encoding="utf-8"))["cases"]
    fragments = {}
    sections = []
    for case in cases:
        for fragment in case["fragments"]:
            assert sha256(fragment["text"].encode()).hexdigest() == fragment["sha256"]
        sources = tuple(CollectedFragment(item["id"], "공시", item["text"], document_title=item["document_title"])
                        for item in case["fragments"])
        fragments.update({fragment.fragment_id: fragment for fragment in sources})
        citations = tuple(fragment.fragment_id for fragment in sources)
        if case["id"] == "plan_to_assertion":
            # 기존 시점 검증을 면제하지 않도록 정상 대조는 실제 원문의 목표 절 그대로다.
            good = "수수료 없는 비대면 서비스 등 차별화된 고객 경험을 제공하고 공급망금융시장을 선도해나가고자 합니다."
        elif case["id"] == "product_condition_scope":
            good = "기업금융 채널은 신용등급 BB+ 이상의 법인 및 개인사업자를 대상으로 하는 범용 대출상품을 제공한다."
        else:
            good = CULTURE_GOOD
        good_citations = citations
        if case["section"] == "culture":
            # 정상 공식문화 대조는 합성한 별도 절차 자료이며 실제 우리은행 문화로 주장하지 않는다.
            fragments["official-control"] = CollectedFragment("official-control", "공식자료", CULTURE_OFFICIAL)
            good_citations = ("official-control",)
        sections.append(ComposedSection(case["section"], (
            ComposedSentence(case["bad"], citations, "확인"),
            ComposedSentence(good, good_citations, "확인"),
        )))
    return cases, ComposedReport(tuple(sections)), tuple(fragments.values())


@pytest.mark.parametrize("grouped", (False, True), ids=("flat", "grouped"))
@pytest.mark.parametrize("verdict", ("참", "애매"))
def test_actual_three_bad_claims_leave_and_three_controls_remain(grouped, verdict):
    cases, draft, fragments = _actual_inputs()
    calls = []
    diagnostics = None if verdict == "참" else []
    allowed = {section.section_id: frozenset(fid for sentence in section.sentences for fid in sentence.citations)
               for section in draft.sections} if grouped else None
    checked = verify_report(draft, fragments, None, _approver(calls, verdict),
                            allowed_fragment_ids_by_section=allowed, diagnostics=diagnostics)
    assert len(calls) == 1
    assert len(checked.sections) == len(draft.sections)
    for original, actual in zip(draft.sections, checked.sections):
        assert [sentence.text for sentence in actual.sentences] == [original.sentences[1].text]
    if diagnostics is not None:
        closed = final_review_outcomes(checked, diagnostics)
        assert {item["reason_code"] for item in closed} == {case["reason"] for case in cases}
        assert all(case["bad"] not in repr(closed) for case in cases)


def test_summary_culture_slot_is_checked_without_diagnostic_sink():
    calls = []
    result = verify_sentences((ComposedSentence(CULTURE_BAD, ("1",), "확인", planned_claim_slot="culture:decision_process"),),
                              (CollectedFragment("1", "사업내용", CULTURE_SOURCE),), None, _approver(calls))
    assert result == ()
    assert len(calls) == 1


def test_legacy_summary_without_slot_must_not_bypass_culture_guard():
    calls = []
    result = verify_sentences((ComposedSentence(CULTURE_BAD, ("1",), "확인"),),
                              (CollectedFragment("1", "사업내용", CULTURE_SOURCE),), None, _approver(calls))
    assert result == (), "슬롯이 없는 legacy 요약의 문화 추론이 참 응답으로 살아남았습니다"


def test_other_company_assertion_must_not_exempt_this_company_plan():
    sources = {"plan": PLAN_SOURCE, "unrelated": "경쟁사는 공급망금융시장을 선도한다."}
    assert modality_problem(PLAN_BAD, {"plan": PLAN_SOURCE}) == "planned_claim_asserted"
    calls = []
    result = verify_sentences((ComposedSentence(PLAN_BAD, tuple(sources), "확인"),),
                              tuple(CollectedFragment(key, "공시", text) for key, text in sources.items()),
                              None, _approver(calls))
    assert result == (), "다른 회사 직접 서술을 빌려 당행의 계획이 현재형으로 검증 완료됐습니다"
    assert modality_problem(PLAN_BAD, sources) == "planned_claim_asserted"
    assert grounding_problem(PLAN_BAD, sources, {}) == "planned_claim_asserted"


def test_same_company_direct_current_evidence_is_kept():
    assert modality_problem(PLAN_BAD, {"plan": PLAN_SOURCE, "current": PLAN_BAD}) == ""


def test_existing_time_boundary_is_not_relaxed_for_goal_paraphrase():
    cases, _draft, _fragments = _actual_inputs()
    plan = next(case for case in cases if case["id"] == "plan_to_assertion")
    sources = {fragment["id"]: fragment["text"] for fragment in plan["fragments"]}
    assert grounding_problem("당행은 공급망금융시장을 선도해나가고자 합니다.", sources, {}) == "semantic_grounding_missing"


@pytest.mark.parametrize("grouped", (False, True), ids=("legacy-diagram", "grouped-diagram"))
def test_condition_split_across_flow_cells_must_not_expand_to_whole_channel(grouped):
    row = FlowRow(("기업금융 채널", "신용등급 QX+ 이상 기업 대상", "대출 제공"), ("1",))
    source = CollectedFragment("1", "공시", "한빛상품 | 신용등급 QX+ 이상 기업을 대상으로 하는 대출상품")
    draft = ComposedReport((ComposedSection("operations_partners", (), flow_rows=(row,)),))
    if grouped:
        calls = []
        result = verify_report(draft, (source,), None, _approver(calls),
                               allowed_fragment_ids_by_section={"operations_partners": frozenset({"1"})})
    else:
        result, _problems = check_diagrams(draft, (source,), lambda _prompt: json.dumps({"판정": [{"번호": 1, "결과": "참"}]}))
    assert result.sections[0].flow_rows == (), "칸 사이 조건 범위가 끊겨 참 응답으로 전체 채널 조건이 살아남았습니다"


@pytest.mark.parametrize("rewritten,should_keep", ((CULTURE_BAD, False), (CULTURE_GOOD, True)))
def test_rewrite_recheck_preserves_culture_section_without_diagnostics(rewritten, should_keep):
    initial = ComposedSentence("기존 설명은 추가 검토가 필요하다.", ("1", "2"), "확인")
    sources = (CollectedFragment("1", "사업내용", CULTURE_SOURCE),)
    if should_keep:
        initial = replace(initial, citations=("2",))
        sources = (CollectedFragment("2", "공식자료", CULTURE_OFFICIAL),)
    else:
        initial = replace(initial, citations=("1",))
    calls = []
    replies = iter((json.dumps({"판정": [{"번호": 1, "결과": "거짓"}]}), rewritten,
                    json.dumps({"판정": [{"번호": 1, "결과": "참"}]})))
    def ask(prompt):
        calls.append(prompt)
        return next(replies)
    result = verify_report(ComposedReport((ComposedSection("culture", (initial,)),)), sources, None, ask)
    assert len(calls) == 3
    assert [sentence.text for sentence in result.sections[0].sentences] == ([rewritten] if should_keep else [])


def test_semantic_diagnostics_keep_closed_reason_and_non_numeric_notice():
    cases, draft, fragments = _actual_inputs()
    diagnostics = []
    checked = verify_report(draft, fragments, None, _approver([]), diagnostics=diagnostics)
    closed = final_review_outcomes(checked, diagnostics)
    observation = GenerationQualityObservation(
        mode="shadow", contract_version="report-quality-v1", quality_grade="완성", safety_decision="공개 허용",
        publication_grade="완성", release_allowed=True, quality_shortfalls=(), safety_problems=(),
        substantive_claims=40, verified_claims=40, verified_ratio="1", document_sources=8,
    )
    labelled = _apply_generation_quality_label(
        Report(company="합성 대조", job="", corp_type="상장사", grade=Grade.COMPLETE, sections=[]),
        observation, NumericSafetyFiltering(), review_diagnostics=closed,
    )
    assert {item["reason_code"] for item in closed} == {case["reason"] for case in cases}
    assert not any("숫자·날짜 문장의 항목" in reason for reason in labelled.shortfall_reasons)
    assert any("범위가 일치" in reason and "자료 자체가 없다는 뜻은 아닙니다" in reason
               for reason in labelled.shortfall_reasons)
