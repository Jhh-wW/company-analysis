"""검수 근거 결속의 필드·수치·기간 구문 계약."""

from __future__ import annotations

import re

GROUNDING_KEY = "검증근거"
NUMERIC_KEY = "수치"
TREND_KEY = "추세"
TIME_KEY = "시점"
GROUNDING_MISSING = "semantic_grounding_missing"
GROUNDING_INVALID = "semantic_grounding_invalid"
MIN_TREND_POINTS = 2
MIN_CONTINUOUS_POINTS = 3
PREVIOUS_YEAR_OFFSET = 1
MIN_METRIC_STEM_LENGTH = 2
NEGATIVE_SIGNS = frozenset({"-", "−"})
SURFACE_SIGN_CHARACTERS = frozenset({"-", "−", "+"})

# 항목을 되풀이하지 않는 다년도 열거에서 허용할 연결어만 제거한다.
# 임의 명사까지 지우면 다른 지표의 숫자를 빌릴 수 있으므로 닫힌 문법을 쓴다.
NUMERIC_BRIDGE_RE = re.compile(
    r"전년|지난해|직전연도|전기|전분기|동기|대비|보다|비해|에서|으로|로|은|는|이|가|"
    r"의|을|를|약|각각|및|와|과|인식|기록|집계"
)
RELATIVE_YEAR_RE = re.compile(r"전년|지난해|직전연도")
COUNT_UNIT_RE = re.compile(r"\s*(?:장|명|개|건|대|회)(?![가-힣])")
ORDINAL_RE = re.compile(r"\d+위")
RATIO_QUALIFIER_RE = re.compile(r"비중(?:은|는|이|가)?")
QUARTER_RE = re.compile(r"([1-4])\s*분기")
OBSERVATION_PERIOD_RE = re.compile(r"((?:19|20)\d{2})(?:Q([1-4]))?")
QUARTERS_PER_YEAR = 4
DECIMAL_SENTENCE_BODY = r"(?:[^.!?\n]|\.(?=\d))"

# 회사명이나 지표 목록이 아니라 기간·값·비교 연산의 표면 구문을 읽는다.
YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})년")
CONTINUOUS_RE = re.compile(
    r"(?:지속(?:적으로)?|꾸준(?:히)?|연속(?:적으로)?|매년|해마다|계속)\s*"
    r"(?:증가|확대|성장|상승|감소|축소|하락)"
)
COMPARATIVE_RE = re.compile(
    r"(?:(?:전년|전기|전분기|지난해|직전(?:기|연도|분기))"
    r"\s*(?:동기\s*)?(?:보다|대비|에\s*비해)?|(?:19|20)\d{2}년\s*(?:보다|대비|에\s*비해))"
    + DECIMAL_SENTENCE_BODY + r"{0,80}?"
    r"(?:증가|확대|성장|상승|늘(?:었|어|었으)|오르(?:거나|고|며)|올랐|"
    r"감소|축소|하락|줄(?:었|어|었으)|내렸)"
)
PERIOD_COMPARATIVE_RE = re.compile(
    r"(?:19|20)\d{2}년" + DECIMAL_SENTENCE_BODY + r"{0,80}?에서\s*"
    r"(?:19|20)\d{2}년" + DECIMAL_SENTENCE_BODY + r"{0,80}?"
    r"(?:증가|상승|늘었|올랐|감소|하락|줄었|내렸)"
)
UP_RE = re.compile(r"증가|확대|성장|상승|늘(?:었|어|었으)|오르(?:거나|고|며)|올랐")
DOWN_RE = re.compile(r"감소|축소|하락|줄(?:었|어|었으)|내렸")
PLANNED_END_RE = re.compile(r"(?:계획|예정|목표|방침|전망|추진할|확대할|늘릴|줄일)")
RETROSPECTIVE_RE = re.compile(
    r"(?P<year>(?:19|20)\d{2})년[^.\n]{0,100}?"
    r"(?:재임기간|평가(?:대상기간|결과)?|성과급|경영성과|사업성과|실적|"
    r"완료|달성|출시|신설|개선|시행|수행)"
)
PARENTHETICAL_RE = re.compile(r"(?P<label>[가-힣A-Za-z]+)\s*\((?P<body>[^()]*)\)")
SENTENCE_SPLIT_RE = re.compile(r"(?<=[다요])[.!?]\s+|[;\n]")
PARTICLE_RE = re.compile(r"(?:으로|에서|은|는|이|가|의|을|를)$")
PAIR_SEPARATOR_RE = re.compile(r"[\s:：|,=()·\-]")
PRESENT_RE = re.compile(
    r"현재|지금|(?:추진|확대|강화|구축|운영|제공|진행|개선|출시|신설)"
    r"(?:하고|되고|중이고|중이며)?\s*(?:있다|있으며|있는|있음|중이다|중)"
)
TOKEN_RE = re.compile(r"[가-힣A-Za-z][가-힣A-Za-z0-9]{2,}")
TIME_STOPWORDS = frozenset({
    "그리고", "그러나", "따라서", "관련하여", "대해서", "위해서", "통해서",
    "회사는", "기업은", "현재", "지금", "추진하고", "확대하고", "강화하고",
    "구축하고", "운영하고", "제공하고", "진행하고", "있다", "있으며",
})
PRESENT_PERIOD = "현재"
DIMENSION_RE = re.compile(r"%|퍼센트|배|원|[조억만]")
WORD_CHARACTER_RE = re.compile(r"[가-힣A-Za-z0-9]")
VALUE_CONTINUATION_RE = re.compile(r"[0-9,.]")
APPROXIMATE_RE = re.compile(r"약\s*$")
RATIO_METRIC_RE = re.compile(r"(?:율|률|비율)$")
TABLE_SOURCE_ID = "실적표"
COMPOUND_AMOUNT_RE = re.compile(
    r"(?<![\d,.])\d+(?:,\d{3})*(?:\.\d+)?\s*[조억]\s+"
    r"\d+(?:,\d{3})*(?:\.\d+)?\s*[억만]\s*원"
)
REVIEW_ENTRIES_KEY = "판정"
REVIEW_NUMBER_KEY = "번호"
REVIEW_RESULT_KEY = "결과"
REVIEW_REJECTED = "거짓"
REVIEW_GROUNDING_REJECTED = "근거결속실패"
TREND_DIRECTIONS = frozenset({"지속증가", "지속감소", "증가", "감소"})

