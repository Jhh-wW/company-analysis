"""실행 실패 진단이 닫힌 코드 밖의 민감한 값을 저장하지 않는지 검사한다."""

from __future__ import annotations

import pytest

from src.shared import runtime_failure_constants as failure_constants
from src.shared import runtime_failure_diagnostic as runtime_failure


def test_실패단계는_예외메시지와_url을_저장하지_않는다():
    secret = "https://private.example/path?token=top-secret 원문"
    step = runtime_failure.failure_step(
        phase=failure_constants.PHASE_COORDINATION,
        error=RuntimeError(secret),
        reason_code=failure_constants.REASON_GENERATION_COORDINATION_FAILED,
    )

    assert step == {
        "step": "runtime_failure",
        "phase": "coordination",
        "state": "failed",
        "role": "worker",
        "exception_class": "RuntimeError",
        "reason_code": "generation_coordination_failed",
    }
    assert secret not in repr(step)


def test_동적_예외클래스_이름도_ascii식별자가_아니면_미상으로_접는다():
    unsafe_type = type("https://secret.example/token", (Exception,), {})
    step = runtime_failure.failure_step(
        phase=failure_constants.PHASE_AI_CALL,
        error=unsafe_type(),
        reason_code=failure_constants.REASON_PROVIDER_CALL_FAILED,
    )

    assert step["exception_class"] == failure_constants.UNKNOWN_EXCEPTION_CLASS
    assert "secret.example" not in repr(step)


@pytest.mark.parametrize(
    "phase, reason_code",
    [
        ("임의 phase https://secret.example", "provider_call_failed"),
        ("ai_call", "임의 reason https://secret.example"),
    ],
)
def test_닫힌목록_밖의_phase와_사유는_저장하지_않는다(phase, reason_code):
    with pytest.raises(ValueError):
        runtime_failure.failure_step(
            phase=phase,
            error=RuntimeError("원문"),
            reason_code=reason_code,
        )


def test_재전파된_같은실패는_가장_안쪽_경계_한건만_남긴다():
    steps: list[dict] = []
    assert runtime_failure.append_failure_once(
        steps,
        phase=failure_constants.PHASE_AI_CALL,
        error=TimeoutError("provider 원문"),
        reason_code=failure_constants.REASON_PROVIDER_CALL_FAILED,
    )
    assert not runtime_failure.append_failure_once(
        steps,
        phase=failure_constants.PHASE_ASSEMBLY,
        error=RuntimeError("바깥 원문"),
        reason_code=failure_constants.REASON_REPORT_ASSEMBLY_FAILED,
    )

    assert len(steps) == 1
    assert steps[0]["phase"] == "ai_call"


def test_waiter는_원owner의_닫힌사유를_별도칸에_남긴다():
    step = runtime_failure.failure_step(
        phase=failure_constants.PHASE_COORDINATION,
        error=RuntimeError("예외 원문"),
        reason_code=failure_constants.REASON_GENERATION_OWNER_FAILED,
        role=failure_constants.ROLE_WAITER,
        owner_reason_code="official_evidence_insufficient",
    )

    assert step["role"] == "waiter"
    assert step["reason_code"] == "generation_owner_failed"
    assert step["owner_reason_code"] == "official_evidence_insufficient"


def test_waiter의_owner사유도_닫힌목록_밖이면_저장하지_않는다():
    with pytest.raises(ValueError):
        runtime_failure.failure_step(
            phase=failure_constants.PHASE_COORDINATION,
            error=RuntimeError("예외 원문"),
            reason_code=failure_constants.REASON_GENERATION_OWNER_FAILED,
            role=failure_constants.ROLE_WAITER,
            owner_reason_code="https://secret.example/raw",
        )
