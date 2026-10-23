"""현재 계획으로 쓰기 어려운, 날짜가 명시된 단일 행사 문맥의 경계."""

import re

EVENT_PLAN_EXPIRED = 'grounded_event_plan_expired'
EVENT_CONTEXT_MAX_CHARS = 1400
EVENT_CONTEXT_PREVIOUS_UNITS = 3
EVENT_TYPES = r'(?:세미나|설명회|간담회|컨퍼런스|박람회|포럼|행사)'
EVENT_SCHEDULE_RE = re.compile(
    rf'(?P<event>{EVENT_TYPES})(?:는|은|가|이)\s+(?P<upcoming>오는\s*)?'
    r'(?:(?P<year>20\d{2})년\s*)?(?P<month>\d{1,2})월\s*(?P<day>\d{1,2})일'
    r'[^.!?。]{0,160}?(?:열린다|개최된다|개최될\s*예정)'
)
EVENT_NAME_RE = re.compile(r'''["'“‘]([^"'”’\n]{4,100})["'”’]''')
EVENT_PLANNED_ACTION_RE = re.compile(r'(?:소개|안내|강연|발표|개최)(?:할\s*(?:예정|계획)|한다고)')
EVENT_CONTEXT_RE = re.compile(rf'{EVENT_TYPES}(?:에서|에|는|은|가|이)|현장\s*참석자')
EVENT_AMBIGUOUS_RE = re.compile(r'연기|취소|일정\s*변경|매년|정기적|상시|반복\s*개최')
EVENT_NEW_CYCLE_RE = re.compile(r'내년|내후년|다음\s*(?:해|행사|세미나|설명회)|재개최|다시\s*개최')
EVENT_OWNER_SUFFIX = rf'(?:은|는|이|가)\s+[^.!?。\n]{{0,240}}{EVENT_TYPES}[^.!?。\n]{{0,160}}개최'
