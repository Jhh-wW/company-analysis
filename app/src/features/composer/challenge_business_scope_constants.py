"""사업 영향 없이 사건 절차만 서술한 issue 후보의 닫힌 형식."""
import re

from src.shared.report_evidence.challenge_eligibility_constants import ADMINISTRATIVE_EVENT_ONLY

PROCEDURAL_ISSUE_ONLY = ADMINISTRATIVE_EVENT_ONLY
# 기존 사업관계 미결속 사유를 재사용하되 절차-only 별칭과 구분한다.
EXPENSE_BUSINESS_RELATION_UNBOUND = ADMINISTRATIVE_EVENT_ONLY
PROCEDURAL_ISSUE_UNIT_RE = re.compile(r"[.!?。;\n]+")
# 사건 이름을 읽는 구간에 원인·영향·다른 술어가 있으면 절차 전용으로 확정하지 않는다.
PROCEDURAL_LABEL_RELATION_RE = re.compile(
    r"으로|로인해|때문|에따라|에따른|해서|되어|되면서|못|중단|미지급|"
    r"손실|피해|차질|금지|제한|지연|영향|매출|영업|고객|제공|수수료"
)
PROCEDURAL_LABEL_MAX_CHARS = 160
_LABEL = rf"(?P<label>[가-힣A-Za-z0-9·ㆍ()'‘’\-]{{1,{PROCEDURAL_LABEL_MAX_CHARS}}}(?:청구|확인|취소|소송|사건)(?:사건|소송)?)"
_OWNER = r"(?:(?:회사|당사)(?:는|가|의))?"
_COURT = r"[가-힣A-Za-z·ㆍ]+(?:법원|지원)"
_COURTS = rf"{_COURT}(?:(?:과|와|및){_COURT})*"
_STATUS = r"(?:(?:\d+심|상고심|항소심|재판|심리)이?)?(?:진행|계류|심리)중"
_PARTY_PROCEDURE = (
    r"(?:회사|당사)(?:가|는)[가-힣A-Za-z·ㆍ()]{1,80}(?:을|를)상대로"
    r"소송(?:을)?(?:진행하고있다|진행중이다)"
)
PROCEDURAL_COURT_ISSUE_RE = re.compile(
    rf"{_OWNER}{_LABEL}(?:은|는|이|가|도)(?:각각)?{_COURTS}(?:에서|에)"
    rf"{_STATUS}(?:이다|에있다|이며|으로)?(?:,?{_PARTY_PROCEDURE})?"
)
PROCEDURAL_ACTION_ISSUE_RE = re.compile(
    rf"{_OWNER}{_LABEL}(?:은|는|이|가|을|를)(?:{_COURTS}에서)?"
    r"(?:진행하고있다|진행중이다|계류중이다|심리중이다)"
)
PROCEDURAL_EVENT_LABEL_RE = re.compile(rf"{_OWNER}{_LABEL}")

# 표의 명사형 셀은 사건명 뒤에 절차 상태와 괄호 속 법원·심급을 적기도 한다.
# 괄호 안에 임의 설명을 허용하지 않아 실제 사업 영향까지 절차로 지우지 않는다.
_TABLE_COURTS = rf"{_COURT}(?:(?:,|·|ㆍ|및|과|와){_COURT})*"
_TABLE_STAGE = r"(?:\d+심|상고심|항소심)"
_TABLE_METADATA = rf"(?:{_TABLE_COURTS}(?:,?{_TABLE_STAGE})?|{_TABLE_STAGE})"
PROCEDURAL_TABLE_ISSUE_RE = re.compile(
    rf"{_OWNER}{_LABEL}(?:은|는|이|가)?{_STATUS}(?:\({_TABLE_METADATA}\))?"
)

TABLE_ACCOUNTING_RESPONSE_UNIT_RE = re.compile(r"[.!?。;,，\n]+")
# 회계평가만인 대응 칸 전체를 읽는다. 실제 소송 수행·회수·제품 조치가 섞이면
# 이 닫힌 형식에서 벗어나므로 기존 원문·의미 검수에 남는다.
TABLE_ACCOUNTING_RESPONSE_ONLY_RE = re.compile(
    r"(?:(?:회사|당사)(?:는|가))?(?:"
    r"충당부채(?:미인식|미설정|(?:를)?(?:인식|설정)(?:하지않음|하지않았다|하지않는다))"
    r"|(?:재무상태|재무제표)(?:에)?(?:중요한|중요|중대한|중대)?영향"
    r"(?:이)?(?:없음|없다|없을것으로(?:판단|판단함|판단하고있다))"
    r")"
)

# 비용의 증감 표는 숫자를 지원하지만 실제 사업 제약이나 대응을 대신하지 않는다.
EXPENSE_NAME_MAX_CHARS = 30
EXPENSE_COMPARISON_MAX_CHARS = 64
EXPENSE_COMPARISON_RE = re.compile(
    rf"(?:^|[\s,;:])(?P<expense>[가-힣A-Za-z]{{1,{EXPENSE_NAME_MAX_CHARS}}}(?:비용|비))"
    rf"(?:는|은|이|가)?[^.!?。;\n]{{0,{EXPENSE_COMPARISON_MAX_CHARS}}}"
    r"(?:감소|증가|줄었|늘었)"
)
EXPENSE_ROW_VALUES_RE = re.compile(r"\s*[-+]?\d[\d,.]*\s+[-+]?\d[\d,.]*(?!\d)")
EXPENSE_ACTIVITY_MAX_CHARS = 64
EXPENSE_ACTIVITY_EVENT_RE = re.compile(
    r"(?:활동|사업|과제|수행|개발|연구|인력|인원|조직|투자|생산)"
    rf"[^.!?。;\n]{{0,{EXPENSE_ACTIVITY_MAX_CHARS}}}"
    r"(?:중단|지연|취소|축소|감축|확대|증설|재개)"
    r"(?:했|하였|됐|되었|하고있|되어있|이발생|가발생)"
)
EXPENSE_NONACTUAL_CONTEXT_RE = re.compile(
    r"향후|앞으로|가정|경우|가능성|(?:할|하는)(?:예정|계획)|"
    r"(?:예정|계획)(?:이다|이며|하고있)|(?:중단|축소|감축)하지않"
)
EXPENSE_ACTOR_RE = re.compile(rf"(?:^|[\s,])(?P<actor>[가-힣A-Za-z0-9()·]{{2,{EXPENSE_NAME_MAX_CHARS}}})(?:는|은|이|가)(?=\s)")
EXPENSE_OWNER_RE = re.compile(rf"(?:^|[\s,])(?P<actor>[가-힣A-Za-z0-9()·]{{2,{EXPENSE_NAME_MAX_CHARS}}})의(?=\s)")
EXPENSE_SELF_ACTORS = frozenset({"회사", "당사", "본사", "자사"})
