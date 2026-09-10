"""도식은 칸으로 관계를 나눈다 — 그 구조에서만 분할된 인과를 결속한다.

★ 왜 필요했나 (실제 재현) — 도식 후보는 칸을 ` ; `로 이어 붙인 «한 문자열»로
  가드에 들어가는데, 인과 역할 틀은 절 경계(`;`·`.`)를 넘지 않는다. 그래서 원문
  한 문장이 온전히 지지하는 정상 도식 줄이 `causal_relation_claim_not_covered`
  로 삭제됐다. 재현 기록은 .local-artifacts/resume-20260910-split-flow-causal-fix
  의 01-repro-before-FAILED.log 에 있다.

★ 행을 이렇게 만든 이유 — 처음 메모의 3칸 예시는 세 번째 칸(「누구에게 닿나」)까지
  원문이 지지한다고 증명한 적이 없었다. 그래서 «원문 한 문장이 온전히 지지하는
  두 칸 + 빈 나머지»로 만든다. 빈 칸은 `labelled_flow_cells`가 프롬프트에서 빼므로
  그 줄은 세 번째 헤더에 대해 아무것도 주장하지 않는다.

★ 무엇을 «넓히지 않았나» — prose 는 그대로다. 같은 세미콜론 문자열이라도 칸 구조
  없이 오면 여전히 미증명이다. 방향·부정·차용 인용·두 번째 미증명 인과도 그대로 막는다.
"""

import json

import pytest

from src.features.composer.diagram_check import check_diagrams
from src.features.composer.direct_support import causal_relation_problem
from src.features.composer.direct_support_constants import (
    CAUSE_CLAIM_UNCOVERED, CAUSE_DIRECTION_REVERSED, CAUSE_NEGATED_IN_SOURCE,
    CAUSE_RELATION_NOT_IN_SOURCE, FLOW_CELL_JOIN, RELATION_REVIEW_GUIDE,
)
from src.features.composer.grounding import grounding_problem
from src.features.composer.grounding_constants import GROUNDING_MISSING
from src.features.composer.port import (
    CollectedFragment, ComposedReport, ComposedSection, ComposedSentence, FlowRow,
)
from src.features.composer.verify import verify_report


SECTION = "operations_partners"
SOURCE_TEXT = "비용 감소가 이익 증가의 주요 원인이다."
DENIAL_TEXT = "비용 감소가 이익 증가의 주요 원인이 아니다."
#: 한 칸 안에 인과가 완결된 «기존 정상» 줄.
WHOLE_CELLS = (SOURCE_TEXT, "", "")
#: 원문 한 문장이 온전히 지지하는 «정상 분할» 줄.
SPLIT_CELLS = ("비용 감소", "이익 증가의 주요 원인", "")
PAIR = {"원인": "비용 감소", "결과": "이익 증가"}


def _relation(source_id="cause", quote=SOURCE_TEXT, **pair):
    return {"관계": [{"유형": "인과", "근거": source_id, "원문": quote,
                    **(pair or PAIR)}]}


def _fragments(text=SOURCE_TEXT):
    return (CollectedFragment("cause", "경영진 설명", text),
            CollectedFragment("other", "다른 설명", SOURCE_TEXT))


def _joined(cells):
    return FLOW_CELL_JOIN.join(cells)


def _sources(text=SOURCE_TEXT):
    return {"cause": text}


# ══════════════════════════════════════════════════════════
# ① 가드 자체 — 구조가 있을 때만 분할을 읽는다
# ══════════════════════════════════════════════════════════

def test_split_cells_prove_the_relation_only_with_the_cell_structure() -> None:
    """같은 문자열이라도 «칸 구조»가 함께 올 때만 분할 인과가 증명된다."""

    joined = _joined(SPLIT_CELLS)

    assert causal_relation_problem(joined, _sources(), _relation(), SPLIT_CELLS) == ""
    # prose 는 그대로다 — 칸 구조 없이 온 같은 문자열은 여전히 미증명이다.
    assert causal_relation_problem(joined, _sources(), _relation()) == CAUSE_CLAIM_UNCOVERED


