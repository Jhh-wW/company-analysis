"""명시 품목 표를 인용한 산문의 공급 대상을 같은 부문·행에 묶는다.

일반 산문의 모든 명사를 축자로 재지 않는다. 공급 대상을 명시한 경우에만
품목 칸과 같은 행의 설명을 확인하며, 다른 행·표제·다른 인용은 빌리지 않는다.
"""
from collections.abc import Mapping
import re
import unicodedata

from src.features.composer import portfolio_product_scope_constants as c
from src.features.composer.scope_constants import SCOPE_CONDITION_UNBOUND
from src.features.composer.portfolio_name_constants import PORTFOLIO_NAME_BRACKET_SPAN_RE
from src.features.composer.business_population_scope_constants import REVENUE_TABLE_OWNER_RE
from src.shared.revenue_population_constants import REVENUE_ITEM_HEADER_RE
from src.shared.revenue_population_scope import revenue_product_name_span


def _surface(value: str) -> str:
    return "".join(char for char in unicodedata.normalize("NFKC", value).casefold()
                   if not char.isspace() and not unicodedata.category(char).startswith("P"))


def _owner(value: str) -> str:
    return c.PRODUCT_OWNER_SUFFIX_RE.sub("", _surface(value))


def _rows(source: str) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """완전한 명시 품목 열과 같은 행의 설명만 읽는다."""
    if "|" not in source:
        return ()
    found = []
    headers = ()
    heading = ""
    own_table = True
    for raw in c.PRODUCT_TABLE_BOUNDARY_RE.split(unicodedata.normalize("NFKC", source)):
        explicit_owners = sorted((*c.PRODUCT_TABLE_OWNER_RE.finditer(raw),
                                  *REVENUE_TABLE_OWNER_RE.finditer(raw)), key=lambda match: match.start())
        if explicit_owners:
            own_table = _surface(explicit_owners[-1]["owner"]) in c.PRODUCT_SELF_ACTORS
            headers = ()
        title = c.PRODUCT_TABLE_HEADING_RE.match(raw)
        if title:
            heading = c.PRODUCT_OWNER_PART_RE.split(title["owner"])[-1]
            headers = ()
        if "|" not in raw:
            headers = ()
            continue
        columns = tuple(cell.strip() for cell in raw.strip().strip("|").split("|"))
        items = [i for i, cell in enumerate(columns) if REVENUE_ITEM_HEADER_RE.fullmatch(cell)]
        if items and (not headers or any(_surface(cell) in c.PRODUCT_TABLE_OWNER_HEADERS
                                        or _surface(cell) in c.PRODUCT_TABLE_DESCRIPTION_HEADERS
                                        for cell in columns)):
            headers = columns if len(items) == 1 else ()
            continue
        if not headers or len(columns) != len(headers) or not own_table:
            continue
        if all(c.PRODUCT_ALIGNMENT_RE.fullmatch(cell) for cell in columns):
            continue
        span = revenue_product_name_span("|".join(columns), "|".join(headers))
        if span is None:
            continue
        item = "|".join(columns)[span[0]:span[1]]
        if not item or _surface(item) in c.PRODUCT_GENERIC_ITEMS:
            continue
        owner_columns = [i for i, cell in enumerate(headers)
                         if _surface(cell) in c.PRODUCT_TABLE_OWNER_HEADERS]
        if len(owner_columns) > 1:
            continue
        row_owner = columns[owner_columns[0]] if owner_columns else ""
        if (heading and c.PRODUCT_OWNER_SUFFIX_RE.search(_surface(row_owner))
                and _owner(heading) != _owner(row_owner)):
            continue
        # 표제는 부분 범위를 좁힌다. 품목 행의 용도에서 새 부문을 추측하지 않는다.
        actor = heading or row_owner
        descriptions = tuple(columns[i] for i, cell in enumerate(headers)
                             if _surface(cell) in c.PRODUCT_TABLE_DESCRIPTION_HEADERS)
        labelled_items = tuple(item + " " + columns[i] for i, cell in enumerate(headers)
                               if _surface(cell) in c.PRODUCT_TABLE_TYPE_HEADERS
                               and _surface(columns[i]) in c.PRODUCT_TABLE_TYPES)
        found.append((actor, (item, *labelled_items, *descriptions)))
    return tuple(found)


