"""도식의 회사 정산 관계를 대조하는 닫힌 문법. 일반 동의어 치환은 하지 않는다."""

import re
from typing import Final

# 동일한 두 공제 항목의 접속 조사만 정규화한다. 세금은 회사 수익으로 바꾸지 않는다.
SETTLEMENT_FEES: Final[str] = r"수수료(?:·|와|및)부가가치세"
SETTLEMENT_TAX_WORD: Final[str] = "부가가치세"
SETTLEMENT_ROLE_RE: Final[re.Pattern[str]] = re.compile(
    rf"{SETTLEMENT_FEES}(?:를)?(?:차감후|제외하여)지급"
)
# 도식 검수자가 원문의 업종을 대상으로 적을 때도 수령인까지 같은 관계여야 한다.
SETTLEMENT_BUSINESS_ROLE_RE: Final[re.Pattern[str]] = re.compile(
    rf"{SETTLEMENT_FEES}(?:를)?(?:차감후|제외하여)가맹점(?:에게|에)지급"
)
SETTLEMENT_SOURCE_TEMPLATE: Final[str] = (
    r"(?:\(\d+\)금융예수금)?(?:회사|당사)는"
    r"(?:(?P<business>전자(?:지불|지급)결제대행업|결제대행업)을통하여)?"
    r"최종소비자가결제한거래금액에대해"
    r"원천사로부터대금을받은후,?"
    r"(?P<relation>(?:회사|당사)의{target}(?:으로|에게|에)"
    + SETTLEMENT_FEES + r"를제외하여지급하고있습니다)[.]?"
)
SETTLEMENT_CANDIDATE_TEMPLATE: Final[str] = (
    r"(?:최종소비자결제→원천사가회사에대금지급→)?"
    r"(?:회사|당사)가(?P<fees>" + SETTLEMENT_FEES + r")"
    r"(?:를)?차감후{target}(?:에게|에)지급"
    # 이 부가 절은 정산의 증명 범위가 아니다. 기존 시점·내용 검사가 별도로 대조한다.
    r"(?:;거래승인시점에수익인식)?[.]?"
)
SETTLEMENT_NONACTUAL_RE: Final[re.Pattern[str]] = re.compile(
    r"가상|가정|예시|예를들|사례를가정|교육용|교육을위한"
)

# 대상에 공제 항목을 적은 검수도 완결된 동일 정산 관계를 제출했을 때만 대조한다.
# 일반 대상/역할 부분 일치는 그대로 금지하며 수취인·방향·공제 항목은 고정한다.
SETTLEMENT_STATEMENT_TARGETS: Final[frozenset[str]] = frozenset({"수수료", "가맹점"})
SETTLEMENT_STATEMENT_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:대금흐름의구조를보면,)?(?:회사|당사)는"
    r"(?P<business>전자(?:지불|지급)결제대행업|결제대행업)을통하여"
    r"최종소비자가결제한거래금액에대해원천사로부터대금을받은후,?"
    r"(?:(?:회사|당사)의)?가맹점(?:으로|에게|에)"
    r"(?P<fees>" + SETTLEMENT_FEES + r")를제외하여"
    r"(?:지급하고있습니다|지급하며,지급할의무가발생하는시점에해당채무를"
    r"재무상태표상예수금으로표시한다)[.]?"
)
