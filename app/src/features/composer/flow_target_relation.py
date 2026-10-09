"""운영 도식에서 명시 대상의 행동 관계만 검사하고 일반 요약·산문은 유지한다."""
from collections.abc import Mapping, Sequence
import unicodedata

from src.features.composer.flow_target_relation_constants import (
    FLOW_TARGET_ACTION_INDEX, FLOW_TARGET_ACTION_RE, FLOW_TARGET_ACTION_SWITCH_RE, FLOW_TARGET_CELL_COUNT,
    FLOW_TARGET_CLAUSE_RE, FLOW_TARGET_GENERIC_TERMS, FLOW_TARGET_LIST_RE,
    FLOW_TARGET_LIST_TAIL_RE, FLOW_TARGET_MAX_PAIRS, FLOW_TARGET_NEGATION_RE,
    FLOW_TARGET_PASSIVE_RE, FLOW_TARGET_QUOTED_NAME_RE, FLOW_TARGET_RECIPIENT_INDEX,
    FLOW_TARGET_RELATION_GUIDE, FLOW_TARGET_LOCAL_ACTION_RE,
    FLOW_TARGET_ROLE_TAIL_RE, FLOW_TARGET_ROLE_PARTICLE_RE,
    FLOW_TARGET_ROLE_PREFIX_RE, FLOW_TARGET_ROLE_GROUP_RE,
    FLOW_TARGET_ROLE_JOIN_RE, FLOW_TARGET_EXPLICIT_ROLE_RE,
    FLOW_TARGET_ACTION_LIST_PREFIX_RE,
    FLOW_TARGET_PRODUCT_ACTIONS, FLOW_TARGET_FOREIGN_SUBJECT_RE,
    FLOW_TARGET_RELATION_SECTION, FLOW_TARGET_RELATION_TYPE,
    FLOW_TARGET_RELATION_UNBOUND, FLOW_TARGET_SUBJECT_RE,
)


def _surface(text: str) -> str:
    """공백·인용부호만 접으며 행동·대상 낱말은 바꾸지 않는다."""
    return "".join(char for char in unicodedata.normalize("NFKC", text).casefold()
                   if not char.isspace() and char not in "'‘’“”\"")


def _role_groups(text: str) -> tuple[str, ...]:
    """양쪽에 대상·행동을 명시한 접속만 나누고 대상·행동 목록은 유지한다."""
    groups = []
    for group in FLOW_TARGET_ROLE_GROUP_RE.split(text):
        start = 0
        for join in FLOW_TARGET_ROLE_JOIN_RE.finditer(group):
            if (FLOW_TARGET_EXPLICIT_ROLE_RE.search(group[start:join.start()])
                    and FLOW_TARGET_EXPLICIT_ROLE_RE.search(group[join.end():])
                    and not FLOW_TARGET_ACTION_LIST_PREFIX_RE.match(group[join.end():])):
                groups.append(group[start:join.start()])
                start = join.end()
        groups.append(group[start:])
    return tuple(groups)


def flow_target_relation_pairs(cells: Sequence[str], sources: Mapping[str, str],
                               *, section_id: str) -> tuple[tuple[str, str], ...]:
    """명시 역할의 모든 대상과 자기 원문에서 발견한 목록 대상을 요구한다."""
    if section_id != FLOW_TARGET_RELATION_SECTION or len(cells) != FLOW_TARGET_CELL_COUNT:
        return ()
    action, recipient = cells[FLOW_TARGET_ACTION_INDEX], cells[FLOW_TARGET_RECIPIENT_INDEX]
    actions = tuple(dict.fromkeys(match.group() for match in FLOW_TARGET_ACTION_RE.finditer(action)))
    if not actions:
        return ()
    source_keys = tuple(_surface(text) for text in sources.values())
    pairs = []
    # 열거의 끝 '등 ...' 설명은 앞의 이름과 같은 대상으로 중복 요구하지 않는다.
    literal = FLOW_TARGET_LIST_TAIL_RE.split(recipient, maxsplit=1)[0]
    for group in _role_groups(literal):
        local = tuple(match.group() for match in FLOW_TARGET_LOCAL_ACTION_RE.finditer(group))
        # 칸에서 역할을 직접 구분한 대상에 다른 대상의 행동까지 요구하지 않는다.
        scoped_actions = tuple(activity for activity in actions
                               if activity in local or (activity == "협력" and "파트너십" in local)) if local else actions
        target_text = FLOW_TARGET_ROLE_TAIL_RE.sub("", group).strip() if local else group
        targets = []
        for part in FLOW_TARGET_LIST_RE.split(target_text):
            part = FLOW_TARGET_ROLE_PREFIX_RE.sub("", part.strip())
            if local:
                part = FLOW_TARGET_ROLE_PARTICLE_RE.sub("", part).strip()
            # 명시 역할의 미지원 대상도 남겨 관계 검사가 원문 누락을 거절한다.
            if (part and _surface(part) not in FLOW_TARGET_GENERIC_TERMS
                    and (local or any(_surface(part) in source for source in source_keys))):
                targets.append(part)
        for source in sources.values():
            for match in FLOW_TARGET_QUOTED_NAME_RE.finditer(source):
                name = match.group(1).strip()
                if _surface(name) in _surface(target_text):
                    targets.append(name)
        pairs.extend((target, activity) for target in dict.fromkeys(targets) for activity in scoped_actions)
    return tuple(dict.fromkeys(pairs))


