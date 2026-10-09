"""이미 검증된 뉴스 자기 인용에서 다음 배치의 사업 연결을 보충한다."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import date
from collections.abc import Mapping, Sequence

from src.features.news_intake.grounded_mapping import to_evidence_fragment
from src.features.news_intake.models import GroundedNewsExcerpt, NewsCompanyContext
from src.shared.business_challenge_context import BusinessActivityAnchor
from src.shared.report_evidence.news_business_activity import (
    encode_news_business_binding, news_business_item,
)
from src.shared.report_evidence import news_business_activity_constants as c


def extend_verified_news_business_anchors(
    company: NewsCompanyContext, excerpts: Sequence[GroundedNewsExcerpt], *,
    document_hashes: Mapping[str, str], as_of: date,
) -> NewsCompanyContext:
    """상한의 빈 자리에만 추가하며 재탐색·모델 호출은 하지 않는다."""
    if not company.company_id:
        return company
    anchors = list(company.business_anchors)
    seen = {" ".join(value.business_item.casefold().split()) for value in anchors}
    names = (company.company_name, *company.aliases)
    for excerpt in excerpts:
        if len(anchors) >= c.NEWS_BUSINESS_MAX_ANCHORS:
            break
        candidate = excerpt.candidate
        if candidate.published_on > as_of.isoformat():
            continue
        item, role = news_business_item(
            excerpt.text, company_names=names, claim_kind=excerpt.claim_kind,
            temporal_status=excerpt.temporal_status, published_on=candidate.published_on,
        )
        key = " ".join(item.casefold().split())
        document_hash = document_hashes.get(candidate.source_url, "")
        if not item or key in seen or len(document_hash) != 64:
            continue
        fragment = to_evidence_fragment(excerpt)
        location = f"기사 본문 · {fragment.fragment_id}"
        payload = {
            "version": c.NEWS_BUSINESS_BINDING_VERSION,
            "company_id": company.company_id, "company_name": company.company_name,
            "company_names": list(names), "business_item": item, "business_role": role,
            "claim_kind": excerpt.claim_kind, "temporal_status": excerpt.temporal_status,
            "quote_sha256": fragment.text_sha256, "document_sha256": document_hash,
            "document_id": fragment.document_id, "url": fragment.url,
            "published_on": fragment.published_on, "publisher": fragment.publisher,
            "title": fragment.title, "location": location,
            "span_start": excerpt.span_start, "span_end": excerpt.span_end,
        }
        binding = encode_news_business_binding(payload)
        anchor_id = c.NEWS_BUSINESS_ID_PREFIX + hashlib.sha256(binding.encode("utf-8")).hexdigest()
        anchors.append(BusinessActivityAnchor(
            anchor_id=anchor_id, company_id=company.company_id, source_id="",
            document_id=fragment.document_id, source_kind="news", source_url=fragment.url,
            location=location, exact_text=fragment.text, text_sha256=fragment.text_sha256,
            business_item=item, publisher=fragment.publisher, title=fragment.title,
            published_on=fragment.published_on, document_content_sha256=document_hash,
            identity_binding=binding,
        ))
        seen.add(key)
    return replace(company, business_anchors=tuple(anchors))
