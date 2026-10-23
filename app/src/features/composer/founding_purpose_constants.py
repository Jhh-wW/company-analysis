"""설립 목적과 현재 사업을 구분하는 닫힌 문법 표지."""

import re
from src.features.composer.settlement_role_constants import SETTLEMENT_SOURCE_TEMPLATE

UNIT_RE = re.compile(r"(?<=[.!?。])\s*|[;\n]+")
PURPOSE_OBJECT_RE = re.compile(
    r"(?P<object>[^.!?。\n;]+?)\s*(?:을|를)\s*"
    r"(?:영위|수행|경영|운영)할\s*목적으로[^.!?。\n;]*?설립"
)
HISTORICAL_FOUNDING_CLAIM_RE = re.compile(
    r"(?:영위|경영)하는\s*(?:회사|기업|법인)(?:으로|로)"
    r"[^.!?。\n;]*?설립(?:되었|됐|되|한|하였)"
)
SUBJECT_PREFIX_RE = re.compile(r"^.*?(?:는|은)\s+")
ITEM_SEPARATOR_RE = re.compile(r"[,·ㆍ]|\s+및\s+|(?:와|과)\s+")
ITEM_SUFFIX_RE = re.compile(r"\s*등\s*$")
CONTINUING_ACTIVITY_ENDING = r"해(?:왔(?:다|습니다|으며|고)|온)"
CURRENT_BUSINESS_RE = re.compile(
    r"(?:영위|경영|운영|제조|생산|판매|제공)(?:한다|합니다|하며|하고있(?:다|습니다|으며|는)|해오고있(?:다|습니다|으며|는)|중이다|중입니다|하는(?:회사|기업)(?:이다|입니다))"
)
CURRENT_CLAIM_RE = re.compile(
    r"(?:영위|경영)(?:한다|합니다|하며|하는|하고있(?:다|습니다|으며|는)|해오고있(?:다|습니다|으며|는)|중이다|중입니다|"
    + CONTINUING_ACTIVITY_ENDING + r")"
)
CONTINUING_CLAIM_RE = re.compile(r"(?:영위|경영)" + CONTINUING_ACTIVITY_ENDING)
CONTINUING_BUSINESS_RE = re.compile(r"(?:영위|경영|운영|제조|생산|판매|제공)" + CONTINUING_ACTIVITY_ENDING)
EXPLICIT_PRESENT_CLAIM_RE = re.compile(r"현재|지금|당기")
UNCONFIRMED_BUSINESS_RE = re.compile(
    r"목적|계획|예정|목표|추진할|검토중|영위하지|제조하지|생산하지|판매하지|제공하지"
    r"|영위할|운영할|제조할|생산할|판매할|제공할|중단|철수|폐업"
)
SOURCE_CLAUSE_BOUNDARY_RE = re.compile(
    r"(?<=으며)\s+|(?<=하며)\s+|(?<=했지만)\s+|(?<=하였지만)\s+|(?<=하지만)\s+"
)
CLAIM_CLAUSE_BOUNDARY_RE = re.compile(
    SOURCE_CLAUSE_BOUNDARY_RE.pattern + r"|\s+(?=(?:현재|지금|당기)(?:\s|도\s|는\s))"
)
SOURCE_HISTORICAL_RE = re.compile(r"과거|종전|이전에는|[0-9]{4}년당시")
SOURCE_CURRENT_RESET_RE = re.compile(r"^(?:현재|지금|당기)|(?:당사|회사)(?:는|가)현재")
SOURCE_OTHER_SUBJECT_RE = re.compile(
    r"(?:고객(?:사|기업)?|종속기업|종속회사|자회사|계열사|경쟁사|타사|거래처|협력사)"
    r"(?:은|는|이|가)"
)
SOURCE_SELF_SUBJECT_RE = re.compile(r"^(?:현재|지금|당기)?(?:당사|회사|본회사|우리회사)(?:는|가)")
SOURCE_MODAL_RE = re.compile(r"가정|가설|전제로|경우|한다면|된다면|할때")

# 도식 칸의 명사형도 현재 실행을 단언한다. 역사·목적 칸은 이 새 경계로 바꾸지 않는다.
FLOW_NOMINAL_ACTIVITY_RE = re.compile(
    r"(?P<activity>[가-힣A-Za-z·]+(?:업|사업|서비스))(?:을|를)?"
    r"(?:영위|수행|운영|경영)(?:함)?[.]?"
)
FLOW_HISTORICAL_RE = re.compile(r"설립|목적|과거|당시|이력|[12]\d{3}년")
FLOW_PG_ACTIVITY_RE = re.compile(r"전자(?:지급|지불)결제대행업")
FLOW_PG_ACTIVITY_CANONICAL = "전자지급결제대행업"
FLOW_PRESENT_PREFIX_RE = re.compile(r"^(?:현재|지금|당기)(?:는|도)?")
FLOW_CURRENT_SETTLEMENT_RE = re.compile(SETTLEMENT_SOURCE_TEMPLATE.format(target="가맹점"))
