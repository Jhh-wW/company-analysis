"""절차-only 표와 실제 사업 사건·대응을 구별한다."""
import pytest

from src.features.composer import verify as v
from src.features.composer.challenge_business_scope import challenge_business_problem
from src.features.composer.challenge_event_scope import response_current_activity_problem
from src.features.composer.industry_context import has_verified_direct_business_issue
from src.features.composer.logic import summary_candidates
from src.features.composer.port import CollectedFragment, ComposedReport, ComposedSection, FlowRow

PROCEDURE_REASON = "challenge_business_relation_unbound"
ACCOUNTING_REASON = "accounting_policy_boilerplate"
BUSINESS_ISSUE = "고객의 공사대금 미지급으로 공사가 중단됐다"
BUSINESS_RESPONSE = "회사는 미지급 공사대금 회수 소송을 진행하고 있다"


@pytest.mark.parametrize("issue", [
    "채무부존재확인·공사대금반환청구 소송 진행 중(서울중앙·남부지방법원, 1심)",
    "계약취소 소송 계류 중(지방법원)",
    "손해배상청구 심리 중(항소심)",
    "대금반환청구 소송 진행 중",
])
def test_table_procedure_label_has_no_direct_business_effect(issue):
    sources = {"a": issue + ". " + BUSINESS_ISSUE + ". " + BUSINESS_RESPONSE}
    assert challenge_business_problem(issue, sources, cells=(issue, BUSINESS_RESPONSE)) == PROCEDURE_REASON


def test_current_activity_does_not_borrow_another_sentence_stop():
    source = '저탄소 시스템 개발 진행 중. 고객의 공사대금 미지급으로 공사가 중단됐다.'
    claim = '저탄소 시스템 개발이 진행되고 있다.'
    assert response_current_activity_problem(claim, {'a': source}) == ''


def test_current_activity_question_does_not_establish_current_support():
    source = '광학 센서 개발 진행 중? 공장 가동이 중단됐다.'
    claim = '광학 센서 개발이 진행되고 있다.'
    assert response_current_activity_problem(claim, {'a': source}) == 'time_invalid'


def test_stopped_activity_does_not_borrow_another_sentence_progress():
    source = '저탄소 시스템 개발 진행 중단. 고객의 공사는 진행 중이다.'
    claim = '저탄소 시스템 개발이 진행되고 있다.'
    assert response_current_activity_problem(claim, {'a': source}) == 'time_invalid'


def test_quote_newline_and_repeated_punctuation_do_not_create_current_support():
    claim = '저탄소 시스템 개발이 진행되고 있다.'
    sources = (
        "'저탄소 시스템 개발 진행 중.'은 가정이며 중단됐다.",
        '저탄소 시스템 개발 진행 중.. 그러나 중단됐다.',
        '저탄소 시스템 개발 중단\n대체 설비가 진행 중이다.',
    )
    for source in sources:
        assert response_current_activity_problem(claim, {'a': source}) == 'time_invalid'


@pytest.mark.parametrize("response", [
    "충당부채 미인식, 재무상태 중요 영향 없을 것으로 판단",
    "충당부채를 인식하지 않았다",
    "재무제표에 중요한 영향이 없음",
])
def test_accounting_assessment_is_not_a_business_response(response):
    source = BUSINESS_ISSUE + ". " + response
    assert challenge_business_problem(BUSINESS_ISSUE, {"a": source},
                                      cells=(BUSINESS_ISSUE, response)) == ACCOUNTING_REASON


@pytest.mark.parametrize("issue,response", [
    (BUSINESS_ISSUE, BUSINESS_RESPONSE),
    ("납품 중단으로 계약취소 소송 진행 중(지방법원, 1심)", "회사는 대체 생산설비를 확보했다"),
    ("특허 침해로 제품 판매가 금지됐다", "회사는 특허침해 소송을 진행하고 있다"),
    ("고객의 연체 채권 회수가 지연됐다", "은행은 연체 채권 회수 소송을 진행하고 있다"),
    ("고객의 소송 수행이 지연됐다", "법률서비스 회사는 고객의 소송을 대리하고 있다"),
    (BUSINESS_ISSUE, "충당부채 미인식, 회사는 미지급 대금을 회수하기 위해 소송을 진행하고 있다"),
    ("제품 결함으로 납품이 중단됐다", "회계서비스 회사는 고객에게 충당부채 평가 서비스를 제공하고 수수료를 받는다"),
])
def test_real_business_effect_and_response_are_preserved(issue, response):
    source = issue + ". " + response
    assert not challenge_business_problem(issue, {"a": source}, cells=(issue, response))


@pytest.mark.parametrize("accounting_only", [False, True])
def test_final_table_filter_rejects_model_true_without_losing_original(monkeypatch, accounting_only):
    monkeypatch.setattr(v, "_semantic_review", lambda groups, *args, **kwargs: groups)
    issue = BUSINESS_ISSUE if accounting_only else "대금반환청구 소송 진행 중(지방법원, 1심)"
    response = "충당부채 미인식" if accounting_only else BUSINESS_RESPONSE
    row = FlowRow((issue, response), ("a",))
    report = ComposedReport((ComposedSection("current_challenges", (), flow_rows=(row,)),))
    source = issue + ". " + response
    diagnostics = []
    final = v.verify_report(report, (CollectedFragment("a", "공식 자료", source),),
                            None, lambda *args: "", diagnostics=diagnostics)
    assert not final.sections[0].flow_rows
    assert report.sections[0].flow_rows == (row,)
    assert not has_verified_direct_business_issue(final)
    assert not summary_candidates(final)
    assert diagnostics


def test_final_table_filter_keeps_real_business_response():
    row = FlowRow((BUSINESS_ISSUE, BUSINESS_RESPONSE), ("a",))
    report = ComposedReport((ComposedSection("current_challenges", (), flow_rows=(row,)),))
    source = BUSINESS_ISSUE + ". " + BUSINESS_RESPONSE
    final = v._filter_challenge_flow_scope(report, {"a": CollectedFragment("a", "공식 자료", source)}, None)
    assert final.sections[0].flow_rows == (row,)
