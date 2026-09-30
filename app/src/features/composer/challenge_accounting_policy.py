"""회사 실제 사건과 평상시 회계·반품 조건을 5장에서 구별한다."""
from __future__ import annotations

import unicodedata
from src.features.composer import challenge_accounting_constants as c


def is_challenge_accounting_policy(clause: str) -> bool:
    units = [item for item in c.POLICY_SUBCLAUSE_RE.split(clause) if item.strip()]
    if len(units) > 1:
        return all(is_challenge_accounting_policy(item) for item in units)
    surface = "".join(unicodedata.normalize("NFKC", clause).casefold().split())
    return bool(
        ((c.POLICY_SUBJECT_RE.search(surface) and c.POLICY_TREATMENT_RE.search(surface))
         or c.FINANCIAL_SUBJECT_RE.search(surface))
        and not c.BUSINESS_SERVICE_RE.search(surface)
        and (c.HYPOTHETICAL_EVENT_RE.search(surface) or not (
            c.ACTUAL_EVENT_RE.search(surface) or c.BUSINESS_OPERATION_RE.search(surface)
        ))
    )