def test_whole_cell_causal_sentence_keeps_the_existing_path() -> None:
    """한 칸 안에 완결된 인과는 지금까지와 같은 경로로 통과한다(양성 대조)."""

    joined = _joined(WHOLE_CELLS)

    assert causal_relation_problem(joined, _sources(), _relation(), WHOLE_CELLS) == ""
    assert causal_relation_problem(SOURCE_TEXT, _sources(), _relation()) == ""


@pytest.mark.parametrize("cells", (
    # 떨어진 칸을 건너뛰어 묶지 않는다.
    ("비용 감소", "고객 응대", "이익 증가의 주요 원인"),
    # 칸의 «일부»가 원인인 것으로는 부족하다.
    ("비용 감소 흐름", "이익 증가의 주요 원인", ""),
    # 결과 칸이 통째로 그 꼴이 아니면 부족하다.
    ("비용 감소", "이익 증가의 주요 원인과 고객 확대", ""),
))
def test_only_the_closed_adjacent_split_form_is_accepted(cells) -> None:
    """인접·전체 일치가 아닌 분할은 «통과»가 아니라 «미증명»으로 남는다."""

    assert causal_relation_problem(
        _joined(cells), _sources(), _relation(), cells) == CAUSE_CLAIM_UNCOVERED


def test_split_structure_does_not_prove_the_direction_by_itself() -> None:
    """후보 구조가 닫힌 분할이어도 원문이 반대로 배정하면 막는다."""

    cells = ("이익 증가", "비용 감소의 주요 원인", "")
    entry = _relation(**{"원인": "이익 증가", "결과": "비용 감소"})

    assert causal_relation_problem(
        _joined(cells), _sources(), entry, cells) == CAUSE_DIRECTION_REVERSED


def test_split_row_still_needs_its_own_citation_and_a_positive_source() -> None:
    """차용 인용과 원문 부정은 분할이어도 그대로 막힌다."""

    borrowed = _relation(source_id="other")
    denied = _relation(quote=DENIAL_TEXT)

    assert causal_relation_problem(
        _joined(SPLIT_CELLS), _sources(), borrowed, SPLIT_CELLS
    ) == CAUSE_RELATION_NOT_IN_SOURCE
    assert causal_relation_problem(
        _joined(SPLIT_CELLS), _sources(DENIAL_TEXT), denied, SPLIT_CELLS
    ) == CAUSE_NEGATED_IN_SOURCE


def test_a_second_unproven_cause_in_the_same_row_does_not_ride_along() -> None:
    """한 줄의 두 번째 인과 단언이 첫 관계에 얹혀 통과하지 않는다."""

    cells = ("비용 감소", "이익 증가의 주요 원인", "환율 변동이 매출 감소의 주요 원인")

    assert causal_relation_problem(
        _joined(cells), _sources(), _relation(), cells) == CAUSE_CLAIM_UNCOVERED


def test_relation_evidence_cannot_stand_in_for_numeric_grounding() -> None:
    """관계 근거가 있어도 수치·추세·시점 요구는 그대로다(인접 보호)."""

    text = "매출 100억원"
    entry = {"검증근거": _relation(quote=text)}

    assert grounding_problem(text, {"cause": SOURCE_TEXT}, entry) == GROUNDING_MISSING


def test_prompt_asks_for_exactly_what_the_code_enforces() -> None:
    """검수 안내와 가드가 서로 다른 근거 형태를 요구하면 조용히 어긋난다."""

    assert "바로 다음" in RELATION_REVIEW_GUIDE
    assert "빈 칸을 뺀" in RELATION_REVIEW_GUIDE
    assert "내용 있는 다른 칸을 건너뛰어" in RELATION_REVIEW_GUIDE
    assert "칸의 일부만" in RELATION_REVIEW_GUIDE


