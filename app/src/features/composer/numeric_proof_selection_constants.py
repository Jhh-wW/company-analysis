"""사건 표의 명시 금액 선택 계약. 그 밖의 수치는 기존 증명으로 검사한다."""
import re
from src.shared.report_evidence.constants import (
    SOURCE_KIND_DART_BUSINESS_REPORT, SOURCE_KIND_DART_AUDIT_REPORT,
    SOURCE_KIND_DART_CONSOLIDATED_AUDIT_REPORT, SOURCE_KIND_DART_QUARTERLY_REPORT,
    SOURCE_KIND_DART_SEMIANNUAL_REPORT,
)

NUMERIC_SELECTION_KEY = "수치선택"
NUMERIC_SELECTION_VERSION = "event-currency-selection-v1"
NUMERIC_SELECTION_STAGE = "numeric_selection_unbound"
NUMERIC_SELECTION_SECTION = "current_challenges"
NUMERIC_SELECTION_GUIDE = (
    "  아래 수치선택은 이 번호의 자기 인용 사건 행에 결속된 금액 증명 후보다. "
    "내용·주체·날짜·조치 상태가 맞는지 기존 기준으로 먼저 검수하라. "
    "맞으면 검증근거의 '수치선택'에 ID 문자열 하나를 그대로 넣고 '수치' 배열은 넣지 않는다. "
    "이 선택은 참 판정을 보장하지 않으며 다른 번호·행에는 사용할 수 없다. "
    "선택하지 않거나 대상 밖 숫자는 기존 검증근거 형식을 사용한다.\n"
)
_VALUE = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\s*(?:억원|천만원|백만원|만원|천원|원)"
CURRENCY_VALUE_RE = re.compile(_VALUE)
PENALTY_CURRENCY_RE = re.compile(
    r"(?P<metric>과태료|벌금)\s*(?:처분\s*)?[\(（:]?\s*(?P<value>" + _VALUE + r")"
)
UNSUPPORTED_PENALTY_RE = re.compile(r"미부과|면제|감면|부과하지|납부하지|부과예정|부과 예정|상한|하한|이상|이하|초과|미만")
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")
NUMERIC_SELECTION_DART_KINDS = frozenset({SOURCE_KIND_DART_BUSINESS_REPORT, SOURCE_KIND_DART_AUDIT_REPORT,
    SOURCE_KIND_DART_CONSOLIDATED_AUDIT_REPORT, SOURCE_KIND_DART_QUARTERLY_REPORT,
    SOURCE_KIND_DART_SEMIANNUAL_REPORT})
