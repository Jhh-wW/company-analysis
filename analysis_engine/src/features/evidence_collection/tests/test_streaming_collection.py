"""대형 공시의 끝부분 보존·유한 보관·정직한 순회 상태 회귀."""
import hashlib
from dataclasses import asdict

import pytest

from features.evidence_collection import constants as c, collect, relevance, segment
from features.evidence_collection.filing_select import DocumentFetchResult, FilingListResult, RawFilingRow
from features.evidence_collection.scan_contract import DocumentScan
from features.evidence_collection.serialize import harvest_to_mapping
from features.evidence_collection.tests.fixtures.fake_fetcher import FakeFetcher


IDENTITY = "당사는 정밀부품을 생산하는 주식회사이며 법인이다."
TAIL = "핵심가치와 원칙에 따라 운영하며 우수기업 인증받았다. 최종 끝부분 근거다."


def _collect(text, **kwargs):
    row = RawFilingRow("20250315000001", "사업보고서 (2024.12)", "20250315")
    fetcher = FakeFetcher(
        list_responses_by_pblntf_ty={"A": FilingListResult(state="OK", rows=(row,))},
        document_responses_by_rcept_no={row.rcept_no: DocumentFetchResult(state="OK", text=text)},
    )
    return collect.collect_dart_evidence(fetcher, "00126380", now="2026-09-14", **kwargs)


def _attempt(harvest):
    return next(value for value in harvest.attempts if value.attempt_id.startswith("document:"))


@pytest.mark.parametrize("heading_count, paragraph_count", [(4200, 1), (1, 8300)])
def test_옛_제목과_문단_한도_뒤의_유일한_필수근거를_보존한다(heading_count, paragraph_count):
    text = "\n\n".join(f"{index}.회사정보\n{IDENTITY} 항목 {index}" for index in range(heading_count))
    text += "\n\n" + "\n\n".join(f"{IDENTITY} 추가 문단 {index}" for index in range(paragraph_count))
    text += "\n\nV. 임원 및 직원\n" + TAIL
    harvest = _collect(text)
    assert any(TAIL in fragment.text for fragment in harvest.fragments)
    assert {value.section_id for value in harvest.fragments} >= {"identity", "culture"}
    attempt = _attempt(harvest)
    assert attempt.state == c.ATTEMPT_STATE_OK
    assert attempt.requirement == c.REQUIREMENT_REQUIRED
    assert attempt.document_scan.state == c.SCAN_STATE_COMPLETE
    assert attempt.document_scan.scanned_chars == len(text)
    assert attempt.document_scan.selection_compressed
    assert len(harvest.fragments) <= len(c.COLLECTOR_SLOT_IDS) * (
        c.RETAINED_TOP_PER_SLOT + c.RETAINED_RECENT_PER_SLOT + c.RETAINED_CHANGE_PER_SLOT
    )
    assert sum(len(fragment.text) for fragment in harvest.fragments) <= c.MAX_LONG_FRAGMENT_CHARS_PER_DOCUMENT
    for document in (*harvest.documents, *harvest.unclassified_documents):
        assert document.content_sha256 == hashlib.sha256(text.encode()).hexdigest()
    for fragment in (*harvest.fragments, *harvest.unclassified_fragments):
        start, end = map(int, fragment.location.split("-"))
        assert text[start:end] == fragment.text
        assert hashlib.sha256(text[start:end].encode()).hexdigest() == fragment.text_sha256
        assert any(span.start <= start and end <= span.end for doc in (*harvest.documents, *harvest.unclassified_documents)
                   if doc.document_id == fragment.document_id for span in doc.usable_ranges)


def test_고득점_앞부분_홍수_뒤의_취소와_기간변경을_남긴다():
    text = "\n\n".join(f"향후 투자 계획을 추진할 계획이며 검토 중이다. 계획 항목 {index}" for index in range(300))
    cancellation = "당초 투자 계획을 취소하고 실행 시기를 2028년으로 변경하였다."
    text += "\n\n" + cancellation
    harvest = _collect(text)
    tail = next(fragment for fragment in harvest.fragments if cancellation in fragment.text)
    assert c.REASON_CHANGE_CONTEXT in tail.reason_codes
    assert c.REASON_RECENT_CONTEXT in tail.reason_codes


@pytest.mark.parametrize("value", ["1.234", "12.50", "0.25", "123.456"])
def test_소수는_제목이_아니다(value):
    assert not segment._is_heading(value)


@pytest.mark.parametrize("value", ["1.회사의개요", "12. 회사의 개요", "II.사업의내용", "가.주요제품"])
def test_공백없는_한국어_제목도_인식한다(value):
    assert segment._is_heading(value)


def test_번호목록은_끝까지_읽고_본문_좌표를_보존한다():
    text = "\n\n".join(f"{index}) 목록 설명" for index in range(200)) + "\n\n" + TAIL
    assert any(TAIL in value.text for value in _collect(text).fragments)


