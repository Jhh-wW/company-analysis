"""빈 장의 제한된 복구도 실제 문장 검수를 통과해야 한다."""
from __future__ import annotations

import hashlib
import json
from dataclasses import replace

import pytest

from src.features.composer.constants import GRADE_CONFIRMED, GRADE_INTERPRETED
from src.features.composer.empty_section_recovery import recover_empty_sections, recovery_evidence, rejected_sentence_fingerprint
from src.features.composer.port import AskFatalError, CollectedFragment, ComposedReport, ComposedSection, ComposedSentence
from src.features.composer.tests.test_pipeline import _FakeReviewer
from src.features.composer.tests.test_verify import _FakeVerifier, _all_true, _verdict_json
from src.features.composer.verify import verify_report
from src.shared.report_quality.source_identity import document_identity_from_parts


PAST_TEXT = "가나다전자는 물류 사업을 분할하고 투자 사업을 영위하고 있다."
CULTURE_TEXT = "가나다전자는 구성원 역량 개발을 위해 직무 교육을 운영한다."


def _fragment(section_id="past_changes", text=PAST_TEXT, fragment_id="1"):
    url = "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=2026031600000" + fragment_id
    return CollectedFragment(
        fragment_id=fragment_id, kind="사업내용", text=text,
        source_url=url, document_identity=document_identity_from_parts(
            host="dart.fss.or.kr", document_id="2026031600000" + fragment_id, url=url),
        document_title="사업보고서", document_date="2026-03-16",
        source_collected_on="2026-09-14", source_publisher="가나다전자",
        source_document_id="2026031600000" + fragment_id,
        document_content_sha256=hashlib.sha256(text.encode()).hexdigest(),
        formal_source_kind="dart_business_report", identity_binding="dart_corp_code",
        supported_claim_slots=(section_id + ":facts",),
    )


def _report(*section_ids):
    return ComposedReport(tuple(ComposedSection(section_id, (), notice="기존 한계 안내")
                                for section_id in section_ids))


def _response(sections):
    return json.dumps({"장들": {
        section_id: {"문장들": [{"글": text, "인용": [fragment_id], "등급": grade}
                              for text, fragment_id, grade in sentences]}
        for section_id, sentences in sections.items()
    }}, ensure_ascii=False)


def test_selects_only_bound_official_section_evidence():
    valid = _fragment()
    culture = _fragment("culture", CULTURE_TEXT, "2")
    business = _fragment("culture", "당사는 수익성 극대화를 목표로 투자 사업을 확대한다.", "3")
    fragments = (valid, culture, business, replace(valid, fragment_id="4", identity_binding=""),
                 replace(valid, fragment_id="5", formal_source_kind="news"),
                 replace(valid, fragment_id="6", supported_claim_slots=()))
    assert recovery_evidence(fragments) == {"past_changes": (valid,), "culture": (culture,)}


def test_recovers_two_sections_with_one_write_and_review_preserving_existing_body():
    fragments = (_fragment(), _fragment("culture", CULTURE_TEXT, "2"))
    existing = ComposedSection("identity", (ComposedSentence(
        "가나다전자는 지주회사다.", ("3",), GRADE_CONFIRMED, verification_state="verified"),))
    report = replace(_report("identity", "past_changes", "culture"),
                     sections=(existing, *_report("past_changes", "culture").sections))
    writes = []
    reviewer = _FakeReviewer()
    def writer(prompt):
        writes.append(prompt)
        return _response({"past_changes": [(PAST_TEXT, "1", GRADE_CONFIRMED)],
                          "culture": [(CULTURE_TEXT, "2", GRADE_CONFIRMED)]})
    result = recover_empty_sections("가나다전자", report, targets=("past_changes", "culture"),
                                    evidence=recovery_evidence(fragments), writer=writer, reviewer=reviewer)
    assert len(writes) == len(reviewer.prompts) == 1
    assert result.sections[0] is existing
    assert [section.sentences[0].text for section in result.sections[1:]] == [PAST_TEXT, CULTURE_TEXT]
    assert all(section.sentences[0].verification_state == "verified" for section in result.sections[1:])