def flow_target_relation_hint(cells: Sequence[str], sources: Mapping[str, str],
                              *, section_id: str) -> str:
    pairs = flow_target_relation_pairs(cells, sources, section_id=section_id)
    if len(pairs) > FLOW_TARGET_MAX_PAIRS:
        return "  전달 대상·행동 쌍이 검증 상한을 넘어 이 행은 공개할 수 없다. 행을 나눠 자기 관계만 적는다.\n"
    return (FLOW_TARGET_RELATION_GUIDE.format(pairs="; ".join(f"{target} → {action}" for target, action in pairs))
            if pairs else "")


def _supported(target: str, action: str, quote: str, source: str, product: str,
               product_is_explicit: bool) -> bool:
    target_key, action_key, quote_key = map(_surface, (target, action, quote))
    if not quote_key or quote_key not in _surface(source):
        return False
    for clause in FLOW_TARGET_CLAUSE_RE.split(FLOW_TARGET_ACTION_SWITCH_RE.sub(";", source)):
        key = _surface(clause)
        # 구절은 여러 절을 포함할 수 있어도 쌍의 근거는 같은 절 안이어야 한다.
        if not key or not (key in quote_key or quote_key in key):
            continue
        start = key.find(target_key)
        if start < 0:
            continue
        for match in FLOW_TARGET_LOCAL_ACTION_RE.finditer(clause):
            if (_surface(match.group()) != action_key
                    and not (action == "협력" and match.group() == "파트너십")):
                continue
            if start >= len(_surface(clause[:match.start()])):
                continue
            if FLOW_TARGET_NEGATION_RE.match(clause[match.end():]):
                continue
            if FLOW_TARGET_FOREIGN_SUBJECT_RE.search(clause[:match.start()]):
                continue
            if (action in FLOW_TARGET_PRODUCT_ACTIONS and product_is_explicit
                    and _surface(product) not in key):
                continue
            # 전달 대상 이름이 행동의 주체인 원문을 수령인으로 뒤집지 않는다.
            raw_start = clause.casefold().find(target.casefold())
            if raw_start >= 0:
                tail = clause[raw_start + len(target):].lstrip("'‘’“”\"")
                if FLOW_TARGET_SUBJECT_RE.match(tail) and not FLOW_TARGET_PASSIVE_RE.search(tail):
                    continue
            if target_key in quote_key and (action_key in quote_key
                    or (action == "협력" and "파트너십" in quote_key)):
                return True
    return False


def flow_target_relation_problem(cells: Sequence[str], sources: Mapping[str, str],
                                 entries: object, *, section_id: str) -> str:
    """명시된 전달 대상 각각의 행동을 다른 절·다른 인용에서 빌리지 않는다."""
    pairs = flow_target_relation_pairs(cells, sources, section_id=section_id)
    if not pairs:
        return ""
    if len(pairs) > FLOW_TARGET_MAX_PAIRS:
        return FLOW_TARGET_RELATION_UNBOUND
    values = entries.get("관계", ()) if isinstance(entries, Mapping) else ()
    if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
        values = ()
    proofs = [item for item in values if isinstance(item, Mapping) and item.get("유형") == FLOW_TARGET_RELATION_TYPE]
    product = cells[0]
    product_is_explicit = bool(_surface(product)) and any(_surface(product) in _surface(source)
                                                         for source in sources.values())
    if not proofs:
        # 기존 검수가 참이고 자기 원문의 같은 절이 모든 쌍을 직접 지원하면
        # 새 배열 미제출만으로 정상 경로를 지우지 않는다. 절을 합치지는 않는다.
        return "" if all(any(_supported(target, action, source, source, product, product_is_explicit)
                             for source in sources.values())
                         for target, action in pairs) else FLOW_TARGET_RELATION_UNBOUND
    for target, action in pairs:
        valid = False
        for item in proofs:
            if any(type(item.get(field)) is not str for field in ("대상", "역할값", "원문", "근거")):
                continue
            if (_surface(item["대상"]) != _surface(target)
                    or _surface(item["역할값"]) != _surface(action)
                    or item["근거"] not in sources):
                continue
            if _supported(target, action, item["원문"], sources[item["근거"]], product, product_is_explicit):
                valid = True
                break
        if not valid:
            return FLOW_TARGET_RELATION_UNBOUND
    return ""
