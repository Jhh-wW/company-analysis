"""DartRuntimeFetcher — 실제 네트워크 없이 get_json/download_document를
가짜 callable로 주입해 종단까지 검증한다.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import io
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

from core import dart_client
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


def test_엄격수집_callback_시그니처불일치는_FAILED가_아니라_밖으로_전파된다(
    tmp_path: Path,
) -> None:
    receipt_no = "20250315000001"

    def fake_get_json(
        endpoint: str,
        params: dict[str, Any],
        counter: UsageCounter,
    ) -> dict[str, Any]:
        return {
            "status": "000",
            "list": [
                {
                    "rcept_no": receipt_no,
                    "report_nm": "사업보고서 (2025.03)",
                    "rcept_dt": "20250315",
                }
            ],
        }

    # 의도적으로 옛 3개 positional 인자만 받는다. 엄격 수집기는
    # require_official_url_sidecar keyword를 요구하므로 이것은 외부 장애가
    # 아니라 조립부 callback 계약 위반이다.
    def incompatible_download(
        rcept_no: str,
        directory: Path,
        counter: UsageCounter,
    ) -> Path:
        raise AssertionError("시그니처 검사에서 함수 본문은 실행되지 않아야 합니다")

    fetcher = DartRuntimeFetcher(
        document_cache_dir=tmp_path / "raw",
        counter=_counter(tmp_path),
        get_json_fn=fake_get_json,
        download_document_fn=incompatible_download,
        require_official_url_sidecar=True,
        today=lambda: _FIXED_TODAY,
    )

    with pytest.raises(TypeError, match="require_official_url_sidecar"):
        collect_dart_evidence(fetcher, "00126380", now=_NOW)


def _sidecar_payload(
    *,
    receipt_no: str,
    main: bytes,
    url: str = "https://attachment-official.example/company",
) -> dict[str, object]:
    return {
        "version": dart_client.DOCUMENT_URL_SIDECAR_VERSION,
        "rcept_no": receipt_no,
        "main_document_sha256": hashlib.sha256(main).hexdigest(),
        "candidates": [
            {
                "url": url,
                "source_member_name": "covers/company.xml",
                "source_location": "raw_xml_chars:20-67",
                "source_payload_sha256": "a" * 64,
            }
        ],
    }


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


def test_구버전_cache가_비XML바이너리면_근거처럼_해석하지_않는다(
    tmp_path: Path,
) -> None:
    doc_path = tmp_path / "20250315000001.xml"
    doc_path.write_bytes(b"%PDF-binary cached by the old largest-file rule")
    fetcher = _fetcher(
        tmp_path,
        get_json_fn=lambda *args, **kwargs: {"status": "000", "list": []},
        download_document_fn=lambda *_args, **_kwargs: doc_path,
    )

    with pytest.raises(dart_client.DartResponseError, match="XML 문서"):
        fetcher.fetch_document_text("20250315000001")


def test_주입_transport가_과대_cache를_돌려줘도_소비경계에서_제한한다(
    tmp_path: Path,
    monkeypatch,
) -> None:
    doc_path = tmp_path / "20250315000001.xml"
    doc_path.write_bytes(b"<DOC>" + b"x" * 64 + b"</DOC>")
    monkeypatch.setattr(dart_client, "DOCUMENT_MEMBER_MAX_BYTES", 32)
    fetcher = _fetcher(
        tmp_path,
        get_json_fn=lambda *args, **kwargs: {"status": "000", "list": []},
        download_document_fn=lambda *_args, **_kwargs: doc_path,
    )

    with pytest.raises(dart_client.DartResponseError, match="허용 크기"):
        fetcher.fetch_document_text("20250315000001")


def test_document_xml의_href와_표시URL을_원문위치_해시와_함께_후보로_보존한다(
    tmp_path: Path,
) -> None:
    doc_path = tmp_path / "20250315000001.xml"
    doc_path.write_bytes(
        (
            '<P>홈페이지 <A HREF="http://shop.wisely.example/company?tenant=wise">회사</A></P>'
            '<P>공식 웹사이트 www.wisely.example</P>'
            '<SCHEMA href="http://www.w3.org/2001/XMLSchema.xsd" />'
            '<P>내부 주소 http://127.0.0.1/admin</P>'
        ).encode("utf-8")
    )

    fetcher = _fetcher(
        tmp_path,
        get_json_fn=lambda *a, **k: {"status": "013"},
        download_document_fn=lambda *_args, **_kwargs: doc_path,
    )

    result = fetcher.fetch_document_text("20250315000001")

    assert {candidate.url for candidate in result.official_url_candidates} == {
        "http://shop.wisely.example/company?tenant=wise",
        "https://www.wisely.example/",
    }
    assert all(
        candidate.location.startswith("raw_xml_chars:")
        for candidate in result.official_url_candidates
    )
    assert {candidate.source_member_name for candidate in result.official_url_candidates} == {
        "20250315000001.xml"
    }
    assert len({candidate.source_payload_sha256 for candidate in result.official_url_candidates}) == 1
    assert len(result.official_url_candidates[0].source_payload_sha256) == 64


def test_대표XML에는_없고_작은첨부_sidecar에만_있는_URL도_typed후보로_합친다(
    tmp_path: Path,
) -> None:
    receipt_no = "20250315000001"
    main = b"<DOC>main document without a web address</DOC>"
    doc_path = tmp_path / f"{receipt_no}.xml"
    doc_path.write_bytes(main)
    dart_client.document_url_sidecar_path(doc_path).write_text(
        json.dumps(_sidecar_payload(receipt_no=receipt_no, main=main)),
        encoding="utf-8",
    )
    fetcher = _fetcher(
        tmp_path,
        get_json_fn=lambda *args, **kwargs: {"status": "000", "list": []},
        download_document_fn=lambda *_args, **_kwargs: doc_path,
    )

    result = fetcher.fetch_document_text(receipt_no)

    assert [candidate.url for candidate in result.official_url_candidates] == [
        "https://attachment-official.example/company"
    ]
    assert result.official_url_candidates[0].source_member_name == (
        "covers/company.xml"
    )


def test_한번받은_DART_ZIP의_작은첨부_URL이_download에서_typed결과까지_이어진다(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """core 저장 이름과 typed loader가 실제 인터페이스로 맞물리는 시험."""

    receipt_no = "20250315000001"
    main = b"<DOC>main document without a web address</DOC>" * 20
    attachment = (
        "<COVER>공식 홈페이지 https://attachment-official.example/company</COVER>"
    ).encode("utf-8")
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("main.xml", main)
        archive.writestr("covers/company.xml", attachment)
    read_calls = 0

    def fake_read_url(*_args, **_kwargs) -> bytes:
        nonlocal read_calls
        read_calls += 1
        return buffer.getvalue()

    monkeypatch.setenv("DART_API_KEY", "fake-key")
    monkeypatch.setattr(dart_client, "_read_url", fake_read_url)
    fetcher = _fetcher(
        tmp_path,
        get_json_fn=lambda *args, **kwargs: {"status": "000", "list": []},
        download_document_fn=dart_client.download_document,
    )

    result = fetcher.fetch_document_text(receipt_no)

    assert read_calls == 1
    assert [candidate.url for candidate in result.official_url_candidates] == [
        "https://attachment-official.example/company"
    ]
    assert result.official_url_candidates[0].source_member_name == (
        "covers/company.xml"
    )


def test_FULL_typed_fetcher는_warm_cache_sidecar실패를_자료부족으로_숨기지_않는다(
    tmp_path: Path,
    monkeypatch,
) -> None:
    receipt_no = "20250315000001"
    cache_dir = tmp_path / "raw"
    cache_dir.mkdir()
    document_path = cache_dir / f"{receipt_no}.xml"
    old_document = b"<DOC>old representative remains intact</DOC>"
    document_path.write_bytes(old_document)
    monkeypatch.setenv("DART_API_KEY", "fake-key")
    monkeypatch.setattr(
        dart_client,
        "_read_url",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            dart_client.DartTransportError("가짜 일시 장애")
        ),
    )
    fetcher = DartRuntimeFetcher(
        document_cache_dir=cache_dir,
        counter=_counter(tmp_path),
        get_json_fn=lambda *_args, **_kwargs: {"status": "013"},
        download_document_fn=dart_client.download_document,
        require_official_url_sidecar=True,
        today=lambda: _FIXED_TODAY,
    )

    with pytest.raises(dart_client.DartTransportError):
        fetcher.fetch_document_text(receipt_no)

    assert document_path.read_bytes() == old_document
    assert not dart_client.document_url_sidecar_path(document_path).exists()


def test_FULL_typed_fetcher는_주입_transport가_sidecar를_빼도_대표XML로_우회하지_않는다(
    tmp_path: Path,
) -> None:
    receipt_no = "20250315000001"
    document_path = tmp_path / f"{receipt_no}.xml"
    document_path.write_bytes(
        b"<DOC>official homepage https://main-official.example/</DOC>"
    )
    observed_kwargs: dict[str, object] = {}

    def fake_download(*_args, **kwargs):
        observed_kwargs.update(kwargs)
        return document_path

    fetcher = DartRuntimeFetcher(
        document_cache_dir=tmp_path,
        counter=_counter(tmp_path),
        get_json_fn=lambda *_args, **_kwargs: {"status": "013"},
        download_document_fn=fake_download,
        require_official_url_sidecar=True,
        today=lambda: _FIXED_TODAY,
    )

    with pytest.raises(
        dart_client.DartResponseError,
        match="sidecar 결속",
    ):
        fetcher.fetch_document_text(receipt_no)

    assert observed_kwargs == {"require_official_url_sidecar": True}


def test_다른접수번호나_부분JSON_sidecar는_전부버리고_대표XML만_쓴다(
    tmp_path: Path,
) -> None:
    receipt_no = "20250315000001"
    fallback_url = "https://main-official.example/"
    main = f"<DOC>공식 홈페이지 {fallback_url}</DOC>".encode("utf-8")
    doc_path = tmp_path / f"{receipt_no}.xml"
    doc_path.write_bytes(main)
    sidecar_path = dart_client.document_url_sidecar_path(doc_path)
    sidecar_path.write_text(
        json.dumps(
            _sidecar_payload(
                receipt_no="20250315000002",
                main=main,
                url="https://stale-attacker.example/",
            )
        ),
        encoding="utf-8",
    )
    fetcher = _fetcher(
        tmp_path,
        get_json_fn=lambda *args, **kwargs: {"status": "000", "list": []},
        download_document_fn=lambda *_args, **_kwargs: doc_path,
    )

    stale_result = fetcher.fetch_document_text(receipt_no)
    sidecar_path.write_text('{"version":', encoding="utf-8")
    partial_result = fetcher.fetch_document_text(receipt_no)

    assert [candidate.url for candidate in stale_result.official_url_candidates] == [
        fallback_url
    ]
    assert [candidate.url for candidate in partial_result.official_url_candidates] == [
        fallback_url
    ]
    assert all(
        candidate.source_member_name == doc_path.name
        for candidate in partial_result.official_url_candidates
    )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("url", "https://127.0.0.1/admin"),
        ("url", "https://attachment-official.example:443/company"),
        ("url", "https://attachment-official.example\\attacker.example/company"),
        ("source_member_name", "covers/../attacker.xml"),
        ("source_location", "raw_xml_chars:" + "9" * 5_000 + "-10"),
    ),
)
def test_sidecar의_URL_경로_위치가_생산계약과_다르면_전체를_버린다(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    receipt_no = "20250315000001"
    fallback_url = "https://main-official.example/"
    main = f"<DOC>공식 홈페이지 {fallback_url}</DOC>".encode("utf-8")
    doc_path = tmp_path / f"{receipt_no}.xml"
    doc_path.write_bytes(main)
    payload = _sidecar_payload(receipt_no=receipt_no, main=main)
    candidate = payload["candidates"][0]
    assert isinstance(candidate, dict)
    candidate[field] = value
    dart_client.document_url_sidecar_path(doc_path).write_text(
        json.dumps(payload),
        encoding="utf-8",
    )
    fetcher = _fetcher(
        tmp_path,
        get_json_fn=lambda *args, **kwargs: {"status": "000", "list": []},
        download_document_fn=lambda *_args, **_kwargs: doc_path,
    )

    result = fetcher.fetch_document_text(receipt_no)

    assert [candidate.url for candidate in result.official_url_candidates] == [
        fallback_url
    ]


def test_DART전문_URL은_채점되지않은_href라도_harvest에_남는다(tmp_path: Path) -> None:
    doc_path = tmp_path / "20250315000001.xml"
    doc_path.write_bytes(
        (
            '<P><A HREF="https://official.wisely.example/">공식 홈페이지</A></P>\n'
            + LISTED_BUSINESS_REPORT_TEXT
        ).encode("utf-8")
    )

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

    fetcher = _fetcher(
        tmp_path,
        get_json_fn=fake_get_json,
        download_document_fn=lambda *_args, **_kwargs: doc_path,
    )

    harvest = collect_dart_evidence(fetcher, "00126380", now=_NOW)

    assert len(harvest.official_url_candidates) == 1
    candidate = harvest.official_url_candidates[0]
    assert candidate.url == "https://official.wisely.example/"
    assert candidate.source_receipt_no == "20250315000001"
    assert candidate.source_document_id == "dart_business_report:20250315000001"
    assert candidate.source_member_name == "20250315000001.xml"
    assert candidate.source_location.startswith("raw_xml_chars:")
    assert candidate.source_document_sha256 == harvest.documents[0].content_sha256


def test_종단_가짜_client_주입으로_collect_dart_evidence까지_돌아간다(tmp_path: Path) -> None:
    """실제 네트워크 없이 get_json/download_document만 가짜로 갈아끼워
    filing_select → collect까지 실제 어댑터로 종단 수집이 되는지 확인한다.
    """
    doc_path = tmp_path / "20250315000001.xml"
    doc_path.write_bytes(
        f"<DOC>{LISTED_BUSINESS_REPORT_TEXT}</DOC>".encode("utf-8")
    )

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
