"""사업 사례·미래 목표를 조직문화로 격상하는 좁은 경계를 제한한다.

빈 문자열은 문화가 검증됐다는 뜻이 아니다. 공식 문화·구체적인 절차가 있는
자료의 주어, 시점, 주장 범주 일치는 기존 의미 검수에서 판단해야 한다.
"""

from collections.abc import Mapping, Sequence
import unicodedata

from src.features.composer.culture_constants import (
    CULTURE_ATTRIBUTION_RE,
    CULTURE_EVIDENCE_SCOPE_MISMATCH,
    CULTURE_FLOW_CELL_COUNT,
    CURRENT_CULTURE_DENIAL_RE,
    EXPLICIT_CULTURE_RE,
    FLOW_GOAL_QUALIFIER_RE,
    GOAL_NEGATION_RE,
    ORGANIZATIONAL_CLAIM_RE,
    OTHER_ORGANIZATION_RE,
    SOURCE_CLAUSE_SPLIT_RE,
    SOURCE_GOAL_RE,
    SOURCE_UNAVAILABLE_RE,
)


def _surface(text: str) -> str:
    return "".join(unicodedata.normalize("NFKC", text).casefold().split())


def culture_problem(text: str, sources_mapping: Mapping[str, str]) -> str:
    """문화 장 후보의 근거 범위 확대가 보일 때 고정 사유를 반환한다.

    호출자는 culture 장/슬롯 또는 원래 장이 생략된 요약 후보와 인용한
    원문만 전달한다. 공시라는 출처 유형만으로 사업 실적을 공식문화로
    인정하지 않는다. 구체적 절차·문화가 있는 원문의 적절한 바꿔쓰기는
    이 어휘 검사만으로 거절하지 않는다.
    """
    candidate = _surface(text)
    if not (ORGANIZATIONAL_CLAIM_RE.search(candidate)
            and CULTURE_ATTRIBUTION_RE.search(candidate)):
        return ""

    for source in sources_mapping.values():
        if candidate and candidate in _surface(source):
            return ""
        for clause in SOURCE_CLAUSE_SPLIT_RE.split(source):
            normalized = _surface(clause)
            if (EXPLICIT_CULTURE_RE.search(normalized)
                    and not SOURCE_UNAVAILABLE_RE.search(normalized)):
                # 이는 승인 조건이 아니다. 명시된 문화·절차가 후보의 바로 그
                # 주장인지와 출처 공식성은 같은 기존 검수 호출이 판정한다.
                return ""
    return CULTURE_EVIDENCE_SCOPE_MISMATCH


def culture_flow_problem(cells: Sequence[str], sources_mapping: Mapping[str, str]) -> str:
    """사업목표를 현재의 일하는 원칙으로 격상한 culture 행만 제한한다.

    culture 장에서만, 원래 순서의 세 칸(가치/원칙/사례)과 그 행의 인용
    원문을 전달한다. 열 제목이 이미 문화 의미를 부여하므로 셀에 '문화'가
    없어도 검사한다. 같은 절에 가치 셀의 원문 표현과 목표 표지가 있는
    경우만 다루며, 낯선 바꿔쓰기·법인 동일성·공식성은 의미 검수에 남긴다.
    빈 결과는 승인도, 목표가 거짓이라는 판정도 아니다.
    """
    if isinstance(cells, (str, bytes)) or len(cells) != CULTURE_FLOW_CELL_COUNT:
        return ""  # 형식 검사는 기존 도식 검증기가 담당한다.
    value, principle = (_surface(cell) for cell in cells[:2])
    if not value or not principle:
        return ""
    explicit_attribution = (ORGANIZATIONAL_CLAIM_RE.search(principle)
                            and CULTURE_ATTRIBUTION_RE.search(principle))
    if (FLOW_GOAL_QUALIFIER_RE.search(principle) and not GOAL_NEGATION_RE.search(principle)
            and not explicit_attribution):
        return ""  # 원칙 칸 자체가 계획임을 명시한 경우 현재형 격상은 아니다.
    clauses = [_surface(clause) for source in sources_mapping.values()
               for clause in SOURCE_CLAUSE_SPLIT_RE.split(source)]
    goal_clauses = [clause for clause in clauses if value in clause
                    and SOURCE_GOAL_RE.search(clause) and not GOAL_NEGATION_RE.search(clause)]
    if not goal_clauses:
        return ""  # 목표 근거와 결속하지 못한 행을 문화 어휘만으로 삭제하지 않는다.
    for clause in clauses:
        if (principle in clause and EXPLICIT_CULTURE_RE.search(clause)
                and not SOURCE_GOAL_RE.search(clause)
                and not SOURCE_UNAVAILABLE_RE.search(clause)
                and not CURRENT_CULTURE_DENIAL_RE.search(clause)
                and not OTHER_ORGANIZATION_RE.search(clause)):
            # 다른 절의 문화 단어만으로 면제하지 않는다. 바로 이 원칙을
            # 명시한 현재 절차가 있으면 그 의미·주체 검증은 기존 검수에 남긴다.
            return ""
    return CULTURE_EVIDENCE_SCOPE_MISMATCH
