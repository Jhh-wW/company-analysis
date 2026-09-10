"""성장 계획 표의 미래 근거를 «실제 진입점»과 «실제 파서»에서 검증한다.

여기서 보는 것은 세 가지다.
  ① 파서 — 검수 응답에서 번호별 근거를 꺼내는 실제 함수의 중복 번호·누락·형식 오류.
  ② 전달 경계 — 그 근거가 «그 번호·그 장»의 줄에만 닿는가.
  ③ 진입점 — legacy 도식 최종 게이트(``check_diagrams``)와 packet 묶음 검수
     (``verify_report(..., allowed_fragment_ids_by_section=...)``), 그리고 flat
     문장 경로에서 실제로 줄이 남거나 빠지는가.

★ 모든 시험은 주입한 ``ask`` 로만 응답을 만든다 — 새 AI 호출이 없다. 호출 횟수도
  함께 단정해 이 가드가 비용 계약을 바꾸지 않았음을 확인한다.
"""

from dataclasses import replace
import json

import pytest

from src.features.composer.constants import STRATEGY_TABLE_SECTION_ID
from src.features.composer.diagram_check import check_diagrams
from src.features.composer.future_plan_constants import (
    FUTURE_EVIDENCE_MISSING,
    FUTURE_KEY,
    FUTURE_PLAN_REVIEW_GUIDE,
    FUTURE_SOURCE_NOT_CITED,
    FUTURE_SOURCE_STATES_CURRENT,
)
from src.features.composer.future_plan_guard import future_plan_entries_by_number
from src.features.composer.port import (
    CollectedFragment,
    ComposedReport,
    ComposedSection,
    ComposedSentence,
    FlowRow,
)
from src.features.composer.verify import verify_report

# ══════════════════════════════════════════════════════════
# 실측 원문 (SM run d86b56f3… / WOORI run 8022de86…)
# ══════════════════════════════════════════════════════════
WOORI_SOURCE = (
    "신규사업 등의 내용 및 전망 우리은행은 고객만족을 최우선으로 하는 미래 "
    "성장동력을 발굴할 계획입니다. 특히, 세분화된 고객 세그먼트별 맞춤형 "
    "마케팅을 확대하고, 금융 취약층을 지원하는 상생금융을 적극 추진하여 금융의 "
    "사회적 책임을 다하고자 합니다."
)
SM_SOURCE = (
    "이러한 Buying Power를 바탕으로 K-Pop 글로벌 팬들이 공연을 관람하고 한국 "
    "문화를 향유하는 'K컬처 여행 상품'을 런칭하여 매출 증대가 이뤄지고 있습니다."
)

KEEP_ROW = FlowRow(
    (
        "고객 세그먼트별 맞춤형 마케팅 확대",
        "",
        "세분화된 고객 세그먼트별 맞춤형 마케팅을 확대하고 상생금융을 적극 추진",
    ),
    ("woori-8",),
)
REJECT_ROW = FlowRow(
    (
        "여행 상품 확대",
        "",
        "K-Pop 글로벌 팬 대상 여행 상품 런칭을 통한 IP 기반 수익화 채널 개척",
    ),
    ("sm-63",),
)
FRAGMENTS = (
    CollectedFragment("woori-8", "공시", WOORI_SOURCE),
    CollectedFragment("sm-63", "공시", SM_SOURCE),
)

KEEP_EVIDENCE = {
    "근거": "woori-8",
    "대상": "맞춤형 마케팅",
    "활동": "확대",
    "원문": "특히, 세분화된 고객 세그먼트별 맞춤형 마케팅을 확대하고, 금융 취약층을 "
    "지원하는 상생금융을 적극 추진하여 금융의 사회적 책임을 다하고자 합니다.",
    "양태": "계획",
}
REJECT_EVIDENCE = {
    "근거": "sm-63",
    "대상": "여행 상품",
    "활동": "런칭",
    "원문": "'K컬처 여행 상품'을 런칭하여 매출 증대가 이뤄지고 있습니다.",
    "양태": "계획",
}


