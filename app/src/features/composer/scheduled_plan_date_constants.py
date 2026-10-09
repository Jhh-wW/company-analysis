"""명시 예정 기간과 같은 행사·행위의 현재화 대조 규칙."""

import re
from typing import Final

SCHEDULE_DATE_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<!\d)(?P<year>\d{4})(?:\s*년\s*(?:"
    r"(?P<quarter>[1-4])\s*분기|(?P<month>\d{1,2})\s*월\s*"
    r"(?:(?P<day>\d{1,2})\s*일)?"
    r")?|-(?P<iso_month>\d{2})-(?P<iso_day>\d{2}))(?!\d)"
)
SCHEDULE_CLAUSE_RE: Final[re.Pattern[str]] = re.compile(
    r"[.!?;\n]+|(?:했|하였|되었|됐)(?:고|으며|지만|으나)\s*|하지만\s*"
)
SCHEDULE_ACTION: Final[str] = r"개최|개시|착수|준공|완공|완료|개장|출시|개통|상용화|진행|상정|선임"
SCHEDULE_FUTURE_RE: Final[re.Pattern[str]] = re.compile(
    rf"(?P<action>{SCHEDULE_ACTION})(?:할|될|될\s*것으로|하기로|을)?\s*"
    r"(?:예정(?:이다|입니다|이며|인)|계획(?:이다|입니다|이며)|계획하고\s*있)"
)
SCHEDULE_MODIFIER_RE: Final[re.Pattern[str]] = re.compile(
    rf"(?:{SCHEDULE_ACTION})\s*예정인\s*(?:제\s*(?P<term>\d+)\s*기\s*)?"
    r"(?P<event>[가-힣A-Za-z][가-힣A-Za-z0-9·]*)\s*(?:에서|에|을|를)"
)
SCHEDULE_REFERENCE_PREFIX: Final[str] = r"(?:같은|해당|이번)"
SCHEDULE_PERIODIC_EVENT_PREFIX_RE: Final[re.Pattern[str]] = re.compile(r"^(?:정기|임시)")
SCHEDULE_AGENDA_RE: Final[re.Pattern[str]] = re.compile(r"안건|의\s*건")
SCHEDULE_AGENDA_ACTION_RE: Final[re.Pattern[str]] = re.compile(r"선임|해임|개정|승인")
SCHEDULE_RELATED_AGENDA_RE: Final[re.Pattern[str]] = re.compile(r"(?P<agenda>.+)(?:과|와)관련하여$")
SCHEDULE_UNQUALIFIED_REAPPOINTMENT_RE: Final[re.Pattern[str]] = re.compile(r"^중임(?:의)?건")
SCHEDULE_OBJECT_RE: Final[re.Pattern[str]] = re.compile(
    r"(?P<object>[가-힣A-Za-z][가-힣A-Za-z0-9·]*)\s*(?:을|를)\s*$"
)
SCHEDULE_DATE_LINK_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?:\s*(?:에|까지|중|말|초))?\s*"
)
SCHEDULE_NON_TARGET_RE: Final[re.Pattern[str]] = re.compile(
    r"이후|이래|부터|보고서|공시|제출일|매출|영업이익|순이익"
)
SCHEDULE_CHANGED_RE: Final[re.Pattern[str]] = re.compile(
    r"연기|연장|지연|중단|취소|철회|변경|미완료|미달|못했|못하고"
)
SCHEDULE_REPORTED_PLAN_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:예정|계획)(?:이라고|라고)\s*(?:밝혔|설명했|전했|발표했|기재했|명시했)"
    r"(?:다|습니다)\s*$"
)
SCHEDULE_CONTEXT_CHARS: Final[int] = 180
SCHEDULE_NORMALIZE_RE: Final[re.Pattern[str]] = re.compile(r"\s+")
