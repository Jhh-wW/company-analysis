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
    "challenge_response_missing": "장별 작성범위",
    "future_section_no_forward_statement": "장별 작성범위",
    "culture_financial_risk_scope_misplaced": "장별 작성범위",
    "culture_section_evidence_offcontract": "장별 작성범위",
    # ★ 장과 무관한 부재 단언(composer.absence_claim_constants). 코드 문자열은
    #   그 모듈의 상수와 «반드시 같은 값»이어야 한다.
    "absence_claim_unsupported": "자료 부재 단언",
    # ★ «확인» 산문의 자기 원문 근거(composer.prose_own_source_constants).
    #   코드 문자열은 그 모듈의 상수와 «반드시 같은 값»이어야 한다.
    "prose_own_source_unsupported": "자기 근거 부족",
    "prose_revenue_primacy_from_recognition_only": "자기 근거 부족",
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
    # 역할·과금 결속(composer.role_binding). 코드 문자열은 그 모듈의
    # ROLE_BINDING_* 상수와 «반드시 같은 값»이어야 진단이 표에서 새지 않는다.
    "role_binding_evidence_missing": "역할·과금 결속",
    "role_binding_field_type_invalid": "역할·과금 결속",
    "role_binding_pair_missing": "역할·과금 결속",
    "role_binding_pair_degenerate": "역할·과금 결속",
    "role_binding_kind_does_not_answer_claim": "역할·과금 결속",
    "role_binding_not_own_citation": "역할·과금 결속",
    "role_binding_quote_not_in_source": "역할·과금 결속",
    "role_binding_target_not_in_candidate": "역할·과금 결속",
    "role_binding_role_not_in_candidate": "역할·과금 결속",
    "role_binding_actor_boundary_crossed": "역할·과금 결속",
    "role_binding_role_outside_quote": "역할·과금 결속",
    "role_binding_unbound_in_source": "역할·과금 결속",
    "role_binding_negated_in_source": "역할·과금 결속",
    "role_binding_condition_dropped_from_source": "역할·과금 결속",
    "role_binding_direction_reversed": "역할·과금 결속",
    "role_binding_claim_not_covered": "역할·과금 결속",
    # ★ 6장 「회사가 밝힌 성장 계획」 표의 미래 근거 결속 사유.
    #   composer 의 future_plan_constants.FUTURE_REASON_CODES 와 같은 목록이며,
    #   어긋나면 test_future_plan_guard 의 동기화 시험이 깨진다. 공유 계층이
    #   feature 를 import 하지 않도록 인과 코드와 같은 방식으로 글자를 적는다.
    "future_plan_evidence_missing": "미래 계획 근거",
    "future_plan_field_type_invalid": "미래 계획 근거",
    "future_plan_slots_missing": "미래 계획 근거",
    "future_plan_slots_degenerate": "미래 계획 근거",
    "future_plan_target_too_short": "미래 계획 근거",
    "future_plan_target_generic": "미래 계획 근거",
    "future_plan_activity_too_short": "미래 계획 근거",
    "future_plan_target_not_in_candidate": "미래 계획 근거",
    "future_plan_activity_not_in_candidate": "미래 계획 근거",
    "future_plan_slots_split_across_cells": "미래 계획 근거",
    "future_plan_source_id_empty": "미래 계획 근거",
    "future_plan_source_not_cited": "미래 계획 근거",
    "future_plan_quote_missing": "미래 계획 근거",
    "future_plan_quote_too_short": "미래 계획 근거",
    "future_plan_quote_not_in_source": "미래 계획 근거",
    "future_plan_target_not_in_quote": "미래 계획 근거",
    "future_plan_activity_not_in_quote": "미래 계획 근거",
    "future_plan_target_not_bound_to_activity": "미래 계획 근거",
    "future_plan_modality_not_bound": "미래 계획 근거",
    "future_plan_source_states_current": "미래 계획 근거",
    "future_plan_subject_mismatch": "미래 계획 근거",
    "future_plan_mode_invalid": "미래 계획 근거",
    "future_plan_mode_misdeclared": "미래 계획 근거",
    "future_plan_outlook_hardened": "미래 계획 근거",
    "future_plan_polarity_flipped": "미래 계획 근거",
    "future_plan_candidate_states_current": "미래 계획 근거",
    "future_plan_denied_in_source": "미래 계획 근거",
    "future_plan_second_claim_unproven": "미래 계획 근거",
    "future_plan_claim_cell_inconsistent": "미래 계획 근거",
    # ★ 임원 직함 시점 가드(composer.executive_status_guard). 코드 문자열은 그
    #   모듈의 EXECUTIVE_STATUS_* 상수와 «반드시 같은 값»이어야 진단이 표에서
    #   새지 않는다.
    "executive_status_outdated": "임원 재직 확인",
}
REVIEW_REASONS = (
    "semantic_grounding_missing", "semantic_grounding_invalid", *REVIEW_SCOPE_ITEMS,
)
REVIEW_ITEMS = ("수치", "추세", "시점", *REVIEW_SCOPE_ITEMS.values())
CANDIDATE_FINGERPRINT_RE = re.compile(r"[0-9a-f]{64}")