def _draft(rows, section_id=STRATEGY_TABLE_SECTION_ID):
    return ComposedReport((ComposedSection(section_id, (), flow_rows=tuple(rows)),))


def _response(records):
    return json.dumps({"판정": records}, ensure_ascii=False)


def _verdict(number, evidence, section_id=STRATEGY_TABLE_SECTION_ID, citations=()):
    record = {"번호": number, "결과": "참"}
    if evidence is not None:
        record["검증근거"] = {FUTURE_KEY: [evidence]}
    if citations:
        record["장"] = section_id
        record["근거"] = list(citations)
    return record


def _run(rows, records, *, grouped, diagnostics=None, section_id=STRATEGY_TABLE_SECTION_ID):
    """실제 진입점을 그대로 태운다. 반환은 (남은 줄, 호출 프롬프트 목록)."""

    calls = []
    draft = _draft(rows, section_id)

    def ask(prompt):
        calls.append(prompt)
        return _response(records)

    if grouped:
        allowed = {section_id: frozenset(
            fid for row in draft.sections[0].flow_rows for fid in row.citations
        )}
        result = verify_report(
            draft, FRAGMENTS, None, ask,
            allowed_fragment_ids_by_section=allowed, diagnostics=diagnostics,
        )
    else:
        result, _problems = check_diagrams(draft, FRAGMENTS, ask, diagnostics=diagnostics)
    return result.sections[0].flow_rows, calls


# ══════════════════════════════════════════════════════════
# ① 파서 — 중복 번호 · 누락 · 형식 오류
# ══════════════════════════════════════════════════════════
def test_the_parser_extracts_only_per_number_evidence():
    raw = _response([
        _verdict(1, KEEP_EVIDENCE),
        _verdict(2, REJECT_EVIDENCE),
    ])
    entries = future_plan_entries_by_number(raw)
    assert set(entries) == {1, 2}
    assert entries[1][FUTURE_KEY] == [KEEP_EVIDENCE]
    assert entries[2][FUTURE_KEY] == [REJECT_EVIDENCE]


def test_the_parser_voids_a_number_that_appears_twice():
    """★ «마지막이 이긴다»면 좋은 근거를 뒤에 덧붙여 나쁜 줄을 통과시킬 수 있다."""

    raw = _response([
        _verdict(1, REJECT_EVIDENCE),
        _verdict(1, KEEP_EVIDENCE),
    ])
    assert future_plan_entries_by_number(raw)[1] is None


def test_a_duplicated_number_closes_the_row_as_missing_evidence():
    """파서의 무효(None)가 실제로 «제외»까지 이어지는지 진입점에서 확인한다."""

    records = [
        {"번호": 1, "결과": "참", "검증근거": {FUTURE_KEY: [KEEP_EVIDENCE]}},
        {"번호": 1, "결과": "참", "검증근거": {FUTURE_KEY: [KEEP_EVIDENCE]}},
    ]
    diagnostics = []
    kept, calls = _run([KEEP_ROW], records, grouped=False, diagnostics=diagnostics)
    assert kept == () and len(calls) == 1
    assert diagnostics[0]["reason_code"] == FUTURE_EVIDENCE_MISSING


@pytest.mark.parametrize(
    "raw",
    (
        None,
        "",
        "설명만 있고 JSON 이 없다",
        json.dumps({"판정": "배열이 아님"}, ensure_ascii=False),
        json.dumps({"다른키": []}, ensure_ascii=False),
        json.dumps([{"번호": 1}], ensure_ascii=False),
    ),
)
def test_the_parser_extracts_nothing_from_a_malformed_response(raw):
    assert future_plan_entries_by_number(raw) == {}


@pytest.mark.parametrize("number", (True, False, 1.0, None))
def test_the_parser_drops_non_integer_numbers(number):
    raw = _response([{"번호": number, "결과": "참",
                      "검증근거": {FUTURE_KEY: [KEEP_EVIDENCE]}}])
    assert future_plan_entries_by_number(raw) == {}


