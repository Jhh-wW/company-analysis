"""현재 회사의 공식 조각에 그대로 있는 사업 항목을 산업 조사 앵커로 만든다."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import replace
from typing import Any

from src.features.pipeline import business_activity_constants as c
from src.features.pipeline.official_news_aliases import (
    _document_matches_profile,
    _fragment_matches_location,
    _validate_collection,
)
from src.shared.company_identity import exact_company_names_equivalent
from src.shared.business_challenge_context import BusinessActivityAnchor
from src.shared.name_fragments.constants import (
    NAME_LABEL_SEPARATOR, NAME_VALUE_SEPARATOR, parse_name_location,
)
from src.shared.report_evidence.models import CollectedEvidenceDocument, EvidenceFragment
from src.shared.report_evidence.runtime_port import OfficialEvidenceCollectionResult
from src.shared.report_evidence.source_kind_policy import formal_document_is_writer_eligible


def _valid_item(item: str) -> bool:
    return bool(
        item and item not in c.BUSINESS_ACTIVITY_GENERIC_ITEMS
        and not c.BUSINESS_ACTIVITY_NONITEM_RE.search(item)
        and not c.BUSINESS_ACTIVITY_EXCLUDED_RE.search(item)
    )


def _has_other_company_reference(text: str, company_name: str) -> bool:
    for match in c.BUSINESS_ACTIVITY_COMPANY_HEADING_RE.finditer(text):
        if not exact_company_names_equivalent(match["company"], company_name):
            return True
    for match in c.BUSINESS_ACTIVITY_LEGAL_COMPANY_RE.finditer(text):
        reference = match.group().strip()
        if exact_company_names_equivalent(reference, company_name):
            continue
        # 법인명 직후 붙은 주격 조사만 현재 프로필과 정확히 같은 경우 제거한다.
        if reference.endswith(("는", "은", "가")) and exact_company_names_equivalent(
            reference[:-1], company_name,
        ):
            continue
        return True
    return False


def _business_item(fragment: EvidenceFragment, company_name: str) -> str:
    if not set(fragment.covered_slot_ids) & c.BUSINESS_ACTIVITY_SLOT_IDS:
        return ""
    named = parse_name_location(fragment.location)
    if named is not None:
        label, item = named
        # 이름 표 조각은 전체 행이 하나의 단위다. 다른 회사·미래 품목을
        # 실제 사업으로 바꾸지 않고 원문에 명시한 이름만 받는다.
        if (
            label in c.BUSINESS_ACTIVITY_NAME_LABELS
            and item in fragment.text and _valid_item(item)
            and not c.BUSINESS_ACTIVITY_EXCLUDED_RE.search(fragment.text)
            and not _has_other_company_reference(fragment.text, company_name)
        ):
            return item
        return ""
    named_subject = (
        rf"{re.escape(company_name)}(?:\s*\(이하\s*['\"‘’“”]?회사['\"‘’“”]?\))?(?:는|은|가)\s*"
        if company_name else r"(?!)"
    )
    subject = re.compile(rf"^(?:{c.BUSINESS_ACTIVITY_SELF_SUBJECT}|{named_subject})")
    for unit in c.BUSINESS_ACTIVITY_UNIT_RE.split(fragment.text):
        unit = c.BUSINESS_ACTIVITY_OVERVIEW_PREFIX_RE.sub("", unit.strip())
        if not unit or c.BUSINESS_ACTIVITY_EXCLUDED_RE.search(unit):
            continue
        matched_subject = subject.match(unit)
        if matched_subject is None:
            continue
        # 연결 문서의 다른 법인 표제어 아래 나온 '당사는'을 문장 분할로
        # 현재 회사의 자기서술로 바꾸지 않는다. 원문 전체를 기준으로 판정한다.
        original_at = fragment.text.find(unit)
        if original_at < 0 or _has_other_company_reference(
            fragment.text[:original_at + matched_subject.end()], company_name,
        ):
            continue
        business_text = unit[matched_subject.end():]
        business_text = c.BUSINESS_ACTIVITY_OVERVIEW_TAIL_RE.split(business_text)[-1].lstrip(" ,，")
        business_text = c.BUSINESS_ACTIVITY_RECIPIENT_RE.sub("", business_text)
        for pattern in (
            c.BUSINESS_ACTIVITY_DELIVERY_RE,
            c.BUSINESS_ACTIVITY_SEGMENT_COMPOSITION_RE,
            c.BUSINESS_ACTIVITY_SINGLE_MANUFACTURING_RE,
            c.BUSINESS_ACTIVITY_MANUFACTURING_RE,
            c.BUSINESS_ACTIVITY_BUSINESS_RE,
            c.BUSINESS_ACTIVITY_PRIMARY_BUSINESS_RE,
        ):
            match = pattern.match(business_text)
            if match is not None:
                item = match["item"].strip()
                if _valid_item(item) and item in fragment.text:
                    return item
                # '제품을 제조'의 제품이 너무 일반적이라고 탈락한 뒤
                # 다른 문법이 '제품을'을 사업명으로 다시 받지 않는다.
                break
    return ""


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
    *, profile: Mapping[str, Any],
) -> tuple[BusinessActivityAnchor, ...]:
    """공식 회사결속·원문결속·실제 사업명 세 조건을 만족하는 최대 세 항목."""

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
        return ()
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
            item = _business_item(fragment, company_name)
            key = " ".join(item.casefold().split())
            if not item or key in seen:
                continue
            anchors.append(_anchor(fragment, document, item))
            seen.add(key)
            if len(anchors) == c.MAX_BUSINESS_ACTIVITY_ANCHORS:
                return tuple(anchors)
    return tuple(anchors)
