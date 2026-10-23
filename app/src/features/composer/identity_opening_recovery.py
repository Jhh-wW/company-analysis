"""기존 승인 본문을 보존하며 첫 사업 소개 한 문장만 독립 작성·검수한다."""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from typing import Protocol

from src.features.composer.citation_labels import normalize_draft_sentence_citations
from src.features.composer.constants import GRADE_CONFIRMED, IDENTITY_FIRST_SENTENCE_WRITER_GUIDE
from src.features.composer.empty_section_recovery import (
    _section_context_metadata,
    recovery_evidence,
    rejected_sentence_fingerprint,
    requested_sections_from_response,
)
from src.features.composer.identity_content_constants import (
    IDENTITY_BUSINESS_ACTIVITY_RE, IDENTITY_SECTION_ID,
)
from src.features.composer.identity_opening import (
    IdentityOpeningFact,
    identity_opening_candidate,
    identity_opening_definition_form,
    identity_opening_sentence_eligible,
)
from src.features.composer.identity_opening_constants import IDENTITY_BUSINESS_DEFINITION_SLOT
from src.features.composer.identity_opening_constants import (
    IDENTITY_ACTUAL_FACT_STATUS, IDENTITY_CURRENT_TIME_STATE, IDENTITY_VERIFIED_STATE,
)
from src.features.composer.identity_opening_recovery_constants import (
    IDENTITY_EXISTING_CORPORATE_SLOT,
    IDENTITY_EXISTING_CURRENT_BUSINESS_RE,
    IDENTITY_EXISTING_HISTORY_EVENT_RE,
    IDENTITY_EXISTING_SPECIALISM_PREFIXES,
    IDENTITY_EXISTING_TOPIC_SEPARATORS,
    IDENTITY_OPENING_RECOVERY_ADDED,
    IDENTITY_OPENING_RECOVERY_ATTEMPTED,
    IDENTITY_OPENING_RECOVERY_NO_BUDGET,
    IDENTITY_OPENING_RECOVERY_NO_CANDIDATE,
    IDENTITY_OPENING_RECOVERY_NO_EVIDENCE,
    IDENTITY_OPENING_RECOVERY_PARSE_FAILED,
    IDENTITY_OPENING_RECOVERY_PRESENT,
    IDENTITY_OPENING_RECOVERY_REJECTED,
    IDENTITY_OPENING_RECOVERY_STEP,
    IDENTITY_OPENING_RECOVERY_WRITER_GUIDE,
)
from src.features.composer.logic import AskFn, extract_json_payload, parse_section_response
from src.features.composer.port import (
    AskFatalError, CollectedFragment, ComposedReport, ComposedSection,
)
from src.features.composer.structured_claims import enforce_public_numeric_safety
from src.features.composer.verify import verify_report
from src.shared.report_quality.constants import VERIFIED_PROSE_CLAIM_TYPE


class BoundIdentityOpeningFact(IdentityOpeningFact, Protocol):
    """공개 결속 검사가 남긴 문장과 기존 Fact의 정확 주장만 대조한다."""

    claim: str


@dataclass(frozen=True)
class IdentityOpeningRecoveryResult:
    report: ComposedReport
    attempted: bool
    added: int
    reason: str


def _existing_nominal_business_intro(text: str) -> bool:
    """기존 전문·전업 기업 문형 앞에 회사 주제부와 사업 설명이 함께 있는가.

    업종 명사 목록을 새로 만들지 않는다. 기존 행정정보 구별기가 이미 아는
    전문·전업 문형을 쓰되, '회사는 전문기업이다'처럼 유형만 있는 말은 제외한다.
    """

    for match in IDENTITY_BUSINESS_ACTIVITY_RE.finditer(text):
        if not match.group().startswith(IDENTITY_EXISTING_SPECIALISM_PREFIXES):
            continue
        before_type = text[:match.start()]
        if any(before_type.partition(separator)[2].strip()
               for separator in IDENTITY_EXISTING_TOPIC_SEPARATORS):
            return True
    return False