@pytest.mark.parametrize("number", ("1", " 1 "))
def test_the_parser_accepts_pure_digit_string_numbers(number):
    """"1"처럼 순수 숫자 문자열로 와도 정수로 보정해 받는다.

    이 함수는 ``support_entries_by_number``(direct_support.py)를 그대로
    위임 호출하므로(모듈 docstring 참고) 그 파서의 번호 보정 규칙을 그대로
    물려받는다 — True/False/1.0/None처럼 «진짜 비정수»만 여전히 버린다.
    """
    raw = _response([{"번호": number, "결과": "참",
                      "검증근거": {FUTURE_KEY: [KEEP_EVIDENCE]}}])
    assert set(future_plan_entries_by_number(raw)) == {1}


def test_a_verdict_entry_that_is_not_an_object_is_skipped():
    raw = _response(["문자열", _verdict(1, KEEP_EVIDENCE)])
    assert set(future_plan_entries_by_number(raw)) == {1}


# ══════════════════════════════════════════════════════════
# ② 전달 경계 — 근거는 «그 번호»의 줄에만 닿는다
# ══════════════════════════════════════════════════════════
def test_a_row_cannot_pass_using_another_rows_citation():
    """2번 줄(SM 런칭 완료)이 «자기 칸»으로 1번 줄의 인용을 대도 살아나지 않는다.

    ★ 근거 id 는 «그 줄이 인용한» 조각이라야 한다. 다른 줄의 인용을 빌려 오면
      한 응답 안에서 근거를 옮겨 붙이는 우회가 된다.
    """

    borrowed = dict(REJECT_EVIDENCE, 근거="woori-8")
    records = [
        _verdict(1, KEEP_EVIDENCE),
        _verdict(2, borrowed),  # ← 2번 줄이 인용하지 않은 근거다
    ]
    diagnostics = []
    kept, calls = _run(
        [KEEP_ROW, REJECT_ROW], records, grouped=False, diagnostics=diagnostics
    )
    assert kept == (KEEP_ROW,) and len(calls) == 1
    assert [item["reason_code"] for item in diagnostics] == [FUTURE_SOURCE_NOT_CITED]


def test_measured_two_rows_receive_their_own_verdicts():
    records = [_verdict(1, KEEP_EVIDENCE), _verdict(2, REJECT_EVIDENCE)]
    diagnostics = []
    kept, calls = _run(
        [KEEP_ROW, REJECT_ROW], records, grouped=False, diagnostics=diagnostics
    )
    assert kept == (KEEP_ROW,) and len(calls) == 1
    assert [item["reason_code"] for item in diagnostics] == [FUTURE_SOURCE_STATES_CURRENT]


# ══════════════════════════════════════════════════════════
# ③ 진입점 — legacy 도식 · packet 묶음
# ══════════════════════════════════════════════════════════
@pytest.mark.parametrize("grouped", (False, True), ids=("legacy", "grouped"))
def test_measured_current_or_completed_rows_drop_at_both_entry_points(grouped):
    records = [_verdict(1, REJECT_EVIDENCE, citations=("sm-63",))]
    diagnostics = []
    kept, calls = _run([REJECT_ROW], records, grouped=grouped, diagnostics=diagnostics)
    assert kept == () and len(calls) == 1
    assert diagnostics[0]["reason_code"] == FUTURE_SOURCE_STATES_CURRENT
    assert diagnostics[0]["verification_items"] == ("미래 계획 근거",)


@pytest.mark.parametrize("grouped", (False, True), ids=("legacy", "grouped"))
def test_measured_real_plan_rows_survive_at_both_entry_points(grouped):
    records = [_verdict(1, KEEP_EVIDENCE, citations=("woori-8",))]
    diagnostics = []
    kept, calls = _run([KEEP_ROW], records, grouped=grouped, diagnostics=diagnostics)
    assert kept == (KEEP_ROW,) and len(calls) == 1
    assert diagnostics == []


