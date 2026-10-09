"""검증 뉴스의 자기 사업 원문과 소비자의 출처 결속을 함께 대조한다."""

from __future__ import annotations

import hashlib
import json
import re
from datetime import date
from collections.abc import Mapping

from src.shared.report_evidence import news_business_activity_constants as c


def news_business_item(
    text: str, *, company_names: tuple[str, ...], claim_kind: str, temporal_status: str,
    published_on: str = "",
) -> tuple[str, str]:
    """명시 자기 주어와 같은 절의 실제 사업 행동만 읽는다."""
    if claim_kind not in c.NEWS_BUSINESS_KINDS or temporal_status not in c.NEWS_BUSINESS_STATES:
        return "", ""
    names = tuple(name for name in company_names if type(name) is str and name.strip())
    if not names:
        return "", ""
    subject = re.compile(r"^(?:" + "|".join(re.escape(name) for name in names) + r")(?:은|는|가)\s*")
    units = c.NEWS_BUSINESS_UNIT_RE.split(text)
    for unit_index, unit in enumerate(units):
        unit = unit.strip()
        matched = subject.match(unit)
        if matched is None:
            continue
        activity = unit[matched.end():]
        for pattern, role in (
            (c.NEWS_BUSINESS_PROVIDING_RE, "operating"),
            (c.NEWS_BUSINESS_ACQUISITION_RE, "acquired_business"),
        ):
            action = pattern.match(activity)
            if action is None:
                continue
            if role == "acquired_business" and temporal_status != "completed":
                continue
            item = action["item"].strip()
            # 계획이나 부정이 다른 문장에 있다는 이유로 실제 행동까지 지우지 않는다.
            # 같은 행동 문장 안의 취소·중단은 현재 사업으로 승격하지 않는다.
            if (item in c.NEWS_BUSINESS_GENERIC_ITEMS
                    or c.NEWS_BUSINESS_NONITEM_RE.search(item)
                    or c.NEWS_BUSINESS_NESTED_ACTOR_RE.search(item)
                    or c.NEWS_BUSINESS_INVALID_RE.search(action.group())
                    or c.NEWS_BUSINESS_INVALID_RE.search(activity[action.end():].split(",", 1)[0])):
                continue
            years = c.NEWS_BUSINESS_YEAR_RE.findall(action.group())
            if years and any(year[:-1] != published_on[:4] for year in years):
                continue
            tail = activity[action.end():] + " " + " ".join(units[unit_index + 1:])
            if c.NEWS_BUSINESS_CESSATION_RE.search(tail) and (
                c.NEWS_BUSINESS_REFERENCE_RE.search(tail) or item in tail
            ):
                continue
            return item, role
    return "", ""


def encode_news_business_binding(payload: Mapping[str, object]) -> str:
    """허용된 모든 필드를 고정 정규 JSON으로 운반한다."""
    if set(payload) != c.NEWS_BUSINESS_BINDING_FIELDS:
        raise ValueError("뉴스 사업 결속 필드가 계약과 다릅니다")
    return c.NEWS_BUSINESS_BINDING_PREFIX + json.dumps(
        dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )


def parse_news_business_binding(binding: object) -> dict:
    """변조·누락·비정규 결속은 빈 값으로 거절한다."""
    if type(binding) is not str or not binding.startswith(c.NEWS_BUSINESS_BINDING_PREFIX):
        return {}
    try:
        payload = json.loads(binding[len(c.NEWS_BUSINESS_BINDING_PREFIX):])
        if (type(payload) is not dict or set(payload) != c.NEWS_BUSINESS_BINDING_FIELDS
                or encode_news_business_binding(payload) != binding
                or payload["version"] != c.NEWS_BUSINESS_BINDING_VERSION):
            return {}
        return payload
    except (ValueError, TypeError):
        return {}


def news_business_anchor_problem(anchor: object, *, company_id: str, reference_date: str) -> str:
    """봉인 이전·이후에 같은 회사·시점·메타·원문 문법을 검증한다."""
    payload = parse_news_business_binding(getattr(anchor, "identity_binding", ""))
    if not payload:
        return "news_business_binding_invalid"
    try:
        text = anchor.exact_text
        names = payload["company_names"]
        if (not company_id or payload["company_id"] != company_id
                or anchor.company_id != company_id
                or type(names) is not list or not names
                or any(type(name) is not str or not name.strip() for name in names)
                or payload["company_name"] not in names):
            return "news_business_wrong_company"
        metadata = {
            "quote_sha256": anchor.text_sha256, "document_sha256": anchor.document_content_sha256,
            "document_id": anchor.document_id, "url": anchor.source_url,
            "published_on": anchor.published_on, "publisher": anchor.publisher,
            "title": anchor.title, "location": anchor.location, "business_item": anchor.business_item,
        }
        if any(payload[key] != value for key, value in metadata.items()):
            return "news_business_changed_metadata"
        if (hashlib.sha256(text.encode("utf-8")).hexdigest() != anchor.text_sha256
                or not re.fullmatch(r"[0-9a-f]{64}", anchor.document_content_sha256)
                or type(payload["span_start"]) is not int or type(payload["span_end"]) is not int
                or payload["span_start"] < 0
                or payload["span_end"] - payload["span_start"] != len(text)
                or date.fromisoformat(anchor.published_on) > date.fromisoformat(reference_date)):
            return "news_business_unbound_quote"
        item, role = news_business_item(
            text, company_names=tuple(names), claim_kind=payload["claim_kind"],
            temporal_status=payload["temporal_status"], published_on=payload["published_on"],
        )
        if not item or item != anchor.business_item or role != payload["business_role"]:
            return "news_business_activity_unbound"
    except (AttributeError, KeyError, ValueError, TypeError):
        return "news_business_binding_invalid"
    return ""


def news_business_fragment_matches(
    fragment: object, anchor: object, *, company_id: str, reference_date: str,
) -> bool:
    """뉴스 원문과 상태 메타를 선정 전후 같은 계약으로 대조한다."""
    if news_business_anchor_problem(anchor, company_id=company_id, reference_date=reference_date):
        return False
    payload = parse_news_business_binding(anchor.identity_binding)
    fields = {
        "text": anchor.exact_text, "formal_source_kind": "news",
        "source_document_id": anchor.document_id, "source_url": anchor.source_url,
        "document_content_sha256": anchor.document_content_sha256,
        "identity_binding": anchor.identity_binding, "location": anchor.location,
        "document_title": anchor.title, "document_date": anchor.published_on,
        "source_publisher": anchor.publisher, "news_claim_kind": payload["claim_kind"],
        "news_temporal_status": payload["temporal_status"], "news_grounded": True,
    }
    return all(getattr(fragment, key, None) == value for key, value in fields.items())
