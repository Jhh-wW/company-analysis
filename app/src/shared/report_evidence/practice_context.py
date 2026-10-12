"""교육 예시·검수 안내의 원문 범위를 문서와 조각에 결속한다."""

from __future__ import annotations

import hashlib
import json
import re

from src.shared.report_evidence import practice_context_constants as c


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _span(raw: str) -> tuple[int, int]:
    match = c.LOCATION_RE.fullmatch(raw)
    if match is None:
        raise ValueError("실습 문맥의 위치 형식이 다릅니다")
    start, end = map(int, match.groups())
    if start >= end:
        raise ValueError("실습 문맥의 위치가 비어 있거나 역순입니다")
    return start, end


def _mode(text: str) -> str:
    if c.EXAMPLE_MARKER_RE.search(text):
        return "example"
    if c.INSTRUCTION_MARKER_RE.search(text):
        return "instruction"
    return ""


def _actual_boundary(text: str, company_name: str = "", *, hypothetical: bool = False) -> bool:
    unit = text.strip()
    if c.EXPLICIT_CASE_HEADING_RE.fullmatch(unit):
        return True
    if c.NON_ACTUAL_RE.search(unit) or not c.ACTUAL_ACTION_RE.search(unit):
        return False
    if hypothetical and not c.EXPLICIT_ACTUAL_PREFIX_RE.match(unit):
        return False
    if c.ACTUAL_SUBJECT_RE.match(unit):
        return True
    unit = c.EXPLICIT_ACTUAL_PREFIX_RE.sub("", unit)
    names = {company_name.strip(), re.sub(r"주식회사|㈜|\(주\)", "", company_name).strip()}
    return any(name and re.match(re.escape(name) + r"\s*(?:는|은|이|가|에서는|에서)\s+", unit) for name in names)


def actual_execution_source_text(
    text: str, *, company_name: str = "", practice_marker: str = "",
) -> str:
    """같은 원문 안의 명시 회사 행동 절을 그대로 반환한다."""
    hypothetical = bool(c.HYPOTHETICAL_MARKER_RE.search(practice_marker))
    return "\n".join(unit for unit in c.ACTUAL_CLAUSE_BOUNDARY_RE.split(text)
                     if _actual_boundary(unit, company_name, hypothetical=hypothetical)
                     and not c.EXPLICIT_CASE_HEADING_RE.fullmatch(unit.strip()))


def practice_range_scopes(
    ranges: tuple[str, ...], *, company_name: str = "",
) -> dict[int, tuple[int, int, str]]:
    """실제 예시 표지 이후의 구간만 묶고 명시 회사 실행·사례에서 끝낸다.

    반환값은 조각 인덱스별 (표지 인덱스, 끝 인덱스(배타), 모드)다.
    회사 실제 사례와 직원 원칙은 어미나 교육 글 제목만으로 제외하지 않는다.
    """
    result = {}
    active = None
    starts = []
    for index, text in enumerate(ranges):
        marker = ranges[active[0]] if active is not None else text if _mode(text) else ""
        hypothetical = bool(c.HYPOTHETICAL_MARKER_RE.search(marker))
        if (_actual_boundary(text, company_name, hypothetical=hypothetical)
                or actual_execution_source_text(text, company_name=company_name, practice_marker=marker)):
            if active is not None:
                starts.append((active[0], index, active[1]))
                active = None
            continue
        mode = _mode(text)
        if mode:
            if active is not None:
                starts.append((active[0], index, active[1]))
            active = (index, mode)
    if active is not None:
        starts.append((active[0], len(ranges), active[1]))
    for start, end, mode in starts:
        for index in range(start, end):
            result[index] = (start, end, mode)
    return result


