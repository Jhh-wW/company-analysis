"""원문 순회와 후보 보관을 분리한 문서 분할기.

색인·후보 보관 상한은 EOF 순회를 중단하지 않는다. 시간 제한만 실제
미완료로 기록하며, 후보 원문은 항상 입력의 연속 구간이다.
"""
from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

from features.evidence_collection import constants as c
from features.evidence_collection.models import DocumentTextRange


@dataclass(frozen=True)
class TextSegment:
    heading: str
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class FragmentCandidate:
    start: int
    end: int
    text: str
    section_heading: str
    is_short: bool = False


@dataclass
class ScanProgress:
    scanned_chars: int = 0
    complete: bool = False
    truncation_reason: str = ""
    line_index_saturated: bool = False
    windowed_paragraphs: bool = False
    candidates_seen: int = 0
    classification_keywords: set[str] = field(default_factory=set)


@dataclass(frozen=True)
class DocumentSegmentationResult:
    candidates: tuple[FragmentCandidate, ...]
    truncation_reason: str = ""
    scan_complete: bool = False
    scanned_chars: int = 0
    selection_compressed: bool = False
    compression_reason: str = ""
    line_index_saturated: bool = False


_TOC_ENTRY_LEADER_PATTERN = re.compile(r"[.·]{2,}\s*\d{1,4}\s*$")


def _is_heading(line: str) -> bool:
    return len(line.strip()) <= c.MAX_HEADING_CONTEXT_CHARS and bool(
        c.DOCUMENT_HEADING_PATTERN.match(line.strip())
    )


def _is_context_heading(text: str) -> bool:
    return _is_heading(text) or (
        len(text.strip()) <= c.MAX_HEADING_CONTEXT_CHARS
        and bool(c.PARAGRAPH_SUBHEADING_PATTERN.match(text.strip()))
    )


def _is_toc_heading(line: str) -> bool:
    stripped = line.strip()
    collapsed = re.sub(r"\s+", "", stripped)
    return any(marker in collapsed for marker in c.TOC_HEADING_MARKERS) or bool(
        _TOC_ENTRY_LEADER_PATTERN.search(stripped)
    )


def _lines(text: str) -> Iterator[tuple[int, int, str]]:
    """개행 없는 장문도 유한한 작업 단위로 나누어 마감을 확인할 수 있다."""
    start = 0
    while start < len(text):
        limit = min(len(text), start + c.MAX_CANDIDATE_WINDOW_CHARS)
        newline = text.find("\n", start, limit)
        end = newline + 1 if newline >= 0 else limit
        if newline < 0 and limit < len(text):
            # 원문 좌표와 최대 창 크기를 유지한다. 가까운 문장 끝이 없으면
            # 고정 경계로 진행하므로 모든 장문 의미를 복원했다고 주장하지 않는다.
            boundary_start = max(start, limit - c.CANDIDATE_WINDOW_SENTENCE_LOOKBACK_CHARS)
            for boundary in c.CANDIDATE_WINDOW_SENTENCE_END_PATTERN.finditer(text, boundary_start, limit):
                end = boundary.end()
        yield start, end, text[start:end]
        start = end


