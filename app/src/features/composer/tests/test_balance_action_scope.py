"""잔액·누적 표에서 행동·인식 기준을 확대하는 공통 경계를 검증한다."""
import json
import pytest

from src.features.composer.business_relation_scope import business_relation_scope_problem
from src.features.composer.verify import _apply_grounding
from src.features.composer.verify import verify_report
from src.features.composer.diagram_check import check_diagrams
from src.features.composer.port import CollectedFragment, ComposedReport, ComposedSection, FlowRow

BALANCE = "구분 | 도급금액 | 누적공사수익 | 공사미수금; 장비공사 | 300 | 200 | 100"

@pytest.mark.parametrize("claim", (
    "공사미수금 회수", "도급금액 기준 진행율 인식", "회사는 공사미수금을 회수한다.",
))
def test_잔액은_회수행위나_인식기준의_증거가_아니다(claim):
    assert business_relation_scope_problem(claim, {"a": BALANCE}, section_id="business_model")

@pytest.mark.parametrize("claim, source", (
    ("회사는 공사미수금을 회수했다.", "회사는 공사미수금을 회수했다."),
    ("공사미수금 회수", "회사는 공사미수금 회수를 완료했다."),
    ("회사는 매출채권을 회수할 계획이다.", "회사는 매출채권을 회수할 계획이다."),
    ("은행은 대출채권을 회수하고 있다.", "은행은 대출채권을 회수하고 있다."),
    ("회사는 매출채권을 회수한다.", "회사는 매출채권 중 연체분을 회수한다."),
    ("총추정원가 대비 누적원가 비율의 진행율로 수익을 인식한다.",
     "총 추정원가에 대한 실제 발생한 누적원가의 비율에 의해 계산된 진행율을 신뢰성 있게 측정할 수 있을 때 수익을 인식한다."),
    ("도급금액 기준 진행률 인식", "회사는 도급금액 기준 진행률에 따라 수익을 인식한다."),
    ("총추정원가 대비 누적원가 비율의 진행률로 수익을 인식한다.",
     "진행률은 총 추정원가 대비 누적원가의 비율로 산정하며 이에 따라 수익을 인식한다."),
    ("공사미수금 잔액은 100이다.", BALANCE),
    ("고객은 매출채권을 회수한다.", "고객은 매출채권을 회수한다."),
))
def test_실제회수_계획_본업과_정확인식기준_잔액설명을_보존한다(claim, source):
    assert not business_relation_scope_problem(claim, {"a": source}, section_id="business_model")

@pytest.mark.parametrize("claim, source", (
    ("공사미수금 회수", "공사미수금은 10이다. 매출채권은 회수했다."),
    ("회사는 공사미수금을 회수했다.", "회사는 공사미수금을 회수할 계획이다."),
    ("도급금액 기준 진행율 인식", "총추정원가 대비 누적원가 비율의 진행율로 수익을 인식한다."),
    ("매출채권 회수", "회사는 업무를 지원하며 고객은 매출채권을 회수한다."),
    ("회사는 매출채권을 회수한다.", "회사는 매출채권 중 연체분을 회수할 계획이다."),
))
def test_다른대상_계획_다른산식을_빌리지않는다(claim, source):
    assert business_relation_scope_problem(claim, {"a": source}, section_id="business_model")

@pytest.mark.parametrize("flow", (False, True))
def test_모델참을_산문과_묶음도식_공통입구에서_거절한다(flow):
    claim = "도급금액 기준 진행율 인식, 공사미수금 회수"
    raw = json.dumps({"판정": [{"번호": 1, "결과": "참", "근거": ["a"]}]}, ensure_ascii=False)
    problems = {}
    result = _apply_grounding(raw, {1: "참"}, {1: (claim, {"a": BALANCE})},
        diagnostic_contexts={1: ("business_model", "도식" if flow else "본문", "확인")},
        flow_cells_by_number={1: ("공사 수행", "장비공사", claim)} if flow else None,
        grounding_problems=problems)
    assert result[1] != "참" and problems[1] == "scope_condition_unbound"

@pytest.mark.parametrize("path", ("grouped", "legacy"))
@pytest.mark.parametrize("good", (False, True))
def test_미인용조각을빌리지않고_실제도식검수후에만봉인한다(path, good):
    text = BALANCE + (" 회사는 공사미수금을 회수한다." if good else "")
    row = FlowRow(("공사 수행", "장비공사", "공사미수금 회수"), ("1",))
    original = ComposedReport((ComposedSection("business_model", (), flow_rows=(row,)),))
    source = CollectedFragment("1", "사업내용", text)
    uncited = CollectedFragment("2", "사업내용", "회사는 공사미수금을 회수한다.")
    def reviewer(_):
        return json.dumps({"판정": [{"번호": 1, "장": "business_model", "근거": ["1"],
            "결과": "참", "검증근거": {}}]}, ensure_ascii=False)
    if path == "legacy":
        checked, _ = check_diagrams(original, (source, uncited), reviewer)
    else:
        checked = verify_report(original, (source, uncited), None, reviewer,
            allowed_fragment_ids_by_section={"business_model": frozenset({"1", "2"})})
    assert len(checked.sections[0].flow_rows) == int(good)
    if good:
        assert checked.sections[0].flow_rows[0].review_binding is not None
    assert original.sections[0].flow_rows == (row,) and source.text == text
