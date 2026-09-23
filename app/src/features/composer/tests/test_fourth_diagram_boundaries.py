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
