"""typed 사업 범위는 정규화·raw·packet·비교 후보에서 삭제나 교체를 감지한다."""

import copy
import hashlib
import json
from dataclasses import replace

import pytest

from src.features.pipeline.comparison_transport import build_typed_comparison_candidate_inputs
from src.features.pipeline.evidence_transport import build_section_evidence_packet_set
from src.features.pipeline.official_evidence_transport_adapter import merge_official_evidence_fragments
from src.features.pipeline.tests.test_comparison_transport_dates import (
    _formal_result, _PROFILE, _CORP_CODE, _COMPANY_NAME, _COLLECTED_ON,
)
from src.features.composer.port import filing_meta_from_raw
from src.features.pipeline.tests.test_evidence_transport import _all_legacy_frags
from src.shared.report_evidence.models import DocumentTextRange
from src.features.chapter_evidence.normalize import normalize_fragments


def _result():
    result = _formal_result("dart_business_report")
    original = next(fragment for candidate in result.candidates for fragment in candidate.fragments)
    document = next(document for candidate in result.candidates for document in candidate.documents)
    heading = "[기타부문-장비]"
    full = heading + "\n" + original.text
    digest = hashlib.sha256(full.encode()).hexdigest()
    start = len(heading) + 1
    location = f"{start}-{len(full)}"
    context = json.dumps({"version": "source-section-context-v1",
        "document_id": document.document_id, "document_sha256": digest, "text": heading,
        "location": f"0-{len(heading)}", "text_sha256": hashlib.sha256(heading.encode()).hexdigest(),
        "scope_location": f"0-{len(full)}", "fragment_location": location,
        "fragment_sha256": original.text_sha256,
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    changed = replace(result, candidates=tuple(replace(candidate,
        documents=tuple(replace(item, content_sha256=digest,
            usable_ranges=(DocumentTextRange(start, len(full)),)) for item in candidate.documents),
        fragments=tuple(replace(item, location=location, section_context_json=context) for item in candidate.fragments),
    ) for candidate in result.candidates))
    return changed, context


def _packets(raw, generation):
    # packet의 기존 아홉 장 계약을 유지하며 새 문맥을 가진 typed 조각만 대조한다.
    combined = dict(raw)
    offset = max(combined)
    combined.update({number + offset: item for number, item in _all_legacy_frags().items()})
    return build_section_evidence_packet_set(corp_id=_CORP_CODE,
        source_generation_sha256=generation, frags=combined,
        filing_meta=filing_meta_from_raw({"rcept_no": "20260315000123", "report_nm": "사업보고서", "rcept_dt": "20260315"}))


def test_generation_raw_packet_and_comparison_preserve_section_context():
    result, context = _result()
    raw = merge_official_evidence_fragments({}, result)[0]
    assert all(item["section_context_json"] == context for item in raw.values())
    packets = _packets(raw, result.source_snapshot_sha256)
    fragments = [fragment for packet in packets.packets for fragment in packet.fragments if fragment.formal_source_kind]
    assert fragments and all(fragment.section_context_json == context for fragment in fragments)
    _, candidates = build_typed_comparison_candidate_inputs(raw, result=result, profile=_PROFILE,
        corp_code=_CORP_CODE, company_name=_COMPANY_NAME, collected_on=_COLLECTED_ON)
    assert candidates and all(item.section_context_json == context for item in candidates)
    assert result.source_snapshot_sha256 != _formal_result("dart_business_report").source_snapshot_sha256
    typed = next(item for candidate in result.candidates for item in candidate.fragments)
    mapping = {"company_id": typed.company_id, "fragment_id": typed.fragment_id,
        "document_id": typed.document_id, "location": typed.location, "text": typed.text,
        "text_sha256": typed.text_sha256, "section_id": typed.section_id, "slot_id": typed.slot_id,
        "score_millis": typed.score_millis, "reason_codes": typed.reason_codes,
        "section_context_json": typed.section_context_json}
    assert normalize_fragments([mapping])[0].section_context_json == context


@pytest.mark.parametrize("change", ["delete", "replace"])
def test_raw_comparison_rejects_deleted_or_cross_section_context(change):
    result, context = _result()
    raw = copy.deepcopy(merge_official_evidence_fragments({}, result)[0])
    fragment = next(iter(raw.values()))
    if change == "delete":
        fragment.pop("section_context_json")
    else:
        item = json.loads(context)
        item["text"] = "[별도부문-소재]"
        item["location"] = f"0-{len(item['text'])}"
        item["text_sha256"] = hashlib.sha256(item["text"].encode()).hexdigest()
        fragment["section_context_json"] = json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    with pytest.raises(ValueError, match="사업 범위"):
        build_typed_comparison_candidate_inputs(raw, result=result, profile=_PROFILE,
            corp_code=_CORP_CODE, company_name=_COMPANY_NAME, collected_on=_COLLECTED_ON)


def test_packet_and_transport_markers_change_when_context_is_removed():
    result, _ = _result()
    raw = merge_official_evidence_fragments({}, result)[0]
    bound = _packets(raw, result.source_snapshot_sha256)
    old = copy.deepcopy(raw)
    for item in old.values():
        item.pop("section_context_json")
    unbound = _packets(old, result.source_snapshot_sha256)
    assert bound.packet_sha256s != unbound.packet_sha256s
    assert [f.kind for p in bound.packets for f in p.fragments] != [f.kind for p in unbound.packets for f in p.fragments]