def build_practice_context(
    *, ranges: tuple[str, ...], document_id: str, document_sha256: str,
    fragment_index: int, fragment_location: str, company_name: str = "",
) -> str:
    scopes = practice_range_scopes(ranges, company_name=company_name)
    if fragment_index not in scopes:
        return ""
    marker_index, end_index, mode = scopes[fragment_index]
    text = "\n".join(ranges)
    if _hash(text) != document_sha256:
        raise ValueError("실습 문맥의 문서 해시가 정본과 다릅니다")
    offsets = []
    cursor = 0
    for unit in ranges:
        offsets.append((cursor, cursor + len(unit)))
        cursor += len(unit) + 1
    marker_start, marker_end = offsets[marker_index]
    fragment_start, fragment_end = offsets[fragment_index]
    scope_end = offsets[end_index - 1][1]
    marker = ranges[marker_index]
    item = {
        "version": c.CONTEXT_VERSION, "document_id": document_id,
        "document_sha256": document_sha256, "mode": mode, "text": marker,
        "location": f"{marker_start}-{marker_end}", "text_sha256": _hash(marker),
        "scope_location": f"{marker_start}-{scope_end}",
        "fragment_location": fragment_location,
        "fragment_range": f"{fragment_start}-{fragment_end}",
        "fragment_sha256": _hash(ranges[fragment_index]),
    }
    raw = json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    parse_practice_context(raw, document_text=text, fragment_text=ranges[fragment_index])
    return raw


def parse_practice_context(
    raw: str, *, document_id: str = "", document_sha256: str = "",
    fragment_location: str = "", fragment_sha256: str = "",
    fragment_text: str | None = None, document_text: str | None = None,
) -> dict[str, str]:
    if type(raw) is not str:
        raise ValueError("실습 문맥은 문자열이어야 합니다")
    if not raw:
        return {}
    item = json.loads(raw)
    if (type(item) is not dict or set(item) != c.CONTEXT_KEYS
            or any(type(value) is not str for value in item.values())
            or item["version"] != c.CONTEXT_VERSION or item["mode"] not in c.MODES
            or not item["document_id"] or not item["fragment_location"]
            or len(item["text"]) > c.MAX_MARKER_CHARS or _mode(item["text"]) != item["mode"]):
        raise ValueError("실습 문맥의 형식이나 명시 예시 표지가 다릅니다")
    for key in ("document_sha256", "text_sha256", "fragment_sha256"):
        if c.SHA256_RE.fullmatch(item[key]) is None:
            raise ValueError("실습 문맥의 해시 형식이 다릅니다")
    marker_start, marker_end = _span(item["location"])
    scope_start, scope_end = _span(item["scope_location"])
    fragment_start, fragment_end = _span(item["fragment_range"])
    if (marker_end - marker_start != len(item["text"])
            or scope_start != marker_start or marker_end > scope_end
            or not marker_start <= fragment_start < fragment_end <= scope_end
            or _hash(item["text"]) != item["text_sha256"]):
        raise ValueError("실습 문맥의 표지·범위·조각 위치나 해시가 다릅니다")
    for key, expected in (("document_id", document_id), ("document_sha256", document_sha256),
                          ("fragment_location", fragment_location), ("fragment_sha256", fragment_sha256)):
        if expected and item[key] != expected:
            raise ValueError("실습 문맥이 원래 문서·조각과 다릅니다")
    if fragment_text is not None and (_hash(fragment_text) != item["fragment_sha256"]
            or len(fragment_text) != fragment_end - fragment_start):
        raise ValueError("실습 문맥이 정확 자기 인용과 다릅니다")
    if json.dumps(item, ensure_ascii=False, sort_keys=True, separators=(",", ":")) != raw:
        raise ValueError("실습 문맥의 직렬화가 정본과 다릅니다")
    if document_text is not None:
        if (scope_end > len(document_text) or _hash(document_text) != item["document_sha256"]
                or document_text[marker_start:marker_end] != item["text"]
                or _hash(document_text[fragment_start:fragment_end]) != item["fragment_sha256"]):
            raise ValueError("실습 문맥이 실제 문서 원문과 다릅니다")
    return item


def practice_context_fingerprint(raw: str) -> str:
    parse_practice_context(raw)
    return _hash(raw) if raw else ""
