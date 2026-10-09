"""공사·프로젝트의 시설 분류 수식을 자기 계약에 묶는 문법."""
import re

CLASS_WORD = r"[가-힣A-Za-z0-9]+"
CLASS_LIST_SEPARATOR = r"(?:\s*[·,，]\s*|\s+(?:및|와|과)\s+)"
CLASS_LIST = CLASS_WORD + r"(?:" + CLASS_LIST_SEPARATOR + CLASS_WORD + r")*"
CLASSIFICATION_RE = re.compile(
    r"(?P<classes>" + CLASS_LIST + r")\s*(?:"
    r"(?:시설|공장|플랜트|인프라)\s*(?:관련\s*)?(?:공사|사업|프로젝트)"
    r"|관련\s*(?:시설\s*)?(?:공사|사업|프로젝트))"
)
CLASS_LIST_SPLIT_RE = re.compile(CLASS_LIST_SEPARATOR)
GENERIC_CLASSES = frozenset({"주요", "대형", "각종", "관련", "일반", "당기", "전기", "기타"})
PROJECT_HEADERS = frozenset({"공사명", "계약명", "프로젝트명", "사업명"})
CLASS_HEADERS = frozenset({"시설분류", "시설종류", "사업분야", "사업종류", "공사종류", "프로젝트분류"})
TABLE_SPLIT_RE = re.compile(r"\r?\n|;")
SENTENCE_SPLIT_RE = re.compile(r"[;。\n]|(?<=[다요])\.\s*")
ALIGNMENT_RE = re.compile(r":?-+:?")
CONTRACT_PERIOD_RE = re.compile(r"\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2}\s*[~∼〜-]\s*\d{4}")
PROJECT_SUBJECT_RE = re.compile(r"(?:^|[,;]\s*)(?P<project>[가-힣A-Za-z0-9()_\- ]+?(?:공사|프로젝트))\s*(?:은|는|이|가)\s")
GENERIC_PROJECTS = frozenset({"공사", "주요공사", "주요도급공사", "당기주요도급공사", "전기주요도급공사"})
NON_ACTUAL_RE = re.compile(
    r"(?<![가-힣])(?:예정|계획|미확인|검토)(?:\b|이|인|중)"
    r"|확인되지|해당하지|하지\s*(?:않|못)|(?:공사|사업)(?:가|는|은)?\s*아니"
    r"|(?:철회|취소|중단)(?:되|하|한|된|중)"
)
OTHER_OWNER_RE = re.compile(r"(?:다른\s*(?:회사|기업)|타사|고객사|관계기업|종속기업|자회사)(?:의|는|은|이|가)")
SELF_OWNER_RE = re.compile(r"(?:당사|본회사|우리\s*회사)(?:의|는|은|이|가)")
FACILITY_SUFFIX_RE = re.compile(r"(?:시설|공장|플랜트|인프라)$")
PROJECT_SUBJECT_PREFIX_RE = re.compile(r"^(?:(?:당사|회사)의\s*|(?:당기|전기)\s*)")
# 업종 이름은 열거하지 않는다. 아래는 계약명 자체가 물리 시설을 명시하는 명사다.
CONCRETE_FACILITY_RE = re.compile(r"(?:공항|학교|병원|철도|항만|발전소|도로|교량|터널|댐)$")
CLASSIFIED_FACILITY_SUFFIX_RE = re.compile(r"^(?:시설|공장|플랜트|센터|인프라|관련공사|공사|프로젝트)")
FACILITY_OWNER_TAIL_RE = re.compile(r"^(?:시설|공장|플랜트|센터|인프라)(?:운영)?(?:회사|기업|업체|운영사)")
