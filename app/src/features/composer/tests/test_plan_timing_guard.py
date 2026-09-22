"""지난 개시 목표를 현재 진행으로 옮긴 실제 문장과 정상 시점의 대조.

실측 두 문장은 2026-09-22 glovis_fixed 보고서의 6·9장이다. 첫 회귀는
같은 문장을 인용 원문으로도 주어, 축자 복사여도 현재성을 보증하지 못함을 본다.
실제 원문 조각의 복원으로 가장하지 않는다. 검수는 모두 주입한 응답만 사용한다.
"""

from dataclasses import asdict
import json
import re

import pytest

from src.features.composer.grounding import constrain_verdicts
from src.features.composer.plan_timing_guard import plan_timing_problem
from src.features.composer.port import (
    CollectedFragment, ComposedReport, ComposedSection, ComposedSentence,
)
from src.features.composer.render import render_report
from src.features.composer.tests.review_evidence_fixture import review_items
from src.features.composer.verify import verify_report


BASELINE = "2026-09-22"
REASON = "plan_target_year_outdated"
STALE_PLAN = "회사는 2025년 사업 개시를 목표로 인허가 확보 및 거점 구축을 진행 중이다."
REAL_STRATEGY = (
    "전기차 사용후 배터리 산업에서 인도네시아와 국내는 2025년 사업 개시, "
    "유럽과 미국은 2026년 사업 개시를 목표로 인허가 확보 및 거점 구축을 진행 중이며, "
    "국내 제주와 대구 지역에서는 지자체 협업을 통한 배터리 회수·재사용·재활용 "
    "사업을 추진할 예정이다."
)
REAL_POSITION = (
    "회사는 2024년 배터리 재활용 전문 기업 이알과 지분 투자 계약을 체결했으며, "
    "2023년 배터리 전처리 기술 업체 ER에 투자하여 인도네시아와 국내에서 "
    "2025년 사업 개시, 유럽과 미국에서 2026년 사업 개시를 목표로 "
    "인허가 확보 및 거점 구축을 진행 중이다."
)


def _constrain(text, *, sources=None, baseline=BASELINE, verdict="참"):
    return constrain_verdicts(
        json.dumps({"판정": [{"번호": 1, "결과": verdict}]}, ensure_ascii=False),
        {1: verdict}, {1: (text, sources or {"1": text})}, baseline_date=baseline,
    )


@pytest.mark.parametrize("text", (STALE_PLAN, REAL_STRATEGY, REAL_POSITION))
@pytest.mark.parametrize("verdict", ("참", "애매"))
def test_past_target_is_not_current_even_when_the_source_repeats_it(text, verdict):
    constrained, problems = _constrain(text, verdict=verdict)
    assert constrained == {1: "근거결속실패"}
    assert problems == {1: REASON}


