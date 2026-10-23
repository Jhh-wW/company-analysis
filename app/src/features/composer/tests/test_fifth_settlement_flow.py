"""5차 저장 응답의 무료 소비 시험. 새 모델 승인·공개 Fact 봉인을 만들지 않는다."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path

import pytest

from src.features.composer.diagram_check import check_diagram_numbers
from src.features.composer.diagram_review_constants import FLOW_ACCOUNTING_REVENUE_CODE
from src.features.composer.logic import _compose_one_section
from src.features.composer.port import CollectedFragment, ComposedReport, ComposedSection, ComposedSentence, FlowRow
from src.features.composer.role_binding import role_binding_problem, _binding_problem, _units
from src.features.composer.role_binding_constants import ROLE_BINDING_PAIR_DEGENERATE
from src.features.composer.settlement_candidate import supplement_settlement_candidates
from src.features.composer.settlement_candidate_constants import SETTLEMENT_SLOT
from src.features.composer.verify import _apply_grounding

FIXTURE = json.loads((Path(__file__).parent / "fixtures/fifth_business_flow.json").read_text(encoding="utf-8"))


def _entry(number):
    return deepcopy(next(item for item in FIXTURE["review_entries"] if item["번호"] == number))


def _fragment():
    # 후보 준비만 시험하는 메타. 이 값으로 Source/Fact 승인을 만들지 않는다.
    return CollectedFragment(
        "90", "공시", FIXTURE["sources"]["90"], source_url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260331000296",
        document_date="2026-03-31", document_identity="dart:20260331000296",
        document_content_sha256="a" * 64, formal_source_kind="dart_audit_report",
        source_document_id="20260331000296", identity_binding="후보 준비 시험",
        supported_claim_slots=(SETTLEMENT_SLOT,),
    )


def _guard(text=None, source=None, evidence=None):
    row = FIXTURE["draft"]["문장들"][2]
    return role_binding_problem(
        row["글"] if text is None else text,
        {"52": FIXTURE["sources"]["52"] if source is None else source},
        _entry(10)["검증근거"] if evidence is None else evidence,
    )


def test_actual_fifth_pg_prose_receipt_is_bound_without_changing_any_saved_response():
    before = deepcopy(FIXTURE)
    row = FIXTURE["draft"]["문장들"][2]
    problems = {}
    verdicts = _apply_grounding(
        json.dumps({"판정": [_entry(10)]}, ensure_ascii=False), {10: "참"},
        {10: (row["글"], {fid: FIXTURE["sources"][fid] for fid in row["인용"]})},
        diagnostic_contexts={10: ("business_model", "본문", "확인")},
        claim_slots_by_number={10: SETTLEMENT_SLOT}, grounding_problems=problems,
    )
    assert verdicts == {10: "참"} and problems == {}
    assert FIXTURE == before
    assert all(hashlib.sha256(text.encode()).hexdigest() == FIXTURE["source_sha256"][fid]
               for fid, text in FIXTURE["sources"].items())


@pytest.mark.parametrize("old,new", [
    ("회사는 전자지불", "자회사는 전자지불"),
    ("원천사로부터 대금을 받은 후", "원천사에 대금을 준 후"),
    ("가맹점에", "다른 수취인에게"),
    ("수수료와 부가가치세", "배송수수료와 부가가치세"),
    ("수수료와 부가가치세", "수수료"),
    ("제외하여 지급하며", "회사 수익으로 인식하며"),
    ("제외하여 지급하며", "제외하여 지급하지 않으며"),
    ("회사는 전자지불", "회사는 계약을 체결하면 전자지불"),
])
def test_complete_pg_prose_does_not_prove_changed_actor_direction_tax_or_condition(old, new):
    text = FIXTURE["draft"]["문장들"][2]["글"]
    assert old in text
    changed = text.replace(old, new)
    # 인식 단정은 역할 발동 어휘가 없을 수도 있으므로 제출 관계 자체를 직접 대조한다.
    # 이 시험은 새 정산 증명이 변조된 관계를 승인하지 않는 경계를 검사한다.
    problem, spans, _, _ = _binding_problem(
        _entry(10)["검증근거"]["관계"][0], _units(changed, None),
        {"52": FIXTURE["sources"]["52"]}, False,
    )
    assert problem and not spans


@pytest.mark.parametrize("target", ["부가가치세", "회사", "다른 가맹점", "거래금액"])
def test_fee_target_exception_is_not_an_arbitrary_entity_exception(target):
    evidence = _entry(10)["검증근거"]
    evidence["관계"][0]["대상"] = target
    assert _guard(evidence=evidence)


def test_general_overlapping_pair_and_missing_own_quote_still_fail():
    assert role_binding_problem("회사는 광고를 제작합니다.", {"1": "회사는 광고를 제작합니다."},
        {"관계": [{"근거": "1", "대상": "광고", "역할값": "광고를 제작", "원문": "회사는 광고를 제작합니다.", "유형": "역할"}]}) == ROLE_BINDING_PAIR_DEGENERATE
    evidence = _entry(10)["검증근거"]
    evidence["관계"][0]["원문"] = "가맹점에 수수료를 제외하고 지급합니다."
    assert _guard(evidence=evidence)
    assert _guard(source="교육을 위한 가상의 회사 사례입니다. " + FIXTURE["sources"]["52"])
    assert _guard(evidence={})


@pytest.mark.parametrize("old,new", [
    ("회사는 전자지불", "자회사는 전자지불"),
    ("회사는 전자지불", "회사는 사업에 진출하면 전자지불"),
    ("원천사로부터 대금을 받은 후", "원천사에 대금을 준 후"),
    ("회사의 가맹점으로", "회사의 다른 수취인에게"),
    ("수수료와 부가가치세", "배송수수료와 부가가치세"),
    ("지급하고 있습니다", "지급하지 않습니다"),
])
def test_changed_source_and_matching_quote_cannot_prove_original_settlement(old, new):
    source = FIXTURE["sources"]["52"]
    assert old in source
    evidence = _entry(10)["검증근거"]
    evidence["관계"][0]["원문"] = evidence["관계"][0]["원문"].replace(old, new)
    assert _guard(source=source.replace(old, new), evidence=evidence)


def test_actual_wrong_repeated_fee_row_remains_false_and_cash_accounting_row_is_removed():
    pg, cash = FIXTURE["draft"]["경로표"][:2]
    entries = [_entry(54), _entry(55)]
    candidates = {n: (" → ".join(row["칸"]), {fid: FIXTURE["sources"][fid] for fid in row["인용"]})
                  for n, row in ((54, pg), (55, cash))}
    verdicts = _apply_grounding(
        json.dumps({"판정": entries}, ensure_ascii=False), {54: "거짓", 55: "참"}, candidates,
        diagnostic_contexts={n: ("business_model", "도식", "확인") for n in candidates},
        flow_cells_by_number={54: tuple(pg["칸"]), 55: tuple(cash["칸"])},
    )
    assert verdicts[54] == "거짓"
    row = FlowRow(tuple(cash["칸"]), tuple(cash["인용"]))
    report = ComposedReport((ComposedSection("business_model", (), flow_rows=(row,)),))
    checked, diagnostics = check_diagram_numbers(report, tuple(
        CollectedFragment(fid, "공시", text) for fid, text in FIXTURE["sources"].items()))
    assert checked.sections[0].flow_rows == ()
    assert any(FLOW_ACCOUNTING_REVENUE_CODE in item for item in diagnostics)


def test_saved_writer_consumption_adds_one_source_bound_unsigned_flow_before_review():
    calls = []
    def saved_writer(prompt):
        calls.append(prompt)
        return json.dumps(FIXTURE["draft"], ensure_ascii=False)
    fragment = _fragment()
    section = _compose_one_section("business_model", "저장 작성 응답 무료 소비", saved_writer,
                                   source_fragments=(fragment,))
    assert len(calls) == 1
    added = section.flow_rows[-1]
    assert added.citations == ("90",) and added.review_binding is None
    assert added.cells[0] == added.cells[-1] == ""
    assert "원천사가 회사에 대금 지급" in added.cells[2]
    assert "수수료·부가가치세 차감 후 가맹점에 지급" in added.cells[2]
    assert len(section.flow_rows) == len(FIXTURE["draft"]["경로표"]) + 1
    assert all(s.verification_state == "unverified" and not s.verified_fact_id for s in section.sentences)
    assert supplement_settlement_candidates(section.sentences, section.flow_rows, (fragment,)) == (section.sentences, section.flow_rows)
    # 새 후보에는 별도 검수 증명이 필요하다. 기존 거짓 행의 승인으로 바꾸지 않는다.
    assert role_binding_problem(" → ".join(added.cells), {"90": fragment.text}, {}, cells=added.cells)
    evidence = _entry(10)["검증근거"]
    evidence["관계"][0]["근거"] = "90"  # 같은 실제 원문으로 만든 합성 검수 경계 입력.
    assert not role_binding_problem(" → ".join(added.cells), {"90": fragment.text}, evidence, cells=added.cells)
    entry = {"번호": 1, "장": "business_model", "근거": ["90"], "결과": "참", "검증근거": evidence}
    def consume(verdict, proof):
        response = dict(entry, 결과=verdict, 검증근거=proof)
        return _apply_grounding(
            json.dumps({"판정": [response]}, ensure_ascii=False), {1: verdict},
            {1: (" → ".join(added.cells), {"90": fragment.text})},
            diagnostic_contexts={1: ("business_model", "도식", "확인")},
            flow_cells_by_number={1: added.cells},
        )[1]
    assert consume("참", evidence) == "참"
    assert consume("참", {}) != "참"
    assert consume("거짓", evidence) == "거짓"
    assert added.review_binding is None


def test_missing_prose_is_added_as_exact_source_span_and_never_from_legacy_or_wrong_slot():
    fragment = _fragment()
    sentences, rows = supplement_settlement_candidates((), (), (fragment,))
    assert len(sentences) == len(rows) == 1
    assert sentences[0].text in fragment.text
    assert sentences[0].planned_claim_slot == SETTLEMENT_SLOT
    assert sentences[0].verification_state == "unverified"
    for invalid in (replace(fragment, formal_source_kind=""), replace(fragment, identity_binding=""),
                    replace(fragment, supported_claim_slots=("business_model:revenue_model",)),
                    replace(fragment, text=fragment.text.replace("회사는 전자지불", "한빛회사는 전자지불")),
                    replace(fragment, text=fragment.text.replace("회사는 전자지불", "회사는 사업에 진출하면 전자지불")),
                    replace(fragment, text="교육을 위한 가상의 회사 사례입니다. " + fragment.text)):
        assert supplement_settlement_candidates((), (), (invalid,)) == ((), ())
    signed = replace(sentences[0], verification_state="verified")
    assert supplement_settlement_candidates((signed,), (), (fragment,)) == ((signed,), ())
