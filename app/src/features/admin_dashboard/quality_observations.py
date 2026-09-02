"""저장된 보고서에서 SHADOW 품질 관측값(`quality_observation`)만 읽기 전용으로 모은다.

★ 새 AI·네트워크 호출은 없다. 이미 저장된 payload만 읽는다. 여기서 읽은 값을
  다시 판정하지 않는다 — `src.shared.report_quality.observation_summary`가
  이미 내려진 판정을 셀 뿐이다.

★ report_id 목록 구하는 법에 대한 설계 기록 (2026-09-02) — `src/features/storage/
  reports.py`에는 「report_id 하나를 안다」는 전제의 `load(conn, report_id)`만
  있고, DB에 어떤 report_id들이 있는지 목록화하는 공개 함수가 없다(직접 확인,
  `list`/`iter` 접두 공개 함수 0건). 같은 feature 안에서도
  `admin_dashboard/store.py:1804`·`:1868`이 이미 `storage_constants.
  TABLE_REPORTS`에 직접 JOIN하는 전례가 있어 그 결을 따르되, 이 모듈은 그보다
  더 좁게 `report_id`·`generated_at` 두 칸만 읽고(`payload_json`은 절대 안
  읽음) 실제 본문 해석은 전부 `report_store.load()`(storage 공개 API)에
  맡긴다. 이 판단은 root 검토 대상이다 — 소유 경로 밖 파일에 최소 열거 함수를
  추가하는 대안(A안)을 함께 제안해 두었다.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Iterator, Optional

from src.features.storage import reports as report_store
from src.features.storage.constants import TABLE_REPORTS
from src.shared.report_quality.observation_summary import ObservationRow


@dataclass(frozen=True)
class CollectedQualityObservations:
    """DB 한 번 순회 결과 — 요약기 입력 행과 「관측값 없는 보고서」건수를 함께 담는다."""

    rows: tuple[ObservationRow, ...]
    #: `quality_observation`이 없는 보고서(대부분 SHADOW·과거 저장본) 건수.
    without_observation_count: int
    #: 필터를 통과해 실제로 `load()`한 보고서 총 건수(관측값 유무 무관).
    total_reports_scanned: int


def _candidate_report_ids(
    conn: sqlite3.Connection, *, since: Optional[str], until: Optional[str]
) -> Iterator[str]:
    """`reports` 표에서 `report_id`만 읽는다. `payload_json`은 절대 건드리지 않는다.

    ``since``/``until``은 날짜만(``YYYY-MM-DD``) 받는다. `generated_at`은 초 단위
    ISO 8601 전체 시각이라 문자열째로 비교하면 시각이 붙은 값이 같은 날짜의
    날짜만 있는 상한보다 사전식으로 더 «크게» 읽혀 그날 하루가 통째로
    빠진다. `admin_dashboard/weekly.py`가 이미 쓰는 ``substr(..., 1, 10)``
    날짜 앞자리 비교로 같은 함정을 피한다.
    """

    query = f"SELECT report_id FROM {TABLE_REPORTS}"
    clauses: list[str] = []
    params: list[str] = []
    if since is not None:
        clauses.append("substr(generated_at, 1, 10) >= ?")
        params.append(since)
    if until is not None:
        clauses.append("substr(generated_at, 1, 10) <= ?")
        params.append(until)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY generated_at, report_id"
    for row in conn.execute(query, tuple(params)):
        yield str(row[0])


def collect_quality_observations(
    conn: sqlite3.Connection,
    *,
    since: Optional[str] = None,
    until: Optional[str] = None,
) -> CollectedQualityObservations:
    """저장된 보고서를 `report_store.load()`로만 읽어 관측값 있는 것만 모은다.

    Args:
        conn: 이미 연결된 SQLite 연결(읽기 전용 연결도 가능 — 쓰지 않는다).
        since: `generated_at` 하한(포함). 생략하면 제한 없음.
        until: `generated_at` 상한(포함). 생략하면 제한 없음.

    Returns:
        관측값이 있는 보고서만 요약기 입력 형태로 담은 결과. `payload`를
        바꾸지 않는다 — 오직 `report_store.load()`만 부른다.
    """

    rows: list[ObservationRow] = []
    without_observation = 0
    scanned = 0
    for report_id in _candidate_report_ids(conn, since=since, until=until):
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
