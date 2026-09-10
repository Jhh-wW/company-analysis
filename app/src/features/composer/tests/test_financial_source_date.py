"""공시일 전달·packet/출처 봉인·저장·표시만 검증하며 의미 품질을 주장하지 않는다."""

import hashlib
from dataclasses import replace

import pytest

from src.features.composer import port
from src.features.composer.constants import (
    DART_FINANCIAL_API_PREFIX, DART_FINANCIAL_API_URL,
    DART_FINANCIAL_API_HOST, DART_FINANCIAL_API_DOCUMENT_ID,
    GRADE_CONFIRMED,
)
from src.features.composer.port import (
    CollectedFragment, ComposedReport, ComposedSection, ComposedSentence,
    FilingMeta, SectionEvidencePacket,
)
from src.features.composer.logic import _normalize_packet_fragments
from src.features.composer.public_manifest import _expected_source, _expected_public_content_projection
from src.features.composer.render import _build_source, _fragment_metas
from src.shared.report_quality.source_identity import document_identity_from_parts

DISCLOSED = "2026-03-18"
COLLECTED = "2026-09-09"
TEXT = f"{DART_FINANCIAL_API_PREFIX} 영업이익 12000(2025.01.01 ~ 2025.12.31)"


def fragment(**changes):
    value = CollectedFragment(
        fragment_id="7", kind="재무", text=TEXT,
        document_identity=document_identity_from_parts(
            document_id=DART_FINANCIAL_API_DOCUMENT_ID,
            host=DART_FINANCIAL_API_HOST, url=DART_FINANCIAL_API_URL),
        financial_api_disclosed_at=DISCLOSED,
    )
    return replace(value, **changes)


@pytest.mark.parametrize("shape", ["raw", "typed"])
def test_source_and_manifest_keep_separate_dates(shape):
    item = fragment(document_date=COLLECTED)
    raw = {7: {"종류": "재무", "원문": TEXT, "문서일": COLLECTED,
               "financial_api_disclosed_at": DISCLOSED}}
    meta = _fragment_metas(raw if shape == "raw" else (item,))[0]
    unrelated = FilingMeta("20260901000999", "반기보고서 (2026.06)", "2026-09-01")
    source = _build_source(meta, 7, "시험법인", ("past_changes",), unrelated)
    expected = _expected_source(item, number=7, company_name="시험법인",
                                used_in=("past_changes",), filing_meta=unrelated)
    assert source == expected
    assert source.disclosed_at == DISCLOSED
    assert source.collected_at == COLLECTED
    assert source.number == 7 and source.url == DART_FINANCIAL_API_URL
    assert source.exact_evidence_hashes == [hashlib.sha256(TEXT.encode()).hexdigest()]


def test_raw_and_typed_packet_normalization_keep_explicit_date():
    raw = {7: {"종류": "재무", "원문": TEXT, "financial_api_disclosed_at": DISCLOSED}}
    assert port.fragments_from_raw(raw)[0].financial_api_disclosed_at == DISCLOSED
    assert _normalize_packet_fragments(raw)[0].financial_api_disclosed_at == DISCLOSED
    assert _normalize_packet_fragments((fragment(),))[0].financial_api_disclosed_at == DISCLOSED
    assert _normalize_packet_fragments(raw)[0].text == TEXT


def test_full_expected_public_projection_includes_verified_api_date():
    item = fragment()
    report = ComposedReport(
        sections=(ComposedSection(section_id="past_changes", sentences=(
            ComposedSentence(text="영업이익을 공시했다.", citations=("7",), grade=GRADE_CONFIRMED),
        )),), summary=(),
    )
    projection = _expected_public_content_projection(
        report, (item,), (), company_name="시험법인", company_id="01234567",
        corp_type="상장", generated_at="2026-09-09", as_of_date="2026-09-09",
        analysis_period="2025", latest_performance_period="2025 연간",
        citation_style="inline", filing_meta=None,
    )
    citation = next(c for c in projection["citations"] if c["number"] == 7)
    assert citation["disclosed_at"] == DISCLOSED and citation["collected_at"] == ""
    assert citation["exact_evidence_hashes"] == [hashlib.sha256(TEXT.encode()).hexdigest()]


def test_legacy_missing_date_does_not_inherit_latest_filing_or_reporting_period():
    item = fragment(financial_api_disclosed_at="", reporting_period="2025-12-31")
    source = _expected_source(item, number=7, company_name="시험법인", used_in=(),
                              filing_meta=FilingMeta("20260319000999", "사업보고서 (2025.12)", "2026-03-19"))
    assert source.disclosed_at == source.collected_at == ""
    dated_legacy = _expected_source(replace(item, document_date=COLLECTED), number=7,
                                    company_name="시험법인", used_in=(), filing_meta=None)
    assert dated_legacy.disclosed_at == "" and dated_legacy.collected_at == COLLECTED


def test_packet_binds_new_date_but_preserves_absent_field_contract(monkeypatch):
    payloads = []
    original = port._packet_json

    def capture(value):
        payloads.append(value)
        return original(value)

    monkeypatch.setattr(port, "_packet_json", capture)
    def packet(item):
        return SectionEvidencePacket(company_id="01234567", evidence_generation_sha256="a" * 64,
                                     section_id="past_changes", fragments=(item,))
    legacy = packet(fragment(financial_api_disclosed_at=""))
    assert payloads[-1]["version"] == 4
    assert "financial_api_disclosed_at" not in payloads[-1]["fragments"][0]
    assert packet(fragment(financial_api_disclosed_at="")).packet_sha256 == legacy.packet_sha256
    dated = packet(fragment())
    assert payloads[-1]["version"] == 5
    assert payloads[-1]["fragments"][0]["financial_api_disclosed_at"] == DISCLOSED
    assert dated.packet_sha256 != legacy.packet_sha256
    changed = packet(fragment(financial_api_disclosed_at="2026-03-19"))
    assert changed.packet_sha256 != dated.packet_sha256
    assert changed.fragments[0].text == dated.fragments[0].text


@pytest.mark.parametrize("value", ["20260230", "2026-02-30", "20260318", " "])
def test_malformed_date_is_not_a_valid_fragment(value):
    with pytest.raises(ValueError, match="날짜|YYYY"):
        fragment(financial_api_disclosed_at=value)
