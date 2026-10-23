"""포트폴리오 수익 연결과 회계처리 상용구를 구별하는 좁은 문법."""
import re

PORTFOLIO_REVENUE_SLOT = "portfolio:revenue_link"
PORTFOLIO_SOURCE_CANDIDATE_LIMIT = 1
PORTFOLIO_SOURCE_CANDIDATE_REASON = "portfolio_current_service_source_candidate"
PORTFOLIO_ACCOUNTING_ONLY = "portfolio_accounting_policy_only"
PORTFOLIO_POLICY_SERVICE_NAME_MAX_CHARS = 70
_END = r"(?:한다|합니다|하고있다|하고있습니다)[.!]?"
_SERVICE = rf"[가-힣A-Za-z0-9()·/-]{{1,{PORTFOLIO_POLICY_SERVICE_NAME_MAX_CHARS}}}?서비스"
PORTFOLIO_ACCOUNTING_PATTERNS = tuple(re.compile(pattern) for pattern in (
    rf"(?:한편)?(?:회사는)?{_SERVICE}(?:및일부기타용역)?(?:에대해서는|는)"
    r"고객에게실질적으로서로같고고객에게이전하는방식도같은일련의구별되는용역을"
    r"제공(?:하며|하고있으며),?청구권이있는금액으로수익을인식하는실무적간편법을사용" + _END,
    r"회사는고객에게마케팅비용을지급하거나고객의고객에게할인을제공하고있으며,?"
    r"이러한(?:지급)?대가가(?:고객이회사에)?구별되는재화나용역(?:을이전함|의이전)에따른"
    r"(?:대가가|것이)아닌경우거래가격에서차감하여인식" + _END,
))
PORTFOLIO_REAL_ACTIVITY_PREFIX_RE = re.compile(r"제공|판매|개발|제조|운영|공급|하며|하고")
PORTFOLIO_DECLARATION_ENDING_RE = re.compile(r"인식(?:합니다|한다|하고있습니다|하고있다)[.!]?$")
