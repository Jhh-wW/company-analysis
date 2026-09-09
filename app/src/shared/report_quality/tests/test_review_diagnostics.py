"""전송 계약이 작성기 생산자와 일치하고 오염된 진단은 닫아서 버린다."""

from hashlib import sha256

import pytest

from src.shared.report_quality.review_diagnostics import observed_review_outcomes


def test_transport_contract_matches_the_actual_producer():
    from src.features.composer.direct_support_constants import DIRECT_SUPPORT_REASON_TEXTS
    from src.features.composer.constants import SECTION_IDS
    from src.features.composer.grounding_constants import (
        GROUNDING_INVALID, GROUNDING_MISSING, NUMERIC_KEY, TIME_KEY, TREND_KEY,
    )
    from src.shared.report_quality.review_diagnostic_constants import (
        REVIEW_ITEMS, REVIEW_REASONS, REVIEW_SECTION_IDS, REVIEW_SCOPE_ITEMS,
    )
    from src.features.composer.modality_constants import MODALITY_PLAN_ASSERTED
    from src.features.composer.scope_constants import SCOPE_CONDITION_UNBOUND
    from src.features.composer.prose_own_source_constants import (
        PROSE_OWN_SOURCE_REASON_CODES,
    )
    from src.features.composer.culture_constants import (
        CULTURE_ACCOUNTING_POLICY_MISPLACED, CULTURE_EVIDENCE_SCOPE_MISMATCH,
    )
    from src.features.composer.future_plan_constants import FUTURE_REASON_CODES

    from src.features.composer.role_binding_constants import ROLE_BINDING_REASON_TEXTS

    assert set(REVIEW_SCOPE_ITEMS) == {
        MODALITY_PLAN_ASSERTED, SCOPE_CONDITION_UNBOUND, CULTURE_EVIDENCE_SCOPE_MISMATCH,
        CULTURE_ACCOUNTING_POLICY_MISPLACED, *DIRECT_SUPPORT_REASON_TEXTS,
        *ROLE_BINDING_REASON_TEXTS,
        *FUTURE_REASON_CODES,
        *PROSE_OWN_SOURCE_REASON_CODES,
    }
    assert set(REVIEW_ITEMS) == {NUMERIC_KEY, TIME_KEY, TREND_KEY, *REVIEW_SCOPE_ITEMS.values()}
    assert set(REVIEW_REASONS) == {GROUNDING_INVALID, GROUNDING_MISSING, *REVIEW_SCOPE_ITEMS}
    assert REVIEW_SECTION_IDS == set(SECTION_IDS) | {"summary"}


@pytest.mark.parametrize("field", (
    "section_id", "kind", "reason_code", "candidate_sha256", "verification_items",
))
@pytest.mark.parametrize("pollution", ({}, [[]], None, 1))
def test_polluted_event_fields_are_discarded(field, pollution):
    event = {
        "section_id": "identity", "kind": "본문",
        "reason_code": "semantic_grounding_invalid",
        "candidate_sha256": sha256("관측".encode()).hexdigest(),
        "verification_items": (),
    }
    event[field] = pollution
    assert observed_review_outcomes([event]) == ()
