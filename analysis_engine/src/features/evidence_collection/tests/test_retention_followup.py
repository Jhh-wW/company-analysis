"""잡음 뒤 비교 근거 보존과 반복 줄의 선형 처리 회귀."""
import hashlib

import pytest

from features.evidence_collection import constants as c, segment
from features.evidence_collection.retention import CandidateRetention
from features.evidence_collection.segment import FragmentCandidate
from features.evidence_collection.tests.test_streaming_collection import _attempt, _collect


@pytest.mark.parametrize("position", [0, 250, 500])
def test_비교_무분류원천은_앞뒤_잡음에_밀리지_않는다(position):
    comparisons = [
        f"당사의 제품 가격은 동종 경쟁사 두 곳과 비교해 낮은 편이고 배송 기간도 짧다는 평가를 받았다. 순번 {index}"
        for index in range(10)
    ]
    paragraphs = [f"오늘 날씨가 맑다는 별도 문단을 충분히 기록한다. 항목 {index}" for index in range(500)]
    paragraphs[position:position] = comparisons
    text = "\n\n".join(paragraphs)
    harvest = _collect(text, short_observation_filter=lambda body: "경쟁" in body)
    retained = {fragment.text: fragment for fragment in harvest.unclassified_fragments}
    assert all(body in retained for body in comparisons)
    assert not harvest.fragments  # 보관 우선순위가 사실 인정이나 장 배정으로 바뀌지 않는다.
    assert len(retained) <= c.MAX_UNCLASSIFIED_CANDIDATES_PER_DOCUMENT
    assert sum(map(len, retained)) <= c.MAX_UNCLASSIFIED_CHARS_PER_DOCUMENT
    for body in comparisons:
        fragment = retained[body]
        start, end = map(int, fragment.location.split("-"))
        assert text[start:end] == body
        assert fragment.text_sha256 == hashlib.sha256(body.encode()).hexdigest()
    scan = _attempt(harvest).document_scan
    assert scan.state == c.SCAN_STATE_COMPLETE and scan.scanned_chars == len(text)
    assert scan.selection_compressed and scan.unclassified_seen == len(paragraphs)


@pytest.mark.parametrize("heading", ["II. 2025년 주요 사업", "II.2025년 주요 사업", "제 2 장 2025년 사업", "가.2025년 사업"])
def test_로마숫자와_한글_장번호_다음_숫자제목은_문맥에_붙는다(heading):
    assert segment._is_heading(heading)
    body = "당사는 올해도 전자부품 판매에서 발생하는 매출을 기록하였다."
    text = "이전 문서의 독립적인 설명 문장을 충분히 기록한다.\n\n" + heading + "\n\n" + body
    candidates = segment.segment_document(text)
    candidate = next(value for value in candidates if body in value.text)
    assert candidate.section_heading == heading
    assert text[candidate.start:candidate.end] == candidate.text


@pytest.mark.parametrize("value", ["1.234", "12.50", "0.25", "123.456 매출액", "1.234원"])
def test_실제_소수_표셀은_숫자제목_허용에_영향받지_않는다(value):
    assert not segment._is_heading(value)
    assert not segment._is_context_heading(value)


def test_짧은_반복줄은_한번_관측하고_꼬리까지_읽는다():
    tail = "당사의 제품은 다른 경쟁사와 비교하여 가격이 낮다는 설명을 기록한다."
    text = "a\n\n" * 100_000 + tail
    harvest = _collect(text)
    scan = _attempt(harvest).document_scan
    assert scan.state == c.SCAN_STATE_COMPLETE and scan.scanned_chars == len(text)
    assert scan.candidates_seen == 2
    assert {fragment.text for fragment in harvest.unclassified_fragments} == {"a", tail}


def test_중복제거된_짧은제목도_직후_문단의_문맥을_보존한다():
    text = "II.사업\n\n짧음\n\nII.사업\n\n당사의 판매에서 발생하는 매출에 관한 상세한 설명이다."
    candidates = segment.segment_document(text)
    assert candidates[-1].text.startswith("II.사업\n\n")
    assert candidates[-1].section_heading == "II.사업"


