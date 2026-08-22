"""P01~P10 사람 품질판정을 별도 JSON에 기록·집계한다."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence


APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from src.features.pilot_evaluation.quality_store import (  # noqa: E402
    PilotQualityStore,
    QUALITY_JUDGMENTS,
    QualityStoreError,
    aggregate_as_dict,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "P01~P10 사람 품질판정을 checkpoint 옆의 별도 JSON에 저장합니다. "
            "이 도구는 런타임 출고에 영향을 주지 않습니다."
        )
    )
    parser.add_argument(
        "--checkpoint",
        required=True,
        type=Path,
        help=(
            "기존 canonical-pilot25-checkpoint.json. 품질판정은 이 파일을 "
            "수정하지 않고 같은 폴더의 canonical-pilot25-quality.json에 저장"
        ),
    )
    parser.add_argument(
        "--storage-db",
        required=True,
        type=Path,
        help="checkpoint와 봉인된 동일 평가 SQLite storage.db",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    record = subparsers.add_parser("record", help="사람이 검수한 한 건 기록")
    record.add_argument("--case-id", required=True)
    record.add_argument(
        "--user-judgment",
        required=True,
        choices=tuple(sorted(QUALITY_JUDGMENTS)),
    )
    record.add_argument(
        "--wrong-legal-entity-released",
        required=True,
        choices=("yes", "no"),
        help="잘못된 법인이 출고됐는지 사람이 명시적으로 yes/no 판정",
    )
    record.add_argument(
        "--partial-report-released",
        required=True,
        choices=("yes", "no"),
        help="부분 보고서가 출고됐는지 사람이 명시적으로 yes/no 판정",
    )
    record.add_argument(
        "--major-fact-citation-numeric-error-auto-passed",
        required=True,
        choices=("yes", "no"),
        help="중대 사실·인용·수치 오류가 자동통과했는지 명시적으로 yes/no 판정",
    )
    record.add_argument(
        "--replace",
        action="store_true",
        help="이미 기록된 동일 case를 사람이 명시적으로 정정",
    )
    subparsers.add_parser("summary", help="10건 완성 여부와 고정 합격선 집계")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        store = PilotQualityStore(args.checkpoint, args.storage_db)
        if args.command == "record":
            row = store.record(
                case_id=args.case_id,
                user_judgment=args.user_judgment,
                wrong_legal_entity_released=(
                    args.wrong_legal_entity_released == "yes"
                ),
                partial_report_released=args.partial_report_released == "yes",
                major_fact_citation_numeric_error_auto_passed=(
                    args.major_fact_citation_numeric_error_auto_passed == "yes"
                ),
                replace=args.replace,
            )
            print(
                json.dumps(
                    {
                        "기록됨": row["case_id"],
                        "판정일치": row["judgments_agree"],
                        "저장파일": str(store.path),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
            return 0

        aggregate = store.aggregate()
        print(
            json.dumps(
                aggregate_as_dict(aggregate),
                ensure_ascii=False,
                sort_keys=True,
                indent=2,
            )
        )
        return 0 if aggregate.ready else 3
    except (OSError, QualityStoreError, ValueError) as exc:
        print(f"안전 중단: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
