"""첫 사업 소개가 없는 보고서의 한 번짜리 보충 계약."""

import re


IDENTITY_EXISTING_CORPORATE_SLOT = "identity:corporate_identity"
IDENTITY_EXISTING_SPECIALISM_PREFIXES = ("전문", "전업")
IDENTITY_EXISTING_TOPIC_SEPARATORS = ("는 ", "은 ")
# 구형 슬롯의 이미 승인된 첫 소개를 인정하기 위한 좁은 현재 사업 문형이다.
# 업종·제품 이름은 제한하지 않고, 행정제도의 '운영'만으로는 인정하지 않는다.
IDENTITY_EXISTING_CURRENT_BUSINESS_RE = re.compile(
    r"(?:제조|판매|생산|공급|제공|개발|중개|유통|운송|배송|보관|가공|진단|치료|제작)"
    r"(?:하는|하고|하며|하여)"
    r"|사업(?:을|를)\s*(?:영위|전개|운영)(?:하는|하고|하며|하여)"
)
IDENTITY_EXISTING_HISTORY_EVENT_RE = re.compile(
    r"(?:합병|분할|설립)(?:하|되|해|한|된|했|을|를|\s)"
)

IDENTITY_OPENING_RECOVERY_STEP = "8_정체성_첫문장_보충"
IDENTITY_OPENING_RECOVERY_PRESENT = "이미확보"
IDENTITY_OPENING_RECOVERY_ATTEMPTED = "재시도안함"
IDENTITY_OPENING_RECOVERY_NO_EVIDENCE = "근거없음"
IDENTITY_OPENING_RECOVERY_NO_BUDGET = "호출불가"
IDENTITY_OPENING_RECOVERY_PARSE_FAILED = "작성형식실패"
IDENTITY_OPENING_RECOVERY_NO_CANDIDATE = "적격작성후보없음"
IDENTITY_OPENING_RECOVERY_REJECTED = "검수탈락"
IDENTITY_OPENING_RECOVERY_ADDED = "검수승인후보추가"
IDENTITY_OPENING_RECOVERY_WRITER_GUIDE = (
    "기존 기업 정체성 본문은 보존하고, 첫 소개로 쓸 쉬운 현재 사업 설명 한 문장만 보충한다. "
    "회사가 현재 무엇을 주로 만들거나 제공하는지와 기업유형을 한 문장으로 설명한다. "
    "전문 업종명만 나열하지 말고 독자가 이해할 수 있는 제품·서비스를 적는다. "
    "기업·회사·업체·사업자·플랫폼 등의 기업유형을 명사 서술형으로 마친다. "
    "합병·설립목적·과거·계획·행정정보·조직 운영방식·정산 절차를 첫 소개 대신 쓰지 않는다. "
    "제공된 공식 원문과 그 의미칸만 사용하고, 근거가 부족하면 문장들 배열을 비운다. "
    "기존 문장과 인용·승인을 고치거나 기존 문장에 승인 표시를 새로 붙이지 않는다. "
    "주장슬롯은 identity:business_definition, 등급은 확인이며 인용 ID를 빠짐없이 적는다. "
    "해석·숫자 추정·새 표·새 도식·다른 장은 쓰지 않는다. "
    'JSON만 출력한다: {"장들":{"identity":{"문장들":'
    '[{"글":"쉬운 현재 사업 정의 한 문장","인용":["조각 ID"],'
    '"등급":"확인","주장슬롯":"identity:business_definition"}]}}}\n'
)
