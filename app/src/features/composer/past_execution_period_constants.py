"""4장 완료 실행 산문의 명시적 사건일 범위 검사 상수."""

import re

# 출고 계약의 '기준일 전 최근 36개월'과 같은 연도·월·일 경계다.
PAST_EXECUTION_LOOKBACK_YEARS = 3
PAST_EXECUTION_LEAP_FALLBACK_DAY = 28
PAST_EXECUTION_MONTHS_IN_YEAR = 12
PAST_EXECUTION_COMPLETED_SLOT = "past_changes:completed_execution"
PAST_EXECUTION_OUTSIDE_PERIOD = "completed_execution_outside_period"
PAST_EXECUTION_SENTENCE_RE = re.compile(r"[!?。;\n]|\.(?!\d)")
PAST_EXECUTION_DATE_RE = re.compile(
    r"(?<![\w])(?P<year>20\d{2})"
    r"(?:\s*년(?:\s*(?P<ko_month>\d{1,2})\s*월(?:\s*(?P<ko_day>\d{1,2})\s*일)?)?"
    r"|[-.](?P<iso_month>\d{1,2})[-.](?P<iso_day>\d{1,2})(?!\d))"
)
PAST_EXECUTION_COMPLETED_RE = re.compile(
    r"(?:완료|준공|설립|구축|도입|시행|설치|출시|착공|개시|가동|인수|취득|합병)"
    r"(?:했|하였|되었|됐)(?:다|으며|고|습니다|다고|음)"
)
PAST_EXECUTION_DATE_CONTEXT_RE = re.compile(
    r"공시(?:일|된|한)?|보고서|발행(?:일|된|한)?|작성(?:일|된|한)?|기준일|자료|"
    r"(?:설립|창립|출시|도입|준공|취득|인수|합병|계약|매입|발표)(?:한|된|했던|되었던)|부터|이래"
)
PAST_EXECUTION_CONNECTED_ENDINGS = ("으며", "고")
PAST_EXECUTION_RELATIVE_DATE_RE = re.compile(
    r"당기|전기|올해|금년|작년|지난해|최근|이번|금번|현재"
)
PAST_EXECUTION_NEGATION_RE = re.compile(r"않|못|아니|여부|경우|가정")