def iter_document_candidates(
    text: str, *, progress: ScanProgress,
    deadline_at: float | None = None,
    short_filter: Callable[[str], bool] | None = None,
) -> Iterator[FragmentCandidate]:
    """일반·짧은 후보를 같은 순회에서 즉시 내보낸다.

    반복 줄 사전 색인은 해시만 유한하게 보관한다. 포화 뒤에는 새 줄을
    색인하지 않을 뿐 끝까지 읽는다. 이 색인 스캔은 후보 검사 완료 증명이
    아니므로 scanned_chars는 실제 후보 순회에서만 늘어난다.
    """
    def expired() -> bool:
        if deadline_at is not None and time.monotonic() > deadline_at:
            progress.truncation_reason = c.REASON_DEADLINE_EXCEEDED
            return True
        return False

    counts: dict[bytes, int] = {}
    classification_tail = ""
    classification_keywords = (c.REVENUE_LINE_ITEM_KEYWORD, *c.FINANCIAL_COMPANY_REVENUE_KEYWORDS)
    overlap = max(map(len, classification_keywords))
    for _start, _end, line in _lines(text):
        if expired():
            return
        stripped = line.strip()
        probe = classification_tail + line
        progress.classification_keywords.update(keyword for keyword in classification_keywords if keyword in probe)
        classification_tail = probe[-overlap:]
        if len(stripped) < c.BOILERPLATE_MIN_CHARS:
            continue
        digest = hashlib.sha256(stripped.encode("utf-8")).digest()
        if digest in counts:
            counts[digest] = min(c.BOILERPLATE_MIN_REPEAT_COUNT, counts[digest] + 1)
        elif len(counts) < c.MAX_BOILERPLATE_DISTINCT_LINES_PER_DOCUMENT:
            counts[digest] = 1
        else:
            progress.line_index_saturated = True

    heading = ""
    in_toc = False
    para_start: int | None = None
    para_end = 0
    pending_heading: int | None = None
    short_observations: set[str] = set()

    def emit() -> FragmentCandidate | None:
        nonlocal para_start, pending_heading
        if para_start is None:
            return None
        start = para_start
        para_start = None
        raw = text[start:para_end]
        body = raw.strip()
        previous_heading = pending_heading
        pending_heading = None
        if not body or in_toc:
            return None
        if counts.get(hashlib.sha256(body.encode("utf-8")).digest(), 0) >= c.BOILERPLATE_MIN_REPEAT_COUNT:
            return None
        start += len(raw) - len(raw.lstrip())
        end = start + len(body)
        is_short = len(body) < c.MIN_FRAGMENT_CHARS
        if is_short:
            if _is_context_heading(body) and not _is_toc_heading(body):
                pending_heading = start
            if short_filter is not None and not short_filter(body):
                return None
            # 짧은 셀·잔여물도 첫 관측은 남긴다. 제목 문맥은 위에서 이미
            # 갱신했으므로 같은 제목의 재등장이 다음 문단에서 사라지지 않는다.
            if len(body) < c.BOILERPLATE_MIN_CHARS:
                if body in short_observations:
                    return None
                if len(short_observations) < c.MAX_SHORT_DUPLICATE_TEXTS_PER_DOCUMENT:
                    short_observations.add(body)
        elif previous_heading is not None and end - previous_heading <= c.MAX_CANDIDATE_WINDOW_CHARS:
            start = previous_heading
            body = text[start:end]
        first = body.partition("\n")[0].strip()
        candidate = FragmentCandidate(start, end, body, first if _is_context_heading(first) else heading, is_short)
        progress.candidates_seen += 1
        return candidate

    for start, end, line in _lines(text):
        if expired():
            return
        is_heading = _is_heading(line) or (in_toc and _is_context_heading(line) and not _is_toc_heading(line))
        if is_heading:
            candidate = emit()
            if candidate is not None:
                yield candidate
            heading = line.strip()
            in_toc = _is_toc_heading(line)
            pending_heading = None
        if line.strip():
            if para_start is not None and end - para_start > c.MAX_CANDIDATE_WINDOW_CHARS:
                progress.windowed_paragraphs = True
                candidate = emit()
                if candidate is not None:
                    yield candidate
            if para_start is None:
                para_start = start
            para_end = end
        else:
            candidate = emit()
            if candidate is not None:
                yield candidate
        progress.scanned_chars = end
    candidate = emit()
    if candidate is not None:
        yield candidate
    if not expired():
        progress.scanned_chars = len(text)
        progress.complete = True


def _bounded_candidates(text: str, *, short: bool, candidate_filter=None) -> DocumentSegmentationResult:
    progress = ScanProgress()
    candidates: list[FragmentCandidate] = []
    chars = 0
    reason = ""
    count_limit = c.MAX_SHORT_OBSERVATION_CANDIDATES_PER_DOCUMENT if short else c.MAX_LONG_FRAGMENT_CANDIDATES_PER_DOCUMENT
    char_limit = c.MAX_SHORT_OBSERVATION_CHARS_PER_DOCUMENT if short else c.MAX_LONG_FRAGMENT_CHARS_PER_DOCUMENT
    for candidate in iter_document_candidates(text, progress=progress, short_filter=candidate_filter):
        if candidate.is_short != short:
            continue
        if len(candidates) >= count_limit:
            reason = reason or c.REASON_DOCUMENT_FRAGMENT_COUNT_EXCEEDED
        elif chars + len(candidate.text) > char_limit:
            reason = reason or c.REASON_DOCUMENT_FRAGMENT_CHARS_EXCEEDED
        else:
            candidates.append(candidate)
            chars += len(candidate.text)
    return DocumentSegmentationResult(tuple(candidates), progress.truncation_reason,
        progress.complete, progress.scanned_chars, bool(reason), reason, progress.line_index_saturated)


def segment_document_with_status(text: str) -> DocumentSegmentationResult:
    """호환 보관 API. 정식 수집기는 반복자를 직접 채점하며 소비한다."""
    return _bounded_candidates(text, short=False)


def segment_document(text: str) -> list[FragmentCandidate]:
    return list(segment_document_with_status(text).candidates)


def segment_short_observation_candidates_with_status(text: str, *, candidate_filter=None) -> DocumentSegmentationResult:
    return _bounded_candidates(text, short=True, candidate_filter=candidate_filter)


def segment_short_observation_candidates(text: str) -> list[FragmentCandidate]:
    return list(segment_short_observation_candidates_with_status(text).candidates)


def segment_sections(text: str) -> list[TextSegment]:
    """호환 제목 목록의 보관량만 제한한다. 후보 반복자는 이 목록을 쓰지 않는다."""
    result: list[TextSegment] = []
    start, heading, in_toc = 0, "", False
    def keep(end: int) -> None:
        if not in_toc and end > start and len(result) < c.MAX_TEXT_SEGMENTS_PER_DOCUMENT:
            result.append(TextSegment(heading, start, end, text[start:end]))
    for offset, _end, line in _lines(text):
        if _is_heading(line):
            keep(offset)
            start, heading, in_toc = offset, line.strip(), _is_toc_heading(line)
    keep(len(text))
    return result


def usable_ranges_from_candidates(candidates: list[FragmentCandidate]) -> tuple[DocumentTextRange, ...]:
    return tuple(sorted((DocumentTextRange(candidate.start, candidate.end) for candidate in candidates),
                        key=lambda value: (value.start, value.end)))
