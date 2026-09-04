"""`reports.list_report_ids` — payload를 안 건드리는 최소 열거 API 시험."""

from __future__ import annotations

from pathlib import Path

from src.features.pipeline.port import Grade, Report
from src.features.storage import db, reports


def _minimal_report() -> Report:
    return Report(
        company="가나다전자",
        job="",
        corp_type="상장사",
        grade=Grade.PARTIAL,
        sections=[],
    )


def test_빈_DB는_빈_목록이다(tmp_path: Path) -> None:
    db_path = tmp_path / "storage.db"
    with db.connect(db_path) as conn:
        result = reports.list_report_ids(conn)

    assert result == []


def test_정렬은_created_at_그다음_report_id_오름차순으로_결정론적이다(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "storage.db"
    with db.connect(db_path) as conn:
        # 저장 순서를 뒤섞어도(zzz 먼저, aaa 나중) 정렬 결과는 같아야 한다.
        reports.save(
            conn, "zzz", "CORP-1", "", _minimal_report(),
            created_at="2026-02-01T00:00:00",
        )
        reports.save(
            conn, "b-same-time", "CORP-1", "", _minimal_report(),
            created_at="2026-01-01T00:00:00",
        )
        reports.save(
            conn, "a-same-time", "CORP-1", "", _minimal_report(),
            created_at="2026-01-01T00:00:00",
        )

    with db.connect(db_path) as conn:
        result = reports.list_report_ids(conn)

    assert [report_id for report_id, _ in result] == [
        "a-same-time",
        "b-same-time",
        "zzz",
    ]


def test_기간_필터는_since_until_모두_날짜_경계를_포함한다(tmp_path: Path) -> None:
    db_path = tmp_path / "storage.db"
    with db.connect(db_path) as conn:
        for report_id, created_at in (
            ("early", "2026-01-31T23:59:59"),
            ("boundary-start", "2026-02-01T00:00:00"),
            ("middle", "2026-02-15T12:00:00"),
            ("boundary-end", "2026-02-28T23:59:59"),
            ("late", "2026-03-01T00:00:00"),
        ):
            reports.save(
                conn, report_id, "CORP-1", "", _minimal_report(),
                created_at=created_at,
            )

    with db.connect(db_path) as conn:
        windowed = reports.list_report_ids(conn, since="2026-02-01", until="2026-02-28")
        since_only = reports.list_report_ids(conn, since="2026-02-15")
        until_only = reports.list_report_ids(conn, until="2026-02-01")
        unfiltered = reports.list_report_ids(conn)

    # since·until 둘 다 그 날짜 자체를 포함한다(경계 포함).
    assert [report_id for report_id, _ in windowed] == [
        "boundary-start",
        "middle",
        "boundary-end",
    ]
    assert [report_id for report_id, _ in since_only] == ["middle", "boundary-end", "late"]
    assert [report_id for report_id, _ in until_only] == ["early", "boundary-start"]
    assert len(unfiltered) == 5


def test_payload_json은_전혀_읽지_않고_report_id와_created_at만_돌려준다(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "storage.db"
    with db.connect(db_path) as conn:
        reports.save(
            conn, "r1", "CORP-1", "", _minimal_report(),
            created_at="2026-05-01T00:00:00",
        )

    with db.connect(db_path) as conn:
        result = reports.list_report_ids(conn)

    assert result == [("r1", "2026-05-01T00:00:00")]
