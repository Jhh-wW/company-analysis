"""`Report` ↔ JSON 직렬화, 그리고 DB 저장·조회.

★ `Report`는 `features/pipeline/port.py`의 것을 그대로 쓴다 — 여기서 새
  자료구조를 만들지 않는다. `sections`·`citations`까지 «전부» 되살아나야
  재내보내기(워드·노션)가 화면과 같은 문서를 다시 만들 수 있다
  (저장 구간이 갖는 것 — "재내보내기가 화면과 «똑같은» 문서를
  다시 만들 수 있는 최소 집합").

★ S2(공고 원문 미저장) — 이 파일이 받는 것은 `Report` 객체 «하나뿐»이다.
  `Report`에는 애초에 공고 원문(`UserInput.posting_text`) 필드가 없다.
  그래서 공고 원문은 «구조적으로» 이 파일을 거쳐 DB에 들어갈 길이 없다 —
  코드로 막는 게 아니라 자료구조에 자리 자체가 없다.

구간이 갖는 것
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from dataclasses import fields, replace
from typing import Any, Optional

from src.core.constants import COUNTED_CELLS, HIDDEN_CELLS
from src.core.persisted_json import validate_persisted_json_text
from src.features.grading.logic import grade_of
from src.features.pipeline.port import (
    FactRecord,
    Grade,
    Report,
    ReportSection,
    ReportTable,
    SourceStatus,
    SummaryItem,
)
from src.features.provenance.sources import (
    Source,
    SourceKind,
    stored_sources_seal_problem,
)
from src.features.report_standard.constants import CANONICAL_SCHEMA_VERSION
from src.features.storage.constants import (
    TABLE_REPORT_PUBLIC_PROJECTIONS,
    TABLE_REPORTS,
)
from src.shared.report_quality.constants import STRICT_QUALITY_CONTRACT_VERSION
from src.shared.report_quality.generation import (
    assert_observation_matches_assessment,
    generation_quality_observation_from_dict,
    generation_quality_observation_to_dict,
)
from src.shared.report_generation.constants import ENGINE_V2_SCHEMA_VERSION
from src.shared.report_generation.canonical import (
    PublicManifestError,
    assert_report_matches_generation_evidence,
)
from src.shared.report_generation.models import (
    generation_metrics_from_dict,
    generation_metrics_to_dict,
    producer_evidence_from_dict,
    producer_evidence_to_dict,
)
from src.shared.report_evidence.constants import ReleaseMode
from src.shared.report_generation.public_projection import (
    PublicReportProjection,
    build_report_digest,
    public_report_projection_from_dict,
    public_report_projection_to_dict,
)

# ══════════════════════════════════════════════════════════
# 직렬화 — dataclass ↔ dict (JSON에 바로 쓸 수 있는 모양)
# ══════════════════════════════════════════════════════════


def _table_to_dict(table: ReportTable) -> dict[str, Any]:
    payload = {
        "caption": table.caption,
        "headers": list(table.headers),
        "rows": [list(row) for row in table.rows],
        "cite": table.cite,
        "numeric": table.numeric,
        "raw_rows": [list(row) for row in table.raw_rows],
        "scale_divisor": table.scale_divisor,
        "scale_places": table.scale_places,
        "display_unit": table.display_unit,
    }
    # FULL 구조 manifest는 원문 자체가 아니라 행/셀 typed ref·전체 출처·표
    # 참조만 저장한다. 원문 evidence_rows는 수집 경계 밖 장기 저장물이 아니다.
    if table.manifest_ref:
        # manifest가 없는 SHADOW/과거 표는 이 키들이 없던 저장 bytes를 그대로
        # 유지한다. strict 표만 네 필드를 한 묶음으로 왕복한다.
        payload["source_cites"] = list(table.source_cites)
        payload["manifest_ref"] = table.manifest_ref
        payload["row_fact_ids"] = list(table.row_fact_ids)
        payload["row_evidence_refs"] = list(table.row_evidence_refs)
        payload["row_binding_refs"] = list(table.row_binding_refs)
        payload["cell_binding_refs"] = [
            list(row) for row in table.cell_binding_refs
        ]
    # 옛 payload의 암묵적 기본값은 그대로 직렬화해야 기존 보고서 hash·자동출고
    # 승인이 이유 없이 바뀌지 않는다. 실제 시각화 힌트가 있을 때만 새 키를 저장한다.
    if table.presentation != "table":
        payload["presentation"] = table.presentation
    for name in ("entity_scope", "raw_unit", "unit_dimension"):
        value = getattr(table, name, "")
        if value:
            payload[name] = value
    return payload


def _table_from_dict(data: dict[str, Any]) -> ReportTable:
    return ReportTable(
        caption=data["caption"],
        headers=list(data["headers"]),
        rows=[list(row) for row in data["rows"]],
        cite=data.get("cite", ""),
        numeric=bool(data.get("numeric", False)),
        raw_rows=[list(row) for row in data.get("raw_rows", [])],
        scale_divisor=str(data.get("scale_divisor", "")),
        scale_places=int(data.get("scale_places", 0)),
        display_unit=str(data.get("display_unit", "")),
        # strict 저장에는 원문 행을 넣지 않는다. manifest/typed refs가 검증한다.
        evidence_rows=[],
        presentation=str(data.get("presentation", "table")),
        entity_scope=str(data.get("entity_scope", "")),
        raw_unit=str(data.get("raw_unit", "")),
        unit_dimension=str(data.get("unit_dimension", "")),
        source_cites=[str(value) for value in data.get("source_cites", [])],
        manifest_ref=str(data.get("manifest_ref", "")),
        row_fact_ids=[str(value) for value in data.get("row_fact_ids", [])],
        row_evidence_refs=[
            str(value) for value in data.get("row_evidence_refs", [])
        ],
        row_binding_refs=[
            str(value) for value in data.get("row_binding_refs", [])
        ],
        cell_binding_refs=[
            [str(value) for value in row]
            for row in data.get("cell_binding_refs", [])
        ],
    )


def _prose_lines_from_dict(
    data: dict[str, Any], *, is_v2: bool
) -> list[tuple[str, str]]:
    """선택 층인 표시용 글만 안전하게 되살린다.

    ★ v1(canonical)은 옛 저장값에 이 필드가 없을 수 있다. 깨진 항목·출처
      없는 항목·옛 문자열 prose는 검증 여부를 증명할 수 없으므로 버리고,
      근거 원문 보고서는 계속 연다 — v1은 줄마다 cite(부록
      번호 표기)가 있어야 «검증된 표시용 글»로 본다.
    ★ v2(엔진 v2 composer)는 다르다: 인용 번호를 cite 필드가 아니라 문장
      텍스트 안 "[n]" 표기로 담고(render.sentence_display_text), «해석»
      문장이나 안내문은 인용 자체가 없어도 정당하다(render.py의 모든
      prose_line이 cite=""로 저장된다). v1의 «cite 없으면 버림» 규칙을
      v2에 그대로 적용하면 저장된 v2 본문이 «전부» 사라진다(실측 결함 —
      유료 실행이 완주해 87문장을 저장했는데, 재로드 시 prose_lines가
      0개가 되어 인용-부록 불일치로 결과 화면이 409로 막혔다). 그래서
      v2는 cite가 비어 있어도 글만 있으면 살린다.
    """
    out: list[tuple[str, str]] = []
    for item in data.get("prose_lines", []):
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            continue
        text, cite = item
        if not isinstance(text, str) or not isinstance(cite, str):
            continue
        if not text.strip():
            continue
        if is_v2 or cite.strip():
            out.append((text, cite))
    return out


def _section_to_dict(section: ReportSection) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "cell": section.cell,
        "title": section.title,
        "lines": [[text, cite] for text, cite in section.lines],
        # ★ 검증된 표시용 글도 저장해야 서버 재시작·워드·노션에서 화면과 같다.
        #   문장별 출처를 잃지 않도록 문자열 하나가 아니라 2열 목록으로 저장한다.
        "prose_lines": [[text, cite] for text, cite in section.prose_lines],
        # 회사 사실이 아닌 프로그램 제안 질문은 출처 문장과 별도 필드로 보존한다.
        "guidance_lines": list(section.guidance_lines),
        "display_number": section.display_number,
        "tag": section.tag,
        "fact_ids": list(section.fact_ids),
        "empty_reason": section.empty_reason,
        "tables": [_table_to_dict(t) for t in section.tables],
    }
    # 화면·PDF가 문단을 만드는 단위. 저장하지 않으면 다시 열었을 때 문단이
    # 통째로 사라져 한 덩어리로 보인다(v2 본문 소실과 같은 유형).
    # ★ 비어 있으면 키를 «넣지 않는다» — 옛 보고서의 저장 바이트와 해시가
    #   그대로 유지돼야 한다(결속 검사).
    if section.prose_paragraphs:
        payload["prose_paragraphs"] = list(section.prose_paragraphs)
    return payload


def _section_from_dict(data: dict[str, Any], *, is_v2: bool) -> ReportSection:
    return ReportSection(
        cell=data["cell"],
        title=data["title"],
        lines=[(text, cite) for text, cite in data.get("lines", [])],
        # 옛 저장 보고서에는 키가 없다. 빈 목록으로 읽어 원문 보고서를 그대로 살린다.
        prose_lines=_prose_lines_from_dict(data, is_v2=is_v2),
        prose_paragraphs=[
            str(text) for text in (data.get("prose_paragraphs") or []) if str(text).strip()
        ],
        guidance_lines=[
            item.strip()
            for item in data.get("guidance_lines", [])
            if isinstance(item, str) and item.strip()
        ],
        display_number=str(data.get("display_number", "")),
        tag=str(data.get("tag", "")),
        fact_ids=[
            item.strip()
            for item in data.get("fact_ids", [])
            if isinstance(item, str) and item.strip()
        ],
        empty_reason=data.get("empty_reason", ""),
        tables=[_table_from_dict(t) for t in data.get("tables", [])],
    )


def _source_status_to_dict(status: SourceStatus) -> dict[str, Any]:
    return {"name": status.name, "state": status.state, "detail": status.detail}


def _source_status_from_dict(data: dict[str, Any]) -> SourceStatus:
    return SourceStatus(
        name=data["name"], state=data["state"], detail=data.get("detail", "")
    )


def _citation_to_dict(citation: object) -> dict[str, Any]:
    """`Report.citations`는 `list[object]`로 열려 있지만 실제로는 `Source`뿐이다.

    Raises:
        TypeError: `Source`가 아닌 값이 섞여 있을 때 — 조용히 버리지 않고
            바로 알린다. 출처가 조용히 사라지면 W 검사·C3가 못 잡는다.
    """
    if not isinstance(citation, Source):
        raise TypeError(f"알 수 없는 출처 타입입니다: {type(citation)!r}")
    payload = {
        "number": citation.number,
        "kind": citation.kind.value,
        "label": citation.label,
        "disclosed_at": citation.disclosed_at,
        "collected_at": citation.collected_at,
        "published_at": citation.published_at,
        "domain": citation.domain,
        "source_id": citation.source_id,
        "title": citation.title,
        "publisher": citation.publisher,
        "host": citation.host,
        "url": citation.url,
        "document_id": citation.document_id,
        "location": citation.location,
        "source_type": citation.source_type,
        "fact_status": citation.fact_status,
        "used_in": list(citation.used_in),
        "evidence_hashes": list(citation.evidence_hashes),
        "domain_attestation_source_id": citation.domain_attestation_source_id,
        "domain_attestation_evidence": citation.domain_attestation_evidence,
        "provenance_seal": citation.provenance_seal,
    }
    # 기본 citation은 기존 저장 JSON·release ledger digest와 byte 호환을
    # 유지한다. 새 내부 attester만 역할 필드를 명시해 재시작 뒤에도 숨김 정책과
    # provenance seal을 잃지 않는다.
    if citation.provenance_role != "citation":
        payload["provenance_role"] = citation.provenance_role
    # 빈 목록은 이 필드가 없던 저장 JSON과 release digest를 그대로 유지한다.
    if citation.exact_evidence_hashes:
        payload["exact_evidence_hashes"] = list(citation.exact_evidence_hashes)
    if citation.reporting_period:
        payload["reporting_period"] = citation.reporting_period
    if citation.attachment_url:
        payload["attachment_url"] = citation.attachment_url
    if citation.domain_redirect_verification:
        payload["domain_redirect_verification"] = citation.domain_redirect_verification
    if citation.domain_redirect_from_host:
        payload["domain_redirect_from_host"] = citation.domain_redirect_from_host
    if citation.domain_redirect_to_host:
        payload["domain_redirect_to_host"] = citation.domain_redirect_to_host
    return payload


def _citation_from_dict(data: dict[str, Any]) -> Source:
    return Source(
        number=data["number"],
        kind=SourceKind(data["kind"]),
        label=data["label"],
        disclosed_at=data.get("disclosed_at", ""),
        collected_at=data.get("collected_at", ""),
        published_at=data.get("published_at", ""),
        reporting_period=data.get("reporting_period", ""),
        attachment_url=data.get("attachment_url", ""),
        domain_redirect_verification=data.get("domain_redirect_verification", ""),
        domain_redirect_from_host=data.get("domain_redirect_from_host", ""),
        domain_redirect_to_host=data.get("domain_redirect_to_host", ""),
        domain=data.get("domain", ""),
        source_id=data.get("source_id", ""),
        title=data.get("title", ""),
        publisher=data.get("publisher", ""),
        host=data.get("host", ""),
        url=data.get("url", ""),
        document_id=data.get("document_id", ""),
        location=data.get("location", ""),
        source_type=data.get("source_type", ""),
        fact_status=data.get("fact_status", ""),
        used_in=[
            item.strip()
            for item in data.get("used_in", [])
            if isinstance(item, str) and item.strip()
        ],
        evidence_hashes=[
            item.strip()
            for item in data.get("evidence_hashes", [])
            if isinstance(item, str) and item.strip()
        ],
        exact_evidence_hashes=[
            item.strip()
            for item in data.get("exact_evidence_hashes", [])
            if isinstance(item, str) and item.strip()
        ],
        domain_attestation_source_id=str(
            data.get("domain_attestation_source_id", "")
        ).strip(),
        domain_attestation_evidence=str(
            data.get("domain_attestation_evidence", "")
        ).strip(),
        provenance_seal=str(data.get("provenance_seal", "")).strip(),
        provenance_role=str(data.get("provenance_role", "citation")).strip(),
    )


def _summary_to_dict(item: SummaryItem) -> dict[str, Any]:
    return {
        "text": item.text,
        "section_id": item.section_id,
        "fact_ids": list(item.fact_ids),
        "evidence_text": item.evidence_text,
        "verification_status": item.verification_status,
        "verification_binding": item.verification_binding,
        "support_terms": list(item.support_terms),
    }


def _summary_from_dict(data: dict[str, Any]) -> SummaryItem:
    return SummaryItem(
        text=str(data.get("text", "")),
        section_id=str(data.get("section_id", "")),
        fact_ids=[
            item.strip()
            for item in data.get("fact_ids", [])
            if isinstance(item, str) and item.strip()
        ],
        evidence_text=str(data.get("evidence_text", "")),
        verification_status=str(data.get("verification_status", "")),
        verification_binding=str(data.get("verification_binding", "")),
        support_terms=[
            item.strip()
            for item in data.get("support_terms", [])
            if isinstance(item, str) and item.strip()
        ],
    )


_FACT_FIELDS = tuple(item.name for item in fields(FactRecord))
_OPTIONAL_STRUCTURED_FACT_FIELDS = frozenset(
    {
        "claim_slot",
        "metric",
        "period_start",
        "period_end",
        "sign",
        "unit",
        "unit_dimension",
        "formula",
        "supporting_source_ids",
        "supporting_source_identities",
        "supporting_evidence_hashes",
    }
)


def _fact_to_dict(fact: FactRecord) -> dict[str, Any]:
    return {
        name: getattr(fact, name)
        for name in _FACT_FIELDS
        if name not in _OPTIONAL_STRUCTURED_FACT_FIELDS or getattr(fact, name)
    }


def _fact_from_dict(data: dict[str, Any]) -> FactRecord:
    return FactRecord(**{name: data[name] for name in _FACT_FIELDS if name in data})


def public_projection_payload(projection: PublicReportProjection) -> dict[str, Any]:
    """공개 봉인 projection을 저장 열에 넣을 JSON 객체로 만든다.

    ★ 이 값은 보고서 payload에 «들어가지 않는다».
      별도 표 ``report_public_projections``의 ``projection_json`` 열에만 들어간다.
      두 문서를 나눠야 각자 저장 자원 상한 아래에 머무르고, 이미 승인된 PDF
      출고 기록이 입력으로 쓰는 보고서 payload 바이트가 한 글자도 안 바뀐다.
    """

    return public_report_projection_to_dict(projection)


def report_to_dict(report: Report) -> dict[str, Any]:
    """`Report` → JSON에 바로 쓸 수 있는 dict.

    ★ 새 키를 «있을 때만» 넣는 이유 — 이 payload의 바이트가
      `export_pdf.automatic_release.report_sha256` 의 입력이고, 그 해시가
      **이미 승인된 PDF 출고 기록**(`pdf_release_records`)에 박혀 있다.
      키를 무조건 넣으면 옛 보고서의 해시가 통째로 달라져 지난 승인이 전부
      안 맞게 된다. 그래서 `presentation` 이 기본값일 때 키를 빼는 것과
      같은 방식으로, 빈 값이면 키를 아예 만들지 않는다.
      (`test_report_presentation_compat.py` 가 이 계약을 지킨다.)
    """
    payload: dict[str, Any] = {
        "company": report.company,
        "job": report.job,
        "corp_type": report.corp_type,
        "grade": report.grade.value,
        "sections": [_section_to_dict(s) for s in report.sections],
        "requirements": list(report.requirements),
        "sources": [_source_status_to_dict(s) for s in report.sources],
        "citations": [_citation_to_dict(c) for c in report.citations],
        "cells": dict(report.cells),
        "shortfall_reasons": list(report.shortfall_reasons),
        "generated_at": report.generated_at,
        "schema_version": report.schema_version,
        "summary_items": [_summary_to_dict(item) for item in report.summary_items],
        "fact_records": [_fact_to_dict(fact) for fact in report.fact_records],
        "as_of_date": report.as_of_date,
        "analysis_period": report.analysis_period,
        "latest_performance_period": report.latest_performance_period,
    }
    # 엔진 v2 전용 — 부록 「사실 검증」 칸이 읽는 문장 등급.
    # 비어 있으면(v1·옛 v2) 키를 아예 넣지 않아 옛 payload 바이트를 그대로 둔다.
    if report.source_grades:
        payload["source_grades"] = {
            str(number): list(grades)
            for number, grades in report.source_grades.items()
        }
    if report.quality_contract_version:
        payload["quality_contract_version"] = report.quality_contract_version
    if report.safety_decision:
        payload["safety_decision"] = report.safety_decision
    if report.publication_policy:
        payload["publication_policy"] = report.publication_policy
    if report.public_structure_manifest:
        payload["public_structure_manifest"] = report.public_structure_manifest
    if report.company_id:
        payload["company_id"] = report.company_id
    if report.release_mode:
        payload["release_mode"] = report.release_mode
    if report.generation_evidence is not None:
        payload["generation_evidence"] = producer_evidence_to_dict(
            report.generation_evidence
        )
    if report.generation_metrics is not None:
        payload["generation_metrics"] = generation_metrics_to_dict(
            report.generation_metrics
        )
    if report.quality_observation is not None:
        payload["quality_observation"] = generation_quality_observation_to_dict(
            report.quality_observation
        )
    # ★ ``public_projection``은 «일부러» 여기 넣지 않는다.
    #   봉인은 별도 표에 저장한다 — payload 바이트·노드 수를
    #   예전과 똑같이 두려는 것이고, 그 동일성은
    #   ``test_report_payload는_projection을_싣지_않아_바이트가_기존과_같다``가
    #   지킨다.
    return payload


def report_from_dict(data: dict[str, Any]) -> Report:
    """dict → `Report`. `report_to_dict`의 역함수 — 왕복해도 같아야 한다."""
    schema_version = str(data.get("schema_version", ""))
    is_v2 = schema_version == ENGINE_V2_SCHEMA_VERSION
    release_mode = str(data.get("release_mode", ""))
    allowed_release_modes = {
        "",
        ReleaseMode.SHADOW.value,
        ReleaseMode.ENFORCE_NO_PARTIAL.value,
        ReleaseMode.FULL.value,
    }
    if release_mode not in allowed_release_modes:
        raise ValueError(f"알 수 없는 저장 release_mode입니다: {release_mode!r}")
    strict_reload = release_mode in {
        ReleaseMode.ENFORCE_NO_PARTIAL.value,
        ReleaseMode.FULL.value,
    }
    if strict_reload and (
        schema_version != ENGINE_V2_SCHEMA_VERSION
        or str(data.get("quality_contract_version", ""))
        != STRICT_QUALITY_CONTRACT_VERSION
    ):
        raise ValueError("엄격 보고서의 schema 또는 품질 contract_version이 바뀌었습니다")
    # ★ quality_observation은 이 「엄격 전용」 묶음에서 뺐다 —
    #   SHADOW도 관측 전용으로 저장한다. generation_evidence·
    #   public_structure_manifest·STRICT contract_version은 여전히 FULL/
    #   ENFORCE 전용이다(그 셋은 실제로 strict 생산 증거·공개 봉인 절차를
    #   거쳐야만 만들어질 수 있는 값이라 강등된 release_mode로 들어오면
    #   신뢰할 수 없다. quality_observation은 SHADOW 생성 경로도 항상 스스로
    #   계산하므로 같은 위험이 없다).
    if (
        not strict_reload
        and (
            data.get("generation_evidence") is not None
            or str(data.get("public_structure_manifest", "")).strip()
            or str(data.get("quality_contract_version", ""))
            == STRICT_QUALITY_CONTRACT_VERSION
        )
    ):
        raise ValueError("엄격 보고서의 release_mode가 누락되거나 낮아졌습니다")

    generation_evidence = None
    generation_metrics = None
    quality_observation = None
    if data.get("generation_metrics") is not None:
        if type(data["generation_metrics"]) is not dict:
            raise ValueError("저장된 생성 지표 형식이 깨졌습니다")
        generation_metrics = generation_metrics_from_dict(
            data["generation_metrics"]
        )
    if data.get("quality_observation") is not None:
        if type(data["quality_observation"]) is not dict:
            raise ValueError("저장된 생성 품질 관측 형식이 깨졌습니다")
        quality_observation = generation_quality_observation_from_dict(
            data["quality_observation"]
        )
    if strict_reload and quality_observation is None:
        raise ValueError("엄격 보고서의 생성 품질 관측이 누락됐습니다")
    if release_mode == ReleaseMode.FULL.value:
        if not str(data.get("public_structure_manifest", "")).strip():
            raise ValueError("FULL 보고서의 공개 구조 manifest가 누락됐습니다")
        evidence_raw = data.get("generation_evidence")
        if type(evidence_raw) is not dict:
            raise ValueError("FULL 보고서의 generation evidence가 누락됐습니다")
        generation_evidence = producer_evidence_from_dict(evidence_raw)
        assert_observation_matches_assessment(
            quality_observation,
            generation_evidence.assessment,
        )
        for section in data.get("sections", []):
            for table in section.get("tables", []):
                rows = table.get("rows", [])
                headers = table.get("headers", [])
                row_evidence_refs = table.get("row_evidence_refs", [])
                row_binding_refs = table.get("row_binding_refs", [])
                cell_binding_refs = table.get("cell_binding_refs", [])
                if (
                    "evidence_rows" in table
                    or not str(table.get("manifest_ref", "")).strip()
                    or not table.get("source_cites")
                    or len(row_evidence_refs) != len(rows)
                    or len(row_binding_refs) != len(rows)
                    or not all(str(value).strip() for value in row_binding_refs)
                    or len(cell_binding_refs) != len(rows)
                    or any(
                        len(cell_refs) != len(headers)
                        or not all(str(value).strip() for value in cell_refs)
                        for cell_refs in cell_binding_refs
                    )
                ):
                    raise ValueError(
                        "FULL 보고서 표의 manifest/typed 행·셀 근거가 누락됐습니다"
                    )
    elif data.get("generation_evidence") is not None:
        raise ValueError("FULL이 아닌 보고서에 generation evidence가 있습니다")
    report = Report(
        company=data["company"],
        job=data["job"],
        corp_type=data["corp_type"],
        grade=Grade(data["grade"]),
        sections=[
            _section_from_dict(s, is_v2=is_v2) for s in data.get("sections", [])
        ],
        requirements=list(data.get("requirements", [])),
        sources=[_source_status_from_dict(s) for s in data.get("sources", [])],
        citations=[_citation_from_dict(c) for c in data.get("citations", [])],
        cells=dict(data.get("cells", {})),
        shortfall_reasons=list(data.get("shortfall_reasons", [])),
        generated_at=data.get("generated_at", ""),
        schema_version=schema_version,
        summary_items=[
            _summary_from_dict(item)
            for item in data.get("summary_items", [])
            if isinstance(item, dict)
        ],
        fact_records=[
            _fact_from_dict(item)
            for item in data.get("fact_records", [])
            if isinstance(item, dict)
        ],
        as_of_date=str(data.get("as_of_date", "")),
        analysis_period=str(data.get("analysis_period", "")),
        latest_performance_period=str(data.get("latest_performance_period", "")),
        quality_contract_version=str(data.get("quality_contract_version", "")),
        safety_decision=str(data.get("safety_decision", "")),
        publication_policy=str(data.get("publication_policy", "")),
        public_structure_manifest=str(data.get("public_structure_manifest", "")),
        company_id=str(data.get("company_id", "")),
        release_mode=release_mode,
        generation_evidence=generation_evidence,
        generation_metrics=generation_metrics,
        quality_observation=quality_observation,
        # 옛 저장본(이 키가 없는 v1·초기 v2)은 빈 사전으로 읽는다 — 그러면
        # 부록이 예전처럼 화면 글자에서 등급을 되짚는 폴백으로 떨어진다.
        source_grades={
            str(number): [str(grade) for grade in grades]
            for number, grades in (data.get("source_grades") or {}).items()
            if isinstance(grades, list)
        },
    )
    if release_mode == ReleaseMode.FULL.value:
        if generation_evidence is None:  # pragma: no cover - 위 경계 방어
            raise ValueError("FULL generation evidence 복원이 실패했습니다")
        assert_report_matches_generation_evidence(
            data,
            generation_evidence,
            manifest_bytes=str(data["public_structure_manifest"]).encode("utf-8"),
        )
    return report


def report_to_json(report: Report) -> str:
    """`Report` → JSON 문자열."""
    payload = json.dumps(report_to_dict(report), ensure_ascii=False)
    validate_persisted_json_text(payload)
    return payload


def report_from_json(text: str) -> Report:
    """JSON 문자열 → `Report`."""
    return report_from_dict(json.loads(text))


def _normalize_legacy_report(report: Report) -> Report:
    """옛 저장 보고서를 회사분석 전용 화면·등급 규칙으로 읽는다.

    채용공고를 받던 시절의 JSON에는 5·6·7·8 section과 그 칸의 판정값이
    남아 있다. 역직렬화 자체는 허용하되 현재 보고서에서는 직무 의존 칸을
    제거한다. 회사 사실인 9·附는 보존한다.

    ★ DB의 JSON은 절대 덮어쓰지 않는다. `load()`가 돌려줄 메모리
    객체만 정규화하므로 원본을 다시 읽어 되돌릴 수 있다.
    새 규칙으로 저장된 보고서는 숨긴 칸이 없으므로 그대로 돌려준다.
    """
    # v3의 내부 ID는 semantic ID이며, 숫자 5~8이 섞여 있더라도 레거시
    # 채용 블록이라고 추정해 삭제하면 안 된다. schema가 명시된 자료는 원형 보존한다.
    if report.schema_version == CANONICAL_SCHEMA_VERSION:
        return report

    hidden = set(HIDDEN_CELLS)
    has_legacy_cells = any(section.cell in hidden for section in report.sections) or any(
        cell in hidden for cell in report.cells
    )
    if not has_legacy_cells:
        return report

    sections = [section for section in report.sections if section.cell not in hidden]
    by_cell: dict[str, bool] = {}
    for section in sections:
        if section.cell in COUNTED_CELLS:
            # 같은 칸이 둘 이상이어도 하나라도 근거가 있으면 채워진 칸이다.
            by_cell[section.cell] = by_cell.get(section.cell, False) or section.is_filled

    # 옛 `cells`가 참이어도 화면에 남은 section에 근거가 없으면 거짓이다.
    # 빈 칸으로 저장된 키는 남겨 옛 JSON의 칸 모양을 필요 이상 바꾸지 않는다.
    cells = {
        cell: by_cell.get(cell, False)
        for cell in COUNTED_CELLS
        if cell in by_cell or cell in report.cells
    }
    grade, shortfall = grade_of(cells)
    return replace(
        report,
        sections=sections,
        cells=cells,
        grade=grade,
        shortfall_reasons=shortfall,
    )


# ══════════════════════════════════════════════════════════
# 저장 · 조회
# ══════════════════════════════════════════════════════════


# ══════════════════════════════════════════════════════════
# 공개 봉인 projection — 보고서 payload와 «다른 표»
# ══════════════════════════════════════════════════════════


def save_public_projection(
    conn: sqlite3.Connection,
    report_id: str,
    projection: Optional[PublicReportProjection],
    *,
    created_at: str,
) -> None:
    """보고서 하나의 공개 봉인을 전용 표에 쓴다(없으면 지운다).

    ★ 반드시 보고서 본문과 «같은 거래»에서 불러야 한다. 이 함수는 commit을
      하지 않는다 — 호출부(``save``·``insert_new``)가 이미 열려 있는 거래에
      얹으므로, 이 쓰기가 실패하면 본문 행도 함께 되돌아간다.

    ★ 봉인이 ``None``이면 옛 행을 «지운다». 남겨 두면 새로 덮어쓴 본문에 옛
      봉인이 붙어, 화면이 지금 본문과 다른 글자를 그리게 된다.
    """

    if projection is None:
        conn.execute(
            f"DELETE FROM {TABLE_REPORT_PUBLIC_PROJECTIONS} WHERE report_id = ?",
            (report_id,),
        )
        return
    digest = build_report_digest(projection)
    payload = json.dumps(
        public_projection_payload(projection), ensure_ascii=False
    )
    validate_persisted_json_text(payload)
    conn.execute(
        f"""
        INSERT INTO {TABLE_REPORT_PUBLIC_PROJECTIONS}
            (report_id, projection_json, content_sha256, display_sha256, created_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(report_id) DO UPDATE SET
            projection_json=excluded.projection_json,
            content_sha256=excluded.content_sha256,
            display_sha256=excluded.display_sha256,
            created_at=excluded.created_at
        """,
        (
            report_id,
            payload,
            digest.content_sha256,
            digest.display_sha256,
            created_at,
        ),
    )


def load_public_projection(
    conn: sqlite3.Connection, report_id: str
) -> Optional[PublicReportProjection]:
    """저장된 봉인을 되살리고 digest를 «재계산해» 대조한다.

    저장된 digest를 그대로 믿지 않는 이유는 ``canonical.py``의 봉인 원칙과
    같다 — 자기 자신만 검사하는 checksum은 본문과 digest를 «함께» 바꾼 위조를
    못 막는다. 여기서 다시 계산해 열 값과 맞대면 저장 뒤에 손댄 흔적이
    로드에서 닫힌다.

    Returns:
        봉인이 있으면 그 값, 없으면 ``None``. ``None``은 오류가 아니라
        「이 보고서에는 봉인이 없다」는 **정의된 상태**다 — 이 표가 생기기 전
        저장본과 SHADOW 보고서가 그렇다. 화면이 그 사실을 보고 판단한다.

    Raises:
        ValueError: 저장된 봉인이 구조·digest 대조를 통과하지 못할 때.
    """

    try:
        row = conn.execute(
            f"""SELECT projection_json, content_sha256, display_sha256
            FROM {TABLE_REPORT_PUBLIC_PROJECTIONS} WHERE report_id = ?""",
            (report_id,),
        ).fetchone()
    except sqlite3.OperationalError as error:
        # 이 표가 생기기 전 만들어진 DB를 읽기 전용으로 여는 경우다. 읽기
        # 전용 연결은 schema bootstrap을 하지 않으므로 표가 없을 수 있다.
        # 그건 「봉인 없음」이지 오류가 아니다.
        if "no such table" in str(error).lower():
            return None
        raise
    if row is None:
        return None
    projection = public_report_projection_from_dict(
        json.loads(str(row["projection_json"]))
    )
    digest = build_report_digest(projection)
    if str(row["content_sha256"]) != digest.content_sha256:
        raise ValueError("저장된 공개 봉인의 content 지문이 재계산 값과 다릅니다")
    if str(row["display_sha256"]) != digest.display_sha256:
        raise ValueError("저장된 공개 봉인의 display 지문이 재계산 값과 다릅니다")
    return projection


def attach_public_projection(
    conn: sqlite3.Connection, report_id: str, report: Report
) -> Report:
    """보고서에 저장된 봉인을 붙이고 생성 증거와 맞대본다.

    ★ 왜 증거와도 맞대나 — 위 ``load_public_projection``은 봉인 «자체»의
      앞뒤만 본다. 다른 실행의 봉인을 digest 열까지 통째로 갈아 끼우면 그
      검사는 통과한다. 생성 증거가 지목하는 지문과 맞대야 바꿔치기가 잡힌다.

    ★ ``load()``만 이걸 부르는 게 아니다. 봉인은 payload가 아니라 별도 표에
      있으므로, **payload 문자열에서 Report를 다시 만드는 경로**(공개 결과
      화면이 읽는 delivery content snapshot, 관리자 승인 snapshot, 캐시 재사용)는
      이 함수를 «명시적으로» 불러야 봉인이 붙는다. 안 부르면 봉인이 있는데도
      화면은 「봉인 없음」으로 그린다. 자세한 경로 목록은
      ``storage/tests/test_public_projection_storage.py``의
      ``test_payload_문자열에서_되살린_보고서에는_봉인이_붙지_않는다`` docstring에 있다.

    Returns:
        봉인이 있으면 붙인 새 ``Report``, 없으면 받은 값 그대로.

    Raises:
        ValueError: 저장된 봉인이 구조·digest·생성 증거 대조를 통과하지 못할 때.
    """

    projection = load_public_projection(conn, report_id)
    if projection is None:
        return report
    evidence = report.generation_evidence
    # ★ 증거가 없으면 붙이지 않고 닫는다. 봉인의 진짜 권위는
    #   생성 증거의 `public_projection_sha256`이다 — 증거가 없으면 「이 봉인이
    #   이 보고서의 것」이라고 말해 주는 것이 아무것도 없고, 남는 검사는 봉인
    #   스스로의 앞뒤가 맞는지뿐이라 DB에 직접 넣은 봉인도 통과한다.
    #   정상 SHADOW·옛 저장본은 애초에 봉인 행이 없어 위에서 이미 돌아갔다.
    if evidence is None:
        raise ValueError("생성 증거가 없는 보고서에 저장된 공개 봉인이 있습니다")
    if (
        build_report_digest(projection).content_sha256
        != evidence.public_projection_sha256
    ):
        raise ValueError("저장된 공개 봉인이 생성 증거의 지문과 다릅니다")
    # ★ 여기까지의 검사는 전부 «열쇠 없는» 해시다 — 저장소에 직접 쓸 수 있는
    #   쪽은 출처를 고친 뒤 지문을 다시 계산해 통과시킬 수 있다. 수집 도장만
    #   저장소 밖 열쇠로 찍혀 있으므로, 읽는 경계에서 그 도장을 한 번 더 본다.
    #   출처가 바뀐 본문은 그리지 않고 닫는다.
    problem = stored_sources_seal_problem(
        _citation_from_dict(dict(row.source)) for row in projection.citations
    )
    if problem:
        raise ValueError(f"저장된 공개 봉인의 출처를 믿을 수 없습니다: {problem}")
    return replace(report, public_projection=projection)


def save(
    conn: sqlite3.Connection,
    report_id: str,
    corp_id: str,
    job: str,
    report: Report,
    *,
    created_at: Optional[str] = None,
    engine_epoch_digest: str = "",
) -> None:
    """보고서 본문을 저장한다(같은 `report_id`면 덮어쓴다).

    ★ 보통 직접 부르지 않는다 — `cache.save_layer1()`이 `report_id`를 만들어
      대신 불러준다. 여기 남겨 둔 이유는 캐시를 거치지 않는 저장(재내보내기용
      단순 보관 등)이 나중에 필요할 수 있어서다.

    Args:
        conn: `db.connect()`가 연 연결.
        report_id: 이 저장에 붙일 고유 키.
        corp_id: 회사 고유번호(예: 전자공시 고유번호). 화면에 보이는 회사명이
            아니라 «흔들리지 않는 값»을 넣어야 다른 회사와 안 섞인다.
        job: 직무명. 화면 표시·이력용으로 같이 저장한다(조회 키가 아니다 —
            조회는 `cache`가 정규화한 값으로 한다).
        report: 저장할 보고서.
        created_at: 저장 시각(ISO 8601). 생략하면 지금.
    """
    stamp = created_at or dt.datetime.now().isoformat(timespec="seconds")
    conn.execute(
        f"""
        INSERT INTO {TABLE_REPORTS}
            (report_id, corp_id, job, payload_json, generated_at, created_at,
             engine_epoch_digest)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(report_id) DO UPDATE SET
            corp_id=excluded.corp_id,
            job=excluded.job,
            payload_json=excluded.payload_json,
            generated_at=excluded.generated_at,
            created_at=excluded.created_at,
            engine_epoch_digest=excluded.engine_epoch_digest
        """,
        (
            report_id,
            corp_id,
            job,
            report_to_json(report),
            report.generated_at,
            stamp,
            engine_epoch_digest,
        ),
    )
    # 같은 거래 — 봉인 쓰기가 실패하면 위 본문 행도 함께 되돌아간다.
    save_public_projection(
        conn, report_id, report.public_projection, created_at=stamp
    )


def insert_new(
    conn: sqlite3.Connection,
    report_id: str,
    corp_id: str,
    job: str,
    report: Report,
    *,
    engine_epoch_digest: str,
    created_at: Optional[str] = None,
) -> bool:
    """새 공개 결과만 저장한다. 같은 ID의 기존 보고서는 절대 덮지 않는다."""
    from src.shared import engine_build_identity as build_identity_contract  # noqa: PLC0415

    if not build_identity_contract.epoch_digest_is_valid(engine_epoch_digest):
        raise ValueError("신규 공개 보고서에는 정상 engine epoch 영수증이 필요합니다")
    stamp = created_at or dt.datetime.now().isoformat(timespec="seconds")
    cursor = conn.execute(
        f"""
        INSERT OR IGNORE INTO {TABLE_REPORTS}
            (report_id, corp_id, job, payload_json, generated_at, created_at,
             engine_epoch_digest)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            report_id,
            corp_id,
            job,
            report_to_json(report),
            report.generated_at,
            stamp,
            engine_epoch_digest,
        ),
    )
    if cursor.rowcount != 1:
        # 이미 있는 공개 보고서는 본문도 봉인도 덮지 않는다(append-only).
        return False
    # 같은 거래 — 봉인 쓰기가 실패하면 위 본문 행도 함께 되돌아간다.
    save_public_projection(
        conn, report_id, report.public_projection, created_at=stamp
    )
    return True


