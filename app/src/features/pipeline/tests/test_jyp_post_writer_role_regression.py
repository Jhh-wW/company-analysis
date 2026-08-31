"""JYP형 최소 선택 뒤 Writer·검수 역할 누락의 무과금 회귀검사."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from src.core import deployment_identity
from src.core.provider_gateway import attempt_context
from src.core.provider_gateway.attempt_context import ProviderAttemptCallbacks
from src.core.provider_gateway.types import ProviderObservation
from src.features.budget import provider_budget
from src.features.pipeline import real
from src.features.pipeline.port import Outcome
from src.features.pipeline.tests.test_real_cache import CORP_ID, FakeEngine, _run
from src.shared import engine_build_identity as build_identity_contract
from src.shared.final_gate_diagnostics import (
    FINAL_GATE_REASON_MISSING_REVENUE,
)
from src.shared.span_selection_diagnostics import SELECTION_REASON_KEPT


_DROPPED_REQUIRED_ROLE = "revenue_model"


class _AttemptRecorder:
    """웹 원장 대신 가짜 호출의 attempt 생명주기를 빠짐없이 기록한다."""

    def __init__(self) -> None:
        self.events: list[tuple[str, int]] = []
        self.observations: list[ProviderObservation] = []
        self._next_token = 0

    def callbacks(self) -> ProviderAttemptCallbacks:
        def begin(_provider: str, _operation: str, _reserved_krw: float) -> int:
            self._next_token += 1
            token = self._next_token
            self.events.append(("begin", token))
            return token

        def heartbeat(token: int) -> None:
            self.events.append(("heartbeat", token))

        def mark_dispatch_intent(token: int) -> None:
            self.events.append(("dispatch", token))

        def record(token: int, observation: ProviderObservation) -> None:
            self.events.append(("observation", token))
            self.observations.append(observation)

        return ProviderAttemptCallbacks(
            begin,
            heartbeat,
            mark_dispatch_intent,
            record,
        )


@pytest.fixture
def jyp_attempt_recorder() -> _AttemptRecorder:
    return _AttemptRecorder()


@pytest.fixture
def jyp_free_pipeline(
    monkeypatch: pytest.MonkeyPatch,
    jyp_attempt_recorder: _AttemptRecorder,
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
    for name in deployment_identity.COMMIT_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RENDER_GIT_COMMIT", "a" * 40)
    build_identity_contract.freeze_process_engine_build_identity()
    real.engine_mode.freeze_process_engine_mode(real.engine_mode.EngineMode.V1)
    # 이 시험은 ``test_real_cache``의 실행 함수만 재사용한다. 그 파일의
    # autouse fixture는 다른 시험 모듈까지 따라오지 않으므로, 직접 실행하는
    # 유료 경계도 운영 worker와 똑같이 budget과 영속 attempt 문맥을 함께 연다.
    with provider_budget.activate(100_000.0), attempt_context.activate(
        jyp_attempt_recorder.callbacks()
    ):
        yield engine


def test_선택최소관문_통과뒤_Writer검수에서_수익역할이_빠지면_보충하거나_코드로_멈춘다(
    jyp_free_pipeline: FakeEngine,
    jyp_attempt_recorder: _AttemptRecorder,
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
    assert (
        len(jyp_attempt_recorder.observations)
        == jyp_free_pipeline.client.messages.calls
    )
    assert jyp_attempt_recorder.events == [
        (event, token)
        for token in range(1, len(jyp_attempt_recorder.observations) + 1)
        for event in ("begin", "heartbeat", "dispatch", "observation")
    ]

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
