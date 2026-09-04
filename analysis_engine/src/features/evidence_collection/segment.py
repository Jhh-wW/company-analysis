"""전문(全文) 의미 분할.

기존 방식(공시 종류당 첫 1,200자만 봄, 실측 커버리지 2.4%)을 버리고 문서
전체를 제목·문단 구조로 나눈다. 목차 구간과 문서 전체에서 반복되는 상투
문구(면책 등)는 후보에서 뺀다.
"""

from __future__ import annotations

import io
import re
from collections.abc import Callable
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


@dataclass(frozen=True)
class DocumentSegmentationResult:
    """bounded 전문 분할 결과와 잘린 이유.

    ``truncation_reason``이 있으면 후보는 안전한 상한까지의 실제 원문이지만
    문서 전문을 끝까지 검사했다는 뜻은 아니다. 호출자는 이를 OK로 기록하면
    안 된다.
    """

    candidates: tuple[FragmentCandidate, ...]
    truncation_reason: str = ""


def _is_heading(line: str) -> bool:
    return bool(_HEADING_PATTERN.match(line.strip()))


#: 목차 «항목» 줄(예: 「I. 회사의 개요 ...... 3」) — 점·가운뎃점 leader가 2개
#: 이상 이어지다 쪽 번호로 끝나는 형태를 목차 항목으로 본다(P2 v1 휴리스틱 —
#: 모든 DART 공시의 목차 표기를 전수 조사하지 않았다. 확인 못 함). 이 줄은
#: _HEADING_PATTERN에도 걸려 진짜 표제로 오인되던 결함이 있었다 — 표제
#: 패턴과 겹치는 번호 매김(로마숫자 등)을 그대로 쓰기 때문이다.
_TOC_ENTRY_LEADER_PATTERN = re.compile(r"[.·]{2,}\s*\d{1,4}\s*$")


def _is_toc_heading(line: str) -> bool:
    stripped = line.strip()
    # 「목 차」처럼 마커 안에 공백이 섞여도 잡도록 공백을 모두 지우고 비교한다.
    collapsed = re.sub(r"\s+", "", stripped)
    if any(marker in collapsed for marker in c.TOC_HEADING_MARKERS):
        return True
    return bool(_TOC_ENTRY_LEADER_PATTERN.search(stripped))


def _segment_sections_with_status(text: str) -> tuple[list[TextSegment], str]:
    """제목 구간과 잘린 이유를 함께 돌려준다."""
    # ``splitlines``로 줄·offset list를 두 벌 만들면 8MiB의
    # ``a\n`` 입력이 수백만 Python 객체로 증폭된다. 한 줄씩
    # 스캔하고 현재 제목의 start만 유지한다.
    segments: list[TextSegment] = []
    found_heading = False
    current_heading = ""
    current_start = 0
    current_is_toc = False
    running = 0
    for line in io.StringIO(text):
        if _is_heading(line):
            if found_heading and not current_is_toc:
                segments.append(
                    TextSegment(
                        heading=current_heading,
                        start=current_start,
                        end=running,
                        text=text[current_start:running],
                    )
                )
                if len(segments) >= c.MAX_TEXT_SEGMENTS_PER_DOCUMENT:
                    return segments, c.REASON_DOCUMENT_SECTION_COUNT_EXCEEDED
            found_heading = True
            current_heading = line.strip()
            current_start = running
            current_is_toc = _is_toc_heading(line)
        running += len(line)

    if not found_heading:
        stripped = text.strip()
        if not stripped:
            return [], ""
        return [TextSegment(heading="", start=0, end=len(text), text=text)], ""
    if not current_is_toc and len(segments) < c.MAX_TEXT_SEGMENTS_PER_DOCUMENT:
        segments.append(
            TextSegment(
                heading=current_heading,
                start=current_start,
                end=len(text),
                text=text[current_start:],
            )
        )
    return segments, ""


def segment_sections(text: str) -> list[TextSegment]:
    """제목 줄 기준 구간을 bounded list로 돌려준다(상태는 전문 API가 보존)."""

    segments, _truncation_reason = _segment_sections_with_status(text)
    return segments


