"""도식의 독립 검수 결과를 정확한 행·자기 인용에 묶어 공개까지 보존한다."""

from collections.abc import Mapping, Sequence
from dataclasses import replace

from src.features.composer.flow_review_constants import (
    FLOW_REVIEW_BINDING_INVALID,
    FLOW_REVIEW_BINDING_MISSING,
    FLOW_REVIEW_PATHS,
    FLOW_REVIEW_RULE_VERSION,
)
from src.features.composer.port import (
    CollectedFragment, ComposedReport, FlowEvidenceRef, FlowReviewBinding, FlowRow,
)
from src.shared.report_generation.models import canonical_sha256, exact_text_sha256


def _binding(
    row: FlowRow,
    *,
    section_id: str,
    fragments: Mapping[str, CollectedFragment],
    review_path: str,
    baseline_date: str,
) -> FlowReviewBinding:
    if not section_id or review_path not in FLOW_REVIEW_PATHS or type(baseline_date) is not str:
        raise ValueError("도식 검수 장·경로·기준일이 올바르지 않습니다")
    if (not row.cells or any(type(cell) is not str for cell in row.cells)
            or not any(cell.strip() for cell in row.cells)
            or not row.citations
            or any(type(fid) is not str or not fid or fid != fid.strip() for fid in row.citations)
            or len(set(row.citations)) != len(row.citations)):
        raise ValueError("도식 검수 행의 칸·인용이 비었거나 중복됐습니다")
    refs = []
    for fid in row.citations:
        fragment = fragments.get(fid)
        if (type(fragment) is not CollectedFragment or fragment.fragment_id != fid
                or not fragment.text or not fragment.text.strip()):
            raise ValueError("도식 검수 행이 인용한 정확 원문을 찾지 못했습니다")
        refs.append(FlowEvidenceRef(
            fragment_id=fid,
            document_identity=fragment.document_identity,
            document_content_sha256=fragment.document_content_sha256,
            exact_evidence_sha256=exact_text_sha256(fragment.text),
            document_date=fragment.document_date,
            source_scope_sha256=canonical_sha256({
                "source_url": fragment.source_url,
                "source_document_id": fragment.source_document_id,
                "document_title": fragment.document_title,
                "location": fragment.location,
                "kind": fragment.kind,
                "formal_source_kind": fragment.formal_source_kind,
                "source_publisher": fragment.source_publisher,
                "supported_claim_slots": fragment.supported_claim_slots,
                "identity_binding": fragment.identity_binding,
                "news_grounded": fragment.news_grounded,
                "news_event_on": fragment.news_event_on,
                "news_temporal_status": fragment.news_temporal_status,
            }),
        ))
    candidate_sha256 = canonical_sha256({
        "rule_version": FLOW_REVIEW_RULE_VERSION,
        "section_id": section_id,
        "cells": row.cells,
        "citations": row.citations,
        "baseline_date": baseline_date,
        "review_path": review_path,
    })
    return FlowReviewBinding(section_id, candidate_sha256, tuple(refs), review_path,
                             FLOW_REVIEW_RULE_VERSION, baseline_date)


def bind_reviewed_flow_row(
    row: FlowRow,
    *,
    section_id: str,
    fragments: Mapping[str, CollectedFragment],
    review_path: str,
    baseline_date: str = "",
) -> FlowRow:
    """최종 참과 모든 가드를 통과한 호출부만 검수 입력을 봉인한다."""
    binding = _binding(row, section_id=section_id, fragments=fragments,
                       review_path=review_path, baseline_date=baseline_date)
    return replace(row, review_binding=binding)


def flow_review_problem(
    row: FlowRow,
    *,
    section_id: str,
    fragments: Mapping[str, CollectedFragment],
    baseline_date: str = "",
) -> str:
    """검수 후 바뀐 행·근거·기준일은 재검수 없이 공개하지 않는다."""
    binding = row.review_binding
    if binding is None:
        return FLOW_REVIEW_BINDING_MISSING
    if type(binding) is not FlowReviewBinding:
        return FLOW_REVIEW_BINDING_INVALID
    try:
        expected = _binding(row, section_id=section_id, fragments=fragments,
                            review_path=binding.review_path, baseline_date=baseline_date)
    except (ValueError, TypeError, AttributeError):
        return FLOW_REVIEW_BINDING_INVALID
    return "" if expected == binding else FLOW_REVIEW_BINDING_INVALID


def filter_reviewed_flow_rows(
    report: ComposedReport,
    fragments: Sequence[CollectedFragment] | Mapping[str, CollectedFragment],
    *,
    baseline_date: str = "",
    diagnostics: list[dict] | None = None,
) -> ComposedReport:
    """공개 전 생존 행을 확정한다. renderer·manifest에는 같은 결과를 넘긴다."""
    if isinstance(fragments, Mapping):
        by_id = dict(fragments)
    else:
        by_id = {}
        duplicate_ids = set()
        for fragment in fragments:
            if fragment.fragment_id in by_id:
                duplicate_ids.add(fragment.fragment_id)
            by_id[fragment.fragment_id] = fragment
        # 중복 ID에서 마지막 원문을 임의 선택해 승인하지 않는다.
        for fragment_id in duplicate_ids:
            by_id.pop(fragment_id)
    sections = []
    for section in report.sections:
        kept = []
        for row in section.flow_rows:
            problem = flow_review_problem(row, section_id=section.section_id,
                                          fragments=by_id, baseline_date=baseline_date)
            if not problem:
                kept.append(row)
            elif diagnostics is not None:
                diagnostics.append({
                    "section_id": section.section_id, "kind": "도식",
                    "reason_code": problem,
                    # 기존 원문 없는 진단은 raw 문자열 지문을 사용한다.
                    "candidate_sha256": exact_text_sha256(" ".join(row.cells)),
                    "source_fragment_ids": list(row.citations),
                    "verification_items": (),
                })
        sections.append(replace(section, flow_rows=tuple(kept)))
    return replace(report, sections=tuple(sections))
