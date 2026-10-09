"""일반 분할 근거에 인적·물적 유형을 추가하는 범위 확대를 막는다."""
from collections.abc import Mapping
from dataclasses import dataclass
import unicodedata

from src.features.composer import division_type_scope_constants as c
from src.features.composer.scope_constants import SCOPE_CONDITION_UNBOUND


def _surface(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).split())


def _subjects(text: str):
    # 존재하는 이력·금액의 주어를 분할 사건의 행위자로 읽지 않는다.
    return [match for match in c.BARE_SUBJECT_RE.finditer(text)
            if _surface(match["name"]) not in c.PRONOUN_NAMES
            and not c.NON_OWNER_PREDICATE_RE.match(text[match.end():])]


@dataclass(frozen=True)
class _Division:
    mode: str
    entity: str
    business: str
    date: str
    formal_self_creation: bool = False


def _events(text: str, *, support: bool, aliases: frozenset[str] = frozenset()) -> list[_Division]:
    events = []
    normalized = c.SELF_ALIAS_RE.sub("당사는", unicodedata.normalize("NFKC", text))
    normalized = c.BARE_SUBJECT_RE.sub(
        lambda match: "당사는 " if _surface(match["name"]) in aliases else match.group(), normalized,
    )
    for sentence in c.SENTENCE_RE.split(normalized):
        inherited_owner = ""
        for unit in c.CLAUSE_RE.split(sentence):
            subjects_in_unit = _subjects(unit)
            if subjects_in_unit:
                inherited_owner = _surface(subjects_in_unit[-1]["name"])
            for match in c.TYPE_RE.finditer(unit):
                tail = unit[match.end():]
                if c.DEFINITION_RE.match(tail):
                    continue
                if c.NONFACT_RE.search(unit):
                    continue
                if support and not c.FACT_TAIL_RE.match(tail):
                    continue
                prefix = c.LEADING_WORD_RE.sub("", unit[:match.start()])
                entity = ""
                # 유형에 바로 이어지는 분리·설립 수식의 법인을 우선 결속한다.
                adnominal = c.ADNOMINAL_TAIL_RE.match(tail)
                created = c.CREATED_ENTITY_RE.search(tail)
                if created and (adnominal or tail.lstrip().startswith(("하여", "해서", "해"))):
                    entity = _surface(created["name"])
                else:
                    owners = list(c.CORPORATE_RE.finditer(prefix))
                    suffix_owners = list(c.SUFFIX_CORPORATE_RE.finditer(prefix))
                    subjects = _subjects(prefix)
                    if owners:
                        entity = _surface(owners[-1]["name"])
                    elif suffix_owners:
                        entity = _surface(suffix_owners[-1]["name"])
                    elif subjects and _surface(subjects[-1]["name"]) not in c.PRONOUN_NAMES:
                        entity = _surface(subjects[-1]["name"])
                    else:
                        entity = inherited_owner
                if entity in c.SELF_NAMES:
                    entity = "self"
                boundaries = list(c.EVENT_PREFIX_RE.finditer(prefix))
                object_prefix = prefix[boundaries[-1].end():] if boundaries else prefix
                object_match = c.OBJECT_RE.search(object_prefix)
                business = _surface(c.PARENTHETICAL_RE.sub("", object_match["object"])) if object_match else ""
                dates = list(c.DATE_RE.finditer(prefix))
                date = _surface(dates[-1].group()) if dates else ""
                formal_self_creation = bool((c.SUFFIX_CORPORATE_RE.search(prefix) or c.CORPORATE_RE.search(prefix))
                                            and c.SELF_CREATED_RE.match(tail)
                                            and not c.EXTERNAL_OWNER_RE.search(prefix))
                for item in c.BUSINESS_LIST_RE.split(business):
                    events.append(_Division(match["mode"], entity, item, date, formal_self_creation))
    return events


def division_type_scope_problem(text: str, sources: Mapping[str, str]) -> str:
    """같은 법인·사업·명시 시점의 사실형 분할 유형만 지원으로 쓴다."""
    aliases = frozenset(_surface(match["name"]) for source in sources.values()
                        for match in c.SELF_ALIAS_RE.finditer(unicodedata.normalize("NFKC", source)))
    claims = _events(text, support=False, aliases=aliases)
    if not claims:
        return ""
    supports = [event for source in sources.values() for event in _events(source, support=True, aliases=aliases)]
    for claim in claims:
        if not any(
            claim.mode == event.mode
            and ((claim.entity == event.entity if claim.entity else event.entity in ("", "self"))
                 or (claim.formal_self_creation and event.entity == "self" and claim.business and claim.date))
            and (not claim.business or claim.business == event.business)
            and (not claim.date or event.date.startswith(claim.date))
            and event.entity not in c.FOREIGN_NAMES
            for event in supports
        ):
            return SCOPE_CONDITION_UNBOUND
    return ""
