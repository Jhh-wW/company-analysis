"""전송 계약이 작성기 생산자와 일치하고 오염된 진단은 닫아서 버린다."""

from hashlib import sha256

import pytest

from src.shared.report_quality.review_diagnostics import observed_review_outcomes


def test_transport_contract_matches_the_actual_producer():
    from src.features.composer.research_table_status_scope_constants import RESEARCH_TABLE_STATE_UNSUPPORTED
    from src.features.composer.accounting_scope_constants import ACCOUNTING_SCOPE_UNBOUND
    from src.features.composer.direct_support_constants import DIRECT_SUPPORT_REASON_TEXTS
    from src.features.composer.constants import SECTION_IDS
    from src.features.composer.grounding_constants import (
        GROUNDING_INVALID, GROUNDING_MISSING, NUMERIC_KEY, RECOGNITION_KEY,
        TIME_KEY, TREND_KEY,
    )
    from src.shared.report_quality.review_diagnostic_constants import (
        REVIEW_ITEMS, REVIEW_REASONS, REVIEW_SECTION_IDS, REVIEW_SCOPE_ITEMS,
    )
    from src.features.composer.modality_constants import (
        MODALITY_PLAN_ASSERTED, MODALITY_POSSIBILITY_ASSERTED,
    )
    from src.features.composer.scope_constants import SCOPE_CONDITION_UNBOUND
    from src.features.composer.quantified_relation_constants import QUANTIFIED_DIVIDEND_UNBOUND
    from src.features.composer.prose_own_source_constants import (
        PROSE_OWN_SOURCE_REASON_CODES,
    )
    from src.features.composer.culture_constants import (
        CULTURE_ACCOUNTING_POLICY_MISPLACED, CULTURE_EVIDENCE_SCOPE_MISMATCH,
        CULTURE_FINANCIAL_RISK_SCOPE_MISPLACED,
        CULTURE_SECTION_EVIDENCE_OFFCONTRACT,
    )
    from src.features.composer.absence_claim_constants import (
        ABSENCE_CLAIM_UNSUPPORTED,
    )
    from src.features.composer.accounting_policy_constants import (
        ACCOUNTING_POLICY_BOILERPLATE,
    )
    from src.features.composer.challenge_constants import CHALLENGE_RESPONSE_MISSING
    from src.shared.report_evidence.challenge_eligibility_constants import (
        HISTORICAL_EVENT_ONLY, ADMINISTRATIVE_EVENT_ONLY, POSITIVE_RESPONSE_ONLY,
    )
    from src.features.composer.future_plan_constants import (
        FUTURE_REASON_CODES, FUTURE_SECTION_NO_FORWARD_STATEMENT,
    )
    from src.features.composer.plan_status_constants import (
        COMPLETED_EXECUTION_STATE_MISMATCH,
    )

    from src.features.composer.role_binding_constants import ROLE_BINDING_REASON_TEXTS
    from src.features.composer.executive_status_constants import (
        EXECUTIVE_STATUS_REASON_TEXTS,
    )
    from src.features.composer.combined_relation_constants import (
        COMBINED_RELATION_REASON_TEXTS,
    )
    from src.features.composer.stray_citation_marker_constants import (
        STRAY_CITATION_MARKER_REASON_CODES,
    )

    from src.features.composer.flow_review_constants import (
        FLOW_REVIEW_BINDING_INVALID, FLOW_REVIEW_BINDING_MISSING,
    )
    from src.features.composer.competitive_scope_constants import COMPETITIVE_SECTION_EVIDENCE_OFFCONTRACT
    assert set(REVIEW_SCOPE_ITEMS) == {
        RESEARCH_TABLE_STATE_UNSUPPORTED,
        ACCOUNTING_SCOPE_UNBOUND,
        "public_sentence_fact_unbound", "public_sentence_fact_duplicate",
        COMPETITIVE_SECTION_EVIDENCE_OFFCONTRACT,
        FLOW_REVIEW_BINDING_INVALID, FLOW_REVIEW_BINDING_MISSING,
        MODALITY_PLAN_ASSERTED, MODALITY_POSSIBILITY_ASSERTED,
        SCOPE_CONDITION_UNBOUND, CULTURE_EVIDENCE_SCOPE_MISMATCH,
        QUANTIFIED_DIVIDEND_UNBOUND,
        CULTURE_ACCOUNTING_POLICY_MISPLACED, *DIRECT_SUPPORT_REASON_TEXTS,
        *ROLE_BINDING_REASON_TEXTS,
        *FUTURE_REASON_CODES,
        # 6장 장 계약 — 근거 결속 사유가 아니라 «작성 범위» 사유다.
        FUTURE_SECTION_NO_FORWARD_STATEMENT,
        # 완료 실행 칸에 진행 상태를 배치한 생산자 사유도 같은 전송 계약에 속한다.
        COMPLETED_EXECUTION_STATE_MISMATCH,
        CHALLENGE_RESPONSE_MISSING,
        HISTORICAL_EVENT_ONLY, ADMINISTRATIVE_EVENT_ONLY, POSITIVE_RESPONSE_ONLY,
        CULTURE_FINANCIAL_RISK_SCOPE_MISPLACED,
        # 8장 «원문 절» 긍정 계약과 장 무관 부재 단언 가드가 더해졌다 —
        # 둘 다 새 사유 코드를 쓰므로 이 전송 계약에도 함께 등록한다.
        CULTURE_SECTION_EVIDENCE_OFFCONTRACT,
        ABSENCE_CLAIM_UNSUPPORTED,
        # 전 장 공통 회계정책 상용구 가드의 사유 코드(2026-09-22).
        ACCOUNTING_POLICY_BOILERPLATE,
        *PROSE_OWN_SOURCE_REASON_CODES,
        *EXECUTIVE_STATUS_REASON_TEXTS,
        # 수량 범위 결속(«결합» 유형) — 진단 우선 모드라도 전송 계약에는 항상 있어야
        # 한다. 나중에 quantified_dividend_recipient_unbound를 흡수해도 이 스프레드는
        # 그대로 둔다(§6 흡수 계획).
        *COMBINED_RELATION_REASON_TEXTS,
        # 인용 아닌 대괄호 숫자를 정리하며 «뺀» 축자 문장의 사유 코드.
        *STRAY_CITATION_MARKER_REASON_CODES,
    }
    # 생산자(grounding_requirements)가 내는 근거 배열 이름이 전부 들어 있어야
    # 그 요구로 탈락한 진단이 검증항목 검사에서 버려지지 않는다.
    assert set(REVIEW_ITEMS) == {
        NUMERIC_KEY, TIME_KEY, TREND_KEY, RECOGNITION_KEY, *REVIEW_SCOPE_ITEMS.values(),
    }
    assert set(REVIEW_REASONS) == {GROUNDING_INVALID, GROUNDING_MISSING, *REVIEW_SCOPE_ITEMS}
    assert REVIEW_SECTION_IDS == set(SECTION_IDS) | {"summary"}


