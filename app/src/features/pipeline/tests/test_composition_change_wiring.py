"""3개년 매출 구성 변화 표의 수집·배치 이음매를 검증한다."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

from src.core import revenue_table_switch
from src.features.pipeline import real
from src.features.pipeline.port import ReportTable, UserInput
from src.features.revenuemix.logic import build_multi_year
from src.shared.revenue_table_provenance import revenue_row_evidence_matches


_FIXTURE = (
    Path(__file__).resolve().parents[2]
    / "revenuemix"
    / "tests"
    / "fixtures"
    / "hybe_product_and_region.txt"
)
_FILING = {
    "rcept_no": "20260331000123",
    "rcept_dt": "20260331",
    "report_nm": "사업보고서 (2025.12)",
}


class _Engine:
    RAW_DIR = Path(".")
    SECTION_HEADS: dict[str, str] = {}
    FRAG_CHARS = 0

    def __init__(self, filing_text: str) -> None:
        self.filing_text = filing_text

    def download_document(self, *_args: Any) -> str:
        return "fixture"

    def read_filing_text(self, _path: str) -> str:
        return self.filing_text

    def make_fragments(self, *_args: Any) -> dict[int, dict[str, str]]:
        return {}


def _empty_collection() -> SimpleNamespace:
    return SimpleNamespace(
        state="none",
        detail="시험 자료 없음",
        candidate_scope_complete=True,
        fragments=(),
        attempted_documents=0,
        downloaded_pdf_bytes=0,
    )


def test_다개년_구성표를_수집해_실적표_뒤_4장에_제품_지역_순으로_놓는다(
    monkeypatch,
) -> None:
    filing_text = _FIXTURE.read_text(encoding="utf-8")
    monkeypatch.setenv(revenue_table_switch.REVENUE_TABLE_V2_ENV_NAME, "1")
    revenue_table_switch._reset_process_revenue_table_switch_for_tests()  # noqa: SLF001
    monkeypatch.setattr(real.filing_clean, "repair", lambda frags, *_args: (frags, 0))
    monkeypatch.setattr(real.filing_extra, "add_to", lambda frags, *_args: (frags, 0))
    monkeypatch.setattr(
        real.filing_relationships, "add_to", lambda frags, *_args: (frags, 0)
    )
    monkeypatch.setattr(
        real, "collect_homepage_fragments", lambda *_a, **_k: _empty_collection()
    )
    monkeypatch.setattr(
        real, "collect_official_ir_fragments", lambda *_a, **_k: _empty_collection()
    )

    steps: list[dict[str, Any]] = []
    try:
        fragments, tables, collected_text = real._collect(  # noqa: SLF001
            _Engine(filing_text),
            object(),
            {"corp_name": "가나다회사", "hm_url": ""},
            UserInput(company="가나다회사", job="", region="", posting_text=""),
            object(),
            steps,
            financials=None,
            fin_years=[],
            filing=_FILING,
        )
    finally:
        revenue_table_switch._reset_process_revenue_table_switch_for_tests()  # noqa: SLF001

    assert collected_text == filing_text
    assert len(fragments) == len(tables) == 4
    performance = ReportTable(
        caption="3개년 실적",
        headers=["사업연도", "매출액"],
        rows=[["2023", "1"], ["2024", "2"], ["2025", "3"]],
    )
    by_section = real._report_tables_by_section(tables, performance)  # noqa: SLF001
    assert len(by_section["portfolio"]) == 1
    assert len(by_section["business_model"]) == 1
    assert [table.caption for table in by_section["past_changes"]] == [
        "3개년 실적",
        "제품·서비스별 매출 비중 변화 (2023~2025)",
        "지역별 매출 비중 변화 (2023~2025)",
    ]
    composition_step = next(step for step in steps if step["step"] == "7_구성변화표")
    assert composition_step == {
        "step": "7_구성변화표",
        "축": ["product", "region"],
        "연도": ["2023", "2024", "2025"],
        "제외연도": [],
    }


def test_단년_원문에는_구성변화표와_연도가_기록되지_않는다(monkeypatch) -> None:
    filing_text = (
        "제품별 매출액 구 분 2025년 제1기 매 출 액 비 중 "
        "제품가 6,000 60.00% 제품나 4,000 40.00% 합계 10,000 100.00%"
    )
    monkeypatch.setattr(real.filing_clean, "repair", lambda frags, *_args: (frags, 0))
    monkeypatch.setattr(real.filing_extra, "add_to", lambda frags, *_args: (frags, 0))
    monkeypatch.setattr(
        real.filing_relationships, "add_to", lambda frags, *_args: (frags, 0)
    )
    monkeypatch.setattr(
        real, "collect_homepage_fragments", lambda *_a, **_k: _empty_collection()
    )
    monkeypatch.setattr(
        real, "collect_official_ir_fragments", lambda *_a, **_k: _empty_collection()
    )
    steps: list[dict[str, Any]] = []

    _fragments, tables, _text = real._collect(  # noqa: SLF001
        _Engine(filing_text),
        object(),
        {"corp_name": "가나다회사", "hm_url": ""},
        UserInput(company="가나다회사", job="", region="", posting_text=""),
        object(),
        steps,
        financials=None,
        fin_years=[],
        filing=_FILING,
    )

    assert len(tables) == 1
    assert real._report_tables_by_section(tables, None)["portfolio"]  # noqa: SLF001
    composition_step = next(step for step in steps if step["step"] == "7_구성변화표")
    assert composition_step == {
        "step": "7_구성변화표",
        "축": [],
        "연도": [],
        "제외연도": [],
    }


def test_다개년_근거는_실제_포함된_연도의_정확한_투영만_허용한다() -> None:
    filing_text = _FIXTURE.read_text(encoding="utf-8")
    table = build_multi_year(filing_text)[0]
    latest_index = table["headers"].index("2025 비중")
    projected_headers = [table["headers"][0], table["headers"][latest_index]]
    projected_row = [table["rows"][0][0], table["rows"][0][latest_index]]
    projected_raw_row = [
        table["raw_rows"][0][0],
        table["raw_rows"][0][latest_index],
    ]
    common = {
        "cited_source_text": filing_text,
        "filing_text": filing_text,
        "raw_row": projected_raw_row,
        "expected_selected_index": 0,
        "expected_row_count": len(table["rows"]) - 1,
    }

    assert revenue_row_evidence_matches(
        table["evidence_rows"][0],
        headers=projected_headers,
        public_row=projected_row,
        **common,
    )
    assert not revenue_row_evidence_matches(
        table["evidence_rows"][0],
        headers=["구분", "2099 비중"],
        public_row=projected_row,
        **common,
    )
    assert not revenue_row_evidence_matches(
        table["evidence_rows"][0],
        headers=projected_headers,
        public_row=[projected_row[0], "99.99%"],
        **common,
    )
