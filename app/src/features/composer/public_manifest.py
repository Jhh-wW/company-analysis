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
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Final

from src.core.citations import citation_number
from src.features.composer.constants import (
    DART_DOCUMENT_HOST,
    DART_DOCUMENT_URL_TEMPLATE,
    DART_FINANCIAL_API_DOCUMENT_ID,
    DART_FINANCIAL_API_HOST,
    DART_FINANCIAL_API_LABEL,
    DART_FINANCIAL_API_PREFIX,
    DART_FINANCIAL_API_URL,
    FLOW_ARROW_SECTION_IDS,
    FLOW_CAPTION_BY_SECTION,
    FLOW_HEADERS_BY_SECTION,
    FLOW_PRESENTATION,
    FLOW_UNCONFIRMED_CELL,
    GRADE_INTERPRETED,
    CITATION_STYLE_MERGED,
    PARAGRAPH_MAX_SENTENCES,
    SECTION_IDS,
    SECTION_TITLES,
)
from src.features.composer.logic import FragmentsInput, _normalize_fragments
from src.features.composer.port import (
    CollectedFragment,
    ComposedReport,
    ComposedSentence,
    FilingMeta,
    PerformanceTable,
    StructuredClaim,
)
from src.features.pipeline.port import Report, ReportTable
from src.features.provenance.sources import (
    Source,
    SourceKind,
    bind_document_content_sha256,
    ensure_dart_profile_attesters,
    evidence_text_hash,
    exact_evidence_text_hash,
    full_typed_source_registry_problem,
    has_valid_provenance_seal,
    is_canonical_official_with_registry,
    seal_collected_source,
)
from src.shared.report_evidence.constants import (
    SOURCE_KIND_OFFICIAL_IDENTITY_VERIFIED_WEB_PAGE,
)
from src.shared.report_evidence.source_kind_policy import (
    formal_web_public_source_metadata,
)
from src.shared.dart_financial_provenance import dart_payload_matches_table
from src.shared.report_generation.canonical import (
    PUBLIC_STRUCTURE_MANIFEST_VERSION,
    PublicManifestError,
    assert_report_matches_manifest as _assert_report_matches_manifest,
    report_verification_payload,
)
from src.shared.report_generation.models import canonical_sha256
from src.shared.report_generation.constants import ENGINE_V2_SCHEMA_VERSION
from src.shared.report_quality.constants import STRICT_QUALITY_CONTRACT_VERSION
from src.shared.report_quality.models import (
    PublicationPolicy,
    QualityGrade,
    ReleaseDecision,
)
from src.shared.report_quality.source_identity import (
    bind_declared_document_identity_to_url,
    document_identity,
    document_identity_components,
    document_identity_from_parts,
)
from src.shared.revenue_table_provenance import (
    is_revenue_total_name,
    revenue_row_evidence_matches,
    revenue_table_evidence_identity,
)


_COMPOSITION_PRESENTATION: Final[str] = "composition"
_SOURCE_ID_PREFIX: Final[str] = "v2-frag-"
_HEX_64_RE: Final[re.Pattern[str]] = re.compile(r"[0-9a-f]{64}")
_NUMERIC_TOKEN_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<![\w])[-+]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?%?"
)
_INTERPRETATION_MARKER: Final[str] = f" — {GRADE_INTERPRETED}"
_SOURCE_LABEL_FALLBACK: Final[str] = "수집 자료"
_FILING_LABEL_PREFIX: Final[str] = "전자공시"
_SECTION_TAGS: Final[dict[str, str]] = {
    "past_changes": "#과거",
    "current_challenges": "#현재",
    "future_strategy": "#미래",
}
_BINDING_KEYS: Final[frozenset[str]] = frozenset(
    {
        "source_fragment_ids",
        "document_identities",
        "exact_evidence_hashes",
        "row_evidence_hash",
        "injected_fact_id",
    }
)


@dataclass(frozen=True)
class PublicStructureSeal:
    """pipeline이 renderer에 전달하고 뒤에서 다시 검증할 immutable 봉인."""

    canonical_json: str
    table_refs: tuple[tuple[str, int, str], ...]
    public_content_sha256: str
    section_sha256s: tuple[tuple[str, str], ...]

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


