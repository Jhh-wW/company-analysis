"""실제 문화 도식의 목표 격상을 두 공개 검수 진입점에서 대조한다."""

from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import re

import pytest

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
    draft, fragments = _actual_inputs()
    diagnostics = [] if record_diagnostics else None
    checked = _check(draft, fragments, grouped, diagnostics)
    assert checked.sections[0].flow_rows == draft.sections[0].flow_rows[:2]
    if diagnostics is not None:
        outcomes = final_review_outcomes(checked, diagnostics)
        assert len(outcomes) == 1
        assert outcomes[0]["reason_code"] == "culture_evidence_scope_mismatch"
        assert outcomes[0]["verification_items"] == ("공식 조직설명",)
        assert "금융 취약층" not in repr(outcomes)


@pytest.mark.parametrize("grouped", (False, True), ids=("legacy", "grouped"))
def test_same_official_goal_keeps_its_plan_qualification(grouped):
    draft, fragments = _actual_inputs()
    goal = draft.sections[0].flow_rows[-1]
    qualified = replace(goal, cells=(goal.cells[0], goal.cells[1] + " 계획", goal.cells[2]))
    draft = replace(draft, sections=(replace(draft.sections[0], flow_rows=(qualified,)),))
    assert _check(draft, fragments, grouped, []).sections[0].flow_rows == (qualified,)
