"""``python -m src.features.release_acceptance`` 진입점."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.features.release_acceptance.constants import (
    DEFAULT_STARTUP_TIMEOUT_SEC,
    DEFAULT_WORKFLOW_TIMEOUT_SEC,
)
from src.features.release_acceptance.logic import (
    CheckStatus,
    RunConfig,
    render_korean_summary,
    run_acceptance,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "외부 provider 없이 격리된 로컬 demo 서버를 두 번 기동해 "
            "릴리스 수락 조건을 검사합니다."
        )
    )
    parser.add_argument(
        "--python",
        default=sys.executable,
        help="uvicorn 자식 프로세스에 사용할 Python 실행 파일",
    )
    parser.add_argument(
        "--startup-timeout",
        type=float,
        default=DEFAULT_STARTUP_TIMEOUT_SEC,
        help="서버 시작 제한시간(초)",
    )
    parser.add_argument(
        "--workflow-timeout",
        type=float,
        default=DEFAULT_WORKFLOW_TIMEOUT_SEC,
        help="데모 완료·PDF 생성 제한시간(초)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    # Codex/CI의 pipe와 로컬 실행기의 UTF-8 로그 계약을 같게 둔다.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8")
    args = _parser().parse_args(argv)
    app_root = Path(__file__).resolve().parents[3]
    report = run_acceptance(
        RunConfig(
            app_root=app_root,
            python_executable=args.python,
            startup_timeout_sec=max(1.0, args.startup_timeout),
            workflow_timeout_sec=max(1.0, args.workflow_timeout),
        )
    )
    print("=== JSON ===")
    print(report.to_json())
    print("=== 한국어 요약 ===")
    print(render_korean_summary(report))
    return {
        CheckStatus.PASS: 0,
        CheckStatus.FAIL: 1,
        CheckStatus.BLOCKED: 2,
    }[report.overall_status]


if __name__ == "__main__":
    raise SystemExit(main())
