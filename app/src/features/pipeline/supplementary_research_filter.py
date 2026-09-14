"""최종 대조에서 불일치한 사실과 그 공개 표현만 제거한다.

자료 수량을 세는 guard와 실제 공개 내용 제거를 분리한다. 기존 작성기에서
검증한 legacy 재무 표는 typed 공식 수집 목록에 없다는 이유로 지우지 않는다.
"""

from dataclasses import replace

from src.core.citations import (
    INTERPRETATION_SUFFIX, citation_number, split_citation_markers, split_interpretation_marker,
)
from src.features.pipeline.constants import EVIDENCE_AVAILABLE_PUBLICATION_POLICY
from src.features.pipeline.port import Grade, Report, ReportSection, ReportTable
from src.features.pipeline.supplementary_fact_binding import bound_supplementary_fact_sources
from src.features.pipeline.supplementary_research_release import (
    _citation_registry, _collected_documents, _normalized, _row_citation_numbers,
    _source_can_ground_body, _visible_fact_ids,
)
from src.features.pipeline.supplementary_research_runtime_constants import (
    SUPPLEMENTARY_RESEARCH_FILTER_NOTICE,
)
from src.shared.report_evidence.runtime_port import OfficialEvidenceCollectionResult
from src.shared.report_evidence.source_verification import SourceVerifier
from src.shared.report_quality.output_validation import _cited_numbers_in_body


def _displayed_numbers(report: Report) -> set[str]:
    """출고 검증과 같은 본문·요약·표의 공개 인용 위치만 읽는다."""
    return {str(number) for number in _cited_numbers_in_body(report, citation_number)}


def prune_unused_supplementary_citations(
    report: Report, *, numbers_by_source_id: dict[str, str] | None = None,
) -> Report:
    """공개 사용과 잔여 사실 참조가 모두 없는 부록만 제거한다. 새 인용은 만들지 않는다."""
    displayed = _displayed_numbers(report)
    referenced_ids = {source_id for fact in report.fact_records
                      for source_id in (fact.source_id, *fact.supporting_source_ids)}
    numbers = numbers_by_source_id or {
        source.source_id: str(getattr(source, "number", 0)) for source in report.citations
    }
    citations = [source for source in report.citations
                 if getattr(source, "provenance_role", "citation") != "citation"
                 or source.source_id in referenced_ids or numbers.get(source.source_id) in displayed]
    kept_numbers = {numbers.get(source.source_id) for source in citations}
    source_grades = {number: grades for number, grades in report.source_grades.items()
                     if number in kept_numbers}
    if citations == report.citations and source_grades == report.source_grades:
        return report
    return replace(report, citations=citations, source_grades=source_grades)


def reconcile_supplementary_citations(report: Report, *, source_verifier: SourceVerifier) -> Report:
    """이미 걸러진 본문에 맞춰 인용 표시와 부록만 정리한다. 출처를 재서명하지 않는다."""
    if not report.citations:
        return replace(report, source_grades={}) if report.source_grades else report
    registry_result = _citation_registry(report, source_verifier)
    if registry_result is None:
        raise ValueError("공개 인용 정리 전에 출처 등록부 검사가 필요합니다")
    registry, by_id, _by_number = registry_result
    facts = {fact.fact_id: fact for fact in report.fact_records}
    displayed = _displayed_numbers(report)
    sections = []
    for section in report.sections:
        prose = []
        for text, cite in section.prose_lines:
            body, interpreted = split_interpretation_marker(text)
            parts = split_citation_markers(body)
            numbers = {str(part.number) for part in parts if part.number > 0}
            claim = _normalized("".join(part.text for part in parts))
            ids = {fact_id for fact_id in section.fact_ids
                   if fact_id in facts and facts[fact_id].section_owner == section.cell
                   and _normalized(facts[fact_id].claim) == claim}
            if not numbers and not citation_number(cite) and len(ids) == 1:
                bindings = bound_supplementary_fact_sources(
                    facts[next(iter(ids))], registry=registry, source_verifier=source_verifier,
                    reference_date=report.as_of_date,
                )
                bound_numbers = {str(source.number) for source in bindings}
                # 앞 문장의 제거로 생략 인용만 남은 경우, 잠긴 사실과 기존
                # 봉인 출처에 다시 결속되는 번호만 표시한다. 새 근거는 붙이지 않는다.
                if bound_numbers - displayed:
                    markers = " ".join(f"[{number}]" for number in sorted(bound_numbers, key=int))
                    text = body.rstrip() + " " + markers + (INTERPRETATION_SUFFIX if interpreted else "")
                    displayed.update(bound_numbers)
            prose.append((text, cite))
        sections.append(
            replace(section, prose_lines=prose, prose_paragraphs=[text for text, _cite in prose])
            if prose != section.prose_lines else section
        )
    # 출처 DTO와 번호·봉인은 그대로 둔다. 숨은 회사 신원 증명도 보존한다.
    numbers = {source.source_id: str(by_id[source.source_id][1].number if source.source_id in by_id
                                   else getattr(source, "number", 0)) for source in report.citations}
    restored = replace(report, sections=sections) if sections != report.sections else report
    return prune_unused_supplementary_citations(restored, numbers_by_source_id=numbers)


