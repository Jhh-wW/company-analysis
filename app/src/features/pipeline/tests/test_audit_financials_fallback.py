from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.features.composer.constants import (
    DART_DOCUMENT_HOST,
    DART_DOCUMENT_URL_TEMPLATE,
    SECTION_IDS,
)
from src.features.composer.port import (
    CollectedFragment,
    ComposedReport,
    ComposedSection,
    FilingMeta,
    performance_table_from_report_table,
)
from src.features.composer.public_manifest import (
    PublicManifestError,
    _FragmentBinding,
    _validated_program_bindings,
    build_public_structure_seal,
)
from src.features.pipeline import real
from src.features.pipeline.canonical_report import (
    basic_report_selection_subset,
    historical_performance_bases_are_complete,
    historical_performance_required_year_count,
)
from src.features.pipeline.port import ReportTable
from src.features.report_standard.cover_metrics import cover_metrics
from src.features.spanselect.canonical import CanonicalPick
from src.shared.report_quality.source_identity import document_identity_from_parts


FIXTURE = (
    Path(__file__).parents[2]
    / "audit_financials"
    / "tests"
    / "fixtures"
    / "20260406001240_income.xml"
)


def _api_table() -> ReportTable:
    return ReportTable(
        caption="전자공시 최근 세 사업연도 별도 주요 실적 (단위: 억원)",
        headers=["사업연도", "매출액", "영업이익"],
        rows=[
            ["2025", "300", "30"],
            ["2024", "200", "20"],
            ["2023", "100", "10"],
        ],
        cite="조각 1·재무",
        numeric=True,
        raw_rows=[
            ["2025", "30000000000", "3000000000"],
            ["2024", "20000000000", "2000000000"],
            ["2023", "10000000000", "1000000000"],
        ],
        scale_divisor="100000000",
        display_unit="억원",
        evidence_rows=["API payload"] * 3,
        entity_scope="separate",
        raw_unit="원",
        unit_dimension="currency",
    )


def _fallback() -> tuple[dict[int, dict[str, object]], ReportTable, list[dict[str, object]]]:
    frags: dict[int, dict[str, object]] = {
        1: {"종류": "사업", "원문": "회사는 제품을 판매합니다."}
    }
    steps: list[dict[str, object]] = []
    table, is_audit = real._build_performance_table_with_audit_fallback(
        frags=frags,
        financials=None,
        filing={
            "rcept_no": "20260406001240",
            "rcept_dt": "20260406",
            "report_nm": "감사보고서 (2025.12)",
        },
        filing_text=FIXTURE.read_text(encoding="utf-8"),
        steps=steps,
    )
    assert is_audit
    assert table is not None
    return frags, table, steps


def test_API_표가_있으면_감사보고서_파서를_부르지_않는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected = _api_table()
    monkeypatch.setattr(real, "build_three_year_table", lambda *_args, **_kwargs: expected)

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("API 정본이 있으면 감사보고서 파서를 부르면 안 됩니다")

    monkeypatch.setattr(real, "parse_audit_financials", forbidden)
    steps: list[dict[str, object]] = []

    table, is_audit = real._build_performance_table_with_audit_fallback(
        frags={1: {"종류": "재무", "원문": "주요계정(DART API): payload"}},
        financials={"status": "000", "list": [{}]},
        filing={"rcept_no": "20260406001240"},
        filing_text=FIXTURE.read_text(encoding="utf-8"),
        steps=steps,
    )

    assert table is expected
    assert not is_audit
    assert steps == []


def test_API가_비면_감사보고서_2개년_표와_전용_원문_조각을_만든다() -> None:
    frags, table, steps = _fallback()

    assert table.rows == [
        ["2025", "43", "-24", "-22"],
        ["2024", "29", "-24", "-33"],
    ]
    assert table.caption.count("단위: 억원") == 1
    assert "최근 두 사업연도" in table.caption
    assert table.cite == "조각 2·감사보고서 재무"
    assert frags[2]["문서ID"] == "20260406001240"
    assert steps[-1] == {
        "step": "7_실적표_감사보고서_대체",
        "접수번호": "20260406001240",
        "연도": ["2025", "2024"],
        "진단": "성공",
    }

    metrics = cover_metrics(SimpleNamespace(sections=[SimpleNamespace(tables=[table])]))
    assert metrics
    assert [(item.label, item.value) for item in metrics.items] == [
        ("매출액", "43"),
        ("영업이익", "-24"),
    ]
    assert metrics.cite == table.cite


def test_감사보고서_표는_원문_span_해시와_원수치가_맞아야_봉인된다() -> None:
    frags, table, _steps = _fallback()
    fragment = frags[2]
    source_text = str(fragment["원문"])
    source = _FragmentBinding(
        fragment_id="2",
        document_identity="dart:20260406001240",
        exact_evidence_hash=hashlib.sha256(source_text.encode("utf-8")).hexdigest(),
    )
    performance = performance_table_from_report_table(table)

    bindings = _validated_program_bindings(
        performance,
        {"2": source},
        {},
        fragment_texts={"2": source_text},
    )

    assert len(bindings) == 2
    assert all(binding["source_fragment_ids"] == ["2"] for binding in bindings)

    tampered_payload = json.loads(performance.evidence_rows[0])
    tampered_payload["source_sha256"] = "0" * 64
    tampered = replace(
        performance,
        evidence_rows=(
            json.dumps(tampered_payload, ensure_ascii=False),
            performance.evidence_rows[1],
        ),
    )
    with pytest.raises(PublicManifestError, match="canonical numeric"):
        _validated_program_bindings(
            tampered,
            {"2": source},
            {},
            fragment_texts={"2": source_text},
        )


