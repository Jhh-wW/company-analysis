"""연결대상 회사 수만으로 그 회사 모두의 배당 지급을 단정하지 않는다.

원문의 집단 수와 배당 설명이 각각 맞아도 둘을 합친 관계는 별도 사실이다.
이 검사는 숫자가 붙은 직접 배당 관계만 대조하며 다른 의미 검수를 대체하지 않는다.
"""
from __future__ import annotations

from collections.abc import Mapping
import unicodedata

from src.features.composer.quantified_relation_constants import (
    DIRECT_PAYMENT, DIRECT_RECEIPT, GROUP_COUNT_RE, GROUP_COUNT_REVERSED,
    NON_ACTUAL, QUANTIFIED_DIVIDEND_UNBOUND, RECIPIENT_CLAIM, SENTENCE_BOUNDARY,
)


def quantified_dividend_problem(text: str, sources: Mapping[str, str]) -> str:
    claims = tuple(
        claim for clause in SENTENCE_BOUNDARY.split(unicodedata.normalize("NFKC", text))
        for claim in RECIPIENT_CLAIM.finditer(clause)
    )
    if not claims:
        return ""
    clauses = [
        clause for source in sources.values()
        for clause in SENTENCE_BOUNDARY.split(unicodedata.normalize("NFKC", source))
    ]
    for claim in claims:
        # 이 검사는 실제 수취를 넓혀 쓴 주장만 다룬다. 계획·부정 문장은
        # 기존 시점·양태 검수에 남겨 참인 부정까지 긍정 관계로 바꾸지 않는다.
        if NON_ACTUAL.search(claim["action"]):
            continue
        count = int(claim["count"].replace(",", ""))
        bound = False
        for clause in clauses:
            for pattern in (GROUP_COUNT_RE, GROUP_COUNT_REVERSED):
                for group in pattern.finditer(clause):
                    if group["kind"] != claim["kind"] or int(group["count"].replace(",", "")) != count:
                        continue
                    tail = clause[group.end():]
                    if NON_ACTUAL.search(tail):
                        continue
                    if DIRECT_RECEIPT.search(tail) or DIRECT_PAYMENT.search(tail):
                        bound = True
            if bound:
                break
        if not bound:
            return QUANTIFIED_DIVIDEND_UNBOUND
    return ""
