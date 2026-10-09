"""법인 신원이나 제재 대상 이름을 소속 관계의 증명으로 사용하지 않는다.

보고 회사의 소속을 덧붙인 명시 법인 수식에 대한 좁은 검사다.
다른 소유주를 명시한 관계는 기존 의미 검수에 맡긴다. 빈 결과는 전체 합격이 아니다.
인용 원문과 번호를 바꾸거나 인용하지 않은 다른 조각을 빌리지 않는다.
"""

from collections.abc import Mapping
import re
import unicodedata

from src.features.composer.entity_relationship_constants import (
    FOREIGN_OWNER_RE, FORMAL_NAME_RE, LEGAL_FORM_RE, NAMED_RELATION_RE,
    RELATION_KINDS, RELATION_NEGATION_RE, RELATION_PATTERN, TABLE_ENTITY_HEADERS,
    NAMED_OWNER_RE, SELF_OWNER_KEYS,
    TABLE_RELATION_HEADERS,
)
from src.features.composer.scope_constants import SCOPE_CONDITION_UNBOUND


def _key(value: str) -> str:
    return re.sub(r"\s+", "", LEGAL_FORM_RE.sub("", value)).casefold()


def _formal_actor(actor: str, sources: tuple[str, ...]) -> bool:
    if FORMAL_NAME_RE.search(actor):
        return True
    # 후보에서 법인 표기만 줄인 이름도 같은 원문에 정식 법인 표기가 있으면 다룬다.
    label = re.escape(actor.strip())
    return any(re.search(rf"(?:주식회사|\(주\))\s*{label}|{label}\s*(?:주식회사|\(주\))", source)
               for source in sources)


def _foreign_owner(prefix: str) -> bool:
    if FOREIGN_OWNER_RE.search(prefix):
        return True
    owner = NAMED_OWNER_RE.search(LEGAL_FORM_RE.sub("", prefix))
    return bool(owner and _key(owner["owner"]) not in SELF_OWNER_KEYS)


def _table_relationship(actor: str, kind: str, source: str) -> bool:
    entity_index = relation_index = None
    foreign_table = False
    for row in re.split(r"[;\n]", source):
        if "|" not in row:
            headings = tuple(re.finditer(RELATION_PATTERN, row))
            if headings:
                foreign_table = any(_foreign_owner(row[:match.start()]) for match in headings)
        cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
        normalized = [_key(cell) for cell in cells]
        entity_columns = [i for i, cell in enumerate(normalized) if cell in TABLE_ENTITY_HEADERS]
        relation_columns = [i for i, cell in enumerate(normalized) if cell in TABLE_RELATION_HEADERS]
        if entity_columns and relation_columns:
            entity_index, relation_index = entity_columns[0], relation_columns[0]
            continue
        if entity_index is None or relation_index is None:
            continue
        if max(entity_index, relation_index) >= len(cells):
            continue
        if (not foreign_table and _key(cells[entity_index]) == _key(actor)
                and RELATION_KINDS.get(cells[relation_index]) == kind):
            return True
    return False


def _relationship_recorded(actor: str, kind: str, sources: tuple[str, ...]) -> bool:
    name = re.escape(_key(actor))
    relation_words = "|".join(word for word, value in RELATION_KINDS.items() if value == kind)
    # 주격·관형 수식 양쪽을 지원하되 다른 이름의 관계를 빌리지 않는다.
    patterns = (
        re.compile(rf"(?:{relation_words})(?:인|중(?:의)?|가운데|에해당하는)?{name}(?=은|는|이|가|의|을|를|에|[,;.]|$)"),
        re.compile(rf"{name}(?:은|는|이|가)(?:당사의|회사의|본사의)?(?:{relation_words})(?:입니다|이다|다|이며|이고|로서)"),
    )
    for source in sources:
        if _table_relationship(actor, kind, source):
            return True
        for unit in re.split(r"[;\n]|(?<=[.!?])\s+", source):
            # 소유격의 낱말 경계를 유지하려고 정규화 위치를 공백이 남은 원문에 연결한다.
            visible = LEGAL_FORM_RE.sub("", unit).casefold()
            positions = [index for index, char in enumerate(visible) if not char.isspace()]
            normalized = "".join(visible[index] for index in positions)
            for pattern_index, pattern in enumerate(patterns):
                for match in pattern.finditer(normalized):
                    # 서술형 관계의 이름 앞에 다른 이름이 붙어 있으면 같은 법인이 아니다.
                    source_start = positions[match.start()]
                    if pattern_index == 1 and source_start and visible[source_start - 1].isalnum():
                        continue
                    if _foreign_owner(visible[:positions[match.start()]]):
                        continue
                    if RELATION_NEGATION_RE.search(normalized[match.end():]):
                        continue
                    return True
    return False


def entity_relationship_problem(candidate: str, sources: Mapping[str, str]) -> str:
    """제재·행동 자체가 지원돼도 별도로 추가한 명시 소속 수식은 자기 근거가 필요하다."""
    candidate = unicodedata.normalize("NFKC", candidate)
    originals = tuple(unicodedata.normalize("NFKC", source) for source in sources.values())
    for match in NAMED_RELATION_RE.finditer(candidate):
        if _foreign_owner(candidate[:match.start()]):
            continue
        actor = match["actor"].strip()
        if not _formal_actor(actor, originals):
            continue
        if not _relationship_recorded(actor, RELATION_KINDS[match["relation"]], originals):
            return SCOPE_CONDITION_UNBOUND
    return ""
