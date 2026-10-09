"""비용·비교 잔액과 실제 매출 경로·사업 개시를 구분하는 좁은 관계 문법."""
import re

UNIT_BOUNDARY_RE = re.compile(
    r"[;。\n]|(?<=[다요])\.\s*|[,，]\s*|(?:존재하며|존재하고|있으며|있고|없으며|없고)\s*"
)
OTHER_OWNER_RE = re.compile(r"(?:타사|다른\s*(?:회사|기업)|고객사|자회사|종속기업|관계기업)(?:의|는|은|이|가)")
SELF_OWNER_RE = re.compile(r"(?:당사|회사|본회사|우리\s*회사)(?:의|는|은|이|가)")
NON_ACTUAL_RE = re.compile(
    r"예정|계획|검토|추진|하지\s*(?:않|못)|확인되지\s*(?:않|못)|"
    r"없(?:다|습니다|음)|아니|취소|철회|부인"
)
EXPORT_COST_RE = re.compile(r"수출\s*(?:제\s*)?(?:비용|경비)|해외\s*(?:판매\s*)?(?:비용|경비)")
EXPORT_REVENUE_RE = re.compile(
    r"(?:수출|해외)\s*(?:관련\s*)?(?:매출|판매|수익)(?!\s*(?:원가|비용|경비|비))\s*(?:경로|채널)?"
)
EXPORT_CUSTOMER_RE = re.compile(r"(?:수출|해외)\s*고객(?:사)?")
EXPORT_SALE_RE = re.compile(r"(?:수출|해외)[^.!?。;\n|]{0,30}(?:판매|공급|납품|제공)(?:하|한|해|했|합)")
EXPORT_SOURCE_REVENUE_RE = re.compile(r"(?:수출|해외)\s*(?:매출|판매수익|수익)(?!\s*(?:원가|비용))")
EXPORT_SOURCE_ACTION_RE = re.compile(r"(?:수출|해외)[^.!?。;\n|]{0,30}(?:판매|공급|납품|제공)(?:하|한|해|했|합)")
REVENUE_ACTIVITY_WORD = r"[가-힣A-Za-z]{1,30}?"
START_ACTIVITY_RE = re.compile(
    r"(?<![가-힣A-Za-z])(?P<activity>" + REVENUE_ACTIVITY_WORD + r")\s*"
    r"(?:사업|수익\s*경로|매출\s*경로|판매\s*경로|영업\s*활동)"
    r"(?:은|는|을|를|이|가)?\s*"
    r"(?:(?:당기|올해|이번|신규|새로|최초로|처음(?:으로)?|[12]\d{3}년)(?:에|부터|는)?\s*){0,3}"
    r"(?:발생|개시|시작|신설|출범|진출|생겨)"
)
ACTIVITY_PREFIX_RE = re.compile(r"^(?:당사|회사|본회사)(?:의|는|은)")
BUSINESS_SCOPE_RE = re.compile(r"(?<![가-힣A-Za-z])(?P<business>[가-힣A-Za-z]{1,30})\s*사업(?:은|는|을|를|에서|의|이|가)?")
YEAR_RE = re.compile(r"(?<!\d)([12]\d{3})\s*년")
