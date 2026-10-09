"""대상회사가 취재 상대격으로만 쓰인 닫힌 원문 관계."""

import re

INTERVIEW_RECIPIENT_SUFFIX = (
    r"(?:[을를]\s*만나(?:서)?|[와과](?:의)?\s*(?:인터뷰|대담)(?:에서|를\s*통해))"
)
REPORTING_STATEMENT_RE = re.compile(
    r"(?:전했다|전했으며|말했다|말했으며|설명했다|설명했으며|밝혔다|밝혔으며|강조했다|강조했으며)"
)
MEETING_TRANSACTION_TAIL_RE = re.compile(
    r"^\s*[,，]?\s*(?:(?:(?:공급|판매|공동\s*(?:생산|개발|공급))\s*)?"
    r"(?:계약|협약)[을를]\s*(?:체결|맺)|협력\s*(?:방안|계획)[을를]\s*(?:논의|협의))"
)
RECIPROCAL_TRANSACTION_RE = re.compile(
    r"^\s*[,，]?\s*(?:인터뷰(?:하고|했으며)\s*)?양사\s*간\s*"
    r"(?:(?:공급|판매|공동\s*(?:생산|개발|공급))\s*)?"
    r"(?:계약|협약)[을를]\s*(?:체결|맺)"
)
REPORTING_SENTENCE_RE = re.compile(r"[^\n]+?(?:[.!?。](?=\s|$)|(?=\n|$))")
SUBJECT_INTERVIEW_RECIPIENT_ONLY = "subject_interview_recipient_only"
