"""평가기가 읽는 비용 원장의 버전과 상태 계약."""

from typing import Final


ATTEMPT_LEDGER_VERSION: Final[str] = "attempt-ledger-v1"
MIGRATION_TABLE: Final[str] = "budget_schema_migrations"
PHASE_TABLE: Final[str] = "budget_phase_accounts"
ATTEMPT_TABLE: Final[str] = "budget_provider_attempts"
ATTEMPT_EVENT_TABLE: Final[str] = "budget_provider_attempt_events"
LEGACY_SPEND_TABLE: Final[str] = "budget_spend_events"
LEGACY_INFLIGHT_TABLE: Final[str] = "budget_spend_inflight"
PHASE_STATES: Final[frozenset[str]] = frozenset(
    {"ACTIVE", "SUCCEEDED", "FAILED", "UNKNOWN_LEGACY"}
)
TRANSPORT_STATES: Final[frozenset[str]] = frozenset(
    {"PLANNED", "DISPATCH_INTENT_RECORDED", "RESPONSE_RECEIVED",
     "TRANSPORT_AMBIGUOUS", "LOCAL_FAILURE", "UNKNOWN_LEGACY"}
)
SETTLED_BILLING_STATES: Final[frozenset[str]] = frozenset(
    {"KNOWN_COST", "KNOWN_ZERO"}
)
LIABILITY_BILLING_STATES: Final[frozenset[str]] = frozenset(
    {"CONSERVATIVE_LIABILITY", "LIABILITY_CONFIRMED", "UNKNOWN_LEGACY"}
)
