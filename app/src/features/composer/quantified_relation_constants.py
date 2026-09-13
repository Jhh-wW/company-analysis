"""집단의 전체 수를 실제 배당 관계의 수로 바꾸지 않는 범위 계약."""
from __future__ import annotations

import re

QUANTIFIED_DIVIDEND_UNBOUND = "quantified_dividend_recipient_unbound"
GROUP_KINDS = r"종속회사|자회사|계열사"
GROUP_COUNT = rf"(?P<count>\d[\d,]*)\s*개(?:사)?\s*(?P<kind>{GROUP_KINDS})"
DIVIDEND = r"배당(?:금|수익)?"
RECEIPT = r"(?:수취|수령|받)"
SENTENCE_BOUNDARY = re.compile(r"(?<=[다요])[.!?]\s*|[\n;]|(?<=[고며]),\s*")
RECIPIENT_CLAIM = re.compile(
    GROUP_COUNT + rf"\s*(?:들)?(?:으로부터|로부터)\s*(?P<action>[^.!?\n;]*{DIVIDEND}[^.!?\n;]*{RECEIPT}[^.!?\n;]*)"
)
GROUP_COUNT_RE = re.compile(GROUP_COUNT)
GROUP_COUNT_REVERSED = re.compile(
    rf"(?P<kind>{GROUP_KINDS})\s*(?:총\s*)?(?P<count>\d[\d,]*)\s*개(?:사)?"
)
DIRECT_RECEIPT = re.compile(rf"^\s*(?:들)?(?:으로부터|로부터)\s*{DIVIDEND}(?:을|를)?\s*{RECEIPT}")
DIRECT_PAYMENT = re.compile(rf"^\s*(?:들)?(?:는|은|가|이)\s*당사에\s*{DIVIDEND}(?:을|를)?\s*지급")
NON_ACTUAL = re.compile(r"않|없|아니|불가|예정|계획|예상|전망|가능|조건|경우|일부|중에서|중\s")