def test_인이지_접수번호_표가_조립과_공개구조_봉인을_끝까지_통과한다() -> None:
    frags, table, _steps = _fallback()
    performance = performance_table_from_report_table(table)
    receipt_number = "20260406001240"
    source_text = str(frags[2]["원문"])
    identity = document_identity_from_parts(
        document_id=receipt_number,
        host=DART_DOCUMENT_HOST,
        url=DART_DOCUMENT_URL_TEMPLATE.format(document_id=receipt_number),
    )
    fragment = CollectedFragment(
        fragment_id="2",
        kind="감사보고서 재무",
        text=source_text,
        document_title="감사보고서 (2025.12)",
        location=str(frags[2]["원문위치"]),
        document_identity=identity,
    )
    report = ComposedReport(
        sections=tuple(
            ComposedSection(section_id=section_id, sentences=())
            for section_id in SECTION_IDS
        )
    )

    seal = build_public_structure_seal(
        report,
        (fragment,),
        performance,
        filing_meta=FilingMeta(
            document_id=receipt_number,
            title="감사보고서 (2025.12)",
            disclosed_at="2026-04-06",
        ),
        composition_tables=(),
        table_presentation="trend",
        company_id="00123456",
        evidence_generation_sha256="a" * 64,
        evidence_packet_sha256s=tuple(
            (section_id, "b" * 64) for section_id in SECTION_IDS
        ),
        company_name="인이지",
        corp_type="비상장 외감",
        generated_at="2026-09-06T00:00:00+09:00",
        as_of_date="2026-09-06",
        analysis_period="2024~2025 완료 회계연도",
        latest_performance_period="2025년 연간 공식 공시",
        citation_style="auto",
    )

    manifest_table = seal.table_entry("past_changes", 0)
    assert manifest_table["rows"] == table.rows
    assert manifest_table["cite"] == table.cite
    assert manifest_table["source_cites"] == ["[2]"]
    assert manifest_table["display_unit"] == "억원"


def test_감사보고서_파서가_못_찾아도_진단만_남기고_표없이_계속한다() -> None:
    frags: dict[int, dict[str, object]] = {}
    steps: list[dict[str, object]] = []

    table, is_audit = real._build_performance_table_with_audit_fallback(
        frags=frags,
        financials=None,
        filing={"rcept_no": "20260406001240", "report_nm": "감사보고서"},
        filing_text="재무제표가 없는 감사보고서 본문",
        steps=steps,
    )

    assert table is None
    assert not is_audit
    assert frags == {}
    assert steps == [
        {
            "step": "7_실적표_감사보고서_대체",
            "접수번호": "20260406001240",
            "연도": [],
            "진단": "손익계산서 미탐",
        }
    ]


def test_감사보고서를_먼저_고르고_없을_때만_연결감사보고서를_고른다() -> None:
    regular = real._latest_audit_statement_filing(
        [
            {"rcept_no": "20260409000001", "report_nm": "연결감사보고서 (2025.12)"},
            {"rcept_no": "20260408000001", "report_nm": "감사보고서 (2025.12)"},
            {"rcept_no": "20260410000001", "report_nm": "감사보고서 (2024.12)"},
        ]
    )
    consolidated = real._latest_audit_statement_filing(
        [
            {"rcept_no": "20260409000001", "report_nm": "연결감사보고서 (2024.12)"},
            {"rcept_no": "20260410000001", "report_nm": "연결감사보고서 (2025.12)"},
        ]
    )

    assert regular is not None
    assert regular["rcept_no"] == "20260410000001"
    assert consolidated is not None
    assert consolidated["rcept_no"] == "20260410000001"


def test_연도_관문은_API_3개년을_유지하고_감사보고서_2개년만_허용한다() -> None:
    api_three = _api_table()
    api_two = replace(
        api_three,
        rows=api_three.rows[:2],
        raw_rows=api_three.raw_rows[:2],
        evidence_rows=api_three.evidence_rows[:2],
    )
    _frags, audit_two, _steps = _fallback()
    bases_two = {"historical-performance:2024", "historical-performance:2025"}
    bases_three = {*bases_two, "historical-performance:2023"}

    assert historical_performance_required_year_count(api_three) == 3
    assert historical_performance_required_year_count(api_two) == 3
    assert historical_performance_bases_are_complete(bases_three)
    assert not historical_performance_bases_are_complete(bases_two)
    assert historical_performance_required_year_count(audit_two) == 2
    assert historical_performance_bases_are_complete(
        bases_two,
        required_year_count=historical_performance_required_year_count(audit_two),
    )

    picks = [
        CanonicalPick(
            section_id="identity",
            sentence="회사는 소프트웨어 기업입니다.",
            fragment_id=1,
            sid="1-1",
            claim_type="identity_summary",
        ),
        CanonicalPick(
            section_id="business_model",
            sentence="회사는 사용료로 수익을 냅니다.",
            fragment_id=2,
            sid="2-1",
            claim_type="revenue_model",
        ),
    ]
    assert not basic_report_selection_subset(
        picks,
        historical_performance_bases=bases_two,
    )
    assert basic_report_selection_subset(
        picks,
        historical_performance_bases=bases_two,
        required_performance_year_count=2,
    )
