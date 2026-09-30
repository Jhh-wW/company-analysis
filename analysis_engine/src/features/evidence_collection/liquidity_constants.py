"""5장 유동성 회계 상용구를 채점에서만 가리는 좁은 표지."""

from __future__ import annotations

import re
from typing import Final


CHALLENGE_POLICY_SLOTS: Final[frozenset[str]] = frozenset({
    "current_challenges:issue", "current_challenges:response",
    "current_challenges:initial_signal", "current_challenges:unresolved_gap",
    "current_challenges:next_check",
})

# composer의 절 경계·유동성 규칙·회사 고유 면제와 동등하게 유지한다.
# 별도 배포 단위인 엔진에서 app 모듈을 직접 import하지 않는다.
CLAUSE_SPLIT_PATTERN: Final[str] = "[.!?。\n]+(?!['\"’”」』])"
# composer의 종국 가드는 후보를 판정하지만, 엔진은 조각 전체의 점수 근거를
# 버릴 수 있다. 소수점 양옆이 숫자면 절 경계로 보지 않아 금액 면제를 보존한다.
DECIMAL_SAFE_CLAUSE_SPLIT_PATTERN: Final[str] = (
    "(?:[;!?。\\n]|(?<!\\d)\\.|\\.(?!\\d))+(?!['\\\"’”」』])"
)
LIQUIDITY_SUBJECT_PATTERN: Final[str] = r"유동성"
LIQUIDITY_BOILERPLATE_PATTERN: Final[str] = (
    r"영업자금수요|예측하고관리|부채상환|자금수요를충당|"
    r"유동성위험(?:을|를|의)?(?:지속적으로|정기적으로|적절히)?관리"
)
MONETARY_UNIT_AMOUNT_PATTERN: Final[str] = (
    r"[0-9][0-9,]*(?:\.[0-9]+)?[조억만천백십]+원"
)
MONETARY_BARE_WON_PATTERN: Final[str] = (
    r"[0-9][0-9,]*(?:\.[0-9]+)?원(?:(?![가-힣])|(?=[을를이은는와과의에도]))"
)
MONETARY_FOREIGN_AMOUNT_PATTERN: Final[str] = (
    r"[0-9][0-9,]*(?:\.[0-9]+)?(?:usd|달러)|"
    r"\$[0-9]|[0-9][0-9,]*(?:\.[0-9]+)?\$"
)
COMPANY_EVENT_PATTERN: Final[str] = (
    r"(?:변경|전환|도입|폐지)(?:했|하였|됐|되었)"
)

# 숫자 없는 실제 압박도 무차별 상용구 취급하지 않는다. 이 표지는 상류의
# 보수적 보존에만 쓰고, 최종 진실성·공개 판정을 대신하지 않는다.
ACTUAL_PRESSURE_PATTERN: Final[str] = (
    r"(?:유동성|자금)(?:위험)?(?:이|가|의|에)?"
    r"(?:악화|부족|고갈|급감)|"
    r"(?:자금조달|부채상환)(?:에|이|가)?(?:차질|어려움)|"
    r"(?:차질|부족)(?:이|가)?(?:발생|심화)|"
    r"신용등급[^.!?。\n]{0,40}하락|"
    r"채무[^.!?。\n]{0,40}연체|"
    r"유동성위험[^.!?。\n]{0,40}(?:증가|확대|커졌|커지|급증)"
)
HEADING_ONLY_SURFACES: Final[frozenset[str]] = frozenset({"유동성위험"})
BUSINESS_OFFERING_PATTERN: Final[str] = (
    r"(?:서비스|상품|대출|자문)(?:를|을|의)?(?:제공|운영|판매|취급)|"
    r"(?:제공|운영|판매|취급)(?:하는|한)?(?:서비스|상품|대출|자문)|"
    r"(?:고객|거래처|이용자|회원|차주)(?:에게|에|을위해|를위해|대상으로)"
    r"[^.!?。\n;|]{1,80}(?:을|를)(?:제공|공급|판매|발행)"
)
# 관리 설명에 실제 사업 사건이 섞였으면 보존한다. 사업 대상과 이미 일어난
# 행위가 같은 절에 연결되어야 하며 '중단할 수 있다' 등 조건은 면제가 아니다.
BUSINESS_EVENT_PATTERN: Final[str] = (
    r"(?:생산|수주|납품|공장|주문|판매|서비스|공급|사업)"
    r"[^.!?。\n;|]{0,48}(?:중단|취소|지연|재개|폐쇄|축소)"
    r"(?:했(?:다|습니다)|하였(?:다|습니다)|됐(?:다|습니다)|"
    r"되었(?:다|습니다)|(?:하고|되어)있(?:다|습니다))"
)

CLAUSE_SPLIT_RE: Final[re.Pattern[str]] = re.compile(CLAUSE_SPLIT_PATTERN)
DECIMAL_SAFE_CLAUSE_SPLIT_RE: Final[re.Pattern[str]] = re.compile(DECIMAL_SAFE_CLAUSE_SPLIT_PATTERN)
LIQUIDITY_SUBJECT_RE: Final[re.Pattern[str]] = re.compile(LIQUIDITY_SUBJECT_PATTERN)
LIQUIDITY_BOILERPLATE_RE: Final[re.Pattern[str]] = re.compile(LIQUIDITY_BOILERPLATE_PATTERN)
EXEMPTION_RES: Final[tuple[re.Pattern[str], ...]] = tuple(map(re.compile, (
    MONETARY_UNIT_AMOUNT_PATTERN,
    MONETARY_BARE_WON_PATTERN,
    MONETARY_FOREIGN_AMOUNT_PATTERN,
    COMPANY_EVENT_PATTERN,
)))
ACTUAL_PRESSURE_RE: Final[re.Pattern[str]] = re.compile(ACTUAL_PRESSURE_PATTERN)
BUSINESS_OFFERING_RE: Final[re.Pattern[str]] = re.compile(BUSINESS_OFFERING_PATTERN)
BUSINESS_EVENT_RE: Final[re.Pattern[str]] = re.compile(BUSINESS_EVENT_PATTERN)