def _fragment_binding(
    fragment: CollectedFragment, filing_meta: FilingMeta | None
) -> _FragmentBinding:
    fragment_id = str(fragment.fragment_id).strip()
    exact_hash = exact_evidence_text_hash(fragment.text)
    declared_identity = str(fragment.document_identity).strip()
    if not declared_identity or declared_identity.startswith("embedded:"):
        raise PublicManifestError(
            f"조각 {fragment_id!r}에 검증된 외부 문서 identity가 없습니다"
        )
    derived_identity = ""
    if str(fragment.text).startswith(DART_FINANCIAL_API_PREFIX):
        derived_identity = document_identity_from_parts(
            document_id=DART_FINANCIAL_API_DOCUMENT_ID,
            host=DART_FINANCIAL_API_HOST,
            url=DART_FINANCIAL_API_URL,
        )
    elif fragment.source_url:
        # formal 공식 웹은 canonical URL, DART는 URL 안 접수번호와 결속된
        # document identity가 정본이다. 생산자가 선언한 문자열을 그대로
        # 믿지 않고 사용자가 실제 여는 URL에서 같은 shared 규칙으로 재계산한다.
        derived_identity = bind_declared_document_identity_to_url(
            declared_identity,
            fragment.source_url,
        )
    elif filing_meta is not None and filing_meta.document_id:
        derived_identity = document_identity_from_parts(
            document_id=filing_meta.document_id,
            host=DART_DOCUMENT_HOST,
            url=DART_DOCUMENT_URL_TEMPLATE.format(
                document_id=filing_meta.document_id
            ),
        )
    if derived_identity and derived_identity != declared_identity:
        raise PublicManifestError(
            f"조각 {fragment_id!r}의 packet 문서 identity가 원문 주소와 다릅니다"
        )
    if (
        not fragment_id
        or not declared_identity
        or _HEX_64_RE.fullmatch(exact_hash) is None
    ):
        raise PublicManifestError(
            f"조각 {fragment_id!r}의 문서 신원 또는 exact evidence hash가 없습니다"
        )
    return _FragmentBinding(fragment_id, declared_identity, exact_hash)


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
    identity = document_identity(source)
    if identity.startswith("embedded:"):
        return None
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
    record = _matching_evidence_record(headers, evidence)
    if record is None:
        return False
    normalized_headers = tuple(_normalized_header(value) for value in headers)
    if len(normalized_headers) != len(row):
        return False
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


def _normalized_header(value: object) -> str:
    return " ".join(str(value).split())


def _matching_evidence_record(
    headers: Sequence[str], evidence: str
) -> Mapping[str, object] | None:
    """중복 없는 공개 머리글 전체를 가진 원자료 record 하나만 고른다."""

    try:
        payload = json.loads(evidence)
    except (json.JSONDecodeError, TypeError, ValueError):
        return None
    normalized_headers = tuple(_normalized_header(value) for value in headers)
    if (
        not normalized_headers
        or any(not value for value in normalized_headers)
        or len(set(normalized_headers)) != len(normalized_headers)
    ):
        return None
    candidates = []
    for record in _evidence_records(payload):
        normalized_record = {
            _normalized_header(key): value for key, value in record.items()
        }
        if all(header in normalized_record for header in normalized_headers):
            candidates.append(normalized_record)
    if len(candidates) != 1:
        # 값이 evidence 어딘가에 존재한다는 사실만으로는 열 바꿔치기를 막지
        # 못한다. 머리글→원값 대응을 하나로 특정할 수 있을 때만 재검산한다.
        return None
    return candidates[0]


