"""보완조사 경로에만 적용할 순수 최종 출고 guard.

호출자는 기존 composer 안전·의미 검수를 먼저 끝내야 한다. 이 helper는 그
검수를 대체하거나 SHADOW 관측값을 재해석하지 않고, 최종 ``Report`` DTO와
같은 실행의 typed 공식 수집 결과를 대조해 추가 출고 하한만 확인한다.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.core.citations import (
    citation_number,
    split_citation_markers,
    split_interpretation_marker,
)
from src.features.pipeline.port import FactRecord, Report, ReportSection, ReportTable
from src.features.pipeline.supplementary_fact_binding import (
    bound_supplementary_fact_sources,
)
from src.features.pipeline.supplementary_research_release_constants import (
    DART_FORMAL_SOURCE_KINDS,
    MINIMUM_SUPPLEMENTARY_BODY_SECTION_COUNT,
    SUPPLEMENTARY_CORE_OFFICIAL_SECTION_IDS,
    SUPPLEMENTARY_RELEASE_ALLOWED,
    SUPPLEMENTARY_RELEASE_BUSINESS_MODEL_WITHOUT_OFFICIAL_BODY,
    SUPPLEMENTARY_RELEASE_IDENTITY_WITHOUT_OFFICIAL_BODY,
    SUPPLEMENTARY_RELEASE_INSUFFICIENT_BODY_SECTIONS,
    SUPPLEMENTARY_RELEASE_INVALID_CITATION_REGISTRY,
    SUPPLEMENTARY_RELEASE_INVALID_REPORT_DTO,
    SUPPLEMENTARY_RELEASE_WITHOUT_DART_BODY,
)
from src.shared.report_evidence.constants import (
    OFFICIAL_WEB_SOURCE_KINDS,
)
from src.shared.report_evidence.policy import REQUIRED_EVIDENCE_SECTION_IDS
from src.shared.report_evidence.runtime_port import OfficialEvidenceCollectionResult
from src.shared.report_evidence.source_verification import (
    SourceVerification,
    SourceVerifier,
)
from src.shared.report_quality.source_identity import (
    collected_document_identity,
)


@dataclass(frozen=True)
class SupplementaryResearchReleaseDecision:
    """출고 허용 여부와 감사 가능한 닫힌 판정."""

    allowed: bool
    code: str
    qualified_section_ids: tuple[str, ...] = ()
    official_grounded_core_section_ids: tuple[str, ...] = ()
    dart_grounded_section_ids: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return self.allowed


@dataclass(frozen=True)
class _CollectedDocument:
    document_identity: str
    document_content_sha256: str
    source_kind: str
    exact_evidence_hashes: frozenset[str]


def _normalized(value: object) -> str:
    return " ".join(str(value or "").split()).casefold()


def _collected_documents(
    official_evidence: OfficialEvidenceCollectionResult,
) -> dict[str, _CollectedDocument] | None:
    """같은 회사의 실제 typed 수집 문서를 안정 신원으로 한 번만 묶는다."""

    by_identity: dict[str, _CollectedDocument] = {}
    for candidate in official_evidence.candidates:
        for document in candidate.documents:
            identity = collected_document_identity(
                source_kind=document.source_kind,
                document_id=document.document_id,
                url=document.canonical_url,
            )
            if not identity:
                return None
            item = _CollectedDocument(
                document_identity=identity,
                document_content_sha256=document.content_sha256,
                source_kind=document.source_kind,
                exact_evidence_hashes=frozenset(document.exact_evidence_hashes),
            )
            previous = by_identity.setdefault(identity, item)
            if previous != item:
                return None
    return by_identity


def _citation_registry(
    report: Report,
    verifier: SourceVerifier,
) -> tuple[
    tuple[object, ...],
    dict[str, tuple[object, SourceVerification]],
    dict[str, tuple[object, SourceVerification]],
] | None:
    """기존 정본 검증을 통과한 출처만 ID·번호로 모호함 없이 색인한 등록부.

    등록부에는 typed 공식 출처만 있는 것이 아니다. 같은 보고서가 v2 legacy
    조각 출처(공시 원문 조각·재무 API 응답)도 함께 싣는데, 그 출처들은 v3
    출처표의 필수 필드(``source_type``·``fact_status``·발행일)가 없어
    ``is_canonical_valid``가 거짓이고 검증자가 ``None``을 돌려준다. 그것은
    「이 회사 자료가 깨졌다」가 아니라 「본문 근거로 셀 수 없는 출처다」라는
    뜻이므로 색인에서만 뺀다. 전체를 거절하면 legacy 조각이 한 줄만 있어도
    내부 계약 오류가 되어, legacy 조각이 늘 섞이는 보완조사 경로가 자료
    유무와 무관하게 항상 막힌다.

    색인에서 빠진 출처는 아래 본문 판정에서 «없는 번호»가 되어 그 번호를 쓴
    문장·표 행이 통째로 제외된다. 판정은 좁아질 뿐 넓어지지 않는다. 등록부
    자체가 모호하거나(중복 ID·번호) 검증자가 형식이 깨진 값을 돌려주거나
    한 줄도 통과하지 못하면 예전처럼 그대로 닫는다.
    """

    if type(report.citations) is not list or not report.citations:
        return None
    registry = tuple(report.citations)
    by_id: dict[str, tuple[object, SourceVerification]] = {}
    by_number: dict[str, tuple[object, SourceVerification]] = {}
    for source in registry:
        verified = verifier(
            source,
            registry,
            reference_date=report.as_of_date,
            evidence_text="",
        )
        if verified is None:
            continue
        if (
            type(verified) is not SourceVerification
            or not verified.source_id.strip()
            or type(verified.number) is not int
            or verified.number <= 0
            or not verified.document_identity.strip()
            or verified.source_id in by_id
            or str(verified.number) in by_number
        ):
            return None
        item = (source, verified)
        by_id[verified.source_id] = item
        by_number[str(verified.number)] = item
    if not by_id:
        return None
    return registry, by_id, by_number


def _row_citation_numbers(table: ReportTable, row_index: int) -> set[str]:
    raw_cites: list[str] = []
    if row_index < len(table.row_cites):
        raw_cites.extend(str(value) for value in table.row_cites[row_index])
    if not raw_cites:
        raw_cites.extend(str(value) for value in table.source_cites)
        raw_cites.append(str(table.cite or ""))
    return {
        number
        for raw in raw_cites
        if (number := citation_number(raw))
    }


def _visible_fact_ids(
    section: ReportSection,
    facts: dict[str, FactRecord],
    source_by_number: dict[
        str, tuple[object, SourceVerification]
    ],
) -> tuple[tuple[str, bool], ...]:
    """실제 공개 본문에 대응한 ``(fact_id, 뉴스 허용 여부)``.

    뉴스는 검증 산문에서만 장을 채운다. 표는 역할 필드가 없어 뉴스 목록과
    검증 본문 표를 구분할 수 없으므로 공식 직접 근거만 허용한다.
    """

    candidates = [
        facts[fact_id]
        for fact_id in section.fact_ids
        if fact_id in facts and facts[fact_id].section_owner == section.cell
    ]
    visible: list[tuple[str, bool]] = []

    def add_exact(
        texts: tuple[str, ...], numbers: set[str], *, news_allowed: bool
    ) -> None:
        if not numbers or not numbers <= set(source_by_number):
            return
        source_ids = {source_by_number[number][1].source_id for number in numbers}
        normalized = {_normalized(text) for text in texts if _normalized(text)}
        matches = [
            fact
            for fact in candidates
            if fact.fact_id not in {fact_id for fact_id, _allowed in visible}
            and _normalized(fact.claim) in normalized
            and set(fact.supporting_source_ids) == source_ids
        ]
        if len(matches) == 1:
            visible.append((matches[0].fact_id, news_allowed))

    for text, cite in section.prose_lines:
        body, _interpreted = split_interpretation_marker(str(text))
        parts = split_citation_markers(body)
        inline_numbers = {
            str(part.number) for part in parts if part.number > 0
        }
        legacy_number = citation_number(cite)
        if inline_numbers and legacy_number and inline_numbers != {legacy_number}:
            continue
        numbers = inline_numbers or ({legacy_number} if legacy_number else set())
        claim_text = "".join(part.text for part in parts)
        if claim_text.strip():
            add_exact((claim_text,), numbers, news_allowed=True)

    for table in section.tables:
        if type(table) is not ReportTable or not table.is_valid:
            continue
        for row_index, row in enumerate(table.rows):
            cells = tuple(str(cell).strip() for cell in row)
            if not any(cells):
                continue
            numbers = _row_citation_numbers(table, row_index)
            if not numbers or not numbers <= set(source_by_number):
                continue
            add_exact(
                (
                    *cells,
                    " ".join(cells),
                    " | ".join(cells),
                    f"{table.caption}: " + " | ".join(cells),
                ),
                numbers,
                news_allowed=False,
            )
    return tuple(visible)


def _collection_matches_source(
    source: SourceVerification,
    collected: dict[str, _CollectedDocument],
) -> bool:
    document = collected.get(source.document_identity)
    return bool(
        document is not None
        and source.official
        and source.formal_kind == document.source_kind
        and source.content_sha256 == document.document_content_sha256
        and frozenset(source.exact_evidence_hashes) <= document.exact_evidence_hashes
    )


def _source_can_ground_body(
    source: SourceVerification,
    collected: dict[str, _CollectedDocument],
) -> bool:
    """공식 수집과 재대조된 원문 또는 봉인 검증된 뉴스만 허용한다."""

    if source.news:
        return not source.formal_kind
    return _collection_matches_source(source, collected)


def _source_can_ground_core(
    source: SourceVerification,
    collected: dict[str, _CollectedDocument],
) -> bool:
    return bool(
        _collection_matches_source(source, collected)
        and source.formal_kind
        in (DART_FORMAL_SOURCE_KINDS | OFFICIAL_WEB_SOURCE_KINDS)
    )


def _assess(
    report: Report,
    *,
    official_evidence: OfficialEvidenceCollectionResult,
    source_verifier: SourceVerifier,
) -> SupplementaryResearchReleaseDecision:
    if type(official_evidence) is not OfficialEvidenceCollectionResult:
        return SupplementaryResearchReleaseDecision(
            False, SUPPLEMENTARY_RELEASE_INVALID_REPORT_DTO
        )
    if (
        not report.company_id.strip()
        or report.company_id.strip() != official_evidence.company_id
    ):
        return SupplementaryResearchReleaseDecision(
            False, SUPPLEMENTARY_RELEASE_INVALID_REPORT_DTO
        )
    collected = _collected_documents(official_evidence)
    registry_result = _citation_registry(report, source_verifier)
    if collected is None or registry_result is None:
        return SupplementaryResearchReleaseDecision(
            False, SUPPLEMENTARY_RELEASE_INVALID_CITATION_REGISTRY
        )
    registry, _source_by_id, source_by_number = registry_result

    if type(report.fact_records) is not list or type(report.sections) is not list:
        return SupplementaryResearchReleaseDecision(
            False, SUPPLEMENTARY_RELEASE_INVALID_REPORT_DTO
        )
    facts: dict[str, FactRecord] = {}
    for fact in report.fact_records:
        if type(fact) is not FactRecord:
            return SupplementaryResearchReleaseDecision(
                False, SUPPLEMENTARY_RELEASE_INVALID_REPORT_DTO
            )
        fact_id = str(fact.fact_id or "").strip()
        if not fact_id or fact_id in facts:
            return SupplementaryResearchReleaseDecision(
                False, SUPPLEMENTARY_RELEASE_INVALID_REPORT_DTO
            )
        facts[fact_id] = fact

    qualified: list[str] = []
    official_core: list[str] = []
    dart_grounded: list[str] = []
    seen_sections: set[str] = set()
    canonical = set(REQUIRED_EVIDENCE_SECTION_IDS)
    for section in report.sections:
        if type(section) is not ReportSection:
            return SupplementaryResearchReleaseDecision(
                False, SUPPLEMENTARY_RELEASE_INVALID_REPORT_DTO
            )
        if section.cell not in canonical:
            continue
        if section.cell in seen_sections:
            return SupplementaryResearchReleaseDecision(
                False, SUPPLEMENTARY_RELEASE_INVALID_REPORT_DTO
            )
        seen_sections.add(section.cell)
        visible_ids = _visible_fact_ids(section, facts, source_by_number)
        bound_sources: list[SourceVerification] = []
        for fact_id, news_allowed in visible_ids:
            fact = facts[fact_id]
            bindings = bound_supplementary_fact_sources(
                fact,
                registry=registry,
                source_verifier=source_verifier,
                reference_date=report.as_of_date,
            )
            if (
                not bindings
                or (not news_allowed and any(source.news for source in bindings))
                or not all(
                    _source_can_ground_body(source, collected)
                    for source in bindings
                )
            ):
                continue
            bound_sources.extend(bindings)
        if not bound_sources:
            continue
        qualified.append(section.cell)
        if section.cell in SUPPLEMENTARY_CORE_OFFICIAL_SECTION_IDS and any(
            _source_can_ground_core(source, collected) for source in bound_sources
        ):
            official_core.append(section.cell)
        if any(
            _collection_matches_source(source, collected)
            and source.formal_kind in DART_FORMAL_SOURCE_KINDS
            for source in bound_sources
        ):
            dart_grounded.append(section.cell)

    qualified_ids = tuple(
        section_id
        for section_id in REQUIRED_EVIDENCE_SECTION_IDS
        if section_id in qualified
    )
    core_ids = tuple(
        section_id
        for section_id in SUPPLEMENTARY_CORE_OFFICIAL_SECTION_IDS
        if section_id in official_core
    )
    dart_ids = tuple(
        section_id
        for section_id in REQUIRED_EVIDENCE_SECTION_IDS
        if section_id in dart_grounded
    )
    decision_fields = (qualified_ids, core_ids, dart_ids)
    if len(qualified_ids) < MINIMUM_SUPPLEMENTARY_BODY_SECTION_COUNT:
        return SupplementaryResearchReleaseDecision(
            False,
            SUPPLEMENTARY_RELEASE_INSUFFICIENT_BODY_SECTIONS,
            *decision_fields,
        )
    if "identity" not in core_ids:
        return SupplementaryResearchReleaseDecision(
            False,
            SUPPLEMENTARY_RELEASE_IDENTITY_WITHOUT_OFFICIAL_BODY,
            *decision_fields,
        )
    if "business_model" not in core_ids:
        return SupplementaryResearchReleaseDecision(
            False,
            SUPPLEMENTARY_RELEASE_BUSINESS_MODEL_WITHOUT_OFFICIAL_BODY,
            *decision_fields,
        )
    if not dart_ids:
        return SupplementaryResearchReleaseDecision(
            False,
            SUPPLEMENTARY_RELEASE_WITHOUT_DART_BODY,
            *decision_fields,
        )
    return SupplementaryResearchReleaseDecision(
        True,
        SUPPLEMENTARY_RELEASE_ALLOWED,
        *decision_fields,
    )


def assess_supplementary_research_release(
    report: object,
    *,
    official_evidence: OfficialEvidenceCollectionResult | None,
    source_verifier: SourceVerifier,
) -> SupplementaryResearchReleaseDecision:
    """보완조사 Report의 실제 본문·공식 context 하한을 순수 판정한다.

    ``Report.safety_decision``과 전역 news 스위치는 읽지 않는다. 기존 검수와
    provenance 구현은 호출자가 주입하며, malformed DTO는 예외 대신 닫힌 코드로
    거절한다.
    """

    if type(report) is not Report or official_evidence is None:
        return SupplementaryResearchReleaseDecision(
            False, SUPPLEMENTARY_RELEASE_INVALID_REPORT_DTO
        )
    try:
        return _assess(
            report,
            official_evidence=official_evidence,
            source_verifier=source_verifier,
        )
    except (AttributeError, TypeError, ValueError):
        return SupplementaryResearchReleaseDecision(
            False, SUPPLEMENTARY_RELEASE_INVALID_REPORT_DTO
        )


__all__ = [
    "SupplementaryResearchReleaseDecision",
    "assess_supplementary_research_release",
]
