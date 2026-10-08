"""거래별로 닫힌 결제수단 목록의 범위를 검사하는 문법."""

import re

PAYMENT_SCOPE_PROBLEM = "scope_condition_unbound"
PAYMENT_SCOPE_SECTIONS = frozenset({"business_model", "operations_partners"})
PAYMENT_METHOD_RE = re.compile(r"(?<![A-Za-z])(?:D\s*/\s*[AP]|O\s*/\s*A|L\s*/\s*C|T\s*/\s*T|C\s*/\s*T)(?![A-Za-z])", re.I)
PAYMENT_TRADE_NAME = r"[가-힣A-Za-z0-9_-]+(?:\s*\([^()\n]*\))?\s*거래"
PAYMENT_TRADE_RE = re.compile(PAYMENT_TRADE_NAME)
PAYMENT_SUBJECT_TAIL_RE = re.compile(
    r"\s*(?:(?:및|와|과|·|,)\s*" + PAYMENT_TRADE_NAME + r")*\s*(?:는|은|:)"
)
PAYMENT_CLAUSE_END_RE = re.compile(r"[.!?。;\n|]|(?:\(\d+\)|(?<![A-Za-z0-9])\d+\))")
PAYMENT_CLOSED_RE = re.compile(r"모두|오직|만으로|에\s*한정")
PAYMENT_LIST_BRIDGE_RE = re.compile(r"[\s,·ㆍ/]*(?:(?:거래|방식|또는|및|와|과)[\s,·ㆍ/]*)*")
PAYMENT_NONASSERTION_RE = re.compile(r"아니|제외|사용하지|하지\s*않|불가|예정|계획|검토")