def _validate_composition_total(table: PerformanceTable) -> None:
    for index, header in enumerate(table.headers):
        if "%" not in str(header) and "비중" not in str(header):
            continue
        raw_values = tuple(
            str(row[index]).strip().replace(",", "").removesuffix("%")
            for row in table.rows
            if len(row) > index
            and not is_revenue_total_name(row[0])
            and re.sub(r"\s+", "", str(row[0])) != "소계"
        )
        values = tuple(_decimal(value) for value in raw_values)
        if not values or any(value is None for value in values):
            raise PublicManifestError(
                "구성 표의 공개 비중 합계를 숫자로 재검산할 수 없습니다"
            )
        concrete = tuple(value for value in values if value is not None)
        if any(value < 0 or value > 100 for value in concrete):
            raise PublicManifestError(
                "구성 표의 공개 비중 합계 항목이 0~100 범위를 벗어났습니다"
            )
        # 공개된 마지막 자리에서 각 행은 최대 반 단위만 반올림될 수 있다.
        # 행별 허용치를 합친 범위 안에서만 99.99/100.01 같은 표시 오차를 받는다.
        tolerance = sum(
            (
                Decimal(1).scaleb(
                    -len(value.partition(".")[2]) if "." in value else 0
                )
                / Decimal(2)
                for value in raw_values
            ),
            Decimal(0),
        )
        total = sum(concrete, Decimal(0))
        difference = abs(total - Decimal(100))
        if difference != 0 and difference >= tolerance:
            raise PublicManifestError(
                "구성 표의 공개 비중 합계가 표시 자릿수의 반올림 범위를 "
                f"벗어났습니다: {total} (허용 {tolerance})"
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


def _cell_kind(header: str, value: object) -> str:
    normalized = _normalized_header(header).casefold()
    if "%" in normalized or "비중" in normalized or "증감률" in normalized:
        if _decimal(value) is None:
            raise PublicManifestError(
                f"비율 머리글 {header!r}의 공개 셀이 숫자가 아닙니다"
            )
        if "%" in normalized and not str(value).strip().endswith("%"):
            raise PublicManifestError(
                f"% 머리글 {header!r}의 공개 셀에 % 단위가 없습니다"
            )
        return "percentage"
    return "number" if _decimal(value) is not None else "text"


def _typed_cell(
    *,
    header: str,
    column_index: int,
    public_value: object,
    source_field: str,
    source_value: object,
    values_equivalent: bool = True,
) -> dict[str, object]:
    if not source_field:
        raise PublicManifestError("typed cell의 원자료 필드가 비었습니다")
    kind = _cell_kind(header, public_value)
    if values_equivalent and kind in {"number", "percentage"}:
        if _decimal(public_value) != _decimal(source_value):
            raise PublicManifestError(
                f"머리글 {header!r}의 공개 숫자가 지정 원자료 필드와 다릅니다"
            )
    elif values_equivalent and _normalized_header(public_value) != _normalized_header(
        source_value
    ):
        raise PublicManifestError(
            f"머리글 {header!r}의 공개 셀이 지정 원자료 필드와 다릅니다"
        )
    return {
        "column_index": column_index,
        "header": _normalized_header(header),
        "kind": kind,
        "public_value_sha256": _sha256_text(str(public_value)),
        "source_field": source_field,
        "source_value_sha256": _sha256_text(str(source_value)),
    }


def _claim_field_for_cell(
    claim: StructuredClaim,
    header: str,
    value: object,
) -> tuple[str, object] | None:
    """머리글 의미가 허용하는 claim 필드 하나만 선택한다.

    행 전체 값의 집합만 비교하면 열을 맞바꿔도 통과한다. 머리글별 닫힌 필드
    후보와 실제 값을 함께 비교해 열 위치를 잠근다.
    """

    key = _normalized_header(header).casefold()
    if "%" in key or "비중" in key or "증감률" in key or "비율" in key:
        candidates = ("display_value",)
    elif "원시" in key or "원값" in key:
        candidates = ("raw_value",)
    elif "단위" in key:
        candidates = ("unit", "unit_dimension")
    elif "지표" in key or "항목" in key:
        candidates = ("metric", "subject_scope")
    elif "기간" in key or "연도" in key or "시점" in key:
        candidates = ("period_end", "period_start")
    elif any(
        token in key
        for token in ("제품", "사업", "지역", "구분", "대상", "범위", "명")
    ):
        candidates = ("subject_scope",)
    elif _decimal(value) is not None:
        candidates = ("display_value", "raw_value")
    else:
        candidates = ("metric", "subject_scope")
    matches: list[tuple[str, object]] = []
    for field_name in candidates:
        source_value = getattr(claim, field_name)
        if not str(source_value).strip():
            continue
        if _decimal(value) is not None:
            matched = _decimal(value) == _decimal(source_value)
        else:
            matched = _normalized_header(value) == _normalized_header(source_value)
        if matched:
            matches.append((field_name, source_value))
    return matches[0] if len(matches) == 1 else None


def _fact_typed_cells(
    headers: Sequence[str],
    row: Sequence[str],
    claim: StructuredClaim,
) -> list[dict[str, object]]:
    cells: list[dict[str, object]] = []
    for column_index, (header, public_value) in enumerate(zip(headers, row)):
        matched = _claim_field_for_cell(claim, header, public_value)
        if matched is None:
            raise PublicManifestError(
                f"injected fact가 {header!r} 열의 공개 셀에 typed 결속되지 않았습니다"
            )
        source_field, source_value = matched
        cells.append(
            _typed_cell(
                header=header,
                column_index=column_index,
                public_value=public_value,
                source_field=f"claim:{source_field}",
                source_value=source_value,
            )
        )
    return cells


def _evidence_typed_cells(
    headers: Sequence[str],
    row: Sequence[str],
    raw_row: Sequence[str] | None,
    evidence: str,
) -> list[dict[str, object]]:
    record = _matching_evidence_record(headers, evidence)
    cells: list[dict[str, object]] = []
    normalized_headers = tuple(_normalized_header(value) for value in headers)
    for column_index, (header, public_value) in enumerate(zip(headers, row)):
        source_value = (
            raw_row[column_index]
            if raw_row is not None
            else record[normalized_headers[column_index]] if record is not None else None
        )
        if source_value is None:
            raise PublicManifestError(
                f"원자료에서 {header!r} 열의 typed cell 값을 찾지 못했습니다"
            )
        cells.append(
            _typed_cell(
                header=header,
                column_index=column_index,
                public_value=public_value,
                source_field=f"evidence:{normalized_headers[column_index]}",
                source_value=source_value,
                values_equivalent=(raw_row is None or column_index == 0),
            )
        )
    return cells


def _validated_program_bindings(
    table: PerformanceTable,
    fragments: Mapping[str, _FragmentBinding],
    verified_claims: Mapping[str, StructuredClaim],
    *,
    fragment_texts: Mapping[str, str],
    require_source_row_provenance: bool = False,
) -> tuple[dict[str, object], ...]:
    width = len(table.headers)
    normalized_headers = tuple(_normalized_header(value) for value in table.headers)
    if (
        width == 0
        or not table.rows
        or any(not value for value in normalized_headers)
        or len(set(normalized_headers)) != len(normalized_headers)
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
    strict_table_identity = ""
    strict_row_count = sum(
        1
        for row in table.rows
        if row
        and not is_revenue_total_name(row[0])
        and re.sub(r"\s+", "", str(row[0])) != "소계"
    )
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
            typed_cells = _fact_typed_cells(table.headers, row, claim)
        else:
            if not evidence:
                raise PublicManifestError(
                    f"프로그램 표 {index + 1}번 행의 evidence_rows가 비었습니다"
                )
            raw_row = table.raw_rows[index] if table.raw_rows else None
            if require_source_row_provenance:
                evidence_matches = revenue_row_evidence_matches(
                    evidence,
                    cited_source_text=fragment_texts.get(source.fragment_id, ""),
                    headers=table.headers,
                    public_row=row,
                    raw_row=raw_row,
                    expected_selected_index=index,
                    expected_row_count=strict_row_count,
                )
                table_identity = revenue_table_evidence_identity(evidence)
                if not strict_table_identity:
                    strict_table_identity = table_identity
                evidence_matches = bool(
                    evidence_matches
                    and table_identity
                    and table_identity == strict_table_identity
                )
            else:
                evidence_matches = is_dart_table or _generic_evidence_matches_row(
                    table.headers,
                    row,
                    raw_row,
                    evidence,
                    scale_divisor=table.scale_divisor,
                    scale_places=table.scale_places,
                )
            if not evidence_matches:
                raise PublicManifestError(
                    f"프로그램 표 {index + 1}번 행을 인용 원문으로 재검산할 수 없습니다"
                )
            typed_cells = _evidence_typed_cells(
                table.headers,
                row,
                table.raw_rows[index] if table.raw_rows else None,
                evidence,
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
                "typed_cells": typed_cells,
            }
        )
    return tuple(bindings)


def _flow_binding(
    fragment_ids: Sequence[str],
    fragments: Mapping[str, _FragmentBinding],
    *,
    headers: Sequence[str],
    row: Sequence[str],
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
        "semantic_review": "bundled:true",
        "typed_cells": [
            _typed_cell(
                header=header,
                column_index=index,
                public_value=value,
                source_field=f"bundled-reviewed-flow-cell:{index}",
                source_value=value,
            )
            for index, (header, value) in enumerate(zip(headers, row))
        ],
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
    source_cites: Sequence[str],
    row_fact_ids: Sequence[str],
    row_bindings: Sequence[Mapping[str, object]],
) -> dict[str, object]:
    binding_payloads = [dict(value) for value in row_bindings]
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
        "source_cites": [str(value) for value in source_cites],
        "row_fact_ids": [str(value) for value in row_fact_ids],
        "row_evidence_refs": [
            str(value.get("row_evidence_hash") or "")
            for value in binding_payloads
        ],
        "row_binding_refs": [canonical_sha256(value) for value in binding_payloads],
        "cell_binding_refs": [
            [canonical_sha256(cell) for cell in value.get("typed_cells", [])]
            for value in binding_payloads
        ],
        "row_bindings": binding_payloads,
        "numeric_tokens": _numeric_tokens(rows),
    }


