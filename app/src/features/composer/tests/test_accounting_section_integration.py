"""같은 회계 문장이 실제 소유 장에 따라 제한되고 조직 절차는 남는지 확인한다."""
from hashlib import sha256
import json
import re

import pytest

from src.features.composer.port import CollectedFragment, ComposedReport, ComposedSection, ComposedSentence
from src.features.composer.review_outcomes import final_review_outcomes
from src.features.composer.tests.review_evidence_fixture import review_items
from src.features.composer.verify import verify_report, verify_sentences


ACCOUNTING_TEXT = (
    "매출채권 및 계약자산에 대하여 전체기간 기대신용손실을 손실충당금으로 "
    "인식하는 간편법을 적용하며, 신용위험 특성과 연체일을 기준으로 "
    "구분하여 기대신용손실률을 산출한다."
)
PROCEDURE_TEXT = "재무 담당부서가 위험관리 정책을 수립하고 이사회가 감독한다."


def _approval(calls):
    def ask(prompt):
        calls.append(prompt)
        items = review_items(re.sub(r"(?m)^  등급: [^\n]+\n", "", prompt))
        assert items, "실제 검수 입력에 후보가 없습니다"
        return json.dumps({"판정": [
            {"번호": item.number, "장": item.section, "근거": list(item.citations), "결과": "참"}
            for item in items
        ]}, ensure_ascii=False)
    return ask


def _inputs(section):
    sentences = (
        ComposedSentence(ACCOUNTING_TEXT, ("accounting",), "확인", planned_claim_slot="culture:decision_process"),
        ComposedSentence(PROCEDURE_TEXT, ("procedure",), "확인"),
    )
    fragments = (
        CollectedFragment("accounting", "회계정책", ACCOUNTING_TEXT),
        CollectedFragment("procedure", "공식 운영절차", PROCEDURE_TEXT),
    )
    return ComposedReport((ComposedSection(section, sentences),)), fragments


@pytest.mark.parametrize("grouped", (False, True))
@pytest.mark.parametrize("section", ("culture", "operations_partners"))
def test_accounting_policy_is_limited_by_actual_owner_with_one_review(section, grouped):
    report, fragments = _inputs(section)
    calls, diagnostics = [], []
    checked = verify_report(
        report, fragments, None, _approval(calls), diagnostics=diagnostics,
        allowed_fragment_ids_by_section={section: frozenset(("accounting", "procedure"))} if grouped else None,
    )
    expected = [PROCEDURE_TEXT] if section == "culture" else [ACCOUNTING_TEXT, PROCEDURE_TEXT]
    assert [sentence.text for sentence in checked.sections[0].sentences] == expected
    assert len(calls) == 1
    outcomes = final_review_outcomes(checked, diagnostics)
    if section == "culture":
        assert len(outcomes) == 1
        assert outcomes[0]["reason_code"] == "culture_accounting_policy_misplaced"
        assert outcomes[0]["candidate_sha256"] == sha256(ACCOUNTING_TEXT.encode()).hexdigest()
        assert outcomes[0]["verification_items"] == ("장별 작성범위",)
        assert ACCOUNTING_TEXT not in repr(outcomes)
    else:
        assert outcomes == ()


def test_summary_is_not_classified_as_culture_from_old_planned_slot():
    report, fragments = _inputs("culture")
    calls = []
    checked = verify_sentences(report.sections[0].sentences, fragments, None, _approval(calls))
    assert [sentence.text for sentence in checked] == [ACCOUNTING_TEXT, PROCEDURE_TEXT]
    assert len(calls) == 1
