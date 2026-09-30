"""작성 응답의 같은 의미칸 선택과 기존 파서·문맥 운반을 검증한다."""

from __future__ import annotations

from dataclasses import asdict
import hashlib
import json

from jsonschema import Draft202012Validator
import pytest

from src.features.composer.constants import RETRY_REMINDER
from src.features.composer.evidence_pair_selection import build_evidence_pair_map
from src.features.composer.logic import (
    CacheablePrompt, _normalize_packet_fragments, build_section_prompt,
    parse_section_response,
)
from src.features.composer.port import CollectedFragment, fragments_from_raw
from src.features.composer.writer_schema import build_full_writer_schema, with_full_writer_schema
from src.features.composer.writer_schema_constants import FULL_REQUIRED_SLOT_SENTENCE_GUIDE


PAIRS = {
    "p2-001": ("business_model:customer_type", "1"),
    "p2-002": ("business_model:customer_type", "2"),
    "p2-003": ("business_model:value_exchange", "3"),
    "p2-004": ("business_model:sales_channel", "3"),
}


def _payload(selected):
    return {"필수내용": {"business_model:customer_type": [], "business_model:value_exchange": []},
            "문장들": [{"글": "회사는 제조업 고객에게 장비를 제공한다.",
                     "등급": "확인", "근거선택": selected}]}


@pytest.mark.parametrize("selected,allowed", [
    (["p2-001"], True), (["p2-001", "p2-002"], True),
    (["p2-003"], True), (["p2-001", "p2-004"], False),
    (["p2-003", "p2-004"], False), (["p2-999"], False), ([], False),
])
def test_native_schema_closes_each_sentence_to_one_supported_slot(selected, allowed):
    validator = Draft202012Validator(build_full_writer_schema("business_model", PAIRS))
    assert validator.is_valid(_payload(selected)) is allowed
    parsed = parse_section_response(json.dumps(_payload(selected), ensure_ascii=False),
                                    "business_model", evidence_pairs=PAIRS)
    assert bool(parsed) is allowed


def test_schema_does_not_promote_selected_pair_to_verified_fact():
    # 같은 의미칸의 잘못된 번호는 형식만 통과하고 이후 원문 검수가 판단한다.
    result = parse_section_response(json.dumps(_payload(["p2-003"]), ensure_ascii=False),
                                    "business_model", evidence_pairs=PAIRS)
    assert result[0].citations == ("3",)
    assert result[0].planned_claim_slot == "business_model:value_exchange"
    assert result[0].verification_state == "unverified"


def test_schema_keeps_empty_result_and_optional_flow_news_without_forcing_content():
    schema = build_full_writer_schema("business_model", PAIRS, news_fragment_ids=("3",))
    validator = Draft202012Validator(schema)
    empty = {"문장들": [], "필수내용": {"business_model:customer_type": [], "business_model:value_exchange": []}}
    assert validator.is_valid(empty)
    assert not validator.is_valid({"문장들": []})
    assert validator.is_valid({**empty, "경로표": [], "뉴스근거판정": [
        {"조각": "3", "사유": "장무관", "설명": "이 장의 사실과 관련이 없다."}]})
    assert not validator.is_valid({**empty, "뉴스근거판정": [
        {"조각": "99", "사유": "장무관", "설명": "관련이 없다."}]})
    assert not validator.is_valid({**empty, "임의필드": []})


def test_required_slot_arrays_reach_existing_unverified_parser_in_policy_order():
    data = _payload(["p2-004"])
    data["필수내용"]["business_model:customer_type"] = [
        {"글": "장비의 고객은 제조업체다.", "등급": "확인", "근거선택": ["p2-001", "p2-002"]}]
    data["필수내용"]["business_model:value_exchange"] = [
        {"글": "회사는 장비를 납품하고 판매대금을 받는다.", "등급": "확인", "근거선택": ["p2-003"]}]
    assert Draft202012Validator(build_full_writer_schema("business_model", PAIRS)).is_valid(data)
    parsed = parse_section_response(json.dumps(data), "business_model", evidence_pairs=PAIRS)
    assert [row.planned_claim_slot for row in parsed] == [
        "business_model:customer_type", "business_model:value_exchange", "business_model:sales_channel"]
    assert all(row.verification_state == "unverified" for row in parsed)
    assert parsed[0].citations == ("1", "2")


@pytest.mark.parametrize("selected", [["p2-004"], ["p2-001", "p2-003"], ["p2-999"], []])
def test_required_array_cannot_relabel_wrong_pair_or_inject_evidence(selected):
    data = _payload(["p2-001"])
    data["문장들"] = []
    data["필수내용"]["business_model:value_exchange"] = [
        {"글": "대가 칸을 임의로 채우려는 문장이다.", "등급": "확인", "근거선택": selected}]
    assert not Draft202012Validator(build_full_writer_schema("business_model", PAIRS)).is_valid(data)
    assert parse_section_response(json.dumps(data), "business_model", evidence_pairs=PAIRS) == ()


