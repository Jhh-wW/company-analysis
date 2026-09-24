"""FULL 공개 안전 판정의 문제 문장을 닫힌 «유형 코드»로 접는다.

안전 판정(``assessment.py``)은 사람이 읽는 문장으로 문제를 남긴다. 그 문장에는
fact_id·source_id·claim_type 값이 섞여 있어 실행 기록에 그대로 실을 수 없다.
진단 한 줄은 이 모듈이 돌려주는 닫힌 코드와 개수만 싣는다. 모르는 문장은
``other`` 로 센다 — 코드를 지어내지 않는다. 판정 문구가 바뀌어 ``other`` 로 새면
표류 방지 시험(판정 모듈의 문구 틀을 전부 이 표에 넣어 보는 시험)이 잡는다.
"""

from __future__ import annotations

from typing import Final

SAFETY_KIND_UNBOUND_PUBLIC_CONTENT: Final[str] = "unbound_public_content"
SAFETY_KIND_NUMERIC_LABELS_MISSING: Final[str] = "numeric_labels_missing"
SAFETY_KIND_NUMERIC_BINDING_MISSING: Final[str] = "numeric_binding_missing"
#: «{fact_id}: …» — 뉴스 산문·비교·수치 검산 같은 하위 검사기가 붙인 세부 문구.
SAFETY_KIND_CLAIM_DETAIL: Final[str] = "claim_detail"
SAFETY_KIND_COMPARISON_PROGRAM: Final[str] = "comparison_program"
SAFETY_KIND_SUMMARY_BINDING: Final[str] = "summary_binding"
SAFETY_KIND_UNVERIFIED_CLAIM: Final[str] = "unverified_claim"
SAFETY_KIND_REJECTED_CLAIM: Final[str] = "rejected_claim"
SAFETY_KIND_CLAIM_TYPE_UNKNOWN: Final[str] = "claim_type_unknown"
SAFETY_KIND_CLAIM_SLOT: Final[str] = "claim_slot"
SAFETY_KIND_CLAIM_EMPTY: Final[str] = "claim_empty"
SAFETY_KIND_DUPLICATE_PUBLIC_FACT: Final[str] = "duplicate_public_fact"
SAFETY_KIND_EVIDENCE_BINDING_INVALID: Final[str] = "evidence_binding_invalid"
SAFETY_KIND_VERIFICATION_STATE_UNKNOWN: Final[str] = "verification_state_unknown"
SAFETY_KIND_SECTION_OWNER_MISMATCH: Final[str] = "section_owner_mismatch"
SAFETY_KIND_NO_PUBLIC_CLAIM: Final[str] = "no_public_claim"
SAFETY_KIND_FACT_REGISTRY: Final[str] = "fact_registry"
SAFETY_KIND_EXACT_EVIDENCE: Final[str] = "exact_evidence"
SAFETY_KIND_SOURCE_REFERENCE: Final[str] = "source_reference"
SAFETY_KIND_SOURCE_REGISTRY: Final[str] = "source_registry"
SAFETY_KIND_SECTION_STRUCTURE: Final[str] = "section_structure"
SAFETY_KIND_OTHER: Final[str] = "other"

#: 진단 한 줄이 받는 유형 코드의 닫힌 목록. 기록·정화기 모두 이 순서로 싣는다.
SAFETY_PROBLEM_KINDS: Final[tuple[str, ...]] = (
    SAFETY_KIND_UNBOUND_PUBLIC_CONTENT,
    SAFETY_KIND_NUMERIC_LABELS_MISSING,
    SAFETY_KIND_NUMERIC_BINDING_MISSING,
    SAFETY_KIND_CLAIM_DETAIL,
    SAFETY_KIND_COMPARISON_PROGRAM,
    SAFETY_KIND_SUMMARY_BINDING,
    SAFETY_KIND_UNVERIFIED_CLAIM,
    SAFETY_KIND_REJECTED_CLAIM,
    SAFETY_KIND_CLAIM_TYPE_UNKNOWN,
    SAFETY_KIND_CLAIM_SLOT,
    SAFETY_KIND_CLAIM_EMPTY,
    SAFETY_KIND_DUPLICATE_PUBLIC_FACT,
    SAFETY_KIND_EVIDENCE_BINDING_INVALID,
    SAFETY_KIND_VERIFICATION_STATE_UNKNOWN,
    SAFETY_KIND_SECTION_OWNER_MISMATCH,
    SAFETY_KIND_NO_PUBLIC_CLAIM,
    SAFETY_KIND_FACT_REGISTRY,
    SAFETY_KIND_EXACT_EVIDENCE,
    SAFETY_KIND_SOURCE_REFERENCE,
    SAFETY_KIND_SOURCE_REGISTRY,
    SAFETY_KIND_SECTION_STRUCTURE,
    SAFETY_KIND_OTHER,
)

#: «비교 프로그램: …» — 비교 검산기가 붙인 머리.
_COMPARISON_PROGRAM_PREFIX: Final[str] = "비교 프로그램: "
#: «{fact_id}: …» 의 구분자. 앞머리에 빈칸이 없을 때만 fact_id 로 본다.
_CLAIM_DETAIL_SEPARATOR: Final[str] = ": "

