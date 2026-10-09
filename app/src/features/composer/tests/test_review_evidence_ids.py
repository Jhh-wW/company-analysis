"""표시 ID 복원은 문장 소유권과 실제 근거 검사를 우회하지 않는다."""

from copy import deepcopy
import json

import pytest

from src.features.composer import verify
from src.features.composer.grounding_constants import REVIEW_GROUNDING_REJECTED
from src.features.composer.port import CollectedFragment, ComposedSentence
from src.features.composer.review_evidence_ids import (
    ReviewEvidenceContext, normalize_review_binding_text, normalize_review_entry,
)


def _context(*, registry=("1", "2"), proof=("1",)):
    return ReviewEvidenceContext(frozenset(registry), {1: frozenset({"1"})}, {1: frozenset(proof)})


def _row(*, ids=None, proof_id="조각 1"):
    return {
        "번호": "1", "장": "identity", "근거": ids or ["조각 1"], "결과": "참",
        "근거대조": "조각 1을 그대로 인용했다.",
        "검증근거": {"관계": [{"근거": proof_id, "유형": "역할", "원문": "조각 1이라는 글"}]},
    }


def test_표시만_복원하고_원응답과_원문은_보존한다():
    row = _row()
    original = deepcopy(row)
    derived, valid = normalize_review_entry(row, _context())
    assert valid
    assert row == original
    assert derived["근거"] == ["1"]
    assert derived["검증근거"]["관계"][0]["근거"] == "1"
    assert derived["검증근거"]["관계"][0]["원문"] == "조각 1이라는 글"
    assert derived["근거대조"] == original["근거대조"]
    assert derived["번호"] == "1" and derived["결과"] == "참"


@pytest.mark.parametrize("ids", [["조각 2"], ["1", "조각 1"], ["조각 01"], ["근거 1"], ["조각 １"]])
def test_외부_중복_추측변환을_거절한다(ids):
    assert not normalize_review_entry(_row(ids=ids), _context())[1]


def test_남의_실제ID와_충돌하는_표시이름은_변환하지_않는다():
    derived, valid = normalize_review_entry(_row(), _context(registry=("1", "조각 1")))
    assert not valid and derived["근거"] == ["조각 1"]


@pytest.mark.parametrize("key", ["수치", "시점", "관계", "미래근거", "인식기준", "추세"])
def test_모든_닫힌_증명칸의_외부ID를_거절한다(key):
    row = _row(ids=["1"])
    proof = {"근거": "조각 2", "원문": "관계없는 원문"}
    row["검증근거"] = {key: [{"관측": [proof]}] if key == "추세" else [proof]}
    assert not normalize_review_entry(row, _context())[1]
    raw = json.dumps({"판정": [row]}, ensure_ascii=False)
    verdicts = verify._parse_grouped_verdicts(raw, {1: "identity"}, {1: frozenset({"1"})}, evidence_context=_context())
    assert verdicts == {1: "참"}  # 의미 실패 때문에 형식 재요청을 추가하지 않는다.
    checked = verify._apply_grounding(raw, verdicts, {1: ("회사는 가구를 생산한다.", {"1": "회사는 가구를 생산한다."})}, review_evidence_context=_context())
    assert checked[1] == REVIEW_GROUNDING_REJECTED


def test_보조실적표는_실제로_소유한_후보의_중첩증명에만_허용한다():
    row = _row(proof_id="실적표")
    assert normalize_review_entry(row, _context(registry=("1", "실적표"), proof=("1", "실적표")))[1]
    assert not normalize_review_entry(row, _context(registry=("1", "실적표")))[1]
    assert not normalize_review_entry(_row(ids=["실적표"]), _context(proof=("1", "실적표")))[1]
    assert not normalize_review_entry(_row(proof_id="조각 실적표"), _context(proof=("1", "실적표")))[1]


def _item(number):
    return verify._GroupedReviewItem(
        number, "identity", verify.REVIEW_KIND_SENTENCE, (str(number),),
        sentence=ComposedSentence("회사는 가구를 생산한다.", (str(number),), "확인"),
    )


def _answer(*numbers, bad_proof=False):
    return json.dumps({"판정": [{
        "번호": n, "장": "identity", "근거": [f"조각 {n}"], "결과": "참",
        "검증근거": {"관계": [{"근거": "외부"}]} if bad_proof else {
            "관계": [{"근거": f"조각 {n}", "유형": "역할", "대상": "가구",
                    "역할값": "생산", "원문": "회사는 가구를 생산한다."}],
        },
    } for n in numbers]}, ensure_ascii=False)


