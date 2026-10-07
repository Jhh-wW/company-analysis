"""자기정의 표의 앞 두 공식 칸만 자기 인용 표현에 결속한다.

이 검사는 공식 칸에 없는 표현을 더하는 경우를 제한한다. 조합의 의미·주체·시점은
기존 의미 검수가 판단하며 빈 결과가 전체 표의 승인이나 세번째 해석의 증명은 아니다.
"""
from collections.abc import Mapping, Sequence
import re
import unicodedata

from src.features.composer.constants import IDENTITY_TABLE_HEADERS
from src.features.composer.identity_flow_scope_constants import (
    ASCII_EDGE_RE, EXPRESSION_EDGE_RE, EXPRESSION_SEPARATOR_RE, GRAMMATICAL_TAILS,
    NOMINAL_BOUNDARY_PARTICLE_RE, OFFICIAL_CELL_COUNT, GRAMMATICAL_TOKEN_EDGE_RE,
)
from src.features.composer.scope_constants import SCOPE_CONDITION_UNBOUND


def _surface(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).casefold().split())


def _found(expression: str, sources: Sequence[str], *, lexical_end: bool = False) -> bool:
    surface = _surface(expression)
    if not surface:
        return False
    if lexical_end:
        # 조사처럼 보이는 명사의 끝 글자를 떼어 다른 단어의 일부를 빌리지 않는다.
        # 줄인 표현은 원문의 전체 단어·구 또는 그 뒤 문법 꼬리에만 맞춘다.
        phrase = r"\s*".join(re.escape(char) for char in surface)
        tails = "|".join(re.escape(tail) for tail in GRAMMATICAL_TAILS)
        pattern = (rf"(?<!{GRAMMATICAL_TOKEN_EDGE_RE})" + phrase
                   + rf"(?:{tails})?(?!{GRAMMATICAL_TOKEN_EDGE_RE})")
        return any(re.search(pattern, unicodedata.normalize("NFKC", source).casefold())
                   for source in sources)
    # 짧은 영문 약어가 다른 영문 단어 안에 있는 경우를 근거로 쓰지 않는다.
    prefix = rf"(?<!{ASCII_EDGE_RE})" if surface[0].isascii() else ""
    suffix = rf"(?!{ASCII_EDGE_RE})" if surface[-1].isascii() else ""
    patterns = [re.escape(surface)]
    words = tuple(_surface(word) for word in expression.split() if _surface(word))
    if len(words) > 1:
        # '센서를 제조'→'센서 제조'처럼 원문의 목적격·관형 조사만 생략한다.
        # 후보의 실제 단어 경계에서만 허용하며 동사·대상·동의어를 만들지 않는다.
        patterns.append(NOMINAL_BOUNDARY_PARTICLE_RE.join(re.escape(word) for word in words))
    return any(re.search(prefix + pattern + suffix, _surface(source)) for pattern in patterns for source in sources)


def _supported(expression: str, sources: Sequence[str]) -> bool:
    expression = EXPRESSION_EDGE_RE.sub("", expression)
    if _found(expression, sources):
        return True
    for tail in GRAMMATICAL_TAILS:
        if expression.endswith(tail) and _found(expression[:-len(tail)], sources, lexical_end=True):
            return True
    return False


def identity_flow_scope_problem(cells: Sequence[str], sources: Mapping[str, str]) -> str:
    """공식 두 칸을 각각 검사한다. 다른 후보·미인용 조각·해석 칸은 빌리지 않는다."""
    if len(cells) != len(IDENTITY_TABLE_HEADERS):
        return SCOPE_CONDITION_UNBOUND
    own_sources = tuple(text for text in sources.values() if text.strip())
    if not own_sources:
        return SCOPE_CONDITION_UNBOUND
    for index, raw in enumerate(cells[:OFFICIAL_CELL_COUNT]):
        raw = unicodedata.normalize("NFKC", raw).strip()
        header = IDENTITY_TABLE_HEADERS[index]
        header_pattern = r"\s*".join(re.escape(word) for word in header.split())
        raw = re.sub(r"^" + header_pattern + r"\s*[:：]\s*", "", raw, count=1)
        # 사업 범위가 미상인 법적 자기정의 행은 빈 칸 그대로 보존할 수 있다.
        if not raw:
            if index == 0:
                return SCOPE_CONDITION_UNBOUND
            continue
        if _supported(raw, own_sources):
            continue
        parts = tuple(part.strip() for part in EXPRESSION_SEPARATOR_RE.split(raw) if part.strip())
        if not parts or not all(_supported(part, own_sources) for part in parts):
            return SCOPE_CONDITION_UNBOUND
    return ""
