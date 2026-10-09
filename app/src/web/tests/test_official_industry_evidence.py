"""무분류 원문 지문을 검증한 뒤 슬롯 없는 산업 후보로만 운반한다."""

import copy
import hashlib

import pytest

from src.web.official_evidence_adapter import _unclassified_evidence_observation
from src.web.official_industry_evidence import industry_candidates_from_envelope


def envelope():
    company, receipt = "00000001", "20260315000001"
    kind = "dart_business_report"
    document_id = f"{kind}:{receipt}"
    text = "2025년 국내 센서 시장의 수요가 감소했습니다."
    digest = hashlib.sha256(text.encode()).hexdigest()
    return {"unclassified_documents": [{
        "company_id": company, "document_id": document_id,
        "canonical_url": f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt}",
        "source_tier": "TIER_1_OFFICIAL", "source_kind": kind, "requirement": "REQUIRED",
        "publisher": "합성 공시 발행자", "title": "사업보고서 (2025.12)",
        "published_on": "20260315", "collected_at": "2026-10-08T00:00:00+09:00",
        "content_sha256": hashlib.sha256(("앞부분 " + text).encode()).hexdigest(),
        "exact_evidence_hashes": [], "usable_ranges": [{"start": 4, "end": 4 + len(text)}],
        "identity_binding": f"corp_code={company};rcept_no={receipt};source_kind={kind};identity_check=verified_match",
        "collector_version": "synthetic-collector", "parser_version": "synthetic-parser",
    }], "unclassified_fragments": [{
        "company_id": company, "fragment_id": "synthetic-industry-fragment", "document_id": document_id,
        "location": f"4-{4 + len(text)}", "text": text, "text_sha256": digest,
        "section_id": "", "slot_id": "", "covered_slot_ids": [], "score_millis": 0,
        "reason_codes": ["no_signal", "official_industry_discovery"],
    }]}


def convert(raw):
    return industry_candidates_from_envelope(raw, company_id="00000001", validate=_unclassified_evidence_observation)


def test_document_and_original_fragment_are_preserved_without_writer_slot():
    raw = envelope()
    before = copy.deepcopy(raw)
    candidate, = convert(raw)
    assert raw == before
    assert candidate.text == raw["unclassified_fragments"][0]["text"]
    assert candidate.document.published_on == "2026-03-15"
    assert candidate.document.exact_evidence_hashes == (candidate.text_sha256,)
    assert not hasattr(candidate, "slot_id")


@pytest.mark.parametrize("field,value", [("text", "변조 원문"), ("location", "5-40"),
                                       ("company_id", "00000002"), ("section_context_json", "변조")])
def test_changed_fragment_never_borrows_valid_document(field, value):
    raw = envelope()
    raw["unclassified_fragments"][0][field] = value
    with pytest.raises(ValueError):
        convert(raw)


def test_ordinary_unclassified_is_not_promoted_and_missing_date_stays_invalid():
    raw = envelope()
    raw["unclassified_fragments"][0]["reason_codes"] = ["no_signal"]
    assert convert(raw) == ()
    raw = envelope()
    raw["unclassified_documents"][0]["published_on"] = ""
    with pytest.raises(ValueError):
        convert(raw)
