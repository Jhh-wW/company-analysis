"""사업 실행을 조직문화로 확대하는 좁은 경계의 표지."""

from typing import Final
import re


CULTURE_EVIDENCE_SCOPE_MISMATCH: Final[str] = "culture_evidence_scope_mismatch"
CULTURE_ACCOUNTING_POLICY_MISPLACED: Final[str] = "culture_accounting_policy_misplaced"

# 공백을 정규화한 문장에만 적용한다. 회사·상품·출시 시점은 조건이 아니다.
ORGANIZATIONAL_CLAIM_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:조직의)?(?:(?:핵심|일상적)?운영원칙|조직문화|일하는방식|"
    r"의사결정(?:방식|과정|절차))"
)
CULTURE_ATTRIBUTION_RE: Final[re.Pattern[str]] = re.compile(
    r"보여(?:준다|주고|주는|주며)|드러(?:낸다|내고)|시사(?:한다|하는)|"
    r"반영(?:한다|하는)|원칙(?:으로|을)(?:삼|내재화)"
)

# 공식성 자체는 이 문자열 API로 판단하지 않는다. 아래 범주의 실제 내용이
# 있으면 일반 의미 검수에 남긴다; '조직/문화' 한 단어는 면제 근거가 아니다.
EXPLICIT_CULTURE_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:운영원칙|일하는방식|조직문화|인재상)(?:은|는|으로|로)|"
    r"(?:조직문화|인재상)(?:을|를)(?:지향|정의|명시)|"
    r"의사결정(?:방식|과정|절차|권한)|승인권한|결정권한|전결규정|"
    r"정기회의|담당자(?:에게)?(?:위임|자율)|조직개편"
)
SOURCE_UNAVAILABLE_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:공개|공시|확인)(?:하지않|되지않|할수없)|미공시|자료(?:가)?없"
)
SOURCE_CLAUSE_SPLIT_RE: Final[re.Pattern[str]] = re.compile(r"[.!?。\n]+")

# culture flow의 열 순서는 호출 계약이다. 회사명·상품·연도는 검사하지 않는다.
CULTURE_FLOW_CELL_COUNT: Final[int] = 3
SOURCE_GOAL_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:할|될|시킬)(?:계획|예정)|고자(?:하|한|합|했)|(?:사업|경영)목표"
)
FLOW_GOAL_QUALIFIER_RE: Final[re.Pattern[str]] = re.compile(r"계획|목표|예정|향후|앞으로")
GOAL_NEGATION_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:계획|목표|예정)(?:이|가|은|는|을|하지)?(?:아니|않|없)"
)
CURRENT_CULTURE_DENIAL_RE: Final[re.Pattern[str]] = re.compile(
    r"하지않|하지못|(?:이|가)아니|미시행|미도입"
)
OTHER_ORGANIZATION_RE: Final[re.Pattern[str]] = re.compile(r"타사|타회사|다른회사|경쟁사|고객사")

# ── 순수 회계 인식·측정 정책이 culture 장에 잘못 들어간 경우만 좁게 잡는다 ──
# 판정은 문장(절) 단위로 한다 — SOURCE_CLAUSE_SPLIT_RE로 나눈 같은 절 안에서만
# 아래 어휘를 대조한다. 글자수 상한(예: ".{0,10}")은 쓰지 않는다 — 그런
# 거리 제한은 근거 없는 매직 넘버다. 대신 "같은 절(문장)"이라는 문법적으로
# 뜻이 통하는 경계를 쓴다: 절 하나가 매출채권/계약자산 손실충당금의 인식
# 어휘와 측정 기준(신용위험 특성·연체일)을 모두 담고 있으면 순수 회계
# 서술이고, 같은 절 안에 실제 담당·검토·승인·감독 절차(부정되지 않은)까지
# 결속돼 있으면 그 절차 서술이 우선한다 — 차단하지 않는다.
CULTURE_ACCOUNTING_RECOGNITION_RE: Final[re.Pattern[str]] = re.compile(
    r"전체기간기대신용손실|손실충당금|간편법"
)
CULTURE_ACCOUNTING_CREDIT_CHARACTERISTIC_RE: Final[re.Pattern[str]] = re.compile(
    r"신용(?:위험)?특성"
)
CULTURE_ACCOUNTING_OVERDUE_BASIS_RE: Final[re.Pattern[str]] = re.compile(r"연체일")

# 같은 절 안에 실제 검토·승인·감독 절차가 결속돼 있는지 본다. "이사회"·
# "담당자" 같은 명사 하나만으로는 절차가 성립하지 않는다 — 반드시 아래
# 동사가 있어야 한다. 부정되면(검토하지 않는다, 승인을 받지 못한다 등)
# 실제로 수행된 절차가 아니므로 예외로 인정하지 않는다.
CULTURE_ACCOUNTING_GOVERNANCE_VERB_RE: Final[re.Pattern[str]] = re.compile(
    r"검토|승인|감독"
)
CULTURE_ACCOUNTING_GOVERNANCE_NEGATION_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:검토|승인|감독)(?:하지않|하지못|되지않|되지못)|받지않|받지못|"
    r"미(?:검토|승인|감독)"
)