#: (포함 문구, 유형) — 위에서부터 처음 맞는 것을 쓴다. 문구는 판정 모듈의 틀에서
#: 건마다 달라지는 자리(fact_id·장 이름·source_id)를 뺀 고정 부분이다.
_PHRASE_KINDS: Final[tuple[tuple[str, str], ...]] = (
    ("요약에 본문 fact_id와 결속되지 않은 공개 내용이 있습니다", SAFETY_KIND_SUMMARY_BINDING),
    ("요약 fact_id가 중복됐습니다", SAFETY_KIND_SUMMARY_BINDING),
    ("가 검증 본문의 부분집합이 아닙니다", SAFETY_KIND_SUMMARY_BINDING),
    ("장에 fact_id와 결속되지 않은 공개 내용이 있습니다", SAFETY_KIND_UNBOUND_PUBLIC_CONTENT),
    ("의 구조화 수치 이름표가 비었습니다", SAFETY_KIND_NUMERIC_LABELS_MISSING),
    ("versioned NumericBinding이 없습니다", SAFETY_KIND_NUMERIC_BINDING_MISSING),
    ("검증하지 못한 공개 claim이 있습니다", SAFETY_KIND_UNVERIFIED_CLAIM),
    ("거절된 claim이 공개 후보에 남아 있습니다", SAFETY_KIND_REJECTED_CLAIM),
    ("의 공개 claim_type을 알 수 없습니다", SAFETY_KIND_CLAIM_TYPE_UNKNOWN),
    ("의 계획된 claim slot이 비었습니다", SAFETY_KIND_CLAIM_SLOT),
    ("의 claim slot이 ", SAFETY_KIND_CLAIM_SLOT),
    ("의 공개 claim이 비었습니다", SAFETY_KIND_CLAIM_EMPTY),
    ("중복 공개했습니다", SAFETY_KIND_DUPLICATE_PUBLIC_FACT),
    ("중복 공개됐습니다", SAFETY_KIND_DUPLICATE_PUBLIC_FACT),
    ("장 안에서 fact_id가 중복됐습니다", SAFETY_KIND_DUPLICATE_PUBLIC_FACT),
    ("의 원문·주장 결속 지문이 유효하지 않습니다", SAFETY_KIND_EVIDENCE_BINDING_INVALID),
    ("의 검증 상태를 알 수 없습니다", SAFETY_KIND_VERIFICATION_STATE_UNKNOWN),
    ("의 소유 장과 공개 장이 일치하지 않습니다", SAFETY_KIND_SECTION_OWNER_MISMATCH),
    ("공개할 원자 claim이 없습니다", SAFETY_KIND_NO_PUBLIC_CLAIM),
    ("가 사실 장부에 없습니다", SAFETY_KIND_FACT_REGISTRY),
    ("의 정확한 원문 조각 결속이 비었습니다", SAFETY_KIND_EXACT_EVIDENCE),
    ("의 다중 출처 결속 열 길이가 다릅니다", SAFETY_KIND_EXACT_EVIDENCE),
    ("의 다중 출처 source_id가 중복됐습니다", SAFETY_KIND_EXACT_EVIDENCE),
    ("의 대표 출처가 다중 출처 첫 항목과 다릅니다", SAFETY_KIND_EXACT_EVIDENCE),
    ("원문 조각 해시가 다릅니다", SAFETY_KIND_EXACT_EVIDENCE),
    ("존재하지 않는 source_id를 참조합니다", SAFETY_KIND_SOURCE_REFERENCE),
    ("존재하지 않는 보조 source_id", SAFETY_KIND_SOURCE_REFERENCE),
    ("의 독립 문서 identity가 출처 장부와 다릅니다", SAFETY_KIND_SOURCE_REFERENCE),
    ("문서 identity가 다릅니다", SAFETY_KIND_SOURCE_REFERENCE),
    ("장이 안내문 전용인데 fact_id도 함께 있습니다", SAFETY_KIND_SECTION_STRUCTURE),
    ("장에 빈 fact_id가 있습니다", SAFETY_KIND_SECTION_STRUCTURE),
    ("빈 section_id가 있습니다", SAFETY_KIND_SECTION_STRUCTURE),
    ("빈 fact_id가 있습니다", SAFETY_KIND_FACT_REGISTRY),
)

#: (머리, 유형) — 주어 이름표로만 갈리는 장부 문장(«source_id X가 중복됐습니다» 등).
_PREFIX_KINDS: Final[tuple[tuple[str, str], ...]] = (
    ("출처의 source_id", SAFETY_KIND_SOURCE_REGISTRY),
    ("source_id ", SAFETY_KIND_SOURCE_REGISTRY),
    ("section_id ", SAFETY_KIND_SECTION_STRUCTURE),
    ("fact_id ", SAFETY_KIND_FACT_REGISTRY),
)


def safety_problem_kind(problem: str) -> str:
    """안전 판정 문제 문장 하나를 닫힌 유형 코드로 바꾼다. 모르면 ``other``.

    Args:
        problem: 안전 판정이 만든 문제 문장. fact_id 해시·장 이름을 자리표시자로
            바꾼 요약 문장(``summarize_safety_problems``)이어도 같은 코드가 나온다.

    Returns:
        ``SAFETY_PROBLEM_KINDS`` 안의 코드 하나.
    """

    text = str(problem or "")
    if text.startswith(_COMPARISON_PROGRAM_PREFIX):
        return SAFETY_KIND_COMPARISON_PROGRAM
    head, separator, _detail = text.partition(_CLAIM_DETAIL_SEPARATOR)
    if separator and head and " " not in head:
        return SAFETY_KIND_CLAIM_DETAIL
    for phrase, kind in _PHRASE_KINDS:
        if phrase in text:
            return kind
    for prefix, kind in _PREFIX_KINDS:
        if text.startswith(prefix):
            return kind
    return SAFETY_KIND_OTHER


__all__ = [
    "SAFETY_KIND_CLAIM_DETAIL",
    "SAFETY_KIND_NUMERIC_BINDING_MISSING",
    "SAFETY_KIND_NUMERIC_LABELS_MISSING",
    "SAFETY_KIND_OTHER",
    "SAFETY_KIND_UNBOUND_PUBLIC_CONTENT",
    "SAFETY_PROBLEM_KINDS",
    "safety_problem_kind",
]