def _find_repeated_lines(text: str) -> tuple[frozenset[str], str]:
    """반복 상투문구와 distinct-line 색인이 잘린 이유를 돌려준다."""
    counts: dict[str, int] = {}
    for line in io.StringIO(text):
        stripped = line.strip()
        if len(stripped) < c.BOILERPLATE_MIN_CHARS:
            continue
        if (
            stripped not in counts
            and len(counts) >= c.MAX_BOILERPLATE_DISTINCT_LINES_PER_DOCUMENT
        ):
            return (
                frozenset(
                    value
                    for value, count in counts.items()
                    if count >= c.BOILERPLATE_MIN_REPEAT_COUNT
                ),
                c.REASON_DOCUMENT_LINE_INDEX_EXCEEDED,
            )
        counts[stripped] = counts.get(stripped, 0) + 1
    return (
        frozenset(
            line
            for line, count in counts.items()
            if count >= c.BOILERPLATE_MIN_REPEAT_COUNT
        ),
        "",
    )


def _split_paragraphs(
    segment: TextSegment,
    boilerplate: frozenset[str],
    *,
    min_chars: int = c.MIN_FRAGMENT_CHARS,
    max_chars_exclusive: int | None = None,
    max_candidates: int | None = None,
    max_total_chars: int | None = None,
    candidate_filter: Callable[[str], bool] | None = None,
) -> tuple[list[tuple[int, int, str]], str]:
    """문단 후보와 count/문자 상한으로 잘린 이유를 함께 돌려준다."""
    paragraphs: list[tuple[int, int, str]] = []
    accepted_chars = 0
    truncation_reason = ""
    current_start: int | None = None
    current_end: int | None = None
    running = segment.start

    def emit() -> None:
        nonlocal accepted_chars, truncation_reason
        if truncation_reason:
            return
        if current_start is None or current_end is None:
            return
        raw = segment.text[
            current_start - segment.start : current_end - segment.start
        ]
        stripped = raw.strip()
        eligible = (
            stripped
            and stripped not in boilerplate
            and len(stripped) >= min_chars
            and (
                max_chars_exclusive is None
                or len(stripped) < max_chars_exclusive
            )
            and (candidate_filter is None or candidate_filter(stripped))
        )
        if not eligible:
            return
        if max_candidates is not None and len(paragraphs) >= max_candidates:
            truncation_reason = c.REASON_DOCUMENT_FRAGMENT_COUNT_EXCEEDED
            return
        if (
            max_total_chars is not None
            and accepted_chars + len(stripped) > max_total_chars
        ):
            truncation_reason = c.REASON_DOCUMENT_FRAGMENT_CHARS_EXCEEDED
            return
        trimmed = raw.rstrip("\r\n")
        paragraphs.append((current_start, current_start + len(trimmed), trimmed))
        accepted_chars += len(stripped)

    # ``splitlines``는 짧은 줄 수백만 개를 한꺼번에 list로 만들어 메모리를
    # 증폭시킨다. StringIO iterator로 한 줄씩 읽어 같은 위치 계산을 유지한다.
    for line in io.StringIO(segment.text):
        if line.strip():
            if current_start is None:
                current_start = running
            current_end = running + len(line)
        else:
            emit()
            if truncation_reason:
                break
            current_start, current_end = None, None
        running += len(line)
    emit()
    return paragraphs, truncation_reason


