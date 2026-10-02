"""이미 검증한 typed 법인 문맥을 비교용 raw 전달에서 삭제하지 못한다."""

import copy
import hashlib
import json
from dataclasses import replace

import pytest

from src.features.pipeline.comparison_transport import build_typed_comparison_candidate_inputs
from src.features.pipeline.official_evidence_transport_adapter import merge_official_evidence_fragments
from src.features.pipeline.tests.test_comparison_transport_dates import (
    _formal_result, _PROFILE, _CORP_CODE, _COMPANY_NAME, _COLLECTED_ON,
)


def _with_context():
    result = _formal_result("dart_business_report")
    original = next(fragment for candidate in result.candidates for fragment in candidate.fragments)
    actor = "다온설비주식회사"
    text = " | ".join((actor, "산업장비", original.text))
    payload = {"version": "source-context-v1", "text": text,
        "location": f"100-{100+len(text)}", "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "actor": actor, "document_actor": _COMPANY_NAME, "document_actor_location": f"0-{len(_COMPANY_NAME)}",
        "document_actor_sha256": hashlib.sha256(_COMPANY_NAME.encode()).hexdigest(), "origin": "table_row", "status": ""}
    context = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    changed = replace(result, candidates=tuple(
        replace(candidate, fragments=tuple(replace(fragment, source_context_json=context) for fragment in candidate.fragments))
        for candidate in result.candidates
    ))
    return changed, context


def _build(fragments, result):
    return build_typed_comparison_candidate_inputs(fragments, result=result, profile=_PROFILE,
        corp_code=_CORP_CODE, company_name=_COMPANY_NAME, collected_on=_COLLECTED_ON)


def test_classified_comparison_transport_preserves_source_context_and_generation_fingerprint():
    result, context = _with_context()
    raw = merge_official_evidence_fragments({}, result)[0]
    assert all(item["source_context_json"] == context for item in raw.values())
    _sources, rows = _build(raw, result)
    assert all(row.source_context_json == context for row in rows)
    assert result.source_snapshot_sha256 != _formal_result("dart_business_report").source_snapshot_sha256


@pytest.mark.parametrize("change", ["delete", "replace"])
def test_comparison_raw_transport_rejects_context_deletion_or_replacement(change):
    result, _context = _with_context()
    raw = copy.deepcopy(merge_official_evidence_fragments({}, result)[0])
    first = next(iter(raw.values()))
    if change == "delete":
        first.pop("source_context_json")
    else:
        payload = json.loads(first["source_context_json"])
        payload["actor"] = "산업장비"
        first["source_context_json"] = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    with pytest.raises(ValueError, match="회사 주어 문맥을 삭제하거나 바꿨습니다"):
        _build(raw, result)
