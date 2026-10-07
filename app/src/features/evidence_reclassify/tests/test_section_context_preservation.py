"""재분류는 자기 원문에 붙은 사업부 제목을 지우거나 다른 제목으로 바꾸지 않는다."""

import hashlib
import json

import pytest

from src.features.evidence_reclassify.logic import parse_and_verify, to_typed_fragments
from src.features.evidence_reclassify.tests.test_logic import _candidate, _assignment, _response


def _candidate_with_context():
    candidate = _candidate()
    heading = "[기타부문-센서]"
    candidate["section_context_json"] = json.dumps({
        "version": "source-section-context-v1", "document_id": candidate["document_id"],
        "document_sha256": "a" * 64, "text": heading,
        "location": f"0-{len(heading)}", "text_sha256": hashlib.sha256(heading.encode()).hexdigest(),
        "scope_location": "0-1000", "fragment_location": candidate["location"],
        "fragment_sha256": candidate["text_sha256"],
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return candidate


def test_reclassified_fragment_preserves_exact_context_and_original_text_coordinates():
    candidate = _candidate_with_context()
    result = parse_and_verify(_response(_assignment()), [candidate])
    fragment, = to_typed_fragments(result, {})
    assert fragment["section_context_json"] == candidate["section_context_json"]
    assert fragment["text"] == candidate["text"] and fragment["location"] == candidate["location"]
    assert fragment["text_sha256"] == candidate["text_sha256"]


@pytest.mark.parametrize("change", ["erase", "cross_heading", "cross_document"])
def test_source_record_cannot_erase_or_replace_candidate_context(change):
    candidate = _candidate_with_context()
    result = parse_and_verify(_response(_assignment()), [candidate])
    source = dict(candidate)
    if change == "erase":
        source["section_context_json"] = ""
    elif change == "cross_document":
        source["content_sha256"] = "b" * 64
    else:
        item = json.loads(candidate["section_context_json"])
        item["text"] = "[기타부문-소재]"
        item["text_sha256"] = hashlib.sha256(item["text"].encode()).hexdigest()
        source["section_context_json"] = json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    with pytest.raises(ValueError):
        to_typed_fragments(result, source)
