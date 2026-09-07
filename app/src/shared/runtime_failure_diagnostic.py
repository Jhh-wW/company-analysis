"""원문·URL·예외 메시지 없이 실행 실패 경계만 운반하는 공유 계약."""

from __future__ import annotations

import re
from typing import Any, Mapping

from src.shared import runtime_failure_constants as constants
from src.shared.final_gate_diagnostics import SAFE_FINAL_GATE_REASONS


_SAFE_EXCEPTION_CLASS = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _exception_class(error: BaseException) -> str:
    """예외의 클래스 이름만 닫힌 ASCII 라벨로 만든다.

    동적으로 만든 클래스는 ``__name__``에도 URL·토큰을 넣을 수 있다. 따라서
    이름이 평범한 식별자가 아니면 일부를 다듬어 남기지 않고 미상으로 접는다.
    예외 문자열·인자·cause·traceback은 이 함수에서 읽지 않는다.
    """

    name = type(error).__name__
    if (
        not isinstance(name, str)
        or len(name) > constants.EXCEPTION_CLASS_MAX_CHARS
        or _SAFE_EXCEPTION_CLASS.fullmatch(name) is None
    ):
        return constants.UNKNOWN_EXCEPTION_CLASS
    return name


def failure_step(
    *,
    phase: str,
    error: BaseException,
    reason_code: str,
    role: str = constants.ROLE_WORKER,
    owner_reason_code: str = "",
) -> dict[str, str]:
    """허용된 실패 위치·사유와 예외 클래스만 단계 한 건으로 만든다."""

    if phase not in constants.ALLOWED_PHASES:
        raise ValueError("허용되지 않은 실행 실패 phase입니다")
    if reason_code not in constants.ALLOWED_REASON_CODES:
        raise ValueError("허용되지 않은 실행 실패 사유 코드입니다")
    if role not in constants.ALLOWED_ROLES:
        raise ValueError("허용되지 않은 실행 실패 역할입니다")
    allowed_owner_reasons = (
        constants.ALLOWED_REASON_CODES
        | SAFE_FINAL_GATE_REASONS
        | {
            "generation_failed",
            "link_revoked",
            "link_expired",
            "link_state_unknown",
        }
    )
    if owner_reason_code and owner_reason_code not in allowed_owner_reasons:
        raise ValueError("허용되지 않은 원 owner 실패 사유 코드입니다")
    step = {
        "step": constants.RUNTIME_FAILURE_STEP,
        "phase": phase,
        "state": constants.STATE_FAILED,
        "role": role,
        "exception_class": _exception_class(error),
        "reason_code": reason_code,
    }
    if owner_reason_code:
        step["owner_reason_code"] = owner_reason_code
    return step


def has_failure(steps: list[dict[str, Any]]) -> bool:
    """이미 더 안쪽 경계가 실패를 기록했는지 본다."""

    return any(
        isinstance(item, Mapping)
        and item.get("step") == constants.RUNTIME_FAILURE_STEP
        for item in steps
    )


def append_failure_once(
    steps: list[dict[str, Any]],
    *,
    phase: str,
    error: BaseException,
    reason_code: str,
    role: str = constants.ROLE_WORKER,
    owner_reason_code: str = "",
) -> bool:
    """한 예외 전파 경로에서 가장 안쪽 실패 한 건만 보존한다."""

    if has_failure(steps):
        return False
    try:
        steps.append(
            failure_step(
                phase=phase,
                error=error,
                reason_code=reason_code,
                role=role,
                owner_reason_code=owner_reason_code,
            )
        )
    except Exception:
        return False
    return True


def append_job_failure(
    steps: list[dict[str, Any]],
    *,
    phase: str,
    error: BaseException,
    reason_code: str,
    role: str = constants.ROLE_WORKER,
    owner_reason_code: str = "",
) -> None:
    """파이프라인 뒤의 서로 다른 후처리 실패를 best-effort로 추가한다."""

    try:
        steps.append(
            failure_step(
                phase=phase,
                error=error,
                reason_code=reason_code,
                role=role,
                owner_reason_code=owner_reason_code,
            )
        )
    except Exception:
        return
