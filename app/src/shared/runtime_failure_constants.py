"""실행 실패 진단에 저장할 닫힌 코드 목록."""

from __future__ import annotations

from typing import Final


RUNTIME_FAILURE_STEP: Final[str] = "runtime_failure"

PHASE_PIPELINE: Final[str] = "pipeline"
PHASE_COORDINATION: Final[str] = "coordination"
PHASE_AI_CALL: Final[str] = "ai_call"
PHASE_ASSEMBLY: Final[str] = "assembly"
PHASE_SAVE: Final[str] = "save"
PHASE_DELIVERY: Final[str] = "delivery"

ALLOWED_PHASES: Final[frozenset[str]] = frozenset(
    {
        PHASE_PIPELINE,
        PHASE_COORDINATION,
        PHASE_AI_CALL,
        PHASE_ASSEMBLY,
        PHASE_SAVE,
        PHASE_DELIVERY,
    }
)

REASON_UNEXPECTED_PIPELINE_FAILURE: Final[str] = "unexpected_pipeline_failure"
REASON_GENERATION_COORDINATION_FAILED: Final[str] = (
    "generation_coordination_failed"
)
REASON_GENERATION_OWNER_FAILED: Final[str] = "generation_owner_failed"
REASON_PROVIDER_ADMISSION_FAILED: Final[str] = "provider_admission_failed"
REASON_PROVIDER_CALL_FAILED: Final[str] = "provider_call_failed"
REASON_REPORT_ASSEMBLY_FAILED: Final[str] = "report_assembly_failed"
REASON_COST_PERSISTENCE_FAILED: Final[str] = "cost_persistence_failed"
REASON_REPORT_PERSISTENCE_FAILED: Final[str] = "report_persistence_failed"
REASON_DELIVERY_PREPARE_FAILED: Final[str] = "delivery_prepare_failed"
REASON_DELIVERY_FINALIZE_FAILED: Final[str] = "delivery_finalize_failed"
REASON_GENERATION_FINALIZE_FAILED: Final[str] = "generation_finalize_failed"

ALLOWED_REASON_CODES: Final[frozenset[str]] = frozenset(
    {
        REASON_UNEXPECTED_PIPELINE_FAILURE,
        REASON_GENERATION_COORDINATION_FAILED,
        REASON_GENERATION_OWNER_FAILED,
        REASON_PROVIDER_ADMISSION_FAILED,
        REASON_PROVIDER_CALL_FAILED,
        REASON_REPORT_ASSEMBLY_FAILED,
        REASON_COST_PERSISTENCE_FAILED,
        REASON_REPORT_PERSISTENCE_FAILED,
        REASON_DELIVERY_PREPARE_FAILED,
        REASON_DELIVERY_FINALIZE_FAILED,
        REASON_GENERATION_FINALIZE_FAILED,
    }
)

STATE_FAILED: Final[str] = "failed"
ROLE_WORKER: Final[str] = "worker"
ROLE_OWNER: Final[str] = "owner"
ROLE_WAITER: Final[str] = "waiter"
ALLOWED_ROLES: Final[frozenset[str]] = frozenset(
    {ROLE_WORKER, ROLE_OWNER, ROLE_WAITER}
)
UNKNOWN_EXCEPTION_CLASS: Final[str] = "UnknownException"
EXCEPTION_CLASS_MAX_CHARS: Final[int] = 80
