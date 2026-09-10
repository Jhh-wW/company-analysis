"""과제만 확인된 행을 회사의 대응까지 확인한 표로 공개하지 않는다."""

import json

import pytest

from src.features.composer.diagram_check import check_diagrams
from src.features.composer.port import (
    CollectedFragment, ComposedReport, ComposedSection, FlowRow,
)
from src.features.composer.verify import verify_report


# SM 실측 인용 177: 과제와 실적 영향은 있지만 회사가 밝힌 대응은 없다.
ACTUAL_SOURCE = (
    "광고대행사업을 영위하는 종속회사 ㈜에스엠컬처앤콘텐츠의 2025년 연간 실적은 "
    "소비 심리 위축 및 기업 투자 축소에 따른 광고 업황 침체와 광고사업 경쟁 심화 등의 "
    "영향으로 전기 대비 매출이 17.3% 감소하였습니다."
)
CHALLENGE = "광고 사업부문의 업황 침체와 경쟁 심화"
MISSING_REASON = "challenge_response_missing"


def run_review(response, grouped, *, section_id="current_challenges", source=ACTUAL_SOURCE):
    row = FlowRow((CHALLENGE, response), ("177",))
    draft = ComposedReport((ComposedSection(section_id, (), flow_rows=(row,)),))
    fragments = (CollectedFragment("177", "공시", source),)
    diagnostics = []
    calls = []

    def ask(prompt):
        calls.append(prompt)
        return json.dumps({"판정": [{
            "번호": 1, "결과": "참", "장": section_id, "근거": ["177"],
        }]}, ensure_ascii=False)

    if grouped:
        result = verify_report(
            draft, fragments, None, ask,
            allowed_fragment_ids_by_section={section_id: frozenset({"177"})},
            diagnostics=diagnostics,
        )
    else:
        result, _ = check_diagrams(draft, fragments, ask, diagnostics=diagnostics)
    return result.sections[0].flow_rows, row, diagnostics, calls


@pytest.mark.parametrize("grouped", (False, True), ids=("flat", "grouped"))
@pytest.mark.parametrize("response", (
    "해당 사항 없음", "없음", "미상", "미확인", "미정", "확인되지 않음", "공시 없음", "Ｎ／Ａ", "-", ".", "",
))
def test_unconfirmed_responses_are_removed_even_if_reviewer_approves(response, grouped):
    rows, _, diagnostics, calls = run_review(response, grouped)
    assert rows == ()
    assert len(calls) == 1
    assert any(item.get("reason_code") == MISSING_REASON for item in diagnostics)


@pytest.mark.parametrize("grouped", (False, True), ids=("flat", "grouped"))
@pytest.mark.parametrize("response", (
    "해외 광고주를 대상으로 신규 영업을 추진한다",
    "추가 차입 없이 기존 자금으로 영업 인력을 확충한다",
    "광고 입찰에는 참여하지 않기로 결정했다",
    "미정인 대응책을 구체화하는 실무팀을 구성한다",
))
def test_disclosed_responses_and_explicit_inaction_are_preserved(response, grouped):
    # 정상 짝은 과제와 대응을 함께 밝힌 별도 원문이다. 실측 과제 원문에
    # 날짜 없는 과거 대응을 임의로 붙여 기존 시점 검사를 끄지 않는다.
    source = CHALLENGE + "; " + response + "."
    rows, row, diagnostics, calls = run_review(response, grouped, source=source)
    assert rows == (row,)
    assert len(calls) == 1
    assert not any(item.get("reason_code") == MISSING_REASON for item in diagnostics)


@pytest.mark.parametrize("grouped", (False, True), ids=("flat", "grouped"))
def test_optional_blank_fields_in_other_sections_keep_their_contract(grouped):
    rows, row, diagnostics, _ = run_review("", grouped, section_id="business_model")
    assert rows == (row,)
    assert not any(item.get("reason_code") == MISSING_REASON for item in diagnostics)
