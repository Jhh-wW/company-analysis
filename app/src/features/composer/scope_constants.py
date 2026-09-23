"""상품 조건의 적용 범위를 대조하는 문법과 고정 사유 코드."""

import re

SCOPE_CONDITION_UNBOUND = "scope_condition_unbound"
# 서술형 원칙·특례는 표의 상품 조건과 별개로 실제 한정 절에 묶는다.
RECOGNITION_CLAUSE_RE = re.compile(r"[.;。\n]|[,，]|(?:하며|되며|이며|하고)\s*")
# 원문만 쉼표로 열린 조건을 보존하며, 문장·병렬 서술 경계는 넘지 않는다.
RECOGNITION_SOURCE_BOUNDARY_RE = re.compile(r"[.;。\n]|(?:하며|되며|이며|하고)\s*")
RECOGNITION_COMMA_RE = re.compile(r"[,，]")
RECOGNITION_OPEN_CONDITION_RE = re.compile(
    r"(?:에\s*(?:대해서|대하여|대해)(?:는)?"
    r"|(?:경우|때)(?:에)?(?:만|는)?"
    r"|(?:용역|프로젝트|계약|거래)(?:은|는)"
    r"|중(?:에서)?(?:는)?|(?:날|시점)에)\s*$"
)
RECOGNITION_DEPENDENT_START_RE = re.compile(
    r"^\s*(?:(?:제공(?:을)?\s*)?(?:완료|완성기준)"
    r"|(?:수익|매출)(?:을|로|으로)?\s*(?:인식|계상))"
)
REVENUE_SUBJECT_RE = re.compile(r"용역|프로젝트|수익|매출")
RECOGNITION_RE = re.compile(r"인식|계상")
COMPLETION_RE = re.compile(r"완료|완성기준")
PROGRESS_RE = re.compile(r"진행기준|진행률")
DURATION_LIMIT_RE = re.compile(r"(?P<value>\d+)\s*(?P<unit>년|개월)\s*(?:이내|내|이하)")
EXPLICIT_EXCLUSION_RE = re.compile(
    r"종속기업(?:에서|의?\s*범위에서)?\s*제외(?:(?:되었|하였|했|된)|(?=\s*(?:\(|$)))"
)
ENTITY_SUBJECT_RE = re.compile(
    r"(?:^|[.;。\n])\s*(?P<owner>[A-Za-z가-힣][A-Za-z0-9가-힣_.-]*(?:\s+[A-Za-z][A-Za-z0-9_.-]*)*)"
    r"(?:은|는|이|가)\s*"
)
CURRENT_SUBSIDIARY_RE = re.compile(r"종속기업(?:이다|으로|에\s*포함|에\s*해당|을\s*보유)|종속기업인")
HISTORICAL_MEMBERSHIP_RE = re.compile(r"과거|당시|이전|제외(?:되었|하였|했|된)|(?:처분|매각)(?:했|하였|된)")
# 회사·상품·특정 등급을 나열하지 않고 범위/조건의 문법만 읽는다.
CONDITION_RE = re.compile(
    r"(?:신용|평가)?등급\s*(?P<grade>[A-Za-z][A-Za-z0-9+\-]*)"
    r"(?:\s*\([^()]*\))?\s*(?:등급)?\s*(?P<grade_op>이상|이하|초과|미만)"
    r"|(?P<number>\d+(?:,\d{3})*(?:\.\d+)?)\s*"
    r"(?P<unit>[조억만]?원|개월|년|명|개|회|점|등급|%|퍼센트|배)\s*"
    r"(?P<number_op>이상|이하|초과|미만|이내)"
    r"|(?P<target>[가-힣A-Za-z][가-힣A-Za-z0-9]*(?:\s*및\s*[가-힣A-Za-z0-9]+)?)"
    r"\s*(?P<target_op>전용|에\s*한정|만\s*대상)"
)
CLAUSE_RE = re.compile(r"[;\n]|(?<=[.!?])\s+|,\s+(?!\d)")
ROW_SEPARATOR_RE = re.compile(r"\s+[-–—]\s+|\s*[:：|]\s*")
BULLET_RE = re.compile(r"^\s*[-•▪]\s+")
LOCAL_UNIT_RE = re.compile(r"상품|서비스|프로그램|계약|요금제")
BROAD_SCOPE_RE = re.compile(
    r"[가-힣A-Za-z0-9_-]+\s*(?:채널|부문|상품군|사업군)"
    r"|(?:모든|전체|전사)\s*[가-힣A-Za-z0-9_-]+"
)
OWNER_BRIDGE_RE = re.compile(r"(?:^|\s)(?:등(?:의|을|이|은|도)?(?:\s|$)|및|포함|비롯)|통해|^[과와·]|[,;]")
RELATIVE_OWNER_RE = re.compile(r"대상으로\s*하는|지원하는|위한|적용되는")
LOCAL_EXAMPLE_RE = re.compile(
    r"(?:대상으로\s*하는|지원하는|위한|적용되는)\s+"
    r"(?P<owner>(?:[가-힣A-Za-z0-9_-]+\s+)?[가-힣A-Za-z0-9_-]*(?:상품|서비스|프로그램|계약|요금제))"
)
TABLE_HEADERS = frozenset({"상품명", "서비스명", "구분", "주요 내용", "주요내용", "내용", "조건", "대상"})

# 도식은 대상명 칸 바로 뒤에 오는 명시적 자격 조건 칸만 연결한다.
FLOW_CONDITION_END_RE = re.compile(r"(?:대상|전용|한정|조건)$")
FLOW_SENTENCE_SUBJECT_RE = re.compile(r"(?:은|는|이|가)\s")
FLOW_DIRECT_SCOPE_BRIDGE_RE = re.compile(
    r"(?:은|는|의)?(?:(?:전체|모든)(?:상품|서비스)(?:의|은|는)?)?"
    r"(?:공통)?(?:(?:이용|가입|신청|지원|취급)?조건(?:은|는)?)?[:：|]?"
)