def test_가능성_실제화_거절사유가_생산자에서_전송진단까지_보존된다():
    from src.features.composer.grounding import constrain_verdicts
    from src.features.composer.modality_constants import MODALITY_POSSIBILITY_ASSERTED
    from src.features.composer.verify import _append_grounding_diagnostic

    candidate = "가람기업은 부품 통합화/모듈화를 추진하고 있다."
    sources = {"공시": "부품 통합화/모듈화 가능"}
    verdicts, problems = constrain_verdicts(
        '{"판정":[{"번호":1,"결과":"참","검증근거":{}}]}', {1: "참"},
        {1: (candidate, sources)},
    )
    assert verdicts[1] != "참"
    assert problems[1] == MODALITY_POSSIBILITY_ASSERTED
    diagnostics = []
    _append_grounding_diagnostic(
        diagnostics, section_id="current_challenges", kind="본문",
        reason_code=problems[1], candidate_text=candidate, sources=sources,
    )
    transported = observed_review_outcomes(diagnostics)
    assert len(transported) == 1
    assert transported[0]["reason_code"] == MODALITY_POSSIBILITY_ASSERTED
    assert transported[0]["candidate_sha256"] == sha256(candidate.encode()).hexdigest()
    assert transported[0]["verification_items"] == ("계획·성과",)
    assert "candidate_text" not in transported[0]


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
