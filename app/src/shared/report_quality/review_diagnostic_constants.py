"""작성기와 실행기가 공유하는 원문 없는 검수 진단의 전송 계약."""

import re

from src.shared.report_quality.constants import STRICT_REQUIRED_QUALITY_SECTION_IDS

REVIEW_SECTION_IDS = frozenset((*STRICT_REQUIRED_QUALITY_SECTION_IDS, "summary"))
REVIEW_KINDS = ("본문", "요약", "도식")
REVIEW_SCOPE_ITEMS = {
    "planned_claim_asserted": "계획·성과",
    "scope_condition_unbound": "조건·적용대상",
    "culture_evidence_scope_mismatch": "공식 조직설명",
    "culture_accounting_policy_misplaced": "장별 작성범위",
    "superlative_attribution_without_source": "회사 직접표현",
    "causal_relation_evidence_missing": "인과 관계",
    "causal_relation_quote_not_in_source": "인과 관계",
    "causal_relation_quote_not_causal": "인과 관계",
    "causal_relation_pair_missing": "인과 관계",
    "causal_relation_pair_not_in_claim": "인과 관계",
    "causal_relation_pair_not_in_quote": "인과 관계",
    "causal_relation_direction_reversed": "인과 관계",
    "causal_relation_negated_in_source": "인과 관계",
    "causal_relation_pair_degenerate": "인과 관계",
    "causal_relation_roles_unproven": "인과 관계",
    "causal_relation_claim_roles_mismatch": "인과 관계",
    "causal_relation_field_type_invalid": "인과 관계",
    "causal_relation_slot_direction_only": "인과 관계",
    "causal_relation_claim_not_covered": "인과 관계",
    "causal_relation_source_id_empty": "인과 관계",
    "causal_relation_hedged_in_source": "인과 관계",
}
REVIEW_REASONS = (
    "semantic_grounding_missing", "semantic_grounding_invalid", *REVIEW_SCOPE_ITEMS,
)
REVIEW_ITEMS = ("수치", "추세", "시점", *REVIEW_SCOPE_ITEMS.values())
CANDIDATE_FINGERPRINT_RE = re.compile(r"[0-9a-f]{64}")
