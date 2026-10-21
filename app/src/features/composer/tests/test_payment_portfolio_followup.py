"""새 실패 실행의 원응답 소비 회귀. 추가 모델 호출이나 새 검수 승인은 만들지 않는다."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest

from src.features.composer.logic import _compose_one_section, _sanitize_report_to_section_evidence, parse_section_response, extract_json_payload
from src.features.composer.port import CollectedFragment, ComposedReport, ComposedSection, ComposedSentence
from src.features.composer.portfolio_revenue_content import portfolio_accounting_only_problem, supplement_portfolio_revenue_source
from src.features.composer.portfolio_revenue_content_constants import PORTFOLIO_REVENUE_SLOT
from src.features.composer.verify import _apply_grounding, DIAGNOSTIC_KIND_BODY
from src.shared.report_evidence.service_revenue_declaration import service_revenue_declaration_spans

FIXTURE = json.loads((Path(__file__).parent / "fixtures/payment_portfolio_followup.json").read_text(encoding="utf-8"))
SOURCE = FIXTURE["fragments"]["75"]


def test_service_declaration_engine_mirror_remains_identical():
    root = Path(__file__).resolve().parents[5]
    for name in ("service_revenue_declaration.py", "service_revenue_declaration_constants.py"):
        app_source = (root / "app/src/shared/report_evidence" / name).read_text(encoding="utf-8")
        engine_source = (root / "analysis_engine/src/features/evidence_collection" / name).read_text(encoding="utf-8")
        assert app_source.replace("src.shared.report_evidence", "features.evidence_collection") == engine_source


def _fragment(text=SOURCE, **changes):
    value = CollectedFragment(
        "75", "공시", text, source_url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260331000296",
        document_date="2026-03-31", document_identity="document:dart.fss.or.kr:20260331000296",
        document_content_sha256=hashlib.sha256(text.encode()).hexdigest(),
        formal_source_kind="dart_audit_report", source_document_id="20260331000296",
        identity_binding="단위시험 회사 결속 메타; 실제 봉인 검증은 통합시험 소유",
        supported_claim_slots=(PORTFOLIO_REVENUE_SLOT,),
    )
    return replace(value, **changes)


def test_actual_writer_input_has_service_list_but_wrong_slot_is_removed_before_review():
    draft = parse_section_response(FIXTURE["portfolio_writer_response"], "portfolio")
    assert "PG 서비스, 자금관리 서비스, 기타용역" in SOURCE
    assert draft[0].planned_claim_slot == "portfolio:product_role"
    report = ComposedReport((ComposedSection("portfolio", draft),))
    filtered = _sanitize_report_to_section_evidence(
        report, {"portfolio": frozenset({"1", "37", "75"})},
        supported_claim_slots_by_fragment_id={"37": frozenset({PORTFOLIO_REVENUE_SLOT}), "75": frozenset({PORTFOLIO_REVENUE_SLOT})},
        enforce_declared_claim_slot_support=True,
    )
    assert draft[0] not in filtered.sections[0].sentences
    assert draft[2] in filtered.sections[0].sentences


def test_actual_response_receives_one_exact_source_candidate_before_independent_review(caplog):
    with caplog.at_level("INFO"):
        section = _compose_one_section("portfolio", "저장 응답 소비", lambda _: FIXTURE["portfolio_writer_response"],
                                       source_fragments=(_fragment(),))
    original = parse_section_response(FIXTURE["portfolio_writer_response"], "portfolio")
    assert section.sentences[:-1] == original
    added = section.sentences[-1]
    assert added.text == "회사는 PG 서비스, 자금관리 서비스, 기타용역으로부터 수익을 인식합니다."
    assert added.text in SOURCE and added.citations == ("75",)
    assert added.planned_claim_slot == PORTFOLIO_REVENUE_SLOT
    assert added.verification_state == "unverified" and added.structured_claim is None
    assert "portfolio_current_service_source_candidate" in caplog.text
    assert supplement_portfolio_revenue_source(section.sentences, (_fragment(),)) == section.sentences


def test_existing_equivalent_candidate_or_reviewed_result_is_not_supplemented():
    start, end = service_revenue_declaration_spans(SOURCE)[0]
    sentence = ComposedSentence(SOURCE[start:end].replace("합니다", "한다"), ("75",), "확인", planned_claim_slot=PORTFOLIO_REVENUE_SLOT)
    assert supplement_portfolio_revenue_source((sentence,), (_fragment(),)) == (sentence,)
    reviewed = replace(sentence, text="이미 검수한 다른 사실", verification_state="verified")
    assert supplement_portfolio_revenue_source((reviewed,), (_fragment(),)) == (reviewed,)


@pytest.mark.parametrize("change", (
    {"identity_binding": ""}, {"document_identity": ""}, {"document_content_sha256": ""},
    {"formal_source_kind": ""}, {"supported_claim_slots": ("portfolio:product_role",)},
))
def test_incomplete_legacy_or_wrong_slot_cannot_supply_a_program_candidate(change):
    assert supplement_portfolio_revenue_source((), (_fragment(**change),)) == ()


@pytest.mark.parametrize("text", (
    "자회사는 데이터 서비스로부터 수익을 인식합니다.",
    "한빛회사는 데이터 서비스로부터 수익을 인식합니다.",
    "회사는 과거 데이터 서비스로부터 수익을 인식합니다.",
    "회사가 진출하는 경우 데이터 서비스로부터 수익을 인식합니다.",
    "회사는 서비스로부터 수익을 인식합니다.",
    "회사는 기타 서비스 및 일반 용역으로부터 수익을 인식합니다.",
    "회사는 데이터 서비스를 제공한 시점에 수익을 인식합니다.",
))
def test_no_candidate_is_invented_from_foreign_conditional_past_or_generic_policy(text):
    assert not service_revenue_declaration_spans(text)
    assert supplement_portfolio_revenue_source((), (_fragment(text),)) == ()


def test_hypothetical_context_cannot_supply_an_exact_program_candidate():
    text = "교육을 위한 가상의 회사 사례입니다.\n\n회사는 데이터 서비스로부터 수익을 인식합니다."
    assert supplement_portfolio_revenue_source((), (_fragment(text),)) == ()


def test_program_candidate_limit_and_newest_source_are_explicit():
    first = _fragment("회사는 데이터 서비스로부터 수익을 인식합니다.", document_date="2025-03-31", fragment_id="37")
    second = _fragment("회사는 정산 서비스로부터 수익을 인식합니다.")
    added = supplement_portfolio_revenue_source((), (first, second))
    assert len(added) == 1
    assert added[0].text == second.text and added[0].citations == ("75",)


def test_program_candidate_is_subject_to_independent_rejection():
    sentence = supplement_portfolio_revenue_source((), (_fragment(),))[0]
    response = json.dumps({"검증결과": [{"문장번호": 1, "판정": "거짓", "이유": "독립 검수 거절"}]}, ensure_ascii=False)
    result = _apply_grounding(
        response, {1: "거짓"}, {1: (sentence.text, {"75": SOURCE})},
        diagnostic_contexts={1: ("portfolio", DIAGNOSTIC_KIND_BODY, sentence.text)},
        claim_slots_by_number={1: PORTFOLIO_REVENUE_SLOT},
        confirmed_prose_numbers=frozenset({1}), prose_numbers=frozenset({1}),
    )
    assert result[1] != "참"


def test_saved_true_verdicts_still_pass_through_real_semantic_guards():
    portfolio = extract_json_payload(FIXTURE["portfolio_writer_response"])["문장들"]
    identity = extract_json_payload(FIXTURE["identity_writer_response"])["문장들"]
    selected = {4: ("identity", identity[4]), 7: ("identity", identity[8]),
                8: ("identity", identity[10]), 19: ("portfolio", portfolio[2]), 20: ("portfolio", portfolio[3])}
    candidates = {number: (row["글"], {fid: FIXTURE["fragments"][fid] for fid in row["인용"]})
                  for number, (_section, row) in selected.items()}
    problems = {}
    constrained = _apply_grounding(
        FIXTURE["first_review_response"], {number: "참" for number in selected}, candidates,
        diagnostic_contexts={number: (section, DIAGNOSTIC_KIND_BODY, row["글"]) for number, (section, row) in selected.items()},
        claim_slots_by_number={number: row["주장슬롯"] for number, (_section, row) in selected.items()},
        confirmed_prose_numbers=frozenset(selected), prose_numbers=frozenset(selected),
        grounding_problems=problems, baseline_date="2026-10-21",
    )
    assert constrained[4] == "참"
    assert problems[7] == problems[8] == "identity_administrative_only"
    assert constrained[19] != "참" and constrained[20] != "참"


@pytest.mark.parametrize("text", (
    "회사는 데이터 서비스를 판매하고 고객에게 이용료를 받는다.",
    "회사는 자금관리 서비스를 제공하며 원천사에서 받은 대금을 가맹점에 지급한다.",
    "회사는 데이터 서비스로부터 수익을 인식합니다.",
))
def test_real_services_and_revenue_declarations_are_preserved(text):
    assert not portfolio_accounting_only_problem(text)


def test_policy_with_an_unknown_or_real_extra_business_clause_is_not_wholly_deleted():
    text = extract_json_payload(FIXTURE["portfolio_writer_response"])["문장들"][2]["글"]
    assert portfolio_accounting_only_problem(text)
    assert not portfolio_accounting_only_problem(text + " 회사는 고객에게 정산 내역을 제공한다.")
    assert not portfolio_accounting_only_problem(text + " 회사는 새로운 분야의 선두이다.")
