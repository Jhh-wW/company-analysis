"""검수 전 정산 후보의 범위·상한·진단 문구."""
import re
from typing import Final

SETTLEMENT_SLOT: Final[str] = "business_model:value_exchange"
SETTLEMENT_CANDIDATE_LIMIT: Final[int] = 1
SETTLEMENT_CANDIDATE_REASON: Final[str] = "current_settlement_source_candidate"
SETTLEMENT_SEGMENT_RE: Final[re.Pattern[str]] = re.compile(r"[^.!?。\n]+[.!?。]?")
SETTLEMENT_HEADING_RE: Final[re.Pattern[str]] = re.compile(r"^\(\d+\)금융예수금")
SETTLEMENT_FLOW_ACTION: Final[str] = (
    "최종소비자 결제 → 원천사가 회사에 대금 지급 → "
    "회사가 수수료·부가가치세 차감 후 가맹점에 지급"
)