@pytest.mark.parametrize("text", (
    "회사는 2025년 사업 개시를 목표로 거점 구축을 진행했다.",
    "회사는 2025년 사업 개시를 목표로 거점 구축을 진행했으며 현재 고객을 지원한다.",
    "회사는 2025년 사업 개시가 목표였으나 목표를 달성하지 못했다.",
    "회사는 2025년 사업 개시 목표를 달성하지 못해 거점 구축을 진행 중이다.",
    "회사는 2025년 사업 개시 일정을 연기하고 현재 거점 구축을 진행 중이다.",
    "회사는 2025년 사업 개시 계획을 중단했고 현재 고객지원센터를 운영한다.",
    "회사는 2025년 사업 개시를 목표로 거점 구축을 진행 중인 것은 아니다.",
    "회사는 2026년 사업 개시를 목표로 거점 구축을 진행 중이다.",
    "회사는 2027년 사업 개시를 목표로 거점 구축을 진행 중이다.",
    "회사는 2025년 투자 계약을 체결하고 2027년 사업 개시를 목표로 거점을 구축 중이다.",
    "회사는 2025년 사업 개시 이후 2027년 공장 준공을 목표로 건설을 진행 중이다.",
    "회사는 2025년부터 사업 개시를 목표로 거점 구축을 진행 중이다.",
    "회사는 2025년 보고서에서 사업 개시를 목표로 거점 구축을 진행한다고 밝혔다.",
    "회사는 2025년 사업 개시를 목표로 거점 구축을 진행하고 있었다.",
    "회사는 2025년 사업 개시를 목표로 거점 구축을 진행 중이었으며 현재 고객을 지원한다.",
    "회사는 2025년 사업 개시 목표를 달성한 뒤 추가 거점 구축을 진행 중이다.",
    "회사는 당시 2025년 사업 개시를 목표로 거점 구축을 추진 중이었습니다.",
    "회사는 당시 2025년 사업 개시를 목표로 거점 구축을 진행 중이었으나 현재는 운영한다.",
    "회사는 2024년 공시에서 2025년 사업 개시를 목표로 거점 구축을 진행 중이라고 밝혔다.",
    "회사는 당시 2025년 사업 개시를 목표로 거점 구축을 진행하고 있다고 설명했다.",
    "회사는 2026년 3월 18일 공시에서 2025년 사업 개시를 목표로 구축을 진행 중이라고 밝혔다.",
))
def test_history_changed_status_and_current_or_future_targets_are_preserved(text):
    assert _constrain(text) == ({1: "참"}, {})


@pytest.mark.parametrize("baseline", (None, "", "날짜 없음", "2026-02-30", "2025-09-22"))
def test_missing_or_inapplicable_baseline_does_not_invent_a_deadline(baseline):
    assert _constrain(STALE_PLAN, baseline=baseline) == ({1: "참"}, {})


def test_current_dated_source_reaffirming_the_same_statement_is_preserved():
    sources = {"1": STALE_PLAN, "2": "2026년 9월 1일 현재 " + STALE_PLAN}
    assert _constrain(STALE_PLAN, sources=sources) == ({1: "참"}, {})


@pytest.mark.parametrize("new_source", (
    "2026년 9월 1일 현재 회사는 국내 고객지원센터를 운영 중이다.",
    "2027년 9월 1일 현재 " + STALE_PLAN,
    "2026년 12월 1일 현재 " + STALE_PLAN,
    "2026년 9월 1일 현재 국내 고객지원센터를 운영 중이다. " + STALE_PLAN,
    "2026년 9월 1일 사업보고서. " + STALE_PLAN,
    "2026년 9월 1일 현재 " + STALE_PLAN.replace("중이다", "중인 것은 아니다"),
    "2026년 9월 1일 현재 " + STALE_PLAN.replace("중이다", "중이었다"),
    "2026년 9월 1일 현재 " + STALE_PLAN.replace("중이다", "중이라고 과거에 설명했다"),
))
def test_unrelated_future_or_publication_date_does_not_refresh_an_old_goal(new_source):
    constrained, problems = _constrain(STALE_PLAN, sources={"1": STALE_PLAN, "2": new_source})
    assert constrained == {1: "근거결속실패"}
    assert problems == {1: REASON}


@pytest.mark.parametrize("text", (
    "신규 서비스는 2024년 출시 예정입니다.",
    "회사는 공장 준공을 2025년까지 목표로 건설을 진행 중이다.",
    "회사는 2025년 말 상용화를 목표로 개발을 추진하고 있다.",
    "회사는 2025년 서비스 개시를 계획하고 있으며 서비스 개시는 2025년 예정입니다.",
))
def test_target_order_and_company_independent_milestones_are_checked(text):
    assert plan_timing_problem(text, {"1": text}, baseline_date=BASELINE) == REASON


def test_each_diagram_cell_keeps_its_own_time_binding():
    cells = ("2025년", "사업 개시를 목표로 거점 구축을 진행 중이다.")
    assert plan_timing_problem(" ".join(cells), {}, cells, baseline_date=BASELINE) == ""
    assert plan_timing_problem(STALE_PLAN, {}, (STALE_PLAN,), baseline_date=BASELINE) == REASON


