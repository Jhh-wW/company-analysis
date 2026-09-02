"""저장된 보고서에서 SHADOW 품질 관측값(`quality_observation`)만 읽기 전용으로 모은다.

★ 새 AI·네트워크 호출은 없다. 이미 저장된 payload만 읽는다. 여기서 읽은 값을
  다시 판정하지 않는다 — `src.shared.report_quality.observation_summary`가
  이미 내려진 판정을 셀 뿐이다.

★ 열거 기준 — `report_store.list_report_ids()`(storage 공개 API, 2026-09-02
  추가)로 `reports` 표 «전체»를 스캔한다. `admin_dashboard`가 자신의 사용
  이벤트 표(회원이 실제로 「사용」한 것만 기록됨)에서 report_id를 뽑는 방식은
  쓰지 않는다 — 그러면 이벤트가 안 남은 SHADOW 생성분을 조용히 빠뜨려 거절
  후보 집계 자체가 과소평가된다.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Optional

from src.features.storage import reports as report_store
from src.shared.report_quality.observation_summary import ObservationRow


@dataclass(frozen=True)
class CollectedQualityObservations:
    """DB 한 번 순회 결과 — 요약기 입력 행과 「관측값 없는 보고서」건수를 함께 담는다."""

    rows: tuple[ObservationRow, ...]
    #: `quality_observation`이 없는 보고서(대부분 SHADOW·과거 저장본) 건수.
    without_observation_count: int
    #: 필터를 통과해 실제로 `load()`한 보고서 총 건수(관측값 유무 무관).
    total_reports_scanned: int


def collect_quality_observations(
    conn: sqlite3.Connection,
    *,
    since: Optional[str] = None,
    until: Optional[str] = None,
) -> CollectedQualityObservations:
    """저장된 보고서를 storage 공개 API로만 읽어 관측값 있는 것만 모은다.

    `report_store.list_report_ids()`로 `report_id`를 얻고(`payload_json` 미접근,
    `created_at` 날짜 경계로 필터) 본문은 전부 `report_store.load()`에 맡긴다.
    이 함수는 `reports` 표에 직접 SQL을 쓰지 않는다.

    Args:
        conn: 이미 연결된 SQLite 연결(읽기 전용 연결도 가능 — 쓰지 않는다).
        since: `created_at` 날짜 하한(포함, ``YYYY-MM-DD``). 생략하면 제한 없음.
        until: `created_at` 날짜 상한(포함, ``YYYY-MM-DD``). 생략하면 제한 없음.

    Returns:
        관측값이 있는 보고서만 요약기 입력 형태로 담은 결과. `payload`를
        바꾸지 않는다 — `list_report_ids()`·`load()`만 부른다.
    """

    rows: list[ObservationRow] = []
    without_observation = 0
    scanned = 0
    candidates = report_store.list_report_ids(
        conn, since=since or "", until=until or ""
    )
    for report_id, _created_at in candidates:
        report = report_store.load(conn, report_id)
        if report is None:
            # 열거와 조회 사이에 지워진 경쟁 — 있었던 적 없는 것처럼 건너뛴다.
            continue
        scanned += 1
        if report.quality_observation is None:
            without_observation += 1
            continue
        rows.append(
            (
                report_id,
                report.company_id,
                report.generated_at,
                report.release_mode,
                report.quality_observation,
            )
        )
    return CollectedQualityObservations(
        rows=tuple(rows),
        without_observation_count=without_observation,
        total_reports_scanned=scanned,
    )