def test_무분류_보관의_문자계산은_보관개수에_비례해_반복되지_않는다():
    calls = [0]

    class CountedText(str):
        def __len__(self):
            calls[0] += 1
            return super().__len__()

    retention = CandidateRetention(frozenset())
    count = 1_000
    for index in range(count):
        body = CountedText(f"날씨에 관한 독립적인 별도 설명 문단 {index}")
        retention.offer_unclassified(index, FragmentCandidate(0, 30, body, ""))
    assert calls[0] <= count * 4
    assert len(retention.unclassified) == c.MAX_UNCLASSIFIED_CANDIDATES_PER_DOCUMENT


def test_우선문맥도_문자와_개수_한도안에서_최근_근거를_남긴다(monkeypatch):
    monkeypatch.setattr(c, "MAX_UNCLASSIFIED_CANDIDATES_PER_DOCUMENT", 3)
    monkeypatch.setattr(c, "MAX_UNCLASSIFIED_CHARS_PER_DOCUMENT", 90)
    retention = CandidateRetention(frozenset())
    for index in range(10):
        body = f"경쟁사와 비교한 상세한 문장 순번 {index}"
        retention.offer_unclassified(index, FragmentCandidate(0, len(body), body, ""))
    selected = retention.selected_unclassified()
    assert len(selected) <= 3
    assert sum(len(value.text) for _, value in selected) <= 90
    assert any("순번 9" in value.text for _, value in selected)
    assert retention.unclassified_seen == 10


def test_한도보다_큰_후보가_이미_보관한_유효근거를_비우지_않는다(monkeypatch):
    monkeypatch.setattr(c, "MAX_UNCLASSIFIED_CHARS_PER_DOCUMENT", 30)
    retention = CandidateRetention(frozenset())
    body = "당사와 경쟁사 두 곳을 비교한 설명이다."
    retention.offer_unclassified(0, FragmentCandidate(0, len(body), body, ""))
    oversized = "비교 문맥을 포함하는 설명 " * 10
    retention.offer_unclassified(1, FragmentCandidate(0, len(oversized), oversized, ""))
    assert [value.text for _, value in retention.selected_unclassified()] == [body]


def test_짧은중복줄을_생략하는_중에도_실제마감을_기록한다(monkeypatch):
    calls = [0]

    def clock():
        calls[0] += 1
        return calls[0]

    monkeypatch.setattr(segment.time, "monotonic", clock)
    text = "a\n\n" * 1_000
    progress = segment.ScanProgress()
    candidates = list(segment.iter_document_candidates(text, progress=progress, deadline_at=2_500))
    assert len(candidates) == 1
    assert not progress.complete
    assert progress.truncation_reason == c.REASON_DEADLINE_EXCEEDED
    assert 0 < progress.scanned_chars < len(text)


@pytest.mark.parametrize("sentence", [
    "당사는 다른 경쟁사와 비교한 사업 계획을 취소하지 않았다.",
    "The company does not compete with Alpha in the component market.",
])
def test_장문_창_직전의_문장끝에서_나누어_비교와_부정_문맥을_함께_보존한다(sentence):
    prefix = "가" * (c.MAX_CANDIDATE_WINDOW_CHARS - len(sentence) // 2 - 2) + ". "
    text = prefix + sentence + " 이어지는 다른 설명을 기록한다."
    progress = segment.ScanProgress()
    candidates = list(segment.iter_document_candidates(text, progress=progress))
    assert any(sentence in candidate.text for candidate in candidates)
    assert progress.complete and progress.windowed_paragraphs
    for candidate in candidates:
        assert text[candidate.start:candidate.end] == candidate.text
        assert len(candidate.text) <= c.MAX_CANDIDATE_WINDOW_CHARS
    # 내부 줄 순회 자체도 원문을 빠뜨리거나 겹쳐 읽지 않는다.
    assert "".join(line for _, _, line in segment._lines(text)) == text
