"""고객 유형·운영 역할의 관리·법률 문맥 제외 범위."""
import re

CUSTOMER_SLOT = "business_model:customer_type"
OPERATING_ROLE_SLOT = "operations_partners:operating_role"
BUSINESS_SCOPE_SLOTS = frozenset({CUSTOMER_SLOT, OPERATING_ROLE_SLOT})
SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[가-힣)])[.!?]\s*|[;\n]+")
CLAUSE_BOUNDARY_RE = re.compile(r"(?<![0-9]),(?![0-9])|(?<=으며)\s+|(?<=이며)\s+")
CUSTOMER_ADMIN_RE = re.compile(
    r"신용(?:위험|등급|(?:을|를)?평가|정보|도)|매출채권|대손(?:충당|손실)|거래한도|채무불이행")
CUSTOMER_ADMIN_ACTION_RE = re.compile(
    r"관리|검토|평가|통제|담보|보증|한도|분산|정책|한해거래|수취|손실|이용|결정|사용|점검")
# 금융자산의 평상시 회수 보고는 제공물의 구매자·이용자 관계가 아니다.
# 외상판매나 실제 대출 제공 자체는 이 좁은 관리 문법에 포함하지 않는다.
CUSTOMER_RECOVERY_OBJECT_RE = re.compile(r"금융자산|매출채권|수취채권|미수금|대출채권")
CUSTOMER_RECOVERY_MANAGEMENT_RE = re.compile(
    r"회수(?:지연)?(?:현황|상태|대책)[^.!?;]{0,32}보고|"
    r"회수(?:지연|대책)[^.!?;]{0,32}(?:관리|정책|점검)|"
    r"지연사유[^.!?;]{0,32}(?:조치|보고|관리)"
)
# 고객 평가의 결과물과 수수료 수취가 같은 절에서 직접 연결된 유상서비스.
# 평가 뒤 거래한도를 정하거나 담보를 받는 내부 관리와 구분한다.
CUSTOMER_CREDIT_SERVICE_RE = re.compile(
    r"(?:고객(?:사)?|거래처)[^.!?;]{0,80}신용(?:을|를)?평가(?:해|하여|하고|하며)"
    r"[^.!?;]{0,80}(?:등급|평가결과|평가보고서)(?:을|를)?제공(?:하고|하며|하여|한다|합니다)"
    r"[^.!?;]{0,80}(?:평가)?수수료(?:를|을)?(?:받는다|받습니다|받고있|받았다|수취한다|수취하며|수취하고있)"
)
OPERATING_ADMIN_RE = re.compile(
    r"제조물책임(?:법|보험)|손해배상책임|보험계약|(?:공정거래위원회|공정위).*(?:조사|과징금)"
    r"|(?:제조|생산|위탁).*(?:조사결과|과징금|지연이자)|(?:제조|생산).*관련.*소송")
CAREER_PROFILE_RE = re.compile(r"학사|석사|박사|졸업|학력|주요경력|경력사항|경력|이전직장|전직장")
PERSONAL_POSITION_RE = re.compile(
    r"팀장|부장|본부장|공장장|담당임원|임원|이사|대표이사|수석|책임연구원"
    r"|(?:개발|제조|생산|공급|운영|공정|품질(?:관리)?)(?:부문|부서|본부|사업부|공장|팀)?"
    r"(?:을|를)?(?:담당|수행)")
PERSONAL_POSITION_MODIFIER_MAX_CHARS = 12
COMPOUND_PERSONAL_POSITION_RE = re.compile(
    rf"(?:개발|제조|생산|공급|운영|공정|품질)[가-힣]{{1,{PERSONAL_POSITION_MODIFIER_MAX_CHARS}}}"
    r"(?:담당|수행)(?=$|[|/(),])")
