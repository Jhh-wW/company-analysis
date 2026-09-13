"""재분류 캐시 적중이 현재 수집의 미완료 상태를 덮어쓰지 않는 회귀."""
import copy
import sqlite3
from types import SimpleNamespace

from src.features.chapter_evidence.produce import produce_from_collection_envelopes
from src.features.pipeline import evidence_reclassify_step as step
from src.features.pipeline.tests.test_evidence_reclassify_step import (
    COMPANY_ID, MODEL, FakeMessages, _connection_factory, _reclassifiable_result, _response,
)
from src.shared.report_evidence.constants import CollectionState
from src.shared.report_evidence.runtime_port import OfficialEvidenceCollectionResult


def _with_scan(original, *, incomplete):
    source = original.reclassify_source
    dart = copy.deepcopy(source.dart_envelope)
    document = dart["documents"][0]
    dart["attempts"].append({
        "company_id": COMPANY_ID,
        "attempt_id": "document:" + document["document_id"],
        "source_kind": "dart_business_report", "requirement": "REQUIRED",
        "state": "TRUNCATED" if incomplete else "OK",
        "reason_code": "deadline_exceeded" if incomplete else "document_fetch_ok",
        "slot_ids": ["portfolio:revenue_link"],
        "document_scan": {
            "version": "document_scan/1", "document_id": document["document_id"],
            "content_sha256": document["content_sha256"],
            "state": "INCOMPLETE" if incomplete else "COMPLETE",
            "total_chars": 2000, "scanned_chars": 1500 if incomplete else 2000,
            "candidates_seen": 20, "candidates_retained": 19,
            "unclassified_seen": 1, "unclassified_retained": 1,
            "selection_compressed": True, "line_index_saturated": False, "windowed_paragraphs": False,
        },
    })
    candidates = produce_from_collection_envelopes(company_id=COMPANY_ID, company_type="listed",
                                                   collection_envelopes=(dart, source.wide_envelope))
    result = OfficialEvidenceCollectionResult(company_id=COMPANY_ID, candidates=candidates)
    return step.attach_reclassify_source(result, company_type="listed", dart_envelope=dart, wide_envelope=source.wide_envelope)


def test_완료때_저장한_재분류_캐시가_적중해도_현재_미완료를_보존한다(monkeypatch):
    monkeypatch.setattr(step, "evidence_reclassify_enabled", lambda: True)
    original = _reclassifiable_result()
    conn = sqlite3.connect(":memory:")
    try:
        complete = step.reclassify_official_evidence(
            _with_scan(original, incomplete=False), client=SimpleNamespace(messages=FakeMessages(_response())),
            connect_db=_connection_factory(conn), model=MODEL, steps=[], generated_at="2026-09-14",
        )
        messages = FakeMessages(error=AssertionError("캐시 적중 뒤 외부 호출 금지"))
        steps = []
        incomplete = step.reclassify_official_evidence(
            _with_scan(original, incomplete=True), client=SimpleNamespace(messages=messages),
            connect_db=_connection_factory(conn), model=MODEL, steps=steps, generated_at="2026-09-14",
        )
        assert not messages.requests
        assert steps[0]["캐시"] == "hit"
        attempts = [value for candidate in incomplete.candidates for value in candidate.attempts if value.document_scan]
        assert attempts and all(value.state is CollectionState.TRUNCATED for value in attempts)
        assert all(value.document_scan.state == "INCOMPLETE" and value.document_scan.scanned_chars == 1500 for value in attempts)
        assert incomplete.source_snapshot_sha256 != complete.source_snapshot_sha256
    finally:
        conn.close()
