"""원문에 없는 연결·별도 회계 범위를 후보에 추가하지 못하게 한다.

자기 인용의 같은 활동에 명시된 범위만 읽는다. 전역 실적표는 호출자가
그 후보의 수치 검증 성공을 확인했을 때만 포함한다. 빈 사유는 다른 의미·
숫자·법인·시점 검사의 면제가 아니다.
"""
from collections.abc import Mapping
import unicodedata

from src.features.composer.accounting_scope_constants import (
    ACCOUNTING_SCOPE_UNBOUND, BASIS_WORD_RE, CLAUSE_RE, CONSOLIDATED_ACTOR_RE,
    CONTENT_STOPWORDS, MIN_CONTENT_TOKEN_CHARS, PARTICLE_RE, SCOPE_HEADING_RE,
    SCOPE_KIND, SCOPE_PREFIX_RE, SCOPE_SUFFIX_RE, WORD_RE, PRODUCT_HEADER_RE,
    USE_HEADER_RE, FINANCIAL_METRIC_RE, TABLE_ROW_RE, NUMERIC_CELL_RE,
)
from src.features.composer.grounding_constants import TABLE_SOURCE_ID


def _scopes(text: str, *, source: bool = False) -> set[str]:
    scopes = {SCOPE_KIND[m['basis']] for pattern in (SCOPE_PREFIX_RE, SCOPE_SUFFIX_RE)
              for m in pattern.finditer(text)}
    if source and CONSOLIDATED_ACTOR_RE.search(text):
        scopes.add('consolidated')
    return scopes


def _tokens(text: str) -> set[str]:
    text = BASIS_WORD_RE.sub('', text)
    tokens = {PARTICLE_RE.sub('', m.group()) for m in WORD_RE.finditer(text)}
    return {t for t in tokens if len(t) >= MIN_CONTENT_TOKEN_CHARS and t not in CONTENT_STOPWORDS}


def _source_units(text: str):
    heading_scopes: set[str] = set()
    for unit in CLAUSE_RE.split(text):
        if not unit.strip():
            continue
        scopes = _scopes(unit, source=True)
        if SCOPE_HEADING_RE.fullmatch(unit):
            heading_scopes = scopes
            continue
        # 같은 인용의 명시 회계 표제만 이어받는다. 다른 활동 문장의 범위를
        # 다음 문장에 확산하지 않고, 새 명시 범위는 그 문장에서 우선한다.
        yield unit, scopes or heading_scopes


def _product_table(text: str) -> bool:
    for row in TABLE_ROW_RE.split(text):
        cells = [cell.strip().replace(' ', '') for cell in row.split('|')]
        if (any(PRODUCT_HEADER_RE.fullmatch(cell) for cell in cells)
                and any(USE_HEADER_RE.fullmatch(cell) for cell in cells)):
            return True
    return False


def _explicit_accounting_table(text: str) -> bool:
    """명시 품목·용도/매출 또는 재무실적 표만 새 검사의 대상으로 삼는다."""
    for row in TABLE_ROW_RE.split(text):
        cells = [cell.strip().replace(' ', '') for cell in row.split('|')]
        if len(cells) < 2:
            continue
        if _product_table(row):
            return True
        if (any(FINANCIAL_METRIC_RE.fullmatch(cell) for cell in cells)
                and (sum(bool(FINANCIAL_METRIC_RE.fullmatch(cell)) for cell in cells) > 1
                     or any(NUMERIC_CELL_RE.search(cell) for cell in cells))):
            return True
    return False


def accounting_scope_problem(
    candidate: str, sources: Mapping[str, str], *, allow_bound_table: bool = False,
) -> str:
    """후보가 추가한 회계 범위가 자기 활동 원문에 없으면 사유를 반환한다.

    적용 범위는 명시 제품/매출/재무실적 표다. 금융계약 산문에 빠진 부모절
    회계범위를 추정하거나 새 가드의 미지원으로 재판정하지 않는다.
    sources는 검수 후보의 자기 인용 맵이다. allow_bound_table은 기존 수치
    검증과 _numeric_binding_uses_table이 모두 통과한 호출자만 True로 준다.
    단순 TABLE 존재·미래근거/관계 선언은 이 권한을 주지 않는다.
    """
    text = unicodedata.normalize('NFKC', candidate)
    eligible = {sid: unicodedata.normalize('NFKC', raw) for sid, raw in sources.items()
                if isinstance(raw, str) and (sid != TABLE_SOURCE_ID or allow_bound_table)}
    # 총 실적 수치가 우연히 같은 값이어도 자기 상품표의 회계 범위를 증명하지
    # 못한다. 수치검증 권한은 제품·매출 구성표로 이전되지 않는다.
    if any(_product_table(raw) for sid, raw in eligible.items() if sid != TABLE_SOURCE_ID):
        eligible.pop(TABLE_SOURCE_ID, None)
    if not any(_explicit_accounting_table(raw) or (sid == TABLE_SOURCE_ID and allow_bound_table)
               for sid, raw in eligible.items()):
        return ''
    units = [item for raw in eligible.values() for item in _source_units(raw)]
    for clause in CLAUSE_RE.split(text):
        requested = _scopes(clause)
        if not requested:
            continue
        anchors = _tokens(clause)
        for scope in requested:
            if not any(scope in scopes and anchors & _tokens(unit) for unit, scopes in units):
                return ACCOUNTING_SCOPE_UNBOUND
    return ''
