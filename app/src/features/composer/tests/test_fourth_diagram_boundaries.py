"""명시 인용 수치와 정상 제품을 보존하며 일반어·회계 절 오독을 막는다."""

import json
from dataclasses import replace

import pytest

from src.features.composer.diagram_check import (
    _drop_uninformative_operations_rows, _numbers_are_grounded,
    _review_prompt, check_diagram_numbers, revenue_stream_names,
)
from src.features.composer.diagram_review_constants import DIAGRAM_CITATIONS_PREFIX, DIAGRAM_EVIDENCE_PREFIX
from src.features.composer.port import CollectedFragment, ComposedReport, ComposedSection, FlowRow


def test_explicit_multiple_clauses_keep_numbers_condition_and_scope():
    texts = {"limit": "하루 3시간·월 10만원을 기준으로 한다.",
             "condition": "초과할 경우 보호자 인증과 동의를 받으면 이용할 수 있다.",
             "plan": "2027년 초 정책을 적용한다."}
    fragments = tuple(CollectedFragment(fid, "뉴스", text, document_identity="document:one", document_content_sha256="a" * 64)
                      for fid, text in texts.items())
    row = FlowRow(("청소년 보호", "하루 3시간·월 10만원 초과 시 보호자 인증·동의, 2027년 초 적용"), tuple(texts))
    report = ComposedReport((ComposedSection("current_challenges", (), flow_rows=(row,)),))
    checked, problems = check_diagram_numbers(report, fragments)
    assert problems == () and checked.sections[0].flow_rows == (row,)
    prompt = _review_prompt(((1, "current_challenges", row),), dict(texts, unused="다른 수치"))
    dictionaries = [json.loads(line[len(DIAGRAM_EVIDENCE_PREFIX):]) for line in prompt.splitlines() if line.startswith(DIAGRAM_EVIDENCE_PREFIX)]
    citations = [json.loads(line[len(DIAGRAM_CITATIONS_PREFIX):]) for line in prompt.splitlines() if line.startswith(DIAGRAM_CITATIONS_PREFIX)]
    assert dictionaries == [texts] and citations == [list(texts)]


@pytest.mark.parametrize("same_document", [True, False])
def test_uncited_threshold_clause_is_not_borrowed(same_document):
    plan = CollectedFragment("plan", "뉴스", "2027년 초 정책을 적용한다.", document_identity="document:one", document_content_sha256="a" * 64)
    threshold = replace(plan, fragment_id="threshold", text="하루 3시간·월 10만원 제한", document_identity="document:one" if same_document else "document:other")
    row = FlowRow(("청소년 보호", "하루 3시간·월 10만원"), ("plan",))
    report = ComposedReport((ComposedSection("current_challenges", (), flow_rows=(row,)),))
    checked, problems = check_diagram_numbers(report, (plan, threshold))
    assert checked.sections[0].flow_rows == ()
    assert "의 수 3" in problems[0] and "인용 조각만 [plan:" in problems[0]


@pytest.mark.parametrize("relative", ["올해", "지난해", "내년", "지난 12월"])
def test_publication_metadata_is_not_added_to_the_numeric_pool(relative):
    fragment = CollectedFragment("one", "뉴스", f"{relative} 서비스를 출시했다.", document_date="2026-09-08")
    assert _numbers_are_grounded(relative, fragment.text) is None
    row = FlowRow(("서비스 출시", "2026년"), ("one",))
    report = ComposedReport((ComposedSection("current_challenges", (), flow_rows=(row,)),))
    checked, _ = check_diagram_numbers(report, (fragment,))
    assert checked.sections[0].flow_rows == ()


@pytest.mark.parametrize("cells", [("AI 콘텐츠", "고객에게 제공", ""), ("인공지능 콘텐츠", "고객에게 제공", "미확인")])
def test_generic_operations_path_is_removed(cells):
    assert _drop_uninformative_operations_rows((FlowRow(cells, ("one",)),))[0] == ()


@pytest.mark.parametrize("cells", [("오로라 콘텐츠 플랫폼", "고객에게 제공", ""), ("AI 콘텐츠 서비스", "제공", ""), ("AI 콘텐츠", "고객에게 제공", "대학 연구실")])
def test_named_product_or_customer_path_is_preserved(cells):
    rows = (FlowRow(cells, ("one",)),)
    assert _drop_uninformative_operations_rows(rows)[0] == rows


@pytest.mark.parametrize("subject", ["환입은", "할인은", "공제액은"])
def test_fused_deduction_clause_is_not_a_revenue_source(subject):
    assert revenue_stream_names((f"부가가치세와 {subject}수익에서 차감하고 있습니다.",)) == ()