def segment_document_with_status(text: str) -> DocumentSegmentationResult:
    """문서 전문을 bounded 분할하고 완전성 상태를 함께 돌려준다."""

    boilerplate, line_index_truncation = _find_repeated_lines(text)
    candidates: list[FragmentCandidate] = []
    total_chars = 0
    sections, section_truncation = _segment_sections_with_status(text)
    for section in sections:
        remaining_count = c.MAX_LONG_FRAGMENT_CANDIDATES_PER_DOCUMENT - len(
            candidates
        )
        remaining_chars = c.MAX_LONG_FRAGMENT_CHARS_PER_DOCUMENT - total_chars
        paragraphs, paragraph_truncation = _split_paragraphs(
            section,
            boilerplate,
            max_candidates=max(0, remaining_count),
            max_total_chars=max(0, remaining_chars),
        )
        for start, end, para_text in paragraphs:
            candidates.append(FragmentCandidate(
                start=start, end=end, text=para_text, section_heading=section.heading,
            ))
            total_chars += len(para_text.strip())
        if paragraph_truncation:
            return DocumentSegmentationResult(
                candidates=tuple(candidates),
                truncation_reason=paragraph_truncation,
            )
    return DocumentSegmentationResult(
        candidates=tuple(candidates),
        truncation_reason=line_index_truncation or section_truncation,
    )


def segment_document(text: str) -> list[FragmentCandidate]:
    """호환 API — 후보는 항상 bounded이며 collect는 별도 상태 API를 쓴다."""

    return list(segment_document_with_status(text).candidates)


def segment_short_observation_candidates_with_status(
    text: str,
    *,
    candidate_filter: Callable[[str], bool] | None = None,
) -> DocumentSegmentationResult:
    """writer 하한보다 짧은 문단을 전문에서 찾고 완전성 상태도 돌려준다.

    ``segment_document``의 품질 하한을 낮추지 않는다. 다른 생산기가 필요로
    할 수 있는 원문을 같은 제목·문단 경계에서 관측한다. 호출자가 중립적인
    ``candidate_filter``를 주입하면 필터에 맞지 않는 앞쪽 표 셀·상품코드는
    예산을 소비하지 않으므로 문서 뒤쪽 후보까지 streaming 탐색할 수 있다.
    필터 문법은 이 engine feature에 하드코딩하지 않는다.

    문서당 개수·총문자를 동시에 제한하며, 맞는 후보가 상한을 넘어 하나라도
    버려졌다면 ``truncation_reason``을 남긴다. 따라서 호출자는 bounded 결과를
    「전문에서 더는 후보가 없었다」고 확대해석할 수 없다.
    """

    boilerplate, line_index_truncation = _find_repeated_lines(text)
    candidates: list[FragmentCandidate] = []
    total_chars = 0
    sections, section_truncation = _segment_sections_with_status(text)
    for section in sections:
        remaining_count = c.MAX_SHORT_OBSERVATION_CANDIDATES_PER_DOCUMENT - len(
            candidates
        )
        remaining_chars = c.MAX_SHORT_OBSERVATION_CHARS_PER_DOCUMENT - total_chars
        paragraphs, paragraph_truncation = _split_paragraphs(
            section,
            boilerplate,
            min_chars=1,
            max_chars_exclusive=c.MIN_FRAGMENT_CHARS,
            max_candidates=max(0, remaining_count),
            max_total_chars=max(0, remaining_chars),
            candidate_filter=candidate_filter,
        )
        for start, end, para_text in paragraphs:
            candidates.append(
                FragmentCandidate(
                    start=start,
                    end=end,
                    text=para_text,
                    section_heading=section.heading,
                )
            )
            total_chars += len(para_text.strip())
        if paragraph_truncation:
            return DocumentSegmentationResult(
                candidates=tuple(candidates),
                truncation_reason=paragraph_truncation,
            )
    return DocumentSegmentationResult(
        candidates=tuple(candidates),
        truncation_reason=line_index_truncation or section_truncation,
    )


def segment_short_observation_candidates(text: str) -> list[FragmentCandidate]:
    """호환 API — 짧은 후보는 bounded이며 정식 수집은 상태 API를 쓴다."""

    return list(segment_short_observation_candidates_with_status(text).candidates)


def usable_ranges_from_candidates(
    candidates: list[FragmentCandidate],
) -> tuple[DocumentTextRange, ...]:
    """조각 후보들의 구간을 CollectedDocument.usable_ranges 모양으로 정렬해 돌려준다."""
    return tuple(sorted(
        (DocumentTextRange(start=candidate.start, end=candidate.end) for candidate in candidates),
        key=lambda text_range: (text_range.start, text_range.end),
    ))
