"""FULL 공개 표·flow를 pre-render 입력에 봉인하는 독립 manifest.

같은 renderer를 두 번 호출한 결과끼리 비교하지 않는다. 이 모듈은 renderer를
import하지 않고 검증을 마친 입력으로 기대 구조를 만들고, 최종 Report를 반대
방향으로 읽어 행·셀·숫자·출처를 완전 비교한다.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Final
from urllib.parse import urlsplit

from src.core.citations import citation_number
from src.features.composer.constants import (
    DART_DOCUMENT_HOST,
    DART_DOCUMENT_URL_TEMPLATE,
    DART_FINANCIAL_API_DOCUMENT_ID,
    DART_FINANCIAL_API_HOST,
    DART_FINANCIAL_API_PREFIX,
    DART_FINANCIAL_API_URL,
    FLOW_ARROW_SECTION_IDS,
    FLOW_CAPTION_BY_SECTION,
    FLOW_HEADERS_BY_SECTION,
    FLOW_PRESENTATION,
    FLOW_UNCONFIRMED_CELL,
    SECTION_IDS,
)
from src.features.composer.logic import FragmentsInput, _normalize_fragments
from src.features.composer.port import (
    CollectedFragment,
    ComposedReport,
    FilingMeta,
    PerformanceTable,
    StructuredClaim,
)
from src.features.pipeline.port import Report, ReportTable
from src.features.provenance.sources import Source, exact_evidence_text_hash
from src.shared.dart_financial_provenance import dart_payload_matches_table
from src.shared.report_quality.source_identity import (
    document_identity,
    document_identity_from_parts,
)


PUBLIC_STRUCTURE_MANIFEST_VERSION: Final[str] = "public-structure-manifest-v1"
_COMPOSITION_PRESENTATION: Final[str] = "composition"
_SOURCE_ID_PREFIX: Final[str] = "v2-frag-"
_HEX_64_RE: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}")
_NUMERIC_TOKEN_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<![\w])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?"
)
_BINDING_KEYS: Final[frozenset[str]] = frozenset(
    {
        "source_fragment_ids",
        "document_identities",
        "exact_evidence_hashes",
        "row_evidence_hash",
        "injected_fact_id",
    }
)


class PublicManifestError(ValueError):
    """공개 구조를 원자료에 완전히 결속할 수 없을 때의 오류."""


@dataclass(frozen=True)
class PublicStructureSeal:
    """pipeline이 renderer에 전달하고 뒤에서 다시 검증할 immutable 봉인."""

    canonical_json: str
    table_refs: tuple[tuple[str, int, str], ...]

    def ref_for(self, section_id: str, table_index: int) -> str:
        for owner, index, value in self.table_refs:
            if owner == section_id and index == table_index:
                return value
        return ""

    def table_entry(self, section_id: str, table_index: int) -> Mapping[str, object]:
        """canonical JSON에서 해당 표 항목을 새 dict로 읽어 돌려준다."""

        payload = json.loads(self.canonical_json)
        for table in payload.get("tables", []):
            if (
                isinstance(table, dict)
                and table.get("section_id") == section_id
                and table.get("table_index") == table_index
            ):
                return dict(table)
        return {}


@dataclass(frozen=True)
class _FragmentBinding:
    fragment_id: str
    document_identity: str
    exact_evidence_hash: str


def _canonical_json(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _normalized_source_cites(fragment_ids: Sequence[str]) -> tuple[str, ...]:
    numbers: set[int] = set()
    for fragment_id in fragment_ids:
        normalized = citation_number(str(fragment_id or ""))
        if not normalized or not normalized.isascii() or not normalized.isdigit():
            raise PublicManifestError(
                f"표 행의 출처 조각 번호가 유효하지 않습니다: {fragment_id!r}"
            )
        numbers.add(int(normalized))
    return tuple(f"[{number}]" for number in sorted(numbers))


def _fallback_document_identity(fragment_id: str, exact_hash: str) -> str:
    if not fragment_id or _HEX_64_RE.fullmatch(exact_hash) is None:
        return ""
    return "embedded:" + _sha256_text(
        f"{_SOURCE_ID_PREFIX}{fragment_id}\x00{exact_hash}"
    )


def _fragment_binding(
    fragment: CollectedFragment, filing_meta: FilingMeta | None
) -> _FragmentBinding:
    fragment_id = str(fragment.fragment_id).strip()
    exact_hash = exact_evidence_text_hash(fragment.text)
    identity = ""
    if str(fragment.text).startswith(DART_FINANCIAL_API_PREFIX):
        identity = document_identity_from_parts(
            document_id=DART_FINANCIAL_API_DOCUMENT_ID,
            host=DART_FINANCIAL_API_HOST,
            url=DART_FINANCIAL_API_URL,
        )
    elif fragment.source_url:
        try:
            host = (urlsplit(fragment.source_url).hostname or "").casefold()
        except ValueError:
            host = ""
        identity = document_identity_from_parts(host=host, url=fragment.source_url)
    elif filing_meta is not None and filing_meta.document_id:
        identity = document_identity_from_parts(
            document_id=filing_meta.document_id,
            host=DART_DOCUMENT_HOST,
            url=DART_DOCUMENT_URL_TEMPLATE.format(
                document_id=filing_meta.document_id
            ),
        )
    if not identity:
        identity = _fallback_document_identity(fragment_id, exact_hash)
    if not fragment_id or not identity or _HEX_64_RE.fullmatch(exact_hash) is None:
        raise PublicManifestError(
            f"조각 {fragment_id!r}의 문서 신원 또는 exact evidence hash가 없습니다"
        )
    return _FragmentBinding(fragment_id, identity, exact_hash)


def _source_binding(source: Source) -> _FragmentBinding | None:
    if not source.source_id.startswith(_SOURCE_ID_PREFIX):
        return None
    fragment_id = source.source_id[len(_SOURCE_ID_PREFIX) :].strip()
    hashes = tuple(
        str(value)
        for value in source.exact_evidence_hashes
        if _HEX_64_RE.fullmatch(str(value)) is not None
    )
    if len(hashes) != 1:
        return None
    identity = document_identity(source) or _fallback_document_identity(
        fragment_id, hashes[0]
    )
    return (
        _FragmentBinding(fragment_id, identity, hashes[0])
        if identity
        else None
    )


def _numeric_tokens(rows: Sequence[Sequence[str]]) -> list[list[str]]:
    return [
        [
            match.group(0)
            for cell in row
            for match in _NUMERIC_TOKEN_RE.finditer(str(cell))
        ]
        for row in rows
    ]


def _evidence_records(value: object) -> tuple[Mapping[str, object], ...]:
    """evidence JSON 안에서 실제 표 머리글로 열을 식별할 수 있는 객체들."""

    out: list[Mapping[str, object]] = []

    def walk(item: object) -> None:
        if isinstance(item, Mapping):
            out.append(item)
            for nested in item.values():
                walk(nested)
        elif isinstance(item, (list, tuple)):
            for nested in item:
                walk(nested)

    walk(value)
    return tuple(out)


def _decimal(value: object) -> Decimal | None:
    raw = str(value or "").strip().replace(",", "").removesuffix("%")
    if not raw or len(raw) > 128:
        return None
    try:
        parsed = Decimal(raw)
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _generic_evidence_matches_row(
    headers: Sequence[str],
    row: Sequence[str],
    raw_row: Sequence[str] | None,
    evidence: str,
    *,
    scale_divisor: str,
    scale_places: int,
) -> bool:
    try:
        payload = json.loads(evidence)
    except (json.JSONDecodeError, TypeError, ValueError):
        return False
    normalized_headers = tuple(" ".join(str(value).split()) for value in headers)
    if len(normalized_headers) != len(row) or len(set(normalized_headers)) != len(
        normalized_headers
    ):
        return False
    candidates = []
    for record in _evidence_records(payload):
        normalized_record = {
            " ".join(str(key).split()): value for key, value in record.items()
        }
        if all(header in normalized_record for header in normalized_headers):
            candidates.append(normalized_record)
    if len(candidates) != 1:
        # 값이 evidence 어딘가에 존재한다는 사실만으로는 열 바꿔치기를 막지
        # 못한다. 머리글→원값 대응을 하나로 특정할 수 있을 때만 재검산한다.
        return False
    record = candidates[0]
    if raw_row is not None:
        if len(raw_row) != len(row) or str(raw_row[0]) != str(row[0]):
            return False
        divisor = _decimal(scale_divisor) if scale_divisor else Decimal(1)
        if divisor is None or divisor == 0 or not 0 <= scale_places <= 12:
            return False
        quantum = Decimal(1).scaleb(-scale_places)
        for index, (header, public, raw) in enumerate(
            zip(normalized_headers, row, raw_row)
        ):
            evidence_value = record[header]
            if index == 0:
                if " ".join(str(raw).split()) != " ".join(
                    str(evidence_value).split()
                ):
                    return False
                continue
            raw_number = _decimal(raw)
            public_number = _decimal(public)
            evidence_number = _decimal(evidence_value)
            try:
                recalculated = (
                    (raw_number / divisor).quantize(
                        quantum, rounding=ROUND_HALF_UP
                    )
                    if raw_number is not None
                    else None
                )
            except (ArithmeticError, InvalidOperation, ValueError):
                return False
            if (
                raw_number is None
                or public_number is None
                or evidence_number != raw_number
                or recalculated != public_number
            ):
                return False
        return True
    for header, cell in zip(normalized_headers, row):
        evidence_value = record[header]
        normalized = " ".join(str(cell).split())
        number = _decimal(cell)
        if number is not None:
            if number != _decimal(evidence_value):
                return False
        elif normalized != " ".join(str(evidence_value).split()):
            return False
    return True


def _validate_composition_total(table: PerformanceTable) -> None:
    for index, header in enumerate(table.headers):
        if "%" not in str(header) and "비중" not in str(header):
            continue
        values = tuple(
            _decimal(row[index])
            for row in table.rows
            if len(row) > index and "합계" not in str(row[0])
        )
        if values and all(value is not None for value in values):
            total = sum((value for value in values if value is not None), Decimal(0))
            if total != Decimal(100):
                raise PublicManifestError(
                    f"구성 표의 공개 비중 합계가 100%가 아닙니다: {total}"
                )


def _claim_supports_row(
    claim: StructuredClaim,
    row: Sequence[str],
    raw_row: Sequence[str] | None,
    source: _FragmentBinding,
) -> bool:
    """검증 fact ID가 이름뿐 아니라 공개 행의 모든 셀을 실제로 봉인하는가."""

    if (
        claim.source_fragment_id != source.fragment_id
        or claim.source_identity != source.document_identity
        or claim.verification_state != "verified"
        or not claim.fact_id.strip()
    ):
        return False
    textual_values = {
        " ".join(str(value).split())
        for value in (
            claim.subject_scope,
            claim.metric,
            claim.period_start,
            claim.period_end,
            claim.sign,
            claim.unit,
            claim.unit_dimension,
            claim.formula,
            claim.raw_value,
            claim.display_value,
        )
        if str(value).strip()
    }
    numeric_values = {
        value
        for raw in (claim.raw_value, claim.display_value)
        if (value := _decimal(raw)) is not None
    }

    def supported(cell: object) -> bool:
        normalized = " ".join(str(cell).split())
        if not normalized:
            return False
        number = _decimal(normalized)
        if number is not None:
            if number not in numeric_values:
                return False
            if normalized.endswith("%") and claim.unit != "%":
                return False
            return True
        return normalized in textual_values

    if not row or not all(supported(cell) for cell in row):
        return False
    if raw_row is not None and (
        len(raw_row) != len(row) or not all(supported(cell) for cell in raw_row)
    ):
        return False
    return True


def _validated_program_bindings(
    table: PerformanceTable,
    fragments: Mapping[str, _FragmentBinding],
    verified_claims: Mapping[str, StructuredClaim],
) -> tuple[dict[str, object], ...]:
    width = len(table.headers)
    if (
        width == 0
        or not table.rows
        or any(len(row) != width for row in table.rows)
        or (table.raw_rows and len(table.raw_rows) != len(table.rows))
        or (table.row_fact_ids and len(table.row_fact_ids) != len(table.rows))
    ):
        raise PublicManifestError("프로그램 표의 행·열 또는 fact id 모양이 깨졌습니다")
    fragment_id = citation_number(table.cite)
    source = fragments.get(fragment_id)
    if source is None:
        raise PublicManifestError("프로그램 표 cite가 검증된 원문 조각을 가리키지 않습니다")
    evidence_rows = tuple(str(value) for value in table.evidence_rows)
    fact_ids = tuple(str(value).strip() for value in table.row_fact_ids)
    if not evidence_rows:
        evidence_rows = tuple("" for _ in table.rows)
    if not fact_ids:
        fact_ids = tuple("" for _ in table.rows)
    if len(evidence_rows) != len(table.rows):
        raise PublicManifestError("프로그램 표 evidence_rows가 공개 행 수와 다릅니다")
    unique_evidence = tuple(dict.fromkeys(value for value in evidence_rows if value))
    is_dart_table = (
        len(unique_evidence) == 1
        and bool(table.raw_rows)
        and dart_payload_matches_table(table, unique_evidence[0])
    )
    if table.raw_rows and table.entity_scope and not is_dart_table:
        raise PublicManifestError("프로그램 재무 표가 canonical numeric 검증에 실패했습니다")
    _validate_composition_total(table)

    bindings: list[dict[str, object]] = []
    for index, row in enumerate(table.rows):
        fact_id = fact_ids[index]
        evidence = evidence_rows[index]
        if fact_id:
            claim = verified_claims.get(fact_id)
            if claim is None or not _claim_supports_row(
                claim,
                row,
                table.raw_rows[index] if table.raw_rows else None,
                source,
            ):
                raise PublicManifestError(
                    "프로그램 표 행의 injected fact가 출처·공개 셀에 완전히 "
                    f"결속되지 않았습니다: {fact_id}"
                )
        else:
            if not evidence:
                raise PublicManifestError(
                    f"프로그램 표 {index + 1}번 행의 evidence_rows가 비었습니다"
                )
            if not is_dart_table and not _generic_evidence_matches_row(
                table.headers,
                row,
                table.raw_rows[index] if table.raw_rows else None,
                evidence,
                scale_divisor=table.scale_divisor,
                scale_places=table.scale_places,
            ):
                raise PublicManifestError(
                    f"프로그램 표 {index + 1}번 행을 원자료로 재검산할 수 없습니다"
                )
        bindings.append(
            {
                "source_fragment_ids": [source.fragment_id],
                "document_identities": [source.document_identity],
                "exact_evidence_hashes": [source.exact_evidence_hash],
                "row_evidence_hash": (
                    exact_evidence_text_hash(evidence) if evidence else ""
                ),
                "injected_fact_id": fact_id,
            }
        )
    return tuple(bindings)


def _flow_binding(
    fragment_ids: Sequence[str],
    fragments: Mapping[str, _FragmentBinding],
) -> dict[str, object]:
    ids = tuple(dict.fromkeys(str(value).strip() for value in fragment_ids))
    sources = tuple(fragments.get(fragment_id) for fragment_id in ids)
    if not ids or any(source is None for source in sources):
        raise PublicManifestError("flow 행이 검증된 출처 조각에 완전히 결속되지 않았습니다")
    concrete = tuple(source for source in sources if source is not None)
    return {
        "source_fragment_ids": [source.fragment_id for source in concrete],
        "document_identities": [source.document_identity for source in concrete],
        "exact_evidence_hashes": [source.exact_evidence_hash for source in concrete],
        "row_evidence_hash": "",
        "injected_fact_id": "",
    }


def _table_payload(
    *,
    section_id: str,
    table_index: int,
    kind: str,
    caption: str,
    headers: Sequence[str],
    rows: Sequence[Sequence[str]],
    cite: str,
    numeric: bool,
    presentation: str,
    display_unit: str,
    raw_rows: Sequence[Sequence[str]],
    scale_divisor: str,
    scale_places: int,
    entity_scope: str,
    raw_unit: str,
    unit_dimension: str,
    evidence_rows: Sequence[str],
    source_cites: Sequence[str],
    row_fact_ids: Sequence[str],
    row_bindings: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    return {
        "section_id": section_id,
        "table_index": table_index,
        "kind": kind,
        "caption": str(caption),
        "headers": [str(value) for value in headers],
        "rows": [[str(cell) for cell in row] for row in rows],
        "cite": str(cite),
        "numeric": bool(numeric),
        "presentation": str(presentation),
        "display_unit": str(display_unit),
        "raw_rows": [[str(cell) for cell in row] for row in raw_rows],
        "scale_divisor": str(scale_divisor),
        "scale_places": int(scale_places),
        "entity_scope": str(entity_scope),
        "raw_unit": str(raw_unit),
        "unit_dimension": str(unit_dimension),
        "evidence_rows": [str(value) for value in evidence_rows],
        "source_cites": [str(value) for value in source_cites],
        "row_fact_ids": [str(value) for value in row_fact_ids],
        "row_bindings": [dict(value) for value in row_bindings],
        "numeric_tokens": _numeric_tokens(rows),
    }


def build_public_structure_seal(
    report: ComposedReport,
    fragments: FragmentsInput,
    performance_table: PerformanceTable | None,
    *,
    filing_meta: FilingMeta | None,
    composition_tables: tuple[PerformanceTable, ...],
    table_presentation: str,
) -> PublicStructureSeal:
    """검증된 pre-render 입력만으로 공개 표·flow 정본을 만든다."""

    fragment_bindings = {
        fragment.fragment_id: _fragment_binding(fragment, filing_meta)
        for fragment in _normalize_fragments(fragments)
    }
    verified_claims: dict[str, StructuredClaim] = {}
    for section in report.sections:
        for sentence in section.sentences:
            claim = sentence.structured_claim
            if (
                claim is None
                or sentence.verification_state != "verified"
                or claim.verification_state != "verified"
            ):
                continue
            if not claim.fact_id.strip() or claim.fact_id in verified_claims:
                raise PublicManifestError(
                    "검증된 injected fact ID가 비었거나 중복됐습니다"
                )
            verified_claims[claim.fact_id] = claim
    tables: list[dict[str, object]] = []
    refs: list[tuple[str, int, str]] = []
    for section in report.sections:
        section_tables: list[dict[str, object]] = []
        if section.section_id in FLOW_HEADERS_BY_SECTION and section.flow_rows:
            rows: list[list[str]] = []
            row_bindings: list[dict[str, object]] = []
            for row in section.flow_rows:
                binding = _flow_binding(row.citations, fragment_bindings)
                rows.append(
                    [
                        (str(cell).strip() or FLOW_UNCONFIRMED_CELL)
                        if section.section_id in FLOW_ARROW_SECTION_IDS
                        else str(cell).strip()
                        for cell in row.cells
                    ]
                )
                row_bindings.append(binding)
            source_ids = tuple(
                fragment_id
                for binding in row_bindings
                for fragment_id in binding["source_fragment_ids"]
            )
            source_cites = _normalized_source_cites(source_ids)
            evidence_rows = tuple(_canonical_json(binding) for binding in row_bindings)
            section_tables.append(
                _table_payload(
                    section_id=section.section_id,
                    table_index=0,
                    kind="flow",
                    caption=FLOW_CAPTION_BY_SECTION[section.section_id],
                    headers=FLOW_HEADERS_BY_SECTION[section.section_id],
                    rows=rows,
                    cite=source_cites[0],
                    numeric=False,
                    presentation=FLOW_PRESENTATION,
                    display_unit="",
                    raw_rows=(),
                    scale_divisor="",
                    scale_places=0,
                    entity_scope="",
                    raw_unit="",
                    unit_dimension="",
                    evidence_rows=evidence_rows,
                    source_cites=source_cites,
                    row_fact_ids=("",) * len(rows),
                    row_bindings=row_bindings,
                )
            )
        program_slots: list[tuple[PerformanceTable, str]] = []
        if (
            section.section_id == "past_changes"
            and performance_table is not None
            and performance_table.rows
        ):
            program_slots.append((performance_table, table_presentation or "table"))
        elif section.section_id == "business_model":
            program_slots.extend(
                (table, _COMPOSITION_PRESENTATION)
                for table in composition_tables
                if table.rows
            )
        for table, presentation in program_slots:
            row_bindings = _validated_program_bindings(
                table, fragment_bindings, verified_claims
            )
            fragment_id = citation_number(table.cite)
            source_cites = _normalized_source_cites((fragment_id,))
            caption = table.caption
            if table.unit and "단위" not in caption:
                caption = f"{caption} (단위: {table.unit})"
            fact_ids = (
                tuple(table.row_fact_ids)
                if table.row_fact_ids
                else ("",) * len(table.rows)
            )
            section_tables.append(
                _table_payload(
                    section_id=section.section_id,
                    table_index=0,
                    kind="program",
                    caption=caption,
                    headers=table.headers,
                    rows=table.rows,
                    cite=table.cite,
                    numeric=True,
                    presentation=presentation,
                    display_unit=table.unit,
                    raw_rows=table.raw_rows,
                    scale_divisor=table.scale_divisor,
                    scale_places=table.scale_places,
                    entity_scope=table.entity_scope,
                    raw_unit=table.raw_unit,
                    unit_dimension=table.unit_dimension,
                    evidence_rows=table.evidence_rows,
                    source_cites=source_cites,
                    row_fact_ids=fact_ids,
                    row_bindings=row_bindings,
                )
            )
        for table_index, payload in enumerate(section_tables):
            payload["table_index"] = table_index
            ref = _sha256_text(_canonical_json(payload))
            payload["manifest_ref"] = ref
            refs.append((section.section_id, table_index, ref))
            tables.append(payload)

    section_ids = tuple(section.section_id for section in report.sections)
    if section_ids != SECTION_IDS:
        raise PublicManifestError("pre-render 장 id·순서가 SECTION_IDS와 다릅니다")
    unsigned = {
        "version": PUBLIC_STRUCTURE_MANIFEST_VERSION,
        "sections": list(section_ids),
        "tables": tables,
    }
    manifest = {**unsigned, "digest": _sha256_text(_canonical_json(unsigned))}
    return PublicStructureSeal(
        canonical_json=_canonical_json(manifest),
        table_refs=tuple(refs),
    )


def _parse_binding_row(value: str) -> dict[str, object] | None:
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    if not isinstance(parsed, dict) or set(parsed) != _BINDING_KEYS:
        return None
    source_ids = parsed.get("source_fragment_ids")
    identities = parsed.get("document_identities")
    hashes = parsed.get("exact_evidence_hashes")
    if (
        not isinstance(source_ids, list)
        or not isinstance(identities, list)
        or not isinstance(hashes, list)
        or not source_ids
        or len(source_ids) != len(identities)
        or len(source_ids) != len(hashes)
        or any(not str(value).strip() for value in source_ids)
        or any(not str(value).strip() for value in identities)
        or any(_HEX_64_RE.fullmatch(str(value)) is None for value in hashes)
    ):
        return None
    return {
        "source_fragment_ids": [str(value) for value in source_ids],
        "document_identities": [str(value) for value in identities],
        "exact_evidence_hashes": [str(value) for value in hashes],
        "row_evidence_hash": str(parsed.get("row_evidence_hash") or ""),
        "injected_fact_id": str(parsed.get("injected_fact_id") or ""),
    }


def _actual_row_bindings(
    table: ReportTable,
    source_by_fragment: Mapping[str, _FragmentBinding],
) -> tuple[dict[str, object], ...]:
    fact_ids = (
        tuple(str(value) for value in table.row_fact_ids)
        if table.row_fact_ids
        else ("",) * len(table.rows)
    )
    if len(fact_ids) != len(table.rows):
        raise PublicManifestError("actual 표의 row_fact_ids가 행 수와 다릅니다")
    if table.presentation == FLOW_PRESENTATION:
        if len(table.evidence_rows) != len(table.rows):
            raise PublicManifestError("actual flow evidence_rows가 행 수와 다릅니다")
        parsed = tuple(_parse_binding_row(value) for value in table.evidence_rows)
        if any(value is None for value in parsed):
            raise PublicManifestError("actual flow 행 근거 manifest를 읽을 수 없습니다")
        concrete = tuple(value for value in parsed if value is not None)
        all_fragment_ids: list[str] = []
        for binding in concrete:
            source_ids = tuple(
                str(value) for value in binding["source_fragment_ids"]
            )
            identities = tuple(
                str(value) for value in binding["document_identities"]
            )
            hashes = tuple(str(value) for value in binding["exact_evidence_hashes"])
            if (
                len(source_ids) != len(set(source_ids))
                or binding["row_evidence_hash"]
                or binding["injected_fact_id"]
            ):
                raise PublicManifestError("actual flow 행 출처 결속 모양이 깨졌습니다")
            for fragment_id, identity, exact_hash in zip(
                source_ids, identities, hashes
            ):
                actual = source_by_fragment.get(fragment_id)
                if actual != _FragmentBinding(fragment_id, identity, exact_hash):
                    raise PublicManifestError(
                        "actual flow 행 출처가 부록의 문서 신원·exact hash와 다릅니다"
                    )
                all_fragment_ids.append(fragment_id)
        if tuple(table.source_cites) != _normalized_source_cites(all_fragment_ids):
            raise PublicManifestError("actual flow 전체 출처가 행별 출처 합집합과 다릅니다")
        return concrete
    evidence_rows = (
        tuple(str(value) for value in table.evidence_rows)
        if table.evidence_rows
        else ("",) * len(table.rows)
    )
    if len(evidence_rows) != len(table.rows):
        raise PublicManifestError("actual 프로그램 표 evidence_rows가 행 수와 다릅니다")
    fragment_ids = tuple(
        normalized
        for value in table.source_cites
        if (normalized := citation_number(value))
    )
    if not fragment_ids:
        raise PublicManifestError("actual 프로그램 표의 전체 source_cites가 비었습니다")
    sources = tuple(source_by_fragment.get(value) for value in fragment_ids)
    if any(source is None for source in sources):
        raise PublicManifestError("actual 프로그램 표 출처를 부록에서 찾지 못했습니다")
    concrete = tuple(source for source in sources if source is not None)
    return tuple(
        {
            "source_fragment_ids": [source.fragment_id for source in concrete],
            "document_identities": [source.document_identity for source in concrete],
            "exact_evidence_hashes": [source.exact_evidence_hash for source in concrete],
            "row_evidence_hash": (
                exact_evidence_text_hash(evidence_rows[index])
                if evidence_rows[index]
                else ""
            ),
            "injected_fact_id": fact_ids[index],
        }
        for index in range(len(table.rows))
    )


def _load_manifest(value: str) -> dict[str, Any]:
    try:
        manifest = json.loads(value)
    except (json.JSONDecodeError, TypeError, ValueError) as error:
        raise PublicManifestError("공개 구조 manifest JSON을 읽을 수 없습니다") from error
    if not isinstance(manifest, dict):
        raise PublicManifestError("공개 구조 manifest가 객체가 아닙니다")
    if set(manifest) != {"version", "sections", "tables", "digest"}:
        raise PublicManifestError("공개 구조 manifest 최상위 키가 계약과 다릅니다")
    if manifest.get("version") != PUBLIC_STRUCTURE_MANIFEST_VERSION:
        raise PublicManifestError("공개 구조 manifest 버전이 다릅니다")
    sections = manifest.get("sections")
    tables = manifest.get("tables")
    if (
        not isinstance(sections, list)
        or any(not isinstance(section_id, str) for section_id in sections)
        or not isinstance(tables, list)
        or any(not isinstance(table, dict) for table in tables)
    ):
        raise PublicManifestError("공개 구조 manifest 장·표 목록 형식이 깨졌습니다")
    unsigned = {
        "version": manifest["version"],
        "sections": manifest["sections"],
        "tables": manifest["tables"],
    }
    if manifest.get("digest") != _sha256_text(_canonical_json(unsigned)):
        raise PublicManifestError("공개 구조 manifest digest가 일치하지 않습니다")
    return manifest


def _actual_table_payloads(report: Report) -> list[dict[str, object]]:
    """Report 소비자 구조를 manifest와 같은 모양으로 역정규화한다."""

    source_by_fragment: dict[str, _FragmentBinding] = {}
    for source in report.citations:
        if not isinstance(source, Source):
            raise PublicManifestError("renderer actual 부록에 Source 아닌 값이 있습니다")
        binding = _source_binding(source)
        if binding is not None:
            if binding.fragment_id in source_by_fragment:
                raise PublicManifestError("renderer actual 부록의 조각 출처가 중복됐습니다")
            source_by_fragment[binding.fragment_id] = binding

    actual_tables: list[dict[str, object]] = []
    for section in report.sections:
        for table_index, table in enumerate(section.tables):
            if not table.manifest_ref:
                raise PublicManifestError("renderer actual 표의 manifest_ref가 없습니다")
            row_bindings = _actual_row_bindings(table, source_by_fragment)
            payload = _table_payload(
                section_id=section.cell,
                table_index=table_index,
                kind=(
                    "flow" if table.presentation == FLOW_PRESENTATION else "program"
                ),
                caption=table.caption,
                headers=table.headers,
                rows=table.rows,
                cite=table.cite,
                numeric=table.numeric,
                presentation=table.presentation,
                display_unit=table.display_unit,
                raw_rows=table.raw_rows,
                scale_divisor=table.scale_divisor,
                scale_places=table.scale_places,
                entity_scope=table.entity_scope,
                raw_unit=table.raw_unit,
                unit_dimension=table.unit_dimension,
                evidence_rows=table.evidence_rows,
                source_cites=table.source_cites,
                row_fact_ids=table.row_fact_ids,
                row_bindings=row_bindings,
            )
            ref = _sha256_text(_canonical_json(payload))
            if ref != table.manifest_ref:
                raise PublicManifestError("renderer actual 표의 manifest_ref가 구조와 다릅니다")
            payload["manifest_ref"] = ref
            actual_tables.append(payload)
    return actual_tables


def assert_report_matches_public_structure(
    report: Report, seal: PublicStructureSeal
) -> None:
    """renderer actual 구조와 pre-render seal을 셀·행·출처까지 완전 비교한다."""

    if not report.public_structure_manifest:
        raise PublicManifestError("renderer actual에 공개 구조 manifest가 없습니다")
    if report.public_structure_manifest != seal.canonical_json:
        raise PublicManifestError("renderer actual의 공개 구조 manifest가 바뀌었습니다")
    expected = _load_manifest(seal.canonical_json)
    if [section.cell for section in report.sections] != expected["sections"]:
        raise PublicManifestError("renderer actual 장 id·순서가 manifest와 다릅니다")
    actual_tables = _actual_table_payloads(report)
    if actual_tables != expected["tables"]:
        raise PublicManifestError(
            "renderer actual 표·flow의 행/셀/숫자/출처가 manifest와 다릅니다"
        )


def assert_stored_strict_manifest(report: Report) -> None:
    """strict JSON 재로드 뒤 manifest와 모든 표 참조가 남았는지 확인한다."""

    manifest = _load_manifest(report.public_structure_manifest)
    if report.public_structure_manifest != _canonical_json(manifest):
        raise PublicManifestError("strict 재로드 manifest canonical bytes가 바뀌었습니다")
    if [section.cell for section in report.sections] != manifest["sections"]:
        raise PublicManifestError("strict 재로드 장 id·순서가 manifest와 다릅니다")
    actual_tables = _actual_table_payloads(report)
    if actual_tables != manifest["tables"]:
        raise PublicManifestError(
            "strict 재로드 표·flow의 행/셀/숫자/출처가 manifest와 다릅니다"
        )
    for section in report.sections:
        for table in section.tables:
            has_evidence = (
                len(table.evidence_rows) == len(table.rows)
                and all(str(value).strip() for value in table.evidence_rows)
            )
            has_facts = (
                len(table.row_fact_ids) == len(table.rows)
                and all(str(value).strip() for value in table.row_fact_ids)
            )
            if not (has_evidence or has_facts):
                raise PublicManifestError(
                    "strict 재로드 표의 행 근거가 누락됐습니다"
                )


__all__ = [
    "PUBLIC_STRUCTURE_MANIFEST_VERSION",
    "PublicManifestError",
    "PublicStructureSeal",
    "assert_report_matches_public_structure",
    "assert_stored_strict_manifest",
    "build_public_structure_seal",
]
