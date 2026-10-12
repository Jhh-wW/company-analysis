"""회사 주어 문맥의 크기와 명시적인 구조 표지."""

import re

CONTEXT_VERSION = "source-context-v1"
SELF_SECTION_ORIGIN = "self_section_declaration"
SELF_SECTION_EXTRA_KEYS = frozenset({"declaration_part", "section_context_json"})
SELF_DECLARATION_RE = re.compile(
    r"^당사는\s+[^\n.!?]{1,1800}(?:으로|로)\s*구성(?:되어\s*있(?:는|습니다)|되어\s*있는)[^\n.!?]*[.]$"
)
SELF_DECLARATION_PART_RE = re.compile(r"하는\s+([가-힣A-Za-z0-9 ·-]{1,80}?(?:사업부문|부문))(?=과|,|으로|\s)")
SELF_DECLARATION_EXCLUDED_RE = re.compile(r"가상|예시|가정|향후|예정|계획|전망|구성하지|아니|과거|당시|타사|다른\s*회사|경쟁사|인용|라고|다고")
MAX_CONTEXT_TABLE_CHARS = 200_000
MAX_CONTEXT_TEXT_CHARS = 12_000
MAX_ACTOR_CHARS = 120
MAX_CONTEXT_TABLES = 4096
MAX_CONTEXT_GRIDS = 512
MAX_CONTEXT_ROWS = 4096
MAX_CONTEXT_TOTAL_CHARS = 2_000_000
CONTEXT_BUDGET_REASON = "source_context_budget_exceeded"
SELF_ACTOR_LABELS = frozenset({"당사", "회사", "본사", "당사연구소", "본사연구소", "사내연구소", "자체", "자체개발"})
CONTEXT_KEYS = frozenset({"version", "text", "location", "text_sha256", "actor", "document_actor", "document_actor_location", "document_actor_sha256", "origin", "status"})
TABLE_ACTOR_HEADERS = frozenset({"연구기관", "개발기관", "연구개발기관", "개발회사", "회사명", "업체명"})
TABLE_STATUS_HEADERS = frozenset({"진행상황", "진행현황", "개발현황", "비고", "연구결과", "연구개발결과", "성과"})
TABLE_ITEM_HEADERS = frozenset({"연구/개발과제", "연구개발과제", "연구과제", "개발과제", "과제", "제품", "제품명", "품목", "기술명"})
COMPANY_NAME_RE = re.compile(r"(?:\(주\)|㈜|주식회사)\s*[가-힣A-Za-z0-9&·._-]+|[가-힣A-Za-z0-9&·._-]+\s*(?:주식회사|\(주\)|㈜)")
COMPANY_HEADING_START_RE = re.compile(r"영업개황\s*(?:[12][0-9]{3}년\s*(?:[1-4]\s*분기|상반기|하반기|연간)\s*(?:누적\s*)?)?")
BUSINESS_SECTION_RE = re.compile(r"(?m)^\s*\[[^\]\n]*(?:부문|사업)[^\]\n]*\]")
MAIN_SECTION_RE = re.compile(r"(?m)^\s*(?:(?:[IVX]+\.|[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\.)\s|[0-9]{1,2}\.\s*(?:주요 제품|주요 상품|매출|위험관리|연구개발|원재료|생산설비|사업의 내용|영업의 개황))")
SELF_SUBJECT_RE = re.compile(r"(?:당사|회사)(?:는|가|의)\s")
PENDING_STATUS_RE = re.compile(r"(?:양산|상용화|적용|개발|출시)[^;|\n]{0,24}(?:예정|계획|준비|검토)")
