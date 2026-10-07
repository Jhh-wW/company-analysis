"""사업부 표제를 사실 증명과 분리하고 원문 위치·문서에 결속한다."""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from heapq import merge

from src.shared.report_evidence import section_context_constants as c


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class SectionContextBudgetExceeded(ValueError):
    """제목 경계를 모두 확인하지 못한 문서는 범위를 추정하지 않는다."""


def section_heading_boundaries(document_text: str) -> Iterator[tuple[int, str]]:
    """제목 개수로 EOF 순회를 끊지 않고 두 정렬된 경계만 순서대로 읽는다."""
    brackets = ((match.start(1), match.group(1)) for match in c.BRACKET_HEADING_RE.finditer(document_text))
    major = ((match.start(), "") for match in c.MAJOR_HEADING_RE.finditer(document_text))
    previous = None
    for boundary in merge(brackets, major, key=lambda item: item[0]):
        if boundary[0] != previous:
            yield boundary
            previous = boundary[0]


def _span(raw: str) -> tuple[int, int]:
    match = c.LOCATION_RE.fullmatch(raw)
    if match is None:
        raise ValueError("사업 범위 문맥의 위치 형식이 다릅니다")
    start, end = map(int, match.groups())
    if start >= end:
        raise ValueError("사업 범위 문맥의 위치가 비어 있거나 역순입니다")
    return start, end


def parse_section_context(
    raw: str, *, document_id: str = "", document_sha256: str = "",
    fragment_location: str = "", fragment_sha256: str = "",
    document_text: str | None = None,
) -> dict[str, str]:
    if type(raw) is not str:
        raise ValueError("사업 범위 문맥은 문자열이어야 합니다")
    if not raw:
        return {}
    item = json.loads(raw)
    if (type(item) is not dict or set(item) != c.CONTEXT_KEYS
            or any(type(value) is not str for value in item.values())
            or item["version"] != c.CONTEXT_VERSION
            or not item["document_id"]
            or len(item["text"]) > c.MAX_HEADING_CHARS
            or c.BUSINESS_HEADING_RE.fullmatch(item["text"]) is None):
        raise ValueError("사업 범위 문맥의 형식이나 사업부 제목이 다릅니다")
    for key in ("document_sha256", "text_sha256", "fragment_sha256"):
        if c.SHA256_RE.fullmatch(item[key]) is None:
            raise ValueError("사업 범위 문맥의 해시 형식이 다릅니다")
    if _hash(item["text"]) != item["text_sha256"]:
        raise ValueError("사업부 제목의 원문 해시가 다릅니다")
    start, end = _span(item["location"])
    scope_start, scope_end = _span(item["scope_location"])
    fragment_start, fragment_end = _span(item["fragment_location"])
    if (end - start != len(item["text"]) or start != scope_start
            or not start <= fragment_start < fragment_end <= scope_end
            or end > scope_end):
        raise ValueError("사업 범위와 제목·조각의 원문 위치가 맞지 않습니다")
    for key, expected in (
        ("document_id", document_id), ("document_sha256", document_sha256),
        ("fragment_location", fragment_location), ("fragment_sha256", fragment_sha256),
    ):
        if expected and item[key] != expected:
            raise ValueError("사업 범위 문맥이 원래 문서·조각과 다릅니다")
    if json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")) != raw:
        raise ValueError("사업 범위 문맥의 직렬화가 정본과 다릅니다")
    if document_text is not None:
        if (scope_end > len(document_text) or _hash(document_text) != item["document_sha256"]
                or document_text[start:end] != item["text"]
                or _hash(document_text[fragment_start:fragment_end]) != item["fragment_sha256"]):
            raise ValueError("사업 범위 문맥이 실제 문서 원문과 다릅니다")
        found_start = False
        expected_end = len(document_text)
        for point, _heading in section_heading_boundaries(document_text):
            if point == start:
                found_start = True
            elif point > start:
                expected_end = point
                break
        if not found_start or scope_end != expected_end:
            raise ValueError("사업 범위가 다음 원문 제목 경계와 다릅니다")
    return item


def section_context_fingerprint(raw: str) -> str:
    parse_section_context(raw)
    return _hash(raw) if raw else ""
