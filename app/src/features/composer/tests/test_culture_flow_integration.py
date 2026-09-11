"""실제 문화 도식의 목표 격상을 두 공개 검수 진입점에서 대조한다."""

from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import re

import pytest

from src.features.composer.culture_constants import (
    CULTURE_SECTION_EVIDENCE_OFFCONTRACT,
)
from src.features.composer.culture_guard import culture_flow_problem
from src.features.composer.diagram_check import check_diagrams
from src.features.composer.port import (
    CollectedFragment, ComposedReport, ComposedSection, FlowRow,
)
from src.features.composer.review_outcomes import final_review_outcomes
from src.features.composer.tests.review_evidence_fixture import review_items
from src.features.composer.verify import verify_report


def _actual_inputs():
    fixture = json.loads(Path(__file__).with_name("culture_flow_public_cases.json").read_text(encoding="utf-8"))
    for source in fixture["sources"].values():
        assert sha256(source["text"].encode()).hexdigest() == source["exact_sha256"]
    fragments = tuple(CollectedFragment(fid, "공시", source["text"])
                      for fid, source in fixture["sources"].items())
    rows = tuple(FlowRow(tuple(case["cells"]), tuple(case["citations"])) for case in fixture["cases"])
    return ComposedReport((ComposedSection("culture", (), flow_rows=rows),)), fragments


def _check(report, fragments, grouped, diagnostics):
    calls = []

    def ask(prompt):
        calls.append(prompt)
        if grouped:
            items = review_items(re.sub(r"(?m)^  등급: [^\n]+\n", "", prompt))
            assert items, "묶음 검수 입력의 후보가 없습니다"
            records = [{"번호": item.number, "장": item.section, "근거": [
                citation.strip().removeprefix("조각 ").strip() for citation in item.citations
            ], "결과": "참"} for item in items]
        else:
            records = [{"번호": number, "결과": "참"}
                       for number in range(1, len(report.sections[0].flow_rows) + 1)]
        return json.dumps({"판정": records}, ensure_ascii=False)

    if grouped:
        section = report.sections[0]
        result = verify_report(report, fragments, None, ask, diagnostics=diagnostics,
                               allowed_fragment_ids_by_section={section.section_id: frozenset(
                                   fid for row in section.flow_rows for fid in row.citations
                               )})
    else:
        result, _problems = check_diagrams(report, fragments, ask, diagnostics=diagnostics)
    assert len(calls) == 1, "목표 범위 검사가 추가 모델 호출을 만들었습니다"
    return result


@pytest.mark.parametrize("grouped", (False, True), ids=("legacy", "grouped"))
@pytest.mark.parametrize("record_diagnostics", (False, True))
def test_actual_goal_is_removed_and_two_actual_procedures_remain(grouped, record_diagnostics):
    """★ 2026-09-11: 남는 행이 2개에서 1개로 줄었다 (독립 검토 P1-5 확정 규칙).

    2번 행의 1칸 「위험 대비 수익 극대화」와 2칸 「위험의 인식·측정·통제·보고
    절차 운영」은 «기댈 절을 찾아보니 그 절이 이 장의 소재가 아니었다» —
    결속 실패로 빠진 것이지, 칸의 낱말이 재무 어휘라서 빠진 것이 아니다.
    (재무 어휘 판정은 culture_financial_risk_* 가드가 따로 한다.)
    예전에는 행 전체를 한 후보로 봐서 3칸의 「리스크관리위원회 의사결정」에
    업혀 통과했다. 이제 판정 대상 칸 하나라도 제외면 행을 뺀다 — 부재 단언과
    같은 잣대다. 남는 1번 행(직업윤리·준법정신·조직문화 정착)은 그대로다.
    """

    draft, fragments = _actual_inputs()
    diagnostics = [] if record_diagnostics else None
    checked = _check(draft, fragments, grouped, diagnostics)
    assert checked.sections[0].flow_rows == draft.sections[0].flow_rows[:1]
    if diagnostics is not None:
        outcomes = final_review_outcomes(checked, diagnostics)
        assert len(outcomes) == 2
        # 사유는 행마다 다르다 — 2번 행은 칸이 기댈 절을 못 찾아 원문 절 계약에,
        # 3번 행은 예전처럼 목표 격상(공식 조직설명)에 걸린다.
        assert sorted(item["reason_code"] for item in outcomes) == [
            "culture_evidence_scope_mismatch",
            "culture_section_evidence_offcontract",
        ]
        assert any(
            item["verification_items"] == ("공식 조직설명",) for item in outcomes
        )
        assert "금융 취약층" not in repr(outcomes)


@pytest.mark.parametrize("grouped", (False, True), ids=("legacy", "grouped"))
def test_same_official_goal_keeps_its_plan_qualification(grouped):
    """계획임을 명시한 원칙 칸은 «목표 격상» 가드에 걸리지 않는다.

    ★ 의도가 바뀐 근거 — 이 행이 기댄 원문(경영목표·마케팅·상생금융 추진
      계획)에는 사람·조직 제도 소재도 조직 주체도 없다. 그래서 8장 «원문 절»
      긍정 계약이 새로 걸린 뒤로는 진입점에서 그 계약에 걸려 빠진다.
      이 시험이 원래 지키던 것(계획 한정어를 붙이면 «현재형 격상»으로 보지
      않는다)은 아래 두 단정으로 그대로 지킨다 — 가드 자체가 ''을 돌려주고,
      진입점에서 빠지는 사유도 «목표 격상»이 아니다.
    """

    draft, fragments = _actual_inputs()
    goal = draft.sections[0].flow_rows[-1]
    qualified = replace(goal, cells=(goal.cells[0], goal.cells[1] + " 계획", goal.cells[2]))
    sources = {
        fid: fragment.text
        for fragment in fragments
        for fid in (fragment.fragment_id,)
        if fid in goal.citations
    }
    # ① 목표 격상 가드는 계획 한정어를 존중한다 (원래 이 시험의 보호 대상).
    assert culture_flow_problem(qualified.cells, sources) == ""

    draft = replace(draft, sections=(replace(draft.sections[0], flow_rows=(qualified,)),))
    diagnostics: list[dict] = []
    checked = _check(draft, fragments, grouped, diagnostics)

    # ② 진입점에서 빠지되, 사유는 «목표 격상»이 아니라 «장 원문 계약»이다.
    assert checked.sections[0].flow_rows == ()
    assert [event["reason_code"] for event in diagnostics] == [
        CULTURE_SECTION_EVIDENCE_OFFCONTRACT
    ]