@pytest.mark.parametrize("answers", [[_answer(1, 2)], ["응답 오류", _answer(1, 2)], [_answer(1), _answer(2)]])
def test_최초_재요청_누락후속에서_같은_ID로_근거를_검사한다(answers, monkeypatch):
    pending = list(answers)
    calls = []
    def ask(prompt):
        calls.append(prompt)
        return pending.pop(0)
    seen = []
    original = verify.constrain_verdicts
    def constrain(raw, *args, **kwargs):
        seen.append(json.loads(raw))
        return original(raw, *args, **kwargs)
    monkeypatch.setattr(verify, "constrain_verdicts", constrain)
    result = verify._ask_grouped_verdicts(
        ask, [_item(1), _item(2)],
        {str(n): CollectedFragment(str(n), "공시", "회사는 가구를 생산한다.") for n in (1, 2)}, None,
    )
    assert result == {1: "참", 2: "참"}
    assert len(calls) == len(answers)
    assert [row["근거"] for row in seen[0]["판정"]] == [["1"], ["2"]]


def test_행구제_이후에도_같은_근거ID로_결속한다():
    raw = _answer(1)[:-2] + ', {"번호": 2, 깨진행}]}'
    context = _context()
    verdicts = verify._parse_grouped_verdicts(raw, {1: "identity"}, {1: frozenset({"1"})}, evidence_context=context)
    assert verdicts == {1: "참"}
    derived, invalid = normalize_review_binding_text(verify._review_binding_text(raw), context)
    assert not invalid
    assert json.loads(derived)["판정"][0]["근거"] == ["1"]


def test_번호표기_복원으로_거짓수치가_통과하지_않는다():
    row = _row(ids=["조각 1"])
    row["검증근거"] = {"수치": [{"근거": "조각 1", "원문": "매출액 10억원", "표현": "99억원", "항목": "매출액", "원문항목": "매출액", "원문값": "10억원"}]}
    raw = json.dumps({"판정": [row]}, ensure_ascii=False)
    context = _context()
    verdicts = verify._parse_grouped_verdicts(raw, {1: "identity"}, {1: frozenset({"1"})}, evidence_context=context)
    result = verify._apply_grounding(raw, verdicts, {1: ("매출액은 99억원이다.", {"1": "매출액 10억원"})}, review_evidence_context=context)
    assert result[1] == REVIEW_GROUNDING_REJECTED


def test_쓰이지_않는_외부증명도_근거검사에서_거절한다():
    row = _row(ids=["1"])
    row["검증근거"] = {"미래근거": [{"근거": "외부"}]}
    raw = json.dumps({"판정": [row]}, ensure_ascii=False)
    text = "회사의 상호는 예시법인이다."
    diagnostics = []
    result = verify._apply_grounding(raw, {1: "참"}, {1: (text, {"1": text})}, review_evidence_context=_context(),
                                     diagnostics=diagnostics,
                                     diagnostic_contexts={1: ("identity", "본문", text)})
    assert result[1] == REVIEW_GROUNDING_REJECTED
    from src.shared.report_quality.review_diagnostics import observed_review_outcomes
    detail = observed_review_outcomes(diagnostics)[0]["grounding_detail"]
    assert detail["check_kind"] == "근거"
    assert detail["stage"] == "review_evidence_id_binding"


def test_쓰이지_않는_외부증명을_붙여도_장_이동으로_빠져나가지_못한다():
    text = "회사는 원료 공급 지연에 대응하여 조달처를 다변화하고 있다."
    context = ReviewEvidenceContext(frozenset({"1"}), {1: frozenset({"1"})}, {1: frozenset({"1"})})
    moves_by_case = []
    for foreign in (False, True):
        row = {"번호": 1, "장": "future_strategy", "근거": ["1"], "결과": "참", "검증근거": {}}
        if foreign:
            row["검증근거"] = {"미래근거": [{"근거": "외부"}]}
        moves = []
        checked = verify._apply_grounding(
            json.dumps({"판정": [row]}, ensure_ascii=False), {1: "참"}, {1: (text, {"1": text})},
            diagnostic_contexts={1: ("future_strategy", "본문", text)},
            evidence_ids_by_number={1: frozenset({"1"})},
            allowed_fragment_ids_by_section={"current_challenges": frozenset({"1"})},
            section_moves=moves, review_evidence_context=context,
        )
        moves_by_case.append(moves)
        if foreign:
            assert checked[1] == REVIEW_GROUNDING_REJECTED
    assert len(moves_by_case[0]) == 1 and not moves_by_case[0][0].blocker
    from src.shared.report_quality.composition_diagnostic_constants import SECTION_MOVE_BLOCKED_SOURCE_BINDING
    assert len(moves_by_case[1]) == 1 and moves_by_case[1][0].blocker == SECTION_MOVE_BLOCKED_SOURCE_BINDING
