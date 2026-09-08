"""공식 HTTP 비교시험의 고정 계약과 측정 한도."""

MANIFEST_SCHEMA = "company-evaluation-manifest-v1"
CHECKPOINT_SCHEMA = "company-http-evaluation-v1"
MAX_CASES = 50
POLL_INTERVAL_SECONDS = 2.0
POLL_TIMEOUT_SECONDS = 35 * 60
HTTP_TIMEOUT_SECONDS = 90.0
MIN_DUPLICATE_LINE_CHARACTERS = 20
NEWS_USAGE_STEP_NAMES = frozenset({"8_뉴스_본문활용", "뉴스_본문활용"})
FEATURE_KEYS = (
    "ENGINE_V2", "NEWS_INTAKE", "REVENUE_TABLE_V2", "TYPED_DART_COLLECTOR",
    "EVIDENCE_RECLASSIFY", "NEWSROOM_DATE_AI",
)
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