GROUNDING_GUIDE = (
    "\n■ 수치 의미·추세·활동기간 결속\n"
    "각 후보의 '추가 검증 필요' 항목이 있으면 기존 판정 행에 '검증근거' 객체를 붙인다. "
    "새 호출이나 회사별 지식을 사용하지 않는다. 필요한 항목을 확인할 수 없으면 참으로 승인하지 않는다.\n"
    "수치: [{표현: 후보에서 숫자와 항목을 함께 포함한 정확 구절, 항목: 후보의 항목명, "
    "근거: 인용 조각 ID, 원문: 그 조각의 정확 인용, 원문항목: 원문의 항목명, "
    "원문값: 해당 항목에 붙은 단위 포함 숫자, 후보값: 표현에서 검증할 단위 포함 숫자}]. "
    "표현에 여러 숫자가 있으면 후보값을 반드시 적고 숫자마다 행을 둔다. "
    "표현과 원문은 같은 항목의 다년도 값을 포함한 전체 구절을 공유할 수 있다. "
    "항목명이 생략된 뒤쪽 값도 앞의 실제 지표명에 결속하며 전년을 지표명으로 쓰지 않는다. "
    "항목·값·기간이 같은 사실이어야 한다. "
    "소계·순액을 그 구성요소 금액으로 바꾸거나 다른 항목의 같은 숫자를 빌리지 않는다. "
    "같은 근거·같은 원문을 이 수치 배열의 앞 행에서 이미 그대로 썼다면, 그 행에만 "
    "있는 대로 원문을 적고 이번 행은 원문 대신 '원문참조': <앞 행 번호>(1부터)를 "
    "쓸 수 있다(같은 근거일 때만, 원문과 원문참조를 함께 쓰지 않는다). 예: "
    "[{\"근거\":\"공시\",\"원문\":\"2023년 80억원, 2024년 100억원, 2025년 120억원\",...}, "
    "{\"근거\":\"공시\",\"원문참조\":1,...}]. 이 줄임은 이미 요구한 항목 하나도 "
    "생략하지 않는다 — 수치가 여러 개면 여전히 숫자마다 행을 두고, 항목·근거·"
    "원문항목·원문값·후보값은 매 행 그대로 적는다.\n"
    "추세: [{표현: 후보의 비교 구절, 항목: 같은 지표명, 방향: 지속증가/지속감소/증가/감소, "
    "관측: [{근거: 인용 조각 ID, 원문: 항목·활동연도·단위값이 결속된 정확 인용, "
    "항목: 같은 지표명, 기간: 네 자리 활동연도 또는 2025Q2 같은 분기, 원문값: 단위 포함 값}]}]. "
    "연속 추세는 최소 세 시점, 전년·전기·특정 연도 대비 같은 단순 비교는 "
    "두 시점의 실제 값이 필요하다. "
    "다만 원문이 같은 지표·기간의 비교율과 방향을 직접 보고한 재서술은 "
    "해당 수치 행의 정확 원문으로 확인하며 존재하지 않는 관측값을 만들지 않는다. "
    "발행일을 활동연도로 대체하거나 여러 행의 값과 연도를 임의로 짝짓지 않는다.\n"
    "시점: [{표현: 후보의 활동기간 포함 정확 구절, 근거: 인용 조각 ID, "
    "원문: 활동기간을 포함한 정확 인용, 기간: 네 자리 활동연도 또는 원문에 "
    "명시된 현재}]. 과거 평가·성과·완료 사건을 현재 진행으로 바꾸지 않는다. "
    "원문이 현재 상태를 직접 말하거나 뒷받침한 계획·해석은 유지한다.\n"
)
