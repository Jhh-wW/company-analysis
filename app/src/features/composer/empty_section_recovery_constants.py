"""확보 근거 보고서의 한 번짜리 빈 장 복구 한도."""
import re

from src.shared.report_quality.composition_diagnostic_constants import (
    EMPTY_RECOVERY_SHAPE_CONTRACT, EMPTY_RECOVERY_SHAPE_FLAT_SINGLE, EMPTY_RECOVERY_SHAPE_NO_TARGET,
    EMPTY_RECOVERY_SHAPE_UNREADABLE, EMPTY_RECOVERY_SHAPE_UNWRAPPED, EMPTY_RECOVERY_STEP,
)

MAX_EMPTY_RECOVERY_SECTIONS = 2
#: 작가 응답의 최상위 포장 키. 계약 형식 ``{"장들": {...}}`` 의 그 키다.
RESPONSE_SECTIONS_KEY = "장들"
#: 응답 꼴 코드 — 진단에만 적는다(내용이 아니라 구조). 닫힌 목록은 shared 정화기와 공유한다.
RESPONSE_SHAPE_CONTRACT = EMPTY_RECOVERY_SHAPE_CONTRACT
RESPONSE_SHAPE_UNWRAPPED = EMPTY_RECOVERY_SHAPE_UNWRAPPED
RESPONSE_SHAPE_FLAT_SINGLE = EMPTY_RECOVERY_SHAPE_FLAT_SINGLE
RESPONSE_SHAPE_NO_TARGET = EMPTY_RECOVERY_SHAPE_NO_TARGET
RESPONSE_SHAPE_UNREADABLE = EMPTY_RECOVERY_SHAPE_UNREADABLE
#: 응답 장 키 «정규화 일치»에서 지우는 글자 — 공백·밑줄·하이픈과 대시류.
#: ``business_model`` · ``Business-Model`` · ``business model`` 을 같은 키로 본다.
SECTION_KEY_SEPARATOR_RE = re.compile(r"[\s_\-\u2010-\u2015]+")
#: 응답 장 키 «표시형 일치»에서 지우는 글자 — 위 구분자에 더해 따옴표·꺾쇠·괄호·
#: 콜론·마침표·쉼표·가운뎃점. «1장 «기업 정체성»»·«제1장: 기업 정체성»·
#: «1. 기업 정체성» 이 모두 같은 비교값이 된다.
SECTION_DISPLAY_NOISE_RE = re.compile(
    r"[\s_\-\u2010-\u2015\"'\u2018\u2019\u201c\u201d«»‹›<>《》〈〉「」『』()\[\]{}:.,·ㆍ・]+"
)
#: 표시형 장 번호 — 작성범위 문구 «N장 «제목»» 의 N은 장 순서(SECTION_IDS)의 1부터다.
SECTION_DISPLAY_FIRST_NUMBER = 1
#: 표시형 장 번호 뒤에 붙는 말(«N장»)과 앞에 붙는 말(«제N장»).
SECTION_DISPLAY_UNIT = "장"
SECTION_DISPLAY_ORDINAL_PREFIX = "제"
#: 작가가 요청 장을 «요청한 순서»로 부를 때의 첫 번호(«1장» = 첫째 요청 장). 정본 장
#: 번호(SECTION_DISPLAY_FIRST_NUMBER)와 뜻이 다른 값이다 — 번호만 있는 키가 두 읽기에서
#: 같은 장을 가리키는지 대조할 때만 쓴다.
REQUEST_ORDER_FIRST_NUMBER = 1
MAX_EMPTY_RECOVERY_SENTENCES = 3
MAX_EMPTY_RECOVERY_FRAGMENTS = 6
MAX_EMPTY_RECOVERY_EVIDENCE_CHARS = 12_000
EMPTY_RECOVERY_GUIDE = (
    "첫 검수에서 본문이 비어진 장만 공식 원문으로 새로 작성한다. "
    "장마다 확인 가능한 사실을 최대 3문장으로 간결하게 쓴다. 해석·추정·새 도식은 쓰지 않는다. "
    "인용 원문이 명시한 주체·대상·관계·시점만 옮기고, 전체 집단의 수를 일부 집단에 붙이지 않는다. "
    "숫자·단위·연도·비교 기준을 정확히 결속할 수 없으면 숫자를 쓰지 않는다. "
    "회사 사업 목표나 투자 계획을 인재상·조직문화로 바꾸지 않는다. "
    "쓸 수 있는 사실이 없으면 문장들 배열을 비운다. 검수 탈락 문장을 그대로 되살리지 않는다. "
    "각 장에 제공된 조각 ID만 인용하고, 다른 장을 추가하거나 기존 보고서를 재작성하지 않는다. "
    "문장마다 인용한 조각의 «의미칸» 목록에 실제로 들어 있는 값 하나를 주장슬롯으로 적는다. "
    "목록에 없는 칸 이름을 새로 만들지 않는다.\n"
    'JSON만 출력한다: {"장들":{"<요청 장 ID>":{"문장들":'
    '[{"글":"짧은 사실 문장","인용":["조각 ID"],"등급":"확인","주장슬롯":"의미칸 값"}]}}}\n'
)
#: 지침 바로 뒤에 붙이는 «요청 장 ID» 줄. 지침의 ``<요청 장 ID>`` 자리에 들어갈
#: 실제 문자열을 그대로 보여 준다. 2026-09-23 실측: 이 줄이 없을 때 작가가
#: 작성범위 문구의 장 번호·제목(«1장 기업 정체성»·«1장»)을 키로 써서, 내용이
#: 맞는 답이 두 번 모두 «요청장없음»으로 버려졌다.
#: ★ 장 ID 바로 뒤에 콜론을 두지 않는다 — 장 ID+콜론은 의미칸 값의 모양이다.
EMPTY_RECOVERY_TARGET_IDS_LINE = '요청 장 ID(이 문자열을 그대로 "장들"의 키로 쓴다): {target_ids}\n'
EMPTY_RECOVERY_TARGET_IDS_JOINER = ", "
#: 첫 답이 «JSON으로 읽히지 않거나 요청한 장을 하나도 담지 않았을» 때만 붙인다.
#: 같은 지침을 그대로 다시 보내고 형식 요구만 덧붙인다 — 작성 범위·근거·금지
#: 사항을 바꾸면 재요청이 다른 기준으로 쓴 문장을 만들어 낸다.
#: 2026-09-23 실측: 재요청 답도 키를 «1장»·«2장»으로, 주장슬롯을 장 ID를 뗀
#: 칸 이름으로 적었다. 그래서 키와 주장슬롯의 «모양» 요구를 여기서 다시 적는다.
EMPTY_RECOVERY_RETRY_GUIDE = (
    "\n앞 응답에서 요청한 장을 읽지 못했다. 설명·머리말·코드 펜스 없이 위 형식의 "
    'JSON 객체만 다시 출력한다. "장들"의 키는 위 «요청 장 ID» 줄의 문자열을 그대로 쓰고 '
    "장 번호나 장 제목으로 바꾸지 않는다. 요청한 장 ID 외의 장은 넣지 않는다. "
    "주장슬롯에는 «의미칸» 목록의 값을 콜론 앞 장 ID까지 그대로 적는다.\n"
)