def _citation_numbers_for_fragments(
    fragments: Sequence[CollectedFragment],
) -> dict[str, int]:
    numbers: dict[str, int] = {}
    pending: list[str] = []
    for fragment in fragments:
        fragment_id = str(fragment.fragment_id)
        if fragment_id.isdigit() and int(fragment_id) > 0:
            numbers[fragment_id] = int(fragment_id)
        else:
            pending.append(fragment_id)
    next_number = max(numbers.values(), default=0) + 1
    for fragment_id in pending:
        numbers[fragment_id] = next_number
        next_number += 1
    return numbers


def _sentence_numbers(
    sentence: ComposedSentence, numbers: Mapping[str, int]
) -> tuple[int, ...]:
    out: list[int] = []
    for citation in sentence.citations:
        number = numbers.get(str(citation).strip())
        if number is not None and number not in out:
            out.append(number)
    return tuple(out)


def _marker_visibility_expected(
    sentences: Sequence[ComposedSentence],
    numbers: Mapping[str, int],
    citation_style: str,
) -> list[bool]:
    if citation_style != CITATION_STYLE_MERGED:
        return [True for _sentence in sentences]
    keys = [frozenset(_sentence_numbers(sentence, numbers)) for sentence in sentences]
    visible: list[bool] = []
    for index, sentence in enumerate(sentences):
        if sentence.grade == GRADE_INTERPRETED or not keys[index]:
            visible.append(False)
            continue
        following = next(
            (
                position
                for position in range(index + 1, len(sentences))
                if sentences[position].grade != GRADE_INTERPRETED
            ),
            None,
        )
        visible.append(not (following is not None and keys[following] == keys[index]))
    return visible


def _ensure_expected_visible_markers(
    groups: list[tuple[Sequence[ComposedSentence], list[bool]]],
    numbers: Mapping[str, int],
) -> None:
    visible: set[int] = set()
    last_seen: dict[int, tuple[int, int]] = {}
    for group_index, (sentences, shows) in enumerate(groups):
        for index, sentence in enumerate(sentences):
            cited = _sentence_numbers(sentence, numbers)
            if shows[index]:
                visible.update(cited)
            for number in cited:
                last_seen[number] = (group_index, index)
    for number, (group_index, index) in last_seen.items():
        if number not in visible:
            groups[group_index][1][index] = True


def _expected_display_text(
    sentence: ComposedSentence,
    numbers: Mapping[str, int],
    *,
    show_markers: bool,
) -> str:
    text = " ".join(sentence.text.split())
    if show_markers:
        markers = "".join(
            f"[{number}]" for number in _sentence_numbers(sentence, numbers)
        )
        if markers:
            text = f"{text} {markers}"
    if sentence.grade == GRADE_INTERPRETED:
        text += _INTERPRETATION_MARKER
    return text


def _expected_paragraph_starts(
    sentences: Sequence[ComposedSentence], numbers: Mapping[str, int]
) -> tuple[int, ...]:
    if not sentences:
        return ()
    starts = [0]
    current_key: frozenset[int] | None = None
    length = 0
    for index, sentence in enumerate(sentences):
        key = frozenset(_sentence_numbers(sentence, numbers))
        interpreted = sentence.grade == GRADE_INTERPRETED
        if index == 0:
            current_key = key
            length = 1
            continue
        changed = bool(key) and not interpreted and key != current_key
        if changed or length >= PARAGRAPH_MAX_SENTENCES:
            starts.append(index)
            length = 1
            if key:
                current_key = key
            continue
        if key and not interpreted:
            current_key = key
        length += 1
    return tuple(starts)


