"""DartRuntimeFetcher — 실제 네트워크 없이 get_json/download_document를
가짜 callable로 주입해 종단까지 검증한다.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

from core.dart_client import UsageCounter
from features.evidence_collection import constants as c
from features.evidence_collection.collect import collect_dart_evidence
from features.evidence_collection.dart_fetcher import DartRuntimeFetcher
from features.evidence_collection.tests.fixtures.synthetic_documents import LISTED_BUSINESS_REPORT_TEXT

_NOW = "2026-08-31T00:00:00+09:00"
_FIXED_TODAY = dt.date(2026, 8, 31)


def _counter(tmp_path: Path) -> UsageCounter:
    # 실제 로그 디렉터리를 절대 건드리지 않는다 — 시험 전용 경로만 쓴다.
    return UsageCounter(path=tmp_path / "dart_usage.json")


def _fetcher(
    tmp_path: Path,
    *,
    get_json_fn: Any,
    download_document_fn: Any,
) -> DartRuntimeFetcher:
    return DartRuntimeFetcher(
        document_cache_dir=tmp_path / "raw",
        counter=_counter(tmp_path),
        get_json_fn=get_json_fn,
        download_document_fn=download_document_fn,
        today=lambda: _FIXED_TODAY,
    )


def test_list_json_status_000이면_OK와_행을_돌려준다(tmp_path: Path) -> None:
    calls: list[tuple[str, dict[str, Any]]] = []

    def fake_get_json(endpoint: str, params: dict[str, Any], counter: UsageCounter) -> dict[str, Any]:
        calls.append((endpoint, params))
        return {
            "status": "000",
            "list": [
                {"rcept_no": "20250315000001", "report_nm": "사업보고서 (2025.03)", "rcept_dt": "20250315"},
            ],
        }

    fetcher = _fetcher(tmp_path, get_json_fn=fake_get_json, download_document_fn=lambda *a, **k: Path("unused"))

    result = fetcher.fetch_filing_list("00126380", "A")

    assert result.state == c.ATTEMPT_STATE_OK
    assert len(result.rows) == 1
    assert result.rows[0].rcept_no == "20250315000001"
    assert calls[0][0] == "list.json"
    assert calls[0][1]["corp_code"] == "00126380"
    assert calls[0][1]["pblntf_ty"] == "A"
    assert calls[0][1]["end_de"] == "20260831"
    assert calls[0][1]["bgn_de"] == "20230901"  # 3년 근사(365*3일) 전


def test_list_json_status_013이면_OK에_빈_행이다(tmp_path: Path) -> None:
    def fake_get_json(endpoint: str, params: dict[str, Any], counter: UsageCounter) -> dict[str, Any]:
        return {"status": "013"}

    fetcher = _fetcher(tmp_path, get_json_fn=fake_get_json, download_document_fn=lambda *a, **k: Path("unused"))

    result = fetcher.fetch_filing_list("00126380", "A")

    assert result.state == c.ATTEMPT_STATE_OK
    assert result.rows == ()


def test_list_json_알_수_없는_상태는_FAILED로_fail_closed한다(tmp_path: Path) -> None:
    def fake_get_json(endpoint: str, params: dict[str, Any], counter: UsageCounter) -> dict[str, Any]:
        return {"status": "999"}

    fetcher = _fetcher(tmp_path, get_json_fn=fake_get_json, download_document_fn=lambda *a, **k: Path("unused"))

    result = fetcher.fetch_filing_list("00126380", "A")

    assert result.state == c.ATTEMPT_STATE_FAILED


def test_document_xml_태그를_벗기고_평문으로_돌려주고_corp_code는_비운다(tmp_path: Path) -> None:
    doc_path = tmp_path / "20250315000001.xml"
    doc_path.write_bytes("<TITLE>회사의 개요</TITLE><P>당사는 주식회사다.</P>".encode("utf-8"))

    def fake_download_document(rcept_no: str, dest_dir: Path, counter: UsageCounter) -> Path:
        assert rcept_no == "20250315000001"
        return doc_path

    fetcher = _fetcher(
        tmp_path, get_json_fn=lambda *a, **k: {"status": "000", "list": []},
        download_document_fn=fake_download_document,
    )

    result = fetcher.fetch_document_text("20250315000001")

    assert result.state == c.ATTEMPT_STATE_OK
    assert "<" not in result.text and ">" not in result.text
    assert "회사의 개요" in result.text
    assert "당사는 주식회사다." in result.text
    assert result.corp_code == ""  # DART document.xml에는 구조화된 corp_code가 없다(확인 못 함)
    assert result.bytes_downloaded == doc_path.stat().st_size


def test_종단_가짜_client_주입으로_collect_dart_evidence까지_돌아간다(tmp_path: Path) -> None:
    """실제 네트워크 없이 get_json/download_document만 가짜로 갈아끼워
    filing_select → collect까지 실제 어댑터로 종단 수집이 되는지 확인한다.
    """
    doc_path = tmp_path / "20250315000001.xml"
    doc_path.write_bytes(LISTED_BUSINESS_REPORT_TEXT.encode("utf-8"))

    def fake_get_json(endpoint: str, params: dict[str, Any], counter: UsageCounter) -> dict[str, Any]:
        if params["pblntf_ty"] == "A":
            return {
                "status": "000",
                "list": [
                    {
                        "rcept_no": "20250315000001",
                        "report_nm": "사업보고서 (2025.03)",
                        "rcept_dt": "20250315",
                    },
                ],
            }
        return {"status": "013"}

    def fake_download_document(rcept_no: str, dest_dir: Path, counter: UsageCounter) -> Path:
        return doc_path

    fetcher = _fetcher(tmp_path, get_json_fn=fake_get_json, download_document_fn=fake_download_document)

    harvest = collect_dart_evidence(fetcher, "00126380", now=_NOW)

    assert harvest.company_type == c.COMPANY_TYPE_LISTED
    assert len(harvest.documents) == 1
    assert harvest.documents[0].source_kind == c.SOURCE_KIND_BUSINESS_REPORT
    assert len(harvest.fragments) >= 1
    assert f"identity_check={c.IDENTITY_CHECK_UNVERIFIED}" in harvest.documents[0].identity_binding


# ══════════════════════════════════════════════════════════
# generation=8 후속 item 3 — list.json 행의 corp_code를 방어적으로 읽는다
# ══════════════════════════════════════════════════════════


def test_item3_list_json_행에_corp_code가_있으면_방어적으로_읽어_싣는다(tmp_path: Path) -> None:
    def fake_get_json(endpoint: str, params: dict[str, Any], counter: UsageCounter) -> dict[str, Any]:
        return {
            "status": "000",
            "list": [
                {
                    "rcept_no": "20250315000001",
                    "report_nm": "사업보고서 (2025.03)",
                    "rcept_dt": "20250315",
                    "corp_code": "00126380",
                    "corp_name": "샘플기업",
                },
            ],
        }

    fetcher = _fetcher(tmp_path, get_json_fn=fake_get_json, download_document_fn=lambda *a, **k: Path("unused"))

    result = fetcher.fetch_filing_list("00126380", "A")

    assert result.rows[0].corp_code == "00126380"
    assert result.rows[0].corp_name == "샘플기업"


def test_item3_list_json_행에_corp_code가_없으면_빈_문자열로_남긴다(tmp_path: Path) -> None:
    """실제 응답에 이 필드가 오는지 실측하지 못했다 — 없으면 확인 못 함으로 남는다."""
    def fake_get_json(endpoint: str, params: dict[str, Any], counter: UsageCounter) -> dict[str, Any]:
        return {
            "status": "000",
            "list": [
                {"rcept_no": "20250315000001", "report_nm": "사업보고서 (2025.03)", "rcept_dt": "20250315"},
            ],
        }

    fetcher = _fetcher(tmp_path, get_json_fn=fake_get_json, download_document_fn=lambda *a, **k: Path("unused"))

    result = fetcher.fetch_filing_list("00126380", "A")

    assert result.rows[0].corp_code == ""
    assert result.rows[0].corp_name == ""
