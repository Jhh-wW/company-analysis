"""자기인용에 없는 분할사업과 설립법인의 관계를 수식절로 추가하지 못하게 한다."""
from collections.abc import Mapping
import unicodedata

from src.features.composer import founding_event_scope_constants as c
from src.features.composer.scope_constants import SCOPE_CONDITION_UNBOUND


def _surface(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).split())


def _relations(text: str) -> list[tuple[str, str, str, str]]:
    relations = []
    for unit in c.UNIT_RE.split(unicodedata.normalize("NFKC", text)):
        for pattern in c.RELATION_PATTERNS:
            for match in pattern.finditer(unit):
                if c.RELATION_NONFACT_RE.search(_surface(match.group())):
                    continue
                if c.RELATION_DENIAL_TAIL_RE.match(unit[match.end():]):
                    continue
                raw_object = _surface(match['object'])
                owner_object = c.LEADING_DATE_RE.sub('', raw_object, count=1)
                owner = ('foreign' if c.FOREIGN_OWNER_RE.match(owner_object)
                         else 'self' if c.SELF_OWNER_RE.match(owner_object) else '')
                division = c.OBJECT_SUBJECT_PREFIX_RE.sub('', raw_object, count=1)
                division = c.LEADING_DATE_RE.sub('', division, count=1)
                if owner:
                    division = c.FOREIGN_OWNER_RE.sub('', division, count=1)
                    division = c.SELF_OWNER_RE.sub('', division, count=1)
                # 능동·주어 구문은 정규식이 이미 조사를 분리했다. 법인명 끝 글자를 다시 자르지 않는다.
                entity = (c.ENTITY_PARTICLE_RE.sub('', match['entity'], count=1)
                          if pattern is c.ADNOMINAL_RELATION_RE else match['entity'])
                if division and entity:
                    relations.append((division, entity, owner, match['mode'] or ''))
    return relations


def founding_event_scope_problem(text: str, sources: Mapping[str, str]) -> str:
    """같은 자기인용의 분할사업과 설립법인 사이 직접 관계만 지원으로 쓴다."""
    claims = _relations(text)
    if not claims:
        return ''
    supports = [relation for source in sources.values() for relation in _relations(source)]
    for division, entity, owner, mode in claims:
        if not any(division == source_division and entity == source_entity
                   and (not owner or owner == source_owner)
                   and (not mode or mode == source_mode)
                   for source_division, source_entity, source_owner, source_mode in supports):
            return SCOPE_CONDITION_UNBOUND
    return ''