@pytest.mark.parametrize("grouped", (False, True), ids=("legacy", "grouped"))
def test_a_row_with_no_evidence_at_all_drops_as_a_binding_failure(grouped):
    records = [_verdict(1, None, citations=("woori-8",))]
    diagnostics = []
    kept, calls = _run([KEEP_ROW], records, grouped=grouped, diagnostics=diagnostics)
    assert kept == () and len(calls) == 1
    assert diagnostics[0]["reason_code"] == FUTURE_EVIDENCE_MISSING


# ══════════════════════════════════════════════════════════
# ④ 범위 — 다른 장·산문은 이 검사를 지나가지 않는다
# ══════════════════════════════════════════════════════════
@pytest.mark.parametrize("grouped", (False, True), ids=("legacy", "grouped"))
def test_diagrams_in_other_sections_are_not_asked_for_future_evidence(grouped):
    """다른 장의 현재 사실에는 성장 계획 표의 미래 근거를 요구하지 않는다."""

    row = replace(
        REJECT_ROW,
        cells=("Buying Power", "K컬처 여행 상품 런칭", "K-Pop 글로벌 팬"),
    )
    records = [_verdict(1, None, section_id="operations_partners",
                        citations=("sm-63",))]
    kept, calls = _run(
        [row], records, grouped=grouped, section_id="operations_partners"
    )
    assert kept == (row,) and len(calls) == 1


def test_prose_in_the_same_section_is_not_asked_for_future_evidence():
    """flat 문장 경로에는 칸이 없다 — «미래 근거» 요구 대상이 아니다.

    ⚠️ 다만 6장 장 계약이 생긴 뒤로는 앞으로의 이야기가 없는 이 문장이 그 계약에
      걸려 공개에서 빠진다. 이 시험은 «미래 근거를 묻지 않는다»만 확인한다.
    """

    sentence = ComposedSentence(
        text="K컬처 여행 상품을 런칭하여 매출 증대가 이뤄지고 있습니다.",
        grade="확인",
        citations=("sm-63",),
    )
    draft = ComposedReport(
        (ComposedSection(STRATEGY_TABLE_SECTION_ID, (sentence,)),)
    )
    calls = []

    def ask(prompt):
        calls.append(prompt)
        return _response([{"번호": 1, "결과": "참"}])

    result = verify_report(draft, FRAGMENTS, None, ask)
    kept = result.sections[0].sentences
    # 검수는 «1회» 그대로이고 미래 근거를 묻지도 않는다 — 이 시험의 원래 요점이다.
    assert len(calls) == 1
    # ⚠️ 계약 변경(6장 장 계약): 앞으로의 이야기가 없는 이 문장은 이제 그 계약에
    #   걸려 공개에서 빠진다. 미래 근거 결속이 뺀 것이 «아니다».
    from src.features.composer.future_plan_guard import future_section_prose_problem
    from src.features.composer.future_plan_constants import (
        FUTURE_SECTION_NO_FORWARD_STATEMENT,
    )
    assert future_section_prose_problem(sentence.text) == (
        FUTURE_SECTION_NO_FORWARD_STATEMENT
    )
    assert len(kept) == 0


# ══════════════════════════════════════════════════════════
# ⑤ 프롬프트 — 파서가 요구하는 계약이 실제로 실린다
# ══════════════════════════════════════════════════════════
@pytest.mark.parametrize("grouped", (False, True), ids=("legacy", "grouped"))
def test_the_review_prompt_carries_the_future_evidence_contract(grouped):
    records = [_verdict(1, KEEP_EVIDENCE, citations=("woori-8",))]
    _kept, calls = _run([KEEP_ROW], records, grouped=grouped)
    assert FUTURE_PLAN_REVIEW_GUIDE in calls[0]
    assert FUTURE_KEY in calls[0]