def exists(conn: sqlite3.Connection, report_id: str) -> bool:
    """payload를 역직렬화하지 않고 보고서 ID의 존재 여부만 확인한다."""
    row = conn.execute(
        f"SELECT 1 FROM {TABLE_REPORTS} WHERE report_id = ?", (report_id,)
    ).fetchone()
    return row is not None


def engine_epoch_digest(conn: sqlite3.Connection, report_id: str) -> str:
    """공개 GET에는 영향 없이 새 생성 권위 판정용 epoch만 읽는다."""

    row = conn.execute(
        f"SELECT engine_epoch_digest FROM {TABLE_REPORTS} WHERE report_id = ?",
        (report_id,),
    ).fetchone()
    return "" if row is None else str(row["engine_epoch_digest"])


def list_report_ids(
    conn: sqlite3.Connection, *, since: str = "", until: str = ""
) -> list[tuple[str, str]]:
    """`payload_json`을 전혀 읽지 않고 `(report_id, created_at)` 목록만 돌려준다.

    관측·집계용 도구가 「이 DB에 어떤 report_id들이 있는가」를 알아야 할 때 쓰는
    최소 열거 공개 API다(`admin_dashboard`가 이 표를 직접
    SQL로 열거하지 않도록 하기 위함).

    ★ 필터·정렬 기준은 `created_at`(이 행이 저장된 시각)이다. `generated_at`
      (보고서 자신이 담은 표시용 시각)이 아니다 — `created_at`은 `save()`가
      항상 채우는 단조 증가 값이라 열거 순서가 안정적이다.
    ★ 날짜만(``YYYY-MM-DD``) 받는다고 가정해 `substr(created_at, 1, 10)`로
      비교한다. 그래야 시각까지 붙은 `created_at`이 같은 날짜의 날짜만 있는
      `until`보다 사전식으로 «커서» 그날 하루가 통째로 빠지는 함정을 피한다.

    Args:
        conn: 이미 연결된 SQLite 연결(읽기 전용 연결도 가능).
        since: `created_at` 날짜 하한(포함). 빈 문자열이면 제한 없음.
        until: `created_at` 날짜 상한(포함). 빈 문자열이면 제한 없음.

    Returns:
        `(report_id, created_at)` 쌍의 목록. `created_at` 오름차순, 같으면
        `report_id` 오름차순 — 입력 저장 순서와 무관하게 결정론적이다.
    """

    query = f"SELECT report_id, created_at FROM {TABLE_REPORTS}"
    clauses: list[str] = []
    params: list[str] = []
    if since:
        clauses.append("substr(created_at, 1, 10) >= ?")
        params.append(since)
    if until:
        clauses.append("substr(created_at, 1, 10) <= ?")
        params.append(until)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY created_at, report_id"
    rows = conn.execute(query, tuple(params)).fetchall()
    return [(str(row[0]), str(row[1])) for row in rows]


