"""문서에서 직접 읽은 사업부 표제를 조각 원문과 별도로 보존한다."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from itertools import chain

from features.evidence_collection import section_context_constants as c
from features.evidence_collection.section_context import (
    parse_section_context, section_heading_boundaries, SectionContextBudgetExceeded,
)


@dataclass(frozen=True)
class SectionScope:
    start: int
    end: int
    text: str


def section_scopes(document_text: str) -> tuple[SectionScope, ...]:
    result = []
    previous: tuple[int, str] | None = None
    for end, next_heading in chain(section_heading_boundaries(document_text), ((len(document_text), ""),)):
        if previous is not None:
            start, heading = previous
            if len(heading) <= c.MAX_HEADING_CHARS and c.BUSINESS_HEADING_RE.fullmatch(heading):
                if len(result) >= c.MAX_RETAINED_SCOPES:
                    raise SectionContextBudgetExceeded("보관할 사업 범위 문맥의 상한을 넘었습니다")
                result.append(SectionScope(start, end, heading))
        previous = end, next_heading
    return tuple(result)


def context_for_section_candidate(
    scopes: tuple[SectionScope, ...], *, text: str, start: int, end: int,
    document_id: str, document_sha256: str,
) -> str:
    """교차 부문 조각이나 표제가 없는 조각에는 사업 범위를 추측하지 않는다."""
    if len(text) != end - start:
        raise ValueError("사업 범위 후보의 원문 길이가 위치와 다릅니다")
    scope = next((item for item in scopes if item.start <= start < end <= item.end), None)
    if scope is None:
        return ""
    digest = lambda value: hashlib.sha256(value.encode("utf-8")).hexdigest()
    raw = json.dumps({
        "version": c.CONTEXT_VERSION,
        "document_id": document_id,
        "document_sha256": document_sha256,
        "text": scope.text,
        "location": f"{scope.start}-{scope.start + len(scope.text)}",
        "text_sha256": digest(scope.text),
        "scope_location": f"{scope.start}-{scope.end}",
        "fragment_location": f"{start}-{end}",
        "fragment_sha256": digest(text),
    }, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    parse_section_context(raw)
    return raw
