"""분류·무분류 문맥은 문서 선언과 조각 위치·원문 해시를 모두 대조한다."""

import copy
import hashlib
import json

import pytest

from src.web.official_evidence_adapter import (
    _classified_evidence_location_bindings, _comparison_candidate_evidence,
    _unclassified_evidence_observation,
)
from src.web.tests.test_official_source_context_binding import _envelope, _unclassified_envelope


def _sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _with_section(unclassified=False):
    envelope = _unclassified_envelope() if unclassified else _envelope()
    prefix = "unclassified_" if unclassified else ""
    document, fragment = envelope[prefix + "documents"][0], envelope[prefix + "fragments"][0]
    heading = "[기타부문-장비]"
    end = int(fragment["location"].split("-")[1])
    document["content_sha256"] = "a" * 64
    raw = json.dumps({
        "version": "source-section-context-v1", "document_id": document["document_id"],
        "document_sha256": document["content_sha256"], "text": heading,
        "location": f"0-{len(heading)}", "text_sha256": _sha(heading),
        "scope_location": f"0-{end}", "fragment_location": fragment["location"],
        "fragment_sha256": fragment["text_sha256"],
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    fragment["section_context_json"] = raw
    document["exact_section_context_bindings"] = [{
        "location": fragment["location"], "text_sha256": fragment["text_sha256"],
        "section_context_sha256": _sha(raw),
    }]
    return envelope


@pytest.mark.parametrize("unclassified", [False, True])
def test_document_declared_section_context_accepts_classified_and_unclassified(unclassified):
    envelope = _with_section(unclassified)
    if unclassified:
        candidate, = _comparison_candidate_evidence(envelope, company_id="00000001")
        assert candidate.section_context_json == envelope["unclassified_fragments"][0]["section_context_json"]
    else:
        _classified_evidence_location_bindings(envelope, company_id="00000001")


@pytest.mark.parametrize("unclassified", [False, True])
@pytest.mark.parametrize("change", ["delete", "heading", "document_id", "document_sha256", "fragment_location", "fragment_sha256", "sidecar", "extra_sidecar", "null"])
def test_context_tampering_is_rejected_even_when_original_fragment_hash_is_unchanged(unclassified, change):
    envelope = _with_section(unclassified)
    prefix = "unclassified_" if unclassified else ""
    document, fragment = envelope[prefix + "documents"][0], envelope[prefix + "fragments"][0]
    if change == "delete":
        fragment.pop("section_context_json")
    elif change == "null":
        fragment["section_context_json"] = None
    elif change == "sidecar":
        document.pop("exact_section_context_bindings")
    elif change == "extra_sidecar":
        document["exact_section_context_bindings"].append(copy.deepcopy(document["exact_section_context_bindings"][0]))
    else:
        context = json.loads(fragment["section_context_json"])
        if change == "heading":
            context["text"] = "[기타부문-소재]"
            context["text_sha256"] = _sha(context["text"])
        else:
            context[change] = {"document_id": "dart:other", "document_sha256": "b"*64,
                "fragment_location": "1-2", "fragment_sha256": "f"*64}[change]
        fragment["section_context_json"] = json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    with pytest.raises(ValueError):
        if unclassified:
            _unclassified_evidence_observation(envelope, company_id="00000001")
        else:
            _classified_evidence_location_bindings(envelope, company_id="00000001")


def test_unclassified_observation_fingerprint_binds_context_without_promoting_claims():
    envelope = _with_section(True)
    bound = _unclassified_evidence_observation(envelope, company_id="00000001")
    old = copy.deepcopy(envelope)
    old["unclassified_documents"][0].pop("exact_section_context_bindings")
    old["unclassified_fragments"][0].pop("section_context_json")
    legacy = _unclassified_evidence_observation(old, company_id="00000001")
    assert bound.observation_sha256 != legacy.observation_sha256
    assert envelope["unclassified_fragments"][0]["section_id"] == ""
