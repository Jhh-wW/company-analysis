"""이미 승인된 현재 사업 설명의 첫 문장 배치 조건."""

import re

from src.features.composer.extractive_summary_constants import SUMMARY_IDENTITY_PURPOSE_PATTERN


IDENTITY_BUSINESS_DEFINITION_SLOT = "identity:business_definition"
IDENTITY_CURRENT_TIME_STATE = "present"
IDENTITY_ACTUAL_FACT_STATUS = "actual"
IDENTITY_VERIFIED_STATE = "verified"

# 시점 메타가 현재여도 설립 목적 자체를 현재 사업 요약으로 승격하지 않는다.
IDENTITY_PURPOSE_RE = re.compile(SUMMARY_IDENTITY_PURPOSE_PATTERN)
# 앞 문장이 있어야 뜻이 닫히는 명시적 지시어만 배치에서 제외한다.
# 이 검사는 내용의 진위를 판정하거나 문장 글자를 바꾸지 않는다.
IDENTITY_DEPENDENT_OPENING_RE = re.compile(
    r"^(?:이는|이후|이러한|이를|이에|이와|이처럼|그러한|이\s+같은|그\s+결과)(?:\s|[,，])"
)
