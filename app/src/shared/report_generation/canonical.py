"""저장·cache·composer가 공유하는 중립 public content/manifest 검증기.

pre-render 기대값 생성은 composer feature가 맡지만, 최종 Report의 명시적 공개
projection과 저장 재로드 검증은 이 모듈 한 벌만 사용한다. 저장 계층이 composer를
import하지 않으며, manifest 내부 self-checksum은 외부 producer evidence digest를
대신할 수 없다.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from typing import Any, Final

from src.core.citations import citation_number
from src.shared.report_generation.models import (
    GenerationProducerEvidence,
    assert_canonical_producer_evidence,
    canonical_json,
    canonical_sha256,
    canonical_value,
    exact_text_sha256,
    producer_evidence_from_dict,
    producer_evidence_to_dict,
)
from src.shared.report_quality.constants import STRICT_QUALITY_CONTRACT_VERSIONS
from src.shared.report_quality.contract import contract_for_stored_assessment
from src.shared.report_quality.models import (
    PublicationPolicy,
    QualityGrade,
    ReleaseDecision,
)
from src.shared.report_quality.source_identity import (
    document_identity,
    document_identity_from_parts,
)


PUBLIC_STRUCTURE_MANIFEST_VERSION: Final[str] = "public-structure-manifest-v2"
_SOURCE_ID_PREFIX: Final[str] = "v2-frag-"
_HEX_64_RE: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}")
_NUMERIC_TOKEN_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<![\w])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?"
)


class PublicManifestError(ValueError):
    """공개 구조·내용·생산 증거가 서로 결속되지 않았을 때의 오류."""


def _value(container: object, key: str, default: Any = None) -> Any:
    if isinstance(container, Mapping):
        return container.get(key, default)
    return getattr(container, key, default)


def _public_text(value: object) -> str:
    """str Enum과 JSON 문자열을 같은 공개 문자로 고정한다."""

    enum_value = getattr(value, "value", value)
    return str(enum_value or "")


def _numeric_tokens(rows: Sequence[Sequence[str]]) -> list[list[str]]:
    return [
        [
            match.group(0)
            for cell in row
            for match in _NUMERIC_TOKEN_RE.finditer(str(cell))
        ]
        for row in rows
    ]


def table_public_projection(table: object) -> dict[str, object]:
    """manifest/producer 필드를 제외한 표의 명시적 공개 projection."""

    return {
        "caption": str(_value(table, "caption", "")),
        "headers": [str(value) for value in _value(table, "headers", ())],
        "rows": [
            [str(cell) for cell in row] for row in _value(table, "rows", ())
        ],
        "cite": str(_value(table, "cite", "")),
        "numeric": bool(_value(table, "numeric", False)),
        "presentation": str(_value(table, "presentation", "table")),
        "display_unit": str(_value(table, "display_unit", "")),
    }


def section_public_projection(section: object) -> dict[str, object]:
    return {
        "cell": str(_value(section, "cell", "")),
        "title": str(_value(section, "title", "")),
        "empty_reason": str(_value(section, "empty_reason", "")),
        "prose_lines": [
            [str(text), str(cite)]
            for text, cite in _value(section, "prose_lines", ())
        ],
        "prose_paragraphs": [
            str(value) for value in _value(section, "prose_paragraphs", ())
        ],
        "guidance_lines": [
            str(value) for value in _value(section, "guidance_lines", ())
        ],
        "display_number": str(_value(section, "display_number", "")),
        "tag": str(_value(section, "tag", "")),
        "tables": [
            table_public_projection(table)
            for table in _value(section, "tables", ())
        ],
    }


def _summary_public_projection(item: object) -> dict[str, object]:
    return {
        "text": str(_value(item, "text", "")),
        "section_id": str(_value(item, "section_id", "")),
    }


def _source_public_projection(source: object) -> dict[str, object]:
    # Source의 전체 dataclass 필드를 고정한다. producer evidence와 달리 Source는
    # Report 안의 독립 부록 항목이라 순환 digest가 생기지 않는다.
    if not isinstance(source, Mapping):
        value = canonical_value(source)
        if not isinstance(value, dict):
            raise PublicManifestError("공개 Source를 canonical 객체로 만들 수 없습니다")
        return value
    # storage payload가 뒤로 호환을 위해 빈 기본 필드를 생략해도 Source
    # dataclass의 명시적 공개 projection은 같은 값으로 복원한다.
    return {
        "number": int(source.get("number", 0)),
        "kind": str(source.get("kind") or ""),
        "label": str(source.get("label") or ""),
        "disclosed_at": str(source.get("disclosed_at") or ""),
        "collected_at": str(source.get("collected_at") or ""),
        "published_at": str(source.get("published_at") or ""),
        "domain": str(source.get("domain") or ""),
        "source_id": str(source.get("source_id") or ""),
        "title": str(source.get("title") or ""),
        "publisher": str(source.get("publisher") or ""),
        "host": str(source.get("host") or ""),
        "url": str(source.get("url") or ""),
        "document_id": str(source.get("document_id") or ""),
        "location": str(source.get("location") or ""),
        "source_type": str(source.get("source_type") or ""),
        "fact_status": str(source.get("fact_status") or ""),
        "used_in": [str(value) for value in source.get("used_in", [])],
        "evidence_hashes": [
            str(value) for value in source.get("evidence_hashes", [])
        ],
        "exact_evidence_hashes": [
            str(value) for value in source.get("exact_evidence_hashes", [])
        ],
        "domain_attestation_source_id": str(
            source.get("domain_attestation_source_id") or ""
        ),
        "domain_attestation_evidence": str(
            source.get("domain_attestation_evidence") or ""
        ),
        "provenance_seal": str(source.get("provenance_seal") or ""),
        "provenance_role": str(source.get("provenance_role") or "citation"),
        "reporting_period": str(source.get("reporting_period") or ""),
        "ir_metadata_verification": str(
            source.get("ir_metadata_verification") or ""
        ),
        "attachment_url": str(source.get("attachment_url") or ""),
        "domain_redirect_verification": str(
            source.get("domain_redirect_verification") or ""
        ),
        "domain_redirect_from_host": str(
            source.get("domain_redirect_from_host") or ""
        ),
        "domain_redirect_to_host": str(
            source.get("domain_redirect_to_host") or ""
        ),
        "formal_source_kind": str(source.get("formal_source_kind") or ""),
        "identity_binding": str(source.get("identity_binding") or ""),
        "document_content_sha256": str(
            source.get("document_content_sha256") or ""
        ),
    }


def public_content_projection(report: Mapping[str, object]) -> dict[str, object]:
    """실제 공개 표시 content만 고른 명시적 projection.

    내부 감사용 ``ReportSection.lines``와 숨은 ``fact_ids``/FactRecord, transport,
    assessment, manifest는 포함하지 않는다. 전체 내부 payload 권위는 별도 release
    snapshot의 몫이며 이 public digest가 대신하지 않는다.
    """

    return {
        "version": 1,
        "company": str(report.get("company") or ""),
        "company_id": str(report.get("company_id") or ""),
        "job": str(report.get("job") or ""),
        "corp_type": str(report.get("corp_type") or ""),
        "generated_at": str(report.get("generated_at") or ""),
        "schema_version": str(report.get("schema_version") or ""),
        "as_of_date": str(report.get("as_of_date") or ""),
        "analysis_period": str(report.get("analysis_period") or ""),
        "latest_performance_period": str(
            report.get("latest_performance_period") or ""
        ),
        "grade": _public_text(report.get("grade")),
        "shortfall_reasons": [
            str(value) for value in report.get("shortfall_reasons", ())
        ],
        "quality_contract_version": str(
            report.get("quality_contract_version") or ""
        ),
        "safety_decision": str(report.get("safety_decision") or ""),
        "publication_policy": str(report.get("publication_policy") or ""),
        "sections": [
            section_public_projection(section)
            for section in report.get("sections", ())
        ],
        "summary_items": [
            _summary_public_projection(item)
            for item in report.get("summary_items", ())
        ],
        "citations": [
            _source_public_projection(source)
            for source in report.get("citations", ())
        ],
    }


def report_public_content_projection(report: object) -> dict[str, object]:
    """런타임 dataclass용 어댑터. 권위 경계는 Mapping 함수만 소비한다."""

    return public_content_projection(report_verification_payload(report))


def report_verification_payload(report: object) -> dict[str, object]:
    """feature 타입 import 없이 Report 유사 객체를 검증용 Mapping으로 고정한다."""

    return {
        "company": str(_value(report, "company", "")),
        "company_id": str(_value(report, "company_id", "")),
        "job": str(_value(report, "job", "")),
        "corp_type": str(_value(report, "corp_type", "")),
        "generated_at": str(_value(report, "generated_at", "")),
        "schema_version": str(_value(report, "schema_version", "")),
        "as_of_date": str(_value(report, "as_of_date", "")),
        "analysis_period": str(_value(report, "analysis_period", "")),
        "latest_performance_period": str(
            _value(report, "latest_performance_period", "")
        ),
        "grade": _public_text(_value(report, "grade", "")),
        "shortfall_reasons": [
            str(value) for value in _value(report, "shortfall_reasons", ())
        ],
        "release_mode": str(_value(report, "release_mode", "")),
        "quality_contract_version": str(
            _value(report, "quality_contract_version", "")
        ),
        "safety_decision": str(_value(report, "safety_decision", "")),
        "publication_policy": str(_value(report, "publication_policy", "")),
        "public_structure_manifest": str(
            _value(report, "public_structure_manifest", "")
        ),
        "sections": list(_value(report, "sections", ())),
        "summary_items": list(_value(report, "summary_items", ())),
        "citations": list(_value(report, "citations", ())),
    }


def public_content_digests(
    report: Mapping[str, object] | object,
) -> tuple[str, tuple[tuple[str, str], ...]]:
    payload = (
        report
        if isinstance(report, Mapping)
        else report_verification_payload(report)
    )
    projection = public_content_projection(payload)
    sections = projection["sections"]
    section_digests = tuple(
        (
            str(section["cell"]),
            canonical_sha256(section),
        )
        for section in sections
        if isinstance(section, Mapping)
    )
    return canonical_sha256(projection), section_digests


def _load_manifest(
    value: str, *, expected_sha256: str = ""
) -> dict[str, Any]:
    raw = str(value or "")
    if expected_sha256 and exact_text_sha256(raw) != expected_sha256:
        raise PublicManifestError(
            "공개 manifest bytes가 producer evidence 지문과 다릅니다"
        )
    try:
        manifest = json.loads(raw)
    except (json.JSONDecodeError, TypeError, ValueError) as error:
        raise PublicManifestError("공개 구조 manifest JSON을 읽을 수 없습니다") from error
    if not isinstance(manifest, dict):
        raise PublicManifestError("공개 구조 manifest가 객체가 아닙니다")
    expected_keys = {
        "version",
        "company_id",
        "evidence_generation_sha256",
        "evidence_packet_sha256s",
        "sections",
        "tables",
        "digest",
    }
    if set(manifest) != expected_keys:
        raise PublicManifestError("공개 구조 manifest 최상위 키가 계약과 다릅니다")
    if manifest.get("version") != PUBLIC_STRUCTURE_MANIFEST_VERSION:
        raise PublicManifestError("공개 구조 manifest 버전이 다릅니다")
    sections = manifest.get("sections")
    tables = manifest.get("tables")
    packet_digests = manifest.get("evidence_packet_sha256s")
    if (
        not isinstance(sections, list)
        or any(not isinstance(item, str) for item in sections)
        or not isinstance(tables, list)
        or any(not isinstance(item, dict) for item in tables)
        or not isinstance(packet_digests, list)
        or any(
            not isinstance(item, list) or len(item) != 2
            for item in packet_digests
        )
    ):
        raise PublicManifestError("공개 구조 manifest 장·표·packet 형식이 깨졌습니다")
    unsigned = {key: manifest[key] for key in expected_keys if key != "digest"}
    # 내부 checksum은 우발적 손상 진단일 뿐이다. 저장 검증의 권위는 위에서
    # 별도 producer evidence가 운반한 exact bytes SHA-256이다.
    if manifest.get("digest") != canonical_sha256(unsigned):
        raise PublicManifestError("공개 구조 manifest 내부 checksum이 일치하지 않습니다")
    return manifest


def _source_bindings(report: Mapping[str, object]) -> dict[str, tuple[str, str]]:
    out: dict[str, tuple[str, str]] = {}
    for source in report.get("citations", ()):
        source_id = str(_value(source, "source_id", ""))
        if not source_id.startswith(_SOURCE_ID_PREFIX):
            continue
        fragment_id = source_id[len(_SOURCE_ID_PREFIX) :].strip()
        hashes = tuple(
            str(value)
            for value in _value(source, "exact_evidence_hashes", ())
            if _HEX_64_RE.fullmatch(str(value)) is not None
        )
        identity = (
            document_identity_from_parts(
                document_id=str(_value(source, "document_id", "")),
                host=str(_value(source, "host", "")),
                url=str(_value(source, "url", "")),
            )
            if isinstance(source, Mapping)
            else document_identity(source)
        )
        if not fragment_id or len(hashes) != 1 or not identity:
            raise PublicManifestError("actual 출처의 문서 신원·exact hash가 불완전합니다")
        if fragment_id in out:
            raise PublicManifestError("actual 출처의 fragment ID가 중복됐습니다")
        out[fragment_id] = (identity, hashes[0])
    return out


def _actual_table_fields(
    *, section_id: str, table_index: int, table: object
) -> dict[str, object]:
    rows = [
        [str(cell) for cell in row] for row in _value(table, "rows", ())
    ]
    return {
        "section_id": section_id,
        "table_index": table_index,
        "kind": (
            "flow"
            if str(_value(table, "presentation", "table")) == "flow"
            else "program"
        ),
        "caption": str(_value(table, "caption", "")),
        "headers": [str(value) for value in _value(table, "headers", ())],
        "rows": rows,
        "cite": str(_value(table, "cite", "")),
        "numeric": bool(_value(table, "numeric", False)),
        "presentation": str(_value(table, "presentation", "table")),
        "display_unit": str(_value(table, "display_unit", "")),
        "raw_rows": [
            [str(cell) for cell in row]
            for row in _value(table, "raw_rows", ())
        ],
        "scale_divisor": str(_value(table, "scale_divisor", "")),
        "scale_places": int(_value(table, "scale_places", 0)),
        "entity_scope": str(_value(table, "entity_scope", "")),
        "raw_unit": str(_value(table, "raw_unit", "")),
        "unit_dimension": str(_value(table, "unit_dimension", "")),
        "source_cites": [
            str(value) for value in _value(table, "source_cites", ())
        ],
        "row_fact_ids": [
            str(value) for value in _value(table, "row_fact_ids", ())
        ],
        "row_evidence_refs": [
            str(value) for value in _value(table, "row_evidence_refs", ())
        ],
        "row_binding_refs": [
            str(value) for value in _value(table, "row_binding_refs", ())
        ],
        "cell_binding_refs": [
            [str(value) for value in row]
            for row in _value(table, "cell_binding_refs", ())
        ],
        "numeric_tokens": _numeric_tokens(rows),
    }


def _verify_row_bindings(
    table_entry: Mapping[str, Any],
    source_bindings: Mapping[str, tuple[str, str]],
) -> None:
    rows = table_entry.get("rows")
    row_bindings = table_entry.get("row_bindings")
    row_refs = table_entry.get("row_binding_refs")
    cell_refs = table_entry.get("cell_binding_refs")
    evidence_refs = table_entry.get("row_evidence_refs")
    if not all(isinstance(value, list) for value in (rows, row_bindings, row_refs, cell_refs, evidence_refs)):
        raise PublicManifestError("manifest 행 typed binding 형식이 깨졌습니다")
    if not (
        len(rows)
        == len(row_bindings)
        == len(row_refs)
        == len(cell_refs)
        == len(evidence_refs)
    ):
        raise PublicManifestError("manifest 행 typed binding 개수가 공개 행과 다릅니다")
    all_fragment_ids: list[str] = []
    for row_index, (row, binding, row_ref, row_cell_refs, evidence_ref) in enumerate(
        zip(rows, row_bindings, row_refs, cell_refs, evidence_refs)
    ):
        if not isinstance(binding, Mapping) or not isinstance(row_cell_refs, list):
            raise PublicManifestError("manifest 행 typed binding 항목이 객체가 아닙니다")
        if canonical_sha256(binding) != str(row_ref):
            raise PublicManifestError("manifest 행 typed binding ref가 일치하지 않습니다")
        typed_cells = binding.get("typed_cells")
        if not isinstance(typed_cells, list) or len(typed_cells) != len(row):
            raise PublicManifestError("manifest typed cell 결속이 공개 셀 전체를 덮지 않습니다")
        expected_cell_refs = [canonical_sha256(cell) for cell in typed_cells]
        if expected_cell_refs != [str(value) for value in row_cell_refs]:
            raise PublicManifestError("manifest typed cell ref가 일치하지 않습니다")
        if str(binding.get("row_evidence_hash") or "") != str(evidence_ref):
            raise PublicManifestError("manifest 행 evidence ref가 일치하지 않습니다")
        source_ids = binding.get("source_fragment_ids")
        identities = binding.get("document_identities")
        hashes = binding.get("exact_evidence_hashes")
        if (
            not isinstance(source_ids, list)
            or not isinstance(identities, list)
            or not isinstance(hashes, list)
            or not source_ids
            or len(source_ids) != len(identities)
            or len(source_ids) != len(hashes)
        ):
            raise PublicManifestError("manifest 행 출처 결속이 불완전합니다")
        for fragment_id, identity, exact_hash in zip(source_ids, identities, hashes):
            if source_bindings.get(str(fragment_id)) != (
                str(identity),
                str(exact_hash),
            ):
                raise PublicManifestError(
                    f"manifest {row_index + 1}번 행 출처가 actual 부록과 다릅니다"
                )
            all_fragment_ids.append(str(fragment_id))
    expected_cites = [
        f"[{number}]"
        for number in sorted(
            {
                int(citation_number(fragment_id))
                for fragment_id in all_fragment_ids
                if citation_number(fragment_id)
            }
        )
    ]
    if expected_cites != [str(value) for value in table_entry.get("source_cites", [])]:
        raise PublicManifestError("manifest 전체 source_cites가 행별 출처 합집합과 다릅니다")


def assert_report_matches_manifest(
    report: Mapping[str, object],
    manifest_json: str,
    *,
    expected_manifest_sha256: str = "",
) -> dict[str, Any]:
    """actual 표·flow를 외부 manifest bytes authority와 완전 비교한다."""

    manifest = _load_manifest(
        manifest_json,
        expected_sha256=expected_manifest_sha256,
    )
    if str(report.get("public_structure_manifest") or "") != manifest_json:
        raise PublicManifestError("Report가 운반한 manifest bytes가 기대값과 다릅니다")
    if str(report.get("company_id") or "") != str(manifest["company_id"]):
        raise PublicManifestError("Report와 manifest의 company_id가 다릅니다")
    sections = list(report.get("sections", ()))
    if [str(_value(section, "cell", "")) for section in sections] != manifest[
        "sections"
    ]:
        raise PublicManifestError("actual 장 id·순서가 manifest와 다릅니다")
    source_bindings = _source_bindings(report)
    actual_tables: list[dict[str, object]] = []
    for section in sections:
        section_id = str(_value(section, "cell", ""))
        for table_index, table in enumerate(_value(section, "tables", ())):
            actual = _actual_table_fields(
                section_id=section_id,
                table_index=table_index,
                table=table,
            )
            actual["manifest_ref"] = str(_value(table, "manifest_ref", ""))
            actual_tables.append(actual)
    expected_tables = manifest["tables"]
    if len(actual_tables) != len(expected_tables):
        raise PublicManifestError("actual 표 개수가 manifest와 다릅니다")
    for actual, expected in zip(actual_tables, expected_tables):
        if not isinstance(expected, Mapping):
            raise PublicManifestError("manifest 표 항목 형식이 깨졌습니다")
        _verify_row_bindings(expected, source_bindings)
        row_bindings = expected.get("row_bindings")
        unsigned_expected = {
            key: value for key, value in expected.items() if key != "manifest_ref"
        }
        expected_ref = canonical_sha256(unsigned_expected)
        if expected.get("manifest_ref") != expected_ref:
            raise PublicManifestError("manifest 표 ref가 내부 표 구조와 다릅니다")
        public_expected = {
            key: value
            for key, value in expected.items()
            if key not in {"row_bindings"}
        }
        if actual != public_expected:
            raise PublicManifestError(
                "actual 표·flow의 행/셀/숫자/typed 출처가 manifest와 다릅니다"
            )
        if not isinstance(row_bindings, list):
            raise PublicManifestError("manifest row_bindings가 누락됐습니다")
    return manifest


def assert_report_matches_generation_evidence(
    report_payload: Mapping[str, object],
    evidence: GenerationProducerEvidence,
    *,
    manifest_bytes: bytes,
) -> None:
    """Mapping/bytes 경계에서 producer transport와 공개 content를 대조한다.

    이 함수는 composer/pipeline/storage feature 타입을 import하지 않는다. 출고
    권위는 별도 receipt가 가진 ``evidence``와 exact ``manifest_bytes``를 넘겨
    같은 JSON 안의 자체 checksum을 신뢰하지 않는다.
    """

    if type(evidence) is not GenerationProducerEvidence:
        raise PublicManifestError("FULL 보고서의 generation producer evidence가 없습니다")
    try:
        assert_canonical_producer_evidence(evidence)
        canonical_evidence = producer_evidence_from_dict(
            producer_evidence_to_dict(evidence)
        )
    except (TypeError, ValueError) as error:
        raise PublicManifestError("generation producer evidence wire가 깨졌습니다") from error
    if canonical_evidence != evidence:
        raise PublicManifestError("generation producer evidence가 canonical wire와 다릅니다")
    if str(report_payload.get("release_mode") or "") != "FULL":
        raise PublicManifestError("generation producer evidence는 FULL 결과에만 유효합니다")
    if str(report_payload.get("company_id") or "") != evidence.company_id:
        raise PublicManifestError("Report와 producer evidence의 company_id가 다릅니다")
    if _public_text(report_payload.get("grade")) != QualityGrade.COMPLETE.value:
        raise PublicManifestError("FULL Report 공개 등급이 완성이 아닙니다")
    if [str(value) for value in report_payload.get("shortfall_reasons", ())]:
        raise PublicManifestError("FULL Report 공개 부족 사유가 남아 있습니다")
    contract_version = str(report_payload.get("quality_contract_version") or "")
    try:
        contract = contract_for_stored_assessment(contract_version)
    except ValueError as error:
        raise PublicManifestError("알 수 없는 품질 계약 버전입니다") from error
    if (
        contract.version not in STRICT_QUALITY_CONTRACT_VERSIONS
        or contract_version != evidence.assessment.contract_version
    ):
        raise PublicManifestError("FULL 품질 계약이 평가 원본과 다릅니다")
    if str(report_payload.get("safety_decision") or "") != (
        evidence.assessment.safety.decision.value
    ):
        raise PublicManifestError("Report 안전 판정이 GenerationAssessment와 다릅니다")
    if (
        evidence.assessment.quality.grade is not QualityGrade.COMPLETE
        or evidence.assessment.publication_grade is not QualityGrade.COMPLETE
        or evidence.assessment.safety.decision is not ReleaseDecision.RELEASE_ALLOWED
        or evidence.assessment.quality.shortfall_reasons
    ):
        raise PublicManifestError("producer evidence가 완성 공개 판정이 아닙니다")
    if str(report_payload.get("publication_policy") or "") != (
        PublicationPolicy.STRUCTURED_SAFETY.value
    ):
        raise PublicManifestError("FULL Report 공개 정책이 structured safety가 아닙니다")
    try:
        manifest_json = manifest_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        raise PublicManifestError("공개 manifest bytes가 UTF-8이 아닙니다") from error
    if str(report_payload.get("public_structure_manifest") or "") != manifest_json:
        raise PublicManifestError("Report manifest와 별도 receipt bytes가 다릅니다")
    manifest = assert_report_matches_manifest(
        report_payload,
        manifest_json,
        expected_manifest_sha256=evidence.public_manifest_sha256,
    )
    if manifest["evidence_generation_sha256"] != evidence.evidence_generation_sha256:
        raise PublicManifestError("manifest와 producer evidence generation이 다릅니다")
    packet_digests = tuple(
        (str(section_id), str(digest))
        for section_id, digest in manifest["evidence_packet_sha256s"]
    )
    if packet_digests != evidence.evidence_packet_sha256s:
        raise PublicManifestError("manifest와 producer의 9장 packet 지문이 다릅니다")
    content_digest, section_digests = public_content_digests(report_payload)
    if content_digest != evidence.public_content_sha256:
        raise PublicManifestError("최종 공개 content가 producer evidence와 다릅니다")
    if section_digests != evidence.section_sha256s:
        raise PublicManifestError("최종 공개 9장 content 지문이 producer evidence와 다릅니다")


__all__ = [
    "PUBLIC_STRUCTURE_MANIFEST_VERSION",
    "PublicManifestError",
    "assert_report_matches_generation_evidence",
    "assert_report_matches_manifest",
    "public_content_projection",
    "public_content_digests",
    "report_public_content_projection",
    "report_verification_payload",
    "section_public_projection",
    "table_public_projection",
]
