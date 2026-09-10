"""공식 메타데이터 결속만 대조한다. API 호출·수치 재계산·실제 키는 없다."""

from __future__ import annotations

import ast
import copy
from pathlib import Path
import re
from types import SimpleNamespace
from typing import Optional

import pytest

from src.features.pipeline import real
from src.features.pipeline.evidence_transport import typed_fragments_from_raw

CORP = "01234567"
RECEIPT = "20260318000123"
DISCLOSED = "2026-03-18"


def filing_record():
    return {"corp_code": CORP, "rcept_no": RECEIPT, "rcept_dt": "20260318",
            "report_nm": "사업보고서 (2025.12)",
            "engine_selected_report_period_kind": "annual"}


def api_payload():
    base = {"corp_code": CORP, "rcept_no": RECEIPT, "bsns_year": "2025",
            "reprt_code": "11011", "fs_div": "CFS", "sj_div": "IS",
            "account_nm": "영업이익", "thstrm_amount": "12000",
            "thstrm_dt": "2025.01.01 ~ 2025.12.31"}
    return {"status": "000", "list": [base, {**base, "fs_div": "OFS"}]}


def engine_fragments(payload):
    """engine의 순수 조각 함수만 읽는다. 모듈 import와 .env 적재는 하지 않는다."""
    path = Path(__file__).resolve().parents[5] / "analysis_engine/tools/run_pilot.py"
    tree = ast.parse(path.read_text(encoding="utf-8-sig"))
    names = {"SECTION_HEADS", "FRAG_CHARS", "MAX_FRAGS"}
    constants = {n.target.id: ast.literal_eval(n.value) for n in tree.body
                 if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)
                 and n.target.id in names}
    constants.update({n.targets[0].id: ast.literal_eval(n.value) for n in tree.body
                      if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
                      and n.targets[0].id in names})
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "make_fragments")
    namespace = {"re": re, "Optional": Optional, **constants}
    exec(compile(ast.Module(body=[function], type_ignores=[]), str(path), "exec"), namespace)
    return namespace["make_fragments"]("", payload)


def test_exact_annual_binding_preserves_all_input_bytes():
    payload, filing = api_payload(), filing_record()
    frags = engine_fragments(payload)
    snapshot = copy.deepcopy((payload, filing, frags))
    attached = real._attach_financial_api_disclosure_date(frags, financials=payload, filing=filing, corp_code=CORP)
    assert attached[1] == {**frags[1], "financial_api_disclosed_at": DISCLOSED}
    assert (payload, filing, frags) == snapshot
    assert attached[1]["원문"] == frags[1]["원문"]
    assert "문서일" not in attached[1]


@pytest.mark.parametrize("field,value", [
    ("corp_code", "07654321"), ("bsns_year", "2024"), ("reprt_code", "11012"),
    ("rcept_no", "20260318000999"), ("rcept_no", ""),
    ("fs_div", ""), ("sj_div", ""), ("account_nm", ""),
])
def test_mixed_response_rejects_date_even_after_displayed_rows(field, value):
    payload = api_payload()
    payload["list"] = [copy.deepcopy(payload["list"][0]) for _ in range(15)]
    payload["list"][-1][field] = value
    assert real._financial_api_disclosed_at(payload, filing_record(), CORP) == ""


@pytest.mark.parametrize("change", [
    {"corp_code": "07654321"}, {"rcept_no": "20260319000124"},
    {"report_nm": "사업보고서 (2024.12)"},
    {"report_nm": "반기보고서 (2025.06)"},
    {"report_nm": "사업보고서 (2025.13)"},
    {"reprt_code": "11012"}, {"bsns_year": "2024"},
    {"engine_selected_report_period_kind": "quarterly"},
    {"engine_selected_report_period_kind": ""},
    {"rcept_dt": ""}, {"rcept_dt": "20260230"}, {"rcept_dt": "2026-03-18"},
])
def test_unbound_or_missing_official_metadata_never_borrows_date(change):
    filing = {**filing_record(), **change}
    assert real._financial_api_disclosed_at(api_payload(), filing, CORP) == ""


def test_explicit_annual_code_and_corrected_report_are_supported():
    filing = filing_record()
    filing.pop("engine_selected_report_period_kind")
    filing.update(reprt_code="11011", report_nm="[기재정정]사업보고서 (2025.12)")
    assert real._financial_api_disclosed_at(api_payload(), filing, CORP) == DISCLOSED


@pytest.mark.parametrize("payload", [None, {}, {"status": "013", "list": []},
                                    {"status": "000", "list": [None]}])
def test_missing_or_malformed_api_does_not_create_date(payload):
    assert real._financial_api_disclosed_at(payload, filing_record(), CORP) == ""


def test_unrelated_text_or_url_and_fiscal_end_are_not_date_evidence():
    payload = api_payload()
    original = engine_fragments(payload)[1]
    frags = {1: {**original, "원문": original["원문"] + " 기자 설명"},
             2: {**original, "출처": "https://example.test/financial"},
             3: {"종류": "사업내용", "원문": "공식 사업 설명"}}
    assert real._attach_financial_api_disclosure_date(frags, financials=payload, filing=filing_record(), corp_code=CORP) == frags
    filing = filing_record()
    filing.pop("rcept_dt")
    assert real._financial_api_disclosed_at(payload, filing, CORP) == ""
    assert real._financial_api_disclosed_at(payload, filing_record(), "07654321") == ""


def test_collect_entry_and_existing_transport_keep_verified_date(monkeypatch):
    payload, filing = api_payload(), filing_record()
    fake_engine = SimpleNamespace(
        RAW_DIR="", download_document=lambda *_: "",
        read_filing_text=lambda _: "", make_fragments=lambda _, data: engine_fragments(data),
    )
    missing = SimpleNamespace(state="none", fragments=())
    monkeypatch.setattr(real, "collect_homepage_fragments", lambda *_a, **_k: missing)
    monkeypatch.setattr(real, "collect_official_ir_fragments", lambda *_a, **_k: missing)
    monkeypatch.setattr(real, "_official_web_collection_step", lambda _: {})
    monkeypatch.setattr(real, "_official_ir_collection_step", lambda _: {})
    monkeypatch.setattr(real, "_attach_name_candidate_fragments", lambda frags, **_: (frags, 0))
    monkeypatch.setattr(real, "_typed_dart_collection_enabled", lambda _: False)
    frags, _, _ = real._collect(fake_engine, None, {"corp_name": "시험법인"}, None, None, [],
                                 financials=payload, fin_years=[2025], filing=filing, corp_code=CORP)
    assert frags[1]["financial_api_disclosed_at"] == DISCLOSED
    assert frags[1]["원문"] == engine_fragments(payload)[1]["원문"]
    flat = typed_fragments_from_raw(corp_id=CORP, frags=frags, filing_meta=None)
    assert flat.fragments[0].financial_api_disclosed_at == DISCLOSED