def test_extension_does_not_gain_or_lose_other_grounding_requirements():
    text = "회사는 2025년 사업 개시 목표를 2027년으로 연장했으며 거점 구축을 진행 중이다."
    assert plan_timing_problem(text, {"1": text}, baseline_date=BASELINE) == ""
    # 기존 역할 검수가 '으로 연장'에 별도 결속을 요구한다. 시점 변경 보존을
    # 위해 그 별개 계약을 열어 주거나 모든 검수의 합격을 주장하지 않는다.
    assert _constrain(text) == ({1: "근거결속실패"}, {1: "role_binding_evidence_missing"})


def test_an_already_rejected_verdict_keeps_its_disposition():
    assert _constrain(STALE_PLAN, verdict="거짓") == ({1: "거짓"}, {})


@pytest.mark.parametrize("text", (
    "회사는 2025년 사업 개시 목표를 변경하지 않고 거점 구축을 진행 중이다.",
    "회사는 2025년 사업 개시 목표의 연기는 없으며 거점 구축을 진행 중이다.",
))
def test_denied_changes_do_not_refresh_a_target(text):
    assert plan_timing_problem(text, {"1": text}, baseline_date=BASELINE) == REASON


@pytest.mark.parametrize("text", (
    "회사는 2024년 공시에서 투자를 설명했으며 2025년 사업 개시를 목표로 구축을 진행 중이다.",
    "회사는 당시 투자를 설명했으며 현재 2025년 사업 개시를 목표로 구축을 진행 중이다.",
    "회사는 2024년 공시에서 2025년 사업 개시를 목표로 구축을 진행 중이라고 밝혔으며, "
    "현재 2025년 사업 개시를 목표로 구축을 진행 중이다.",
    "회사는 2025년 사업 개시를 목표로 구축을 진행 중이었으며 현재 2025년 사업 개시를 "
    "목표로 구축을 진행 중이다.",
    "회사는 2026년 12월 1일 공시에서 2025년 사업 개시를 목표로 구축을 진행 중이라고 밝혔다.",
))
def test_a_historical_word_does_not_excuse_a_separate_present_claim(text):
    assert plan_timing_problem(text, {"1": text}, baseline_date=BASELINE) == REASON


@pytest.mark.parametrize("grouped", (False, True))
@pytest.mark.parametrize("stale", (STALE_PLAN, REAL_POSITION))
def test_report_review_removes_stale_plan_and_preserves_future_without_more_calls(grouped, stale):
    future = stale.replace("2025년", "2027년")
    texts = (stale, future)
    fragments = tuple(CollectedFragment(str(i), "공시", text) for i, text in enumerate(texts, 1))
    draft = ComposedReport((ComposedSection("business_model", tuple(
        ComposedSentence(fragment.text, (fragment.fragment_id,), "확인")
        for fragment in fragments
    )),))
    calls = []

    def ask(prompt):
        calls.append(prompt)
        items = review_items(re.sub(r"(?m)^  등급: [^\n]+\n", "", prompt))
        return json.dumps({"판정": [
            {"번호": item.number, "장": item.section, "결과": "참", "근거": [
                citation.strip().removeprefix("조각 ").strip() for citation in item.citations
            ]} for item in items
        ]}, ensure_ascii=False)

    diagnostics = []
    checked = verify_report(
        draft, fragments, None, ask, baseline_date=BASELINE, diagnostics=diagnostics,
        allowed_fragment_ids_by_section={"business_model": frozenset(("1", "2"))} if grouped else None,
        allow_sentence_rewrite=False,
    )
    assert tuple(sentence.text for sentence in checked.sections[0].sentences) == (future,)
    assert len(calls) == 1, "시점 검사가 새 모델 호출을 만들었습니다"
    assert REASON in json.dumps(diagnostics, ensure_ascii=False)
    public = asdict(render_report("검증회사", checked, fragments, None))
    assert stale not in json.dumps(public, ensure_ascii=False)
    assert future in json.dumps(public, ensure_ascii=False)
