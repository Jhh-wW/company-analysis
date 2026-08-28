"""여러 보고서 생성기가 함께 쓰는 FactRecord 근거 결속 지문.

기존 canonical 지문의 payload는 글자 하나도 바꾸지 않는다. 새 구조화 claim
필드는 값이 실제로 있을 때만 덧붙여 과거 FactRecord의 지문과 승인 해시를
그대로 보존한다.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Final


_OPTIONAL_STRUCTURED_FIELDS: Final[tuple[str, ...]] = (
    "claim_slot",
    "metric",
    "period_start",
    "period_end",
    "sign",
    "unit",
    "unit_dimension",
    "formula",
)


def _binding_digest(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def fact_evidence_binding(fact: Any) -> str:
    """claim·원문·출처·구조를 함께 잠그는 결정론적 SHA-256 지문."""

    payload: dict[str, object] = {
        "fact_id": fact.fact_id,
        "legal_entity": fact.legal_entity,
        "subject_scope": fact.subject_scope,
        "relationship_or_action": fact.relationship_or_action,
        "claim": fact.claim,
        "claim_type": fact.claim_type,
        "section_owner": fact.section_owner,
        "time_state": fact.time_state,
        "as_of": fact.as_of,
        "source_id": fact.source_id,
        "source_type": fact.source_type,
        "source_title": fact.source_title,
        "source_publisher": fact.source_publisher,
        "source_host": fact.source_host,
        "source_url": fact.source_url,
        "source_document_id": fact.source_document_id,
        "source_date": fact.source_date,
        "location": fact.location,
        "state_evidence": fact.state_evidence,
        "fact_status": fact.fact_status,
        "verification_status": fact.verification_status,
        "evidence_support_terms": list(fact.evidence_support_terms),
        "raw_value": fact.raw_value,
        "calculation": fact.calculation,
        "display_value": fact.display_value,
        "rounding_rule": fact.rounding_rule,
        "numeric_checks": list(fact.numeric_checks),
        "fiscal_year": fact.fiscal_year,
        "event_date": fact.event_date,
        "market_priority": fact.market_priority,
        "market_stage": fact.market_stage,
        "market_observation": fact.market_observation,
        "product_role": fact.product_role,
        "portfolio_stage": fact.portfolio_stage,
        "revenue_model_fact_id": fact.revenue_model_fact_id,
        "priority_signals": list(fact.priority_signals),
        "basis_fact_ids": list(fact.basis_fact_ids),
        "response_to_fact_id": fact.response_to_fact_id,
        "response_action": fact.response_action,
        "initial_signal": fact.initial_signal,
        "next_check_metric": fact.next_check_metric,
        "plan_status": fact.plan_status,
        "plan_timing": fact.plan_timing,
        "plan_condition": fact.plan_condition,
        "plan_expected_effect": fact.plan_expected_effect,
        "plan_execution_signal": fact.plan_execution_signal,
        "value_chain_stage": fact.value_chain_stage,
        "relationship_type": fact.relationship_type,
        "supports_causality": fact.supports_causality,
        "causal_subject": fact.causal_subject,
        "causal_mechanism": fact.causal_mechanism,
        "causal_outcome": fact.causal_outcome,
        "causal_evidence": fact.causal_evidence,
        "comparison_target": fact.comparison_target,
        "comparison_metric": fact.comparison_metric,
        "comparison_definition": fact.comparison_definition,
        "comparison_basis": fact.comparison_basis,
        "comparison_period": fact.comparison_period,
        "comparison_scope": fact.comparison_scope,
        "comparison_judgment": fact.comparison_judgment,
        "comparator_source_id": fact.comparator_source_id,
        "comparator_state_evidence": fact.comparator_state_evidence,
        "comparator_evidence_support_terms": list(
            fact.comparator_evidence_support_terms
        ),
        "comparison_conditions": dict(sorted(fact.comparison_conditions.items())),
        "limitations": fact.limitations,
        "limitation": fact.limitation,
    }
    for name in _OPTIONAL_STRUCTURED_FIELDS:
        value = getattr(fact, name, "")
        if value not in ("", None):
            payload[name] = value
    return _binding_digest(payload)
