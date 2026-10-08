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
from src.shared.report_evidence.constants import OFFICIAL_WEB_SOURCE_KINDS
from src.shared.report_evidence.industry_candidates import OfficialIndustrySupplement
from src.shared.report_quality.source_identity import collected_document_identity
from src.features.composer.port import CollectedFragment


def discovery_fragments_for_supplement(
    supplement: OfficialIndustrySupplement, *, fragments: Sequence[CollectedFragment], company_id: str,
) -> tuple[CollectedFragment, ...]:
    """작성 후 채택된 조사 원문만 슬롯 없이 최종 출처 등록부로 옮긴다."""
    supplement.__post_init__()
    if len(supplement.problems) > INDUSTRY_CONTEXT_MAX_ITEMS:
        raise ValueError("공식 산업 설명의 출력 한도를 넘었습니다")
    next_number = max((int(value.fragment_id) for value in fragments if value.fragment_id.isdecimal()), default=0) + 1
    extra = []
    for candidate in supplement.candidates:
        if candidate.company_id != company_id:
            raise ValueError("공식 산업 조사 원문의 회사가 다릅니다")
        document = candidate.document
        identity = collected_document_identity(
            source_kind=document.source_kind, document_id=document.document_id, url=document.canonical_url,
        )
        if not identity:
            raise ValueError("공식 산업 문서의 공개 신원을 확인할 수 없습니다")
        item = CollectedFragment(
            fragment_id=str(next_number), kind="official-industry-discovery", text=candidate.text,
            source_url=document.canonical_url, document_title=document.title, location=candidate.location,
            document_date=document.published_on, document_identity=identity,
            document_content_sha256=document.content_sha256, counts_toward_document_floor=False,
            supported_claim_slots=(), formal_source_kind=document.source_kind,
            source_document_id=document.document_id, source_publisher=document.publisher,
            identity_binding=document.identity_binding, source_collected_on=document.collected_at,
            domain_attestation_source_id=document.domain_attestation_source_id,
            domain_attestation_evidence=document.domain_attestation_evidence,
            reporting_period=document.reporting_period, attachment_url=document.attachment_url,
            ir_metadata_verification=document.ir_metadata_verification,
            domain_redirect_verification=document.domain_redirect_verification,
            domain_redirect_from_host=document.domain_redirect_from_host,
            domain_redirect_to_host=document.domain_redirect_to_host,
            source_context_json=candidate.source_context_json, section_context_json=candidate.section_context_json,
        )
        if not any(_matches_official_problem(item, problem) for problem in supplement.problems):
            raise ValueError("채택된 산업 설명과 무관한 원문을 출처에 추가할 수 없습니다")
        if not any(value.text == item.text and value.source_url == item.source_url
                   and value.location == item.location for value in (*fragments, *extra)):
            extra.append(item)
            next_number += 1
    return tuple(extra)


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


def _matches_official_problem(fragment: object, problem: IndustryProblemEvidence) -> bool:
    """산업 검수 원문도 같은 실행의 공식 조각 전체와 대조한다."""
    return bool(
        fragment.text == problem.exact_text
        and fragment.source_document_id == problem.document_id
        and fragment.source_url == problem.source_url
        and fragment.formal_source_kind == problem.source_kind
        and fragment.document_content_sha256 == problem.document_content_sha256
        and fragment.identity_binding == problem.identity_binding
        and fragment.location == problem.location
        and fragment.source_publisher == problem.publisher
        and fragment.document_title == problem.title
        and fragment.document_date == problem.published_on
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
    available_problems: set[str] = set()
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
        if problem.source_kind:
            original_problems = [value for value in original_fragments
                                 if _matches_official_problem(value, problem)]
            if not original_problems:
                raise ValueError("공식 산업 검수 원문이 이번 실행의 수집 근거와 다릅니다")
            for original in original_problems:
                selected = selected_by_id.get(original.fragment_id)
                if selected is not None and not _matches_official_problem(selected, problem):
                    raise ValueError("선택된 공식 산업 조각의 원문 또는 출처가 수집 근거와 다릅니다")
            if not any(_matches_official_problem(value, problem) for value in selected_fragments):
                continue
        available_problems.add(problem.evidence_id)
    selected_problems = tuple(value for value in problems if (
        value.business_anchor_id in available and value.evidence_id in available_problems
    ))
    return (
        tuple(value for value in anchors if value.anchor_id in available),
        selected_problems,
        len(problems) - len(selected_problems),
    )


def _bind_official_fragment(
    fragment: object, *, registry: list[object], numbers: Mapping[str, int],
    build_official_source: Callable[[object, int], object],
) -> object:
    """기존 출처 생성기의 종류·발행자·날짜 투영과 봉인을 그대로 재사용한다."""
    source = build_official_source(fragment, numbers[fragment.fragment_id])
    previous = next((value for value in registry if value.source_id == source.source_id), None)
    if previous is not None:
        # 기존 등록부의 발행자·날짜·법인·웹 proof를 다시 만든 공식 Source와
        # 모두 대조한다. 사용 장 목록만 최종 공개 사실에 따라 달라질 수 있다.
        if replace(previous, used_in=source.used_in) != source:
            raise ValueError("산업 과제의 공식 출처가 기존 공개 등록부와 다릅니다")
        source = bind_collected_source_section(previous, INDUSTRY_CONTEXT_SECTION)
        registry[registry.index(previous)] = source
    else:
        registry.append(source)
    return source


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
        official_source = _bind_official_fragment(
            fragment, registry=registry, numbers=numbers, build_official_source=build_official_source,
        )
        if problem.source_kind:
            problem_fragments = [value for value in fragments if _matches_official_problem(value, problem)]
            if not problem_fragments:
                raise ValueError("공식 산업 자료가 선택된 자기 원문과 다릅니다")
            industry_source = _bind_official_fragment(
                problem_fragments[0], registry=registry, numbers=numbers,
                build_official_source=build_official_source,
            )
            # DART의 발행자는 회사, 날짜는 공시일이다. 뉴스의 메타데이터 형식을
            # 공시에 적용하거나 수집 메타데이터를 무조건 동일하다고 간주하지 않는다.
            published_on = (
                industry_source.published_at if problem.source_kind in OFFICIAL_WEB_SOURCE_KINDS
                else industry_source.disclosed_at
            )
            bound_problem = replace(
                problem, source_id=industry_source.source_id,
                publisher=industry_source.publisher,
                title=industry_source.title or industry_source.label,
                published_on=published_on,
            )
        else:
            next_number = max((*numbers.values(), *(value.number for value in registry)), default=0) + 1
            industry_source = register_industry_problem_source(
                problem, number=next_number, section_id=INDUSTRY_CONTEXT_SECTION,
            )
            registry.append(industry_source)
            bound_problem = replace(problem, source_id=industry_source.source_id)
        contexts.append(IndustryChallengeContext(
            replace(anchor, source_id=official_source.source_id),
            bound_problem,
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