def has_bound_identity_opening(
    report: ComposedReport, bound_facts: Sequence[BoundIdentityOpeningFact],
) -> bool:
    """공개 결속 후 본문에 현재 사업 정의문이 이미 있는지 확인한다.

    입력 본문은 select_bound_public_sentences 결과여야 한다. 이 함수가 원래
    Fact 검증이나 공개 결속 검사를 대체하지 않는다.
    """

    for section in report.sections:
        if section.section_id != IDENTITY_SECTION_ID:
            continue
        for index, sentence in enumerate(section.sentences):
            matches = tuple(fact for fact in bound_facts
                            if fact.claim == sentence.text
                            and fact.claim_slot == sentence.planned_claim_slot
                            and fact.section_owner == IDENTITY_SECTION_ID)
            if len(matches) != 1:
                continue
            fact = matches[0]
            if identity_opening_candidate(sentence, fact):
                return True
            # 구형 corporate_identity 첫 소개도 이미 현재 사업을 설명했다면
            # 필요를 충족한다. 승인·슬롯·순서를 바꾸거나 다른 문장을 옮기지 않는다.
            if (
                index == 0
                and sentence.planned_claim_slot == IDENTITY_EXISTING_CORPORATE_SLOT
                and sentence.grade == GRADE_CONFIRMED
                and sentence.verification_state == IDENTITY_VERIFIED_STATE
                and identity_opening_definition_form(sentence.text)
                and (IDENTITY_EXISTING_CURRENT_BUSINESS_RE.search(sentence.text)
                     or _existing_nominal_business_intro(sentence.text))
                and not IDENTITY_EXISTING_HISTORY_EVENT_RE.search(sentence.text)
                and fact.claim_type == VERIFIED_PROSE_CLAIM_TYPE
                and fact.time_state == IDENTITY_CURRENT_TIME_STATE
                and fact.fact_status == IDENTITY_ACTUAL_FACT_STATUS
                and not fact.plan_status
            ):
                return True
    return False


