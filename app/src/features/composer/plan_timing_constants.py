"""명시 목표연도의 현재화 검사에 쓰는 좁은 어휘와 범위."""

import re
from typing import Final


PLAN_TARGET_YEAR_OUTDATED: Final[str] = "plan_target_year_outdated"
PLAN_TIMING_CONTEXT_CHARS: Final[int] = 180
PLAN_TIMING_NEGATION_CHARS: Final[int] = 20
PLAN_HISTORICAL_CONTEXT_CHARS: Final[int] = 48
DATE_PERIOD_FIRST_COMPONENT: Final[int] = 1

# 연도와 바로 결속된 달성 사건만 읽는다. 투자한 해·보고서 연도·사업 시작
# 이후의 경과기간은 목표 마감으로 추정하지 않는다. 월·일 마감은 범위 밖이다.
PLAN_TARGET_ACTIVITY: Final[str] = (
    r"(?:사업\s*개시|서비스\s*개시|상업\s*가동|공장\s*준공|"
    r"준공|완공|개장|출시|개통|상용화)"
)
PLAN_YEAR_BEFORE_ACTIVITY_RE: Final[re.Pattern[str]] = re.compile(
    rf"(?<!\d)(?P<year>\d{{4}})\s*년\s*(?:말|초|중)?\s*(?:까지|에)?\s*"
    rf"(?P<activity>{PLAN_TARGET_ACTIVITY})"
)
PLAN_YEAR_AFTER_ACTIVITY_RE: Final[re.Pattern[str]] = re.compile(
    rf"(?P<activity>{PLAN_TARGET_ACTIVITY})(?:을|를)?\s*"
    r"(?P<year>\d{4})\s*년"
)
PLAN_GOAL_RE: Final[re.Pattern[str]] = re.compile(r"목표|계획|예정")
PLAN_LINK_INTERRUPTED_RE: Final[re.Pattern[str]] = re.compile(r"이후|이래|부터|완료|마친")
PLAN_CHANGED_RE: Final[re.Pattern[str]] = re.compile(
    r"미달|미완료|지연|연기|연장|중단|취소|철회|변경|재조정|미뤄|미루|넘겨|넘긴"
    r"|달성하지\s*못|실현하지\s*못"
)
PLAN_CHANGE_DENIED_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*(?:(?:하|되)지\s*않|(?:은|는|이|가|한\s*것이|된\s*것이)?\s*(?:없|아니))"
)
PLAN_NONCURRENT_SOURCE_SUFFIX_RE: Final[re.Pattern[str]] = re.compile(
    r"^(?:이?었|였|던|이라고|라는|다고|다는)"
)
PLAN_PREDICATE_RE: Final[re.Pattern[str]] = re.compile(
    r"(?P<past>목표였|계획이었|예정이었|진행했|추진했|구축했|계획했|목표로\s*했"
    r"|진행하였|추진하였|계획하였|예정되었|예정됐|목표로\s*삼았"
    r"|(?:진행|추진|구축|건설)(?:하고\s*있었|\s*중이었)|달성했|달성한|완료했)"
    r"|(?P<present>진행\s*중|추진\s*중|구축\s*중|건설\s*중|"
    r"진행하고\s*있|추진하고\s*있|구축하고\s*있|"
    r"예정(?:이다|입니다|이며)|계획(?:이다|입니다|이며)|"
    r"목표로\s*(?:한다|하고\s*있|삼고\s*있))"
)
PLAN_PRESENT_NEGATED_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*(?:이|인\s*것은|인\s*것이)?\s*아니"
)
PLAN_PAST_QUOTE_RE: Final[re.Pattern[str]] = re.compile(
    r"^\s*(?:이라고|라고|다고)\s*(?:밝혔|설명했|전했|발표했|기재했|명시했)"
)
PLAN_THEN_RE: Final[re.Pattern[str]] = re.compile(r"(?:^|\s)당시(?:\s|의)")
PLAN_HISTORICAL_REPORT_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<!\d)(?P<year>\d{4})\s*년\s*"
    r"(?:(?P<month>\d{1,2})\s*월\s*)?"
    r"(?:(?P<day>\d{1,2})\s*일\s*)?"
    rf"[^.!?;\n]{{0,{PLAN_HISTORICAL_CONTEXT_CHARS}}}"
    r"(?:공시|보고서|자료)(?:에서|를\s*통해|에)"
)
PLAN_SENTENCE_SPLIT_RE: Final[re.Pattern[str]] = re.compile(r"[.!?;\n]+")
PLAN_NORMALIZE_RE: Final[re.Pattern[str]] = re.compile(r"[\W_]+")
PLAN_CURRENT_DATE_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<!\d)(?P<year>\d{4})\s*년\s*"
    r"(?:(?P<month>\d{1,2})\s*월\s*)?"
    r"(?:(?P<day>\d{1,2})\s*일\s*)?(?:현재|기준)"
)
