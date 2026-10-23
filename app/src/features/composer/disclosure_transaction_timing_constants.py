"""공시의 종료 회계기간 거래·합병일·명시 월 상환완료 확인일에만 쓰는 시점 경계."""
import re

FILING_KIND = "공시"
REVIEWED_CLAIM_SHA256_KEY = "disclosure_reviewed_claim_sha256"
DART_HOST = "dart.fss.or.kr"
TRANSACTION_SECTION = "operations_partners"
MERGER_SECTION = "identity"
MERGER_SECTIONS = frozenset({MERGER_SECTION, "past_changes"})
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

# 월 단위 완료 사건의 as_of는 사건일이 아니라 그 완료를 확인한 실제 공시일이다.
REPAYMENT_CLAIM_PREFIX_RE = re.compile(r"^보고기간\s*이후\s*사건으로,\s*")
REPAYMENT_ACTOR_RE = re.compile(r"^(?P<actor>.+?)(?:는|은)\s+")
REPAYMENT_SELF_ACTORS = ("회사", "당사")
REPAYMENT_MONTH_RE = re.compile(
    r"(?P<year>\d{4})\s*년\s*(?P<month>\d{1,2})\s*월\s*중"
)
REPAYMENT_OBJECT = "신주인수권부사채"
REPAYMENT_COMPLETE_RE = re.compile(r"전액\s*상환(?:하였습니다|하였다|하였으며|했습니다|했다|했으며)")
REPAYMENT_CLOSED_PAST_END_RE = re.compile(r"(?:하였|했|받았)(?:다|습니다)[.!?\s]*$")
REPAYMENT_SENTENCE_END_RE = re.compile(r"(?:습니다|니다|다)\s*(?:[.!?]|$)")
REPAYMENT_UNCERTAIN_RE = re.compile(
    r"고\s*있|계속|지속|유지|예정|계획|검토|가능|조건|경우|라면|다면|취소|철회|보류|미상환|일부|부분"
)
KOREAN_ACTOR_BOUNDARY_RE = re.compile(r"[가-힣]")
