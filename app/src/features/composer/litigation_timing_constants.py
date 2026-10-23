"""결산일 소송 현황의 현재화를 막는 닫힌 시점 문형."""

import re

LITIGATION_TIMING_UNSUPPORTED = "litigation_snapshot_date_unsupported"
LITIGATION_RE = re.compile(r"소송|분쟁")
SNAPSHOT_RE = re.compile(r"(?:계류|충당부채|\d+\s*건)")
CURRENT_PERIOD_RE = re.compile(r"(?:당기말|보고기간말|기말)\s*현재")
PRIOR_PERIOD_RE = re.compile(r"전기말\s*현재")
AS_OF_RE = re.compile(
    r"(?<!\d)(?P<year>\d{4})\s*(?:년\s*(?:"
    r"(?P<year_end>말)|(?P<month>\d{1,2})\s*월\s*(?P<day>\d{1,2})\s*일)"
    r"|(?P<sep>[-.])(?P<iso_month>\d{1,2})(?P=sep)(?P<iso_day>\d{1,2}))"
    r"\s*(?:기준|현재)?"
)
UNBOUND_CURRENT_RE = re.compile(
    r"현재도|지금도|현재까지|지금까지|이후에도|기준일을\s*지나\s*현재|"
    r"아직(?:도)?\s*(?:계류|진행)"
)
DATED_SNAPSHOT_LEAD_RE = re.compile(
    r"(?:으로)?\s*[,·]?\s*(?:(?:회사|당사)(?:가|는|의)\s*)?"
    r"(?:(?P<role>원고|피고)(?:로|인)\s*)?(?:계류\s*중인\s*)?"
    r"(?:총\s*\d+\s*건의\s*)?(?:소송|분쟁)"
)
# 도식의 축약 역할도 같은 기말·자기회사·당사자 역할·건수가 맞을 때만 받는다.
DATED_COMPACT_SNAPSHOT_LEAD_RE = re.compile(
    r"\s*(?P<role>원고|피고)\s+(?:소송(?:사건)?|분쟁)\s*"
    r"(?:은|는|이|가)?\s*(?:총\s*)?(?P<count>\d+)\s*건"
)
SOURCE_CURRENT_COMPANY_SNAPSHOT_RE = re.compile(
    r"(?:^|(?<=[.!?;\n]))\s*(?:\(\d+\)\s*)?"
    r"(?:당기말|보고기간말|기말)\s*현재\s*(?:회사|당사)(?:가|는|의)\s*"
    r"(?P<role>원고|피고)(?:로|인)\s*(?:계류\s*중인\s*)?(?:소송|분쟁)"
)
SNAPSHOT_BEFORE_DATE_RE = re.compile(r"(?:소송(?:사건)?|분쟁)(?:은|는|이|가)\s*$")
DATED_SNAPSHOT_COUNT_RE = re.compile(r"\s*\d+\s*건(?:이|으|[, .]|$)")
PUBLISHER_NAME_MAX_CHARS = 100
COMPACT_SNAPSHOT_BEFORE_DATE_RE = re.compile(
    r"(?<![가-힣])(?P<role>원고|피고)\s+(?:계류\s*(?:중(?:인)?)?\s*)?"
    r"(?:소송(?:사건)?|분쟁)\s*(?:은|는|이|가)?\s*(?:총\s*)?\d+\s*건"
)
COMPACT_SNAPSHOT_PUBLISHER_RE = re.compile(
    r"\s*(?P<publisher>[가-힣A-Za-z0-9㈜().&'· -]{1,"
    + str(PUBLISHER_NAME_MAX_CHARS)
    + r"}?)(?:가|는|의)\s*"
)
DATED_NAMED_SNAPSHOT_LEAD_RE = re.compile(
    r"(?:으로)?\s*(?P<publisher>[가-힣A-Za-z0-9㈜().&'· -]{1,"
    + str(PUBLISHER_NAME_MAX_CHARS)
    + r"}?)(?:가|는|의)\s*(?:(?P<role>원고|피고)(?:로|인)\s*)?"
    r"(?:계류\s*중인\s*)?(?:소송|분쟁)"
)
SOURCE_COMPANY_SNAPSHOT_RE = re.compile(
    r"(?:회사|당사)(?:가|는|의)\s*(?:(?P<role>원고|피고)(?:로|인)\s*)?"
    r"(?:계류\s*중인\s*)?(?:소송|분쟁)"
)
# 괄호 날짜는 바로 앞의 소송 건수·금액 문장만 한정한다. 다른 문장의 날짜는 제외한다.
PARENTHESIZED_SNAPSHOT_HEAD_RE = re.compile(
    r"\s*(?:(?:회사|당사)(?:가|는|의)\s*)?"
    r"(?:(?P<role>원고|피고)(?:로|인)?\s*)?(?:계류\s*(?:중인\s*)?)?"
    r"(?:소송(?:사건)?|분쟁)\s*(?:은|는|이|가)?\s*(?:총\s*)?\d+\s*건"
    r"(?:\s*계류\s*중)?"
    r"(?:\s*,\s*소송금액(?:은)?\s*(?:약\s*)?[\d,]+(?:\.\d+)?"
    r"\s*(?:천|만|억|조)?\s*원(?:\s*규모)?)?\s*\(\s*"
)
PARENTHESIZED_SNAPSHOT_END_RE = re.compile(r"\s*\)\s*(?:[|;/]|$|[.]\s*$)")
LITIGATION_AMOUNT_METRIC = "소송금액"
CURRENT_LITIGATION_CLAUSE_RE = re.compile(
    r"(?:당기말|보고기간말|기말)\s*현재[^.!?;\n]*?(?:소송|분쟁)[^.!?;\n]*"
)
CASE_COUNT_RE = re.compile(r"(?:소송(?:사건)?|분쟁)\s*(?:은|는|이|가)?\s*(?:총\s*)?(?P<count>\d+)\s*건")
YEAR_END_MONTH = 12
YEAR_END_DAY = 31
REPORTING_PERIOD_LABEL = "공시 명시 보고기간"
REPORTING_PERIOD_GUIDE = (
    "당기말은 이 기간의 말일이며 공시일·보고서 기준일의 현재 현황이 아니다. "
    "소송 현황을 사용할 때에는 본문과 도식 모두 이 말일을 명시한다. "
    "원문 앞에 'YYYY년 MM월 DD일 기준'을 붙이는 형식을 쓴다 "
    "(예: 'YYYY년 MM월 DD일 기준 회사가 피고로 계류 중인 소송…'). "
    "회사·당사자 역할은 원문에 있는 표현만 그대로 사용한다"
)
