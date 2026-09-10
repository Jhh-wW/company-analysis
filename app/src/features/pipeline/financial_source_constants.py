"""이미 수집한 DART 주요계정과 연간 공시의 날짜 결속 계약."""

import re
from typing import Final

# 공급자 원 필드와 engine의 내부 연간 선택 표식을 구별한다.
ANNUAL_REPORT_CODE: Final = "11011"
SELECTED_REPORT_PERIOD_KIND_KEY: Final = "engine_selected_report_period_kind"
SELECTED_REPORT_PERIOD_KIND_ANNUAL: Final = "annual"
ANNUAL_REPORT_TITLE = re.compile(
    r"(?:\[[^\]]+\]\s*)*(?:사업|감사)보고서\s*\(([0-9]{4})\.([0-9]{2})\)"
)
CORP_CODE = re.compile(r"[0-9]{8}")
RECEIPT_NUMBER = re.compile(r"[0-9]{14}")
DISCLOSURE_DATE = re.compile(r"[0-9]{8}")
FINANCIAL_STATEMENT_SCOPES: Final = frozenset({"CFS", "OFS"})
FINANCIAL_STATEMENT_KINDS: Final = frozenset({"BS", "IS"})

# engine.make_fragments의 표시 계약이다. 재현 문자열이 다르면 날짜를 붙이지
# 않으며 조각 본문 자체는 바꾸지 않는다. 회귀가 실제 engine과 대조한다.
API_FRAGMENT_ROW_LIMIT: Final = 14
API_FRAGMENT_PREFIX: Final = "주요계정(DART API): "
API_DISCLOSED_AT_KEY: Final = "financial_api_disclosed_at"
