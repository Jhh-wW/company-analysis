"""슬롯 없는 산업 후보도 기존 원문·지역·기간 검수로만 채택한다."""

from dataclasses import replace

import pytest

from src.features.official_industry_context.logic import select_candidates
from src.features.official_industry_context.tests.test_logic import material, anchor, run
from src.shared.report_evidence.industry_candidates import OfficialIndustryCandidateEvidence


def discovery():
    fragment, document = material()
    return OfficialIndustryCandidateEvidence(
        company_id=fragment.company_id, fragment_id=fragment.fragment_id,
        document=document, location=fragment.location, text_sha256=fragment.text_sha256,
        text=fragment.text,
    ), document


def test_discovery_uses_same_exact_quote_and_validation_without_slots():
    fragment, document = discovery()
    result, diagnostic, calls = run(pairs=((fragment, document),))
    assert len(result) == len(calls) == 1
    assert result[0].exact_text == result[0].assessment_quote == fragment.text
    assert not hasattr(fragment, "section_id") and not hasattr(fragment, "slot_id")
    assert result[0].document_content_sha256 == document.content_sha256


@pytest.mark.parametrize("field,value", [
    ("same_business", False), ("geography_supported", False),
    ("geography", "foreign"), ("observation_period", "2027년"),
])
def test_discovery_never_bypasses_existing_semantic_region_or_period(field, value):
    assert run(pairs=(discovery(),), modify=lambda row, _: row.update({field: value}))[0] == ()


def test_candidate_cannot_borrow_different_document_or_duplicate_identifier():
    fragment, document = discovery()
    altered = replace(document, title="다른 공식 문서")
    for pairs in (((fragment, altered),), ((fragment, document), (fragment, document))):
        assert select_candidates(candidates=pairs, anchors=(anchor(),), company_id=fragment.company_id,
                                 reference_date="2026-10-08", diagnostics={}) == ()
