"""JYP형 최소 선택 뒤 Writer·검수 역할 누락의 무과금 회귀검사."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from src.features.budget import provider_budget
from src.features.pipeline import real
from src.features.pipeline.port import Outcome
from src.features.pipeline.tests.test_real_cache import CORP_ID, FakeEngine, _run
from src.shared.final_gate_diagnostics import (
    FINAL_GATE_REASON_MISSING_REVENUE,
)
from src.shared.span_selection_diagnostics import SELECTION_REASON_KEPT


_DROPPED_REQUIRED_ROLE = "revenue_model"


@pytest.fixture
def jyp_free_pipeline(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[FakeEngine]:
    """기존 canonical 가짜 엔진을 재사용해 네트워크·유료 AI를 모두 막는다."""

    engine = FakeEngine()
    monkeypatch.setattr(real, "_engine", lambda: engine)
    monkeypatch.setattr(
        real,
        "_company_catalog",
        lambda: (
            (CORP_ID, "가나다전자", "", "000001", "20260819"),
            ("00999999", "베타전자", "", "999999", "20260819"),
        ),
    )
    with provider_budget.activate(100_000.0):
        yield engine


def test_선택최소관문_통과뒤_Writer검수에서_수익역할이_빠지면_보충하거나_코드로_멈춘다(
    jyp_free_pipeline: FakeEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Writer 뒤 결손을 PARTIAL 정상 출고로 조용히 바꾸지 않는다.

    첫 Writer·Reviewer 결과에서 선택 당시 존재했던 ``revenue_model`` 한 건만
    제거한다. 구현은 같은 검증 원문으로 역할을 보충해 출고하거나, 닫힌 최종
    게이트 코드로 중단할 수 있다. 두 경우 외에는 회귀다.
    """

    original_writer = real.write_and_verify_sections
    writer_calls = 0
    dropped_claims = 0

    def drop_one_required_role_after_review(
        *args: Any,
        **kwargs: Any,
    ):
        nonlocal writer_calls, dropped_claims
        writer_calls += 1
        sections, claims = original_writer(*args, **kwargs)
        if writer_calls != 1:
            return sections, claims

        removed = [
            claim
            for claim in claims
            if claim.claim_type == _DROPPED_REQUIRED_ROLE
        ]
        assert len(removed) == 1, (
            "가짜 JYP형 입력에는 수익 구조 역할이 정확히 한 건이어야 합니다"
        )
        dropped_claims = len(removed)
        return sections, [
            claim
            for claim in claims
            if claim.claim_type != _DROPPED_REQUIRED_ROLE
        ]

    monkeypatch.setattr(
        real,
        "write_and_verify_sections",
        drop_one_required_role_after_review,
    )

    result = _run()

    assert result.span_selection_result_reason == SELECTION_REASON_KEPT
    assert dropped_claims == 1
    assert (
        jyp_free_pipeline.client.messages.calls
        == jyp_free_pipeline.generate_ai_calls
    )

    if result.outcome is Outcome.REPORT:
        assert result.report is not None
        published_roles = {
            fact.claim_type for fact in result.report.fact_records
        }
        assert _DROPPED_REQUIRED_ROLE in published_roles, (
            "Writer·검수 뒤 빠진 수익 구조 역할을 보충하지 않은 채 출고했습니다"
        )
        return

    assert result.outcome is Outcome.GATE_STOPPED
    assert result.report is None
    assert result.final_gate_reason == FINAL_GATE_REASON_MISSING_REVENUE
