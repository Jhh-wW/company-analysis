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

# 이 fixture는 생산 정책 함수를 import하지 않는다. 아래 표는 제품이 답해야
# 하는 질문을 시험이 독립적으로 고정한 값이다. 생산 정책에서 슬롯을 실수로
# 빼거나 이름을 바꾸면 fixture까지 자동으로 따라 바뀌지 않고 gate 시험이
# 깨져야 한다(구현 자기복제 방지).
_FROZEN_SECTION_IDS: tuple[str, ...] = (
    "identity",
    "business_model",
    "portfolio",
    "past_changes",
    "current_challenges",
    "future_strategy",
    "operations_partners",
    "culture",
    "competitive_position",
)

_FROZEN_COLLECTOR_SLOTS: dict[str, tuple[str, ...]] = {
    "identity": ("identity:corporate_identity", "identity:business_definition"),
    "business_model": (
        "business_model:revenue_model",
        "business_model:customer_type",
        "business_model:value_exchange",
    ),
    "portfolio": ("portfolio:product_role", "portfolio:revenue_link"),
    "past_changes": ("past_changes:completed_execution",),
    "current_challenges": (
        "current_challenges:issue",
        "current_challenges:response",
    ),
    "future_strategy": (
        "future_strategy:stated_plan",
        "future_strategy:plan_status",
    ),
    "operations_partners": (
        "operations_partners:value_chain",
        "operations_partners:operating_role",
    ),
    "culture": ("culture:work_principle", "culture:verified_case"),
    "competitive_position": ("competitive_position:self_context",),
}

_FROZEN_INJECTED_SLOTS: dict[str, tuple[str, ...]] = {
    "past_changes": ("past_changes:historical_performance",),
    "competitive_position": (
        "competitive_position:comparison_target",
        "competitive_position:comparison_metric",
        "competitive_position:comparison_basis",
        "competitive_position:comparison_judgment",
        "competitive_position:limitation",
    ),
}


def _collector_slots_for(section_id: str) -> tuple[str, ...]:
    return _FROZEN_COLLECTOR_SLOTS[section_id]


def _injected_slots_for(section_id: str) -> tuple[str, ...]:
    return _FROZEN_INJECTED_SLOTS.get(section_id, ())


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
    exact_evidence_hashes: tuple[str, ...] | None = None,
) -> dict[str, object]:
    """generation=7 문서를 만든다.

    ``exact_evidence_hashes``를 안 주면 이 문서 자체만으로도 유효하도록
    자리표시자 해시 하나를 채운다. 실제 조각과 결속시키려면
    ``_bind_exact_evidence_hashes``로 이 문서가 속한 fixture의 fragments를
    역산해 덮어써야 한다 — build_*_fixture 함수들이 마지막 단계에서 이를
    수행한다.
    """

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
        "exact_evidence_hashes": (
            exact_evidence_hashes
            if exact_evidence_hashes is not None
            else (sha256_of(f"placeholder-evidence:{document_id}"),)
        ),
        "identity_binding": f"binding:{document_id}",
        "usable_ranges": [{"start": 0, "end": 800}],
        "collector_version": "collector-v1",
        "parser_version": "parser-v1",
        "requirement": requirement,
    }


