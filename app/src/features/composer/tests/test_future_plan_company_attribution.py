"""명시적 회사 계획 귀속은 주어·등급에 관계없이 실제 검수에서 근거를 요구한다.

부산항 후보는 2026-09-22 실제 보고서 문구다. 아래 원문은 발동 경계와 결속을
분리해서 검사하는 대역이며, 실제 공시 조각 전체를 복원했다고 주장하지 않는다.
"""

import json
import re

import pytest

from src.features.composer.future_plan_constants import FUTURE_EVIDENCE_MISSING
from src.features.composer.future_plan_guard import future_plan_prose_problem
from src.features.composer.port import (
    CollectedFragment, ComposedReport, ComposedSection, ComposedSentence,
)
from src.features.composer.tests.review_evidence_fixture import review_items
from src.features.composer.verify import verify_report


ACTUAL_PROSE = (
    "부산항 신항에 대규모 복합 물류시설을 구축할 계획으로, 이는 회사의 글로벌 "
    "물류 네트워크 확충과 국내 거점 강화 전략의 일환이다."
)
GOOD_SOURCE = "당사는 대규모 복합 물류시설을 구축할 계획입니다."
BACKGROUND_SOURCE = "물류 산업의 서비스 수요는 다양한 지역에서 발생합니다."
FRAGMENT_ID = "1"
GRADE_LINE_RE = re.compile(r"(?m)^  등급: [^\n]+\n")


def _evidence(source=GOOD_SOURCE):
    return {"미래근거": [{
        "근거": FRAGMENT_ID,
        "대상": "대규모 복합 물류시설",
        "활동": "구축",
        "원문": source,
        "양태": "계획",
    }]}


def _verify(text, *, grade="해석", grouped=False, source=BACKGROUND_SOURCE,
            evidence=None, section_id="future_strategy"):
    calls, diagnostics = [], []

    def reviewer(prompt):
        calls.append(prompt)
        rows = []
        for item in review_items(GRADE_LINE_RE.sub("", prompt)):
            row = {
                "번호": item.number, "장": item.section,
                "근거": [str(citation).split()[-1] for citation in item.citations],
                "결과": "참",
            }
            if evidence is not None:
                row["검증근거"] = evidence
            rows.append(row)
        return json.dumps({"판정": rows}, ensure_ascii=False)

    candidate = ComposedSentence(text, (FRAGMENT_ID,), grade)
    report = ComposedReport((ComposedSection(section_id, (candidate,)),))
    checked = verify_report(
        report, (CollectedFragment(FRAGMENT_ID, "공시", source),), None, reviewer,
        allowed_fragment_ids_by_section=(
            {section_id: frozenset({FRAGMENT_ID})} if grouped else None
        ),
        diagnostics=diagnostics,
    )
    assert len(calls) == 1, "기존 검수 한 번으로 판정해야 합니다"
    return checked.sections[0].sentences, diagnostics


@pytest.mark.parametrize("grade", ("확인", "해석"))
@pytest.mark.parametrize("grouped", (False, True))
def test_actual_attributed_plan_without_proof_is_removed(grade, grouped):
    kept, diagnostics = _verify(
        ACTUAL_PROSE, grade=grade, grouped=grouped, source=GOOD_SOURCE,
    )
    assert not kept, "회사 계획을 해석 등급이나 주어 생략으로 우회했습니다"
    assert any(row.get("reason_code") == FUTURE_EVIDENCE_MISSING for row in diagnostics)


@pytest.mark.parametrize("grade", ("확인", "해석"))
@pytest.mark.parametrize("grouped", (False, True))
def test_actual_attributed_plan_with_bound_proof_is_preserved(grade, grouped):
    kept, _ = _verify(
        ACTUAL_PROSE, grade=grade, grouped=grouped,
        source=GOOD_SOURCE, evidence=_evidence(),
    )
    assert [sentence.text for sentence in kept] == [ACTUAL_PROSE]


@pytest.mark.parametrize("grouped", (False, True))
def test_attribution_cannot_borrow_a_quote_absent_from_its_source(grouped):
    kept, diagnostics = _verify(ACTUAL_PROSE, grouped=grouped, evidence=_evidence())
    assert not kept
    assert any(row.get("reason_code") == "future_plan_quote_not_in_source"
               for row in diagnostics)


@pytest.mark.parametrize("attribution", (
    "회사의 거점 강화 전략의 일환이다",
    "당사의 거점 강화 전략의 일환입니다",
    "자사의 거점 강화 방침의 일환이다",
    "회사의 거점 강화 계획이다",
))
def test_explicit_possessive_attribution_requires_plan_evidence(attribution):
    text = f"대규모 복합 물류시설을 구축할 계획으로, 이는 {attribution}."
    assert future_plan_prose_problem(text, {FRAGMENT_ID: BACKGROUND_SOURCE}, None) == (
        FUTURE_EVIDENCE_MISSING
    )


@pytest.mark.parametrize("text", (
    "물류시설을 구축할 계획이다.",
    "글로벌 물류시장은 확대될 것으로 전망된다.",
    "회사의 비용 부담이 커질 것으로 전망된다.",
    "물류시설 구축은 회사의 전략의 일환일 수 있다.",
    "물류시설을 구축할 계획으로, 이는 협력회사의 전략의 일환이다.",
    "물류시설을 구축할 계획이지만 회사의 전략의 일환은 아니다.",
    "물류시설을 구축할 계획으로, 회사의 전략의 일환이라고 볼 수 있다.",
    "회사의 전략의 일환이다. 물류시설을 구축할 계획이다.",
    "회사의 물류시설 구축 계획을 이미 완료했다.",
))
def test_background_possibility_and_unattributed_plans_keep_existing_scope(text):
    assert future_plan_prose_problem(text, {FRAGMENT_ID: BACKGROUND_SOURCE}, None) == ""


@pytest.mark.parametrize("grouped", (False, True))
def test_industry_outlook_still_passes_the_existing_reviewer(grouped):
    text = "글로벌 물류시장은 확대될 것으로 전망된다."
    kept, _ = _verify(text, grouped=grouped, source=text)
    assert [sentence.text for sentence in kept] == [text]


@pytest.mark.parametrize("grouped", (False, True))
def test_other_sections_keep_their_existing_review_scope(grouped):
    kept, _ = _verify(ACTUAL_PROSE, grouped=grouped, section_id="business_model")
    assert [sentence.text for sentence in kept] == [ACTUAL_PROSE]
