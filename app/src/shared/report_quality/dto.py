"""기존 보고서 정본을 생성 시점 평가기에 넘기는 공유 DTO.

``pipeline``·``report_standard``·``provenance`` 자료형을 이 기능이 직접 import하지
않는다. 실제 호출 feature가 기존 ``Report/FactRecord/Source``를 아래 값으로 한 번
투영하고, 평가 결과의 등급을 기존 ``Grade``로 다시 매핑한다.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class SourceDocument:
    """출처 조각이 아닌 독립 문서 한 건의 신원."""

    source_id: str
    document_identity: str
    exact_evidence_hashes: tuple[str, ...] = ()
    # 수집 경계에서 계산한 문서 *전체* 원문의 SHA-256. 조각별 해시나 URL은
    # 같은 문서를 여러 건처럼 부풀릴 수 있으므로 새 FULL(v3)의 독립 문서 수는
    # 이 값으로만 센다. 빈 기본값은 이 필드가 없던 과거 DTO 호출자 호환용이다.
    document_content_sha256: str = ""
    # 공식 비교 사실의 comparison_target을 실제 comparator Source 법인과
    # 맞추기 위한 수집 당시 발행자. URL이나 본문 단어로 법인명을 추측하지 않는다.
    publisher: str = ""


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
    supporting_source_ids: tuple[str, ...] = ()
    supporting_source_identities: tuple[str, ...] = ()
    supporting_evidence_hashes: tuple[str, ...] = ()
    #: 공식 양사 비교기만 쓰는 프로그램 검산 입력. 일반 NumericBinding은
    #: 한 문서 신원만 소유하므로 양사 네 원값을 억지로 한 출처로 합치지 않는다.
    claim_type: str = ""
    legal_entity: str = ""
    # 비교 맥락의 exact 문장 정본과 claim↔근거어↔원문 교집합을 최종 안전
    # 평가에서도 같은 validator로 재검산하기 위한 손실 없는 생산 필드.
    state_evidence: str = ""
    evidence_support_terms: tuple[str, ...] = ()
    comparison_target: str = ""
    comparison_metric: str = ""
    comparison_definition: str = ""
    comparison_basis: str = ""
    comparison_period: str = ""
    comparison_scope: str = ""
    comparison_judgment: str = ""
    comparator_source_id: str = ""
    comparator_state_evidence: str = ""
    # 양사 비교 프로그램의 고객·제품·시장·기간·계정·회계범위 정합성을
    # 개별 fact 검산 뒤에도 같은 shared validator로 재검산하기 위한 원본 맵.
    comparison_conditions: dict[str, str] = field(default_factory=dict)


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