def filter_supplementary_research_report(
    report: Report,
    *,
    official_evidence: OfficialEvidenceCollectionResult,
    source_verifier: SourceVerifier,
) -> Report:
    """등록부 검사를 통과한 보고서를 받아 변경 없으면 같은 객체를 반환한다."""
    collected = _collected_documents(official_evidence)
    registry_result = _citation_registry(report, source_verifier)
    if collected is None or registry_result is None:
        raise ValueError("불일치 근거 제거 전에 출처 등록부 검사가 필요합니다")
    registry, by_id, by_number = registry_result
    governed_sources = {
        source_id for source_id, (_source, verified) in by_id.items()
        if (verified.formal_kind or verified.news)
        and getattr(_source, "provenance_role", "citation") == "citation"
    }
    governed_numbers = {
        number for number, (_source, verified) in by_number.items()
        if verified.source_id in governed_sources
    }
    facts = {fact.fact_id: fact for fact in report.fact_records}
    governed = {
        fact.fact_id for fact in report.fact_records
        if fact.source_id in governed_sources
        or set(fact.supporting_source_ids).intersection(governed_sources)
    }
    invalid_sources = {
        source_id for source_id, (_source, verified) in by_id.items()
        if source_id in governed_sources and not _source_can_ground_body(verified, collected)
    }
    invalid_numbers = {
        number for number, (_source, verified) in by_number.items()
        if verified.source_id in invalid_sources
    }
    invalid_ids: set[str] = set()
    for fact_id in governed:
        bindings = bound_supplementary_fact_sources(
            facts[fact_id], registry=registry, source_verifier=source_verifier,
            reference_date=report.as_of_date,
        )
        if not bindings or any(source.source_id in invalid_sources for source in bindings):
            invalid_ids.add(fact_id)

    def prose_matches(section: ReportSection, text: str):
        body, _interpreted = split_interpretation_marker(text)
        parts = split_citation_markers(body)
        claim = _normalized("".join(part.text for part in parts))
        ids = {
            fact_id for fact_id in section.fact_ids
            if fact_id in governed and facts[fact_id].section_owner == section.cell
            and _normalized(facts[fact_id].claim) == claim
        }
        numbers = {str(part.number) for part in parts if part.number > 0}
        return ids, numbers

    rejected_lines: set[tuple[str, int]] = set()
    for section in report.sections:
        for index, (text, cite) in enumerate(section.prose_lines):
            ids, numbers = prose_matches(section, text)
            if legacy := citation_number(cite):
                numbers.add(legacy)
            visible = _visible_fact_ids(
                replace(section, prose_lines=[(text, cite)], tables=[]), facts, by_number,
            )
            # 같은 출처를 이어 쓰는 문장은 공개 번호를 생략할 수 있다. 본문과
            # 잠긴 사실이 일치해야 하고, 명시한 번호는 정확히 맞아야 한다.
            implicit_citation = not numbers and len(ids) == 1
            if (
                numbers.intersection(invalid_numbers)
                or (ids and not visible and not implicit_citation)
                or (numbers.intersection(governed_numbers) and not ids and not visible)
            ):
                rejected_lines.add((section.cell, index))
                invalid_ids.update(ids)

    # 제거한 사실을 해석·변화·대응의 기초로 재사용하지 않는다.
    while True:
        dependants = {
            fact.fact_id for fact in report.fact_records
            if invalid_ids.intersection([
                *fact.basis_fact_ids, fact.revenue_model_fact_id, fact.response_to_fact_id,
            ])
        }
        if dependants <= invalid_ids:
            break
        invalid_ids.update(dependants)

    invalid_claims = {_normalized(facts[fact_id].claim) for fact_id in invalid_ids}

    def filter_table(section: ReportSection, table: ReportTable) -> ReportTable | None:
        keep: list[int] = []
        for index, row in enumerate(table.rows):
            numbers = _row_citation_numbers(table, index)
            row_id = table.row_fact_ids[index] if index < len(table.row_fact_ids) else ""
            claims = {_normalized(value) for value in (
                *row, " ".join(row), " | ".join(row), f"{table.caption}: " + " | ".join(row),
            )}
            matching = {
                fact_id for fact_id in section.fact_ids
                if fact_id in governed and _normalized(facts[fact_id].claim) in claims
                and facts[fact_id].section_owner == section.cell
            }
            expected_sources = {
                source_id for fact_id in matching
                for source_id in facts[fact_id].supporting_source_ids
            }
            actual_sources = {
                by_number[number][1].source_id for number in numbers if number in by_number
            }
            if (
                row_id in invalid_ids or numbers.intersection(invalid_numbers)
                or claims.intersection(invalid_claims)
                or (matching and (
                    not numbers <= set(by_number) or actual_sources != expected_sources
                ))
            ):
                continue
            keep.append(index)
        if len(keep) == len(table.rows):
            return table
        if not keep:
            return None
        changes = {}
        for field in (
            "rows", "raw_rows", "evidence_rows", "row_fact_ids", "row_evidence_refs",
            "row_binding_refs", "cell_binding_refs", "row_cites",
        ):
            values = getattr(table, field)
            if values:
                changes[field] = [values[index] for index in keep if index < len(values)]
        if changes.get("row_cites"):
            cites = list(dict.fromkeys(cite for row in changes["row_cites"] for cite in row))
            changes.update(source_cites=cites, cite=cites[0] if cites else "")
        return replace(table, **changes)

    sections = []
    for section in report.sections:
        prose = [
            (text, cite) for index, (text, cite) in enumerate(section.prose_lines)
            if (section.cell, index) not in rejected_lines
            and not prose_matches(section, text)[0].intersection(invalid_ids)
        ]
        tables = [
            filtered for table in section.tables
            if (filtered := filter_table(section, table)) is not None
        ]
        fact_ids = [fact_id for fact_id in section.fact_ids if fact_id not in invalid_ids]
        if prose == section.prose_lines and tables == section.tables and fact_ids == section.fact_ids:
            sections.append(section)
            continue
        sections.append(replace(
            section, prose_lines=prose, prose_paragraphs=[text for text, _cite in prose],
            tables=tables, fact_ids=fact_ids,
            lines=[(text, cite) for text, cite in section.lines
                   if _normalized(text) not in invalid_claims
                   and not prose_matches(section, text)[0].intersection(invalid_ids)
                   and citation_number(cite) not in invalid_numbers],
            empty_reason=(section.empty_reason if prose or tables else SUPPLEMENTARY_RESEARCH_FILTER_NOTICE),
        ))
    content_changed = bool(invalid_ids or rejected_lines or sections != report.sections)
    summaries = ([item for item in report.summary_items
                  if item.fact_ids and not invalid_ids.intersection(item.fact_ids)]
                 if content_changed else report.summary_items)
    candidate = (replace(report, sections=sections, summary_items=summaries,
                         fact_records=[fact for fact in report.fact_records if fact.fact_id not in invalid_ids],
                         citations=[source for source in report.citations
                                    if getattr(source, "source_id", "") not in invalid_sources])
                 if content_changed else report)
    reconciled = reconcile_supplementary_citations(candidate, source_verifier=source_verifier)
    if not content_changed and reconciled is report:
        return report
    # 원래 FULL 봉인은 내용 변경 뒤 재사용할 수 없다. 부분 보고서라는 사실과
    # 원 실행의 비용·생성 지표는 보존하고, 공개 승인·캐시 권한만 거둔다.
    return replace(
        reconciled,
        grade=Grade.PARTIAL, publication_policy=EVIDENCE_AVAILABLE_PUBLICATION_POLICY,
        release_mode="", quality_contract_version="", safety_decision="",
        public_structure_manifest="", public_projection=None, generation_evidence=None,
        quality_observation=None,
        cells={section.cell: section.is_filled for section in sections},
        shortfall_reasons=list(dict.fromkeys([
            *report.shortfall_reasons, SUPPLEMENTARY_RESEARCH_FILTER_NOTICE,
        ])),
    )
