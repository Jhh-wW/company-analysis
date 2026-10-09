"""산업 전용 원문을 회사 재분류와 작성 입력에 넣지 않는 운반 경계."""

import hashlib
from dataclasses import replace

from src.features.pipeline.evidence_reclassify_step import (
    ReclassifySource, _candidate_paragraphs, attach_reclassify_source, plain_official_evidence,
)
from src.features.pipeline.official_industry_context import prepare_official_industry_fallback
from src.features.pipeline.tests.test_official_industry_context import bound_input, PROFILE
from src.shared.report_evidence.industry_candidates import OfficialIndustryCandidateEvidence
from src.shared.report_evidence.industry_candidates import OfficialIndustrySupplement
from src.shared.report_evidence.models import DocumentTextRange


def collection_with_discovery():
    evidence, selected = bound_input()
    document = next(g.documents[0] for g in evidence.candidates if g.fragments)
    text = "2026년 현재 국내 정밀부품 시장에서는 공급 부족이 발생하고 있습니다."
    digest = hashlib.sha256(text.encode()).hexdigest()
    document = replace(document, exact_evidence_hashes=(digest,),
                       usable_ranges=(DocumentTextRange(1000, 1000 + len(text)),))
    candidate = OfficialIndustryCandidateEvidence(evidence.company_id, "synthetic-industry", document,
        f"1000-{1000 + len(text)}", digest, text)
    return replace(evidence, industry_candidates=(candidate,)), selected, candidate


def test_industry_original_is_available_only_to_late_callback_and_preserved_on_reclassification():
    evidence, selected, candidate = collection_with_discovery()
    calls = []
    anchors, fallback = prepare_official_industry_fallback(
        collection=evidence, profile=PROFILE, company_id=evidence.company_id,
        reference_date="2026-10-08", anchors=(), analyze=lambda *_: None,
        available_calls=lambda: 1, diagnostics=[], collect=lambda **kwargs: calls.append(kwargs) or (),
    )
    assert anchors and fallback((selected,)) == ()
    assert any(pair[0] == candidate for pair in calls[0]["candidates"])
    assert not any(candidate.fragment_id == f.fragment_id for g in evidence.candidates for f in g.fragments)
    attached = attach_reclassify_source(evidence, company_type="listed", dart_envelope={}, wide_envelope={})
    assert plain_official_evidence(attached).industry_candidates == (candidate,)


def test_industry_only_raw_candidate_is_not_sent_to_company_reclassification():
    source = ReclassifySource("listed", {"unclassified_fragments": [
        {"fragment_id": "industry", "reason_codes": ["no_signal", "official_industry_discovery"]},
        {"fragment_id": "ordinary", "reason_codes": ["no_signal"]},
    ], "fragments": [{"fragment_id": "classified", "score_millis": 250, "reason_codes": ["keyword_hit"]}]}, {})
    assert [row["paragraph_id"] for row in _candidate_paragraphs(source)] == ["ordinary", "classified"]


def test_discovery_cannot_run_with_missing_selected_anchor_or_profile():
    evidence, selected, _ = collection_with_discovery()
    calls = []
    kwargs = dict(collection=evidence, profile=PROFILE, company_id=evidence.company_id,
                  reference_date="2026-10-08", anchors=(), analyze=lambda *_: None,
                  available_calls=lambda: 1, diagnostics=[], collect=lambda **kw: calls.append(kw) or ())
    _, fallback = prepare_official_industry_fallback(**kwargs)
    assert fallback(()) == () and not calls
    assert prepare_official_industry_fallback(**{**kwargs, "profile": None})[1] is None


def test_late_callback_returns_only_adopted_extra_original_for_final_source_registration():
    import json
    evidence, selected, candidate = collection_with_discovery()
    requests = []

    def analyze(prompt, schema, max_tokens):
        payload = json.loads(prompt.rsplit("\n", 1)[1])
        requests.append(payload)
        material = payload["materials"][0]
        return {"assessments": [{
            "fragment_id": material["fragment_id"], "anchor_id": payload["anchors"][0]["anchor_id"],
            "status": "proposed", "sentence_ids": [material["sentences"][0]["id"]],
            "current_problem": True, "same_business": True, "geography_supported": True, "period_supported": True,
            "industry": "정밀부품 시장", "problem": "공급 부족", "geography": "domestic",
            "geography_detail": "국내", "geography_evidence": "국내", "applicability_quote": "정밀부품 시장",
            "observation_period": "2026년 현재",
        }]}

    _, fallback = prepare_official_industry_fallback(
        collection=evidence, profile=PROFILE, company_id=evidence.company_id, reference_date="2026-10-08",
        anchors=(), analyze=analyze, available_calls=lambda: 1, diagnostics=[],
    )
    result = fallback((selected,))
    assert type(result) is OfficialIndustrySupplement
    assert result.candidates == (candidate,) and len(result.problems) == len(requests) == 1
    assert result.problems[0].exact_text == candidate.text
    assert fallback((selected,)) == ()
