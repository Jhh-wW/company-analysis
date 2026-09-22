"""공식 HTTP 비교시험의 고정 계약과 측정 한도."""

MANIFEST_SCHEMA = "company-evaluation-manifest-v1"
CHECKPOINT_SCHEMA = "company-http-evaluation-v1"
MAX_CASES = 50
POLL_INTERVAL_SECONDS = 2.0
POLL_TIMEOUT_SECONDS = 35 * 60
HTTP_TIMEOUT_SECONDS = 90.0
MIN_DUPLICATE_LINE_CHARACTERS = 20
NEWS_USAGE_STEP_NAMES = frozenset({"8_뉴스_본문활용", "뉴스_본문활용"})
# ``pilot_evaluation.runner._LedgerConsistencyError``가 실제로 내는 닫힌 코드만
# interruption 산출물로 운반한다. 예외문이나 임의 ``code`` 속성은 저장하지 않는다.
LEDGER_CONSISTENCY_ERROR_CODES = frozenset({
    "final_gate_evidence_invalid",
    "ledger_cost_invalid",
    "ledger_cost_mismatch",
    "ledger_lifecycle_cost_mismatch",
    "ledger_lifecycle_invalid",
    "ledger_lifecycle_record_invalid",
    "ledger_outcome_invalid",
    "ledger_outcome_report_mismatch",
    "ledger_spend_invalid",
})
FEATURE_KEYS = (
    "ENGINE_V2", "NEWS_INTAKE", "REVENUE_TABLE_V2", "TYPED_DART_COLLECTOR",
    "EVIDENCE_RECLASSIFY", "NEWSROOM_DATE_AI",
)
# 새 영수증은 네 값을 모두 남긴다. 과거 영수증의 생략은 실제 설정으로 추정하지 않는다.
PERFORMANCE_SETTING_ALLOWED_VALUES = {
    "REPORT_WRITER_MAX_PARALLEL_CALLS": ("1", "2", "3"),
    "PROVIDER_MAX_CONCURRENT_CALLS": ("1", "2", "3", "4", "5"),
    "NEWS_BODY_FETCH_CONCURRENCY": ("1", "2", "3"),
    "COMPOSER_REVIEW_PROMPT_CACHE_ENABLED": ("0", "1"),
}
DIAGNOSTIC_EXPORTS = {
    "observability_run_lifecycle": ("run_id", "state", "elapsed_sec", "final_record_json"),
    "observability_run_steps": ("run_id", "steps_json", "step_count", "omitted_count"),
    "report_cost_summaries": (
        "run_id", "outcome", "internal_ai_cost_krw", "customer_charge_krw",
        "charge_eligible", "automatic_release_sha256", "charge_reason",
    ),
    "budget_spend_events": ("run_id", "phase", "cost_krw"),
    "ai_variable_cost_events": (
        "run_id", "sequence", "stage", "model_id", "input_tokens", "output_tokens",
        "cache_creation_tokens", "cache_read_tokens", "cost_krw", "failed_call", "cache_hit",
    ),
}