@pytest.mark.parametrize("text,grade", [
    ("가나다전자의 매출은 999억원이다.", GRADE_CONFIRMED),
    ("가나다전자는 투자를 중시하는 것으로 보인다.", GRADE_INTERPRETED),
])
def test_unfounded_numbers_and_interpretations_do_not_fill_section(text, grade):
    report = _report("past_changes")
    reviewer = _FakeReviewer()
    result = recover_empty_sections("가나다전자", report, targets=("past_changes",),
        evidence=recovery_evidence((_fragment(),)), reviewer=reviewer,
        writer=lambda _: _response({"past_changes": [(text, "1", grade)]}))
    assert result == report
    assert len(reviewer.prompts) == (1 if grade == GRADE_CONFIRMED else 0)


def test_recovered_culture_sentence_must_pass_source_clause_guard():
    text = CULTURE_TEXT + " 당사는 수익성 극대화를 목표로 투자 사업을 확대한다."
    report = _report("culture")
    result = recover_empty_sections("가나다전자", report, targets=("culture",),
        evidence=recovery_evidence((_fragment("culture", text),)), reviewer=_FakeReviewer(),
        writer=lambda _: _response({"culture": [("가나다전자는 수익성 극대화를 추구하는 조직문화를 갖고 있다.", "1", GRADE_CONFIRMED)]}))
    assert result == report


def test_review_parse_failure_never_retries_provider():
    calls = []
    def reviewer(prompt):
        calls.append(prompt)
        return "깨진 응답"
    with pytest.raises(AskFatalError):
        recover_empty_sections("가나다전자", _report("past_changes"), targets=("past_changes",),
            evidence=recovery_evidence((_fragment(),)), reviewer=reviewer,
            writer=lambda _: _response({"past_changes": [(PAST_TEXT, "1", GRADE_CONFIRMED)]}))
    assert len(calls) == 1


def test_does_not_copy_existing_fact_into_empty_section():
    existing = ComposedSection("identity", (ComposedSentence(PAST_TEXT, ("1",), GRADE_CONFIRMED),))
    report = ComposedReport((existing, *_report("past_changes").sections))
    result = recover_empty_sections("가나다전자", report, targets=("past_changes",),
        evidence=recovery_evidence((_fragment(),)), reviewer=_FakeReviewer(),
        writer=lambda _: _response({"past_changes": [(PAST_TEXT, "1", GRADE_CONFIRMED)]}))
    assert result == report
    assert result.sections[0] is existing


def test_empty_section_gate_replaces_sentence_rewrite_calls():
    report = ComposedReport((ComposedSection("past_changes", (
        ComposedSentence(PAST_TEXT, ("1",), GRADE_CONFIRMED),)),))
    ask = _FakeVerifier([_verdict_json({1: "거짓"})])
    empty = []
    result = verify_report(report, (_fragment(),), None, ask,
        sentence_rewrite_gate=lambda ids: empty.append(ids) or False)
    assert empty == [("past_changes", "summary")]
    assert not result.sections[0].sentences
    assert len(ask.review_prompts) == 1 and not ask.rewrite_prompts


def test_gate_observes_section_emptied_by_machine_check():
    report = ComposedReport((ComposedSection("past_changes", (
        ComposedSentence(PAST_TEXT, ("99",), GRADE_CONFIRMED),)),))
    empty = []
    ask = _FakeReviewer()
    result = verify_report(report, (_fragment(),), None, ask,
        sentence_rewrite_gate=lambda ids: empty.append(ids) or False)
    assert "past_changes" in empty[0]
    assert not result.sections[0].sentences and not ask.prompts


def test_keeps_optional_sentence_rewrite_when_section_is_not_empty():
    report = ComposedReport((ComposedSection("past_changes", (
        ComposedSentence(PAST_TEXT, ("1",), GRADE_CONFIRMED),
        ComposedSentence("가나다전자는 물류 사업을 영위한다.", ("1",), GRADE_CONFIRMED))),))
    ask = _FakeVerifier([_verdict_json({1: "참", 2: "거짓"}), _all_true(1)],
                        rewrite_response=json.dumps({"재작성": [{"번호": 2, "글": PAST_TEXT}]}))
    empty = []
    verify_report(report, (_fragment(),), None, ask,
                  sentence_rewrite_gate=lambda ids: empty.append(ids) or True)
    assert "past_changes" not in empty[0]
    assert len(ask.rewrite_prompts) == 1


