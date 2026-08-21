from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from src.features.export_pdf.automatic_release import report_sha256
from src.features.pipeline.port import Grade, Report, ReportSection, ReportTable
from src.features.storage import db, reports


def _report(*, presentation: str = "table") -> Report:
    return Report(
        company="호환회사",
        job="",
        corp_type="상장사",
        grade=Grade.COMPLETE,
        sections=[
            ReportSection(
                cell="1",
                title="사업 구성",
                lines=[("공식 자료에서 확인된 구성이다.", "[1]")],
                tables=[
                    ReportTable(
                        caption="사업 구성표",
                        headers=["항목", "비중"],
                        rows=[["제품", "100%"]],
                        cite="[1]",
                        display_unit="%",
                        presentation=presentation,
                    )
                ],
            )
        ],
        cells={"1": True},
        generated_at="2026-08-21",
    )


def _legacy_payload() -> dict[str, object]:
    """``presentation`` 필드가 생기기 전의 정확한 저장 모양."""

    return {
        "company": "호환회사",
        "job": "",
        "corp_type": "상장사",
        "grade": "완성",
        "sections": [
            {
                "cell": "1",
                "title": "사업 구성",
                "lines": [["공식 자료에서 확인된 구성이다.", "[1]"]],
                "prose_lines": [],
                "guidance_lines": [],
                "display_number": "",
                "tag": "",
                "fact_ids": [],
                "empty_reason": "",
                "tables": [
                    {
                        "caption": "사업 구성표",
                        "headers": ["항목", "비중"],
                        "rows": [["제품", "100%"]],
                        "cite": "[1]",
                        "numeric": False,
                        "raw_rows": [],
                        "scale_divisor": "",
                        "scale_places": 0,
                        "display_unit": "%",
                    }
                ],
            }
        ],
        "requirements": [],
        "sources": [],
        "citations": [],
        "cells": {"1": True},
        "shortfall_reasons": [],
        "generated_at": "2026-08-21",
        "schema_version": "",
        "summary_items": [],
        "fact_records": [],
        "as_of_date": "",
        "analysis_period": "",
        "latest_performance_period": "",
    }


def test_default_table_keeps_legacy_payload_bytes_and_report_digest() -> None:
    report = _report(presentation="table")
    expected_payload = _legacy_payload()
    expected_json = json.dumps(expected_payload, ensure_ascii=False)
    expected_digest_input = json.dumps(
        expected_payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    actual_json = reports.report_to_json(report)
    table_payload = reports.report_to_dict(report)["sections"][0]["tables"][0]

    assert "presentation" not in table_payload
    assert actual_json.encode("utf-8") == expected_json.encode("utf-8")
    assert report_sha256(report) == hashlib.sha256(expected_digest_input).hexdigest()


def test_legacy_payload_without_presentation_is_read_as_table() -> None:
    restored = reports.report_from_json(
        json.dumps(_legacy_payload(), ensure_ascii=False)
    )

    assert restored.sections[0].tables[0].presentation == "table"


@pytest.mark.parametrize("presentation", ["composition", "trend"])
def test_non_default_presentation_survives_storage_roundtrip(
    tmp_path: Path,
    presentation: str,
) -> None:
    original = _report(presentation=presentation)
    target = tmp_path / f"{presentation}.db"

    with db.connect(target) as conn:
        reports.save(
            conn,
            f"report-{presentation}",
            "CORP-PRESENTATION",
            "",
            original,
            created_at="2026-08-21T12:00:00+09:00",
        )
        stored_json = conn.execute(
            "SELECT payload_json FROM reports WHERE report_id = ?",
            (f"report-{presentation}",),
        ).fetchone()["payload_json"]

    with db.connect(target) as conn:
        restored = reports.load(conn, f"report-{presentation}")

    assert json.loads(stored_json)["sections"][0]["tables"][0][
        "presentation"
    ] == presentation
    assert restored is not None
    assert restored.sections[0].tables[0].presentation == presentation
    assert restored == original
