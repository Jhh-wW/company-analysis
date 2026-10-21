"""저장 응답의 재사용은 오프라인 소비 경계 시험이며 새 모델 검수가 아니다."""
import json
from dataclasses import replace
from pathlib import Path

import pytest

from src.features.composer.empty_section_recovery import recover_empty_sections
from src.features.composer.logic import _compose_one_section, _sanitize_report_to_section_evidence, parse_section_response, extract_json_payload
from src.features.composer.port import CollectedFragment, ComposedReport, ComposedSection, ComposedSentence
from src.features.composer.revenue_source_slot import normalize_revenue_source_slot
from src.shared.report_evidence.business_slot_scope import business_slot_scope_problem

REVENUE = "business_model:revenue_model"
EXCHANGE = "business_model:value_exchange"
FIXTURE = json.loads((Path(__file__).parent / "fixtures/payment_service_slots.json").read_text(encoding="utf-8"))


def _fragments():
    return {key: CollectedFragment(key, "공시", text, supported_claim_slots=(REVENUE, EXCHANGE))
            for key, text in FIXTURE["fragments"].items()}


def _source_sentence():
    body = extract_json_payload(FIXTURE["recovery_writer_response"])["장들"]["business_model"]
    return parse_section_response(json.dumps(body, ensure_ascii=False), "business_model")[0]


def test_actual_drafts_keep_original_words_and_citations_with_correct_source_slots():
    rows = FIXTURE["writer_sentences"]
    sentences = parse_section_response(json.dumps({"문장들": rows}, ensure_ascii=False), "business_model")
    report = ComposedReport((ComposedSection("business_model", sentences),))
    old_slots = {"9": frozenset({EXCHANGE}), "40": frozenset({EXCHANGE}),
                 "37": frozenset({REVENUE}), "73": frozenset({REVENUE})}
    arguments = dict(allowed_fragment_ids_by_section={"business_model": frozenset({"2", *old_slots})},
                     enforce_declared_claim_slot_support=True)
    assert not _sanitize_report_to_section_evidence(report, supported_claim_slots_by_fragment_id=old_slots,
                                                   **arguments).sections[0].sentences
    corrected = {key: frozenset({REVENUE, EXCHANGE}) for key in old_slots}
    retained = _sanitize_report_to_section_evidence(report, supported_claim_slots_by_fragment_id=corrected,
                                                   **arguments).sections[0].sentences
    assert retained == sentences
    assert all(not business_slot_scope_problem(sentence.text, sentence.planned_claim_slot) for sentence in retained)


def test_writer_consumption_normalizes_before_review_without_rewriting_content():
    original = _source_sentence()
    body = {"문장들": [{"글": original.text, "인용": list(original.citations),
                       "등급": original.grade, "주장슬롯": original.planned_claim_slot}]}
    section = _compose_one_section("business_model", "저장 응답 소비", lambda _: json.dumps(body, ensure_ascii=False),
                                   source_fragments=tuple(_fragments().values()))
    assert section.sentences == (replace(original, planned_claim_slot=REVENUE),)
    assert section.sentences[0].verification_state == "unverified"


def test_recovery_sends_normalized_slot_to_reviewer_and_rechecks_saved_verdict():
    prompts = []
    def reviewer(prompt):
        prompts.append(prompt)
        assert '"business_model:revenue_model"' in prompt
        assert _source_sentence().text in prompt
        return FIXTURE["recovery_review_response"]
    result = recover_empty_sections(
        "토스페이먼츠 주식회사", ComposedReport((ComposedSection("business_model", ()),)),
        targets=("business_model",), evidence={"business_model": (_fragments()["9"],)},
        writer=lambda _: FIXTURE["recovery_writer_response"], reviewer=reviewer,
        baseline_date="2026-10-21", require_claim_slot=True,
    )
    assert len(prompts) == 1
    sentence, = result.sections[0].sentences
    assert sentence.text == _source_sentence().text
    assert sentence.citations == _source_sentence().citations
    assert sentence.planned_claim_slot == REVENUE and sentence.verification_state == "verified"


@pytest.mark.parametrize("change", (
    {"verification_state": "verified"},
    {"planned_claim_slot": "business_model:customer_type"},
    {"citations": ("missing",)},
    {"text": "회사는 서비스 제공 시점에 수익을 인식합니다."},
    {"text": "경쟁사는 데이터 서비스로부터 수익을 인식합니다."},
    {"text": "자회사는 데이터 서비스, 운영 용역으로부터 수익을 인식합니다."},
    {"text": "회사가 사업에 진출하는 경우 데이터 서비스로부터 수익을 인식합니다."},
    {"text": "회사는 서비스로부터 수익을 인식합니다. 고객에게 서비스를 제공하고 대금을 받습니다."},
))
def test_normalization_does_not_relabel_reviewed_or_unrelated_claims(change):
    sentence = replace(_source_sentence(), **change)
    assert normalize_revenue_source_slot(sentence, _fragments()) is sentence


@pytest.mark.parametrize("fragment", (
    CollectedFragment("9", "공시", "회사는 PG 서비스로부터 수익을 인식합니다.", supported_claim_slots=(EXCHANGE,)),
    CollectedFragment("9", "공시", "회사는 재화 인도 시점에 수익을 인식합니다.", supported_claim_slots=(REVENUE,)),
    CollectedFragment("9", "공시", "자회사는 PG 서비스로부터 수익을 인식합니다.", supported_claim_slots=(REVENUE,)),
))
def test_normalization_requires_own_declared_revenue_source_and_supported_slot(fragment):
    sentence = _source_sentence()
    assert normalize_revenue_source_slot(sentence, {"9": fragment}) is sentence
