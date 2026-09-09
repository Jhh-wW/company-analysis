"""사업 실행을 조직문화로 확대하는 좁은 경계의 표지."""

from typing import Final
import re


CULTURE_EVIDENCE_SCOPE_MISMATCH: Final[str] = "culture_evidence_scope_mismatch"

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
