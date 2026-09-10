"""모델이 참이라고 해도 자기 인용의 관계 근거를 각 공개 경로에서 확인한다."""
import json
import re

import pytest

from src.features.composer.diagram_check import check_diagrams
from src.features.composer.port import (
    CollectedFragment, ComposedReport, ComposedSection, ComposedSentence, FlowRow,
)
from src.features.composer.review_outcomes import final_review_outcomes
from src.features.composer.tests.review_evidence_fixture import review_items
from src.features.composer.verify import verify_report, verify_sentences


CAUSAL_TEXT = "비용 감소가 이익 증가의 주요 원인이다."
SUPERLATIVE_TEXT = "회사는 금융권 최초 진출로 표현했다."
LAUNCH_TEXT = "회사는 통신 사업 진출을 발표했다."
RELATION = {"유형": "인과", "근거": "cause", "원인": "비용 감소",
            "결과": "이익 증가", "원문": CAUSAL_TEXT}


def _inputs():
    sentences = (
        ComposedSentence(CAUSAL_TEXT, ("cause",), "확인"),
        ComposedSentence(SUPERLATIVE_TEXT, ("launch",), "확인"),
    )
    fragments = (
        CollectedFragment("cause", "경영진 설명", CAUSAL_TEXT),
        CollectedFragment("launch", "사업 발표", LAUNCH_TEXT),
    )
    return sentences, fragments


def _approval(calls, *, relation_source="cause"):
    def ask(prompt):
        calls.append(prompt)
        items = review_items(re.sub(r"(?m)^  등급: [^\n]+\n", "", prompt))
        assert items, "검수 입력에서 후보를 읽지 못했습니다"
        return json.dumps({"판정": [
            {"번호": item.number, "장": item.section, "근거": list(item.citations),
             "결과": "참", "검증근거": {"관계": [dict(RELATION, 근거=relation_source)]}}
            for item in items
        ]}, ensure_ascii=False)
    return ask


@pytest.mark.parametrize("grouped", (False, True))
@pytest.mark.parametrize("relation_source", ("cause", "launch"))
def test_body_uses_own_relation_and_preserves_supported_cause(grouped, relation_source):
    sentences, fragments = _inputs()
    calls, diagnostics = [], []
    section = "past_changes"
    report = ComposedReport((ComposedSection(section, sentences),))
    checked = verify_report(
        report, fragments, None, _approval(calls, relation_source=relation_source),
        diagnostics=diagnostics,
        allowed_fragment_ids_by_section={section: frozenset(("cause", "launch"))} if grouped else None,
    )
    texts = [sentence.text for sentence in checked.sections[0].sentences]
    assert SUPERLATIVE_TEXT not in texts
    assert (CAUSAL_TEXT in texts) == (relation_source == "cause")
    assert len(calls) == 1
    assert "원인" in calls[0] and "추가 검증 필요: 관계" in calls[0]
    outcomes = final_review_outcomes(checked, diagnostics)
    assert "superlative_attribution_without_source" in {item["reason_code"] for item in outcomes}
    assert all(item["verification_items"] for item in outcomes)
    assert SUPERLATIVE_TEXT not in repr(outcomes)


def test_summary_applies_the_same_relation_contract():
    sentences, fragments = _inputs()
    calls = []
    checked = verify_sentences(sentences, fragments, None, _approval(calls))
    assert [sentence.text for sentence in checked] == [CAUSAL_TEXT]
    assert len(calls) == 1


def test_grouped_body_and_diagram_share_the_same_guard():
    sentences, fragments = _inputs()
    rows = (
        FlowRow((CAUSAL_TEXT, "", ""), ("cause",)),
        FlowRow((SUPERLATIVE_TEXT, "", ""), ("launch",)),
    )
    section = "operations_partners"
    report = ComposedReport((ComposedSection(section, sentences, flow_rows=rows),))
    calls, diagnostics = [], []
    checked = verify_report(
        report, fragments, None, _approval(calls), diagnostics=diagnostics,
        allowed_fragment_ids_by_section={section: frozenset(("cause", "launch"))},
    )
    assert [sentence.text for sentence in checked.sections[0].sentences] == [CAUSAL_TEXT]
    assert checked.sections[0].flow_rows == (rows[0],)
    assert len(calls) == 1
    assert {item["kind"] for item in final_review_outcomes(checked, diagnostics)} == {"본문", "도식"}


def test_standalone_diagram_requires_own_relation_evidence():
    _, fragments = _inputs()
    rows = (
        FlowRow((CAUSAL_TEXT, "", ""), ("cause",)),
        FlowRow((SUPERLATIVE_TEXT, "", ""), ("launch",)),
    )
    report = ComposedReport((ComposedSection("operations_partners", (), flow_rows=rows),))
    calls, diagnostics = [], []

    def ask(prompt):
        calls.append(prompt)
        return json.dumps({"판정": [
            {"번호": 1, "결과": "참", "검증근거": {"관계": [RELATION]}},
            {"번호": 2, "결과": "참"},
        ]}, ensure_ascii=False)

    checked, problems = check_diagrams(report, fragments, ask, diagnostics=diagnostics)
    assert checked.sections[0].flow_rows == (rows[0],)
    assert len(problems) == 1 and "superlative_attribution_without_source" in problems[0]
    assert len(calls) == 1
    assert "추가 검증 필요: 관계" in calls[0]
    assert diagnostics[0]["verification_items"]
