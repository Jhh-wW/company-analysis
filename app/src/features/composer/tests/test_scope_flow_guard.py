"""도식 대상명과 인접 자격 칸의 결속을 실제 legacy 진입점에서 검증한다."""
import json

import pytest

from src.features.composer.diagram_check import check_diagrams
from src.features.composer.port import CollectedFragment, ComposedReport, ComposedSection, FlowRow
from src.features.composer.scope_guard import flow_scope_problem, scope_problem
from src.features.composer.scope_constants import SCOPE_CONDITION_UNBOUND

SOURCE = "한빛상품 | 신용등급 QX+ 이상 기업을 대상으로 하는 대출상품"
BAD_CELLS = ("기업금융 채널", "신용등급 QX+ 이상 기업 대상", "대출 제공")


def _legacy(cells, sources, verdict="참", diagnostics=None):
    calls = []
    row = FlowRow(tuple(cells), tuple(sources))
    draft = ComposedReport((ComposedSection("operations_partners", (), flow_rows=(row,)),))
    def ask(prompt):
        calls.append(prompt)
        return json.dumps({"판정": [{"번호": 1, "결과": verdict}]}, ensure_ascii=False)
    result, problems = check_diagrams(
        draft, tuple(CollectedFragment(key, "공시", value) for key, value in sources.items()),
        ask, diagnostics=diagnostics,
    )
    return result.sections[0].flow_rows, problems, calls


@pytest.mark.parametrize("condition,source", (
    ("신용등급 QX+ 이상 기업 대상", SOURCE),
    ("직원 10명 이상 기업 대상", "한빛상품 | 직원 10명 이상 기업을 지원하는 상품"),
    ("수출기업 전용", "한빛상품 | 수출기업 전용 상품"),
))
def test_whole_channel_cannot_borrow_product_eligibility_across_cells(condition, source):
    cells = ("기업금융 채널", condition, "대출 제공")
    diagnostics = []
    kept, problems, calls = _legacy(cells, {"1": source}, diagnostics=diagnostics)
    assert kept == () and len(calls) == 1
    assert any(SCOPE_CONDITION_UNBOUND in problem for problem in problems)
    assert diagnostics[0]["reason_code"] == SCOPE_CONDITION_UNBOUND
    assert diagnostics[0]["verification_items"] == ("조건·적용대상",)


@pytest.mark.parametrize("cells", (
    ("한빛상품", "신용등급 QX+ 이상 기업 대상", "대출 제공"),
    ("기업금융 채널", "한빛상품", "신용등급 QX+ 이상 기업 대상"),
    ("기업금융 채널", "한빛상품의 신용등급 QX+ 이상 기업 대상", "대출 제공"),
))
def test_specific_product_and_product_example_keep_their_split_cells(cells):
    kept, problems, calls = _legacy(cells, {"1": SOURCE})
    assert len(kept) == 1 and not problems and len(calls) == 1


@pytest.mark.parametrize("direct", (
    "기업금융 채널은 신용등급 QX+ 이상 기업을 대상으로 대출상품을 제공한다.",
    "기업금융 채널의 모든 상품 공통 조건은 신용등급 QX+ 이상 기업 대상이다.",
    "기업금융 채널 공통조건: 신용등급 QX+ 이상 기업 대상",
))
def test_direct_official_whole_channel_condition_is_kept(direct):
    kept, problems, calls = _legacy(BAD_CELLS, {"1": SOURCE, "2": direct})
    assert len(kept) == 1 and not problems and len(calls) == 1


def test_other_product_in_same_source_cannot_lend_its_condition():
    sources = {"1": SOURCE + "\n나래상품 | 신용등급 ZX- 이상 기업을 대상으로 하는 상품"}
    good = ("나래상품", "신용등급 ZX- 이상 기업 대상", "대출 제공")
    bad = ("나래상품", "신용등급 QX+ 이상 기업 대상", "대출 제공")
    assert len(_legacy(good, sources)[0]) == 1
    assert _legacy(bad, sources)[0] == ()


@pytest.mark.parametrize("cells", (
    ("기업금융 채널 소개 문장이다.", "신용등급 QX+ 이상 기업 대상", "대출 제공"),
    ("기업금융 채널", "별도의 운영 설명", "신용등급 QX+ 이상 기업 대상"),
    ("기업금융 채널", "직원 교육은 신용등급 QX+ 이상 기업 대상", "교육 자료 제공"),
    ("기업금융 채널", "운영 설명. 신용등급 QX+ 이상 기업 대상", "교육 자료 제공"),
    ("기업금융 채널", "", "신용등급 QX+ 이상 기업 대상"),
))
def test_unrelated_cells_and_separate_sentences_do_not_get_a_borrowed_subject(cells):
    kept, problems, calls = _legacy(cells, {"1": SOURCE})
    assert len(kept) == 1 and not problems and len(calls) == 1


def test_unseen_rating_stays_with_existing_semantic_reviewer():
    cells = ("기업금융 채널", "신용등급 ZX- 이상 기업 대상", "대출 제공")
    assert flow_scope_problem(cells, {"1": SOURCE}) == ""
    kept, problems, calls = _legacy(cells, {"1": SOURCE}, verdict="거짓")
    assert kept == () and len(calls) == 1
    assert all(SCOPE_CONDITION_UNBOUND not in problem for problem in problems)


def test_invented_number_still_stops_before_review():
    cells = ("기업금융 채널", "직원 30명 이상 기업 대상", "대출 제공")
    sources = {"1": "한빛상품 | 직원 10명 이상 기업 대상 상품"}
    assert flow_scope_problem(cells, sources) == ""
    kept, problems, calls = _legacy(cells, sources)
    assert kept == () and not calls and problems


def test_existing_numeric_json_failure_keeps_its_reason_before_flow_check():
    cells = ("기업금융 채널", "매출 10억원 이상 기업 대상", "대출 제공")
    sources = {"1": "한빛상품 | 매출 10억원 이상 기업을 지원하는 상품"}
    diagnostics = []
    kept, problems, calls = _legacy(cells, sources, diagnostics=diagnostics)
    assert kept == () and len(calls) == 1 and problems
    assert diagnostics[0]["reason_code"] == "semantic_grounding_missing"


def test_false_verdict_keeps_original_disposal_without_scope_reclassification():
    diagnostics = []
    kept, problems, calls = _legacy(BAD_CELLS, {"1": SOURCE}, verdict="거짓", diagnostics=diagnostics)
    assert kept == () and len(calls) == 1
    assert not diagnostics
    assert all(SCOPE_CONDITION_UNBOUND not in problem for problem in problems)


def test_existing_sentence_clause_boundary_is_not_weakened():
    text = "기업금융 채널 ; 신용등급 QX+ 이상 기업 대상 ; 대출 제공"
    assert scope_problem(text, {"1": SOURCE}) == ""
    assert flow_scope_problem(BAD_CELLS, {"1": SOURCE}) == SCOPE_CONDITION_UNBOUND


def test_unrelated_official_scope_clause_cannot_approve_product_condition():
    sources = {"1": SOURCE, "2": "기업금융 채널은 여러 상품을 제공한다. 한빛상품은 신용등급 QX+ 이상 기업 대상이다."}
    kept, _problems, calls = _legacy(BAD_CELLS, sources)
    assert kept == () and len(calls) == 1
