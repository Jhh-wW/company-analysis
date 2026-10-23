"""현재 PG 정산 원문에서 첫 소개 후보만 만드는 닫힌 문형."""

import re
from typing import Final

from src.features.composer.settlement_role_constants import SETTLEMENT_SOURCE_TEMPLATE


IDENTITY_SETTLEMENT_SOURCE_RE: Final[re.Pattern[str]] = re.compile(
    SETTLEMENT_SOURCE_TEMPLATE.format(target="가맹점")
)
# 정산 절이 맞아도 같은 조각·문맥의 역사·계획·타사·부정은 추측해 떼지 않는다.
# 원문49의 별도 회계 절에 있는 일반적인 '경우'는 전역 금칙어로 쓰지 않는다.
IDENTITY_SOURCE_UNCERTAIN_RE: Final[re.Pattern[str]] = re.compile(
    r"과거|종전|이전에는|당시|지난해|전기에는|설립|합병|분할"
    r"|했(?:다|습니다|으며)|하였|였(?:다|습니다)|이었|있었|되었"
    r"|향후|계획|예정|목표|추진|검토|가정|가설|가상"
    r"|타사|경쟁사|종속기업|종속회사|자회사|협력사"
    r"|않|하지못|아니|아닌|불가|중단|폐업|철수"
    r"|없(?:다|습니다|는|었)|한정|전용|일부|특정|만대상|만적용|에만|한다면|할때"
)
IDENTITY_SETTLEMENT_DRAFT_TEMPLATE: Final[str] = (
    "회사는 {business}을 통해 소비자의 결제 대금을 가맹점에 지급하는 기업이다."
)