def _bind_exact_evidence_hashes(
    documents: list[dict[str, object]], fragments: list[dict[str, object]]
) -> list[dict[str, object]]:
    """문서가 실제로 내보내는 조각 해시 목록을 조각들로부터 역산해 채운다.

    generation=7 계약은 문서마다 자신이 내보내는 조각의 text_sha256 전체를
    ``exact_evidence_hashes``로 선언하게 하고, 그 목록에 없는 해시를 가진
    조각은 (select.py의 결속 확인에서) 후보에서 제외된다. fixture는 문서를
    먼저 만들고 조각을 나중에 만들거나 나중에 조각을 바꿔치기하는 경우가
    있어, 매번 손으로 해시를 맞추는 대신 마지막에 한 번 역산해 붙인다.
    이 문서를 참조하는 조각이 하나도 없으면(=계약이 빈 목록을 거부하는
    상황) fixture 자체가 잘못됐다는 뜻이므로 조용히 넘어가지 않고 즉시
    실패시킨다.
    """

    hashes_by_document: dict[str, list[str]] = {}
    for fragment in fragments:
        document_id = str(fragment["document_id"])
        text_sha256 = str(fragment["text_sha256"])
        bucket = hashes_by_document.setdefault(document_id, [])
        if text_sha256 not in bucket:
            bucket.append(text_sha256)

    bound_documents: list[dict[str, object]] = []
    for document in documents:
        document_id = str(document["document_id"])
        hashes = hashes_by_document.get(document_id)
        if not hashes:
            raise AssertionError(
                f"fixture 문서 {document_id} 를 참조하는 조각이 없어 "
                "exact_evidence_hashes를 채울 수 없습니다"
            )
        bound_documents.append({**document, "exact_evidence_hashes": tuple(hashes)})
    return bound_documents


def make_fragment(
    *,
    company_id: str,
    fragment_id: str,
    document_id: str,
    section_id: str,
    slot_id: str,
    text: str,
    score_millis: int = 800,
    reason_codes: tuple[str, ...] = ("official_direct_statement",),
) -> dict[str, object]:
    """generation=8 조각을 만든다.

    ``company_id``는 필수다(기본값 없음) — make_document와 마찬가지로
    호출부가 명시하게 강제해, 대상 회사 값으로 조용히 «보정»하는 실수를
    막는다.
    """

    return {
        "company_id": company_id,
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
    company_id: str,
    attempt_id: str,
    source_kind: str,
    slot_ids: tuple[str, ...],
    state: str,
    reason_code: str,
    requirement: str = SourceRequirement.REQUIRED.value,
) -> dict[str, object]:
    """generation=8 조회 기록을 만든다. ``company_id``는 필수다(기본값 없음)."""

    return {
        "company_id": company_id,
        "attempt_id": attempt_id,
        "source_kind": source_kind,
        "requirement": requirement,
        "state": state,
        "slot_ids": slot_ids,
        "reason_code": reason_code,
    }