def recover_identity_opening(
    company_name: str,
    report: ComposedReport,
    *,
    bound_facts: Sequence[BoundIdentityOpeningFact],
    evidence: Sequence[CollectedFragment],
    allowed_fragment_ids: frozenset[str],
    writer: AskFn | None,
    reviewer: AskFn | None,
    can_start: Callable[[], bool] | None,
    baseline_date: str | None = None,
    diagnostics: list[dict] | None = None,
    protocol_diagnostics: list[dict] | None = None,
    rejected_fingerprints: frozenset[str] = frozenset(),
    attempted: bool = False,
) -> IdentityOpeningRecoveryResult:
    """작가·검수 각 최대 한 번. 기존 본문을 재검수하거나 비우지 않는다.

    writer/reviewer는 호출자가 기존 예산·장부·영수증 포트로 감싸서 주입한다.
    반환 후보도 최종 render→select_bound→현재 사업 소개 게이트를 통과해야 한다.
    AskFatalError는 전역 취소·예산 정책을 결정하는 호출자에게 그대로 전달한다.
    """

    def finish(reason: str, *, result=report, added=0, consumed=True):
        if protocol_diagnostics is not None:
            protocol_diagnostics.append({
                "step": IDENTITY_OPENING_RECOVERY_STEP, "상태": reason,
                "대상장": [IDENTITY_SECTION_ID], "최종반영": added,
            })
        return IdentityOpeningRecoveryResult(result, consumed, added, reason)

    if has_bound_identity_opening(report, bound_facts):
        return finish(IDENTITY_OPENING_RECOVERY_PRESENT, consumed=attempted)
    if attempted:
        return finish(IDENTITY_OPENING_RECOVERY_ATTEMPTED)
    original = next((section for section in report.sections
                     if section.section_id == IDENTITY_SECTION_ID), None)
    if original is None:
        return finish(IDENTITY_OPENING_RECOVERY_NO_EVIDENCE)
    eligible = tuple(fragment for fragment in evidence
                     if fragment.fragment_id in allowed_fragment_ids
                     and IDENTITY_BUSINESS_DEFINITION_SLOT in fragment.supported_claim_slots)
    fragments = recovery_evidence(eligible).get(IDENTITY_SECTION_ID, ())
    if not fragments:
        return finish(IDENTITY_OPENING_RECOVERY_NO_EVIDENCE)
    if writer is None or reviewer is None or can_start is None or not can_start():
        return finish(IDENTITY_OPENING_RECOVERY_NO_BUDGET)
    payload = {
        "기존본문": [sentence.text for sentence in original.sentences],
        "공식근거": [{
            "id": fragment.fragment_id, "원문": fragment.text,
            "의미칸": [IDENTITY_BUSINESS_DEFINITION_SLOT],
            **_section_context_metadata(fragment),
        } for fragment in fragments],
    }
    prompt = (f"분석 회사: {company_name}\n{IDENTITY_OPENING_RECOVERY_WRITER_GUIDE}"
              + IDENTITY_FIRST_SENTENCE_WRITER_GUIDE
              + json.dumps(payload, ensure_ascii=False))
    raw = extract_json_payload(writer(prompt))
    usable, _extra, _shape = requested_sections_from_response(raw, (IDENTITY_SECTION_ID,))
    if IDENTITY_SECTION_ID not in usable:
        return finish(IDENTITY_OPENING_RECOVERY_PARSE_FAILED)
    parsed = parse_section_response(
        json.dumps(usable[IDENTITY_SECTION_ID], ensure_ascii=False), IDENTITY_SECTION_ID,
        reject_inline_citation_markers=True,
    )
    if parsed is None:
        return finish(IDENTITY_OPENING_RECOVERY_PARSE_FAILED)
    allowed = frozenset(fragment.fragment_id for fragment in fragments)
    excluded = rejected_fingerprints | frozenset(
        rejected_sentence_fingerprint(sentence.text) for sentence in original.sentences
    )
    candidates = []
    for sentence in parsed:
        sentence = normalize_draft_sentence_citations(sentence, allowed)
        if (
            sentence.grade == GRADE_CONFIRMED
            and sentence.planned_claim_slot == IDENTITY_BUSINESS_DEFINITION_SLOT
            and sentence.citations and set(sentence.citations) <= allowed
            and identity_opening_definition_form(sentence.text)
            and rejected_sentence_fingerprint(sentence.text) not in excluded
        ):
            candidates.append(sentence)
            break
    if not candidates:
        return finish(IDENTITY_OPENING_RECOVERY_NO_CANDIDATE)
    review_called = False

    def review_once(review_prompt: str) -> str:
        nonlocal review_called
        if review_called:
            raise AskFatalError(ValueError("정체성 첫 문장 보충 검수는 한 번만 허용합니다"), call_limit=True)
        review_called = True
        return reviewer(review_prompt)

    verified = verify_report(
        ComposedReport((ComposedSection(IDENTITY_SECTION_ID, tuple(candidates)),)),
        fragments, None, review_once,
        allowed_fragment_ids_by_section={IDENTITY_SECTION_ID: allowed},
        diagnostics=diagnostics, protocol_diagnostics=protocol_diagnostics,
        baseline_date=baseline_date, allow_sentence_rewrite=False,
        second_review_call_available=lambda: False, skip_empty_grouped_review=True,
    )
    verified, _numeric = enforce_public_numeric_safety(verified)
    additions = tuple(sentence for section in verified.sections
                      if section.section_id == IDENTITY_SECTION_ID
                      for sentence in section.sentences
                      if identity_opening_sentence_eligible(sentence))
    if not additions:
        return finish(IDENTITY_OPENING_RECOVERY_REJECTED)
    # 원래 승인 객체·표·도식·안내문은 그대로 두고 독립 검수된 문장만 하나 보탠다.
    merged = replace(report, sections=tuple(
        replace(section, sentences=(*additions, *section.sentences))
        if section.section_id == IDENTITY_SECTION_ID else section
        for section in report.sections
    ), summary=())
    return finish(IDENTITY_OPENING_RECOVERY_ADDED, result=merged, added=len(additions))
