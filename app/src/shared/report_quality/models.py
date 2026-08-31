"""여러 보고서 기능이 공유하는 품질·공개 안전 자료형."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import Enum

class ReleaseDecision(str, Enum):
    """검증 결과의 공개 가능 여부."""

    RELEASE_ALLOWED = "공개 가능"
    BLOCKED = "공개 차단"


class QualityGrade(str, Enum):
    """보고서 내용의 충분성. 기존 Report 등급은 호출 경계에서 변환한다."""

    COMPLETE = "완성"
    PARTIAL = "부분 완성"
    INCOMPLETE = "미완성"


class QualityProblemCode(str, Enum):
    """사람 문구를 다시 읽지 않고 회복 가능성을 판단하는 닫힌 코드."""

    TOO_MANY_NOTICE_ONLY_SECTIONS = "too_many_notice_only_sections"
    ONE_CLAIM_SECTIONS = "one_claim_sections"
    LOW_SEMANTIC_COVERAGE = "low_semantic_coverage"
    LOW_PUBLIC_SENTENCE_COVERAGE = "low_public_sentence_coverage"
    TOO_FEW_SUBSTANTIVE_CLAIMS = "too_few_substantive_claims"
    LOW_VERIFIED_RATIO = "low_verified_ratio"
    TOO_FEW_DOCUMENT_SOURCES = "too_few_document_sources"


class ContractUse(str, Enum):
    """같은 버전을 새 생성과 과거 조회에 섞지 않기 위한 호출 목적."""

    GENERATION = "새 보고서 생성"
    HISTORICAL_READ = "과거 보고서 조회"


class HistoricalReadPolicy(str, Enum):
    """발급 뒤 계약을 현재 코드로 소급 적용할지에 대한 정책."""

    PRESERVE_ISSUED = "발급 당시 결과 유지"


class PublicationPolicy(str, Enum):
    """안전 판정과 실제 공개 행동을 섞지 않기 위한 저장 정책."""

    STRUCTURED_SAFETY = "structured-safety-v1"
    LEGACY_SHADOW_EXCEPTION = "legacy-shadow-exception-v1"


class VerificationState(str, Enum):
    """주장의 검증 상태. 문체 등급과 분리한다."""

    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    REJECTED = "rejected"


@dataclass(frozen=True)
class QualityContract:
    """한 버전의 COMPLETE/PARTIAL 판정 기준."""

    version: str
    required_section_ids: tuple[str, ...]
    min_claims_per_covered_section: int
    min_substantive_claims: int
    min_verified_ratio: Decimal
    min_document_sources: int
    max_notice_only_sections: int
    historical_read_policy: HistoricalReadPolicy


@dataclass(frozen=True)
class ContractResolution:
    """호출 목적에 따라 선택된 계약과 소급 판정 여부."""

    requested_version: str
    resolved_version: str
    use: ContractUse
    assess_now: bool
    preserve_issued: bool
    reason: str


@dataclass(frozen=True)
class QualityAssessment:
    """보고서의 충분성. 공개 안전 여부는 포함하지 않는다."""

    contract_version: str
    grade: QualityGrade
    substantive_claims: int
    verified_claims: int
    verified_ratio: Decimal
    document_sources: int
    notice_only_sections: tuple[str, ...]
    one_claim_sections: tuple[str, ...]
    section_claim_counts: tuple[tuple[str, int], ...]
    shortfall_reasons: tuple[str, ...]
    # 구조화 전환 중에도 장별 분량을 정직하게 판정하기 위한 공개 문장 수.
    # 원자 fact 수(``section_claim_counts``)와 섞지 않는다.
    section_public_sentence_counts: tuple[tuple[str, int], ...] = ()
    underfilled_sections: tuple[str, ...] = ()
    semantic_underfilled_sections: tuple[str, ...] = ()
    # 회복·출고 상태기계는 사람이 읽는 ``shortfall_reasons`` 문자열을 해석하지
    # 않고 이 닫힌 코드만 읽는다. 문구가 바뀌어도 행동이 달라지지 않는다.
    problem_codes: tuple[QualityProblemCode, ...] = ()


@dataclass(frozen=True)
class SafetyAssessment:
    """공개 가능한지에 대한 검증 결과. 분량과 풍부함은 포함하지 않는다."""

    contract_version: str
    decision: ReleaseDecision
    verified_fact_ids: tuple[str, ...]
    unverified_fact_ids: tuple[str, ...]
    rejected_fact_ids: tuple[str, ...]
    problems: tuple[str, ...]


@dataclass(frozen=True)
class GenerationAssessment:
    """새 보고서 한 건의 품질·안전 결과와 최종 권장 등급."""

    contract_version: str
    quality: QualityAssessment
    safety: SafetyAssessment
    publication_grade: QualityGrade

    @property
    def release_allowed(self) -> bool:
        """안전 검증을 통과하고 내용이 한 건 이상 있는가."""

        return (
            self.safety.decision is ReleaseDecision.RELEASE_ALLOWED
            and self.quality.grade is not QualityGrade.INCOMPLETE
        )
