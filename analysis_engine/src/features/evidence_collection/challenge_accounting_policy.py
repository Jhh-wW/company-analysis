"""원문 보존 상태에서 5장 채점용 회계정책 절만 가린다."""
from __future__ import annotations

from dataclasses import dataclass
import unicodedata

from features.evidence_collection import challenge_accounting_constants as c
from features.evidence_collection.liquidity_constants import DECIMAL_SAFE_CLAUSE_SPLIT_RE


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


@dataclass(frozen=True)
class ChallengePolicySplit:
    score_text: str
    excluded_clauses: int


def split_challenge_accounting_policy(text: str) -> ChallengePolicySplit:
    clauses = [unit for item in DECIMAL_SAFE_CLAUSE_SPLIT_RE.split(text)
               for unit in c.POLICY_SUBCLAUSE_RE.split(item) if unit.strip()]
    kept = [item for item in clauses if not is_challenge_accounting_policy(item)]
    excluded = len(clauses) - len(kept)
    return ChallengePolicySplit(" ".join(kept) if excluded else text, excluded)
