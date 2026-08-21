"""Preflight 25 canonical cases; paid execution is currently P01-P10 only."""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Sequence

import httpx


APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from src.features.pilot_evaluation.checkpoint import (  # noqa: E402
    CheckpointError,
    CheckpointStore,
)
from src.features.pilot_evaluation.manifest import (  # noqa: E402
    APPROVED_PAID_CASE_IDS,
    CANONICAL_PILOT_CASES,
    manifest_sha256,
)
from src.features.pilot_evaluation.runner import (  # noqa: E402
    CanonicalPilotRunner,
    PilotBatchBlocked,
    PilotRunnerError,
    canonical_loopback_origin,
)

def _validate_paid_scope(args: argparse.Namespace) -> None:
    """Keep the current user's paid approval narrower than the 25-case manifest."""

    if not args.execute:
        return
    if not args.case_id:
        raise PilotRunnerError(
            "유료 --execute에는 승인된 --case-id를 하나 이상 명시해야 합니다"
        )
    outside = sorted(set(args.case_id) - APPROVED_PAID_CASE_IDS)
    if outside:
        raise PilotRunnerError(
            "현재 유료 승인은 P01~P10뿐입니다. 승인 밖 case: " + ", ".join(outside)
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "현재 localhost 실시간 평가 웹 흐름으로 G3.5 후보를 점검합니다. "
            "유료 실행 승인은 P01~P10으로 제한됩니다. "
            "기본값은 provider POST가 없는 dry-run입니다."
        )
    )
    parser.add_argument(
        "--origin",
        required=True,
        help="예: http://127.0.0.1:8020 (숫자형 loopback과 명시적 port만 허용)",
    )
    parser.add_argument(
        "--storage-db",
        required=True,
        type=Path,
        help="실시간 평가 launcher가 이번 서버에 만든 storage.db",
    )
    parser.add_argument(
        "--checkpoint",
        type=Path,
        help="기본값: storage.db와 같은 폴더의 canonical-pilot25-checkpoint.json",
    )
    action = parser.add_mutually_exclusive_group()
    action.add_argument(
        "--execute",
        action="store_true",
        help="유료 provider 호출 가능. 생략하면 GET 기반 dry-run/preflight만 수행",
    )
    action.add_argument(
        "--recover-legal-name-mismatch",
        metavar="CASE_ID",
        help=(
            "봉인된 legal_name_mismatch 한 건을 DB 증거로 검증해 재시도 준비 상태로만 전이"
        ),
    )
    action.add_argument(
        "--recover-prior-day-restart",
        metavar="CASE_ID",
        help=(
            "전날 P01 미확정 비용 표식을 보존한 채 새 로컬 서버 digest로 "
            "봉인만 옮김(GET/local-only)"
        ),
    )
    action.add_argument(
        "--recover-identity-ref-unverified",
        metavar="CASE_ID",
        help=(
            "P02의 0원 candidate_ref_not_observed 종료를 봉인 검증 뒤 "
            "새 서버 결속의 재시도 준비 상태로만 전이"
        ),
    )
    action.add_argument(
        "--recover-service-maintenance-pre-provider",
        metavar="CASE_ID",
        help=(
            "P02의 점검 429가 DART/AI 호출 전임을 DB 증거로 검증해 "
            "새 서버 결속의 재시도 준비 상태로만 전이"
        ),
    )
    action.add_argument(
        "--retire-unproven-p02-retry-and-rebind",
        metavar="CASE_ID",
        help="증거 없는 과거 P02 재시도를 원래 미관측 종료로 은퇴하고 새 서버에 재결속(GET/local-only)",
    )
    parser.add_argument(
        "--case-id",
        action="append",
        default=[],
        help="P01 형식. 반복 지정 가능; 생략하면 manifest 순서 전체",
    )
    parser.add_argument(
        "--max-cases",
        type=int,
        help="이번 invocation에서 새로 시작할 최대 case 수",
    )
    parser.add_argument("--poll-interval-sec", type=float, default=2.0)
    parser.add_argument("--poll-timeout-sec", type=float, default=35 * 60)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        origin = canonical_loopback_origin(args.origin)
        storage_db = args.storage_db.resolve()
        checkpoint_path = (
            args.checkpoint.resolve()
            if args.checkpoint is not None
            else storage_db.parent / "canonical-pilot25-checkpoint.json"
        )
        if args.max_cases is not None and args.max_cases < 0:
            raise PilotRunnerError("--max-cases는 0 이상이어야 합니다")
        _validate_paid_scope(args)
        recovery_case = (
            args.recover_legal_name_mismatch
            or args.recover_prior_day_restart
            or args.recover_identity_ref_unverified
            or args.recover_service_maintenance_pre_provider
            or args.retire_unproven_p02_retry_and_rebind
        )
        if recovery_case and (args.case_id or args.max_cases is not None):
            raise PilotRunnerError(
                "복구 명령에는 --case-id 또는 --max-cases를 함께 쓸 수 없습니다"
            )
        store = CheckpointStore(checkpoint_path)
        timeout = httpx.Timeout(30 * 60, connect=5.0)
        with httpx.Client(
            base_url=origin,
            headers={
                "Origin": origin,
                "Accept": "text/html,application/json;q=0.9",
                "User-Agent": "company-analysis-canonical-pilot25/1",
            },
            follow_redirects=False,
            timeout=timeout,
            trust_env=False,
        ) as client:
            runner = CanonicalPilotRunner(
                origin=origin,
                storage_db_path=storage_db,
                checkpoint=store,
                client=client,
                poll_interval_sec=args.poll_interval_sec,
                poll_timeout_sec=args.poll_timeout_sec,
            )
            if args.recover_legal_name_mismatch:
                summary = runner.recover_legal_name_mismatch(
                    args.recover_legal_name_mismatch
                )
            elif args.recover_prior_day_restart:
                summary = runner.recover_prior_day_restart(
                    args.recover_prior_day_restart
                )
            elif args.recover_identity_ref_unverified:
                summary = runner.recover_identity_ref_unverified(
                    args.recover_identity_ref_unverified
                )
            elif args.recover_service_maintenance_pre_provider:
                summary = runner.recover_service_maintenance_pre_provider(
                    args.recover_service_maintenance_pre_provider
                )
            elif args.retire_unproven_p02_retry_and_rebind:
                summary = runner.retire_unproven_p02_retry_and_rebind(
                    args.retire_unproven_p02_retry_and_rebind
                )
            else:
                summary = runner.operate(
                    execute=bool(args.execute),
                    case_ids=tuple(args.case_id),
                    max_cases=args.max_cases,
                )
    except (CheckpointError, PilotBatchBlocked, PilotRunnerError) as exc:
        print(f"안전 중단: {exc}", file=sys.stderr)
        return 2
    except (OSError, sqlite3.Error) as exc:
        print(
            "안전 중단: 로컬 체크포인트 또는 SQLite를 사용할 수 없습니다 "
            f"({type(exc).__name__})",
            file=sys.stderr,
        )
        return 2

    if args.recover_legal_name_mismatch:
        mode = "법인명 오탐 복구 준비"
    elif args.recover_prior_day_restart:
        mode = "전날 미확정 P01 재시작 복구"
    elif args.recover_identity_ref_unverified:
        mode = "P02 DART 번호 미관측 재시도 준비"
    elif args.recover_service_maintenance_pre_provider:
        mode = "P02 점검 전단계 재시도 준비"
    elif args.retire_unproven_p02_retry_and_rebind:
        mode = "P02 증거 없는 재시도 은퇴·재결속"
    else:
        mode = "실행" if args.execute else "dry-run"
    print(
        f"G3.5 후보25/유료10 {mode} 완료: "
        f"manifest={manifest_sha256(CANONICAL_PILOT_CASES)[:12]} "
        f"실행={len(summary.executed_case_ids)} 누적완료={len(summary.completed_case_ids)} "
        f"누적종료={len(summary.terminal_case_ids)}"
    )
    if summary.executed_case_ids:
        print("이번 case: " + ", ".join(summary.executed_case_ids))
    if summary.next_recommended_at:
        print(
            "10분당 5건 기준으로 새 호출을 멈췄습니다. 권장 재개 시각(UTC): "
            + summary.next_recommended_at
        )
    print(f"체크포인트: {checkpoint_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
