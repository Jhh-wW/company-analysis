"""산업 조사 원문은 캐시를 구별하되 회사 자료 하한을 채우지 않는다."""

from dataclasses import replace

import pytest

from src.shared.report_evidence.industry_candidates import OfficialIndustryCandidateEvidence
from src.shared.report_evidence.runtime_port import OfficialEvidenceCollectionResult
from src.shared.report_evidence.tests.test_runtime_port import COMPANY_ID, FIRST_TEXT, _candidates, _document, _sha256


def candidate():
    digest = _sha256(FIRST_TEXT)
    return OfficialIndustryCandidateEvidence(
        COMPANY_ID, "industry-discovery", _document(text_hashes=(digest,)),
        f"0-{len(FIRST_TEXT)}", digest, FIRST_TEXT,
    )


def test_industry_changes_snapshot_but_not_company_document_floor_or_slots():
    baseline = OfficialEvidenceCollectionResult(COMPANY_ID, _candidates())
    added = replace(baseline, industry_candidates=(candidate(),))
    assert baseline.candidates == added.candidates
    assert baseline.independent_document_count == added.independent_document_count
    assert baseline.source_snapshot_sha256 != added.source_snapshot_sha256
    assert replace(added, industry_candidates=()).source_snapshot_sha256 == baseline.source_snapshot_sha256
    later = replace(candidate(), document=replace(candidate().document, collected_at="2026-10-01"))
    assert replace(added, industry_candidates=(later,)).source_snapshot_sha256 == added.source_snapshot_sha256


def test_same_document_cannot_change_content_or_identity_through_industry_channel():
    baseline = OfficialEvidenceCollectionResult(COMPANY_ID, _candidates())
    for changes in ({"content_sha256": "a" * 64}, {"publisher": "다른 발행자"}, {"identity_binding": "다른 결속"}):
        altered = replace(candidate(), document=replace(candidate().document, **changes))
        with pytest.raises(ValueError, match="신원이 다릅니다"):
            replace(baseline, industry_candidates=(altered,))


def test_candidate_cannot_use_an_unregistered_hash_or_outside_document_range():
    with pytest.raises(ValueError):
        replace(candidate(), document=replace(candidate().document, exact_evidence_hashes=("f" * 64,)))
    with pytest.raises(ValueError):
        replace(candidate(), location=f"1000-{1000 + len(FIRST_TEXT)}")


def test_duplicate_candidate_is_rejected():
    with pytest.raises(ValueError, match="중복"):
        OfficialEvidenceCollectionResult(COMPANY_ID, _candidates(), industry_candidates=(candidate(), candidate()))