def _same_actor(actor: str, row_actor: str) -> bool:
    if _surface(actor) in c.PRODUCT_SELF_ACTORS:
        return True
    candidate, original = _owner(actor), _owner(row_actor)
    return bool(candidate and original and candidate == original)


def _name_supported(item: str, cells: tuple[str, ...]) -> bool:
    item = c.PRODUCT_NAME_SUFFIX_RE.sub("", item)
    previous = None
    while item != previous:
        previous, item = item, c.PRODUCT_NAME_PREFIX_RE.sub("", item)
    item = item.strip()
    candidate = _surface(item)
    if not candidate or candidate in c.PRODUCT_GENERIC_ITEMS:
        return True
    for index, cell in enumerate(cells):
        variants = (cell,)
        if index == 0:
            variants = (cell, PORTFOLIO_NAME_BRACKET_SPAN_RE.sub("", cell),
                        *(match[1] for match in PORTFOLIO_NAME_BRACKET_SPAN_RE.finditer(cell)),
                        *c.PRODUCT_LIST_RE.split(cell))
        originals = tuple(_surface(value) for value in variants)
        # 같은 칸에 명시된 별칭·약어는 보존한다. 다른 행의 설명은 대조하지 않는다.
        if candidate in originals:
            return True
        # 용도 수식어를 붙인 정확한 품목 이름도 같은 행에서 확인한다.
        for original in originals:
            if not original or not candidate.endswith(original):
                continue
            prefix = c.PRODUCT_MODIFIER_RE.sub("", candidate[:-len(original)])
            described = c.PRODUCT_MODIFIER_RE.sub("", "".join(_surface(value) for value in cells))
            if prefix and prefix in described:
                return True
    return False


def _claims(text: str):
    """명시 주어와 목적격 공급 대상만 읽는다."""
    for clause in c.PRODUCT_CLAUSE_RE.split(unicodedata.normalize("NFKC", text)):
        if "|" in clause:
            continue
        actors = tuple(c.PRODUCT_ACTOR_RE.finditer(clause))
        if not actors:
            continue
        actor = actors[-1]
        content = clause[actor.end():]
        if c.PRODUCT_DENIAL_RE.search(content):
            continue
        object_match = c.PRODUCT_OBJECT_RE.fullmatch(content.strip())
        if not object_match or not c.PRODUCT_ACTION_RE.search(object_match["tail"]):
            continue
        items = tuple(item for item in c.PRODUCT_LIST_RE.split(object_match["items"]) if item.strip())
        yield actor["actor"], items


def portfolio_product_scope_problem(text: str, own_sources: Mapping[str, str]) -> str:
    """명시 자기 품목 표의 공급 대상만 검사한다. 빈 결과는 의미 승인이 아니다."""
    has_table = any(any(REVENUE_ITEM_HEADER_RE.fullmatch(cell.strip())
                        for cell in raw.strip().strip("|").split("|"))
                    for source in own_sources.values() for raw in c.PRODUCT_TABLE_BOUNDARY_RE.split(source)
                    if "|" in raw)
    if not has_table:
        return ""
    rows = tuple(row for source in own_sources.values() for row in _rows(source))
    # 함께 선택한 산문이 같은 부문의 공급 대상을 직접 밝힌 경우도 보존한다.
    direct = tuple((actor, (item,)) for source in own_sources.values()
                   for line in source.splitlines() if "|" not in line
                   for actor, items in _claims(line) for item in items)
    for actor, items in _claims(text):
        matching = tuple(cells for row_actor, cells in (*rows, *direct) if _same_actor(actor, row_actor))
        for item in items:
            if not any(_name_supported(item, cells) for cells in matching):
                return SCOPE_CONDITION_UNBOUND
    return ""
