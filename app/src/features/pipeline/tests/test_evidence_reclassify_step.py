from __future__ import annotations

import copy
import hashlib
import json
import sqlite3
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from src.features.chapter_evidence.produce import produce_from_collection_envelopes
from src.features.chapter_evidence.select import select_section_fragments
from src.features.chapter_evidence.tests.fixtures import build_listed_fixture
from src.features.evidence_reclassify.constants import RECLASSIFY_PROMPT_VERSION
from src.features.pipeline import evidence_reclassify_step as step
from src.features.pipeline.official_evidence_preflight import empty_collector_sections
from src.features.storage import evidence_reclassify_cache
from src.shared.report_evidence.runtime_port import OfficialEvidenceCollectionResult


COMPANY_ID = "00126380"
RECEIPT_NUMBER = "20260318000001"
MODEL = "claude-haiku-4-5"
TARGET_SECTION = "portfolio"
TARGET_SLOT = "portfolio:revenue_link"
CANDIDATE_ID = "dart-business-report-01:unclassified0"
CANDIDATE_TEXT = "제품별 매출은 서비스 판매에서 발생하며 핵심 제품의 수익 기여를 설명한다."


class FakeMessages:
    def __init__(self, payload: object = None, *, error: Exception | None = None):
        self.payload = payload
        self.error = error
        self.requests: list[dict[str, object]] = []

    def create(self, **kwargs):
        self.requests.append(kwargs)
        if self.error is not None:
            raise self.error
        return SimpleNamespace(
            content=[
                SimpleNamespace(
                    type="text",
                    text=json.dumps(self.payload, ensure_ascii=False),
                )
            ]
        )


def _connection_factory(conn: sqlite3.Connection):
    @contextmanager
    def connect():
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    return connect


def _response(*, quote: str = CANDIDATE_TEXT) -> dict[str, object]:
    return {
        "assignments": [
            {
                "paragraph_id": CANDIDATE_ID,
                "section_id": TARGET_SECTION,
                "slot_id": TARGET_SLOT,
                "quote": quote,
            }
        ],
        "removals": [],
    }


def _reclassifiable_result(
    *,
    missing_slots: tuple[str, ...] = (TARGET_SLOT,),
    include_unclassified: bool = True,
    low_score_fragment_id: str = "",
) -> OfficialEvidenceCollectionResult:
    fixture = copy.deepcopy(build_listed_fixture(company_id=COMPANY_ID))
    document = fixture["documents"][0]
    document["canonical_url"] = (
        "https://dart.fss.or.kr/dsaf001/main.do?rcpNo=" + RECEIPT_NUMBER
    )
    fragments = [
        fragment
        for fragment in fixture["fragments"]
        if str(fragment["slot_id"]) not in set(missing_slots)
    ]
    for fragment in fragments:
        if fragment["fragment_id"] == low_score_fragment_id:
            fragment["score_millis"] = step.LOW_SCORE_MAX_MILLIS
    document["exact_evidence_hashes"] = tuple(
        dict.fromkeys(str(fragment["text_sha256"]) for fragment in fragments)
    )
    dart_envelope: dict[str, object] = {
        "company_id": COMPANY_ID,
        "company_type": "listed",
        "documents": fixture["documents"],
        "fragments": fragments,
        "attempts": fixture["attempts"],
        "unclassified_documents": [],
        "unclassified_fragments": [],
    }
    if include_unclassified:
        unclassified_document = copy.deepcopy(document)
        unclassified_document["exact_evidence_hashes"] = []
        unclassified_document["exact_evidence_bindings"] = []
        dart_envelope["unclassified_documents"] = [unclassified_document]
        dart_envelope["unclassified_fragments"] = [
            {
                "company_id": COMPANY_ID,
                "fragment_id": CANDIDATE_ID,
                "document_id": document["document_id"],
                "location": "1200-1241",
                "text_sha256": hashlib.sha256(CANDIDATE_TEXT.encode()).hexdigest(),
                "text": CANDIDATE_TEXT,
                "section_id": "",
                "slot_id": "",
                "covered_slot_ids": [],
                "score_millis": 0,
                "reason_codes": ["no_direct_relevance_signal"],
            }
        ]
    wide_envelope = {
        "company_id": COMPANY_ID,
        "documents": [],
        "fragments": [],
        "attempts": [],
    }
    candidates = produce_from_collection_envelopes(
        company_id=COMPANY_ID,
        company_type="listed",
        collection_envelopes=(dart_envelope, wide_envelope),
    )
    original = OfficialEvidenceCollectionResult(
        company_id=COMPANY_ID,
        candidates=candidates,
    )
    return step.attach_reclassify_source(
        original,
        company_type="listed",
        dart_envelope=dart_envelope,
        wide_envelope=wide_envelope,
    )


