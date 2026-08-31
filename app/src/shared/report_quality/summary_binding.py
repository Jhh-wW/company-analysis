"""검증 본문을 재사용한 핵심요약의 결정론적 결속 계약.

요약은 새 사실을 만들지 않는다. 본문 사실 ID와 공개 문장, 검증 상태를 한
지문에 묶어 저장 뒤 어느 한 글자라도 바뀌면 다시 검증하도록 한다. 구체적인
Report/FactRecord 자료형에는 의존하지 않아 여러 보고서 경로가 같은 계산법을
사용할 수 있다.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Protocol


class SummaryFact(Protocol):
    """요약 근거 묶음에 필요한 사실의 최소 읽기 계약."""

    claim: str


def summary_evidence_text(
    fact_ids: Sequence[str], facts: Mapping[str, SummaryFact]
) -> str:
    """요약 검증기가 보는 사실 ID·주장 묶음을 입력 순서대로 만든다.

    없는 사실을 건너뛰지 않고 ``KeyError``로 닫는다. 일부 근거만 남긴 요약을
    정상 묶음처럼 봉인하는 것보다 생성 경계에서 실패하는 편이 안전하다.
    """

    return "\n".join(f"{fact_id}: {facts[fact_id].claim}" for fact_id in fact_ids)


def summary_verification_binding(
    text: str,
    section_id: str,
    fact_ids: Sequence[str],
    evidence_text: str,
    verification_status: str,
    support_terms: Sequence[str],
) -> str:
    """요약과 근거·검증 판정 전체를 잠그는 SHA-256 지문."""

    payload = {
        "text": text,
        "section_id": section_id,
        "fact_ids": list(fact_ids),
        "evidence_text": evidence_text,
        "verification_status": verification_status,
        "support_terms": list(support_terms),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()
