"""SHADOW 품질 관측값(`quality_observation`) 집계를 표·JSON으로 보여주는 읽기 전용 CLI.

DB를 절대 수정하지 않는다 — `db.connect_readonly_existing()`으로만 연다(SQLite
``PRAGMA query_only=ON``까지 걸려 실수로 쓰기를 시도해도 거부된다). 새 AI·네트워크
호출은 하지 않는다.

사용법::

    python -m tools.quality_observations --db data/storage.db
    python -m tools.quality_observations --db data/storage.db --since 2026-08-01 --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

from src.features.admin_dashboard.quality_observations import (  # noqa: E402
    CollectedQualityObservations,
    collect_quality_observations,
)
from src.features.storage import db  # noqa: E402
from src.shared.report_quality.observation_summary import (  # noqa: E402
    QualityObservationSummary,
    summarize_quality_observations,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m tools.quality_observations",
        description=(
            "저장된 보고서 payload 안의 SHADOW 품질 관측값(quality_observation)을 "
            "읽기 전용으로 집계합니다. DB를 수정하지 않고 새 AI·네트워크 호출도 "
            "하지 않습니다."
        ),
    )
    parser.add_argument(
        "--db", required=True, type=Path, help="읽을 SQLite storage.db 경로(필수)"
    )
    parser.add_argument("--since", default=None, help="YYYY-MM-DD 이후(포함)만 집계")
    parser.add_argument("--until", default=None, help="YYYY-MM-DD 이전(포함)만 집계")
    parser.add_argument(
        "--top-companies",
        type=int,
        default=20,
        help="거절 후보 상위 회사 목록에 남길 회사 수(기본 20)",
    )
    parser.add_argument("--json", action="store_true", help="표 대신 JSON으로 출력")
    return parser


def _summary_to_json_dict(
    collected: CollectedQualityObservations, summary: QualityObservationSummary
) -> dict[str, object]:
    return {
        "total_reports_scanned": collected.total_reports_scanned,
        "without_observation_count": collected.without_observation_count,
        "total_observations": summary.total_count,
        "release_blocked_count": summary.release_blocked_count,
        "release_blocked_ratio": summary.release_blocked_ratio,
        "safety_decision_counts": [list(item) for item in summary.safety_decision_counts],
        "quality_grade_counts": [list(item) for item in summary.quality_grade_counts],
        "quality_problem_code_counts": [
            list(item) for item in summary.quality_problem_code_counts
        ],
        "release_mode_breakdown": [
            {
                "release_mode": item.release_mode,
                "total_count": item.total_count,
                "release_blocked_count": item.release_blocked_count,
                "release_blocked_ratio": item.release_blocked_ratio,
            }
            for item in summary.release_mode_breakdown
        ],
        "top_companies": [
            {
                "company_id": item.company_id,
                "total_count": item.total_count,
                "release_blocked_count": item.release_blocked_count,
            }
            for item in summary.top_companies
        ],
    }


def _print_table(
    collected: CollectedQualityObservations, summary: QualityObservationSummary
) -> None:
    print("SHADOW 품질 관측값 집계 (읽기 전용 — 이 도구는 DB를 바꾸지 않습니다)")
    print(f"- 저장된 보고서 스캔: {collected.total_reports_scanned}건")
    print(f"- quality_observation 없는 보고서(옛 저장분 포함): {collected.without_observation_count}건")
    print(f"- quality_observation 있는 보고서(아래 집계 대상): {summary.total_count}건")
    if summary.total_count == 0:
        print(
            "  → 0건입니다. 지금 운영 SHADOW 저장 경로는 quality_observation을 "
            "저장 직전에 비웁니다(composer/pipeline.py의 replace() 호출, "
            "release_mode가 SHADOW면 quality_observation=None). 이 CLI의 결함이 "
            "아니라 SHADOW 저장 경로 자체가 관측값을 담지 않기 때문입니다."
        )
        return
    ratio_text = f" (비율 {summary.release_blocked_ratio})" if summary.release_blocked_ratio else ""
    print(f"- 거절 후보(release_allowed=False): {summary.release_blocked_count}건{ratio_text}")
    print("- safety_decision 분포:")
    for key, count in summary.safety_decision_counts:
        print(f"    {key}: {count}건")
    print("- quality_grade 분포:")
    for key, count in summary.quality_grade_counts:
        print(f"    {key}: {count}건")
    if summary.quality_problem_code_counts:
        print("- 부족 사유 코드별 건수(한 건에 여러 코드가 있을 수 있어 합이 총 건수보다 클 수 있음):")
        for key, count in summary.quality_problem_code_counts:
            print(f"    {key}: {count}건")
    print("- release_mode별 분해:")
    for item in summary.release_mode_breakdown:
        label = item.release_mode or "(빈 값 — 현재 SHADOW 표기)"
        item_ratio = f" (비율 {item.release_blocked_ratio})" if item.release_blocked_ratio else ""
        print(
            f"    {label}: 총 {item.total_count}건 · 거절후보 "
            f"{item.release_blocked_count}건{item_ratio}"
        )
    if summary.top_companies:
        print("- 거절 후보 상위 회사(거절건수 내림차순):")
        for item in summary.top_companies:
            label = item.company_id or "(company_id 없음)"
            print(
                f"    {label}: 총 {item.total_count}건 · 거절후보 "
                f"{item.release_blocked_count}건"
            )


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    with db.connect_readonly_existing(args.db) as conn:
        if conn is None:
            print(f"DB 파일이 없습니다: {args.db}", file=sys.stderr)
            return 1
        collected = collect_quality_observations(conn, since=args.since, until=args.until)
        summary = summarize_quality_observations(
            collected.rows, top_companies_limit=max(0, args.top_companies)
        )
    if args.json:
        print(
            json.dumps(
                _summary_to_json_dict(collected, summary), ensure_ascii=False, indent=2
            )
        )
    else:
        _print_table(collected, summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
