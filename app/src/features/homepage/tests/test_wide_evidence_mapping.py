"""계약 Mapping 변환기(`to_evidence_mappings`) 회귀시험 — 왕복 보장이 핵심."""

from __future__ import annotations

import hashlib
from pathlib import Path

from src.features.homepage.wide_evidence_mapping import (
    canonical_text_of,
    range_offsets_of,
    to_evidence_mappings,
)
from src.features.homepage.wide_fragments import build_fragments
from src.features.homepage.wide_types import (
    WideCollectionAttempt,
    WideDocumentIdentity,
)

_SHA = "a" * 64


def _document(usable_ranges: tuple[str, ...], **overrides: object) -> WideDocumentIdentity:
    content_sha256 = hashlib.sha256("\n".join(usable_ranges).encode("utf-8")).hexdigest()
    fields = dict(
        company_id="c1",
        document_id="d1",
        canonical_url="https://company.example/careers",
        source_kind="official_recruit_page",
        publisher="company.example",
        title="채용",
        published_on="",
        collected_at="2026-08-31T00:00:00+00:00",
        content_sha256=content_sha256,
        identity_binding="root",
        usable_ranges=usable_ranges,
        collector_version="v1",
        parser_version="v1",
        requirement="REQUIRED",
        source_tier="TIER_1_OFFICIAL",
    )
    fields.update(overrides)
    return WideDocumentIdentity(**fields)


def _attempt(**overrides: object) -> WideCollectionAttempt:
    fields = dict(
        attempt_id="page-0001",
        source_kind="official_web_page",
        requirement="REQUIRED",
        state="OK",
        slot_ids=("culture:work_principle",),
        reason_code="page_ok",
        elapsed_ms=10,
        bytes_downloaded=100,
        documents_seen=1,
    )
    fields.update(overrides)
    return WideCollectionAttempt(**fields)


def test_offset으로_슬라이스하면_원_구간_텍스트가_정확히_복원된다():
    ranges = ("첫 번째 구간입니다.", "두 번째는 조금 더 긴 구간의 본문입니다.", "셋째.")
    document = _document(ranges)

    canonical = canonical_text_of(document)
    offsets = range_offsets_of(document)

    assert len(offsets) == len(ranges)
    for offset, original in zip(offsets, ranges):
        assert canonical[offset["start"] : offset["end"]] == original


def test_canonical_text는_content_sha256과_일관된다():
    ranges = ("구간 하나.", "구간 둘.")
    document = _document(ranges)
    assert hashlib.sha256(canonical_text_of(document).encode("utf-8")).hexdigest() == (
        document.content_sha256
    )


def test_offset은_정렬되고_겹치지_않는다():
    ranges = ("a" * 10, "b" * 5, "c" * 20)
    document = _document(ranges)
    offsets = range_offsets_of(document)
    for previous, current in zip(offsets, offsets[1:]):
        assert previous["end"] < current["start"]  # "\n" 한 글자만큼 벌어져 있다
        assert previous["start"] < previous["end"]


def test_document_mapping은_계약_필드_이름을_그대로_쓴다():
    document = _document(("본문 구간입니다.",))
    result = to_evidence_mappings(documents=(document,), fragments=(), attempts=())

    assert len(result["documents"]) == 1
    mapping = result["documents"][0]
    assert set(mapping.keys()) == {
        "company_id", "document_id", "canonical_url", "source_kind", "publisher",
        "title", "published_on", "collected_at", "content_sha256", "identity_binding",
        "usable_ranges", "collector_version", "parser_version", "requirement", "source_tier",
    }
    assert mapping["document_id"] == "d1"
    assert isinstance(mapping["usable_ranges"], list)
    assert mapping["usable_ranges"][0] == {"start": 0, "end": len("본문 구간입니다.")}


def test_fragment_mapping은_평범한_dict_list다():
    document = _document(("채용 문구입니다.",))
    fragments = build_fragments(document)
    result = to_evidence_mappings(documents=(document,), fragments=fragments, attempts=())

    assert len(result["fragments"]) == len(fragments)
    for mapping in result["fragments"]:
        assert isinstance(mapping, dict)
        assert isinstance(mapping["reason_codes"], list)
        assert isinstance(mapping["score_millis"], int)
        assert set(mapping.keys()) == {
            "fragment_id", "document_id", "location", "text_sha256", "text",
            "section_id", "slot_id", "score_millis", "reason_codes",
        }


def test_attempt_mapping은_slot_ids를_list로_바꾼다():
    result = to_evidence_mappings(documents=(), fragments=(), attempts=(_attempt(),))
    mapping = result["attempts"][0]
    assert isinstance(mapping["slot_ids"], list)
    assert mapping["slot_ids"] == ["culture:work_principle"]


def test_출력값_안에_tuple가_없다():
    document = _document(("본문 구간 하나입니다.",))
    fragments = build_fragments(document)
    result = to_evidence_mappings(documents=(document,), fragments=fragments, attempts=(_attempt(),))

    def _assert_no_tuple(value: object) -> None:
        assert not isinstance(value, tuple)
        if isinstance(value, dict):
            for item in value.values():
                _assert_no_tuple(item)
        elif isinstance(value, list):
            for item in value:
                _assert_no_tuple(item)

    _assert_no_tuple(result)


def test_report_evidence는_import하지_않는다():
    """실행계획 §4-2 — 앱 공용 계약 스키마 직접 사용은 chapter_evidence만의 몫이다."""
    module_path = Path(__file__).resolve().parents[1] / "wide_evidence_mapping.py"
    source = module_path.read_text(encoding="utf-8")
    for line in source.splitlines():
        stripped = line.strip()
        assert not stripped.startswith("import src.shared.report_evidence")
        assert not stripped.startswith("from src.shared.report_evidence")
