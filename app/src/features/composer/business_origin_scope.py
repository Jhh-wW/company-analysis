"""현재 사업 구성만으로 과거의 사업 기원·전환 관계를 만들지 못하게 한다."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import unicodedata

from src.features.composer import business_origin_scope_constants as c
from src.features.composer.scope_constants import SCOPE_CONDITION_UNBOUND


def _surface(text: str) -> str:
    return ''.join(unicodedata.normalize('NFKC', text).casefold().split())


def _actor(text: str) -> str:
    matches = list(c.ACTOR_RE.finditer(text))
    if not matches:
        return 'self'
    name = _surface(matches[-1]['actor'])
    return 'self' if name in c.SELF_ACTORS else name


def _key(text: str) -> str:
    text = c.ACTOR_RE.sub('', text).strip()
    text = c.KEY_PREFIX_RE.sub('', text)
    # 연혁 표의 연도·창업 사건 칸은 제공물 이름으로 사용하지 않는다.
    text = text.split('|')[-1].strip()
    return _surface(c.KEY_DESCRIPTOR_RE.sub('', text)).strip('·:()[]')


def _owned_units(text: str):
    heading_actor = 'self'
    for unit in c.UNIT_RE.split(unicodedata.normalize('NFKC', text)):
        if not unit.strip():
            continue
        if c.FOREIGN_RE.search(unit) and ('연혁' in unit or '역사' in unit):
            heading_actor = 'foreign'
            continue
        if ('당사' in unit or '회사' == unit.strip()) and ('연혁' in unit or '역사' in unit):
            heading_actor = 'self'
        actor = _actor(unit)
        yield unit, heading_actor if actor == 'self' else actor


@dataclass(frozen=True)
class _Relation:
    actor: str
    prior: str
    next: str = ''
    exclusive: bool = False


def _relations(text: str) -> list[_Relation]:
    found = []
    for unit, heading_actor in _owned_units(text):
        for pattern in (c.ORIGIN_RE, c.TRANSITION_RE):
            for match in pattern.finditer(unit):
                if c.NON_BUSINESS_RE.search(match.group()):
                    continue
                tail = unit[match.end():]
                if c.FUTURE_TAIL_RE.match(tail) or c.DENIAL_TAIL_RE.match(tail):
                    continue
                prior = _key(match['prior'])
                next_business = _key(match['next']) if pattern is c.TRANSITION_RE else ''
                if prior and (pattern is c.ORIGIN_RE or next_business):
                    actor = _actor(unit[:match.end()])
                    found.append(_Relation(heading_actor if actor == 'self' else actor, prior, next_business,
                                           bool(c.EXCLUSIVE_RE.search(match['prior']))))
    return found


def _history_records(text: str) -> tuple[list[_Relation], list[tuple[str, str]]]:
    """자기 연혁의 초기 사업과 후속 확장을 읽되 서로 다른 법인 제목은 섞지 않는다."""
    initial, later = [], []
    for unit, actor in _owned_units(text):
        if c.FUTURE_UNIT_RE.search(unit) or c.DENIAL_TAIL_RE.search(unit):
            continue
        if c.INITIAL_RE.search(unit):
            for match in c.HISTORY_BUSINESS_RE.finditer(unit):
                key = _key(match['business'])
                if key:
                    initial.append(_Relation(actor, key, exclusive=bool(c.EXCLUSIVE_RE.search(unit))))
        for match in c.SPLIT_ORIGIN_RE.finditer(unit):
            key = _key(match['prior'])
            if key:
                initial.append(_Relation(actor, key))
        if c.LATER_RE.search(unit):
            # 명시 후속 시점 뒤의 사업명을 앞 기원 절 전체와 합치지 않는다.
            later_start = list(c.LATER_RE.finditer(unit))[-1].end()
            for match in c.EXPANSION_RE.finditer(unit[later_start:]):
                key = _key(match['business'])
                if key:
                    later.append((actor, key))
    return initial, later


def business_origin_scope_problem(candidate_text: str, sources: Mapping[str, str]) -> str:
    """직접 기원 관계 또는 같은 주체의 명시 연혁만 지원으로 인정한다."""
    claims = _relations(candidate_text)
    if not claims:
        return ''
    direct = [r for text in sources.values() for r in _relations(text)]
    records = [_history_records(text) for text in sources.values()]
    initial = [r for before, _ in records for r in before]
    later = [r for _, after in records for r in after]
    for claim in claims:
        supports = [r for r in (*direct, *initial)
                    if r.actor == claim.actor and r.prior == claim.prior
                    and (not claim.exclusive or r.exclusive)]
        if not supports:
            return SCOPE_CONDITION_UNBOUND
        if claim.next and not (any(r.next == claim.next for r in supports)
                               or (claim.actor, claim.next) in later):
            return SCOPE_CONDITION_UNBOUND
    return ''
