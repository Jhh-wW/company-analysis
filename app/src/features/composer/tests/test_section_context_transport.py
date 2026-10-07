"""사업부 제목은 자기 원문에 결속해 작성·검수·봉인에 같은 값으로 전달한다."""

import hashlib
import json
from dataclasses import replace

import pytest

from src.features.composer.flow_review_binding import bind_reviewed_flow_row, flow_review_problem
from src.features.composer.logic import _render_fragments
from src.features.composer.numeric_proof_selection import _fragment_snapshot
from src.features.composer.port import CollectedFragment, FlowRow, fragments_from_raw
from src.features.composer.section_context_constants import SECTION_CONTEXT_LABEL
from src.features.composer.verify import _review_fragment_metadata


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _source(heading="[기타부문-장비]", *, context=True):
    text = "국내 대금은 현금으로 받으며 수출 대금은 신용장으로 받습니다."
    document = heading + "\n" + text + "\n[별도부문-소재]\n소재를 판매합니다."
    start = len(heading) + 1
    location = f"{start}-{start+len(text)}"
    payload = {
        "version": "source-section-context-v1", "document_id": "dart:test",
        "document_sha256": _sha(document), "text": heading,
        "location": f"0-{len(heading)}", "text_sha256": _sha(heading),
        "scope_location": f"0-{document.index('[별도부문')}",
        "fragment_location": location, "fragment_sha256": _sha(text),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return CollectedFragment("one", "사업내용", text, location=location,
        source_document_id="dart:test", document_content_sha256=_sha(document),
        document_identity="document:example:filing", section_context_json=raw if context else "")


def test_writer_and_reviewer_receive_same_exact_heading_without_positive_fact_claim():
    source = _source()
    writer = _render_fragments((source,))
    reviewer = _review_fragment_metadata(source)
    assert SECTION_CONTEXT_LABEL in writer and SECTION_CONTEXT_LABEL in reviewer
    assert json.loads(reviewer.split(": ", 1)[1])[SECTION_CONTEXT_LABEL] == "[기타부문-장비]"
    assert source.text in writer and "[별도부문-소재]" not in writer
    assert source.source_context_json == ""


def test_context_roundtrip_preserves_utf8_and_document_fragment_bindings():
    source = _source()
    restored, = fragments_from_raw({1: {"종류": source.kind, "원문": source.text,
        "원문위치": source.location, "문서ID": source.source_document_id,
        "_evidence_document_content_sha256": source.document_content_sha256,
        "section_context_json": source.section_context_json}})
    assert restored.section_context_json.encode() == source.section_context_json.encode()
    assert restored.source_document_id == source.source_document_id
    assert restored.document_content_sha256 == source.document_content_sha256
    assert restored.text == source.text and restored.location == source.location


@pytest.mark.parametrize("changes", [
    {"text": "다른 제품 대금을 현금으로 받습니다."},
    {"location": "900-930"}, {"source_document_id": "dart:other"},
    {"document_content_sha256": "f" * 64}, {"section_context_json": None},
])
def test_cross_document_fragment_or_nonstring_context_is_rejected(changes):
    with pytest.raises((TypeError, ValueError)):
        replace(_source(), **changes)


def test_replacement_or_removal_of_heading_cannot_reuse_review_or_numeric_source_fingerprint():
    source = _source()
    row = FlowRow(("장비", "대금", "고객"), ("one",))
    bound = bind_reviewed_flow_row(row, section_id="operations_partners",
        fragments={"one": source}, review_path="legacy")
    for changed in (_source("[기타부문-소재]"), replace(source, section_context_json="")):
        assert flow_review_problem(bound, section_id="operations_partners", fragments={"one": changed})
        assert _fragment_snapshot(source) != _fragment_snapshot(changed)


def test_legacy_empty_context_leaves_existing_writer_reviewer_and_snapshot_shape():
    source = _source(context=False)
    assert SECTION_CONTEXT_LABEL not in _render_fragments((source,))
    assert SECTION_CONTEXT_LABEL not in _review_fragment_metadata(source)
    assert "section_context_json" not in _fragment_snapshot(source)
    assert replace(source, section_context_json="") == source


def test_rendered_fact_and_storage_restore_keep_section_context_fingerprint():
    from src.features.composer.port import ComposedReport, ComposedSection, ComposedSentence
    from src.features.composer.constants import GRADE_CONFIRMED
    from src.features.composer.render import render_report
    from src.features.storage.reports import report_from_json, report_to_json

    source = replace(_source(), source_url="https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20260315000123",
                     document_identity="document:dart.fss.or.kr:20260315000123", document_title="사업보고서")
    sentence = ComposedSentence(source.text, ("one",), GRADE_CONFIRMED,
        planned_claim_slot="business_model:sales_channel", verification_state="verified")
    report = render_report("예시회사", ComposedReport((ComposedSection("business_model", (sentence,)),)),
        (source,), None)
    fact, = report.fact_records
    assert json.loads(fact.state_evidence)[0]["section_context_sha256"] == _sha(source.section_context_json)
    restored = report_from_json(report_to_json(report))
    assert restored.fact_records[0].state_evidence == fact.state_evidence
    assert restored.fact_records[0].evidence_binding == fact.evidence_binding


def test_prose_fact_cannot_bind_other_fragment_context_to_unchanged_source():
    from src.features.composer.prose_facts import ProseEvidence
    from src.features.composer.tests.test_prose_facts import _source as public_source

    source = _source()
    with pytest.raises(ValueError):
        ProseEvidence("one", public_source(1, "별도 제품을 판매한다."),
            "별도 제품을 판매한다.", section_context_json=source.section_context_json)
