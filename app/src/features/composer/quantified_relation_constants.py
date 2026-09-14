"""집단의 전체 수를 실제 배당 관계의 수로 바꾸지 않는 범위 계약.

★ 후보 쪽과 원문 쪽에 «같은» 꼴을 쓴다. 후보는 한 어순만 보고 원문은 두 어순과
  지급형까지 보면, 후보가 어순만 바꿔도(「종속회사 N개사로부터」·「N개 종속회사가
  당사에 지급」) 검사를 지나간다(2026-09-14 독립 검토 실측).
"""
from __future__ import annotations

import re

QUANTIFIED_DIVIDEND_UNBOUND = "quantified_dividend_recipient_unbound"
#: 집단 이름. 「연결대상 회사」는 지주회사 공시가 종속회사 수를 적는 실제 표현이다.
GROUP_KINDS = r"연결대상\s*(?:종속)?회사|종속회사|자회사|계열사"
GROUP_COUNT = rf"(?P<count>\d[\d,]*)\s*개(?:사)?\s*(?P<kind>{GROUP_KINDS})"
GROUP_COUNT_REVERSED = rf"(?P<kind>{GROUP_KINDS})\s*(?:총\s*)?(?P<count>\d[\d,]*)\s*개(?:사)?"
DIVIDEND = r"배당(?:금|수익)?"
RECEIPT = r"(?:수취|수령|받)"
SENTENCE_BOUNDARY = re.compile(r"(?<=[다요])[.!?]\s*|[\n;]|(?<=[고며]),\s*")
#: 후보 쪽 — 「…로부터 배당을 받는다」(수취형)와 「…가 당사에 배당을 지급한다」(지급형).
_RECEIPT_TAIL = (
    rf"\s*(?:들)?(?:으로부터|로부터)\s*"
    rf"(?P<action>[^.!?\n;]*{DIVIDEND}[^.!?\n;]*{RECEIPT}[^.!?\n;]*)"
)
_PAYMENT_TAIL = (
    rf"\s*(?:들)?(?:가|이|는|은)\s*"
    rf"(?P<action>[^.!?\n;]*(?:당사|회사|모회사|지주회사)에\s*{DIVIDEND}(?:을|를)?\s*지급[^.!?\n;]*)"
)
RECIPIENT_CLAIMS = (
    re.compile(GROUP_COUNT + _RECEIPT_TAIL),
    re.compile(GROUP_COUNT_REVERSED + _RECEIPT_TAIL),
    re.compile(GROUP_COUNT + _PAYMENT_TAIL),
    re.compile(GROUP_COUNT_REVERSED + _PAYMENT_TAIL),
)
GROUP_COUNT_RE = re.compile(GROUP_COUNT)
GROUP_COUNT_REVERSED_RE = re.compile(GROUP_COUNT_REVERSED)
DIRECT_RECEIPT = re.compile(rf"^\s*(?:들)?(?:으로부터|로부터)\s*{DIVIDEND}(?:을|를)?\s*{RECEIPT}")
DIRECT_PAYMENT = re.compile(
    rf"^\s*(?:들)?(?:는|은|가|이)\s*(?:당사|회사|모회사|지주회사)에\s*{DIVIDEND}(?:을|를)?\s*지급")
NON_ACTUAL = re.compile(r"않|없|아니|불가|예정|계획|예상|전망|가능|조건|경우|일부|중에서|중\s")
