"""계약의 존재에서 추가·독립 계약 관계를 만들지 않는다."""

from collections.abc import Mapping

from src.features.composer.grounding_constants import TABLE_SOURCE_ID
from src.features.composer.prose_own_source_constants import SUPPORT_TERM_TAIL_RE
from src.features.composer.transaction_independence_constants import (
    ACCOUNTING_PREFIX_RE, INDEPENDENCE_MARKER_RE, QUALIFIED_TRANSACTION_RE, TRANSACTION_ACTION_RE,
    TRANSACTION_ACTIVITY_TERM_LIMIT, TRANSACTION_REPORTING_RE,
    TRANSACTION_FOREIGN_ACTORS, TRANSACTION_GENERIC_ACTORS, TRANSACTION_GENERIC_WORDS,
    TRANSACTION_INDEPENDENCE_UNBOUND, TRANSACTION_NONACTUAL_RE,
    TRANSACTION_NOUN_RE, TRANSACTION_SPLIT_RE, TRANSACTION_SUBJECT_RE,
    TRANSACTION_WORD_RE,
)


def _qualified_clauses(text: str) -> tuple[str, ...]:
    result = []
    for clause in TRANSACTION_SPLIT_RE.split(text):
        if not TRANSACTION_ACTION_RE.search(clause):
            continue
        # 회계범위는 별도 가드가 담당하며 계약 독립성의 표지가 아니다.
        searchable = ACCOUNTING_PREFIX_RE.sub('', clause)
        if any(not TRANSACTION_REPORTING_RE.search(match.group('object') or '')
               for match in QUALIFIED_TRANSACTION_RE.finditer(searchable)):
            result.append(clause)
    return tuple(result)


def _activity_terms(clause: str) -> frozenset[str]:
    match = next((match for match in QUALIFIED_TRANSACTION_RE.finditer(clause)
                  if not TRANSACTION_REPORTING_RE.search(match.group('object') or '')), None)
    if match is None:
        return frozenset()
    noun = list(TRANSACTION_NOUN_RE.finditer(clause, match.start(), match.end()))[-1]
    head = clause[:noun.start()]
    # 명시 주어와 회사명은 거래 활동명이 아니다. 조사는 기존 지지어 계약으로 정리한다.
    subject = TRANSACTION_SUBJECT_RE.match(head.strip())
    if subject:
        head = head.strip()[subject.end():]
    head = INDEPENDENCE_MARKER_RE.sub('', head)
    words = [SUPPORT_TERM_TAIL_RE.sub('', word, count=1).casefold()
             for word in TRANSACTION_WORD_RE.findall(head)
             if word not in TRANSACTION_GENERIC_WORDS]
    return frozenset(words[-TRANSACTION_ACTIVITY_TERM_LIMIT:])


def _explicit_actor(clause: str) -> str:
    match = TRANSACTION_SUBJECT_RE.match(clause.strip())
    if not match:
        return ''
    actor = match['actor']
    return '' if actor in TRANSACTION_GENERIC_ACTORS else actor


def transaction_independence_problem(candidate: str, sources: Mapping[str, str]) -> str:
    """자기 인용 하나의 같은 계약 활동에 직접 적힌 추가·독립 관계만 인정한다."""
    for clause in _qualified_clauses(candidate):
        terms = _activity_terms(clause)
        actor = _explicit_actor(clause)
        supported = False
        for source_id, text in sources.items():
            if source_id == TABLE_SOURCE_ID:
                continue
            for source_clause in _qualified_clauses(text):
                if TRANSACTION_NONACTUAL_RE.search(source_clause):
                    continue
                source_actor = _explicit_actor(source_clause)
                if (source_actor in TRANSACTION_FOREIGN_ACTORS and source_actor != actor
                        or source_actor and actor and source_actor != actor):
                    continue
                # 계약 명칭 없는 표지나 다른 거래의 표지만으로 관계를 빌리지 않는다.
                source_terms = _activity_terms(source_clause)
                # 같은 거래어의 띄어쓰기 분할(미래 시점/미래시점)은 새 관계가 아니다.
                if (terms and all(any(term in source_term for source_term in source_terms)
                                  for term in terms)) or (not terms and not source_terms):
                    supported = True
                    break
            if supported:
                break
        if not supported:
            return TRANSACTION_INDEPENDENCE_UNBOUND
    return ''
