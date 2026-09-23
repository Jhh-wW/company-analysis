"""SHADOW 표의 감사 상태를 원문 문장과 별개로 영속화한다."""

from dataclasses import replace

import pytest

from src.features.pipeline.port import Grade, Report, ReportSection, ReportTable
from src.features.storage.reports import _table_from_dict, _table_to_dict, report_from_json, report_to_json
from src.shared.report_generation.constants import ENGINE_V2_SCHEMA_VERSION


def _table():
    return ReportTable(caption="비교 재무", headers=["연도", "매출"], rows=[["2024", "100"]], cite="[1]")


def test_old_table_wire_omits_audit_field_and_roundtrips_unchanged():
    payload = _table_to_dict(_table())
    assert "unaudited_years" not in payload
    restored = _table_from_dict(payload)
    assert restored.unaudited_years == ()
    assert _table_to_dict(restored) == payload


def test_shadow_saved_report_preserves_typed_audit_years():
    table = replace(_table(), unaudited_years=("2024",))
    report = Report(company="합성 검증 대상", job="", corp_type="비상장 외감", grade=Grade.PARTIAL,
                    schema_version=ENGINE_V2_SCHEMA_VERSION, release_mode="SHADOW",
                    sections=[ReportSection(cell="past_changes", title="실적", tables=[table])])
    stored = report_to_json(report)
    restored = report_from_json(stored)
    assert restored.sections[0].tables[0].unaudited_years == ("2024",)
    assert report_to_json(restored) == stored


@pytest.mark.parametrize("years", [None, "2024", [2024], [True], ["24"], ["2024년"], ["２０２４"], ["2024", "2024"], [{}]])
def test_invalid_audit_year_wire_is_rejected(years):
    with pytest.raises(ValueError, match="비교연도"):
        _table_from_dict({**_table_to_dict(_table()), "unaudited_years": years})
