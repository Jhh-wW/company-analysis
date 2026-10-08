"""즉시 채점한 후보를 의미 칸별 유한한 몫으로 보관한다."""
from __future__ import annotations

from dataclasses import dataclass
from collections import OrderedDict

from features.evidence_collection import constants as c
from features.evidence_collection.business_constraint_signal_constants import DIRECT_RELATION_REASONS_BY_SLOT
from features.evidence_collection.relevance import SlotScore
from features.evidence_collection.segment import FragmentCandidate
from features.evidence_collection.official_industry_discovery import official_industry_discovery
from features.evidence_collection import official_industry_discovery_constants as industry_c


@dataclass(frozen=True)
class ScoredCandidate:
    index: int
    candidate: FragmentCandidate
    scores: tuple[SlotScore, ...]
    rank_index: int


def _direct_relation_priority(score: SlotScore) -> bool:
    return bool(DIRECT_RELATION_REASONS_BY_SLOT.get(score.slot_id, frozenset()).intersection(score.reason_codes))


class _UnclassifiedPool:
    """문맥 우선순위와 최근 순서로 보관하며 교체는 상각 O(1)이다."""

    def __init__(self, count_limit: int, char_limit: int) -> None:
        self.count_limit = count_limit
        self.char_limit = char_limit
        self.chars = 0
        self.ordinary: OrderedDict[str, tuple[int, FragmentCandidate]] = OrderedDict()
        self.important: OrderedDict[str, tuple[int, FragmentCandidate]] = OrderedDict()
        self.industry: OrderedDict[str, tuple[int, FragmentCandidate]] = OrderedDict()

    def __len__(self) -> int:
        return len(self.ordinary) + len(self.important) + len(self.industry)

    def offer(self, index: int, candidate: FragmentCandidate, *, industry: bool = False) -> None:
        body = candidate.text
        if self.count_limit <= 0 or len(body) > self.char_limit:
            return
        important = bool(c.UNCLASSIFIED_PRIORITY_PATTERN.search(body)) or any(
            marker in body for marker in c.CHANGE_CONTEXT_MARKERS
        )
        pool = self.industry if industry else self.important if important else self.ordinary
        if pool.pop(body, None) is None:
            self.chars += len(body)
        pool[body] = (index, candidate)
        # 산업 탐색은 기존 총량의 작은 몫만 보호한다. 그 몫 안에서도 최근
        # 원문을 보관하며 신원·일반 무분류 후보의 전체 몫을 독점하지 않는다.
        if industry:
            while (len(self.industry) > industry_c.DISCOVERY_RETAINED_COUNT
                   or sum(len(value) for value in self.industry) > industry_c.DISCOVERY_RETAINED_CHARS):
                old_body, _ = self.industry.popitem(last=False)
                self.chars -= len(old_body)
        while len(self) > self.count_limit or self.chars > self.char_limit:
            evicted = self.ordinary if self.ordinary else self.important if self.important else self.industry
            old_body, _ = evicted.popitem(last=False)
            self.chars -= len(old_body)

    def selected(self) -> list[tuple[int, FragmentCandidate]]:
        return sorted((*self.ordinary.values(), *self.important.values(), *self.industry.values()))