def load_corp_id(conn: sqlite3.Connection, report_id: str) -> str:
    """이 보고서가 «어느 회사 것으로 저장됐는지»를 표의 열에서 직접 읽는다.

    Args:
        conn: `db.connect()`가 연 연결.
        report_id: 찾을 보고서 번호.

    Returns:
        저장할 때 받은 회사 고유번호. 보고서가 없거나 그때 값이 비어 있었으면
        빈 문자열.

    ★ 왜 `load()`로 안 되나 — `load()`는 `payload_json`만 읽어 되살리고, 본문의
      `company_id`는 출고 상태가 FULL일 때만 채워진다(`pipeline/real.py:3519`).
      반면 이 열은 **출고 상태와 무관하게** 저장 경로가 항상 채운다
      (`cache.save_layer1()` → `save()`의 `corp_id` 인자). 그래서 옛 저장본에서도
      회사를 가르려면 본문이 아니라 이 열을 봐야 한다.
    ★ 읽기 실패(예외)는 삼키지 않는다 — 부르는 쪽이 「확인 못 했다」를 「없다」와
      다르게 다뤄야 하기 때문이다.
    """

    row = conn.execute(
        f"SELECT corp_id FROM {TABLE_REPORTS} WHERE report_id = ?",
        (str(report_id or "").strip(),),
    ).fetchone()
    if row is None:
        return ""
    return str(row[0] or "").strip()


