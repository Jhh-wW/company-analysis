"""공시의 종료 회계기간 거래와 명시 합병 완료일에만 쓰는 시점 경계."""
import re

FILING_KIND = "공시"
REVIEWED_CLAIM_SHA256_KEY = "disclosure_reviewed_claim_sha256"
DART_HOST = "dart.fss.or.kr"
TRANSACTION_SECTION = "operations_partners"
MERGER_SECTION = "identity"
RELATIVE_PERIOD_RE = re.compile(r"(?<![가-힣])(?:당기|전기)\s*중")
FINANCING_RE = re.compile(r"자금\s*거래")
PAST_TRANSACTION_RE = re.compile(r"(?:수행하였|수행했|자금\s*거래[를가]?\s*(?:하였|했|없었)|없었다)")
NON_ANNUAL_CLAIM_RE = re.compile(r"(?:\d+\s*월|분기|반기|개월|월간|분기중)")
CURRENT_OR_FUTURE_RE = re.compile(
    r"현재|지금|앞으로|향후|예정|계획|전망|진행\s*중|하고\s*있|이어가|유지하고|"
    r"수행한다|거래한다|수행합니다|거래합니다"
    r"|방침|추진|이뤄진다|이루어진다|확대한다|축소한다|거래가\s*있다"
)
CLOSED_PAST_TRANSACTION_END_RE = re.compile(
    r"(?:수행하였|수행했|거래하였|거래했|없었)(?:다|으며|고)[,.\s]*$"
)
QUOTE_RE = re.compile(r"[\"'“”‘’「」]")
ANNUAL_END_RE = re.compile(
    r"(?P<year>\d{4})\s*년\s*(?P<month>\d{1,2})\s*월\s*(?P<day>\d{1,2})\s*일"
    r"(?:자로)?\s*종료되는\s*회계연도"
)
YEAR_PERIOD_RE = re.compile(r"\d{4}")
FULL_PERIOD_RE = re.compile(r"(\d{4}-\d{2}-\d{2})/(\d{4}-\d{2}-\d{2})")
AUDIT_TITLE_RE = re.compile(r"(?:감사|사업)보고서\s*\((\d{4})\.(\d{1,2})\)")
PUBLIC_PERIOD_PREFIX = "{year}년 {month}월 {day}일 종료 회계기간 공시상, "
PUBLIC_PERIOD_PREFIX_RE = re.compile(
    r"^(?P<year>\d{4})년 (?P<month>\d{1,2})월 (?P<day>\d{1,2})일 종료 회계기간 공시상, "
)
COMPANY_NAME_REMOVE_RE = re.compile(r"주식회사|\(주\)|㈜|\s+")
KOREAN_DATE_RE = re.compile(r"(?P<year>\d{4})\s*년\s*(?P<month>\d{1,2})\s*월\s*(?P<day>\d{1,2})\s*일")
MERGER_COMPLETE_RE = re.compile(r"합병을\s*완료(?:하였|했)")
MERGER_CLAUSE_RE = re.compile(r"[^.!?\n]*합병을\s*완료(?:하였|했)[^.!?\n]*")
MERGER_EVENT_DATE_SUFFIX_RE = re.compile(r"^\s*에(?:\s|$)")
