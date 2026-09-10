"""짧은 명시 제목과 직후 본문의 연속 원문·소유 범위 계약."""
import hashlib

import pytest

from features.evidence_collection import constants as c
from features.evidence_collection.collect import collect_dart_evidence
from features.evidence_collection.filing_select import DocumentFetchResult, FilingListResult, RawFilingRow
from features.evidence_collection.serialize import harvest_to_mapping
from features.evidence_collection.tests.fixtures.fake_fetcher import FakeFetcher
from features.evidence_collection.segment import (
    segment_document, segment_document_with_status,
    segment_short_observation_candidates_with_status, usable_ranges_from_candidates,
)

BODY = "현지 규제를 준수하고 기존 거래의 위험 관리와 유동성 확보에 집중하고 있습니다."


@pytest.mark.parametrize("heading", ["8) 예시법인", "8)예시법인", "2. 하위 법인", "가. 하위 법인", "II. 하위 법인"])
def test_short_heading_and_first_eligible_paragraph_preserve_contiguous_text(heading):
    text = "앞 문서는 이 문단과 관련 없는 충분히 긴 별도 설명입니다.\n\n" + heading + "\n\n " + BODY + " \n"
    matches = [x for x in segment_document(text) if BODY in x.text]
    assert len(matches) == 1
    candidate = matches[0]
    assert candidate.text == heading + "\n\n " + BODY
    assert candidate.section_heading == heading
    assert text[candidate.start:candidate.end] == candidate.text
    assert hashlib.sha256(text[candidate.start:candidate.end].encode()).digest() == hashlib.sha256(candidate.text.encode()).digest()
    assert usable_ranges_from_candidates(matches)[0].start == candidate.start


def test_heading_context_does_not_cross_entity_boundaries():
    other = "다른 법인은 신규 상품 판매를 확대하고 고객 서비스 개선에 집중하고 있습니다."
    text = f"7) 앞 법인\n\n{other}\n\n8) 뒤 법인\n\n{BODY}"
    candidates = segment_document(text)
    current = next(x for x in candidates if BODY in x.text)
    assert current.text == "8) 뒤 법인\n\n" + BODY
    assert "앞 법인" not in current.text and other not in current.text
    ranges = usable_ranges_from_candidates(candidates)
    assert all(a.end <= b.start for a, b in zip(ranges, ranges[1:]))


@pytest.mark.parametrize("intervening", ["■ 주요 상품", "상품명", "다른 짧은 설명", " "])
def test_heading_context_does_not_skip_intervening_content(intervening):
    text = f"8) 예시법인\n\n{intervening}\n\n{BODY}"
    candidate = next(x for x in segment_document(text) if BODY in x.text)
    assert ("8) 예시법인" in candidate.text) == (not intervening.strip())


def test_heading_is_not_synthesized_for_later_paragraphs():
    second = "이후 별도 문단에서는 추가적인 투자 계획과 운영 현황을 구분하여 설명합니다."
    text = f"8) 예시법인\n\n{BODY}\n\n{second}"
    candidate = next(x for x in segment_document(text) if second in x.text)
    assert candidate.text == second


def test_table_of_contents_heading_is_not_attached_to_body():
    text = f"1. 목차\n8) 예시법인 ...... 12\n\n잘못된 목차 설명은 본문 후보에 들어가면 안 됩니다.\n\n8) 예시법인\n\n{BODY}"
    candidates = segment_document(text)
    assert len(candidates) == 1
    assert candidates[0].text == "8) 예시법인\n\n" + BODY


def test_heading_length_does_not_promote_short_observation_to_writer():
    short = "가나다는 베타와 경쟁합니다."
    assert len(short) < c.MIN_FRAGMENT_CHARS
    text = "8) 예시법인\n\n" + short
    assert segment_document(text) == []
    result = segment_short_observation_candidates_with_status(text, candidate_filter=lambda value: "경쟁" in value)
    assert [x.text for x in result.candidates] == [short]
    assert text[result.candidates[0].start:result.candidates[0].end] == short


def test_heading_does_not_bypass_boilerplate_exclusion():
    text = f"1) 첫 법인\n\n{BODY}\n\n2) 둘째 법인\n\n{BODY}"
    assert segment_document(text) == []


def test_character_budget_includes_heading_and_intervening_whitespace(monkeypatch):
    text = f"8) 예시법인\n\n{BODY}"
    monkeypatch.setattr(c, "MAX_LONG_FRAGMENT_CHARS_PER_DOCUMENT", len(BODY))
    result = segment_document_with_status(text)
    assert result.candidates == ()
    assert result.truncation_reason == c.REASON_DOCUMENT_FRAGMENT_CHARS_EXCEEDED


