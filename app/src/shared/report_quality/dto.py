"""기존 보고서 정본을 생성 시점 평가기에 넘기는 공유 DTO.

``pipeline``·``report_standard``·``provenance`` 자료형을 이 기능이 직접 import하지
않는다. 실제 호출 feature가 기존 ``Report/FactRecord/Source``를 아래 값으로 한 번
투영하고, 평가 결과의 등급을 기존 ``Grade``로 다시 매핑한다.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SourceDocument:
    """출처 조각이 아닌 독립 문서 한 건의 신원."""

    source_id: str
    document_identity: str


@dataclass(frozen=True)
class ClaimFact:
    """기존 FactRecord에서 품질·안전 판정에 필요한 값만 옮긴 원자 주장."""

    fact_id: str
    section_owner: str
    source_id: str
    source_identity: str
    verification_state: str
    claim_slot: str = ""
    evidence_binding_valid: bool = False
    claim: str = ""
    subject_scope: str = ""
    raw_value: str = ""
    calculation: str = ""
    display_value: str = ""
    rounding_rule: str = ""
    numeric_checks: tuple[str, ...] = ()
    metric: str = ""
    period_start: str = ""
    period_end: str = ""
    sign: str = ""
    unit: str = ""
    unit_dimension: str = ""
    formula: str = ""


@dataclass(frozen=True)
class ReportSectionCandidate:
    """한 장의 공개 claim 결속과 안내문 상태."""

    section_id: str
    fact_ids: tuple[str, ...]
    notice_only: bool = False
    has_unbound_public_content: bool = False
    # composer처럼 구조화 claim 전환 중인 생성기가 실제 공개 문장 수를
    # 별도로 넘길 수 있다. ``None``은 예전 호출자가 아직 이 값을 모른다는
    # 뜻이며, assessor는 그때만 기존 fact slot 수를 사용한다. 0과 미측정을
    # 섞으면 빈 장을 정상 장으로 잘못 셀 수 있으므로 Optional 계약으로 둔다.
    public_sentence_count: int | None = None


@dataclass(frozen=True)
class ReportCandidate:
    """새 보고서 생성 직후 assessor가 받는 채널 중립 후보."""

    sections: tuple[ReportSectionCandidate, ...]
    facts: tuple[ClaimFact, ...]
    sources: tuple[SourceDocument, ...]
    summary_fact_ids: tuple[str, ...] = ()
    has_unbound_summary_content: bool = False