# ══════════════════════════════════════════════════════════
# ② 실제 공개 경로 — 독립 도식과 묶음 도식에서 같은 결과여야 한다
# ══════════════════════════════════════════════════════════

def _ask(numbers, relation, section=SECTION, citations=("cause",)):
    def ask(prompt):
        return json.dumps({"판정": [
            {"번호": number, "장": section, "근거": list(citations), "결과": "참",
             "검증근거": relation}
            for number in numbers
        ]}, ensure_ascii=False)
    return ask


def _standalone(cells, relation, fragments=None):
    rows = (FlowRow(cells, ("cause",)),)
    report = ComposedReport((ComposedSection(SECTION, (), flow_rows=rows),))
    checked, problems = check_diagrams(
        report, fragments or _fragments(), _ask((1,), relation))
    return checked.sections[0].flow_rows, problems


def _grouped(cells, relation, fragments=None):
    rows = (FlowRow(cells, ("cause",)),)
    sentence = ComposedSentence("회사는 사업을 운영한다.", ("cause",), "확인")
    report = ComposedReport((ComposedSection(SECTION, (sentence,), flow_rows=rows),))
    checked = verify_report(
        report, fragments or _fragments(), None, _ask((1, 2), relation),
        allowed_fragment_ids_by_section={SECTION: frozenset(("cause", "other"))},
    )
    return checked.sections[0].flow_rows


@pytest.mark.parametrize("cells", (SPLIT_CELLS, WHOLE_CELLS))
def test_both_diagram_paths_keep_a_fully_supported_row(cells) -> None:
    """정상 분할과 한 칸 완결이 독립·묶음 두 경로에서 모두 남는다."""

    rows, problems = _standalone(cells, _relation())

    assert len(rows) == 1 and not problems
    assert len(_grouped(cells, _relation())) == 1


@pytest.mark.parametrize("cells,relation,fragments", (
    # 차용 인용 — 그 줄이 인용하지 않은 근거 id
    (SPLIT_CELLS, _relation(source_id="other"), None),
    # 역방향 — 원문이 반대로 배정한다
    (("이익 증가", "비용 감소의 주요 원인", ""),
     _relation(**{"원인": "이익 증가", "결과": "비용 감소"}), None),
    # 원문 부정
    (SPLIT_CELLS, _relation(quote=DENIAL_TEXT),
     (CollectedFragment("cause", "경영진 설명", DENIAL_TEXT),)),
    # 같은 줄의 두 번째 미증명 인과
    (("비용 감소", "이익 증가의 주요 원인", "환율 변동이 매출 감소의 주요 원인"),
     _relation(), None),
    # 떨어진 두 칸
    (("비용 감소", "고객 응대", "이익 증가의 주요 원인"), _relation(), None),
))
def test_both_diagram_paths_still_drop_unproven_rows(cells, relation, fragments) -> None:
    """분할 허용이 차용·역방향·부정·미증명 두 번째 인과를 열어 주지 않는다."""

    rows, problems = _standalone(cells, relation, fragments)

    assert rows == () and len(problems) == 1
    assert _grouped(cells, relation, fragments) == ()


def test_prose_sentence_with_the_same_semicolon_text_is_still_rejected() -> None:
    """같은 문자열이라도 본문 문장이면 칸 구조가 없으므로 새로 허용되지 않는다."""

    sentence = ComposedSentence(_joined(SPLIT_CELLS), ("cause",), "확인")
    report = ComposedReport((ComposedSection(SECTION, (sentence,)),))
    checked = verify_report(
        report, _fragments(), None, _ask((1,), _relation()),
        allowed_fragment_ids_by_section={SECTION: frozenset(("cause", "other"))},
    )

    assert checked.sections[0].sentences == ()