def _expected_source_label(
    fragment: CollectedFragment, filing_meta: FilingMeta | None
) -> str:
    if fragment.document_title:
        return fragment.document_title
    if not fragment.kind:
        return _SOURCE_LABEL_FALLBACK
    if fragment.source_url:
        return fragment.kind
    if filing_meta is not None and filing_meta.title:
        return f"{filing_meta.title} · {fragment.kind}"
    return f"{_FILING_LABEL_PREFIX} {fragment.kind}"


def _expected_source(
    fragment: CollectedFragment,
    *,
    number: int,
    company_name: str,
    used_in: Sequence[str],
    filing_meta: FilingMeta | None,
) -> Source:
    normalized_hash = evidence_text_hash(fragment.text)
    exact_hash = exact_evidence_text_hash(fragment.text)
    evidence_hashes = [normalized_hash] if normalized_hash else []
    exact_hashes = [exact_hash] if exact_hash else []
    if fragment.bound_source is not None:
        source = fragment.bound_source
        if (
            type(source) is not Source
            or not has_valid_provenance_seal(source)
            or source.number != number
            or str(source.number) != fragment.fragment_id
            or exact_hash not in source.exact_evidence_hashes
            or document_identity(source) != fragment.document_identity
            or (
                bool(fragment.document_content_sha256)
                and source.document_content_sha256
                != fragment.document_content_sha256
            )
        ):
            raise PublicManifestError(
                "프로그램 비교 조각과 봉인 Source 결속이 깨졌습니다"
            )
        # used_in은 FactRecord에서 나중에 계산하는 출고 투영이며
        # 수집 봉인 payload가 아니다. 그 필드만의 변경은 재봉인하지 않는다.
        return replace(source, used_in=list(dict.fromkeys(used_in)))
    # renderer와 같은 shared identity 규칙으로 formal DART 문서를 먼저
    # 판별한다. URL 유무만 보면 DART도 일반 웹 Source로 바뀌어 pre-render
    # 봉인과 실제 공개 부록의 문서 신원이 갈라진다.
    bound_identity = bind_declared_document_identity_to_url(
        fragment.document_identity,
        fragment.source_url,
    )
    identity_host, identity_document_id = document_identity_components(
        bound_identity
    )
    formal_web = formal_web_public_source_metadata(
        source_kind=fragment.formal_source_kind,
        source_url=fragment.source_url,
        company_name=company_name,
        identity_binding=fragment.identity_binding,
        domain_attestation_source_id=fragment.domain_attestation_source_id,
        domain_attestation_evidence=fragment.domain_attestation_evidence,
        reporting_period=fragment.reporting_period,
        attachment_url=fragment.attachment_url,
        ir_metadata_verification=fragment.ir_metadata_verification,
        domain_redirect_verification=fragment.domain_redirect_verification,
        domain_redirect_from_host=fragment.domain_redirect_from_host,
        domain_redirect_to_host=fragment.domain_redirect_to_host,
    )
    if identity_host and identity_document_id:
        source = Source(
            number=number,
            kind=SourceKind.FILING,
            label=_expected_source_label(fragment, filing_meta),
            disclosed_at=fragment.document_date,
            collected_at=fragment.source_collected_on,
            source_id=f"{_SOURCE_ID_PREFIX}{fragment.fragment_id}",
            title=fragment.document_title,
            # 공시 내용에 책임지는 발행자는 분석 대상 법인이고 DART는
            # 공개 위치(host)다. typed 수집 문서가 보존한 원자료 발행처와
            # 독자에게 보여 주는 Source의 책임 주체를 섞지 않는다.
            publisher=company_name,
            host=identity_host,
            url=fragment.source_url,
            document_id=identity_document_id,
            location=fragment.location or fragment.kind,
            source_type="공식 공시",
            fact_status="공시 실제값",
            used_in=list(used_in),
            evidence_hashes=evidence_hashes,
            exact_evidence_hashes=exact_hashes,
            formal_source_kind=fragment.formal_source_kind,
            identity_binding=fragment.identity_binding,
        )
    elif fragment.text.startswith(DART_FINANCIAL_API_PREFIX):
        source = Source(
            number=number,
            kind=SourceKind.FILING,
            label=DART_FINANCIAL_API_LABEL,
            collected_at=fragment.document_date,
            source_id=f"{_SOURCE_ID_PREFIX}{fragment.fragment_id}",
            title=DART_FINANCIAL_API_LABEL,
            # API 운영 주체가 아니라 이 재무 수치를 공시한 법인을 표시한다.
            publisher=company_name,
            host=DART_FINANCIAL_API_HOST,
            url=DART_FINANCIAL_API_URL,
            document_id=DART_FINANCIAL_API_DOCUMENT_ID,
            location="주요계정 API 응답",
            source_type="공식 재무 API",
            fact_status="공시 실제값",
            used_in=list(used_in),
            evidence_hashes=evidence_hashes,
            exact_evidence_hashes=exact_hashes,
        )
    elif formal_web is not None:
        source = Source(
            number=number,
            kind=SourceKind.OTHER,
            label=_expected_source_label(fragment, filing_meta),
            collected_at=fragment.source_collected_on,
            published_at=fragment.document_date,
            source_id=f"{_SOURCE_ID_PREFIX}{fragment.fragment_id}",
            title=fragment.document_title,
            publisher=company_name,
            host=formal_web.host,
            url=fragment.source_url,
            document_id=fragment.source_document_id,
            location=fragment.location,
            source_type=formal_web.source_type,
            fact_status=(
                "공식 발행일·보고기간 확정"
                if formal_web.source_type == "회사 공식 IR"
                and fragment.document_date
                else "기준일 현재 확인"
            ),
            used_in=list(used_in),
            evidence_hashes=evidence_hashes,
            exact_evidence_hashes=exact_hashes,
            formal_source_kind=formal_web.formal_source_kind,
            identity_binding=formal_web.identity_binding,
            domain_attestation_source_id=formal_web.domain_attestation_source_id,
            domain_attestation_evidence=formal_web.domain_attestation_evidence,
            reporting_period=formal_web.reporting_period,
            attachment_url=formal_web.attachment_url,
            ir_metadata_verification=formal_web.ir_metadata_verification,
            domain_redirect_verification=formal_web.domain_redirect_verification,
            domain_redirect_from_host=formal_web.domain_redirect_from_host,
            domain_redirect_to_host=formal_web.domain_redirect_to_host,
        )
    elif fragment.formal_source_kind:
        raise PublicManifestError(
            "typed 공식 웹의 자료종류·URL·회사 proof가 일치하지 않습니다"
        )
    elif fragment.source_url:
        source = Source(
            number=number,
            kind=SourceKind.OTHER,
            label=_expected_source_label(fragment, filing_meta),
            collected_at=fragment.document_date,
            source_id=f"{_SOURCE_ID_PREFIX}{fragment.fragment_id}",
            title=fragment.document_title,
            publisher=company_name,
            url=fragment.source_url,
            location=fragment.location,
            used_in=list(used_in),
            evidence_hashes=evidence_hashes,
            exact_evidence_hashes=exact_hashes,
        )
    else:
        document_id = filing_meta.document_id if filing_meta is not None else ""
        source = Source(
            number=number,
            kind=SourceKind.FILING,
            label=_expected_source_label(fragment, filing_meta),
            disclosed_at=filing_meta.disclosed_at if filing_meta is not None else "",
            collected_at=fragment.document_date,
            source_id=f"{_SOURCE_ID_PREFIX}{fragment.fragment_id}",
            title=filing_meta.title if filing_meta is not None else "",
            publisher=company_name,
            host=DART_DOCUMENT_HOST if document_id else "",
            url=(
                DART_DOCUMENT_URL_TEMPLATE.format(document_id=document_id)
                if document_id
                else ""
            ),
            document_id=document_id,
            location=fragment.location or fragment.kind,
            used_in=list(used_in),
            evidence_hashes=evidence_hashes,
            exact_evidence_hashes=exact_hashes,
        )
    # renderer와 독립적으로 예상 출처를 만들되, 두 경로 모두
    # 최종 공개 Source를 같은 불변 봉인 규칙에 넣어야 저장 후 검사가
    # 특정 종류의 출처만 누락된 혼합 보고서를 막을 수 있다.
    # renderer와 같은 단일 projection을 써야 예상 manifest와 실제 공개 Source가
    # 출처 종류가 늘어난 뒤에도 문서 전체 지문에서 갈라지지 않는다.
    source = bind_document_content_sha256(
        source,
        fragment.document_content_sha256,
    )
    sealed = seal_collected_source(source)
    if fragment.formal_source_kind:
        if (
            not sealed.is_canonical_valid
            or not has_valid_provenance_seal(sealed)
            or document_identity(sealed) != fragment.document_identity
        ):
            raise PublicManifestError(
                "FULL typed 출처의 공개 신원·필수 필드·도장이 손상됐습니다"
            )
        if (
            fragment.formal_source_kind
            == SOURCE_KIND_OFFICIAL_IDENTITY_VERIFIED_WEB_PAGE
            and not is_canonical_official_with_registry(sealed, [sealed])
        ):
            raise PublicManifestError(
                "DART sidecar 공식 웹 출처의 공식성 proof가 손상됐습니다"
            )
    return sealed


