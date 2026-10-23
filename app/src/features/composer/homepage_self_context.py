"""검증된 홈페이지의 정확한 자기소개 인용을 원래 문맥 칸으로 운반한다.

명사구를 회사의 객관 순위나 발표일 있는 차별점으로 승격하지 않는다.
검수 전 의미칸만 정리하며 새 검수와 공개 사실 결속은 그대로 필요하다.
"""

from dataclasses import replace
import datetime as dt
from typing import Sequence
from urllib.parse import urlsplit

from src.core import clock
from src.features.composer.constants import GRADE_CONFIRMED
from src.features.composer.homepage_self_context_constants import (
    HOMEPAGE_ATTRIBUTED_INTRO_RE, HOMEPAGE_PG_BUSINESS_INTRO_RE,
    HOMEPAGE_SELF_CONTEXT_SLOT, HOMEPAGE_STATED_SLOT,
)
from src.features.composer.port import CollectedFragment, ComposedSentence
from src.shared.company_identity import exact_company_names_equivalent
from src.shared.report_evidence.constants import SOURCE_KIND_OFFICIAL_IDENTITY_VERIFIED_WEB_PAGE
from src.shared.report_evidence.identity_verified_web import (
    verified_official_web_binding_allows_public_source,
)
from src.shared.report_quality.source_identity import collected_document_identity


def _collected_today(value: str) -> bool:
    """수집 날짜와 시각을 구분해 한국 날짜로 검산한다."""
    today = clock.today_kst()
    if value == today.isoformat():
        return True
    try:
        instant = dt.datetime.fromisoformat(value)
    except ValueError:
        return False
    return bool(instant.tzinfo is not None and instant.astimezone(clock.KST).date() == today)


def normalize_homepage_self_context(
    sentence: ComposedSentence, fragments: Sequence[CollectedFragment], *, company_name: str,
) -> ComposedSentence:
    """회사에 정확히 귀속된 현재 홈페이지 소개만 self_context로 보존한다."""
    if (sentence.planned_claim_slot != HOMEPAGE_STATED_SLOT
            or sentence.grade != GRADE_CONFIRMED or sentence.verification_state != "unverified"
            or sentence.structured_claim is not None or len(sentence.citations) != 1):
        return sentence
    attribution = HOMEPAGE_ATTRIBUTED_INTRO_RE.fullmatch(sentence.text)
    if not attribution or not exact_company_names_equivalent(attribution["company"], company_name):
        return sentence
    fragment = next((item for item in fragments if item.fragment_id == sentence.citations[0]), None)
    if (fragment is None
            or fragment.formal_source_kind != SOURCE_KIND_OFFICIAL_IDENTITY_VERIFIED_WEB_PAGE
            or HOMEPAGE_SELF_CONTEXT_SLOT not in fragment.supported_claim_slots
            or not fragment.source_document_id or not fragment.document_identity
            or not fragment.document_content_sha256
            or not _collected_today(fragment.source_collected_on)):
        return sentence
    try:
        url = urlsplit(fragment.source_url)
    except ValueError:
        return sentence
    # 고객·파트너·제품 상세나 후기 페이지로 회사 전체 주어를 확장하지 않는다.
    if (url.scheme != "https" or url.path not in ("", "/") or url.query or url.fragment
            or fragment.document_identity != collected_document_identity(
                source_kind=fragment.formal_source_kind, document_id=fragment.source_document_id,
                url=fragment.source_url,
            )
            or not verified_official_web_binding_allows_public_source(
                fragment.identity_binding, company_name=company_name, source_url=fragment.source_url,
            )):
        return sentence
    source_intro = HOMEPAGE_PG_BUSINESS_INTRO_RE.fullmatch(fragment.text)
    if (not source_intro or not exact_company_names_equivalent(source_intro["company"], company_name)
            or attribution["quote"] != source_intro["rank"]):
        return sentence
    return replace(sentence, planned_claim_slot=HOMEPAGE_SELF_CONTEXT_SLOT)
