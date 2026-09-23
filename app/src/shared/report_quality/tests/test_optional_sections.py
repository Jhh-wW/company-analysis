"""선택 9장의 실측은 보존하고 필수 8장·과거 영수증 계약은 바꾸지 않는다."""

from dataclasses import replace

import pytest

from src.shared.generation_validation_receipt import generation_assessment_from_dict, generation_assessment_to_dict
from src.shared.report_quality.dto import ReportCandidate, ReportSectionCandidate
from src.shared.report_quality.generation import (
    assess_and_observe_generation, assert_observation_matches_assessment,
    generation_quality_observation_from_dict, generation_quality_observation_to_dict,
)
from src.shared.report_quality.optional_sections import OPTIONAL_SECTIONS_VERSION


def _observed():
    return assess_and_observe_generation(ReportCandidate(
        sections=(ReportSectionCandidate(
            "competitive_position", (), public_sentence_count=2,
            has_unbound_public_content=True,
        ),), facts=(), sources=(),
    ))


def test_optional_ninth_has_counts_and_safety_without_changing_required_eight():
    assessment, observation = _observed()
    assert len(observation.section_public_sentence_counts) == 8
    assert observation.optional_sections_version == OPTIONAL_SECTIONS_VERSION
    optional = observation.optional_sections[0]
    assert optional.section_id == "competitive_position"
    assert optional.public_sentence_count == 2
    assert optional.has_unbound_public_content
    assert not observation.release_allowed
    assert any("competitive_position" in problem for problem in observation.safety_problems)
    assert_observation_matches_assessment(observation, assessment)


def test_observation_and_assessment_roundtrip_new_and_legacy_wire():
    assessment, observation = _observed()
    assert generation_assessment_from_dict(generation_assessment_to_dict(assessment)) == assessment
    assert generation_quality_observation_from_dict(generation_quality_observation_to_dict(observation)) == observation
    old_assessment = replace(assessment, quality=replace(assessment.quality, optional_sections_version="", optional_sections=()))
    old_observation = replace(observation, optional_sections_version="", optional_sections=())
    old_wire = generation_assessment_to_dict(old_assessment)
    assert "optional_sections" not in old_wire["quality"]
    assert generation_assessment_to_dict(generation_assessment_from_dict(old_wire)) == old_wire
    old_obs_wire = generation_quality_observation_to_dict(old_observation)
    assert "optional_sections" not in old_obs_wire
    assert generation_quality_observation_to_dict(generation_quality_observation_from_dict(old_obs_wire)) == old_obs_wire


@pytest.mark.parametrize("mutation", ["partial_keys", "unknown_version", "bool_count", "duplicate", "extra_field"])
def test_optional_wire_rejects_tamper(mutation):
    _, observation = _observed()
    raw = generation_quality_observation_to_dict(observation)
    if mutation == "partial_keys":
        del raw["optional_sections_version"]
    elif mutation == "unknown_version":
        raw["optional_sections_version"] = "future-version"
    elif mutation == "bool_count":
        raw["optional_sections"][0]["public_sentence_count"] = True
    elif mutation == "duplicate":
        raw["optional_sections"].append(dict(raw["optional_sections"][0]))
    else:
        raw["optional_sections"][0]["raw_text"] = "저장하지 않을 값"
    with pytest.raises(ValueError):
        generation_quality_observation_from_dict(raw)


def test_observation_cannot_change_optional_count_after_assessment():
    assessment, observation = _observed()
    changed = replace(observation, optional_sections=(replace(observation.optional_sections[0], public_sentence_count=3),))
    with pytest.raises(ValueError):
        assert_observation_matches_assessment(changed, assessment)
