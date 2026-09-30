"""현재 회사의 공식 조각에 그대로 있는 사업 항목을 산업 조사 앵커로 만든다."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from src.features.pipeline import business_activity_constants as c
from src.features.pipeline.official_news_aliases import (
    _document_matches_profile,
    _fragment_matches_location,
    _validate_collection,
)
from src.shared.business_challenge_context import BusinessActivityAnchor
from src.shared.name_fragments.constants import (
    NAME_LABEL_SEPARATOR, NAME_VALUE_SEPARATOR, parse_name_location,
)
from src.shared.report_evidence.models import CollectedEvidenceDocument, EvidenceFragment
from src.shared.report_evidence.runtime_port import OfficialEvidenceCollectionResult
from src.shared.report_evidence.source_kind_policy import formal_document_is_writer_eligible
from src.shared.report_evidence.business_activity import current_business_item


def _anchor(fragment: EvidenceFragment, document: CollectedEvidenceDocument, item: str) -> BusinessActivityAnchor:
    return BusinessActivityAnchor(
        anchor_id=fragment.fragment_id, company_id=fragment.company_id, source_id="",
        document_id=document.document_id, source_kind=document.source_kind,
        source_url=document.canonical_url, location=fragment.location,
        exact_text=fragment.text, text_sha256=fragment.text_sha256, business_item=item,
        publisher=document.publisher, title=document.title, published_on=document.published_on,
        document_content_sha256=document.content_sha256, identity_binding=document.identity_binding,
    )


def _matches_location(document: CollectedEvidenceDocument, fragment: EvidenceFragment) -> bool:
    named = parse_name_location(fragment.location)
    if named is None:
        return _fragment_matches_location(document, fragment)
    label, _item = named
    marker = f"{NAME_LABEL_SEPARATOR}{label}{NAME_VALUE_SEPARATOR}"
    source_location = fragment.location.rpartition(marker)[0]
    return bool(source_location) and _fragment_matches_location(
        document, replace(fragment, location=source_location),
    )


def build_business_activity_anchors(
    official_evidence: OfficialEvidenceCollectionResult | None,
    *, profile: Mapping[str, Any], diagnostics: dict[str, Any] | None = None,
) -> tuple[BusinessActivityAnchor, ...]:
    """공식 회사결속·원문결속·실제 사업명 세 조건을 만족하는 최대 세 항목."""

    counts = {"selected_fragments": 0, "verified_fragments": 0,
              "current_business_matches": 0, "accepted_anchors": 0}
    if diagnostics is not None:
        diagnostics.update(counts)
        diagnostics["status"] = "company_binding_unavailable"
    company_id = str(profile.get("corp_code") or "").strip()
    company_name = str(profile.get("corp_name") or "").strip()
    if (
        type(official_evidence) is not OfficialEvidenceCollectionResult
        or not company_id or not company_name or official_evidence.company_id != company_id
    ):
        return ()
    try:
        _validate_collection(official_evidence)
    except (ValueError, TypeError, AttributeError):
        if diagnostics is not None:
            diagnostics["status"] = "collection_contract_invalid"
        return ()
    if diagnostics is not None:
        diagnostics["status"] = "checked_selected_candidates"
    anchors: list[BusinessActivityAnchor] = []
    seen: set[str] = set()
    candidates = sorted(
        (candidate for candidate in official_evidence.candidates
         if candidate.section_id in c.BUSINESS_ACTIVITY_SECTION_PRIORITY),
        key=lambda candidate: c.BUSINESS_ACTIVITY_SECTION_PRIORITY[candidate.section_id],
    )
    for candidate in candidates:
        documents = {document.document_id: document for document in candidate.documents}
        for fragment in candidate.fragments:
            counts["selected_fragments"] += 1
            if diagnostics is not None:
                diagnostics.update(counts)
            document = documents.get(fragment.document_id)
            if (
                document is None or document.company_id != company_id
                or fragment.company_id != company_id
                or not formal_document_is_writer_eligible(document)
                or not _document_matches_profile(document, profile)
                or fragment.text_sha256 not in document.exact_evidence_hashes
                or hashlib.sha256(fragment.text.encode("utf-8")).hexdigest() != fragment.text_sha256
                or not _matches_location(document, fragment)
            ):
                continue
            counts["verified_fragments"] += 1
            item = current_business_item(fragment, company_name)
            counts["current_business_matches"] += bool(item)
            if diagnostics is not None:
                diagnostics.update(counts)
            key = " ".join(item.casefold().split())
            if not item or key in seen:
                continue
            anchors.append(_anchor(fragment, document, item))
            counts["accepted_anchors"] = len(anchors)
            if diagnostics is not None:
                diagnostics.update(counts)
            seen.add(key)
            if len(anchors) == c.MAX_BUSINESS_ACTIVITY_ANCHORS:
                return tuple(anchors)
    return tuple(anchors)