def test_valid_revenue_nouns_and_long_particles_are_preserved():
    assert revenue_stream_names(("수익은 공연수익과 후원수익으로 구성된다.",)) == ("공연", "후원")
    assert revenue_stream_names(("용역 매출과 콘텐츠 매출로 구성된다.",)) == ("용역", "콘텐츠")
    assert revenue_stream_names(("제품매출 100 상품매출 20",)) == ("제품", "상품")
    assert revenue_stream_names(("수익은 환입수익과 순은수익으로 구성된다.",)) == ("환입", "순은")


def test_unmarked_income_statement_revenue_accounts_are_not_streams():
    """4차 실측 P13 — 구성 표지 없는 붙여 쓴 「…수익」은 회계 항목이 흔하다.

    실측 모양 그대로: 「선수수익」은 계약부채 계상 설명(금액 행이 아님)이고,
    「영업외수익」·「이자수익」·「보조금수익」은 영업손실 아래 «영업외수익
    구획»의 항목이다. 이들이 2장 도식의 «빠진 매출원»으로 진단되면, 다음
    실행 프롬프트가 회계 항목을 사업 도식에 그리라고 밀게 된다.
    """
    # 회계 처리 서술 — 앞에 영업수익 구획이 있어도 금액 행이 아니면 아니다.
    assert revenue_stream_names((
        "회사의 주된 영업수익의 형태는 용역 매출 등으로 구성됩니다. "
        "관련 미수행의무는 선수수익으로 계상하고 있습니다.",
    )) == ("용역",)
    # 영업외수익 구획 — 영업수익 머리가 앞에 있어도, 더 가까운 구획 머리가
    # 「영업외수익」이면 그 아래 항목은 영업외 항목이다.
    assert revenue_stream_names((
        "Ⅰ. 영업수익 47,117,211,348 Ⅱ. 영업비용 105,969,364,757 "
        "영업손실 58,852,153,409 Ⅳ. 영업외수익(주18) 860,912,171 "
        "이자수익 678,091,813 보조금수익 15,000,000",
    )) == ()


def test_operating_header_income_statement_rows_stay_streams():
    """참 반례 (조기 독립 검증) — 금융·서비스업 손익계산서는 「영업수익」 머리
    아래에 정상 영업 수익원을 «수익» 접미의 금액 행으로 적는다. 줄바꿈으로
    갈라져 구성 표지가 없어도 행 귀속(영업수익 구획)으로 살아남아야 한다.
    """
    assert revenue_stream_names((
        "영업수익\n이자수익 100\n수수료수익 20",
    )) == ("이자", "수수료")


def test_balance_sheet_header_breaks_operating_block():
    """최종 경계 반례 — 영업수익 블록 뒤 유동부채 같은 재무상태표 구획 머리가
    오면 그 뒤의 「…수익」 금액 행은 영업 수익원이 아니다.

    실측 모양 그대로: 「영업수익→이자수익100→유동부채→선수수익20」에서
    선수수익은 유동부채 구획에 있으므로 영업 수익원이 아니다.
    """
    assert revenue_stream_names((
        "영업수익\n이자수익 100\n유동부채\n선수수익 20",
    )) == ("이자",)


def test_items_before_balance_sheet_break_preserved():
    """대칭 보존 — 블록 종료 머리 «앞»의 영업수익 항목은 그대로 수익원이다."""
    assert revenue_stream_names((
        "영업수익\n이자수익 100\n수수료수익 20\n유동부채\n선수수익 30",
    )) == ("이자", "수수료")


@pytest.mark.parametrize("break_header", [
    "영업비용", "영업이익", "영업손실", "매출원가", "판매비",
    "비유동부채", "유동자산", "비유동자산",
    "자산총계", "부채총계", "자본총계",
])
def test_various_block_break_headers(break_header):
    """영업수익 블록을 끊는 구획 머리가 다양하다."""
    assert revenue_stream_names((
        f"영업수익\n이자수익 100\n{break_header}\n보조금수익 50",
    )) == ("이자",)


def test_marked_composition_sentence_keeps_income_named_streams():
    """참 반례 — 회사가 «구성»을 밝힌 문장 안의 「…수익」은 매출원이다.

    금융·서비스업은 영업수익 구성을 「이자수익·수수료수익」처럼 «수익» 접미로
    밝힌다. 좁힌 것은 표지 없는 붙여 쓴 갈래뿐이어야 한다.
    """
    assert revenue_stream_names((
        "영업수익은 이자수익과 수수료수익으로 구성됩니다.",
    )) == ("이자", "수수료")
