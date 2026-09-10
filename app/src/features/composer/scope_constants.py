"""상품 조건의 적용 범위를 대조하는 문법과 고정 사유 코드."""

import re

SCOPE_CONDITION_UNBOUND = "scope_condition_unbound"
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
