"""이미 승인된 현재 사업 설명의 첫 문장 배치 조건."""

import re

from src.features.composer.extractive_summary_constants import SUMMARY_IDENTITY_PURPOSE_PATTERN


IDENTITY_BUSINESS_DEFINITION_SLOT = "identity:business_definition"
IDENTITY_CURRENT_TIME_STATE = "present"
IDENTITY_ACTUAL_FACT_STATUS = "actual"
IDENTITY_VERIFIED_STATE = "verified"

# 업종이나 제품 이름을 제한하지 않는다. 운영 방식의 설명을 회사 정의로
# 오인하지 않도록 기업유형을 명사 서술형으로 마치는 문장만 우선 배치한다.
IDENTITY_ENTITY_DEFINITION_END_RE = re.compile(
    r"(?:기업|회사|업체|제조사|공급사|사업자|플랫폼|대행사|운영사|개발사|유통사|기관|법인)"
    r"(?:이다|다|입니다)[.!]?$"
)

# 시점 메타가 현재여도 설립 목적 자체를 현재 사업 요약으로 승격하지 않는다.
IDENTITY_PURPOSE_RE = re.compile(SUMMARY_IDENTITY_PURPOSE_PATTERN)
# 앞 문장이 있어야 뜻이 닫히는 명시적 지시어만 배치에서 제외한다.
# 이 검사는 내용의 진위를 판정하거나 문장 글자를 바꾸지 않는다.
IDENTITY_DEPENDENT_OPENING_RE = re.compile(
    r"^(?:이는|이후|이러한|이를|이에|이와|이처럼|그러한|이\s+같은|그\s+결과)(?:\s|[,，])"
)
