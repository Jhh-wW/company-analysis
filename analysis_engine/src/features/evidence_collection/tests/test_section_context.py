"""사업부 표제의 원문 결속과 형제·상위 구간 경계를 검증한다."""
import hashlib
import json
from dataclasses import replace

import pytest

from features.evidence_collection.section_context import parse_section_context
from features.evidence_collection.section_scope import section_scopes, context_for_section_candidate

_TEXT = (
    "II. 사업의 내용\n[장비사업부문]\n(1) 주요 제품\n산업 설비를 생산한다.\n"
    "(2) 판매방법\n내수는 60일 어음으로 회수하며 수출은 신용장으로 결제한다.\n"
    "[소재사업부문]\n고객에게 소재를 직접 판매한다.\n"
    "3. 원재료 및 생산설비\n전체 사업의 생산설비를 설명한다."
)


def _digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def _raw(text=_TEXT, fragment="내수는 60일 어음으로 회수하며 수출은 신용장으로 결제한다."):
    start = text.index(fragment)
    return context_for_section_candidate(
        section_scopes(text), text=fragment, start=start, end=start + len(fragment),
        document_id="document-a", document_sha256=_digest(text),
    )


def test_original_sales_terms_keep_equipment_heading_and_exact_coordinates():
    raw = _raw()
    parsed = parse_section_context(raw, document_text=_TEXT)
    assert parsed["text"] == "[장비사업부문]"
    assert parsed["scope_location"].endswith("-" + str(_TEXT.index("[소재사업부문]")))
    assert _TEXT[int(parsed["fragment_location"].split("-")[0]):int(parsed["fragment_location"].split("-")[1])] == "내수는 60일 어음으로 회수하며 수출은 신용장으로 결제한다."
    assert "actor" not in parsed and "period" not in parsed


@pytest.mark.parametrize("fragment, expected", [
    ("고객에게 소재를 직접 판매한다.", "[소재사업부문]"),
    ("전체 사업의 생산설비를 설명한다.", ""),
    ("II. 사업의 내용", ""),
])
def test_sibling_and_parent_do_not_inherit_previous_business_scope(fragment, expected):
    parsed = parse_section_context(_raw(fragment=fragment), document_text=_TEXT)
    assert parsed.get("text", "") == expected


def test_unknown_bracket_section_closes_previous_business_scope():
    text = "[장비사업부문]\n장비를 생산한다.\n[참고사항]\n설명 문장이다."
    assert not _raw(text, "설명 문장이다.")


def test_cross_boundary_candidate_does_not_invent_single_business_scope():
    fragment = "신용장으로 결제한다.\n[소재사업부문]\n고객에게 소재를"
    assert not _raw(fragment=fragment)


@pytest.mark.parametrize("key, value", [
    ("document_id", "document-b"),
    ("document_sha256", "a" * 64),
    ("fragment_location", "0-2"),
    ("fragment_sha256", "b" * 64),
])
def test_transport_cannot_reassign_context_to_other_document_or_fragment(key, value):
    with pytest.raises(ValueError):
        parse_section_context(_raw(), **{key: value})


@pytest.mark.parametrize("mutation", [
    {"text": "[소재사업부문]"},
    {"scope_location": "0-1"},
    {"fragment_location": "1-999999"},
    {"document_sha256": "0" * 64},
    {"actor": "가상법인"},
    {"version": "untrusted"},
])
def test_forged_title_scope_and_unknown_authority_rejected(mutation):
    item = json.loads(_raw())
    item.update(mutation)
    changed = json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    with pytest.raises(ValueError):
        parse_section_context(changed, document_text=_TEXT)


def test_valid_new_hash_does_not_allow_scope_extension_into_next_business():
    item = json.loads(_raw())
    start = item["scope_location"].split("-")[0]
    item["scope_location"] = f"{start}-{len(_TEXT)}"
    raw = json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    with pytest.raises(ValueError):
        parse_section_context(raw, document_text=_TEXT)