def build_filled_channel(
    *,
    company_id: str,
    section_id: str,
    source_kind: str,
    document_id: str,
    state: str = CollectionState.OK.value,
    text_by_slot: dict[str, str] | None = None,
) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    """이 장의 수집 슬롯 전부를 한 채널(문서 하나)로 채운 조각·시도를 만든다."""

    slots = _collector_slots_for(section_id)
    text_by_slot = text_by_slot or {}
    fragments = [
        make_fragment(
            company_id=company_id,
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
        company_id=company_id,
        attempt_id=f"attempt-{section_id}-{document_id}",
        source_kind=source_kind,
        slot_ids=slots,
        state=state,
        reason_code=f"{source_kind}_{state.lower()}",
    )
    return fragments, [attempt]


def build_unfilled_channel(
    *, company_id: str, section_id: str, source_kind: str, state: str, reason_code: str
) -> list[dict[str, object]]:
    """이 장의 수집 슬롯을 하나도 채우지 못한 조회 기록만 만든다(조각 없음)."""

    return [
        make_attempt(
            company_id=company_id,
            attempt_id=f"attempt-{section_id}-unfilled-{state.lower()}",
            source_kind=source_kind,
            slot_ids=_collector_slots_for(section_id),
            state=state,
            reason_code=reason_code,
        )
    ]


def injected_facts_for(section_id: str) -> tuple[InjectedSlotFacts, ...]:
    """구조화 검증기가 채웠을 주입 사실을 가짜로 만든다(시험 전용)."""

    return tuple(
        InjectedSlotFacts(slot_id=slot_id, fact_ids=(f"fact-{slot_id}",))
        for slot_id in _injected_slots_for(section_id)
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
    _FROZEN_SECTION_IDS
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
    for section_id in _FROZEN_SECTION_IDS:
        section_fragments, section_attempts = build_filled_channel(
            company_id=company_id,
            section_id=section_id,
            source_kind="dart_business_report",
            document_id=document_id,
        )
        fragments.extend(section_fragments)
        attempts.extend(section_attempts)
    return {
        "documents": _bind_exact_evidence_hashes(documents, fragments),
        "fragments": fragments,
        "attempts": attempts,
    }


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
        company_id=company_id,
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
    # 조각을 바꿔치기했으므로 문서의 exact_evidence_hashes도 다시 역산한다 —
    # 안 그러면 새 replacement_fragment의 해시가 문서 허용 목록에 없어
    # select.py의 결속 확인에서 조용히 걸러진다.
    fixture["documents"] = _bind_exact_evidence_hashes(
        fixture["documents"], fixture["fragments"]
    )
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
            source_kind="official_web_page",
            title="자사몰 회사소개",
        ),
        make_document(
            company_id=company_id,
            document_id="official-careers-01",
            source_kind="official_recruit_page",
            title="채용 페이지",
        ),
        make_document(
            company_id=company_id,
            document_id="official-blog-01",
            source_kind="official_web_page",
            title="공식 블로그",
        ),
    ]
    fragments: list[dict[str, object]] = []
    attempts: list[dict[str, object]] = []

    for section_id in DART_SECTIONS:
        section_fragments, section_attempts = build_filled_channel(
            company_id=company_id,
            section_id=section_id,
            source_kind="dart_audit_report",
            document_id=dart_document_id,
        )
        fragments.extend(section_fragments)
        attempts.extend(section_attempts)

    official_channel_by_section = {
        "identity": ("official_web_page", "official-shop-01"),
        "portfolio": ("official_web_page", "official-shop-01"),
        "future_strategy": ("official_web_page", "official-blog-01"),
        "culture": ("official_recruit_page", "official-careers-01"),
        "competitive_position": ("official_web_page", "official-blog-01"),
    }
    for section_id in OFFICIAL_ONLY_SECTIONS:
        source_kind, document_id = official_channel_by_section[section_id]
        section_fragments, section_attempts = build_filled_channel(
            company_id=company_id,
            section_id=section_id,
            source_kind=source_kind,
            document_id=document_id,
        )
        fragments.extend(section_fragments)
        attempts.extend(section_attempts)

    return {
        "documents": _bind_exact_evidence_hashes(documents, fragments),
        "fragments": fragments,
        "attempts": attempts,
    }


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
            company_id=company_id,
            section_id=section_id,
            source_kind="dart_audit_report",
            document_id=dart_document_id,
        )
        fragments.extend(section_fragments)
        attempts.extend(section_attempts)
    for section_id in OFFICIAL_ONLY_SECTIONS:
        attempts.extend(
            build_unfilled_channel(
                company_id=company_id,
                section_id=section_id,
                source_kind="official_web_page",
                state=CollectionState.MISSING.value,
                reason_code="official_homepage_dns_not_found",
            )
        )
    return {
        "documents": _bind_exact_evidence_hashes(documents, fragments),
        "fragments": fragments,
        "attempts": attempts,
    }


def build_javascript_render_failure_fixture(
    *, company_id: str = "corp-js-render"
) -> dict[str, list]:
    """시나리오 5 — 자바스크립트형: 웹 본문 추출 실패(FAILED)."""

    fixture = build_no_homepage_fixture(company_id=company_id)
    fixture["attempts"] = [
        attempt
        for attempt in fixture["attempts"]
        if attempt["source_kind"] != "official_web_page"
    ]
    for section_id in OFFICIAL_ONLY_SECTIONS:
        fixture["attempts"].extend(
            build_unfilled_channel(
                company_id=company_id,
                section_id=section_id,
                source_kind="official_web_page",
                state=CollectionState.FAILED.value,
                reason_code="official_homepage_render_timeout",
            )
        )
    return fixture
