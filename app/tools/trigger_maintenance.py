"""Render cron에서 웹 서비스의 인증된 정기 작업을 한 번 요청한다."""

from __future__ import annotations

import sys
from typing import Final, Sequence

from tools import internal_trigger


ENV_TRIGGER_URL: Final[str] = "MAINTENANCE_TRIGGER_URL"
ENV_TRIGGER_SECRET: Final[str] = "MAINTENANCE_TRIGGER_SECRET"
ENDPOINT_PATH: Final[str] = "/internal/maintenance/run"
OPERATION_HEADER: Final[str] = "X-Maintenance-Operation"
OPERATIONS: Final[frozenset[str]] = frozenset({"weekly", "cleanup"})
TriggerError = internal_trigger.TriggerError


def _config_from_env() -> tuple[str, str]:
    return internal_trigger.load_exact_https_config(
        url_env=ENV_TRIGGER_URL,
        secret_env=ENV_TRIGGER_SECRET,
        endpoint_path=ENDPOINT_PATH,
    )


def trigger_once(
    url: str,
    secret: str,
    operation: str,
    *,
    opener_factory: internal_trigger.OpenerFactory = internal_trigger.build_opener,
) -> dict:
    if operation not in OPERATIONS:
        raise TriggerError("정기 작업 종류가 올바르지 않습니다")
    payload = internal_trigger.post_json(
        url=url,
        secret=secret,
        service_name="정기 작업",
        user_agent="company-analysis-maintenance-cron/1",
        headers={
            OPERATION_HEADER: operation,
        },
        opener_factory=opener_factory,
    )
    if (
        payload.get("status") not in {"ok", "already_done"}
        or payload.get("operation") != operation
    ):
        raise TriggerError("정기 작업 서버가 완료 상태를 반환하지 않았습니다")
    return payload


def main(argv: Sequence[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if len(argv) != 1 or argv[0] not in OPERATIONS:
        print("사용법: trigger_maintenance.py weekly|cleanup", file=sys.stderr)
        return 2
    operation = argv[0]
    try:
        url, secret = _config_from_env()
        payload = trigger_once(url, secret, operation)
    except TriggerError as exc:
        print(f"정기 작업 실패: {exc}", file=sys.stderr)
        return 1
    print(
        "정기 작업 완료: "
        f"operation={operation} status={payload['status']} "
        f"period={payload.get('period_key', '')}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