def test_candidate_limit_preserves_truncation(monkeypatch):
    monkeypatch.setattr(c, "MAX_LONG_FRAGMENT_CANDIDATES_PER_DOCUMENT", 1)
    text = f"1) 첫 법인\n\n{BODY}\n\n2) 둘째 법인\n\n새로운 고객을 확보하면서 독립적인 업무와 사업을 운영하고 있습니다."
    result = segment_document_with_status(text)
    assert len(result.candidates) == 1
    assert result.truncation_reason == c.REASON_DOCUMENT_FRAGMENT_COUNT_EXCEEDED


def test_parenthesized_subheadings_preserve_segment_and_candidate_limits(monkeypatch):
    monkeypatch.setattr(c, "MAX_TEXT_SEGMENTS_PER_DOCUMENT", 2)
    monkeypatch.setattr(c, "MAX_LONG_FRAGMENT_CANDIDATES_PER_DOCUMENT", 2)
    text = "\n\n".join(f"{i}) 법인\n\n본문 {i}의 독립적인 내용과 운영 현황을 충분한 길이로 기록합니다." for i in range(1, 5))
    result = segment_document_with_status(text)
    assert len(result.candidates) <= 2
    assert result.truncation_reason == c.REASON_DOCUMENT_FRAGMENT_COUNT_EXCEEDED


def test_already_contiguous_heading_preserves_whitespace_offsets():
    text = "　8) 예시법인\r\n " + BODY + "　\r\n"
    candidate = segment_document(text)[0]
    assert candidate.text.count("8) 예시법인") == 1
    assert candidate.text == text[candidate.start:candidate.end] == text.strip()


def test_nonheading_text_preserves_existing_paragraph_segmentation():
    text = "짧은 설명\n\n" + BODY
    assert [x.text for x in segment_document(text)] == [BODY]


def _collect_text(text, **kwargs):
    row = RawFilingRow("20250315000001", "사업보고서 (2024.12)", "20250315")
    fetcher = FakeFetcher(
        list_responses_by_pblntf_ty={"A": FilingListResult(state="OK", rows=(row,))},
        document_responses_by_rcept_no={row.rcept_no: DocumentFetchResult(state="OK", text=text)},
    )
    return collect_dart_evidence(fetcher, "00126380", now="2026-09-09T00:00:00+09:00", **kwargs)


@pytest.mark.parametrize("filtered", [False, True])
def test_collection_removes_only_short_ranges_contained_in_unclassified_body(filtered):
    heading = "8) 예시항목"
    long_text = "오늘 날씨가 맑고 하늘이 파랗다는 이야기를 적어 둔 문단이다."
    independent = "가나다는 나다라와 경쟁합니다."
    text = f"{heading}\n\n{long_text}\n\n{independent}"
    kwargs = {"short_observation_filter": lambda value: "경쟁" in value} if filtered else {}
    harvest = _collect_text(text, **kwargs)
    mapping = harvest_to_mapping(harvest)
    assert harvest.company_type == c.COMPANY_TYPE_LISTED
    assert not any(a.state == c.ATTEMPT_STATE_FAILED for a in harvest.attempts)
    fragments = mapping["unclassified_fragments"]
    assert {x["text"] for x in fragments} == {heading + "\n\n" + long_text, independent}
    ranges = mapping["unclassified_documents"][0]["usable_ranges"]
    assert all(a["end"] <= b["start"] for a, b in zip(ranges, ranges[1:]))
    for fragment in fragments:
        start, end = map(int, fragment["location"].split("-"))
        assert text[start:end] == fragment["text"]
        assert hashlib.sha256(fragment["text"].encode()).hexdigest() == fragment["text_sha256"]
        assert {"start": start, "end": end} in ranges
    # 원래 short API는 바꾸지 않는다. 중복 제거는 수집기의 같은 차선에만 적용한다.
    short = segment_short_observation_candidates_with_status(text)
    assert [x.text for x in short.candidates] == [heading, independent]


def test_classified_body_preserves_short_observation_in_separate_lane():
    heading = "8) 예시항목"
    text = heading + "\n\n당사는 정밀부품을 생산하는 주식회사이며 법인이다."
    mapping = harvest_to_mapping(_collect_text(text))
    assert any(f["text"] == text for f in mapping["fragments"])
    assert [f["text"] for f in mapping["unclassified_fragments"]] == [heading]


def test_short_range_deduplication_preserves_observed_truncation(monkeypatch):
    monkeypatch.setattr(c, "MAX_SHORT_OBSERVATION_CANDIDATES_PER_DOCUMENT", 1)
    text = "8) 예시항목\n\n오늘 날씨가 맑고 하늘이 파랗다는 이야기를 적어 둔 문단이다.\n\n별도 관측"
    harvest = _collect_text(text, short_observation_filter=lambda value: True)
    assert any(
        a.state == c.ATTEMPT_STATE_TRUNCATED and a.reason_code == c.REASON_DOCUMENT_FRAGMENT_COUNT_EXCEEDED
        for a in harvest.attempts
    )