class CandidateRetention:
    """고득점·최근·변경 문맥을 슬롯별로 분리해 앞 장의 독점을 막는다.

    모든 슬롯 몫의 합도 기존 일반 후보 문자/개수 상한 안에 둔다. 원문을
    복사하거나 점수를 부풀리지 않고 후보 객체 참조만 공유한다.
    """
    def __init__(self, allowed_slots: frozenset[str]) -> None:
        self.pools: dict[tuple[str, str], list[ScoredCandidate]] = {}
        self.scored_seen = 0
        self.unclassified_seen = 0
        self.short_seen = 0
        self.unclassified = _UnclassifiedPool(
            c.MAX_UNCLASSIFIED_CANDIDATES_PER_DOCUMENT, c.MAX_UNCLASSIFIED_CHARS_PER_DOCUMENT,
        )
        self.short = _UnclassifiedPool(
            c.MAX_SHORT_OBSERVATION_CANDIDATES_PER_DOCUMENT, c.MAX_SHORT_OBSERVATION_CHARS_PER_DOCUMENT,
        )
        self.slot_count = max(1, len(allowed_slots))
        self.slot_count_limit = c.MAX_LONG_FRAGMENT_CANDIDATES_PER_DOCUMENT // self.slot_count
        self.slot_char_limit = c.MAX_LONG_FRAGMENT_CHARS_PER_DOCUMENT // self.slot_count

    def offer_scored(self, index: int, candidate: FragmentCandidate, scores: tuple[SlotScore, ...],
                     *, rank_index: int | None = None) -> None:
        self.scored_seen += 1
        item = ScoredCandidate(index, candidate, scores, index if rank_index is None else rank_index)
        change = any(marker in candidate.text for marker in c.CHANGE_CONTEXT_MARKERS)
        lane_specs = (("top", c.RETAINED_TOP_PER_SLOT), ("recent", c.RETAINED_RECENT_PER_SLOT),
                      ("change", c.RETAINED_CHANGE_PER_SLOT if change else 0))
        full_quota = c.RETAINED_TOP_PER_SLOT + c.RETAINED_RECENT_PER_SLOT + c.RETAINED_CHANGE_PER_SLOT
        for score in scores:
            for lane, quota in lane_specs:
                if not quota:
                    continue
                count_limit = min(quota, self.slot_count_limit * quota // full_quota)
                char_limit = self.slot_char_limit * quota // full_quota
                pool = self.pools.setdefault((score.slot_id, lane), [])
                # 같은 문장이 수천 번 나와도 다양성 몫을 독점하지 않는다.
                if lane == "top" and any(old.candidate.text == candidate.text for old in pool):
                    continue
                pool[:] = [old for old in pool if old.candidate.text != candidate.text]
                pool.append(item)
                if lane == "top":
                    def priority(value: ScoredCandidate) -> tuple[int, bool, int]:
                        own_score = next(s for s in value.scores if s.slot_id == score.slot_id)
                        return (-own_score.score_millis, not _direct_relation_priority(own_score), value.rank_index)
                    pool.sort(key=priority)
                else:
                    pool.sort(key=lambda value: -value.rank_index)
                while len(pool) > count_limit or sum(len(value.candidate.text) for value in pool) > char_limit:
                    pool.pop()

    def offer_unclassified(self, index: int, candidate: FragmentCandidate) -> None:
        if candidate.is_short:
            self.short_seen += 1
            pool = self.short
        else:
            self.unclassified_seen += 1
            pool = self.unclassified
        # 기존 무분류 상한 안에서 산업 보조 조사 원문을 먼저 보관한다.
        # 장 점수·필수 칸·작성 순위에는 이 표지를 사용하지 않는다.
        pool.offer(index, candidate, industry=not candidate.is_short and official_industry_discovery(candidate.text))

    def selected_scored(self) -> list[tuple[int, FragmentCandidate, tuple[SlotScore, ...]]]:
        selected = {item.index: item for pool in self.pools.values() for item in pool}
        return [(item.index, item.candidate, item.scores) for _, item in sorted(selected.items())]

    def selected_unclassified(self) -> list[tuple[str, FragmentCandidate]]:
        long_candidates = self.unclassified.selected()
        result = [(f"unclassified{index}", value) for index, value in long_candidates]
        for index, candidate in self.short.selected():
            if not any(value.start <= candidate.start and candidate.end <= value.end for _, value in long_candidates):
                result.append((f"short{index}", candidate))
        return sorted(result, key=lambda item: item[1].start)

    def context_reasons(self, index: int) -> tuple[str, ...]:
        reasons = []
        if any(item.index == index for (slot, lane), pool in self.pools.items() if lane == "change" for item in pool):
            reasons.append(c.REASON_CHANGE_CONTEXT)
        if any(item.index == index for (slot, lane), pool in self.pools.items() if lane == "recent" for item in pool):
            reasons.append(c.REASON_RECENT_CONTEXT)
        return tuple(reasons)
