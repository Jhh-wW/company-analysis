"""v2 렌더 결과를 공유 품질 계약으로 손실 없이 투영한다.

문장에 숫자가 있는지 다시 읽어 사실을 꾸미지 않는다. 렌더 단계가 이미 만든
FactRecord의 정확한 주장·범주·장·근거 지문이 본문 문장과 일치할 때만 그
문장을 결속된 공개 내용으로 센다. 요약도 본문과 같은 사실 ID를 재사용한다.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import replace

from src.features.composer.constants import GRADE_CONFIRMED, GRADE_INTERPRETED
from src.features.composer.port import ComposedReport, ComposedSentence
from src.features.pipeline.port import FactRecord, Report
from src.features.provenance.sources import Source
from src.shared.report_quality.dto import (
    ClaimFact,
    ReportCandidate,
    ReportSectionCandidate,
    SourceDocument,
)
from src.shared.report_quality.fact_binding import fact_evidence_binding
from src.shared.report_quality.evidence_support import prose_evidence_support_ready
from src.shared.report_quality.source_identity import (
    document_identity,
    fact_document_identity,
)
from src.shared.report_quality.constants import (
    COMPETITIVE_COMPARISON_CLAIM_TYPE,
    COMPETITIVE_COMPARISON_CONTEXT_CLAIM_TYPE,
    HISTORICAL_PERFORMANCE_RATE_CLAIM_TYPE,
    INTERPRETATION_CLAIM_TYPE,
    VERIFIED_PROSE_CLAIM_TYPE,
)


def _normalized_text(value: object) -> str:
    return " ".join(str(value or "").split())


def _fact_key(
    *, section_id: str, claim: str, claim_slot: str
) -> tuple[str, str, str]:
    return (section_id.strip(), _normalized_text(claim), claim_slot.strip())


def _valid_fact_registries(
    facts: Sequence[FactRecord],
) -> tuple[dict[str, FactRecord], dict[tuple[str, str, str], FactRecord]]:
    """ID나 공개 문장 키가 모호하면 어느 쪽도 임의 선택하지 않는다."""

    by_id: dict[str, FactRecord] = {}
    by_key: dict[tuple[str, str, str], FactRecord] = {}
    duplicate_ids: set[str] = set()
    duplicate_keys: set[tuple[str, str, str]] = set()
    for fact in facts:
        fact_id = fact.fact_id.strip()
        key = _fact_key(
            section_id=fact.section_owner,
            claim=fact.claim,
            claim_slot=fact.claim_slot,
        )
        if (
            not fact_id
            or not all(key)
            or (fact.verification_status or fact.status) != "verified"
            or not prose_evidence_support_ready(
                fact.claim_type, fact.evidence_support_terms
            )
            or not fact.evidence_binding
            or fact.evidence_binding != fact_evidence_binding(fact)
        ):
            continue
        if fact_id in by_id:
            duplicate_ids.add(fact_id)
        else:
            by_id[fact_id] = fact
        if key in by_key:
            duplicate_keys.add(key)
        else:
            by_key[key] = fact
    for fact_id in duplicate_ids:
        by_id.pop(fact_id, None)
    for key in duplicate_keys:
        by_key.pop(key, None)
    return by_id, by_key


def _sentence_fact_id(
    section_id: str,
    sentence: ComposedSentence,
    *,
    by_id: dict[str, FactRecord],
    by_key: dict[tuple[str, str, str], FactRecord],
) -> str:
    citations = tuple(
        dict.fromkeys(
            str(value).strip()
            for value in sentence.citations
            if str(value).strip()
        )
    )
    if sentence.verification_state != "verified" or not citations:
        return ""
    key = _fact_key(
        section_id=section_id,
        claim=sentence.text,
        claim_slot=sentence.planned_claim_slot,
    )
    structured = sentence.structured_claim
    fact = (
        by_id.get(structured.fact_id.strip())
        if structured is not None
        else by_key.get(key)
    )
    if fact is None:
        return ""
    # 문장 등급과 FactRecord 종류는 같은 판단의 양쪽 표현이다. 둘 중 한쪽만
    # 재봉인해 «해석»을 검증 사실로 바꾸면 해석 상한을 우회하므로, 투영 전에
    # 독립적으로 맞춰 본다. 구조화 실적·비교 사실도 확인 등급이어야 한다.
    if (
        sentence.grade == GRADE_INTERPRETED
        and fact.claim_type != INTERPRETATION_CLAIM_TYPE
    ) or (
        sentence.grade == GRADE_CONFIRMED
        and fact.claim_type == INTERPRETATION_CLAIM_TYPE
    ):
        return ""
    if sentence.grade not in {GRADE_CONFIRMED, GRADE_INTERPRETED}:
        return ""
    if (
        fact.claim_type == HISTORICAL_PERFORMANCE_RATE_CLAIM_TYPE
        and structured is None
    ):
        return ""
    if fact.claim_type in {
        COMPETITIVE_COMPARISON_CLAIM_TYPE,
        COMPETITIVE_COMPARISON_CONTEXT_CLAIM_TYPE,
    } and sentence.verified_fact_id.strip() != fact.fact_id:
        return ""
    if fact.claim_type in {
        VERIFIED_PROSE_CLAIM_TYPE,
        INTERPRETATION_CLAIM_TYPE,
    } and (structured is not None or sentence.verified_fact_id.strip()):
        return ""
    fact_key = _fact_key(
        section_id=fact.section_owner,
        claim=fact.claim,
        claim_slot=fact.claim_slot,
    )
    if fact_key != key:
        return ""
    if structured is not None:
        if (
            structured.section_owner != section_id
            or structured.verification_state != "verified"
            or citations != (structured.source_fragment_id.strip(),)
        ):
            return ""
    elif fact.claim_type in {
        VERIFIED_PROSE_CLAIM_TYPE,
        INTERPRETATION_CLAIM_TYPE,
    }:
        try:
            manifest = json.loads(fact.state_evidence)
            bound_citations = tuple(
                str(item["fragment_id"]).strip()
                for item in manifest
                if isinstance(item, dict) and str(item.get("fragment_id") or "").strip()
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return ""
        if citations != bound_citations:
            return ""
    return fact.fact_id


def _claim_fact(fact: FactRecord) -> ClaimFact:
    return ClaimFact(
        fact_id=fact.fact_id,
        section_owner=fact.section_owner,
        source_id=fact.source_id,
        source_identity=fact_document_identity(fact),
        verification_state=fact.verification_status or fact.status,
        claim_slot=fact.claim_slot,
        evidence_binding_valid=bool(fact.evidence_binding)
        and fact.evidence_binding == fact_evidence_binding(fact),
        claim=fact.claim,
        subject_scope=fact.subject_scope,
        raw_value=fact.raw_value,
        calculation=fact.calculation,
        display_value=fact.display_value,
        rounding_rule=fact.rounding_rule,
        numeric_checks=tuple(fact.numeric_checks),
        metric=fact.metric,
        period_start=fact.period_start,
        period_end=fact.period_end,
        sign=fact.sign,
        unit=fact.unit,
        unit_dimension=fact.unit_dimension,
        formula=fact.formula,
        supporting_source_ids=tuple(fact.supporting_source_ids),
        supporting_source_identities=tuple(fact.supporting_source_identities),
        supporting_evidence_hashes=tuple(fact.supporting_evidence_hashes),
        claim_type=fact.claim_type,
        legal_entity=fact.legal_entity,
        state_evidence=fact.state_evidence,
        evidence_support_terms=tuple(fact.evidence_support_terms),
        comparison_target=fact.comparison_target,
        comparison_metric=fact.comparison_metric,
        comparison_definition=fact.comparison_definition,
        comparison_basis=fact.comparison_basis,
        comparison_period=fact.comparison_period,
        comparison_scope=fact.comparison_scope,
        comparison_judgment=fact.comparison_judgment,
        comparator_source_id=fact.comparator_source_id,
        comparator_state_evidence=fact.comparator_state_evidence,
        comparison_conditions=dict(fact.comparison_conditions),
    )


def _summary_sentence_fact_id(
    sentence: ComposedSentence,
    *,
    by_id: dict[str, FactRecord],
    by_key: dict[tuple[str, str, str], FactRecord],
) -> str:
    """본문 소유 장이 하나로 확정되는 추출식 요약만 사실 ID에 잇는다."""

    explicit_fact_id = sentence.verified_fact_id.strip()
    if explicit_fact_id:
        explicit_fact = by_id.get(explicit_fact_id)
        if explicit_fact is None:
            return ""
        validation_sentence = sentence
        if explicit_fact.claim_type in {
            VERIFIED_PROSE_CLAIM_TYPE,
            INTERPRETATION_CLAIM_TYPE,
        }:
            # extractive summary만 본문 FactRecord ID를 덧붙인다. 본문 투영의
            # verified_fact_id 금지는 그대로 두되, 이 summary 전용 private
            # 경계에서만 ID를 잠시 떼고 문장·slot·citation·state manifest를
            # 본문과 동일한 규칙으로 다시 맞춘다. 호출자가 켤 boolean은 없다.
            validation_sentence = replace(sentence, verified_fact_id="")
        matched = _sentence_fact_id(
            explicit_fact.section_owner,
            validation_sentence,
            by_id=by_id,
            by_key=by_key,
        )
        return explicit_fact_id if matched == explicit_fact_id else ""

    structured = sentence.structured_claim
    if structured is not None:
        return _sentence_fact_id(
            structured.section_owner,
            sentence,
            by_id=by_id,
            by_key=by_key,
        )
    normalized_claim = _normalized_text(sentence.text)
    claim_slot = sentence.planned_claim_slot.strip()
    matches = [
        (section_id, fact)
        for (section_id, claim, slot), fact in by_key.items()
        if section_id and claim == normalized_claim and slot == claim_slot
    ]
    if len(matches) != 1:
        return ""
    section_id, _fact = matches[0]
    return _sentence_fact_id(
        section_id,
        sentence,
        by_id=by_id,
        by_key=by_key,
    )


def bound_summary_fact_id(
    sentence: ComposedSentence,
    facts: Sequence[FactRecord],
) -> str:
    """요약 한 문장이 정확히 재사용한 검증 본문 사실 ID를 돌려준다."""

    by_id, by_key = _valid_fact_registries(facts)
    return _summary_sentence_fact_id(
        sentence,
        by_id=by_id,
        by_key=by_key,
    )


def build_generation_quality_candidate(
    rendered: Report,
    composed: ComposedReport,
) -> ReportCandidate:
    """렌더 결과와 본문을 원자 사실 ID 기준으로 품질 평가기에 투영한다."""

    by_id, by_key = _valid_fact_registries(rendered.fact_records)
    structures_manifest_bound = False
    if rendered.public_structure_manifest:
        # 순환 import를 피하면서 FULL pipeline의 독립 manifest 검증 결과를
        # 품질 투영에도 반영한다. digest·표 ref·행 근거가 모두 남아야 한다.
        from src.features.composer.public_manifest import (  # noqa: PLC0415
            PublicManifestError,
            assert_stored_strict_manifest,
        )

        try:
            assert_stored_strict_manifest(rendered)
        except PublicManifestError:
            structures_manifest_bound = False
        else:
            structures_manifest_bound = True
    rendered_sections = {section.cell: section for section in rendered.sections}
    sections: list[ReportSectionCandidate] = []
    for section in composed.sections:
        rendered_section = rendered_sections.get(section.section_id)
        fact_ids = tuple(rendered_section.fact_ids) if rendered_section else ()
        bound_fact_ids = set(fact_ids)
        sentence_fact_ids = tuple(
            _sentence_fact_id(
                section.section_id,
                sentence,
                by_id=by_id,
                by_key=by_key,
            )
            for sentence in section.sentences
        )
        public_sentence_fact_ids = {
            fact_id for fact_id in sentence_fact_ids if fact_id
        }
        mapped_sentence_fact_ids = tuple(
            fact_id for fact_id in sentence_fact_ids if fact_id
        )
        has_unbound_sentences = any(
            not fact_id or fact_id not in bound_fact_ids
            for fact_id in sentence_fact_ids
        )
        # 숨은 FactRecord를 section.fact_ids에만 덧붙이면 문장은 그대로인데
        # 전역 40건 하한만 부풀릴 수 있다. 구조 행 계약 전에는 공개 문장과
        # 장부 ID 집합이 정확히 같아야 하며 여분·누락 어느 쪽도 허용하지 않는다.
        has_fact_mapping_mismatch = (
            bound_fact_ids != public_sentence_fact_ids
            or len(mapped_sentence_fact_ids) != len(public_sentence_fact_ids)
        )
        # FULL 표·도식은 pre-render manifest가 행별 원문 지문과 실제 셀을
        # 완전 비교한 경우에만 결속으로 인정한다. SHADOW·옛 저장본은 그대로다.
        has_public_structures = bool(
            section.flow_rows
            or (rendered_section is not None and rendered_section.tables)
        )
        has_unbound_structures = (
            has_public_structures and not structures_manifest_bound
        )
        sections.append(
            ReportSectionCandidate(
                section_id=section.section_id,
                fact_ids=fact_ids,
                notice_only=not section.sentences and not has_unbound_structures,
                has_unbound_public_content=(
                    has_unbound_sentences
                    or has_fact_mapping_mismatch
                    or has_unbound_structures
                ),
                # 같은 원자 사실 문장을 여러 번 복제해 장별 3문장 하한을
                # 부풀릴 수 없다. 위 mismatch가 공개 안전을 막고, 품질 수치도
                # 실제 고유 결속 문장만 센다.
                public_sentence_count=len(public_sentence_fact_ids),
            )
        )

    sources = tuple(
        SourceDocument(
            source_id=source.source_id,
            document_identity=document_identity(source),
            exact_evidence_hashes=tuple(source.exact_evidence_hashes),
            # 조각에서 다시 계산하지 않는다. 수집→봉인 Source가 운반한 문서
            # 전체 지문을 그대로 품질 평가기에 넘겨 URL 복제로 출처 수를
            # 부풀리지 못하게 한다.
            document_content_sha256=source.document_content_sha256,
            publisher=source.publisher,
        )
        for source in rendered.citations
        if isinstance(source, Source)
    )
    facts = tuple(_claim_fact(fact) for fact in rendered.fact_records)

    summary_fact_ids = tuple(
        fact_id
        for sentence in composed.summary
        if (
            fact_id := _summary_sentence_fact_id(
                sentence,
                by_id=by_id,
                by_key=by_key,
            )
        )
    )
    return ReportCandidate(
        sections=tuple(sections),
        facts=facts,
        sources=sources,
        summary_fact_ids=summary_fact_ids,
        has_unbound_summary_content=(
            len(summary_fact_ids) != len(composed.summary)
        ),
    )
