"""전문(全文) 의미 분할.

기존 방식(공시 종류당 첫 1,200자만 봄, 실측 커버리지 2.4%)을 버리고 문서
전체를 제목·문단 구조로 나눈다. 목차 구간과 문서 전체에서 반복되는 상투
문구(면책 등)는 후보에서 뺀다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from features.evidence_collection import constants as c
from features.evidence_collection.models import DocumentTextRange

#: 제목 줄로 볼 패턴 — 로마숫자(I. II. ...), 아라비아 숫자(1. 2. ...),
#: 「제N장」, 한글 순서(가. 나. ...) 중 하나로 시작하고 뒤에 내용이 있는 줄.
#: ★ v1 휴리스틱 — 본문 안 번호 매긴 목록도 오탐될 수 있다(알려진 한계).
_HEADING_PATTERN = re.compile(
    r"^(?:[IVXLCDM]{1,6}\.|[0-9]{1,3}\.|제\s?[0-9]{1,3}\s?장|[가나다라마바사아자차카타파하]\.)\s*\S"
)


@dataclass(frozen=True)
class TextSegment:
    """제목 줄 하나가 여는 구간. 목차 구간은 애초에 만들지 않는다."""

    heading: str
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class FragmentCandidate:
    """문단 하나 — EvidenceFragment로 올라가기 전의 중간 산출물."""

    start: int
    end: int
    text: str
    section_heading: str


def _line_offsets(text: str) -> tuple[list[str], list[int]]:
    lines = text.splitlines(keepends=True)
    offsets: list[int] = []
    running = 0
    for line in lines:
        offsets.append(running)
        running += len(line)
    return lines, offsets


def _is_heading(line: str) -> bool:
    return bool(_HEADING_PATTERN.match(line.strip()))


def _is_toc_heading(line: str) -> bool:
    stripped = line.strip()
    return any(marker in stripped for marker in c.TOC_HEADING_MARKERS)


def segment_sections(text: str) -> list[TextSegment]:
    """제목 줄 기준으로 문서를 구간으로 나눈다. 목차 구간은 만들지 않는다."""
    lines, offsets = _line_offsets(text)
    heading_indices = [i for i, line in enumerate(lines) if _is_heading(line)]

    if not heading_indices:
        stripped = text.strip()
        if not stripped:
            return []
        return [TextSegment(heading="", start=0, end=len(text), text=text)]

    boundaries = heading_indices + [len(lines)]
    segments: list[TextSegment] = []
    for position, head_idx in enumerate(heading_indices):
        if _is_toc_heading(lines[head_idx]):
            continue
        next_idx = boundaries[position + 1]
        start = offsets[head_idx]
        end = offsets[next_idx] if next_idx < len(lines) else len(text)
        segments.append(TextSegment(
            heading=lines[head_idx].strip(), start=start, end=end, text=text[start:end],
        ))
    return segments


def _find_repeated_lines(text: str) -> frozenset[str]:
    """짧지 않은 줄이 문서 전체에서 반복되면 상투 문구(면책 등)로 본다."""
    counts: dict[str, int] = {}
    for line in text.splitlines():
        stripped = line.strip()
        if len(stripped) < c.BOILERPLATE_MIN_CHARS:
            continue
        counts[stripped] = counts.get(stripped, 0) + 1
    return frozenset(
        line for line, count in counts.items() if count >= c.BOILERPLATE_MIN_REPEAT_COUNT
    )


def _split_paragraphs(
    segment: TextSegment, boilerplate: frozenset[str],
) -> list[tuple[int, int, str]]:
    """구간 안을 빈 줄 기준 문단으로 나누고, 상투 문구·짧은 잔여물을 뺀다."""
    lines = segment.text.splitlines(keepends=True)
    paragraphs: list[tuple[int, int, str]] = []
    current_start: int | None = None
    current_chunks: list[str] = []
    running = segment.start

    def emit() -> None:
        if current_start is None or not current_chunks:
            return
        raw = "".join(current_chunks)
        stripped = raw.strip()
        if stripped and stripped not in boilerplate and len(stripped) >= c.MIN_FRAGMENT_CHARS:
            trimmed = raw.rstrip("\r\n")
            paragraphs.append((current_start, current_start + len(trimmed), trimmed))

    for line in lines:
        if line.strip():
            if current_start is None:
                current_start = running
            current_chunks.append(line)
        else:
            emit()
            current_start, current_chunks = None, []
        running += len(line)
    emit()
    return paragraphs


def segment_document(text: str) -> list[FragmentCandidate]:
    """문서 전체를 조각 후보로 나눈다. 목차·상투 문구·짧은 잔여물은 뺀다."""
    boilerplate = _find_repeated_lines(text)
    candidates: list[FragmentCandidate] = []
    for section in segment_sections(text):
        for start, end, para_text in _split_paragraphs(section, boilerplate):
            candidates.append(FragmentCandidate(
                start=start, end=end, text=para_text, section_heading=section.heading,
            ))
    return candidates


def usable_ranges_from_candidates(
    candidates: list[FragmentCandidate],
) -> tuple[DocumentTextRange, ...]:
    """조각 후보들의 구간을 CollectedDocument.usable_ranges 모양으로 정렬해 돌려준다."""
    return tuple(sorted(
        (DocumentTextRange(start=candidate.start, end=candidate.end) for candidate in candidates),
        key=lambda text_range: (text_range.start, text_range.end),
    ))