def test_collect_classified_and_unclassified_contexts_survive_serialization():
    from features.evidence_collection.collect import collect_dart_evidence
    from features.evidence_collection.filing_select import RawFilingRow
    from features.evidence_collection.serialize import harvest_to_mapping
    from features.evidence_collection.tests.test_collect import _fetcher, _NOW
    text = (
        "II. 사업의 내용\n\n[장비사업부문]\n\n"
        "주요 매출은 제품 판매에서 발생하며 주요 고객사에 서비스를 제공한다.\n\n"
        "별빛과 구름에 관한 독립적인 설명을 적어 둔 긴 문장으로 특별한 사항은 없다.\n\n"
        "[소재사업부문]\n\n고객에게 소재를 직접 판매하여 판매대금을 회수한다."
    )
    row = RawFilingRow("20250315000001", "사업보고서 (2025.03)", "20250315")
    harvest = collect_dart_evidence(_fetcher("A", row, text), "00000001", now=_NOW)
    payload = harvest_to_mapping(harvest)
    assert harvest.fragments and harvest.unclassified_fragments
    for lane in ("", "unclassified_"):
        fragments = payload[lane + "fragments"]
        contexts = [fragment for fragment in fragments if fragment.get("section_context_json")]
        assert contexts
        documents = {doc["document_id"]: doc for doc in payload[lane + "documents"]}
        for fragment in contexts:
            doc = documents[fragment["document_id"]]
            context = parse_section_context(
                fragment["section_context_json"], document_text=text,
                document_id=doc["document_id"], document_sha256=doc["content_sha256"],
                fragment_location=fragment["location"], fragment_sha256=fragment["text_sha256"],
            )
            binding = {
                "location": fragment["location"], "text_sha256": fragment["text_sha256"],
                "section_context_sha256": _digest(fragment["section_context_json"]),
            }
            assert binding in doc["exact_section_context_bindings"]
            start, end = map(int, context["fragment_location"].split("-"))
            assert text[start:end] == fragment["text"]
    altered = replace(harvest.documents[0], content_sha256="f" * 64)
    with pytest.raises(ValueError):
        replace(harvest, documents=(altered,))


@pytest.mark.parametrize("heading", ("3.원재료 및 생산설비", "3．Raw material and equipment"))
def test_parent_heading_without_space_closes_previous_business_scope(heading):
    text = "[장비사업부문]\n장비를 생산한다.\n" + heading + "\n전체 사업의 설비를 설명한다."
    assert not _raw(text, "전체 사업의 설비를 설명한다.")


def test_decimal_and_subsection_do_not_close_business_scope():
    text = "[장비사업부문]\n3.14 비율 설명\n(2) 판매조건\n국내 고객에게 직접 판매한다."
    raw = _raw(text, "국내 고객에게 직접 판매한다.")
    assert parse_section_context(raw, document_text=text)["text"] == "[장비사업부문]"


def test_retained_business_scope_budget_overflow_records_incomplete_collection():
    from features.evidence_collection.collect import collect_dart_evidence
    from features.evidence_collection.filing_select import RawFilingRow
    from features.evidence_collection.section_context_constants import MAX_RETAINED_SCOPES
    from features.evidence_collection.tests.test_collect import _fetcher, _NOW
    text = "[장비사업부문]\n주요 고객에게 제품을 직접 판매한다.\n" * (MAX_RETAINED_SCOPES + 1)
    row = RawFilingRow("20250315000001", "사업보고서 (2025.03)", "20250315")
    harvest = collect_dart_evidence(_fetcher("A", row, text), "00000001", now=_NOW)
    assert not harvest.fragments and not harvest.unclassified_fragments
    assert any(attempt.reason_code == "source_context_budget_exceeded" for attempt in harvest.attempts)


def test_many_unretained_headings_do_not_remove_late_business_context():
    from features.evidence_collection.section_context_constants import MAX_RETAINED_SCOPES
    text = "[참고]\n" * (MAX_RETAINED_SCOPES + 1) + "[장비사업부문]\n국내 고객에게 직접 판매한다."
    assert len(section_scopes(text)) == 1
    assert parse_section_context(_raw(text, "국내 고객에게 직접 판매한다."), document_text=text)["text"] == "[장비사업부문]"
