"""검증된 같은 실행의 공식 자료만 마지막 선택 호출에 연결한다."""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping
from typing import Any

from src.core.official_industry_context_adapter import collect_official_industry_context
from src.features.pipeline.business_activity_anchors import (
    _matches_location, build_business_activity_anchors,
)
from src.features.pipeline.official_news_aliases import _document_matches_profile, _validate_collection
from src.features.pipeline import official_industry_context_constants as c
from src.shared.business_challenge_context import BusinessActivityAnchor
from src.shared.report_evidence.runtime_port import OfficialEvidenceCollectionResult
from src.shared.report_evidence.source_kind_policy import formal_document_is_writer_eligible


def _matches_selected(fragment: object, document: object, selected: object) -> bool:
    return (
        not any(getattr(selected, field, "") for field in c.SUBITEM_FIELDS)
        and all(getattr(selected, left, None) == getattr(document, right)
                for left, right in (*c.SELECTED_DOCUMENT_FIELDS, *c.SELECTED_PROVENANCE_FIELDS))
        and all(getattr(selected, field, None) == getattr(fragment, field)
                for field in c.SELECTED_FRAGMENT_FIELDS)
    )


def prepare_official_industry_fallback(
    *, collection: OfficialEvidenceCollectionResult | None,
    profile: Mapping[str, Any] | None, company_id: str, reference_date: str,
    anchors: tuple[BusinessActivityAnchor, ...], analyze: Callable,
    available_calls: Callable[[], int], diagnostics: list[dict],
    budget_exceptions: tuple[type[Exception], ...] = (),
    collect: Callable = collect_official_industry_context,
) -> tuple[tuple[BusinessActivityAnchor, ...], Callable | None]:
    """누락 신원은 호출하지 않으며, 선택 후 exact 원문에 한 번만 분석을 허용한다."""
    if (type(collection) is not OfficialEvidenceCollectionResult
            or not isinstance(profile, Mapping)
            or profile.get("corp_code") != company_id
            or not profile.get("corp_name") or profile.get("status", "000") != "000"):
        return anchors, None
    try:
        _validate_collection(collection)
        if collection.company_id != company_id:
            return anchors, None
        validated_anchors = build_business_activity_anchors(collection, profile=profile)
        # 기존 뉴스 앵커도 현재 공식 원문으로 재확인하며 순서를 바꾸지 않는다.
        usable_anchors = tuple(value for value in (anchors or validated_anchors)
                               if value in validated_anchors)
        originals = []
        seen = set()
        for group in collection.candidates:
            documents = {value.document_id: value for value in group.documents}
            for fragment in group.fragments:
                document = documents.get(fragment.document_id)
                key = (fragment.document_id, fragment.location, fragment.text_sha256)
                if (document is None or key in seen
                        or fragment.company_id != company_id or document.company_id != company_id
                        or fragment.range_index >= 0
                        or any(getattr(fragment, field) for field in c.SUBITEM_FIELDS)
                        or not formal_document_is_writer_eligible(document)
                        or not _document_matches_profile(document, profile)
                        or not _matches_location(document, fragment)
                        or fragment.text_sha256 not in document.exact_evidence_hashes
                        or hashlib.sha256(fragment.text.encode("utf-8")).hexdigest() != fragment.text_sha256):
                    continue
                originals.append((fragment, document))
                seen.add(key)
    except (ValueError, TypeError, KeyError, AttributeError):
        return anchors, None
    if not usable_anchors or not originals:
        return anchors, None
    attempted = False

    def fallback(selected: tuple) -> tuple:
        nonlocal attempted
        if attempted:
            return ()
        attempted = True
        observation = {"step": c.OFFICIAL_INDUSTRY_DIAGNOSTIC_STEP, "상태": "후보부족"}
        diagnostics.append(observation)
        candidates = tuple((fragment, document) for fragment, document in originals
                           if any(_matches_selected(fragment, document, value) for value in selected))
        selected_anchors = tuple(anchor for anchor in usable_anchors if any(
            fragment.text == anchor.exact_text and fragment.location == anchor.location
            and document.document_id == anchor.document_id
            for fragment, document in candidates
        ))
        if not candidates or not selected_anchors:
            return ()
        if available_calls() <= 0:
            observation["상태"] = "호출여유없음"
            return ()
        calls = 0

        def analyze_once(*args):
            nonlocal calls
            if calls:
                raise ValueError("공식 산업 보조 분석은 한 번만 호출할 수 있습니다")
            calls += 1
            return analyze(*args)

        try:
            result = collect(candidates=candidates, anchors=selected_anchors,
                             company_id=company_id, reference_date=reference_date,
                             analyze=analyze_once, diagnostics=observation)
        except budget_exceptions:
            observation["상태"] = "요청예산부족"
            return ()
        observation["호출수"] = calls
        return tuple(result)

    return usable_anchors, fallback