def _public_table_from_manifest(table: Mapping[str, object]) -> dict[str, object]:
    return {
        "caption": str(table.get("caption") or ""),
        "headers": [str(value) for value in table.get("headers", [])],
        "rows": [
            [str(cell) for cell in row] for row in table.get("rows", [])
        ],
        "cite": str(table.get("cite") or ""),
        "numeric": bool(table.get("numeric", False)),
        "presentation": str(table.get("presentation") or "table"),
        "display_unit": str(table.get("display_unit") or ""),
    }


def _expected_public_content_projection(
    report: ComposedReport,
    fragments: Sequence[CollectedFragment],
    tables: Sequence[Mapping[str, object]],
    *,
    company_name: str,
    company_id: str,
    corp_type: str,
    generated_at: str,
    as_of_date: str,
    analysis_period: str,
    latest_performance_period: str,
    citation_style: str,
    filing_meta: FilingMeta | None,
    program_registry_sources: Sequence[Source] = (),
) -> dict[str, object]:
    numbers = _citation_numbers_for_fragments(fragments)
    groups = [
        (
            section.sentences,
            _marker_visibility_expected(
                section.sentences, numbers, citation_style
            ),
        )
        for section in report.sections
    ]
    groups.append(
        (
            report.summary,
            _marker_visibility_expected(report.summary, numbers, citation_style),
        )
    )
    _ensure_expected_visible_markers(groups, numbers)
    used_sections: dict[int, list[str]] = {}
    public_sections: list[dict[str, object]] = []
    for section_index, section in enumerate(report.sections):
        shows = groups[section_index][1]
        displays = [
            _expected_display_text(sentence, numbers, show_markers=shows[index])
            for index, sentence in enumerate(section.sentences)
        ]
        lines = ([[section.notice, ""]] if section.notice else []) + [
            [display, ""] for display in displays
        ]
        starts = set(_expected_paragraph_starts(section.sentences, numbers))
        paragraphs: list[str] = []
        buffer: list[str] = []
        for index, display in enumerate(displays):
            if index in starts and buffer:
                paragraphs.append(" ".join(buffer))
                buffer = []
            buffer.append(display)
        if buffer:
            paragraphs.append(" ".join(buffer))
        if section.notice:
            paragraphs.insert(0, section.notice)
        for sentence in section.sentences:
            for number in _sentence_numbers(sentence, numbers):
                owners = used_sections.setdefault(number, [])
                if section.section_id not in owners:
                    owners.append(section.section_id)
        section_tables = [
            _public_table_from_manifest(table)
            for table in tables
            if table.get("section_id") == section.section_id
        ]
        for table in tables:
            if table.get("section_id") != section.section_id:
                continue
            for raw_cite in table.get("source_cites", []):
                number_text = citation_number(str(raw_cite))
                if number_text:
                    owners = used_sections.setdefault(int(number_text), [])
                    if section.section_id not in owners:
                        owners.append(section.section_id)
        public_sections.append(
            {
                "cell": section.section_id,
                "title": SECTION_TITLES.get(section.section_id, section.section_id),
                "empty_reason": "",
                "prose_lines": lines,
                "prose_paragraphs": paragraphs,
                "guidance_lines": [],
                "display_number": str(section_index + 1),
                "tag": _SECTION_TAGS.get(section.section_id, ""),
                "tables": section_tables,
            }
        )
    body_owner_matches: dict[
        tuple[str, tuple[str, ...], str], list[str]
    ] = {}
    for section in report.sections:
        for sentence in section.sentences:
            key = (sentence.text, sentence.citations, sentence.planned_claim_slot)
            body_owner_matches.setdefault(key, []).append(section.section_id)
    summary_items: list[dict[str, object]] = []
    summary_shows = groups[-1][1]
    for index, sentence in enumerate(report.summary):
        key = (sentence.text, sentence.citations, sentence.planned_claim_slot)
        owners = body_owner_matches.get(key, [])
        if len(owners) != 1:
            raise PublicManifestError("추출식 요약의 본문 소유 장을 하나로 정할 수 없습니다")
        summary_items.append(
            {
                "text": _expected_display_text(
                    sentence,
                    numbers,
                    show_markers=summary_shows[index],
                ),
                "section_id": owners[0],
            }
        )
        for number in _sentence_numbers(sentence, numbers):
            used_sections.setdefault(number, [])
    fragment_by_number = {
        numbers[fragment.fragment_id]: fragment for fragment in fragments
    }
    citations = [
        _expected_source(
            fragment_by_number[number],
            number=number,
            company_name=company_name,
            used_in=used_sections[number],
            filing_meta=filing_meta,
        )
        for number in sorted(used_sections)
        if number in fragment_by_number
    ]
    citations_by_id = {source.source_id: source for source in citations}
    used_numbers = {source.number for source in citations}
    for source in program_registry_sources:
        if type(source) is not Source or not source.source_id:
            raise PublicManifestError("프로그램 Source 등록부 형식이 올바르지 않습니다")
        previous = citations_by_id.get(source.source_id)
        if previous is not None:
            if previous != source:
                raise PublicManifestError("공개 비교 Source가 packet 등록부와 다릅니다")
            continue
        if source.number in used_numbers:
            raise PublicManifestError("프로그램 Source 번호가 공개 부록과 충돌합니다")
        citations.append(source)
        citations_by_id[source.source_id] = source
        used_numbers.add(source.number)
    try:
        complete_registry = ensure_dart_profile_attesters(
            citations,
            company_name=company_name,
        )
    except ValueError as exc:
        raise PublicManifestError(str(exc)) from exc
    citations = sorted(complete_registry, key=lambda source: source.number)
    complete_registry = tuple(citations)
    for source in complete_registry:
        if problem := full_typed_source_registry_problem(
            source,
            complete_registry,
            reference_date=as_of_date,
        ):
            raise PublicManifestError(
                f"FULL typed 공개 출처 계약 위반: {problem}"
            )
    from src.shared.report_generation.models import canonical_value  # noqa: PLC0415

    return {
        "version": 1,
        "company": company_name,
        "company_id": company_id,
        "job": "",
        "corp_type": corp_type,
        "generated_at": generated_at,
        "schema_version": ENGINE_V2_SCHEMA_VERSION,
        "as_of_date": as_of_date,
        "analysis_period": analysis_period,
        "latest_performance_period": latest_performance_period,
        "grade": QualityGrade.COMPLETE.value,
        "shortfall_reasons": [],
        "quality_contract_version": STRICT_QUALITY_CONTRACT_VERSION,
        "safety_decision": ReleaseDecision.RELEASE_ALLOWED.value,
        "publication_policy": PublicationPolicy.STRUCTURED_SAFETY.value,
        "sections": public_sections,
        "summary_items": summary_items,
        "citations": [canonical_value(source) for source in citations],
    }


