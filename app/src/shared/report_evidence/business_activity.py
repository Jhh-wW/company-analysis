"""근거 대표 선택과 산업 조사가 함께 사용하는 현재 사업의 명시 원문 판정."""

from __future__ import annotations

import re

from src.shared.company_identity import exact_company_names_equivalent
from src.shared.name_fragments.constants import parse_name_location
from src.shared.report_evidence import business_activity_constants as c
from src.shared.report_evidence.models import EvidenceFragment
from src.shared.report_evidence.source_context import parse_source_context


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
        if reference.endswith(("는", "은", "가")) and exact_company_names_equivalent(
            reference[:-1], company_name,
        ):
            continue
        return True
    return False


def current_business_item(fragment: EvidenceFragment, company_name: str) -> str:
    """회사 자신의 현재 구체 사업이 명시된 경우에만 원문 속 항목을 돌려준다."""
    if not company_name.strip():
        return ""
    context = parse_source_context(getattr(fragment, "source_context_json", ""))
    actor = context.get("actor", "")
    if context.get("origin") == "company_heading" and actor.endswith("의"):
        actor = actor[:-1]
    if context and (
        not exact_company_names_equivalent(actor, company_name)
        or not exact_company_names_equivalent(context["document_actor"], company_name)
        or context["status"]
    ):
        return ""
    business_slots = set(fragment.covered_slot_ids) & c.BUSINESS_ACTIVITY_SLOT_IDS
    operating_slots = set(fragment.covered_slot_ids) & c.BUSINESS_ACTIVITY_OPERATING_SLOT_IDS
    if not business_slots and not operating_slots:
        return ""
    operating_only = bool(operating_slots and not business_slots)
    named = parse_name_location(fragment.location)
    if named is not None:
        if operating_only:
            return ""  # 운영 칸의 제품명만으로 현재 생산·제공 관계를 확정하지 않는다.
        label, item = named
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
    )
    subject = re.compile(rf"^(?:{c.BUSINESS_ACTIVITY_SELF_SUBJECT}|{named_subject})")
    for unit in c.BUSINESS_ACTIVITY_UNIT_RE.split(fragment.text):
        unit = c.BUSINESS_ACTIVITY_OVERVIEW_PREFIX_RE.sub("", unit.strip())
        if not unit or c.BUSINESS_ACTIVITY_EXCLUDED_RE.search(unit):
            continue
        definition = c.BUSINESS_ACTIVITY_DEFINITION_PREFIX_RE.match(unit)
        if definition is not None:
            unit = unit[definition.end():]
        matched_subject = subject.match(unit)
        if matched_subject is None:
            continue
        # 다른 법인의 표제 아래 '당사는'을 대상 회사의 자기서술로 바꾸지 않는다.
        original_at = fragment.text.find(unit)
        if original_at < 0 or _has_other_company_reference(
            fragment.text[:original_at + matched_subject.end()], company_name,
        ):
            continue
        business_text = unit[matched_subject.end():]
        business_text = c.BUSINESS_ACTIVITY_OVERVIEW_TAIL_RE.split(business_text)[-1].lstrip(" ,，")
        if operating_only and c.BUSINESS_ACTIVITY_OPERATING_INTERNAL_RE.search(business_text):
            continue
        if operating_only and c.BUSINESS_ACTIVITY_OPERATING_NESTED_ACTOR_RE.match(business_text):
            continue
        business_text = c.BUSINESS_ACTIVITY_RECIPIENT_RE.sub("", business_text)
        business_text = c.BUSINESS_ACTIVITY_PRODUCTION_FOCUS_RE.sub("", business_text)
        patterns = (
            c.BUSINESS_ACTIVITY_COMPOUND_PRODUCTION_RE,
            c.BUSINESS_ACTIVITY_DELIVERY_RE,
            c.BUSINESS_ACTIVITY_SEGMENT_COMPOSITION_RE,
            c.BUSINESS_ACTIVITY_SINGLE_MANUFACTURING_RE,
            c.BUSINESS_ACTIVITY_MANUFACTURING_RE,
            c.BUSINESS_ACTIVITY_BUSINESS_RE,
            c.BUSINESS_ACTIVITY_PRIMARY_BUSINESS_RE,
        ) if not operating_only else (
            c.BUSINESS_ACTIVITY_COMPOUND_PRODUCTION_RE,
            c.BUSINESS_ACTIVITY_OPERATING_DELIVERY_RE,
        )
        for pattern in patterns:
            match = pattern.match(business_text)
            if match is not None:
                item = match["item"].strip()
                if _valid_item(item) and item in fragment.text:
                    return item
                # '제품'이 일반적이라 제외된 뒤 다른 문법으로 다시 받지 않는다.
                break
    return ""
