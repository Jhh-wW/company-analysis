"""공식 양사 비교 생산물을 V2 FULL의 typed 근거 계약으로 옮긴다.

비교 결과를 키워드 조각으로 흉내 내지 않는다. V1과 V2가 함께 쓰는
``build_competitive_position``의 실제 ``FactRecord``·``Source``를 입력으로 받고,
공개 문장·원문 바이트·문서 신원까지 한 객체에 봉인한다.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace

from src.features.company_comparison.logic import (
    COMPETITIVE_SECTION_ID,
    CandidateEvidence,
    ComparisonBuildResult,
)
from src.features.composer.constants import GRADE_CONFIRMED
from src.features.composer.port import (
    CollectedFragment,
    ComposedSentence,
    SectionEvidencePacketSet,
    VerifiedProgramEvidence,
)
from src.features.pipeline.port import FactRecord
from src.features.provenance.sources import (
    Source,
    exact_evidence_text_hash,
    seal_collected_source,
)
from src.shared.report_quality.comparison_claims import (
    COMPARISON_BASIS_SLOT,
    COMPARISON_JUDGMENT_SLOT,
    COMPARISON_LIMITATION_SLOT,
    COMPARISON_METRIC_SLOT,
    COMPARISON_TARGET_SLOT,
    comparison_basis_claim,
    comparison_limitation_claim,
    comparison_metric_claim,
    comparison_metric_summary,
    comparison_target_claim,
)
from src.shared.report_quality.constants import (
    COMPETITIVE_COMPARISON_CLAIM_TYPE,
    COMPETITIVE_COMPARISON_CONTEXT_CLAIM_TYPE,
)
from src.features.company_comparison.stated_differentiator import (
    STATED_DIFFERENTIATOR_CLAIM_TYPE,
    STATED_DIFFERENTIATOR_SLOT,
)
from src.shared.report_quality.comparison_basis import (
    comparison_basis_attester_source_ids,
)
from src.shared.report_quality.fact_binding import fact_evidence_binding
from src.shared.report_quality.evidence_support import (
    MIN_PROSE_EVIDENCE_SUPPORT_TERMS,
    evidence_support_term_mismatches,
    normalized_support_terms,
)
from src.shared.report_quality.source_identity import document_identity
from src.shared.report_quality.source_identity import (
    bound_source_fragment_provenance,
)


_SLOT_TARGET = COMPARISON_TARGET_SLOT
_SLOT_METRIC = COMPARISON_METRIC_SLOT
_SLOT_BASIS = COMPARISON_BASIS_SLOT
_SLOT_JUDGMENT = COMPARISON_JUDGMENT_SLOT
_SLOT_LIMITATION = COMPARISON_LIMITATION_SLOT


def _stable_fact_id(base: FactRecord, suffix: str) -> str:
    payload = "\x1f".join((base.fact_id, suffix, base.comparison_basis))
    return "fact-compare-v2-" + hashlib.sha256(payload.encode("utf-8")).hexdigest()[:20]


def _source_date(source: Source) -> str:
    return source.published_at or source.disclosed_at or source.collected_at


def _with_exact_evidence(source: Source, evidence: str, *, number: int) -> Source:
    exact_hash = exact_evidence_text_hash(evidence)
    if not exact_hash:
        raise ValueError("비교 Source에 결속할 원문 바이트가 비었습니다")
    return seal_collected_source(
        replace(
            source,
            number=number,
            used_in=sorted({*source.used_in, COMPETITIVE_SECTION_ID}),
            exact_evidence_hashes=sorted(
                {*source.exact_evidence_hashes, exact_hash}
            ),
        )
    )


def _source_fields(source: Source) -> dict[str, object]:
    return {
        "source_id": source.source_id,
        "source_type": source.source_type,
        "source_title": source.title or source.label,
        "source_publisher": source.publisher,
        "source_host": source.host,
        "source_url": source.url,
        "source_document_id": source.document_id,
        "location": source.location,
        "source_date": _source_date(source),
    }


def _support_fields(
    sources: tuple[Source, ...], evidence: tuple[str, ...]
) -> dict[str, object]:
    if len(sources) != len(evidence) or not sources:
        raise ValueError("비교 사실의 출처와 원문 열 길이가 다릅니다")
    return {
        "supporting_source_ids": [source.source_id for source in sources],
        "supporting_source_identities": [
            document_identity(source) for source in sources
        ],
        "supporting_evidence_hashes": [
            exact_evidence_text_hash(text) for text in evidence
        ],
    }


def _reseal_fact(fact: FactRecord, **changes: object) -> FactRecord:
    changed = replace(fact, evidence_binding="", **changes)
    return replace(changed, evidence_binding=fact_evidence_binding(changed))


def _numeric_labels(fact: FactRecord) -> dict[str, str]:
    try:
        period_start, period_end = fact.comparison_period.split("~", 1)
    except ValueError as error:
        raise ValueError("비교 사실의 완료 사업연도 기간이 손상됐습니다") from error
    if fact.comparison_metric == "영업이익률":
        return {
            "metric": "영업이익률 차이",
            "period_start": period_start,
            "period_end": period_end,
            "sign": "positive",
            "unit": "%p",
            "unit_dimension": "percentage_point",
            "formula": "comparison_operating_margin_difference_v1",
        }
    if fact.comparison_metric == "매출 규모":
        return {
            "metric": "매출 규모 배수",
            "period_start": period_start,
            "period_end": period_end,
            "sign": "positive",
            "unit": "배",
            "unit_dimension": "multiple",
            "formula": "comparison_revenue_ratio_v1",
        }
    raise ValueError("비교 생산기가 알 수 없는 수치 축을 만들었습니다")


def _candidate_for_fact(
    fact: FactRecord, candidates: tuple[CandidateEvidence, ...]
) -> CandidateEvidence:
    matches = tuple(
        candidate
        for candidate in candidates
        if candidate.candidate_name == fact.comparison_target
        and candidate.source is not None
        and candidate.evidence_text.strip()
    )
    if len(matches) != 1:
        raise ValueError("공개 비교 사실과 공식 후보 원문을 하나로 결속할 수 없습니다")
    return matches[0]


def _context_fact(
    base: FactRecord,
    *,
    suffix: str,
    slot: str,
    claim: str,
    primary_source: Source,
    sources: tuple[Source, ...],
    evidence: tuple[str, ...],
    support_terms: tuple[str, ...],
    comparison_metric: str,
) -> FactRecord:
    normalized_terms = normalized_support_terms(support_terms)
    if (
        len(normalized_terms) < MIN_PROSE_EVIDENCE_SUPPORT_TERMS
        or evidence_support_term_mismatches(claim, evidence[0], normalized_terms)
    ):
        raise ValueError(
            "비교 맥락 문장의 명시 근거어가 claim과 자사 원문 양쪽에 없습니다"
        )
    return _reseal_fact(
        base,
        fact_id=_stable_fact_id(base, suffix),
        claim=claim,
        claim_type=COMPETITIVE_COMPARISON_CONTEXT_CLAIM_TYPE,
        relationship_or_action=suffix,
        state_evidence=evidence[0],
        evidence_support_terms=list(normalized_terms),
        raw_value="",
        calculation="",
        display_value="",
        rounding_rule="",
        numeric_checks=[],
        metric="",
        period_start="",
        period_end="",
        sign="",
        unit="",
        unit_dimension="",
        formula="",
        claim_slot=slot,
        comparison_metric=comparison_metric,
        **_source_fields(primary_source),
        **_support_fields(sources, evidence),
    )


def _attach_comparison_only(
    packets: SectionEvidencePacketSet,
    comparison: ComparisonBuildResult,
) -> SectionEvidencePacketSet:
    """실제 비교 생산물을 competitive packet에 손실 없이 덧붙인다."""

    if not comparison.facts or not comparison.sources or not comparison.candidates:
        raise ValueError("V2 FULL 비교 브리지에 실제 비교 생산물이 없습니다")
    if any(
        fact.section_owner != COMPETITIVE_SECTION_ID
        or fact.claim_type != COMPETITIVE_COMPARISON_CLAIM_TYPE
        for fact in comparison.facts
    ):
        raise ValueError("V2 FULL에는 공식 비교 생산기의 FactRecord만 넣을 수 있습니다")

    maximum_number = max(
        int(fragment.fragment_id)
        for packet in packets.packets
        for fragment in packet.fragments
    )
    original_sources = {source.source_id: source for source in comparison.sources}
    if len(original_sources) != len(comparison.sources):
        raise ValueError("비교 Source ID가 중복됐습니다")

    evidence_by_source_id: dict[str, str] = {}
    base = comparison.facts[0]
    candidate = _candidate_for_fact(base, comparison.candidates)
    assert candidate.source is not None
    evidence_by_source_id[candidate.source.source_id] = candidate.evidence_text
    for fact in comparison.facts:
        for source_id, evidence in (
            (fact.source_id, fact.state_evidence),
            (fact.comparator_source_id, fact.comparator_state_evidence),
        ):
            previous = evidence_by_source_id.setdefault(source_id, evidence)
            if previous != evidence:
                raise ValueError("같은 비교 Source가 서로 다른 원문 bundle을 가리킵니다")

    sources_by_id: dict[str, Source] = {}
    next_number = maximum_number + 1
    for source in comparison.sources:
        evidence = evidence_by_source_id.get(source.source_id)
        if evidence:
            source = _with_exact_evidence(source, evidence, number=next_number)
        else:
            source = seal_collected_source(
                replace(
                    source,
                    number=next_number,
                    used_in=sorted({*source.used_in, COMPETITIVE_SECTION_ID}),
                )
            )
        sources_by_id[source.source_id] = source
        next_number += 1

    candidate_source = sources_by_id.get(candidate.source.source_id)
    if candidate_source is None:
        raise ValueError("비교 후보 공식 Source가 비교 결과 등록부에 없습니다")

    enriched: list[FactRecord] = []
    for fact in comparison.facts:
        self_source = sources_by_id.get(fact.source_id)
        comparator_source = sources_by_id.get(fact.comparator_source_id)
        if self_source is None or comparator_source is None:
            raise ValueError("양사 비교 FactRecord의 Source가 등록부에 없습니다")
        enriched.append(
            _reseal_fact(
                fact,
                claim_slot=_SLOT_JUDGMENT,
                **_numeric_labels(fact),
                **_source_fields(self_source),
                **_support_fields(
                    (self_source, comparator_source),
                    (fact.state_evidence, fact.comparator_state_evidence),
                ),
            )
        )

    first = enriched[0]
    self_source = sources_by_id[first.source_id]
    comparator_source = sources_by_id[first.comparator_source_id]
    dual_sources = (self_source, comparator_source)
    dual_evidence = (first.state_evidence, first.comparator_state_evidence)
    metric_summary = comparison_metric_summary(
        tuple(fact.comparison_metric for fact in enriched)
    )
    if not metric_summary:
        raise ValueError("비교 프로그램의 실제 수치 지표 집합이 손상됐습니다")
    target_claim = comparison_target_claim(
        evidence_text=candidate.evidence_text,
        comparison_target=first.comparison_target,
    )
    metric_claim = comparison_metric_claim(
        comparison_metric=metric_summary,
    )
    basis_claim = comparison_basis_claim()
    limitation_claim = comparison_limitation_claim()
    context_facts = (
        _context_fact(
            first,
            suffix="comparison_target",
            slot=_SLOT_TARGET,
            claim=target_claim,
            primary_source=candidate_source,
            sources=(candidate_source,),
            evidence=(candidate.evidence_text,),
            support_terms=candidate.evidence_support_terms,
            comparison_metric=metric_summary,
        ),
        _context_fact(
            first,
            suffix="comparison_metric",
            slot=_SLOT_METRIC,
            claim=metric_claim,
            primary_source=self_source,
            sources=dual_sources,
            evidence=dual_evidence,
            support_terms=("매출액", "영업이익"),
            comparison_metric=metric_summary,
        ),
        _context_fact(
            first,
            suffix="comparison_basis",
            slot=_SLOT_BASIS,
            claim=basis_claim,
            primary_source=self_source,
            sources=dual_sources,
            evidence=dual_evidence,
            support_terms=("매출액", "영업이익"),
            comparison_metric=metric_summary,
        ),
        _context_fact(
            first,
            suffix="comparison_limitation",
            slot=_SLOT_LIMITATION,
            claim=limitation_claim,
            primary_source=self_source,
            sources=dual_sources,
            evidence=dual_evidence,
            support_terms=("매출액", "영업이익"),
            comparison_metric=metric_summary,
        ),
    )
    facts = (*context_facts, *enriched)

    fragment_by_source_id: dict[str, CollectedFragment] = {}
    slots_by_source_id: dict[str, set[str]] = {}
    for fact in facts:
        for source_id in fact.supporting_source_ids:
            slots_by_source_id.setdefault(source_id, set()).add(fact.claim_slot)
    for source_id, slots in slots_by_source_id.items():
        source = sources_by_id[source_id]
        evidence = evidence_by_source_id[source_id]
        fragment_by_source_id[source_id] = CollectedFragment(
            fragment_id=str(source.number),
            kind=source.source_type or source.kind.value,
            text=evidence,
            # 봉인 Source의 formal kind·문서 지문·날짜·attestation을 필드마다
            # 다시 손으로 복사하지 않는다. 생산자와 packet 검증자가 같은
            # projection 정본을 써야 새 필드 하나만 빠진 legacy 강등을 막는다.
            **bound_source_fragment_provenance(source),
            supported_claim_slots=tuple(sorted(slots)),
            bound_source=source,
        )

    sentences = tuple(
        ComposedSentence(
            text=fact.claim,
            citations=tuple(
                fragment_by_source_id[source_id].fragment_id
                for source_id in fact.supporting_source_ids
            ),
            grade=GRADE_CONFIRMED,
            planned_claim_slot=fact.claim_slot,
            verification_state="verified",
            verified_fact_id=fact.fact_id,
        )
        for fact in facts
    )
    source_fragments = tuple(
        fragment_by_source_id[source.source_id]
        for source in sources_by_id.values()
        if source.source_id in fragment_by_source_id
    )
    # ``comparison.sources``에는 후보를 찾을 때만 쓴 기업개황 attester도 들어올
    # 수 있다. 그것까지 프로그램 등록부에 싣으면 실제 인용하지 않은 문서가
    # 품질 점수의 독립 문서 수를 부풀린다. 반대로 공식 웹 citation이 직접
    # 지목한 attester는 빠지면 안 된다. 따라서 공개 프로그램의 등록부는
    # bound citation과 그 citation의 1단계 provenance 의존성으로 정확히 닫는다.
    direct_source_ids = set(fragment_by_source_id)
    required_attester_ids = {
        sources_by_id[source_id].domain_attestation_source_id.strip()
        for source_id in direct_source_ids
        if sources_by_id[source_id].domain_attestation_source_id.strip()
    }
    required_attester_ids.update(comparison_basis_attester_source_ids(facts))
    missing_attester_ids = required_attester_ids - sources_by_id.keys()
    if missing_attester_ids:
        raise ValueError("비교 프로그램의 직접 도메인 attester가 등록부에 없습니다")
    program_source_ids = direct_source_ids | required_attester_ids
    program_sources = tuple(
        source
        for source_id, source in sources_by_id.items()
        if source_id in program_source_ids
    )
    if {source.source_id for source in program_sources} != program_source_ids:
        raise ValueError("비교 프로그램 Source 등록부를 정확히 닫을 수 없습니다")
    program = VerifiedProgramEvidence(
        section_id=COMPETITIVE_SECTION_ID,
        source_fragments=source_fragments,
        registry_sources=program_sources,
        facts=tuple(facts),
        sentences=sentences,
    )
    updated_packets = tuple(
        replace(
            packet,
            fragments=(*packet.fragments, *source_fragments),
            program_evidence=program,
        )
        if packet.section_id == COMPETITIVE_SECTION_ID
        else packet
        for packet in packets.packets
    )
    return SectionEvidencePacketSet(
        company_id=packets.company_id,
        evidence_generation_sha256=packets.evidence_generation_sha256,
        packets=updated_packets,
    )


def _stated_limitation_fact(base: FactRecord, source: Source) -> FactRecord:
    claim = (
        f"{base.legal_entity}가 공식 자료에서 밝힌 표현의 범위만 옮겼으며 "
        f"'{base.claim}'에 대한 타사 비교 판정은 포함하지 않습니다."
    )
    return _reseal_fact(
        base,
        fact_id=_stable_fact_id(base, "stated_differentiator_limitation"),
        claim=claim,
        claim_type=STATED_DIFFERENTIATOR_CLAIM_TYPE,
        relationship_or_action="회사 발표 표현의 한계",
        claim_slot=_SLOT_LIMITATION,
        raw_value="",
        calculation="",
        display_value="",
        rounding_rule="",
        numeric_checks=[],
        comparison_target="",
        comparison_metric="",
        comparison_definition="",
        comparison_basis="",
        comparison_period="",
        comparison_scope="",
        comparison_judgment="",
        comparator_source_id="",
        comparator_state_evidence="",
        comparator_evidence_support_terms=[],
        comparison_conditions={},
        **_source_fields(source),
        **_support_fields((source,), (base.state_evidence,)),
    )


def _attach_stated_program_evidence(
    packets: SectionEvidencePacketSet,
    comparison: ComparisonBuildResult,
    stated_facts: tuple[FactRecord, ...],
) -> SectionEvidencePacketSet:
    has_existing_program = any(
        packet.section_id == COMPETITIVE_SECTION_ID
        and packet.program_evidence is not None
        for packet in packets.packets
    )
    maximum_number = max(
        int(fragment.fragment_id)
        for packet in packets.packets
        for fragment in packet.fragments
    )
    original_sources = {source.source_id: source for source in comparison.sources}
    sources_by_id: dict[str, Source] = {}
    fragment_by_source_id: dict[str, CollectedFragment] = {}
    facts: list[FactRecord] = []
    next_number = maximum_number + 1
    for fact in stated_facts:
        original = original_sources.get(fact.source_id)
        if original is None:
            raise ValueError("회사 차별점 FactRecord의 공식 Source가 없습니다")
        # 같은 원문 문서가 선택 비교의 자사 수치 Source로도 쓰일 수 있다.
        # 프로그램 계약은 Source 하나와 exact fragment 하나를 1:1로 묶으므로,
        # 자기 선언 문장은 문서 신원을 유지한 별도 citation Source로 투영한다.
        stated_source_id = (
            f"{original.source_id}:stated:"
            f"{hashlib.sha256(fact.state_evidence.encode('utf-8')).hexdigest()[:16]}"
        )
        source = _with_exact_evidence(
            replace(original, source_id=stated_source_id),
            fact.state_evidence,
            number=next_number,
        )
        next_number += 1
        sources_by_id[source.source_id] = source
        enriched = _reseal_fact(
            fact,
            claim_type=STATED_DIFFERENTIATOR_CLAIM_TYPE,
            claim_slot=STATED_DIFFERENTIATOR_SLOT,
            **_source_fields(source),
            **_support_fields((source,), (fact.state_evidence,)),
        )
        facts.append(enriched)
        fragment_by_source_id[source.source_id] = CollectedFragment(
            fragment_id=str(source.number),
            kind=source.source_type or source.kind.value,
            text=fact.state_evidence,
            **bound_source_fragment_provenance(source),
            supported_claim_slots=(
                STATED_DIFFERENTIATOR_SLOT,
                _SLOT_LIMITATION,
            ),
            bound_source=source,
        )

    first = facts[0]
    first_source = sources_by_id[first.source_id]
    if not has_existing_program:
        facts.append(_stated_limitation_fact(first, first_source))
    direct_source_ids = set(fragment_by_source_id)
    required_attester_ids = {
        sources_by_id[source_id].domain_attestation_source_id.strip()
        for source_id in direct_source_ids
        if sources_by_id[source_id].domain_attestation_source_id.strip()
    }
    for attester_id in required_attester_ids:
        attester = original_sources.get(attester_id)
        if attester is None:
            raise ValueError("회사 차별점 공식 Source의 attester가 없습니다")
        sources_by_id[attester_id] = attester
    registry_sources = tuple(
        source
        for source_id, source in sources_by_id.items()
        if source_id in direct_source_ids | required_attester_ids
    )
    source_fragments = tuple(fragment_by_source_id.values())
    sentences = tuple(
        ComposedSentence(
            text=fact.claim,
            citations=tuple(
                str(sources_by_id[source_id].number)
                for source_id in fact.supporting_source_ids
            ),
            grade=GRADE_CONFIRMED,
            planned_claim_slot=fact.claim_slot,
            verification_state="verified",
            verified_fact_id=fact.fact_id,
        )
        for fact in facts
    )

    updated_packets = []
    for packet in packets.packets:
        if packet.section_id != COMPETITIVE_SECTION_ID:
            updated_packets.append(packet)
            continue
        existing = packet.program_evidence
        if existing is None:
            program = VerifiedProgramEvidence(
                section_id=COMPETITIVE_SECTION_ID,
                source_fragments=source_fragments,
                registry_sources=registry_sources,
                facts=tuple(facts),
                sentences=sentences,
            )
        else:
            merged_sources = {
                source.source_id: source for source in existing.registry_sources
            }
            for source in registry_sources:
                merged_sources.setdefault(source.source_id, source)
            program = VerifiedProgramEvidence(
                section_id=COMPETITIVE_SECTION_ID,
                source_fragments=(*existing.source_fragments, *source_fragments),
                registry_sources=tuple(merged_sources.values()),
                facts=(*existing.facts, *facts),
                sentences=(*existing.sentences, *sentences),
            )
        updated_packets.append(
            replace(
                packet,
                fragments=(*packet.fragments, *source_fragments),
                program_evidence=program,
            )
        )
    return SectionEvidencePacketSet(
        company_id=packets.company_id,
        evidence_generation_sha256=packets.evidence_generation_sha256,
        packets=tuple(updated_packets),
    )


def attach_comparison_program_evidence(
    packets: SectionEvidencePacketSet,
    comparison: ComparisonBuildResult,
) -> SectionEvidencePacketSet:
    """회사 자기 선언은 필수로, 동일 조건 비교는 있을 때만 typed 근거로 붙인다."""

    stated_facts = tuple(
        fact
        for fact in comparison.facts
        if fact.claim_type == STATED_DIFFERENTIATOR_CLAIM_TYPE
    )
    comparison_facts = tuple(
        fact
        for fact in comparison.facts
        if fact.claim_type == COMPETITIVE_COMPARISON_CLAIM_TYPE
    )
    if not stated_facts:
        # 과거 비교-only fixture와 저장 결과의 독립 브리지 검증은 유지한다.
        # 실제 FULL 파이프라인은 이 함수에 오기 전에 자기 선언 fact를 필수 검사한다.
        return _attach_comparison_only(packets, comparison)
    if len(stated_facts) + len(comparison_facts) != len(comparison.facts):
        raise ValueError("V2 FULL 9장에 알 수 없는 프로그램 사실이 섞였습니다")
    updated = packets
    if comparison_facts:
        updated = _attach_comparison_only(
            packets,
            replace(comparison, facts=comparison_facts),
        )
    return _attach_stated_program_evidence(updated, comparison, stated_facts)


__all__ = ["attach_comparison_program_evidence"]
