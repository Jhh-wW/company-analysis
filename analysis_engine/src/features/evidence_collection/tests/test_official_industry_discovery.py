"""산업 조사 원문은 기존 보관 상한 안에서 작성 슬롯과 분리한다."""

import hashlib

from features.evidence_collection import collect, constants as c
from features.evidence_collection import official_industry_discovery_constants as dc
from features.evidence_collection.official_industry_discovery import official_industry_discovery
from features.evidence_collection.retention import CandidateRetention
from features.evidence_collection.segment import FragmentCandidate
from features.evidence_collection.tests.test_collect import _fetcher, RawFilingRow, _NOW


def candidate(text, start=0):
    return FragmentCandidate(start, start + len(text), text, "")


def test_industry_observation_is_retained_without_company_issue_slots():
    text = "II. 사업의 내용\n최근 국내 부품 시장의 수요 감소는 업계의 공통 문제입니다."
    harvest = collect.collect_dart_evidence(
        _fetcher("A", RawFilingRow("20260315000001", "사업보고서 (2025.12)", "20260315"), text),
        "00000001", now=_NOW,
    )
    observations = [f for f in harvest.unclassified_fragments if dc.DISCOVERY_REASON in f.reason_codes]
    assert observations
    for fragment in observations:
        start, end = map(int, fragment.location.split("-"))
        assert text[start:end] == fragment.text
        assert fragment.text_sha256 == hashlib.sha256(fragment.text.encode()).hexdigest()
        assert fragment.section_id == fragment.slot_id == ""
        assert fragment.score_millis == 0 and fragment.covered_slot_ids == ()


def test_discovery_preserves_fragment_even_when_only_unavailable_slot_matches(monkeypatch):
    text = "공식 사업 설명\n국내 센서 시장에서 수요 감소가 관측됩니다."
    monkeypatch.setattr(collect.relevance, "score_fragment_slots_with_signal", lambda *args, **kwargs: ((), True))
    harvest = collect.collect_dart_evidence(
        _fetcher("A", RawFilingRow("20260315000001", "사업보고서", "20260315"), text),
        "00000001", now=_NOW,
    )
    assert not harvest.fragments
    assert any(dc.DISCOVERY_REASON in f.reason_codes for f in harvest.unclassified_fragments)


def test_already_admitted_short_observation_receives_same_discovery_marker():
    text = "공식 자료\n국내 센서 시장 수요 감소."
    harvest = collect.collect_dart_evidence(
        _fetcher("A", RawFilingRow("20260315000001", "사업보고서", "20260315"), text),
        "00000001", now=_NOW, short_observation_filter=lambda _: True,
    )
    assert any(dc.DISCOVERY_REASON in f.reason_codes and "수요 감소" in f.text
               for f in harvest.unclassified_fragments)


def test_discovery_has_small_protected_share_with_existing_total_limits(monkeypatch):
    monkeypatch.setattr(c, "MAX_UNCLASSIFIED_CANDIDATES_PER_DOCUMENT", 9)
    retention = CandidateRetention(frozenset())
    for index in range(8):
        retention.offer_unclassified(index, candidate(f"신원 확인과 일반 문맥을 설명하는 합성 원문 {index}."))
    for index in range(12):
        retention.offer_unclassified(100 + index, candidate(f"국내 센서 시장의 수요 감소를 관측한 합성 원문 {index}."))
    assert len(retention.unclassified.industry) == dc.DISCOVERY_RETAINED_COUNT
    assert len(retention.unclassified) == 9
    assert len(retention.unclassified.ordinary) == 3
    assert retention.unclassified.chars <= c.MAX_UNCLASSIFIED_CHARS_PER_DOCUMENT


def test_no_industry_signal_keeps_original_unclassified_priority_order():
    retention = CandidateRetention(frozenset())
    values = [candidate("일반 원문을 설명하는 합성 문장입니다."), candidate("변경 문맥을 설명하는 합성 문장입니다.")]
    for index, value in enumerate(values):
        retention.offer_unclassified(index, value)
    assert not retention.unclassified.industry
    assert [value for _, value in retention.selected_unclassified()] == values
    assert not official_industry_discovery("센서 시장의 일반적 정의입니다.")
    assert not official_industry_discovery("회사의 수요가 감소했습니다.")


def test_same_fragment_in_two_lanes_counts_one_scanned_candidate():
    text = "회사는 산업장비 시장의 공급 부족에 대응해 산업장비를 제조하고 생산한다."
    harvest = collect.collect_dart_evidence(
        _fetcher("A", RawFilingRow("20260315000001", "사업보고서", "20260315"), text),
        "00000001", now=_NOW,
    )
    assert harvest.fragments and harvest.unclassified_fragments
    scans = [attempt.document_scan for attempt in harvest.attempts if attempt.document_scan]
    assert scans and all(scan.candidates_retained <= scan.candidates_seen for scan in scans)
