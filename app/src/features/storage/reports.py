"""`Report` ↔ JSON 직렬화, 그리고 DB 저장·조회.

★ `Report`는 `features/pipeline/port.py`의 것을 그대로 쓴다 — 여기서 새
  자료구조를 만들지 않는다. `sections`·`citations`까지 «전부» 되살아나야
  재내보내기(워드·노션)가 화면과 같은 문서를 다시 만들 수 있다
  (정본 §3 저장 구간이 갖는 것 — "재내보내기가 화면과 «똑같은» 문서를
  다시 만들 수 있는 최소 집합").

★ S2(공고 원문 미저장) — 이 파일이 받는 것은 `Report` 객체 «하나뿐»이다.
  `Report`에는 애초에 공고 원문(`UserInput.posting_text`) 필드가 없다.
  그래서 공고 원문은 «구조적으로» 이 파일을 거쳐 DB에 들어갈 길이 없다 —
  코드로 막는 게 아니라 자료구조에 자리 자체가 없다.

정본: 확정/03_수집/2_규칙/03_캐시와저장.md §3 저장 구간이 갖는 것
"""

from __future__ import annotations

import datetime as dt
import json
import sqlite3
from dataclasses import fields, replace
from typing import Any, Optional

from src.core.constants import COUNTED_CELLS, HIDDEN_CELLS
from src.core.persisted_json import validate_persisted_json_text
from src.features.composer.render import ENGINE_V2_SCHEMA_VERSION
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
from src.features.provenance.sources import Source, SourceKind
from src.features.report_standard.constants import CANONICAL_SCHEMA_VERSION
from src.features.storage.constants import TABLE_REPORTS

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
    # 옛 payload의 암묵적 기본값은 그대로 직렬화해야 기존 보고서 hash·자동출고
    # 승인이 이유 없이 바뀌지 않는다. 실제 시각화 힌트가 있을 때만 새 키를 저장한다.
    if table.presentation != "table":
        payload["presentation"] = table.presentation
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
        presentation=str(data.get("presentation", "table")),
    )


def _prose_lines_from_dict(
    data: dict[str, Any], *, is_v2: bool
) -> list[tuple[str, str]]:
    """선택 층인 표시용 글만 안전하게 되살린다.

    ★ v1(canonical)은 옛 저장값에 이 필드가 없을 수 있다. 깨진 항목·출처
      없는 항목·옛 문자열 prose는 검증 여부를 증명할 수 없으므로 버리고,
      근거 원문 보고서는 계속 연다(P-117·P-118) — v1은 줄마다 cite(부록
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
    return {
        "cell": section.cell,
        "title": section.title,
        "lines": [[text, cite] for text, cite in section.lines],
        # ★ 검증된 표시용 글도 저장해야 서버 재시작·워드·노션에서 화면과 같다(P-117).
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


def _section_from_dict(data: dict[str, Any], *, is_v2: bool) -> ReportSection:
    return ReportSection(
        cell=data["cell"],
        title=data["title"],
        lines=[(text, cite) for text, cite in data.get("lines", [])],
        # 옛 저장 보고서에는 키가 없다. 빈 목록으로 읽어 원문 보고서를 그대로 살린다.
        prose_lines=_prose_lines_from_dict(data, is_v2=is_v2),
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


def _fact_to_dict(fact: FactRecord) -> dict[str, Any]:
    return {name: getattr(fact, name) for name in _FACT_FIELDS}


def _fact_from_dict(data: dict[str, Any]) -> FactRecord:
    return FactRecord(**{name: data[name] for name in _FACT_FIELDS if name in data})


def report_to_dict(report: Report) -> dict[str, Any]:
    """`Report` → JSON에 바로 쓸 수 있는 dict."""
    return {
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


def report_from_dict(data: dict[str, Any]) -> Report:
    """dict → `Report`. `report_to_dict`의 역함수 — 왕복해도 같아야 한다."""
    schema_version = str(data.get("schema_version", ""))
    is_v2 = schema_version == ENGINE_V2_SCHEMA_VERSION
    return Report(
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
    )


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


def save(
    conn: sqlite3.Connection,
    report_id: str,
    corp_id: str,
    job: str,
    report: Report,
    *,
    created_at: Optional[str] = None,
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
            (report_id, corp_id, job, payload_json, generated_at, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(report_id) DO UPDATE SET
            corp_id=excluded.corp_id,
            job=excluded.job,
            payload_json=excluded.payload_json,
            generated_at=excluded.generated_at,
            created_at=excluded.created_at
        """,
        (report_id, corp_id, job, report_to_json(report), report.generated_at, stamp),
    )


def insert_new(
    conn: sqlite3.Connection,
    report_id: str,
    corp_id: str,
    job: str,
    report: Report,
    *,
    created_at: Optional[str] = None,
) -> bool:
    """새 공개 결과만 저장한다. 같은 ID의 기존 보고서는 절대 덮지 않는다."""
    stamp = created_at or dt.datetime.now().isoformat(timespec="seconds")
    cursor = conn.execute(
        f"""
        INSERT OR IGNORE INTO {TABLE_REPORTS}
            (report_id, corp_id, job, payload_json, generated_at, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (report_id, corp_id, job, report_to_json(report), report.generated_at, stamp),
    )
    return cursor.rowcount == 1


def exists(conn: sqlite3.Connection, report_id: str) -> bool:
    """payload를 역직렬화하지 않고 보고서 ID의 존재 여부만 확인한다."""
    row = conn.execute(
        f"SELECT 1 FROM {TABLE_REPORTS} WHERE report_id = ?", (report_id,)
    ).fetchone()
    return row is not None


def load(conn: sqlite3.Connection, report_id: str) -> Optional[Report]:
    """`report_id`로 보고서를 현재 표시 규칙으로 불러온다. 없으면 `None`."""
    row = conn.execute(
        f"SELECT payload_json FROM {TABLE_REPORTS} WHERE report_id = ?", (report_id,)
    ).fetchone()
    if row is None:
        return None
    return _normalize_legacy_report(report_from_json(row["payload_json"]))
