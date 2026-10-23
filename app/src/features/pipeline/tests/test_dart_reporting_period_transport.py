"""DART 날짜범위만 허용하고 웹·IR 전용 근거와 잘못된 기간은 계속 차단한다."""

import pytest

from src.features.pipeline.evidence_transport import (
    EvidenceTransportError, RAW_EVIDENCE_REPORTING_PERIOD_KEY,
    RAW_EVIDENCE_ATTACHMENT_URL_KEY, RAW_EVIDENCE_IR_METADATA_VERIFICATION_KEY,
)
from src.features.pipeline.tests.test_evidence_transport import _typed_raw_for_formal_kind, _build, _all_legacy_frags, _fragment_by_id
from src.shared.report_evidence.constants import SOURCE_KIND_DART_AUDIT_REPORT
from src.shared.report_evidence.filing_period import filing_period_is_valid


@pytest.mark.parametrize("period", ["", "2025-01-01/2025-12-31", "2025-01-01/2025-06-30"])
def test_typed_dart_keeps_empty_or_explicit_valid_period_without_ir_metadata(period):
    raw = _typed_raw_for_formal_kind(SOURCE_KIND_DART_AUDIT_REPORT)
    raw[RAW_EVIDENCE_REPORTING_PERIOD_KEY] = period
    packet = _build({**_all_legacy_frags(), 99: raw})
    assert _fragment_by_id(packet, 99).reporting_period == period


@pytest.mark.parametrize("period", ["2025", "2025-Q4", "2025-12-31", "2025-02-30/2025-12-31", "2025-12-31/2025-01-01"])
def test_typed_dart_rejects_nonexplicit_impossible_or_reversed_period(period):
    assert not filing_period_is_valid(period)
    raw = _typed_raw_for_formal_kind(SOURCE_KIND_DART_AUDIT_REPORT)
    raw[RAW_EVIDENCE_REPORTING_PERIOD_KEY] = period
    with pytest.raises(EvidenceTransportError):
        _build({**_all_legacy_frags(), 99: raw})


@pytest.mark.parametrize("field,value", [(RAW_EVIDENCE_ATTACHMENT_URL_KEY, "https://example.com/report.pdf"),
    (RAW_EVIDENCE_IR_METADATA_VERIFICATION_KEY, "공식 IR 메타데이터 확인")])
def test_valid_dart_period_does_not_release_other_ir_only_fields(field, value):
    raw = _typed_raw_for_formal_kind(SOURCE_KIND_DART_AUDIT_REPORT)
    raw[RAW_EVIDENCE_REPORTING_PERIOD_KEY] = "2025-01-01/2025-12-31"
    raw[field] = value
    with pytest.raises(EvidenceTransportError):
        _build({**_all_legacy_frags(), 99: raw})
