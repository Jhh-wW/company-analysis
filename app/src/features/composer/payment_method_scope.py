"""자기 인용의 한 거래에 한정된 결제수단을 다른 거래에서 빌리지 않는다.

명시된 거래 이름과 약어 목록, '모두'처럼 닫힌 범위가 있는 경우만 검사한다.
일반 결제 설명이나 해외거래 전체의 합집합을 거래별 조건으로 바꾸지 않는다.
"""

from collections.abc import Mapping
import unicodedata

from src.features.composer.payment_method_scope_constants import (
    PAYMENT_CLAUSE_END_RE, PAYMENT_CLOSED_RE, PAYMENT_LIST_BRIDGE_RE,
    PAYMENT_METHOD_RE, PAYMENT_NONASSERTION_RE, PAYMENT_SCOPE_PROBLEM,
    PAYMENT_SCOPE_SECTIONS, PAYMENT_SUBJECT_TAIL_RE, PAYMENT_TRADE_RE,
)


def _key(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).casefold().split())


def _methods(text: str) -> tuple[frozenset[str], str]:
    matches = list(PAYMENT_METHOD_RE.finditer(text))
    if not matches:
        return frozenset(), ""
    selected = [matches[0]]
    for current in matches[1:]:
        if not PAYMENT_LIST_BRIDGE_RE.fullmatch(text[selected[-1].end():current.start()]):
            break
        selected.append(current)
    return frozenset(_key(match.group()) for match in selected), text[:matches[0].start()]


def _relations(text: str):
    text = unicodedata.normalize("NFKC", text)
    for name in PAYMENT_TRADE_RE.finditer(text):
        tail = PAYMENT_SUBJECT_TAIL_RE.match(text, name.end())
        if tail is None:
            continue
        end = PAYMENT_CLAUSE_END_RE.search(text, tail.end())
        clause = text[tail.end():end.start() if end else len(text)]
        methods, before = _methods(clause)
        if methods:
            yield _key(name.group()), methods, before, clause


def payment_method_scope_problem(
    text: str, own_sources: Mapping[str, str], *, section_id: str,
) -> str:
    """빈 문자열은 이 좁은 조건의 모순이 없다는 뜻이며 사실 승인이 아니다."""
    if section_id not in PAYMENT_SCOPE_SECTIONS:
        return ""
    originals = [relation for source in own_sources.values() for relation in _relations(source)]
    for name, methods, _, clause in _relations(text):
        if PAYMENT_NONASSERTION_RE.search(clause):
            continue
        same = [row for row in originals if row[0] == name]
        if not any(PAYMENT_CLOSED_RE.search(row[2]) for row in same):
            continue
        if any(methods.issubset(row[1]) for row in same):
            continue
        return PAYMENT_SCOPE_PROBLEM
    return ""
