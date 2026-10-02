"""문맥 메타 삭제·교체를 기존 원문 해시만으로 통과시키지 않는다."""

import copy
import hashlib
import json

import pytest

from src.web.official_evidence_adapter import (
    _classified_evidence_location_bindings, _comparison_candidate_evidence,
    _unclassified_evidence_observation,
)


def _envelope():
    owner, actor, product = "가람제조주식회사", "다온설비주식회사", "산업장비"
    text = "장비를 개발한다."
    context_text = " | ".join((actor, product, text, "양산 적용 예정"))
    context = {
        "version": "source-context-v1", "text": context_text,
        "location": f"100-{100+len(context_text)}",
        "text_sha256": hashlib.sha256(context_text.encode()).hexdigest(),
        "actor": actor, "document_actor": owner,
        "document_actor_location": f"0-{len(owner)}",
        "document_actor_sha256": hashlib.sha256(owner.encode()).hexdigest(),
        "origin": "table_row", "status": "양산 적용 예정", "item": product,
    }
    raw = json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    start = 100 + context_text.index(text)
    location = f"{start}-{start+len(text)}"
    digest = hashlib.sha256(text.encode()).hexdigest()
    return {
        "documents": [{
            "company_id": "00000001", "document_id": "dart:test",
            "source_kind": "dart_business_report", "canonical_url": "https://dart.fss.or.kr/test",
            "usable_ranges": [{"start": start, "end": start+len(text)}],
            "exact_evidence_hashes": [digest],
            "exact_evidence_bindings": [{"location": location, "text_sha256": digest}],
            "exact_source_context_bindings": [{"location": location, "text_sha256": digest,
                "source_context_sha256": hashlib.sha256(raw.encode()).hexdigest()}],
        }],
        "fragments": [{"document_id": "dart:test", "location": location,
            "text_sha256": digest, "text": text, "source_context_json": raw}],
    }


def test_exact_document_declared_source_context_is_accepted():
    _classified_evidence_location_bindings(_envelope(), company_id="00000001")


@pytest.mark.parametrize("change", ["delete", "actor", "status", "item"])
def test_document_source_context_binding_rejects_deleted_or_replaced_metadata(change):
    envelope = copy.deepcopy(_envelope())
    fragment = envelope["fragments"][0]
    if change == "delete":
        fragment.pop("source_context_json")
    else:
        context = json.loads(fragment["source_context_json"])
        # 다른 완전 셀이어도 최초 생산 메타의 결속과 달라지면 거절한다.
        context[change] = "산업장비" if change == "actor" else ""
        fragment["source_context_json"] = json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    with pytest.raises(ValueError, match="회사 주어 문맥 결속"):
        _classified_evidence_location_bindings(envelope, company_id="00000001")


def test_empty_legacy_context_does_not_add_new_binding_requirement():
    envelope = _envelope()
    envelope["fragments"][0].pop("source_context_json")
    envelope["documents"][0].pop("exact_source_context_bindings")
    _classified_evidence_location_bindings(envelope, company_id="00000001")


def _unclassified_envelope():
    envelope = _envelope()
    document, fragment = envelope["documents"][0], envelope["fragments"][0]
    text = "당사는 베타와 경쟁 관계에 있습니다."
    context = json.loads(fragment["source_context_json"])
    context["text"] = context["text"].replace(fragment["text"], text)
    context["location"] = f"100-{100+len(context['text'])}"
    context["text_sha256"] = hashlib.sha256(context["text"].encode()).hexdigest()
    raw = json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    start = 100 + context["text"].index(text)
    location, digest = f"{start}-{start+len(text)}", hashlib.sha256(text.encode()).hexdigest()
    receipt = "20260315000001"
    document.update({
        "document_id": f"dart_business_report:{receipt}",
        "canonical_url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt}",
        "source_tier": "TIER_1_OFFICIAL", "publisher": "전자공시시스템",
        "title": "사업보고서 (2025.12)", "published_on": "20260315",
        "collected_at": "2026-09-30T00:00:00+09:00", "content_sha256": "a"*64,
        "identity_binding": f"corp_code=00000001;rcept_no={receipt};source_kind=dart_business_report;identity_check=verified_match",
        "collector_version": "evidence_collection/2.5", "parser_version": "evidence_collection_segment/2.1",
        "requirement": "REQUIRED", "exact_evidence_hashes": [], "exact_evidence_bindings": [],
        "usable_ranges": [{"start": start, "end": start+len(text)}],
        "exact_source_context_bindings": [{"location": location, "text_sha256": digest,
            "source_context_sha256": hashlib.sha256(raw.encode()).hexdigest()}],
    })
    fragment.update({"company_id": "00000001", "fragment_id": "raw:1", "document_id": document["document_id"],
        "location": location, "text": text, "text_sha256": digest, "source_context_json": raw,
        "section_id": "", "slot_id": "", "covered_slot_ids": [], "score_millis": 0, "reason_codes": ["no_signal"]})
    return {"unclassified_documents": [document], "unclassified_fragments": [fragment]}


def test_unclassified_comparison_lane_keeps_context_in_dto_and_observation():
    envelope = _unclassified_envelope()
    candidates = _comparison_candidate_evidence(envelope, company_id="00000001")
    assert len(candidates) == 1
    assert candidates[0].source_context_json == envelope["unclassified_fragments"][0]["source_context_json"]
    baseline = _unclassified_evidence_observation(envelope, company_id="00000001")
    legacy = copy.deepcopy(envelope)
    legacy["unclassified_documents"][0].pop("exact_source_context_bindings")
    legacy["unclassified_fragments"][0].pop("source_context_json")
    assert _unclassified_evidence_observation(legacy, company_id="00000001").observation_sha256 != baseline.observation_sha256


@pytest.mark.parametrize("change", ["delete", "actor"])
def test_unclassified_comparison_context_deletion_or_cell_replacement_is_rejected(change):
    envelope = _unclassified_envelope()
    fragment = envelope["unclassified_fragments"][0]
    if change == "delete":
        fragment.pop("source_context_json")
    else:
        context = json.loads(fragment["source_context_json"])
        context["actor"] = context["item"]
        fragment["source_context_json"] = json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    with pytest.raises(ValueError, match="무분류 회사 주어 문맥 결속"):
        _comparison_candidate_evidence(envelope, company_id="00000001")
