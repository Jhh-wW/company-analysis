"""설립관계와 시설종류를 모델이 승인해도 자기 인용으로 확인한다."""
import json

import pytest

from src.features.composer.grounding_constants import TABLE_SOURCE_ID
from src.features.composer.scope_guard import flow_scope_problem, scope_problem
from src.features.composer.verify import (
    REVIEW_EVIDENCE_IDS_KEY, REVIEW_SECTION_KEY,
    _apply_grounding, _parse_grouped_verdicts, _parse_verdicts,
)


@pytest.mark.parametrize("grouped", [False, True])
@pytest.mark.parametrize("section,claim,source", [
    ("identity", "센서 사업을 인적분할하여 설립된 주식회사 새길과 미정산 거래가 있다.",
     "주식회사 새길에 대한 미수금에는 분할과 관련한 미정산 자산이 포함되어 있다."),
    ("portfolio", "연구동 공사는 반도체 시설 관련 공사다.",
     "공사명 | 시설분류 | 발주처\n연구동 공사 | 교육 시설 | 반도체 제조사"),
])
def test_model_acceptance_does_not_supply_missing_qualifier(grouped, section, claim, source):
    row = {"번호": 1, "결과": "참", "근거": ["own"]}
    if grouped:
        row.update({REVIEW_SECTION_KEY: section, REVIEW_EVIDENCE_IDS_KEY: ["own"]})
    raw = json.dumps({"판정": [row]}, ensure_ascii=False)
    parsed = (_parse_grouped_verdicts(raw, {1: section}, {1: frozenset({"own"})})
              if grouped else _parse_verdicts(raw))
    assert parsed == {1: "참"}
    sources = {"own": source}
    before = sources.copy()
    problems = {}
    verdicts = _apply_grounding(raw, parsed, {1: (claim, sources)},
        diagnostic_contexts={1: (section, "본문", "확인")}, grounding_problems=problems)
    assert verdicts[1] != "참"
    assert problems[1] == "scope_condition_unbound"
    assert sources == before


def test_founding_relation_is_checked_in_summary_and_diagram():
    claim = "센서 사업을 인적분할하여 설립된 주식회사 새길이다."
    sources = {"own": "주식회사 새길과 미정산 거래가 있다."}
    assert scope_problem(claim, sources) == "scope_condition_unbound"
    assert flow_scope_problem(("설립", claim), sources) == "scope_condition_unbound"


def test_project_classification_cannot_borrow_financial_table_source():
    claim = "연구동 공사는 반도체 시설 관련 공사다."
    sources = {"own": "공사명 | 발주처\n연구동 공사 | 반도체 제조사",
               TABLE_SOURCE_ID: "공사명 | 시설분류\n연구동 공사 | 반도체 시설"}
    raw = json.dumps({"판정": [{"번호": 1, "결과": "참", "근거": ["own"]}]}, ensure_ascii=False)
    problems = {}
    verdicts = _apply_grounding(raw, {1: "참"}, {1: (claim, sources)},
        diagnostic_contexts={1: ("portfolio", "본문", "확인")}, grounding_problems=problems)
    assert verdicts[1] != "참"
    assert problems[1] == "scope_condition_unbound"


def test_supported_project_classification_preserves_model_verdict():
    claim = "연구동 공사는 반도체 시설 관련 공사다."
    source = "공사명 | 시설분류\n연구동 공사 | 반도체 공장"
    raw = json.dumps({"판정": [{"번호": 1, "결과": "참", "근거": ["own"]}]}, ensure_ascii=False)
    assert _apply_grounding(raw, {1: "참"}, {1: (claim, {"own": source})},
        diagnostic_contexts={1: ("portfolio", "본문", "확인")})[1] == "참"
