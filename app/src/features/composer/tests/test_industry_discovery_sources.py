"""별도 산업 원문이 작성 근거나 회사 사실로 승격되지 않는 최종 경계."""

from dataclasses import replace

import pytest

from src.features.composer import pipeline
from src.features.composer.industry_context import discovery_fragments_for_supplement
from src.features.composer.tests import test_official_industry_fallback as fallback_fixture
from src.features.composer.tests.test_official_industry_context import _official_materials
from src.shared.report_evidence.constants import SourceRequirement, SourceTier
from src.shared.report_evidence.industry_candidates import OfficialIndustryCandidateEvidence, OfficialIndustrySupplement
from src.shared.report_evidence.models import CollectedEvidenceDocument, DocumentTextRange


def materials():
    company, (anchor_fragment, problem_fragment), anchor, problem, composed = _official_materials()
    location = f"0-{len(problem.exact_text)}"
    problem = replace(problem, location=location)
    document = CollectedEvidenceDocument(
        company_id=anchor.company_id, document_id=problem.document_id, canonical_url=problem.source_url,
        source_tier=SourceTier.TIER_1_OFFICIAL, source_kind=problem.source_kind, publisher=problem.publisher,
        title=problem.title, published_on=problem.published_on, collected_at=problem.published_on,
        content_sha256=problem.document_content_sha256, exact_evidence_hashes=(problem.text_sha256,),
        identity_binding=problem.identity_binding, usable_ranges=(DocumentTextRange(0, len(problem.exact_text)),),
        collector_version="synthetic-v1", parser_version="synthetic-v1", requirement=SourceRequirement.OPTIONAL,
    )
    candidate = OfficialIndustryCandidateEvidence(
        anchor.company_id, "discovery-1", document, location, problem.text_sha256, problem.exact_text,
    )
    return company, anchor_fragment, anchor, problem, composed, candidate


def test_discovery_is_registered_only_after_final_body_and_keeps_original_source(monkeypatch):
    company, fragment, anchor, problem, composed, candidate = materials()
    monkeypatch.setattr(fallback_fixture, "_official_materials", lambda: (company, (fragment,), anchor, problem, composed))
    calls = []
    def factory(_):
        def callback(selected):
            calls.append(selected)
            assert len(selected) == 1 and all(value.text != candidate.text for value in selected)
            return OfficialIndustrySupplement((problem,), (candidate,))
        return callback
    result = fallback_fixture.final_partial(factory)
    section = next(value for value in result.report.sections if value.cell == "current_challenges")
    context, = section.industry_contexts
    assert context.problem.exact_text == candidate.text
    assert context.problem.location == candidate.location
    assert len(calls) == 1 and not section.fact_ids and not section.is_filled
    assert all(value.claim_slot != "current_challenges:issue" for value in result.report.fact_records)
    source = next(value for value in result.report.citations if value.source_id == context.problem.source_id)
    assert candidate.text_sha256 in source.exact_evidence_hashes
    assert source.document_content_sha256 == candidate.document.content_sha256


def test_discovery_has_no_writer_slots_or_document_floor():
    _, fragment, anchor, problem, _, candidate = materials()
    extra, = discovery_fragments_for_supplement(
        OfficialIndustrySupplement((problem,), (candidate,)), fragments=(fragment,), company_id=anchor.company_id,
    )
    assert not extra.supported_claim_slots and not extra.counts_toward_document_floor
    assert int(extra.fragment_id) > int(fragment.fragment_id)
    assert not hasattr(candidate, "slot_id") and not hasattr(candidate, "section_id")


@pytest.mark.parametrize("field,value", [("text", "다른 원문"), ("location", "1-2"), ("company_id", "다른회사")])
def test_candidate_rejects_unbound_original(field, value):
    candidate = materials()[-1]
    with pytest.raises(ValueError):
        replace(candidate, **{field: value})


def test_extra_original_does_not_repair_a_missing_selected_business_anchor():
    _, fragment, anchor, problem, report, candidate = materials()
    from src.features.composer.port import ComposedSentence
    report = replace(report, sections=tuple(
        replace(section, sentences=(ComposedSentence("검수된 회사 설명", (fragment.fragment_id,), "확인", verification_state="verified"),))
        if section.section_id == "identity" else section for section in report.sections
    ))
    diagnostics, sink = [], []
    result = pipeline._late_official_industry_problems(
        report, callback=lambda selected: OfficialIndustrySupplement((problem,), (candidate,)),
        anchors=(anchor,), problems=(), fragments=(), company_id=anchor.company_id,
        diagnostics=diagnostics, discovery_fragments_sink=sink,
    )
    assert result == () and sink == []
    assert diagnostics[-1]["상태"] == "결속불가"
