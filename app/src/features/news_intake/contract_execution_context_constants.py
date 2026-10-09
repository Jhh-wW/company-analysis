"""같은 기사 미래 계약 이행의 닫힌 시점 제약."""
import re

CONTRACT_CONTEXT_MAX_CHARS = 1_000
CONTRACT_CONTEXT_MAX_UNITS = 16
CONTRACT_CONTEXT_REASON = 'grounded_contract_execution_period_missing'
CONTRACT_FUTURE_START_RE = re.compile(
    r'(?<!\d)(?P<year>\d{4})\s*년(?:\s*(?P<month>\d{1,2})\s*월)?'
    r'(?:\s*(?P<day>\d{1,2})\s*일)?\s*부터'
)
CONTRACT_FULFILLMENT_RE = re.compile(
    r'(?:공급|납품|후원|제공|지원|운영)(?:한다|하고\s*있|할\s*계획|할\s*예정|하기로)'
)
CONTRACT_COMPLETED_RE = re.compile(
    r'(?:계약|협약)[^.!?。\n]{0,80}(?:체결했다|체결했다고|체결하였다|체결했다고\s*밝혔다)'
)
CONTRACT_EXPLICIT_NOW_RE = re.compile(
    r'(?:현재|이미)[^.!?。\n]{0,80}(?:공급|납품|후원|제공|지원|운영)(?:하고\s*있|해\s*왔)'
)
CONTRACT_REALIZED_ACTION_RE = re.compile(
    r'(?:공급|납품|후원|제공|지원|운영)(?:했다|하였다|해\s*왔다)'
)
CONTRACT_ONGOING_ACTION_RE = re.compile(
    r'(?P<action>공급|납품|후원|제공|지원|운영)하고\s*있(?:다|으며|고)')
CONTRACT_ACTION_NAME_RE = re.compile(r'공급|납품|후원|제공|지원|운영')
CONTRACT_EXPLICIT_YEAR_RE = re.compile(r'(?<!\d)(\d{4})\s*년')
CONTRACT_ENDED_RE = re.compile(
    r'(?:종료|중단|철회|해지)(?:했다|하였다|했|하였|되었|됐다)|'
    r'(?:공급|납품|후원|제공|지원|운영)(?:하지\s*않|하고\s*있지\s*않)')
CONTRACT_ENTITY_RE = re.compile(r'''['‘“"]([^'’”"\n]{2,80})['’”"]''')
CONTRACT_ENTITY_MIN_CHARS = 3
CONTRACT_GENERIC_ENTITIES = frozenset({'제품', '서비스', '행사', '대회', '사업', '계약', '브랜드'})
CONTRACT_SUBJECT_PREFIX = r'^\s*(?:(?:한편|또한|반면)\s+)?'
CONTRACT_TARGET_PARTICLE = r'(?:\([^\n)]*\))?\s*(?:은|는|이|가)'
CONTRACT_OTHER_ACTOR_RE = re.compile(
    r'(?:^|[.!?。]\s*|\n|\s)([가-힣A-Za-z][가-힣A-Za-z0-9]{1,40})(?:은|는|이|가)\s'
)
CONTRACT_GENERIC_ACTORS = frozenset({'회사', '계약', '기간', '이번계약', '후원', '공급', '지원', '대회', '시리즈'})
CONTRACT_EXECUTION_SELECTION_GUIDE = (
    '이미 체결한 계약과 향후 공급·후원 등의 이행을 구분하세요. 같은 기사에 해당 행사나 '
    '거래의 미래 시작기간이 명시되면 이행 문장만 잘라 현재 활동으로 고르지 말고, '
    '기간과 이행이 함께 있는 기존 연속 후보를 선택해 company_plan/planned로 판정하세요. '
    '계약 체결 자체의 완료 사실은 별도로 보존하세요. 같은 행사·행동이 현재 이미 '
    '진행 중이라는 원문도 있으면 현재 이행과 미래 갱신을 구분하세요. '
)
