"""엔진→정규화→슬롯 선택→스냅샷의 순회 상태 결속 회귀."""
import copy
import hashlib
import importlib
from dataclasses import asdict, replace
from pathlib import Path

import pytest

from src.features.chapter_evidence.normalize import to_attempt
from src.features.chapter_evidence.produce import produce_from_collection_envelopes
from src.features.chapter_evidence.select import select_section_fragments
from src.features.chapter_evidence.tests.test_select import _document, _fragment
from src.features.pipeline.official_collection_diagnostics import official_collection_attempt_step
from src.shared.report_evidence.constants import CollectionState
from src.shared.report_evidence.document_scan import DocumentScan
from src.shared.report_evidence.runtime_port import OfficialEvidenceCollectionResult


def _mapping(monkeypatch, text):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[5] / "analysis_engine" / "src"))
    collect = importlib.import_module("features.evidence_collection.collect")
    filings = importlib.import_module("features.evidence_collection.filing_select")
    serialize = importlib.import_module("features.evidence_collection.serialize")
    fake = importlib.import_module("features.evidence_collection.tests.fixtures.fake_fetcher")
    row = filings.RawFilingRow("20250315000001", "사업보고서 (2024.12)", "20250315")
    fetcher = fake.FakeFetcher(
        list_responses_by_pblntf_ty={"A": filings.FilingListResult(state="OK", rows=(row,))},
        document_responses_by_rcept_no={row.rcept_no: filings.DocumentFetchResult(state="OK", text=text)},
    )
    return serialize.harvest_to_mapping(collect.collect_dart_evidence(fetcher, "00126380", now="2026-09-14"))


def _result(mapping):
    return OfficialEvidenceCollectionResult(company_id=mapping["company_id"], candidates=produce_from_collection_envelopes(
        company_id=mapping["company_id"], company_type=mapping["company_type"], collection_envelopes=(mapping,),
    ))


def _document_attempt(mapping):
    return next(value for value in mapping["attempts"] if value.get("document_scan"))


def test_엔진_완료와_압축_계약은_앱_정규화와_진단까지_보존한다(monkeypatch):
    text = "\n\n".join(f"당사는 정밀부품을 생산하는 주식회사이며 법인이다. 항목 {index}" for index in range(200))
    mapping = _mapping(monkeypatch, text)
    raw = _document_attempt(mapping)
    result = _result(mapping)
    attempts = [value for candidate in result.candidates for value in candidate.attempts if value.document_scan]
    assert attempts and all(asdict(value.document_scan) == raw["document_scan"] for value in attempts)
    assert all(value.state is CollectionState.OK for value in attempts)
    scan = attempts[0].document_scan
    assert scan.state == "COMPLETE" and scan.selection_compressed
    assert scan.content_sha256 == hashlib.sha256(text.encode()).hexdigest()
    diagnostic = official_collection_attempt_step(result)
    assert diagnostic["document_scan"]["COMPLETE"] == 1
    assert diagnostic["document_scan"]["selection_compressed"] == 1
    assert any(row["reason_code"] == "document_selection_compressed" for row in diagnostic["histogram"])
    assert scan.content_sha256 not in repr(diagnostic)
    assert scan.document_id not in repr(diagnostic)


def test_상태만_달라도_스냅샷이_달라지고_구형은_완료로_승격되지_않는다(monkeypatch):
    mapping = _mapping(monkeypatch, "당사는 정밀부품을 생산하는 주식회사이며 법인이다.")
    baseline = _result(mapping)
    changed = copy.deepcopy(mapping)
    attempt = _document_attempt(changed)
    attempt["state"] = "TRUNCATED"
    attempt["reason_code"] = "deadline_exceeded"
    attempt["document_scan"]["state"] = "INCOMPLETE"
    attempt["document_scan"]["scanned_chars"] -= 1
    incomplete = _result(changed)
    assert incomplete.source_snapshot_sha256 != baseline.source_snapshot_sha256
    legacy = copy.deepcopy(mapping)
    del _document_attempt(legacy)["document_scan"]
    old = _result(legacy)
    assert old.source_snapshot_sha256 != baseline.source_snapshot_sha256
    assert all(value.document_scan is None for candidate in old.candidates for value in candidate.attempts)
    compressed = copy.deepcopy(mapping)
    _document_attempt(compressed)["document_scan"]["selection_compressed"] = True
    assert _result(compressed).source_snapshot_sha256 != baseline.source_snapshot_sha256


@pytest.mark.parametrize("field,value", [("version", "document_scan/0"), ("scanned_chars", 0),
                                          ("selection_compressed", "false"), ("unclassified_seen", True)])
def test_잘못된_완료_증명은_정규화에서_거절한다(monkeypatch, field, value):
    mapping = _mapping(monkeypatch, "당사는 정밀부품을 생산하는 주식회사이며 법인이다.")
    attempt = _document_attempt(mapping)
    attempt["document_scan"][field] = value
    with pytest.raises(ValueError):
        to_attempt(attempt)


def test_같은_시도의_서로다른_스캔은_봉인할_수_없다(monkeypatch):
    result = _result(_mapping(monkeypatch, "당사는 정밀부품을 생산하는 주식회사이며 법인이다."))
    candidates = list(result.candidates)
    original = next(value for value in candidates[0].attempts if value.document_scan)
    conflicting = replace(original, document_scan=replace(original.document_scan, selection_compressed=True))
    candidates[1] = replace(candidates[1], attempts=(conflicting,))
    with pytest.raises(ValueError, match="같은 수집 시도"):
        replace(result, candidates=tuple(candidates))


def test_문서와_스캔의_해시가_다르면_스냅샷을_만들지_않는다(monkeypatch):
    mapping = _mapping(monkeypatch, "당사는 정밀부품을 생산하는 주식회사이며 법인이다.")
    _document_attempt(mapping)["document_scan"]["content_sha256"] = "a" * 64
    with pytest.raises(ValueError, match="원문 해시"):
        _result(mapping)


def test_무분류_원문을_한건도_보관하지_못해도_관측_상태는_남는다(monkeypatch):
    _mapping(monkeypatch, "")
    constants = importlib.import_module("features.evidence_collection.constants")
    monkeypatch.setattr(constants, "MAX_UNCLASSIFIED_CANDIDATES_PER_DOCUMENT", 0)
    mapping = _mapping(monkeypatch, "오늘 날씨가 맑고 하늘이 파랗다는 독립적인 설명을 충분히 기록한다.")
    result = _result(mapping)
    assert not any(candidate.documents or candidate.fragments for candidate in result.candidates)
    scan = next(value.document_scan for candidate in result.candidates for value in candidate.attempts if value.document_scan)
    assert scan.state == "COMPLETE" and scan.unclassified_seen == 1 and scan.unclassified_retained == 0
    assert scan.selection_compressed


def test_앱_문자예산도_끝부분_취소를_앞부분_고득점으로_밀어내지_않는다():
    first = _fragment(fragment_id="front", text="제품 판매 계획을 확대하고 서비스를 제공한다." * 10, score_millis=1000)
    tail = replace(_fragment(fragment_id="tail", text="기존 제품 판매 계획을 취소하고 적용 기간을 변경하였다." * 10, score_millis=250),
                   location="9000-9300", reason_codes=("selection_change_context", "selection_recent_context"))
    document = _document(exact_evidence_hashes=(first.text_sha256, tail.text_sha256))
    result = select_section_fragments(section_id="business_model", company_id="corp-1", documents=(document,),
                                      fragments=(first, tail), max_chars=len(tail.text), max_estimated_tokens=1000)
    assert result.fragments == (tail,)
