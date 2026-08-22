"""socket/DNS guard를 먼저 설치하는 release_acceptance 전용 uvicorn 진입점."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from src.features.release_acceptance.constants import (
    EGRESS_AUDIT_ENV_NAME,
    LOOPBACK_HOST,
)
from src.features.release_acceptance.egress_guard import (
    EgressGuardError,
    install_child_egress_guard,
)


def main() -> int:
    try:
        audit_value = os.environ.get(EGRESS_AUDIT_ENV_NAME, "").strip()
        data_root_value = os.environ.get("APP_DATA_ROOT", "").strip()
        port_value = os.environ.get("PORT", "").strip()
        if not audit_value or not data_root_value or not port_value:
            raise EgressGuardError("egress guard 필수 자식 설정이 없습니다")
        port = int(port_value)
        if port < 1 or port > 65535:
            raise EgressGuardError("자식 서버 포트가 올바르지 않습니다")
        guard = install_child_egress_guard(
            audit_path=Path(audit_value),
            data_root=Path(data_root_value),
        )
        guard.run_self_test()
        import uvicorn

        uvicorn.run(
            "src.web.main:app",
            host=LOOPBACK_HOST,
            port=port,
            workers=1,
            access_log=False,
            log_level="warning",
        )
        return 0
    except (EgressGuardError, OSError, ValueError):
        print("release_acceptance 자식 egress guard를 준비하지 못했습니다", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
