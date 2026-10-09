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


def _amortization_ranges(text: str) -> tuple[tuple[int, int], ...]:
    """닫힌 상각·원가계상 술어만 원문 좌표로 돌려준다."""
    ranges = []
    start = 0
    for boundary in (*c.AMORTIZATION_UNIT_SPLIT_RE.finditer(text), None):
        end = boundary.start() if boundary else len(text)
        unit = text[start:end]
        chars, offsets = [], []
        for index, character in enumerate(unit):
            for normalized in unicodedata.normalize("NFKC", character).casefold():
                if not normalized.isspace():
                    chars.append(normalized)
                    offsets.append(start + index)
        surface = "".join(chars)
        # 고객 자산을 다루는 서비스는 회사 자체 자산의 상각 정책과 다르다.
        if not c.EXTERNAL_ASSET_ACCOUNTING_RE.search(surface):
            for pattern in (c.ASSET_AMORTIZATION_POLICY_RE, c.AMORTIZATION_COST_POLICY_RE):
                for match in pattern.finditer(surface):
                    ranges.append((offsets[match.start()], offsets[match.end() - 1] + 1))
        start = boundary.end() if boundary else len(text)
    return tuple(sorted(set(ranges)))


def amortization_accounting_policy(text: str) -> bool:
    """다른 사업 사실이 남지 않는 순수 상각 정책만 제한한다."""
    ranges = _amortization_ranges(text)
    if not ranges:
        return False
    kept, cursor = [], 0
    for begin, end in ranges:
        if begin >= cursor:
            kept.append(text[cursor:begin])
        cursor = max(cursor, end)
    kept.append(text[cursor:])
    surface = "".join(unicodedata.normalize("NFKC", "".join(kept)).casefold().split())
    return bool(c.AMORTIZATION_RESIDUE_RE.fullmatch(surface))


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
            local_ranges = list(_amortization_ranges(text[start:end]))
            if mixed is not None:
                local_ranges.append(mixed)
            if not local_ranges:
                kept.append(text[start:next_start])
            else:
                cursor = start
                for local_begin, local_end in sorted(set(local_ranges)):
                    begin, finish = start + local_begin, start + local_end
                    if begin >= cursor:
                        kept.append(text[cursor:begin])
                    spans.append((begin, finish))
                    cursor = max(cursor, finish)
                kept.append(text[cursor:next_start])
        start = next_start
    return OverheadAllocationScope("".join(kept) if spans else text, tuple(spans))
