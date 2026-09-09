"""사업 사례에서 조직문화를 추론한 명시적 구문만 추가 제한한다.

빈 문자열은 문화가 검증됐다는 뜻이 아니다. 공식 문화·구체적인 절차가 있는
자료의 주어, 시점, 주장 범주 일치는 기존 의미 검수에서 판단해야 한다.
"""

from collections.abc import Mapping
import unicodedata

from src.features.composer.culture_constants import (
    CULTURE_ATTRIBUTION_RE,
    CULTURE_EVIDENCE_SCOPE_MISMATCH,
    EXPLICIT_CULTURE_RE,
    ORGANIZATIONAL_CLAIM_RE,
    SOURCE_CLAUSE_SPLIT_RE,
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
