"""고객 유형·운영 역할의 관리·법률 문맥 제외 범위."""
import re

CUSTOMER_SLOT = "business_model:customer_type"
OPERATING_ROLE_SLOT = "operations_partners:operating_role"
BUSINESS_SCOPE_SLOTS = frozenset({CUSTOMER_SLOT, OPERATING_ROLE_SLOT})
SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[다요])[.!?]\s*|[;\n]+")
CLAUSE_BOUNDARY_RE = re.compile(r"(?<![0-9]),(?![0-9])|(?<=으며)\s+|(?<=이며)\s+")
CUSTOMER_ADMIN_RE = re.compile(
    r"신용(?:위험|등급|평가|정보|도)|매출채권|대손(?:충당|손실)|거래한도|채무불이행")
CUSTOMER_ADMIN_ACTION_RE = re.compile(
    r"관리|검토|평가|통제|담보|보증|한도|분산|정책|한해거래|수취|손실|이용|결정|사용|점검")
OPERATING_ADMIN_RE = re.compile(
    r"제조물책임(?:법|보험)|손해배상책임|보험계약|(?:공정거래위원회|공정위).*(?:조사|과징금)"
    r"|(?:제조|생산|위탁).*(?:조사결과|과징금|지연이자)|(?:제조|생산).*관련.*소송")
CUSTOMER_DEFINITION_RE = re.compile(r"(?:주요|핵심)?고객(?:사)?(?:는|은|이|가).+")
BUSINESS_SERVICE_RE = re.compile(
    r"(?:고객(?:사)?|거래처|수요처|구매자|이용자).{0,80}(?:에게|에|로부터|의).{0,80}"
    r"(?:공급|판매|납품|제공|수주|수탁)(?:한다|합니다|했다|했습니다|하였다|하며|하는|하여|하고있|해왔|해오|중|서비스)"
    r"|(?:공급|판매|납품|제공|수주)(?:한다|합니다|했다|했습니다|하였다|하며|하는|하여|하고있|해왔|해오|중).{0,80}(?:고객사|거래처|수요처)"
    r"|(?:주요|핵심)?고객(?:사)?(?:는|은|이|가).{0,80}(?:기업|업체|기관|개인|병원|공장|소비자|차주)(?:이다|입니다|이며|이고|와|과|및)")
OPERATING_ACTION_RE = re.compile(
    r"(?:제품|상품|부품|장비|설비|시스템|서비스|사업|플랫폼|공장).{0,80}"
    r"(?:개발|제조|생산|공급|운영|제공)(?:한다|합니다|했다|했습니다|하였다|하며|하는|하여|하고있|해왔|해오|중|을담당|을수행)"
    r"|보험(?:상품|계약)?.{0,80}(?:판매|인수)(?:한다|합니다|했다|했습니다|하였다|하며|하고있)"
    r"|(?:개발|제조|생산|공급|운영)(?:을|은|는|이|가)?(?:담당|수행|하고있|합니다|한다)")
REJECT_BUSINESS_SLOT_SCOPE = "business_slot_scope_unsupported"
REMAINING_SUPPORT_RE = {
    CUSTOMER_SLOT: re.compile(r"고객사|거래처|수요처"),
    OPERATING_ROLE_SLOT: re.compile(r"생산|제조|운영한다"),
}
