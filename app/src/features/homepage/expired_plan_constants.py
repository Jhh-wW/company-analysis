"""공식 홈페이지의 명시 일정 만료 판정에만 쓰는 닫힌 문형."""

from __future__ import annotations

import re
from typing import Final

FUTURE_SLOT_PREFIX: Final[str] = "future_strategy:"
SHORT_YEAR_CENTURY: Final[int] = 2000
YEARS_PER_CENTURY: Final[int] = 100
MONTHS_PER_QUARTER: Final[int] = 3
LAST_MONTH: Final[int] = 12
ONLY_SCHEDULE_COUNT: Final[int] = 1

# 두 자리 연도는 분기 표기에서만 허용한다. 임의 숫자·발표연도는 기한이 아니다.
PLAN_PERIOD_RE = re.compile(
    r"(?<![\dA-Za-z])(?P<year>20\d{2}|\d{2})\s*년"
    r"(?:\s*(?P<quarter>[1-4])\s*분기"
    r"|\s*(?P<month>\d{1,2})\s*월(?:\s*(?P<day>\d{1,2})\s*일)?)?"
    r"(?!\d)"
)
PLAN_DEADLINE_TAIL_RE = re.compile(
    r"\s*(?:중|내|말|까지|에)?\s*"
    r"(?:지원|제공|개최|출시|개시|개통|상용화|완공|준공|완료|달성)"
    r"(?:\s*(?:할|될|을))?\s*(?:예정|계획|목표)"
)
PLAN_CLAUSE_RE = re.compile(r"[^\n.!?;]+[.!?;]?")
PLAN_END_RE = re.compile(r"\s*(?:입니다|이다|이에요|임)?\s*[.!?;]?\s*$")
CURRENT_CAPABILITY_RE = re.compile(
    r"\s*[가-힣A-Za-z0-9·]+(?:를|을)\s*통해\s*한\s*번에\s*"
    r"(?:사용|이용)할\s*수\s*있(?:어요|습니다|다)\s*[.!?;]?\s*$"
)
# 날짜 앞 쉼표·종결형·조건형이 있으면 하나의 계획 명사구로 확정하지 않는다.
INDEPENDENT_PREFIX_RE = re.compile(r"[,，]|어요|습니다|한다|했다|이며|하며|하고|하면|되면|다면|경우")
CAPABILITY_DEPENDENCY_RE = re.compile(r"다음|내일|내년|조만간|부터|이후|조건|전제")
PLAN_SIGNAL_RE = re.compile(
    r"예정|계획|목표|로드맵|방침|재확인|연장|변경|조정|향후|앞으로|내(?:후)?년|장기|"
    r"전략|검토\s*중|곧|추후|"
    r"(?:추진|준비)(?:\s*중|하고|한다|합니다)|"
    r"(?:지원|제공|확대|개발|출시|도입|구축|투자|개최|진행)"
    r"(?:할|합니다|한다|하겠습니다|할\s*것)"
)
EVENT_SCHEDULE_LABEL_RE = re.compile(r"일시\s*[:：]\s*$")
EVENT_LABEL_COUNT_RE = re.compile(r"일시\s*[:：]")
EVENT_IDENTITY_RE = re.compile(r"세미나|행사|설명회|박람회|포럼|컨퍼런스|살롱|웨비나")
# 회사의 미래 전략과 구별되는 행사 교육 주제·용어 정의의 완결 문형이다.
EVENT_EXPLANATION_RE = re.compile(
    r"(?:[^.!?;\n]*사례를\s*통해\s*알아보는\s*[^.!?;\n]*전략"
    r"|[^.!?;\n]*란\s*[^.!?;\n]*전략을\s*의미합니다)\s*[.!?;]?\s*"
)
EVENT_TOPIC_SIGNAL: Final[str] = "전략"
EVENT_NOTICE_CLAUSE_RE = re.compile(
    r"\s*[*•-]?\s*(?:신청(?:해주신|한)\s*내용을\s*바탕으로\s*)?"
    r"(?:참석\s*대상자를\s*선정하여|참석자에게|선정(?:된|되신)\s*분들께)\s*"
    r"(?:별도\s*|(?:(?:현장과\s*)?온라인\s*참석\s*)?링크를\s*)?"
    r"안내(?:를)?\s*(?:드릴|할)\s*예정(?:입니다|이다|이에요)?\s*[.!?;]?\s*"
)
