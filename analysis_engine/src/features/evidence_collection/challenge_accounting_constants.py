"""5장 과제·대응으로 승격할 수 없는 평상시 회계·반품 조건."""
from __future__ import annotations

import re
from typing import Final

POLICY_SUBJECT_PATTERN: Final[str] = r"수익|매출|거래가격|변동대가|반품|환불|보증책임|보증충당|충당부채|재화의인도"
POLICY_TREATMENT_PATTERN: Final[str] = (
    r"수익(?:을|으로)?인식|매출(?:을|로)?인식|수익인식|거래가격|변동대가|"
    r"(?:수익|매출)[^.!?。;\n]{0,80}인식|"
    r"위험이구매자에게이전|수령승인요건|"
    r"(?:할인|반품|환불|보증책임|충당부채)[^.!?。;\n]{0,60}(?:추정|인식|측정|계상)|"
    r"(?:추정|인식|측정|계상)[^.!?。;\n]{0,60}(?:반품|환불|보증책임|충당부채)|"
    r"(?:불량)?(?:재화|제품|상품)[^.!?。;\n]{0,32}반품할권리|반품권(?:을|이|을가지)"
)
ACTUAL_EVENT_PATTERN: Final[str] = (
    r"(?:리콜|결함|불량률|반품률|반품|환불|사고|규제|제재|소송|분쟁)"
    r"[^.!?。;\n]{0,48}(?:(?:발생|증가|급증|상승|악화|확대|적발|중단|"
    r"실시|진행|시행|착수)(?:했|하였|됐|되었|하여|하고있|으로|로|에따라)|"
    r"명령을받|받았|받고|겪었|당했|제기됐)|"
    r"(?:리콜|시정|판매중단|생산중단)(?:을|를)?(?:명령|실시|시행|결정)"
    r"(?:했|하였|한|하여|하고있)|"
    r"(?:제품|생산|품질|서비스)[^.!?。;\n]{0,40}(?:결함이발견|사고가발생)"
)
BUSINESS_SERVICE_PATTERN: Final[str] = (
    r"(?:고객|고객사|기업|거래처)(?:에게|에|대상으로|을위해|를위해)"
    r"[^.!?。;\n]{0,80}(?:회계|감사|세무|수익인식|반품관리|보증관리|금융|대출|유동성)"
    r"[^.!?。;\n]{0,40}(?:서비스|자문|솔루션|플랫폼|시스템|상품|보고서)"
    r"[^.!?。;\n]{0,16}(?:제공|판매|운영|개발|발행)(?:한다|합니다|하며|하고있|했|하였|중|해왔)"
)
POLICY_SUBJECT_RE = re.compile(POLICY_SUBJECT_PATTERN)
FINANCIAL_SUBJECT_PATTERN: Final[str] = r"유동성|금융부채|공정가치|차입금|차입이자|대출|부채상환|충당부채"
BUSINESS_OPERATION_PATTERN: Final[str] = (
    r"(?:생산|납품|공장|주문|판매|공급|제품|고객|차주)"
    r"[^.!?。;\n]{0,48}(?:중단|지연|취소|차질|피해|연체|불량|결함|손실|이탈)"
    r"(?:했|하였|됐|되었|하여|하고있|이발생|가발생|율이상승|가증가|이증가|으로|로)|"
    r"(?:고객|차주)[^.!?。;\n]{0,32}(?:연체율|부실률|대출손실률)"
    r"[^.!?。;\n]{0,16}(?:상승|급증|증가)"
)
FINANCIAL_SUBJECT_RE = re.compile(FINANCIAL_SUBJECT_PATTERN)
BUSINESS_OPERATION_RE = re.compile(BUSINESS_OPERATION_PATTERN)
HYPOTHETICAL_EVENT_PATTERN: Final[str] = (
    r"(?:결함|사고|손실|반품|연체율|불량률)[^.!?。;\n]{0,48}"
    r"(?:발견되면|발생할|상승할|증가할|급증할|중단할)|"
    r"(?:발생|발견|상승|증가|중단)(?:하는|되는|한|된)경우"
)
HYPOTHETICAL_EVENT_RE = re.compile(HYPOTHETICAL_EVENT_PATTERN)
POLICY_SUBCLAUSE_PATTERN: Final[str] = r"(?<!\d),(?!\d)\s*|(?<=으며)\s+"
POLICY_SUBCLAUSE_RE = re.compile(POLICY_SUBCLAUSE_PATTERN)
POLICY_TREATMENT_RE = re.compile(POLICY_TREATMENT_PATTERN)
ACTUAL_EVENT_RE = re.compile(ACTUAL_EVENT_PATTERN)
BUSINESS_SERVICE_RE = re.compile(BUSINESS_SERVICE_PATTERN)