# 개인의 학위·이력과 함께 있어도 실제 현재 맡은 생산·운영 행동은 보존한다.
CURRENT_OPERATING_RESPONSIBILITY_RE = re.compile(
    r"(?:제품|부품|장비|설비|공장|제조|생산|공정).{0,45}"
    r"(?:생산|제조|운영|공정|품질관리)(?:을|를)?(?:직접)?"
    r"(?:담당|총괄|수행|책임)(?:한다|합니다|하고있|지고있|진다|집니다)"
)
PERSONAL_CAREER_PAST_CONTEXT_RE = re.compile(r"주요경력|경력사항|경력[:：]|이전직장|전직장")
PERSONAL_PAST_RESPONSIBILITY_RE = re.compile(
    r"(?:개발|제조|생산|공급|운영|공정|품질(?:관리)?)(?:을|를)?"
    r"(?:담당|총괄|수행|책임)(?:했다|했습니다|하였다|하였습니다|하였음|했던|하던)")
COMPANY_ACTION_SUBJECT_RE = re.compile(r"(?:회사|당사)(?:는|가|에서)")
OPERATING_PLAN_WINDOW_CHARS = 80
COMPANY_OPERATING_PLAN_RE = re.compile(
    rf"(?:제품|상품|부품|장비|설비|공장).{{0,{OPERATING_PLAN_WINDOW_CHARS}}}"
    r"(?:개발|제조|생산|공급|운영)(?:을|를)?(?:할|하려는|하려고|추진할)"
    rf".{{0,{OPERATING_PLAN_WINDOW_CHARS}}}(?:계획|예정)")
CUSTOMER_DEFINITION_RE = re.compile(r"(?:주요|핵심)?고객(?:사)?(?:는|은|이|가).+")
# 제공과 대가 수취가 같은 절에서 현재 동사로 연결된 고객 관계를 보존한다.
CUSTOMER_PROVISION_PAYMENT_RE = re.compile(
    r"고객(?:사)?[^.!?;,]{0,80}(?:에게|에)[^.!?;,]{0,80}"
    r"(?:공급|판매|납품|제공|수탁)하고[^.!?;,]{0,80}"
    r"(?:받는다|받습니다|수취한다|수취합니다|회수한다|회수합니다|청구한다|청구합니다)"
)
BUSINESS_SERVICE_RE = re.compile(
    r"(?:고객(?:사)?|거래처|수요처|구매자|이용자).{0,80}(?:에게|에|로부터|의).{0,80}"
    r"(?:공급|판매|납품|제공|수주|수탁)(?:한다|합니다|했다|했습니다|하였다|하며|하는|하여|하고있|해왔|해오|중|서비스)"
    r"|(?:공급|판매|납품|제공|수주)(?:한다|합니다|했다|했습니다|하였다|하며|하는|하여|하고있|해왔|해오|중).{0,80}(?:고객사|거래처|수요처)"
    r"|(?:주요|핵심)?고객(?:사)?(?:는|은|이|가).{0,80}(?:기업|업체|기관|개인|병원|공장|소비자|차주)(?:이다|입니다|이며|이고|와|과|및)")
# 경력의 '제조담당' 직함과 실제 '제조를 담당한다' 동사를 구분한다.
OPERATING_ACTION_RE = re.compile(
    r"(?:제품|상품|부품|장비|설비|시스템|서비스|사업|플랫폼|공장).{0,80}"
    r"(?:개발|제조|생산|공급|운영|제공)(?:한다|합니다|했다|했습니다|하였다|하며|하는|하여|하고있|해왔|해오|중"
    r"|을(?:담당|수행)(?:한다|합니다|했다|했습니다|하였다|하며|하는|하여|하고있|해왔|해오|중))"
    r"|보험(?:상품|계약)?.{0,80}(?:판매|인수)(?:한다|합니다|했다|했습니다|하였다|하며|하고있)"
    r"|(?:개발|제조|생산|공급|운영)(?:을|은|는|이|가)?(?:하고있|합니다|한다"
    r"|(?:담당|수행)(?:한다|합니다|했다|했습니다|하였다|하며|하는|하여|하고있|해왔|해오|중))")
REJECT_BUSINESS_SLOT_SCOPE = "business_slot_scope_unsupported"
REMAINING_SUPPORT_RE = {
    CUSTOMER_SLOT: re.compile(r"고객사|거래처|수요처"),
    OPERATING_ROLE_SLOT: re.compile(r"생산|제조|운영한다"),
}
