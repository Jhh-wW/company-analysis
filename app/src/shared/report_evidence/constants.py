"""근거 준비 계약의 버전과 기계 상태값."""

from __future__ import annotations

from enum import Enum
from typing import Final


EVIDENCE_CONTRACT_VERSION: Final[str] = "report-evidence-v1"


class CollectionState(str, Enum):
    """외부 자료 한 경로를 확인한 결과."""

    OK = "OK"
    MISSING = "MISSING"
    FAILED = "FAILED"
    TRUNCATED = "TRUNCATED"


class EvidenceReadiness(str, Enum):
    """한 장을 쓸 근거가 준비됐는지에 대한 판정."""

    READY = "READY"
    INSUFFICIENT = "INSUFFICIENT"
    UNKNOWN = "UNKNOWN"


class SourceRequirement(str, Enum):
    """해당 조회 경로가 필수 의미를 확인하는 유일한 길인지 구분한다."""

    REQUIRED = "REQUIRED"
    OPTIONAL = "OPTIONAL"


class SourceTier(str, Enum):
    """제품 정책으로 허용한 출처 등급."""

    TIER_1_OFFICIAL = "TIER_1_OFFICIAL"
    TIER_2_PUBLIC = "TIER_2_PUBLIC"
    TIER_3_TRUSTED = "TIER_3_TRUSTED"


class ReportExecutionOutcome(str, Enum):
    """사용자 요청이 최종적으로 끝나는 네 가지 상태."""

    COMPLETE = "COMPLETE"
    INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
    TRANSIENT_FAILURE = "TRANSIENT_FAILURE"
    QUALITY_FAILURE = "QUALITY_FAILURE"


class GenerationGateStatus(str, Enum):
    """유료 보고서 작성기를 부르기 직전의 결정."""

    READY_FOR_GENERATION = "READY_FOR_GENERATION"
    STOP_INSUFFICIENT_EVIDENCE = "STOP_INSUFFICIENT_EVIDENCE"
    STOP_TRANSIENT_FAILURE = "STOP_TRANSIENT_FAILURE"


class ReleaseMode(str, Enum):
    """새 계약이 기존 사용자 결과에 미치는 범위."""

    SHADOW = "SHADOW"
    ENFORCE_NO_PARTIAL = "ENFORCE_NO_PARTIAL"
    FULL = "FULL"
