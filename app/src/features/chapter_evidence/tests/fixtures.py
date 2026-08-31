"""오프라인 종단 시나리오용 Mapping 기반 fixture 빌더.

외부 파일·네트워크를 쓰지 않는다. 모든 문서·조각·조회 기록은 딕셔너리
리터럴로 만들며, ``normalize.py``가 계약 dataclass로 바꾼다.
"""

from __future__ import annotations

import hashlib

from src.shared.report_evidence.constants import (
    CollectionState,
    SourceRequirement,
    SourceTier,
)
from src.shared.report_evidence.models import InjectedSlotFacts
from src.shared.report_evidence.policy import (
    REQUIRED_EVIDENCE_SECTION_IDS,
    collector_slots_for,
    injected_slots_for,
)


def sha256_of(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def make_document(
    *,
    company_id: str,
    document_id: str,
    source_kind: str,
    title: str = "예시 문서",
    source_tier: str = SourceTier.TIER_1_OFFICIAL.value,
    requirement: str = SourceRequirement.REQUIRED.value,
) -> dict[str, object]:
    return {
        "company_id": company_id,
        "document_id": document_id,
        "canonical_url": f"https://example.com/{document_id}",
        "source_tier": source_tier,
        "source_kind": source_kind,
        "publisher": f"{company_id}-발행처",
        "title": title,
        "published_on": "2026-03-01",
        "collected_at": "2026-08-31T00:00:00+00:00",
        "content_sha256": sha256_of(f"content:{document_id}"),
        "identity_binding": f"binding:{document_id}",
        "usable_ranges": [{"start": 0, "end": 800}],
        "collector_version": "collector-v1",
        "parser_version": "parser-v1",
        "requirement": requirement,
    }


def make_fragment(
    *,
    fragment_id: str,
    document_id: str,
    section_id: str,
    slot_id: str,
    text: str,
    score_millis: int = 800,
    reason_codes: tuple[str, ...] = ("official_direct_statement",),
) -> dict[str, object]:
    return {
        "fragment_id": fragment_id,
        "document_id": document_id,
        "location": "본문 1문단",
        "text_sha256": sha256_of(text),
        "text": text,
        "section_id": section_id,
        "slot_id": slot_id,
        "score_millis": score_millis,
        "reason_codes": reason_codes,
    }


def make_attempt(
    *,
    attempt_id: str,
    source_kind: str,
    slot_ids: tuple[str, ...],
    state: str,
    reason_code: str,
    requirement: str = SourceRequirement.REQUIRED.value,
) -> dict[str, object]:
    return {
        "attempt_id": attempt_id,
        "source_kind": source_kind,
        "requirement": requirement,
        "state": state,
        "slot_ids": slot_ids,
        "reason_code": reason_code,
    }


def build_filled_channel(
    *,
    section_id: str,
    source_kind: str,
    document_id: str,
    state: str = CollectionState.OK.value,
    text_by_slot: dict[str, str] | None = None,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """이 장의 수집 슬롯 전부를 한 채널(문서 하나)로 채운 조각·시도를 만든다."""

    slots = collector_slots_for(section_id)
    text_by_slot = text_by_slot or {}
    fragments = [
        make_fragment(
            fragment_id=f"frag-{section_id}-{slot_id.split(':')[-1]}-{document_id}",
            document_id=document_id,
            section_id=section_id,
            slot_id=slot_id,
            text=text_by_slot.get(
                slot_id, f"{section_id} {slot_id} 관련 공식 원문 서술({document_id})."
            ),
        )
        for slot_id in slots
    ]
    attempt = make_attempt(
        attempt_id=f"attempt-{section_id}-{document_id}",
        source_kind=source_kind,
        slot_ids=slots,
        state=state,
        reason_code=f"{source_kind}_{state.lower()}",
    )
    return fragments, [attempt]


def build_unfilled_channel(
    *, section_id: str, source_kind: str, state: str, reason_code: str
) -> list[dict[str, object]]:
    """이 장의 수집 슬롯을 하나도 채우지 못한 조회 기록만 만든다(조각 없음)."""

    return [
        make_attempt(
            attempt_id=f"attempt-{section_id}-unfilled-{state.lower()}",
            source_kind=source_kind,
            slot_ids=collector_slots_for(section_id),
            state=state,
            reason_code=reason_code,
        )
    ]


def injected_facts_for(section_id: str) -> tuple[InjectedSlotFacts, ...]:
    """Codex 구조화 검증기가 채웠을 주입 사실을 가짜로 만든다(시험 전용)."""

    return tuple(
        InjectedSlotFacts(slot_id=slot_id, fact_ids=(f"fact-{slot_id}",))
        for slot_id in injected_slots_for(section_id)
    )


DART_SECTIONS: tuple[str, ...] = (
    "business_model",
    "past_changes",
    "current_challenges",
    "operations_partners",
)
OFFICIAL_ONLY_SECTIONS: tuple[str, ...] = (
    "identity",
    "portfolio",
    "future_strategy",
    "culture",
    "competitive_position",
)
assert set(DART_SECTIONS) | set(OFFICIAL_ONLY_SECTIONS) == set(
    REQUIRED_EVIDENCE_SECTION_IDS
)


def build_listed_fixture(*, company_id: str = "corp-listed") -> dict[str, list]:
    """시나리오 2 — 사업보고서형: 공시 하나로 아홉 장 전부 커버."""

    document_id = "dart-business-report-01"
    documents = [
        make_document(
            company_id=company_id,
            document_id=document_id,
            source_kind="dart_business_report",
            title="사업보고서",
        )
    ]
    fragments: list[dict[str, object]] = []
    attempts: list[dict[str, object]] = []
    for section_id in REQUIRED_EVIDENCE_SECTION_IDS:
        section_fragments, section_attempts = build_filled_channel(
            section_id=section_id,
            source_kind="dart_business_report",
            document_id=document_id,
        )
        fragments.extend(section_fragments)
        attempts.extend(section_attempts)
    return {"documents": documents, "fragments": fragments, "attempts": attempts}


def build_financial_fixture(*, company_id: str = "corp-financial") -> dict[str, list]:
    """시나리오 3 — 금융형: 「매출액」 대신 이자수익 등 대체 지표 원문."""

    fixture = build_listed_fixture(company_id=company_id)
    document_id = "dart-business-report-01"
    interest_income_text = (
        "당기 이자수익은 전년 대비 증가했으며 대손충당금 적립 후에도 "
        "순이자마진이 개선됐습니다."
    )
    completed_execution_slot = "past_changes:completed_execution"
    replacement_fragment = make_fragment(
        fragment_id=f"frag-past_changes-completed_execution-{document_id}-financial",
        document_id=document_id,
        section_id="past_changes",
        slot_id=completed_execution_slot,
        text=interest_income_text,
    )
    fixture["fragments"] = [
        fragment
        for fragment in fixture["fragments"]
        if not (
            fragment["section_id"] == "past_changes"
            and fragment["slot_id"] == completed_execution_slot
        )
    ] + [replacement_fragment]
    return fixture


def build_wisely_type_fixture(*, company_id: str = "corp-wisely") -> dict[str, list]:
    """시나리오 1 — 와이즐리형: 감사보고서 1건(빈약) + 공식 웹 여러 호스트."""

    dart_document_id = "dart-audit-report-01"
    documents = [
        make_document(
            company_id=company_id,
            document_id=dart_document_id,
            source_kind="dart_audit_report",
            title="감사보고서",
        ),
        make_document(
            company_id=company_id,
            document_id="official-shop-01",
            source_kind="official_shop",
            title="자사몰 회사소개",
        ),
        make_document(
            company_id=company_id,
            document_id="official-careers-01",
            source_kind="official_careers",
            title="채용 페이지",
        ),
        make_document(
            company_id=company_id,
            document_id="official-blog-01",
            source_kind="official_blog",
            title="공식 블로그",
        ),
    ]
    fragments: list[dict[str, object]] = []
    attempts: list[dict[str, object]] = []

    for section_id in DART_SECTIONS:
        section_fragments, section_attempts = build_filled_channel(
            section_id=section_id,
            source_kind="dart_audit_report",
            document_id=dart_document_id,
        )
        fragments.extend(section_fragments)
        attempts.extend(section_attempts)

    official_channel_by_section = {
        "identity": ("official_shop", "official-shop-01"),
        "portfolio": ("official_shop", "official-shop-01"),
        "future_strategy": ("official_blog", "official-blog-01"),
        "culture": ("official_careers", "official-careers-01"),
        "competitive_position": ("official_blog", "official-blog-01"),
    }
    for section_id in OFFICIAL_ONLY_SECTIONS:
        source_kind, document_id = official_channel_by_section[section_id]
        section_fragments, section_attempts = build_filled_channel(
            section_id=section_id, source_kind=source_kind, document_id=document_id
        )
        fragments.extend(section_fragments)
        attempts.extend(section_attempts)

    return {"documents": documents, "fragments": fragments, "attempts": attempts}


def build_no_homepage_fixture(*, company_id: str = "corp-no-homepage") -> dict[str, list]:
    """시나리오 4 — 홈페이지 없음: 웹 REQUIRED 경로가 정상 확인 후 MISSING."""

    dart_document_id = "dart-audit-report-01"
    documents = [
        make_document(
            company_id=company_id,
            document_id=dart_document_id,
            source_kind="dart_audit_report",
            title="감사보고서",
        )
    ]
    fragments: list[dict[str, object]] = []
    attempts: list[dict[str, object]] = []
    for section_id in DART_SECTIONS:
        section_fragments, section_attempts = build_filled_channel(
            section_id=section_id,
            source_kind="dart_audit_report",
            document_id=dart_document_id,
        )
        fragments.extend(section_fragments)
        attempts.extend(section_attempts)
    for section_id in OFFICIAL_ONLY_SECTIONS:
        attempts.extend(
            build_unfilled_channel(
                section_id=section_id,
                source_kind="official_homepage",
                state=CollectionState.MISSING.value,
                reason_code="official_homepage_dns_not_found",
            )
        )
    return {"documents": documents, "fragments": fragments, "attempts": attempts}


def build_javascript_render_failure_fixture(
    *, company_id: str = "corp-js-render"
) -> dict[str, list]:
    """시나리오 5 — 자바스크립트형: 웹 본문 추출 실패(FAILED)."""

    fixture = build_no_homepage_fixture(company_id=company_id)
    fixture["attempts"] = [
        attempt
        for attempt in fixture["attempts"]
        if attempt["source_kind"] != "official_homepage"
    ]
    for section_id in OFFICIAL_ONLY_SECTIONS:
        fixture["attempts"].extend(
            build_unfilled_channel(
                section_id=section_id,
                source_kind="official_homepage",
                state=CollectionState.FAILED.value,
                reason_code="official_homepage_render_timeout",
            )
        )
    return fixture
