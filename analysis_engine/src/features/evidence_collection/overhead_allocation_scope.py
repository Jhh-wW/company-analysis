"""원문과 좌표를 유지하며 조건부 원가 배부 절의 운영 역할 지원만 제한한다."""
from dataclasses import dataclass
import unicodedata

from features.evidence_collection import overhead_allocation_constants as c


def overhead_allocation_policy(text: str) -> bool:
    surface = "".join(unicodedata.normalize("NFKC", text).casefold().split())
    return bool(c.OVERHEAD_ALLOCATION_SUBJECT_RE.search(surface)
                and c.OVERHEAD_ALLOCATION_TREATMENT_RE.search(surface)
                and not c.OVERHEAD_ACTUAL_BUSINESS_RE.search(surface))


@dataclass(frozen=True)
class OverheadAllocationScope:
    score_text: str
    excluded_spans: tuple[tuple[int, int], ...] = ()


def _mixed_policy_range(text: str) -> tuple[int, int] | None:
    """실제 사건과 함께 있는 절에서도 조건부 처리의 원문 범위는 남긴다."""
    chars, offsets = [], []
    for index, character in enumerate(text):
        for normalized in unicodedata.normalize("NFKC", character).casefold():
            if not normalized.isspace():
                chars.append(normalized)
                offsets.append(index)
    surface = "".join(chars)
    if not c.OVERHEAD_ACTUAL_BUSINESS_RE.search(surface):
        return None
    subject = c.OVERHEAD_ALLOCATION_SUBJECT_RE.search(surface)
    treatment = c.OVERHEAD_ALLOCATION_TREATMENT_RE.search(surface, subject.end()) if subject else None
    if treatment is None:
        return None
    suffix = c.ALLOCATION_RECOGNITION_SUFFIX_RE.match(surface, treatment.end())
    end = suffix.end() if suffix else treatment.end()
    return offsets[subject.start()], offsets[end - 1] + 1


def overhead_allocation_scope(text: str) -> OverheadAllocationScope:
    spans = []
    kept = []
    start = 0
    for match in (*c.CLAUSE_SPLIT_RE.finditer(text), None):
        end = match.start() if match is not None else len(text)
        next_start = match.end() if match is not None else len(text)
        if overhead_allocation_policy(text[start:end]):
            spans.append((start, end))
        else:
            mixed = _mixed_policy_range(text[start:end])
            if mixed is None:
                kept.append(text[start:next_start])
            else:
                begin, finish = (start + point for point in mixed)
                spans.append((begin, finish))
                kept.append(text[start:begin] + text[finish:next_start])
        start = next_start
    return OverheadAllocationScope("".join(kept) if spans else text, tuple(spans))