def resolve_company_id(
    conn: sqlite3.Connection,
    report_id: str,
    report: Optional[Report] = None,
) -> str:
    """이 보고서의 회사 고유번호를 «열 우선, 본문 폴백»으로 읽는다.

    Args:
        conn: `db.connect()`가 연 연결.
        report_id: 볼 보고서 번호.
        report: 이미 되살려 둔 본문이 있으면 다시 안 읽으려고 받는다.

    Returns:
        고유번호. 열도 본문도 비었으면 빈 문자열. **읽기 실패는 예외로 나간다** —
        부르는 쪽이 「확인 못 했다」와 「없다」를 다르게 다뤄야 하기 때문이다.

    ★ 열을 먼저 보는 이유 — 본문의 `company_id`는 출고 상태가 FULL일 때만
      채워진다. 저장 표의 `corp_id` 열은 출고 상태와 무관하게 채워진다.
    ★ 열에서 값을 얻어도 **본문 읽기를 건너뛰지 않는다.** 「대상을 못 읽으면
      거부한다」는 호출부의 fail-closed 계약을 이 함수가 느슨하게 만들지
      않기 위해서다.
    """

    clean_report_id = str(report_id or "").strip()
    if not clean_report_id:
        return ""
    column_value = load_corp_id(conn, clean_report_id)
    current = report if report is not None else load(conn, clean_report_id)
    if column_value:
        return column_value
    if current is None:
        return ""
    return str(getattr(current, "company_id", "") or "").strip()


def load(conn: sqlite3.Connection, report_id: str) -> Optional[Report]:
    """`report_id`로 보고서를 현재 표시 규칙으로 불러온다. 없으면 `None`."""
    row = conn.execute(
        f"SELECT payload_json FROM {TABLE_REPORTS} WHERE report_id = ?", (report_id,)
    ).fetchone()
    if row is None:
        return None
    report = _normalize_legacy_report(report_from_json(row["payload_json"]))
    return attach_public_projection(conn, report_id, report)
