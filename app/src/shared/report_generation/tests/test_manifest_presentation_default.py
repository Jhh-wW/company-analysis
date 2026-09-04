"""저장 wire의 생략 가능한 표 기본값과 공개 manifest의 단일 계약."""

from __future__ import annotations

import json

import pytest

from src.shared.report_generation.canonical import (
    PUBLIC_STRUCTURE_MANIFEST_VERSION,
    PublicManifestError,
    _actual_table_fields,
    assert_report_matches_manifest,
)
from src.shared.report_generation.models import canonical_sha256


_MISSING = object()


def _wire_table(*, presentation: object = _MISSING) -> dict[str, object]:
    table: dict[str, object] = {
        "caption": "실적표",
        "headers": ["항목", "2025"],
        "rows": [],
        "cite": "",
        "numeric": True,
        "display_unit": "억원",
        "raw_rows": [],
        "scale_divisor": "",
        "scale_places": 0,
        "entity_scope": "",
        "raw_unit": "",
        "unit_dimension": "currency",
        "source_cites": [],
        "row_fact_ids": [],
        "row_evidence_refs": [],
        "row_binding_refs": [],
        "cell_binding_refs": [],
    }
    if presentation is not _MISSING:
        table["presentation"] = presentation
    return table


def _manifest_and_report(
    table: dict[str, object], *, expected_presentation: str, expected_kind: str
) -> tuple[str, dict[str, object]]:
    expected_table = {
        "section_id": "past_changes",
        "table_index": 0,
        "kind": expected_kind,
        "caption": "실적표",
        "headers": ["항목", "2025"],
        "rows": [],
        "cite": "",
        "numeric": True,
        "presentation": expected_presentation,
        "display_unit": "억원",
        "raw_rows": [],
        "scale_divisor": "",
        "scale_places": 0,
        "entity_scope": "",
        "raw_unit": "",
        "unit_dimension": "currency",
        "source_cites": [],
        "row_fact_ids": [],
        "row_evidence_refs": [],
        "row_binding_refs": [],
        "cell_binding_refs": [],
        "numeric_tokens": [],
        "row_bindings": [],
    }
    expected_table["manifest_ref"] = canonical_sha256(expected_table)
    table["manifest_ref"] = expected_table["manifest_ref"]
    unsigned_manifest = {
        "version": PUBLIC_STRUCTURE_MANIFEST_VERSION,
        "company_id": "00126380",
        "evidence_generation_sha256": "a" * 64,
        "evidence_packet_sha256s": [],
        "sections": ["past_changes"],
        "tables": [expected_table],
    }
    manifest = {
        **unsigned_manifest,
        "digest": canonical_sha256(unsigned_manifest),
    }
    manifest_json = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    report = {
        "company_id": "00126380",
        "sections": [{"cell": "past_changes", "tables": [table]}],
        "citations": [],
        "public_structure_manifest": manifest_json,
    }
    return manifest_json, report


def test_wire에서_생략된_presentation은_ReportTable_정본대로_table이다() -> None:
    table = _wire_table()
    manifest_json, report = _manifest_and_report(
        table, expected_presentation="table", expected_kind="program"
    )

    assert "presentation" not in table
    assert _actual_table_fields(
        section_id="past_changes", table_index=0, table=table
    )["presentation"] == "table"
    assert_report_matches_manifest(report, manifest_json)


@pytest.mark.parametrize(
    ("presentation", "kind"),
    [("flow", "flow"), ("composition", "program")],
)
def test_wire의_명시적_presentation은_그대로_보존된다(
    presentation: str, kind: str
) -> None:
    table = _wire_table(presentation=presentation)
    manifest_json, report = _manifest_and_report(
        table, expected_presentation=presentation, expected_kind=kind
    )

    assert_report_matches_manifest(report, manifest_json)


def test_알_수_없는_presentation은_table로_숨기지_않고_manifest에서_거절된다() -> None:
    table = _wire_table(presentation="unknown-visual")
    manifest_json, report = _manifest_and_report(
        table, expected_presentation="table", expected_kind="program"
    )

    with pytest.raises(PublicManifestError, match="actual 표·flow"):
        assert_report_matches_manifest(report, manifest_json)