def test_고유줄색인_포화는_반복판별_품질표시이고_순회를_멈추지_않는다(monkeypatch):
    monkeypatch.setattr(c, "MAX_BOILERPLATE_DISTINCT_LINES_PER_DOCUMENT", 3)
    text = "\n\n".join(f"오늘 날씨가 맑다는 별도 문단을 적는다. 항목 {index}" for index in range(300)) + "\n\n" + TAIL
    harvest = _collect(text)
    scan = _attempt(harvest).document_scan
    assert scan.state == c.SCAN_STATE_COMPLETE and scan.line_index_saturated
    assert scan.unclassified_seen > scan.unclassified_retained
    assert scan.unclassified_retained <= c.MAX_UNCLASSIFIED_CANDIDATES_PER_DOCUMENT
    assert any(TAIL in value.text for value in harvest.fragments)


def test_실제_32768개_줄색인_포화_뒤_근거도_선택한다():
    text = "\n\n".join(f"날씨에 관한 별도 설명과 문장을 기록한다. 일련번호 {index}" for index in range(33000))
    text += "\n\n" + TAIL
    harvest = _collect(text)
    assert _attempt(harvest).document_scan.line_index_saturated
    assert _attempt(harvest).document_scan.state == c.SCAN_STATE_COMPLETE
    assert any(TAIL in value.text for value in harvest.fragments)


def test_중간의_고득점_핵심근거를_앞뒤의_약한_후보보다_우선한다():
    paragraphs = [f"당사는 독립적인 업무를 영위하는 주식회사입니다. 설명 {index}" for index in range(100)]
    important = "당사는 설립된 주식회사로 전자부품 제조업을 영위하는 법인이며 목적사업을 명확히 정한다."
    paragraphs.insert(50, important)
    harvest = _collect("\n\n".join(paragraphs))
    selected = next(value for value in harvest.fragments if important in value.text)
    assert selected.score_millis >= c.RELEVANCE_KEYWORD_HIT_SCORE_MILLIS
    assert c.REASON_RECENT_CONTEXT not in selected.reason_codes
    assert _attempt(harvest).document_scan.selection_compressed


def test_개행없는_긴문단을_유한구간으로_읽고_끝부분을_보존한다():
    text = "가나다라마바사 " * 3000 + TAIL
    harvest = _collect(text)
    scan = _attempt(harvest).document_scan
    assert scan.state == c.SCAN_STATE_COMPLETE and scan.windowed_paragraphs
    assert any(TAIL in value.text for value in harvest.fragments)
    assert all(len(value.text) <= c.MAX_CANDIDATE_WINDOW_CHARS for value in (*harvest.fragments, *harvest.unclassified_fragments))


def test_짧은반복줄과_무분류_보관한도도_읽기완료와_별개다(monkeypatch):
    monkeypatch.setattr(c, "MAX_UNCLASSIFIED_CANDIDATES_PER_DOCUMENT", 0)
    text = "가\n\n" * 2000 + "오늘 날씨가 맑다는 독립적인 설명을 충분히 기록한다.\n\n" + TAIL
    harvest = _collect(text)
    scan = _attempt(harvest).document_scan
    assert scan.state == c.SCAN_STATE_COMPLETE and scan.selection_compressed
    assert scan.unclassified_seen > scan.unclassified_retained
    assert any(TAIL in value.text for value in harvest.fragments)


def test_문서중간_마감은_선택압축으로_위장하지_않는다(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(collect.time, "monotonic", lambda: clock[0])
    original = relevance.score_fragment_slots_with_signal
    calls = [0]
    def score(*args, **kwargs):
        calls[0] += 1
        if calls[0] == 20:
            clock[0] = c.DEFAULT_COLLECTION_DEADLINE_SECONDS + 1
        return original(*args, **kwargs)
    monkeypatch.setattr(relevance, "score_fragment_slots_with_signal", score)
    text = "\n\n".join(f"{IDENTITY} 항목 {index}" for index in range(100)) + "\n\n" + TAIL
    harvest = _collect(text)
    attempt = _attempt(harvest)
    assert harvest.fragments
    assert attempt.state == c.ATTEMPT_STATE_TRUNCATED
    assert attempt.requirement == c.REQUIREMENT_REQUIRED
    assert attempt.reason_code == c.REASON_DEADLINE_EXCEEDED
    scan = attempt.document_scan
    assert scan.state == c.SCAN_STATE_INCOMPLETE
    assert 0 < scan.scanned_chars < len(text)
    assert not any(TAIL in value.text for value in harvest.fragments)
    serialized = next(row for row in harvest_to_mapping(harvest)["attempts"] if row["document_scan"])
    assert serialized["document_scan"] == asdict(scan)
    assert DocumentScan(**serialized["document_scan"]) == scan


def test_분류_탐침은_보관하지_않은_짧은_매출액도_읽는다():
    text = IDENTITY + "\n\n영업수익을 통해 금융 서비스를 제공한다.\n\n매출액"
    harvest = _collect(text, short_observation_filter=lambda text: False)
    assert harvest.company_type == c.COMPANY_TYPE_LISTED


@pytest.mark.parametrize("field,value", [("version", "document_scan/0"), ("scanned_chars", 0),
                                          ("total_chars", True), ("candidates_retained", 999)])
def test_구형버전과_거짓완료_계약을_거절한다(field, value):
    scan = asdict(_attempt(_collect(IDENTITY)).document_scan)
    scan[field] = value
    with pytest.raises(ValueError):
        DocumentScan(**scan)
