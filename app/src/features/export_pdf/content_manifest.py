"""PDF bytes를 그 PDF가 표현해야 할 보고서 내용과 결속하는 지문."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict
from typing import Final

from src.features.pipeline.port import Report, ReportSection, ReportTable
from src.features.provenance.sources import Source, visible_citations


CONTENT_MANIFEST_VERSION: Final[str] = "company-analysis-public-content-v1"
PDF_MANIFEST_VERSION_KEY: Final[str] = "/CompanyAnalysisContentManifestVersion"
PDF_MANIFEST_SHA256_KEY: Final[str] = "/CompanyAnalysisContentManifestSHA256"


def _table_payload(table: ReportTable) -> dict[str, object]:
    return {
        "caption": table.caption,
        "headers": list(table.headers),
        "rows": [list(row) for row in table.rows],
        "cite": table.cite,
        "numeric": table.numeric,
        # 공개 숫자의 의미를 정하는 숨은 원값·환산 계약도 같은 지문에 묶는다.
        "raw_rows": [list(row) for row in table.raw_rows],
        "scale_divisor": table.scale_divisor,
        "scale_places": table.scale_places,
        "display_unit": table.display_unit,
        "presentation": table.presentation,
        "entity_scope": table.entity_scope,
        "raw_unit": table.raw_unit,
        "unit_dimension": table.unit_dimension,
    }


def _section_payload(section: ReportSection) -> dict[str, object]:
    return {
        "cell": section.cell,
        "title": section.title,
        "empty_reason": section.empty_reason,
        "display_number": section.display_number,
        "tag": section.tag,
        "prose_lines": [list(item) for item in section.prose_lines],
        "prose_paragraphs": list(section.prose_paragraphs),
        "guidance_lines": list(section.guidance_lines),
        "fact_ids": list(section.fact_ids),
        "tables": [_table_payload(table) for table in section.tables],
    }


def _source_payload(source: Source) -> dict[str, object]:
    # 출처 부록에 표시되거나 공개 claim의 신원을 정하는 필드만 싣는다.
    # 원문·HMAC은 PDF 메타데이터에 싣지 않으며 최종적으로 hash만 기록된다.
    return {
        "number": source.number,
        "kind": source.kind.value,
        "label": source.label,
        "disclosed_at": source.disclosed_at,
        "collected_at": source.collected_at,
        "published_at": source.published_at,
        "domain": source.domain,
        "source_id": source.source_id,
        "title": source.title,
        "publisher": source.publisher,
        "host": source.host,
        "url": source.url,
        "document_id": source.document_id,
        "location": source.location,
        "source_type": source.source_type,
        "fact_status": source.fact_status,
        "used_in": list(source.used_in),
        "reporting_period": source.reporting_period,
        "attachment_url": source.attachment_url,
    }


def public_content_manifest(report: Report) -> dict[str, object]:
    """웹 정본에서 PDF로 옮겨야 하는 내용·claim·표 계약의 정규 표현."""

    return {
        "manifest_version": CONTENT_MANIFEST_VERSION,
        "report": {
            "company": report.company,
            "corp_type": report.corp_type,
            "grade": report.grade.value,
            "generated_at": report.generated_at,
            "schema_version": report.schema_version,
            "shortfall_reasons": list(report.shortfall_reasons),
            "as_of_date": report.as_of_date,
            "analysis_period": report.analysis_period,
            "latest_performance_period": report.latest_performance_period,
            "quality_contract_version": report.quality_contract_version,
            "safety_decision": report.safety_decision,
            "publication_policy": report.publication_policy,
        },
        "summary": [asdict(item) for item in report.summary_items],
        "sections": [_section_payload(section) for section in report.sections],
        "citations": [
            _source_payload(source)
            for source in visible_citations(report.citations)
            if isinstance(source, Source)
        ],
        # 구조화 claim의 지표·기간·부호·단위·공식까지 묶는다. PDF 산문과 표만
        # 우연히 같아도 다른 사실 원장을 같은 공개물로 승인하지 않는다.
        "fact_records": [asdict(fact) for fact in report.fact_records],
        "source_grades": {
            str(number): list(grades)
            for number, grades in sorted(report.source_grades.items())
        },
    }


def public_content_manifest_sha256(report: Report) -> str:
    payload = json.dumps(
        public_content_manifest(report),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
