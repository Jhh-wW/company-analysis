"""실제 표시형 인용이 검수 전에 같은 조각에 연결되고 경계를 넘지 않는지 확인한다."""
import json
from dataclasses import replace
from pathlib import Path

import pytest

from src.features.composer.citation_labels import (
    normalize_draft_flow_citations, normalize_draft_sentence_citations,
)
from src.features.composer.logic import (
    _compose_one_section, _sanitize_report_to_section_evidence,
)
from src.features.composer.port import (
    CollectedFragment, ComposedReport, ComposedSection, ComposedSentence, FlowReviewBinding, FlowRow,
)

FIXTURE = json.loads((Path(__file__).parent / "fixtures/labeled_news_citations.json").read_text(encoding="utf-8"))
SLOT = "future_strategy:stated_plan"


def test_actual_draft_citations_reach_same_section_evidence_without_approval():
    raw = FIXTURE["responses"][0]
    source = CollectedFragment("83", "보조 언론 보도", FIXTURE["source_text"], supported_claim_slots=(SLOT,))
    section = _compose_one_section("future_strategy", "저장된 작성 응답", lambda _: raw,
                                   source_fragments=(source,))
    assert section.sentences and section.flow_rows
    assert all(sentence.citations == ("83",) for sentence in section.sentences)
    assert all(sentence.verification_state == "unverified" for sentence in section.sentences)
    assert all(row.citations == ("83",) and row.review_binding is None for row in section.flow_rows)
    checked = _sanitize_report_to_section_evidence(
        ComposedReport((section,)), {"future_strategy": frozenset({"83"})},
        supported_claim_slots_by_fragment_id={"83": frozenset({SLOT})},
        enforce_declared_claim_slot_support=True,
    )
    assert checked.sections[0].sentences == section.sentences
    assert all(sentence.verification_state == "unverified" for sentence in checked.sections[0].sentences)


def test_initial_multi_payload_response_still_requires_valid_json_retry():
    source = CollectedFragment("83", "보조 언론 보도", FIXTURE["source_text"], supported_claim_slots=(SLOT,))
    section = _compose_one_section(
        "future_strategy", "저장된 형식 오류 응답", lambda _: FIXTURE["responses"][1],
        source_fragments=(source,), parse_retry_limit=0,
    )
    assert not section.sentences and not section.flow_rows


@pytest.mark.parametrize("citation", (
    "조각 84", "조각 0083", "조각 0", "[83]", "조각 83·뉴스", "조각 83 84",
    "조각 83 인용", "조각\n83", "문서 83", "제83조", "83,84",
))
def test_unknown_or_ambiguous_labels_do_not_find_another_source(citation):
    draft = ComposedSentence("회사가 계획을 밝혔다.", (citation,), "확인", planned_claim_slot=SLOT)
    assert normalize_draft_sentence_citations(draft, {"83"}) is draft


@pytest.mark.parametrize("change", (
    {"verification_state": "verified"}, {"verified_fact_id": "승인된-사실"},
    {"verification_state": "rejected"}, {"structured_claim": object()},
))
def test_reviewed_or_structured_claims_never_reuse_approval(change):
    draft = replace(ComposedSentence("원문 그대로", ("조각 83",), "확인"), **change)
    assert normalize_draft_sentence_citations(draft, {"83"}) is draft


def test_exact_existing_id_and_unknown_section_are_preserved():
    draft = ComposedSentence("원문 그대로", ("조각 83",), "확인", planned_claim_slot=SLOT)
    assert normalize_draft_sentence_citations(draft, {"조각 83", "83"}) is draft
    assert normalize_draft_sentence_citations(draft, {"99"}) is draft
    normalized = normalize_draft_sentence_citations(draft, {"83"})
    assert normalized == replace(draft, citations=("83",))


def test_signed_flow_is_not_changed():
    binding = FlowReviewBinding("future_strategy", "검수입력지문", (), "검수경로", "규칙")
    row = FlowRow(("계획", "시점", "내용"), ("조각 83",), review_binding=binding)
    assert normalize_draft_flow_citations(row, {"83"}) is row


def test_empty_recovery_sends_canonical_id_to_independent_review(monkeypatch):
    from src.features.composer import empty_section_recovery as recovery

    slot = "business_model:revenue_model"
    text = "회사는 PG 서비스로부터 수익을 인식합니다."
    fragment = CollectedFragment("9", "공시", text, supported_claim_slots=(slot,))
    draft = {"장들": {"business_model": {"문장들": [
        {"글": text, "인용": ["조각 9"], "등급": "확인", "주장슬롯": slot}
    ]}}}
    captured = []

    def inspect_review_input(report, *args, **kwargs):
        captured.extend(report.sections[0].sentences)
        return report

    monkeypatch.setattr(recovery, "verify_report", inspect_review_input)
    result = recovery.recover_empty_sections(
        "검증 회사", ComposedReport((ComposedSection("business_model", ()),)),
        targets=("business_model",), evidence={"business_model": (fragment,)},
        writer=lambda _: json.dumps(draft, ensure_ascii=False), reviewer=lambda _: "{}",
        baseline_date="2026-10-21", require_claim_slot=True,
    )
    assert len(captured) == 1
    assert captured[0].citations == ("9",) and captured[0].text == text
    assert captured[0].verification_state == "unverified"
    assert not result.sections[0].sentences
