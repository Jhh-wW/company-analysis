"""검수된 산업 과제를 기존 출처 등록부에 결속하는 결정적 변환."""

from __future__ import annotations

from dataclasses import replace
from typing import Callable, Mapping, Sequence
from src.core.source_verification_adapter import (
    supplementary_research_source_verifier, register_industry_problem_source,
    bind_collected_source_section,
)
from src.shared.business_challenge_context import (
    BusinessActivityAnchor, IndustryProblemEvidence, IndustryChallengeContext,
    INDUSTRY_CONTEXT_MAX_ITEMS, INDUSTRY_CONTEXT_SECTION, industry_context_problems,
)


def has_verified_direct_business_issue(report: object) -> bool:
    """회사 직접 과제가 있으면 산업 일반의 과제로 대체하지 않는다."""
    return any(
        sentence.verification_state == "verified" and sentence.grade == "확인"
        and sentence.planned_claim_slot == "current_challenges:issue"
        for section in report.sections if section.section_id == INDUSTRY_CONTEXT_SECTION
        for sentence in section.sentences
    )


def _matches_business_anchor(fragment: object, anchor: BusinessActivityAnchor) -> bool:
    return bool(
        fragment.text == anchor.exact_text
        and fragment.source_document_id == anchor.document_id
        and fragment.source_url == anchor.source_url
        and fragment.formal_source_kind == anchor.source_kind
        and fragment.document_content_sha256 == anchor.document_content_sha256
        and fragment.identity_binding == anchor.identity_binding
        and fragment.location == anchor.location
        and fragment.source_publisher == anchor.publisher
        and fragment.document_title == anchor.title
        and fragment.document_date == anchor.published_on
    )


def select_industry_context_for_fragments(
    *, anchors: tuple[BusinessActivityAnchor, ...],
    problems: tuple[IndustryProblemEvidence, ...],
    original_fragments: Sequence[object], selected_fragments: Sequence[object],
    company_id: str,
) -> tuple[tuple[BusinessActivityAnchor, ...], tuple[IndustryProblemEvidence, ...], int]:
    """원본 결속을 확인한 뒤 작성 입력에서 빠진 사업의 산업 보조만 제외한다.

    원본에도 없는 근거와 변조된 선택 조각은 정상적인 선택 탈락으로 숨기지 않는다.
    번호는 수집·전달 과정에서 바뀔 수 있으므로 원문과 출처 메타데이터를 대조한다.
    """
    by_anchor = {value.anchor_id: value for value in anchors}
    if len(by_anchor) != len(anchors):
        raise ValueError("산업 과제의 공식 사업 앵커가 중복됐습니다")
    selected_by_id = {value.fragment_id: value for value in selected_fragments}
    if len(selected_by_id) != len(selected_fragments):
        raise ValueError("선택된 공식 사업 조각의 인용 번호가 중복됐습니다")
    available: set[str] = set()
    for problem in problems:
        anchor = by_anchor.get(problem.business_anchor_id)
        if anchor is None or anchor.company_id != company_id:
            raise ValueError("산업 문제의 공식 사업 앵커 또는 법인 결속이 다릅니다")
        originals = [value for value in original_fragments if _matches_business_anchor(value, anchor)]
        if not originals or not anchor.identity_binding or not anchor.document_content_sha256:
            raise ValueError("산업 과제의 공식 사업 원문이 이번 실행의 수집 근거와 다릅니다")
        for original in originals:
            selected = selected_by_id.get(original.fragment_id)
            if selected is not None and not _matches_business_anchor(selected, anchor):
                raise ValueError("선택된 공식 사업 조각의 원문 또는 출처가 수집 근거와 다릅니다")
        if any(_matches_business_anchor(value, anchor) for value in selected_fragments):
            available.add(anchor.anchor_id)
    selected_problems = tuple(value for value in problems if value.business_anchor_id in available)
    return (
        tuple(value for value in anchors if value.anchor_id in available),
        selected_problems,
        len(problems) - len(selected_problems),
    )


def bind_industry_context_sources(
    *, anchors: tuple[BusinessActivityAnchor, ...], problems: tuple[IndustryProblemEvidence, ...],
    fragments: Sequence[object], sources: Sequence[object], numbers: Mapping[str, int],
    company_id: str, reference_date: str, has_direct_issue: bool,
    build_official_source: Callable[[object, int], object],
) -> tuple[tuple[IndustryChallengeContext, ...], list[object]]:
    """공식 사업 원문을 다시 대조하고 산업 원문의 출처를 봉인한다.

    추가 원문을 찾거나 모델을 호출하지 않는다. 산업 자료는 회사 사실 원장으로
    만들지 않으며, 원문 발췌와 고정 해석 문구만 별도 블록에 전달한다.
    """
    registry = list(sources)
    if has_direct_issue or not problems:
        return (), registry
    by_anchor = {value.anchor_id: value for value in anchors}
    if len(by_anchor) != len(anchors):
        raise ValueError("산업 과제의 공식 사업 앵커가 중복됐습니다")
    contexts: list[IndustryChallengeContext] = []
    seen: set[str] = set()
    for problem in problems:
        if len(contexts) >= INDUSTRY_CONTEXT_MAX_ITEMS:
            break
        anchor = by_anchor.get(problem.business_anchor_id)
        if anchor is None or anchor.company_id != company_id:
            raise ValueError("산업 문제의 공식 사업 앵커 또는 법인 결속이 다릅니다")
        if problem.evidence_id in seen:
            continue
        seen.add(problem.evidence_id)
        matches = [value for value in fragments if _matches_business_anchor(value, anchor)]
        if not matches or not anchor.identity_binding or not anchor.document_content_sha256:
            raise ValueError("산업 과제의 공식 사업 원문이 이번 실행의 수집 근거와 다릅니다")
        fragment = matches[0]
        official_source = build_official_source(fragment, numbers[fragment.fragment_id])
        previous = next((value for value in registry if value.source_id == official_source.source_id), None)
        if previous is not None:
            if (previous.url != official_source.url
                    or previous.exact_evidence_hashes != official_source.exact_evidence_hashes
                    or previous.document_content_sha256 != official_source.document_content_sha256):
                raise ValueError("산업 과제의 공식 출처가 기존 공개 등록부와 다릅니다")
            official_source = bind_collected_source_section(previous, INDUSTRY_CONTEXT_SECTION)
            registry[registry.index(previous)] = official_source
        else:
            registry.append(official_source)
        next_number = max((*numbers.values(), *(value.number for value in registry)), default=0) + 1
        industry_source = register_industry_problem_source(
            problem, number=next_number, section_id=INDUSTRY_CONTEXT_SECTION,
        )
        registry.append(industry_source)
        contexts.append(IndustryChallengeContext(
            replace(anchor, source_id=official_source.source_id),
            replace(problem, source_id=industry_source.source_id),
        ))
    return tuple(contexts), registry


def assert_industry_context_sources(
    contexts: tuple[IndustryChallengeContext, ...], *, company_id: str,
    sources: tuple[object, ...], reference_date: str,
) -> None:
    problems = industry_context_problems(
        contexts, company_id=company_id, registry=sources, reference_date=reference_date,
        verifier=supplementary_research_source_verifier(),
    )
    if problems:
        raise ValueError("산업 과제의 최종 출처 결속 계약 위반: " + ", ".join(problems))