@pytest.fixture(autouse=True)
def _enable_reclassify(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(step, "evidence_reclassify_enabled", lambda: True)


def test_모든_장이_READY면_캐시와_AI를_모두_건너뛴다() -> None:
    official = _reclassifiable_result(
        missing_slots=(),
        include_unclassified=False,
    )
    messages = FakeMessages(_response())
    steps: list[dict[str, object]] = []

    def forbidden_connection():
        raise AssertionError("빈 장이 없는데 캐시를 열면 안 됩니다")

    result = step.reclassify_official_evidence(
        official,
        client=SimpleNamespace(messages=messages),
        connect_db=forbidden_connection,
        model=MODEL,
        steps=steps,
        generated_at="2026-09-06",
    )

    assert result is official
    assert messages.requests == []
    assert steps == []


def test_캐시미스면_한번_호출하고_저장해_빈장을_READY로_바꾼다() -> None:
    official = _reclassifiable_result()
    messages = FakeMessages(_response())
    steps: list[dict[str, object]] = []
    conn = sqlite3.connect(":memory:")
    try:
        result = step.reclassify_official_evidence(
            official,
            client=SimpleNamespace(messages=messages),
            connect_db=_connection_factory(conn),
            model=MODEL,
            steps=steps,
            generated_at="2026-09-06",
        )

        assert len(messages.requests) == 1
        assert messages.requests[0]["model"] == MODEL
        assert messages.requests[0]["output_config"] == {
            "format": {
                "type": "json_schema",
                "schema": messages.requests[0]["output_config"]["format"]["schema"],
            }
        }
        assert TARGET_SECTION not in {
            item["section_id"] for item in empty_collector_sections(result)
        }
        assert steps == [
            {
                "step": step.RECLASSIFY_STEP_NAME,
                "빈장": [TARGET_SECTION],
                "후보수": 1,
                "프롬프트글자": steps[0]["프롬프트글자"],
                "캐시": "miss",
                "채택": 1,
                "폐기": 0,
                "폐기사유": {},
                "빼기": 0,
                "AI호출": 1,
            }
        ]
        table = evidence_reclassify_cache.TABLE_EVIDENCE_RECLASSIFICATION_CACHE
        assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone() == (1,)
    finally:
        conn.close()


def test_캐시적중은_AI없이_같은_결과를_재현한다() -> None:
    official = _reclassifiable_result()
    conn = sqlite3.connect(":memory:")
    try:
        first_messages = FakeMessages(_response())
        first = step.reclassify_official_evidence(
            official,
            client=SimpleNamespace(messages=first_messages),
            connect_db=_connection_factory(conn),
            model=MODEL,
            steps=[],
            generated_at="2026-09-06",
        )
        hit_messages = FakeMessages(error=AssertionError("캐시 적중 뒤 AI 호출"))
        hit_steps: list[dict[str, object]] = []
        second = step.reclassify_official_evidence(
            official,
            client=SimpleNamespace(messages=hit_messages),
            connect_db=_connection_factory(conn),
            model=MODEL,
            steps=hit_steps,
            generated_at="2026-09-06",
        )

        assert hit_messages.requests == []
        assert hit_steps[0]["캐시"] == "hit"
        assert hit_steps[0]["AI호출"] == 0
        assert second.source_snapshot_sha256 == first.source_snapshot_sha256
    finally:
        conn.close()


def test_원문에_없는_인용은_폐기하고_사유를_기록한다() -> None:
    official = _reclassifiable_result()
    steps: list[dict[str, object]] = []
    conn = sqlite3.connect(":memory:")
    try:
        result = step.reclassify_official_evidence(
            official,
            client=SimpleNamespace(messages=FakeMessages(_response(quote="없는 인용"))),
            connect_db=_connection_factory(conn),
            model=MODEL,
            steps=steps,
            generated_at="2026-09-06",
        )
    finally:
        conn.close()

    assert TARGET_SECTION in {
        item["section_id"] for item in empty_collector_sections(result)
    }
    assert steps[0]["채택"] == 0
    assert steps[0]["폐기"] == 1
    assert steps[0]["폐기사유"] == {"quote_not_found": 1}


def test_빼기는_READY장을_INSUFFICIENT로_내리고_강등을_기록한다() -> None:
    baseline = _reclassifiable_result()
    identity = next(
        fragment
        for candidate in baseline.candidates
        for fragment in candidate.fragments
        if fragment.slot_id == "identity:corporate_identity"
    )
    official = _reclassifiable_result(low_score_fragment_id=identity.fragment_id)
    response = {
        "assignments": [],
        "removals": [
            {
                "paragraph_id": identity.fragment_id,
                "section_id": "identity",
                "reason": "장 목적과 무관한 상투문구",
            }
        ],
    }
    steps: list[dict[str, object]] = []
    conn = sqlite3.connect(":memory:")
    try:
        result = step.reclassify_official_evidence(
            official,
            client=SimpleNamespace(messages=FakeMessages(response)),
            connect_db=_connection_factory(conn),
            model=MODEL,
            steps=steps,
            generated_at="2026-09-06",
        )
    finally:
        conn.close()

    assert "identity" in {
        item["section_id"] for item in empty_collector_sections(result)
    }
    assert steps[0]["빼기"] == 1
    assert steps[0]["강등"] == ["identity"]


def test_스위치OFF면_원결과와_steps가_바이트경계까지_그대로다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(step, "evidence_reclassify_enabled", lambda: False)
    official = _reclassifiable_result()
    steps: list[dict[str, object]] = []

    def forbidden_connection():
        raise AssertionError("OFF에서 캐시를 열면 안 됩니다")

    result = step.reclassify_official_evidence(
        official,
        client=SimpleNamespace(messages=FakeMessages(_response())),
        connect_db=forbidden_connection,
        model=MODEL,
        steps=steps,
        generated_at="2026-09-06",
    )

    assert result is official
    assert result.source_snapshot_sha256 == official.source_snapshot_sha256
    assert steps == []


def test_AI호출예외는_사유만_남기고_원결과로_진행한다() -> None:
    official = _reclassifiable_result()
    steps: list[dict[str, object]] = []
    conn = sqlite3.connect(":memory:")
    try:
        result = step.reclassify_official_evidence(
            official,
            client=SimpleNamespace(
                messages=FakeMessages(error=RuntimeError("provider unavailable"))
            ),
            connect_db=_connection_factory(conn),
            model=MODEL,
            steps=steps,
            generated_at="2026-09-06",
        )
    finally:
        conn.close()

    assert result is official
    assert steps[0]["AI호출"] == 1
    assert steps[0]["실패"] == "호출또는응답:RuntimeError"


def test_재판정조각은_문서hash에_결속되어_select를_통과한다() -> None:
    official = _reclassifiable_result()
    conn = sqlite3.connect(":memory:")
    try:
        result = step.reclassify_official_evidence(
            official,
            client=SimpleNamespace(messages=FakeMessages(_response())),
            connect_db=_connection_factory(conn),
            model=MODEL,
            steps=[],
            generated_at="2026-09-06",
        )
    finally:
        conn.close()

    candidate = next(
        item for item in result.candidates if item.section_id == TARGET_SECTION
    )
    reclassified = next(
        fragment
        for fragment in candidate.fragments
        if "ai_reclassified" in fragment.reason_codes
    )
    document = next(
        item for item in candidate.documents if item.document_id == reclassified.document_id
    )
    selected = select_section_fragments(
        section_id=TARGET_SECTION,
        company_id=COMPANY_ID,
        documents=candidate.documents,
        fragments=candidate.fragments,
    )

    assert reclassified.text_sha256 in document.exact_evidence_hashes
    assert reclassified.fragment_id in {
        fragment.fragment_id for fragment in selected.fragments
    }


def test_저장된_캐시키는_접수번호_프롬프트버전_모델을_쓴다() -> None:
    official = _reclassifiable_result()
    conn = sqlite3.connect(":memory:")
    try:
        step.reclassify_official_evidence(
            official,
            client=SimpleNamespace(messages=FakeMessages(_response())),
            connect_db=_connection_factory(conn),
            model=MODEL,
            steps=[],
            generated_at="2026-09-06",
        )
        expected = evidence_reclassify_cache.key_for(
            [RECEIPT_NUMBER],
            RECLASSIFY_PROMPT_VERSION,
            MODEL,
        )
        table = evidence_reclassify_cache.TABLE_EVIDENCE_RECLASSIFICATION_CACHE
        stored = conn.execute(f"SELECT cache_key FROM {table}").fetchone()
    finally:
        conn.close()

    assert stored == (expected,)