@pytest.mark.parametrize("available", [True, False])
@pytest.mark.parametrize("repeat_rejected", [True, False])
def test_pipeline_recovers_only_new_facts_with_available_calls(monkeypatch, available, repeat_rejected):
    from src.features.composer import pipeline
    from src.features.composer.constants import SECTION_IDS
    from src.features.composer.evidence_availability import EvidenceAvailability
    from src.features.composer.tests.test_pipeline import _FakeWriter

    original = PAST_TEXT if repeat_rejected else "가나다전자는 물류 사업을 합병하고 제조 사업을 영위하고 있다."
    draft = ComposedReport(tuple(ComposedSection(section_id, (
        ComposedSentence(original, ("1",), GRADE_CONFIRMED),
    ) if section_id == "past_changes" else ()) for section_id in SECTION_IDS))
    monkeypatch.setattr(pipeline, "compose_sections", lambda *args, **kwargs: draft)
    initial = _FakeVerifier([_verdict_json({1: "거짓"})])
    writer_calls = []
    recovery_reviewer = _FakeReviewer()
    def recovery_writer(prompt):
        writer_calls.append(prompt)
        return _response({"past_changes": [(PAST_TEXT, "1", GRADE_CONFIRMED)]})
    diagnostics = []
    output = pipeline.run_v2(
        "가나다전자", (_fragment(),), None, writer_ask=_FakeWriter(), reviewer_ask=initial,
        empty_recovery_writer_ask=recovery_writer,
        empty_recovery_reviewer_ask=recovery_reviewer,
        empty_recovery_can_start=lambda: available,
        evidence_availability=EvidenceAvailability("partial"),
        composition_diagnostics_sink=diagnostics,
    )
    assert len(writer_calls) == int(available)
    assert len(recovery_reviewer.prompts) == int(available and not repeat_rejected)
    past = next(section for section in output.report.sections if section.cell == "past_changes")
    assert any(PAST_TEXT in str(line) for line in past.prose_lines) is (available and not repeat_rejected)
    assert len(initial.rewrite_prompts) == (0 if available else 1)
    if available and not repeat_rejected:
        assert any(item.get("복구장") == ["past_changes"] for item in diagnostics)


def test_recovery_limits_two_sections_and_three_sentences():
    fragments = (_fragment("identity"), _fragment(), _fragment("culture", CULTURE_TEXT, "2"))
    calls = []
    def writer(prompt):
        calls.append(prompt)
        return _response({"identity": [(PAST_TEXT, "1", GRADE_CONFIRMED)] * 4,
                          "past_changes": []})
    result = recover_empty_sections("가나다전자", _report("identity", "past_changes", "culture"),
        targets=("identity", "past_changes", "culture"), evidence=recovery_evidence(fragments),
        writer=writer, reviewer=_FakeReviewer())
    assert len(calls) == 1
    assert len(result.sections[0].sentences) == 3
    assert not result.sections[2].sentences


@pytest.mark.parametrize("changed", [PAST_TEXT, PAST_TEXT.replace(" ", "  "), PAST_TEXT + " [조각 1]"])
def test_rejected_sentence_cannot_be_reapproved_with_cosmetic_changes(changed):
    report = _report("past_changes")
    reviewer = _FakeReviewer()
    result = recover_empty_sections("가나다전자", report, targets=("past_changes",),
        evidence=recovery_evidence((_fragment(),)), reviewer=reviewer,
        rejected_fingerprints={"past_changes": frozenset((rejected_sentence_fingerprint(PAST_TEXT),))},
        writer=lambda _: _response({"past_changes": [(changed, "1", GRADE_CONFIRMED)]}))
    assert result == report
    assert not reviewer.prompts