def build_public_structure_seal(
    report: ComposedReport,
    fragments: FragmentsInput,
    performance_table: PerformanceTable | None,
    *,
    filing_meta: FilingMeta | None,
    composition_tables: tuple[PerformanceTable, ...],
    table_presentation: str,
    company_id: str,
    evidence_generation_sha256: str,
    evidence_packet_sha256s: tuple[tuple[str, str], ...],
    company_name: str,
    corp_type: str,
    generated_at: str,
    as_of_date: str,
    analysis_period: str,
    latest_performance_period: str,
    citation_style: str,
    program_registry_sources: Sequence[Source] = (),
) -> PublicStructureSeal:
    """검증된 pre-render 입력만으로 공개 표·flow 정본을 만든다."""

    normalized_fragments = _normalize_fragments(fragments)
    fragment_bindings = {
        fragment.fragment_id: _fragment_binding(fragment, filing_meta)
        for fragment in normalized_fragments
    }
    fragment_texts = {
        fragment.fragment_id: str(fragment.text) for fragment in normalized_fragments
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
                public_row = [
                    (str(cell).strip() or FLOW_UNCONFIRMED_CELL)
                    if section.section_id in FLOW_ARROW_SECTION_IDS
                    else str(cell).strip()
                    for cell in row.cells
                ]
                binding = _flow_binding(
                    row.citations,
                    fragment_bindings,
                    headers=FLOW_HEADERS_BY_SECTION[section.section_id],
                    row=public_row,
                )
                rows.append(public_row)
                row_bindings.append(binding)
            source_ids = tuple(
                fragment_id
                for binding in row_bindings
                for fragment_id in binding["source_fragment_ids"]
            )
            source_cites = _normalized_source_cites(source_ids)
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
                table,
                fragment_bindings,
                verified_claims,
                fragment_texts=fragment_texts,
                require_source_row_provenance=(
                    presentation == _COMPOSITION_PRESENTATION
                ),
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
    if not re.fullmatch(r"[0-9]{8}", str(company_id).strip()):
        raise PublicManifestError("manifest company_id는 gen8이어야 합니다")
    if _HEX_64_RE.fullmatch(str(evidence_generation_sha256).strip()) is None:
        raise PublicManifestError("manifest evidence generation 지문이 없습니다")
    if (
        tuple(section_id for section_id, _digest in evidence_packet_sha256s)
        != SECTION_IDS
        or any(_HEX_64_RE.fullmatch(str(digest)) is None for _sid, digest in evidence_packet_sha256s)
    ):
        raise PublicManifestError("manifest에는 정책 순서 아홉 packet 지문이 필요합니다")
    unsigned = {
        "version": PUBLIC_STRUCTURE_MANIFEST_VERSION,
        "company_id": str(company_id).strip(),
        "evidence_generation_sha256": str(evidence_generation_sha256).strip(),
        "evidence_packet_sha256s": [
            [section_id, digest] for section_id, digest in evidence_packet_sha256s
        ],
        "sections": list(section_ids),
        "tables": tables,
    }
    manifest = {**unsigned, "digest": canonical_sha256(unsigned)}
    normalized_fragments = _normalize_fragments(fragments)
    public_content = _expected_public_content_projection(
        report,
        normalized_fragments,
        tables,
        company_name=company_name,
        company_id=str(company_id).strip(),
        corp_type=corp_type,
        generated_at=generated_at,
        as_of_date=as_of_date,
        analysis_period=analysis_period,
        latest_performance_period=latest_performance_period,
        citation_style=citation_style,
        filing_meta=filing_meta,
        program_registry_sources=program_registry_sources,
    )
    public_sections = public_content["sections"]
    section_sha256s = tuple(
        (str(section["cell"]), canonical_sha256(section))
        for section in public_sections
        if isinstance(section, Mapping)
    )
    return PublicStructureSeal(
        canonical_json=_canonical_json(manifest),
        table_refs=tuple(refs),
        public_content_sha256=canonical_sha256(public_content),
        section_sha256s=section_sha256s,
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
    _assert_report_matches_manifest(
        report_verification_payload(report), seal.canonical_json
    )


def assert_stored_strict_manifest(report: Report) -> None:
    """strict JSON 재로드 뒤 manifest와 모든 표 참조가 남았는지 확인한다."""

    expected_sha256 = (
        report.generation_evidence.public_manifest_sha256
        if report.generation_evidence is not None
        else ""
    )
    _assert_report_matches_manifest(
        report_verification_payload(report),
        report.public_structure_manifest,
        expected_manifest_sha256=expected_sha256,
    )


__all__ = [
    "PUBLIC_STRUCTURE_MANIFEST_VERSION",
    "PublicManifestError",
    "PublicStructureSeal",
    "assert_report_matches_public_structure",
    "assert_stored_strict_manifest",
    "build_public_structure_seal",
]