def test_required_arrays_allow_no_facts_and_legacy_replay_is_unchanged():
    data = _payload(["p2-003"])
    legacy = {"문장들": data["문장들"]}
    assert parse_section_response(json.dumps(data), "business_model", evidence_pairs=PAIRS) == \
        parse_section_response(json.dumps(legacy), "business_model", evidence_pairs=PAIRS)
    data["문장들"] = []
    assert parse_section_response(json.dumps(data), "business_model", evidence_pairs=PAIRS) == ()
    data["필수내용"]["other:invented"] = []
    assert parse_section_response(json.dumps(data), "business_model", evidence_pairs=PAIRS) is None


def test_program_injected_and_unsupported_slots_are_not_required_in_writer_schema():
    pairs = {"p4-001": ("past_changes:completed_execution", "1")}
    schema = build_full_writer_schema("past_changes", pairs)
    assert schema["properties"]["필수내용"]["required"] == ["past_changes:completed_execution"]
    assert "past_changes:historical_performance" not in schema["properties"]["필수내용"]["properties"]


def test_wrapper_preserves_cache_boundary_on_retry_and_disables_only_prefixed_cache():
    old = CacheablePrompt("공유자료\n장별지침", cache_prefix_chars=5)
    prompt = with_full_writer_schema(old, "business_model", PAIRS)
    assert str(prompt) == str(old) and prompt.cache_prefix_chars == 5
    retry = prompt + RETRY_REMINDER
    assert retry.response_schema is prompt.response_schema
    assert retry.cache_prefix_chars == prompt.cache_prefix_chars
    supplement = "보충작성\n" + prompt
    assert supplement.response_schema is prompt.response_schema
    assert supplement.cache_prefix_chars == 0


@pytest.mark.parametrize("shared", [False, True])
def test_full_writer_builder_has_schema_but_legacy_and_empty_pairs_keep_old_bytes(shared):
    fragments = (CollectedFragment("1", "공식자료", "회사는 제조업 고객에게 장비를 공급한다.",
                                   supported_claim_slots=("business_model:customer_type",)),)
    full = build_section_prompt("합성회사", "business_model", fragments, None,
                                show_supported_claim_slots=True, shared_evidence_prefix=shared)
    expected = build_full_writer_schema("business_model", build_evidence_pair_map("business_model", fragments))
    assert full.response_schema == expected
    assert FULL_REQUIRED_SLOT_SENTENCE_GUIDE in full
    assert bool(full.cache_prefix_chars) is shared
    legacy = build_section_prompt("합성회사", "business_model", fragments, None,
                                  shared_evidence_prefix=shared)
    assert not hasattr(legacy, "response_schema")
    assert FULL_REQUIRED_SLOT_SENTENCE_GUIDE not in legacy
    assert with_full_writer_schema(legacy, "business_model", {}) is legacy
    assert build_full_writer_schema("business_model", {}) is None
    empty = build_section_prompt("합성회사", "business_model", (), None,
                                 show_supported_claim_slots=True, shared_evidence_prefix=shared)
    assert not hasattr(empty, "response_schema")
    assert FULL_REQUIRED_SLOT_SENTENCE_GUIDE not in empty


@pytest.mark.parametrize("section,slot", [
    ("business_model", "business_model:customer_type"),
    ("current_challenges", "current_challenges:issue"),
    ("operations_partners", "operations_partners:operating_role"),
])
def test_full_flow_instructions_name_all_three_keys_without_changing_legacy(section, slot):
    fragments = (CollectedFragment("1", "공식자료", "회사는 산업용 장비를 제조한다.",
                                   supported_claim_slots=(slot,)),)
    full = build_section_prompt("합성회사", section, fragments, None, show_supported_claim_slots=True)
    assert "«두 키를 모두»" not in full
    assert "«필수내용·문장들·경로표»를 모두 넣는다" in full
    legacy = build_section_prompt("합성회사", section, fragments, None)
    assert "«두 키를 모두»" in legacy


def _context():
    actor = "합성설비주식회사"
    text = " | ".join((actor, "장비", "개발 완료, 양산 예정"))
    return json.dumps({
        "version": "source-context-v1", "text": text, "location": f"100-{100+len(text)}",
        "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "actor": actor, "document_actor": actor, "document_actor_location": f"0-{len(actor)}",
        "document_actor_sha256": hashlib.sha256(actor.encode()).hexdigest(),
        "origin": "table_row", "status": "개발 완료, 양산 예정", "item": "장비",
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


@pytest.mark.parametrize("context", ["", _context()])
def test_mapping_packet_reconstruction_keeps_exact_actor_status_context(context):
    raw = {1: {"종류": "공식자료", "원문": "회사는 장비 개발을 완료했다.",
               "출처": "https://example.test/report", "문서일": "2026-09-30",
               **({"source_context_json": context} if context else {})}}
    source = fragments_from_raw(raw)[0]
    normalized = _normalize_packet_fragments(raw)[0]
    expected = asdict(source)
    expected["document_date"] = "2026-09-30"
    assert asdict(normalized) == expected
    assert normalized.source_context_json.encode() == context.encode()
