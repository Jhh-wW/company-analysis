"""9장 경쟁사 비교를 공식 원문 두 벌로 만드는 fail-closed 로직.

비교사를 이름이나 업종 상식으로 임의 지정하지 않는다. 먼저 확정된 1~8장 사실
중 회사 공식 자료가 다른 법인을 경쟁·동종 업체로 직접 지목한 문장만 찾고, 그
법인을 DART 전체 법인 목록의 고유번호와 연결한다. 그 뒤 양사의 공식 연간
주요계정에서 지표 정의·기간·연결/별도 범위가 모두 같은 행만 비교한다.

한쪽 자료, 서로 다른 기간/범위, 뉴스나 애널리스트 추정뿐이면 결과 객체를 만들지
않고 ``ComparisonBlockedError``를 낸다. 따라서 호출부는 빈 9장을 일반론으로
채우지 않고 보고서 전체를 출고 차단할 수 있다.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import unicodedata
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from functools import lru_cache
from typing import Callable, Iterable, Mapping, Sequence

from src.features.business_candidate.dart_identity import DartCompanyRecord
from src.features.pipeline.port import FactRecord, Report, ReportSection
from src.features.provenance.sources import (
    Source,
    SourceKind,
    evidence_text_hash,
    exact_evidence_text_hash,
    is_canonical_official_with_registry,
    official_web_currentness_is_usable,
    seal_collected_source,
    source_type_is_official_ir,
)
from src.features.company_comparison.official_sources import OfficialCandidateSentence
from src.features.report_standard.constants import SECTION_BY_ID
from src.features.report_standard.publish import fact_evidence_binding
from src.shared.comparison_candidate_basis import (
    COMPARISON_BASIS_VERSION,
    COMPARISON_SOURCE_BASIS_VERSION,
    comparison_alias_is_competition_target,
    comparison_alias_is_in_sentence,
    comparison_candidate_aliases as _name_aliases,
    comparison_dart_profile_attestation_is_valid,
    comparison_corporate_core as _corporate_core,
    comparison_evidence_sentences as _evidence_sentences,
    comparison_overlap_dimension as _overlap_dimension,
    comparison_source_basis_is_allowed,
    comparison_source_candidate_support_terms,
    comparison_source_overlap_dimension,
    comparison_source_sentence_has_self_subject,
    comparison_source_sentence_has_marker,
    comparison_sentence_has_marker as _has_competition_marker,
    encode_comparison_basis_v1,
    encode_comparison_source_basis_v2,
)
from src.shared.report_quality.constants import COMPETITIVE_COMPARISON_CLAIM_TYPE
from src.shared.report_quality.comparison_claims import (
    comparison_profitability_claim,
    comparison_scale_claim,
)
from src.shared.report_quality.comparison_evidence import comparison_shared_context


COMPETITIVE_SECTION_ID = "competitive_position"
MAX_COMPARATORS = 3
MAX_COMPARISON_FACTS = 3
MAX_CANDIDATE_ALIAS_CHARS = 200
MAX_CANDIDATE_CATALOG_RECORDS = 200_000
MAX_CANDIDATE_ALIASES = 400_000
MAX_IR_CANDIDATE_AGE_DAYS = 400
_CANDIDATE_SECTION_IDS = frozenset(SECTION_BY_ID) - {COMPETITIVE_SECTION_ID}
_PERIOD_NUMBER = re.compile(r"20\d{2}|\d{1,2}")
_INTEGER = re.compile(r"^-?[\d,]+$")
_ANNUAL_REPORT_CODE = "11011"
_SELECTED_REPORT_PERIOD_KIND_KEY = "engine_selected_report_period_kind"
_SELECTED_REPORT_PERIOD_KIND_ANNUAL = "annual"
_WON_CURRENCIES = frozenset({"KRW", "WON", "원"})
_CANDIDATE_ALIAS_INDEX_LOCK = threading.Lock()

# account_id가 같아야만 같은 지표로 본다. 이름이 비슷하다는 이유로 임의 합치지 않는다.
_SUPPORTED_METRICS: Mapping[str, str] = {
    "ifrs-full_Revenue": "매출액",
    "dart_OperatingIncomeLoss": "영업이익",
    "ifrs-full_ProfitLossFromOperatingActivities": "영업이익",
    "ifrs-full_ProfitLoss": "당기순이익",
}


class ComparisonBlockedError(ValueError):
    """동일 조건의 양사 공식 근거가 없어 9장을 만들 수 없음."""

    def __init__(self, reasons: Sequence[str] | str):
        clean = (reasons,) if isinstance(reasons, str) else tuple(reasons)
        self.reasons = tuple(dict.fromkeys(str(reason).strip() for reason in clean if str(reason).strip()))
        super().__init__("; ".join(self.reasons) or "경쟁사 비교 근거가 부족합니다")


class ComparisonSourceTransientError(RuntimeError):
    """비교사 공식 원문 공급자가 일시 실패했음을 보존하는 경계 표지.

    후보 하나의 자료가 없다는 뜻과 DART 요청 자체가 실패했다는 뜻은 다르다.
    후자를 이 표지 없이 일반 ``Exception``으로 삼키면 마지막에는
    ``ComparisonBlockedError``가 되어 회사의 자료 부족으로 잘못 안내된다.
    원래 예외 문자열은 URL·응답 원문을 포함할 수 있으므로 이 객체에는 싣지
    않고 예외 체인으로만 남긴다.
    """


class ComparisonSourceConfigurationError(RuntimeError):
    """비교사 공식 원문 공급자의 인증·권한 설정 오류를 보존하는 표지.

    재시도로 회복될 수 있는 429·전송 장애와 달리 운영 설정을 고쳐야 한다.
    비밀이 섞일 수 있는 원래 예외문은 싣지 않고 예외 체인으로만 남긴다.
    """


class ComparisonSourceInternalError(RuntimeError):
    """비교사 공식 원문 콜백의 내부 계약 오류를 보존하는 표지.

    후보 한 곳의 자료 부족으로 접어 다음 후보를 시도하면 정상 회사가 자료가
    없는 것처럼 보인다. 따라서 provider 호출 전에 요청 전체를 닫는다.
    """


@dataclass(frozen=True)
class CandidateEvidence:
    """확정된 1~8장 사실이 직접 지목한 비교 후보와 근거 결속."""

    record: DartCompanyRecord
    overlap_dimension: str
    evidence_fact_id: str
    evidence_source_id: str
    evidence_text: str
    candidate_corp_code: str
    candidate_name: str
    filing_document_id: str
    evidence_sha256: str
    evidence_exact_sha256: str = ""
    source: Source | None = None
    source_date: str = ""
    self_corp_code: str = ""
    self_attestation_source_id: str = ""
    self_attestation_evidence: str = ""
    document_identity: str = ""
    document_content_sha256: str = ""
    #: 후보 판별기가 실제 원문에서 이미 확인한 alias와 원문 문장. V2 bridge는
    #: 이를 다시 어휘 추출하지 않고 target 맥락 Fact에 그대로 운반한다.
    evidence_support_terms: tuple[str, ...] = ()


@dataclass(frozen=True)
class OfficialCompanyBundle:
    """한 법인의 DART 공식 원문·주요계정 묶음."""

    corp_code: str
    company_name: str
    financials: Mapping[str, object] | None
    filing: Mapping[str, object] | None
    official_text: str


@dataclass(frozen=True)
class ComparisonBuildResult:
    """9장에 원자적으로 덧붙일 공개 장·사실·출처."""

    section: ReportSection
    facts: tuple[FactRecord, ...]
    sources: tuple[Source, ...]
    candidates: tuple[CandidateEvidence, ...]


@dataclass(frozen=True)
class _Observation:
    metric_id: str
    metric_label: str
    account_name: str
    statement_kind: str
    scope_code: str
    period: str
    value: int
    business_year: str
    report_code: str
    currency: str

    @property
    def definition_key(self) -> tuple[str, str, str, str, str, str, str]:
        return (
            self.metric_id,
            _normalized(self.account_name),
            self.statement_kind,
            self.scope_code,
            self.period,
            self.report_code,
            self.currency,
        )


@dataclass(frozen=True)
class _CandidateAliasIndex:
    """한 번 만든 DART 별칭 prefix/길이 색인과 법인 소유권."""

    # 값이 빈 문자열이면 둘 이상 법인이 가진 모호한 별칭이다.
    owners: Mapping[str, str]
    records_by_code: Mapping[str, DartCompanyRecord]
    lengths_by_prefix: Mapping[str, tuple[int, ...]]
    complete: bool = True


def _normalized(value: object) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).casefold().split())


def _shared_context(
    self_bundle: OfficialCompanyBundle,
    comparator_bundle: OfficialCompanyBundle,
) -> dict[str, str]:
    """양사 원문에 모두 직접 나타나는 고객·제품·시장 범위를 구조화한다."""

    return comparison_shared_context(
        self_company=self_bundle.company_name,
        self_text=self_bundle.official_text,
        comparator_company=comparator_bundle.company_name,
        comparator_text=comparator_bundle.official_text,
    )


def _bundle_evidence(bundle: OfficialCompanyBundle) -> str:
    """공식 문서·API 응답을 요약문으로 바꾸지 않은 결정론적 원 payload."""

    try:
        return json.dumps(
            {
                "official_text": bundle.official_text,
                "filing": bundle.filing,
                "financials": bundle.financials,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError):
        return ""


def comparison_basis_v1(candidate: CandidateEvidence) -> str:
    """확정된 1~8장 후보 사실을 기계 재검증 가능한 v1 JSON으로 잇는다."""

    return encode_comparison_basis_v1(
        {
            "version": COMPARISON_BASIS_VERSION,
            "candidate_fact_id": candidate.evidence_fact_id,
            "candidate_source_id": candidate.evidence_source_id,
            "candidate_corp_code": candidate.candidate_corp_code,
            "candidate_name": candidate.candidate_name,
            "filing_document_id": candidate.filing_document_id,
            "evidence_sha256": candidate.evidence_sha256,
            "overlap_dimension": candidate.overlap_dimension,
        }
    )


def comparison_basis(candidate: CandidateEvidence) -> str:
    """1~8장 사실은 v1, 독립 공식 원문 문장은 v2로 서로 위장 없이 잠근다."""

    if candidate.evidence_fact_id:
        return comparison_basis_v1(candidate)
    source = candidate.source
    if source is None:
        return ""
    encoded = encode_comparison_source_basis_v2(
        {
            "version": COMPARISON_SOURCE_BASIS_VERSION,
            "candidate_source_id": source.source_id,
            "self_corp_code": candidate.self_corp_code,
            "self_attestation_source_id": candidate.self_attestation_source_id,
            "self_attestation_evidence": candidate.self_attestation_evidence,
            "candidate_corp_code": candidate.candidate_corp_code,
            "candidate_name": candidate.candidate_name,
            "source_kind": source.kind.value,
            "source_type": source.source_type,
            "source_publisher": source.publisher,
            "source_host": source.host,
            "source_url": source.url,
            "source_document_id": source.document_id,
            "source_location": source.location,
            "source_date": candidate.source_date,
            "evidence_text": candidate.evidence_text,
            "evidence_sha256": candidate.evidence_sha256,
            "evidence_exact_sha256": candidate.evidence_exact_sha256,
            "overlap_dimension": candidate.overlap_dimension,
        }
    )
    return encoded if comparison_source_basis_is_allowed(
        json.loads(encoded) if encoded else {}
    ) else ""


def _exact_legal_name(value: object) -> str:
    return " ".join(
        unicodedata.normalize("NFKC", str(value or "")).casefold().split()
    )


def _source_date_value(source: Source, *, reference_date: str = "") -> str:
    document_date = str(source.published_at or source.disclosed_at or "").strip()
    if not official_web_currentness_is_usable(
        source_type=source.source_type,
        url=source.url,
        published_at=source.published_at,
        disclosed_at=source.disclosed_at,
        collected_at=source.collected_at,
        reference_date=reference_date,
    ):
        return ""
    if source_type_is_official_ir(source.source_type):
        # PDF 접근일은 문서 발표일·기준일이 아니다. 수집기는 아직 신뢰 가능한
        # PDF 문서일을 결속하지 않으므로 현재 경쟁 후보에는 쓰지 않는다.
        try:
            published = date.fromisoformat(document_date)
            collected = date.fromisoformat(str(source.collected_at or "").strip())
        except ValueError:
            return ""
        age_days = (collected - published).days
        return (
            document_date
            if 0 <= age_days <= MAX_IR_CANDIDATE_AGE_DAYS
            else ""
        )
    return document_date or str(source.collected_at or "").strip()


def _candidate_source_allowed(
    source: Source,
    sources: Sequence[Source],
    *,
    reference_date: str = "",
) -> bool:
    """DART 공시 또는 exact-attested 공식 HTML IR·웹만 후보로 허용한다."""

    if (
        source.provenance_role != "citation"
        or not is_canonical_official_with_registry(source, tuple(sources))
    ):
        return False
    return bool(
        _source_date_value(source, reference_date=reference_date)
        and comparison_source_basis_is_allowed(
            {
                "version": COMPARISON_SOURCE_BASIS_VERSION,
                "source_kind": source.kind.value,
                "source_type": source.source_type,
                "source_host": source.host,
                "source_url": source.url,
            }
        )
    )


def _candidate_alias_index(
    records: Sequence[DartCompanyRecord],
) -> _CandidateAliasIndex:
    """DART 별칭 소유권과 문장 길이 비례 검색 색인을 한 번 만든다."""

    with _CANDIDATE_ALIAS_INDEX_LOCK:
        return _cached_candidate_alias_index(
            tuple(records),
            MAX_CANDIDATE_CATALOG_RECORDS,
            MAX_CANDIDATE_ALIASES,
            MAX_CANDIDATE_ALIAS_CHARS,
        )


@lru_cache(maxsize=2)
def _cached_candidate_alias_index(
    records: tuple[DartCompanyRecord, ...],
    max_records: int,
    max_aliases: int,
    max_alias_chars: int,
) -> _CandidateAliasIndex:
    """동일 DART snapshot은 동시 실행끼리 불변 색인 하나만 공유한다."""

    if len(records) > max_records:
        return _CandidateAliasIndex({}, {}, {}, complete=False)
    alias_owners: dict[str, str] = {}
    by_code: dict[str, DartCompanyRecord] = {}
    for record in records:
        corp_code = str(record.corp_code or "").strip()
        if not corp_code:
            continue
        by_code.setdefault(corp_code, record)
        for official_name in (record.corp_name, record.corp_eng_name):
            for alias in _name_aliases(official_name):
                if (
                    len(_corporate_core(alias)) < 2
                    or len(alias) > max_alias_chars
                ):
                    continue
                if alias not in alias_owners:
                    if len(alias_owners) >= max_aliases:
                        return _CandidateAliasIndex({}, {}, {}, complete=False)
                    alias_owners[alias] = corp_code
                elif alias_owners[alias] != corp_code:
                    alias_owners[alias] = ""

    raw_lengths: dict[str, set[int]] = {}
    for alias in alias_owners:
        raw_lengths.setdefault(alias[:2], set()).add(len(alias))

    return _CandidateAliasIndex(
        owners=alias_owners,
        records_by_code=by_code,
        lengths_by_prefix={
            prefix: tuple(sorted(lengths, reverse=True))
            for prefix, lengths in raw_lengths.items()
        },
    )


def _aliases_in_sentence(
    sentence: str,
    alias_index: _CandidateAliasIndex,
) -> tuple[str, ...]:
    """전체 catalog를 다시 훑지 않고 문장에 실제 나온 별칭만 반환한다."""

    normalized = _normalized(sentence)
    found: set[str] = set()
    for start in range(max(0, len(normalized) - 1)):
        lengths = alias_index.lengths_by_prefix.get(normalized[start : start + 2], ())
        for length in lengths:
            if start + length > len(normalized):
                continue
            candidate = normalized[start : start + length]
            if candidate in alias_index.owners:
                found.add(candidate)
    return tuple(
        sorted(
            (
                alias
                for alias in found
                if comparison_alias_is_in_sentence(alias, sentence)
            ),
            key=lambda value: (-len(value), value),
        )
    )


def _candidate_matches_for_sentence(
    sentence: str,
    *,
    alias_index: _CandidateAliasIndex,
    self_corp_code: str,
    self_company: str,
) -> tuple[tuple[DartCompanyRecord, tuple[str, ...]], ...]:
    """자사 경쟁 술어의 target인 고유 비자사 DART 법인만 반환한다."""

    if not alias_index.complete:
        return ()
    present_aliases = _aliases_in_sentence(sentence, alias_index)
    matched: dict[str, list[str]] = {}
    for alias in present_aliases:
        corp_code = alias_index.owners[alias]
        if not corp_code:
            continue
        if corp_code == self_corp_code or not comparison_alias_is_competition_target(
            alias,
            sentence,
            self_company=self_company,
            known_company_aliases=present_aliases,
        ):
            continue
        matched.setdefault(corp_code, []).append(alias)
    found: list[tuple[DartCompanyRecord, tuple[str, ...]]] = []
    for corp_code, aliases in matched.items():
        record = alias_index.records_by_code.get(corp_code)
        if record is not None:
            found.append(
                (
                    record,
                    tuple(sorted(set(aliases), key=lambda value: (-len(value), value))),
                )
            )
    return tuple(
        sorted(
            found,
            key=lambda item: (
                -max(len(alias) for alias in item[1]),
                str(item[0].corp_code or "").strip(),
            ),
        )
    )


def comparison_candidate_preflight_possible(
    official_texts: str | Iterable[str],
    catalog: Iterable[DartCompanyRecord],
    *,
    self_corp_code: str,
    self_company: str = "",
) -> bool | None:
    """자사 공식 가능 원문 상위집합으로 후보 불가능성만 조기에 증명한다.

    ``False``만 결정적이다. 빈 원문·빈/사용 불가 카탈로그는 수집 기술 상태를
    경쟁 관계 부재로 오인하지 않도록 ``None``(unknown)으로 계속 진행시킨다.
    ``True`` 역시 후보 확정이 아니며, 최종 후보는 ``discover_candidates``의
    검증된 1~8장 사실 계약을 별도로 통과해야 한다.
    """

    raw_texts = (
        (official_texts,)
        if isinstance(official_texts, str)
        else tuple(official_texts)
    )
    texts = tuple(
        str(text or "").strip()
        for text in raw_texts
        if str(text or "").strip()
    )
    records = tuple(catalog)
    if not texts or not records:
        return None
    alias_index = _candidate_alias_index(records)
    if (
        not alias_index.complete
        or not alias_index.owners
        or not alias_index.records_by_code
    ):
        return None
    self_code = str(self_corp_code or "").strip()
    self_record = alias_index.records_by_code.get(self_code)
    if self_record is None:
        return None
    resolved_self_company = str(self_company or "").strip()
    if not resolved_self_company:
        resolved_self_company = str(self_record.corp_name or "").strip()
    elif _exact_legal_name(resolved_self_company) != _exact_legal_name(
        self_record.corp_name
    ):
        return None
    if not resolved_self_company:
        return None
    if not any(
        owner and owner != self_code
        for owner in alias_index.owners.values()
    ):
        # 자사 한 곳뿐인 fixture/부분 카탈로그를 "경쟁사 없음"으로 확정하지 않는다.
        return None
    marker_sentences = tuple(
        sentence
        for text in texts
        for sentence in _evidence_sentences(text)
        if comparison_source_sentence_has_marker(sentence)
        and (
            not str(self_company or "").strip()
            or comparison_source_sentence_has_self_subject(
                sentence, self_company
            )
        )
    )
    return any(
        _candidate_matches_for_sentence(
            sentence,
            alias_index=alias_index,
            self_corp_code=self_code,
            self_company=resolved_self_company,
        )
        for sentence in marker_sentences
    )


def discover_candidates(
    report: Report,
    catalog: Iterable[DartCompanyRecord],
    *,
    self_corp_code: str,
    limit: int = MAX_COMPARATORS,
) -> tuple[CandidateEvidence, ...]:
    """확정 1~8장 공식 사실의 단일 원문 문장에서만 비교 후보를 고른다."""

    records = tuple(catalog)
    self_code = str(self_corp_code or "").strip()
    self_records = [
        record for record in records if str(record.corp_code or "").strip() == self_code
    ]
    if (
        len(self_records) != 1
        or _exact_legal_name(self_records[0].corp_name)
        != _exact_legal_name(report.company)
    ):
        return ()
    source_items = tuple(
        source for source in report.citations if isinstance(source, Source)
    )
    source_id_counts: dict[str, int] = {}
    for source in source_items:
        source_id = source.source_id.strip()
        if source_id:
            source_id_counts[source_id] = source_id_counts.get(source_id, 0) + 1
    sources = {
        source.source_id: source
        for source in source_items
        if source_id_counts.get(source.source_id.strip()) == 1
        and is_canonical_official_with_registry(source, source_items)
        and _corporate_core(source.publisher) == _corporate_core(report.company)
    }
    fact_id_counts: dict[str, int] = {}
    for fact in report.fact_records:
        fact_id = fact.fact_id.strip()
        if fact_id:
            fact_id_counts[fact_id] = fact_id_counts.get(fact_id, 0) + 1
    section_counts: dict[str, int] = {}
    referenced_by_section: dict[str, set[str]] = {}
    for section in report.sections:
        if section.cell not in _CANDIDATE_SECTION_IDS:
            continue
        section_counts[section.cell] = section_counts.get(section.cell, 0) + 1
        referenced_by_section.setdefault(section.cell, set()).update(
            str(fact_id or "").strip() for fact_id in section.fact_ids
        )
    evidence_rows: list[tuple[int, FactRecord, Source, str]] = []
    for fact in report.fact_records:
        source = sources.get(fact.source_id)
        sentences = _evidence_sentences(fact.state_evidence)
        if (
            source is None
            or fact_id_counts.get(fact.fact_id.strip()) != 1
            or fact.section_owner not in _CANDIDATE_SECTION_IDS
            or section_counts.get(fact.section_owner) != 1
            or fact.fact_id.strip()
            not in referenced_by_section.get(fact.section_owner, set())
            or fact.status != "verified"
            or fact.verification_status != "verified"
            or not fact.evidence_binding
            or fact.evidence_binding != fact_evidence_binding(fact)
            or _corporate_core(fact.legal_entity) != _corporate_core(report.company)
            or _corporate_core(fact.source_publisher) != _corporate_core(source.publisher)
            or fact.source_document_id.strip() != source.document_id.strip()
            or fact.source_type.strip().casefold() != source.source_type.strip().casefold()
            or fact.section_owner not in source.used_in
            or len(sentences) != 1
            or evidence_text_hash(fact.state_evidence) not in source.evidence_hashes
            or not _has_competition_marker(sentences[0])
        ):
            continue
        evidence_rows.append((len(evidence_rows), fact, source, sentences[0]))

    if not evidence_rows:
        return ()

    alias_index = _candidate_alias_index(records)
    if not alias_index.complete:
        return ()

    found: list[CandidateEvidence] = []
    seen_codes: set[str] = set()
    cap = max(1, min(int(limit), MAX_COMPARATORS))
    for _row_index, fact, source, sentence in evidence_rows:
        matched = _candidate_matches_for_sentence(
            sentence,
            alias_index=alias_index,
            self_corp_code=self_code,
            self_company=report.company,
        )
        for record, _matching_aliases in matched:
            corp_code = str(record.corp_code or "").strip()
            if corp_code in seen_codes:
                continue
            evidence_hash = evidence_text_hash(sentence)
            evidence = CandidateEvidence(
                record=record,
                overlap_dimension=_overlap_dimension(sentence),
                evidence_fact_id=fact.fact_id,
                evidence_source_id=source.source_id,
                evidence_text=sentence,
                candidate_corp_code=corp_code,
                candidate_name=str(record.corp_name or "").strip(),
                filing_document_id=source.document_id,
                evidence_sha256=evidence_hash,
                evidence_support_terms=comparison_source_candidate_support_terms(
                    sentence,
                    str(record.corp_name or "").strip(),
                ),
            )
            seen_codes.add(corp_code)
            found.append(evidence)
            if len(found) >= cap:
                return tuple(found)
    return tuple(found)


def discover_official_source_candidates(
    evidence_rows: Iterable[OfficialCandidateSentence],
    sources: Sequence[Source],
    catalog: Iterable[DartCompanyRecord],
    *,
    self_corp_code: str,
    self_company: str,
    limit: int = MAX_COMPARATORS,
    as_of_date: str = "",
) -> tuple[CandidateEvidence, ...]:
    """봉인된 공식 Source의 순수 경쟁 문장을 1~8장과 독립해 후보로 고른다."""

    records = tuple(catalog)
    self_code = str(self_corp_code or "").strip()
    self_records = [
        record for record in records if str(record.corp_code or "").strip() == self_code
    ]
    if (
        len(self_records) != 1
        or _exact_legal_name(self_records[0].corp_name)
        != _exact_legal_name(self_company)
    ):
        return ()
    source_registry = tuple(sources)
    identity_attesters = [
        source
        for source in source_registry
        if source.provenance_role == "attestation_only"
        and is_canonical_official_with_registry(source, source_registry)
        and comparison_dart_profile_attestation_is_valid(
            source_kind=source.kind.value,
            source_type=source.source_type,
            source_publisher=source.publisher,
            source_host=source.host,
            source_url=source.url,
            source_document_id=source.document_id,
            evidence=source.domain_attestation_evidence,
            self_corp_code=self_code,
            self_company=self_company,
        )
    ]
    if len(identity_attesters) != 1:
        return ()
    identity_attester = identity_attesters[0]
    source_counts: dict[str, int] = {}
    for source in source_registry:
        source_id = source.source_id.strip()
        if source_id:
            source_counts[source_id] = source_counts.get(source_id, 0) + 1
    alias_index = _candidate_alias_index(records)
    if not alias_index.complete:
        return ()
    found: list[CandidateEvidence] = []
    seen_codes: set[str] = set()
    cap = max(1, min(int(limit), MAX_COMPARATORS))
    for _row_index, item in enumerate(evidence_rows):
        source = item.source
        sentences = _evidence_sentences(item.evidence_text)
        if (
            source_counts.get(source.source_id.strip()) != 1
            or not _candidate_source_allowed(
                source,
                source_registry,
                reference_date=as_of_date,
            )
            or _exact_legal_name(source.publisher) != _exact_legal_name(self_company)
            or len(sentences) != 1
            or not comparison_source_sentence_has_marker(sentences[0])
            or evidence_text_hash(sentences[0]) not in source.evidence_hashes
            or exact_evidence_text_hash(sentences[0])
            not in source.exact_evidence_hashes
            or (
                source.kind is SourceKind.OTHER
                and source.domain_attestation_source_id.strip()
                != identity_attester.source_id.strip()
            )
        ):
            continue
        sentence = sentences[0]
        if not comparison_source_sentence_has_self_subject(
            sentence, self_records[0].corp_name
        ):
            continue
        matched = _candidate_matches_for_sentence(
            sentence,
            alias_index=alias_index,
            self_corp_code=self_code,
            self_company=self_company,
        )
        for record, _matching_aliases in matched:
            corp_code = str(record.corp_code or "").strip()
            if corp_code in seen_codes:
                continue
            evidence = CandidateEvidence(
                record=record,
                overlap_dimension=comparison_source_overlap_dimension(sentence),
                evidence_fact_id="",
                evidence_source_id=source.source_id,
                evidence_text=sentence,
                candidate_corp_code=corp_code,
                candidate_name=str(record.corp_name or "").strip(),
                filing_document_id=source.document_id,
                evidence_sha256=evidence_text_hash(sentence),
                evidence_exact_sha256=exact_evidence_text_hash(sentence),
                source=source,
                source_date=_source_date_value(
                    source,
                    reference_date=as_of_date,
                ),
                self_corp_code=self_code,
                self_attestation_source_id=identity_attester.source_id,
                self_attestation_evidence=identity_attester.domain_attestation_evidence,
                document_identity=item.document_identity,
                document_content_sha256=item.document_content_sha256,
                evidence_support_terms=comparison_source_candidate_support_terms(
                    sentence,
                    str(record.corp_name or "").strip(),
                ),
            )
            seen_codes.add(corp_code)
            found.append(evidence)
            if len(found) >= cap:
                return tuple(found)
    return tuple(found)


def _period(raw: object) -> str:
    numbers = _PERIOD_NUMBER.findall(str(raw or ""))
    if len(numbers) < 6:
        return ""
    try:
        start_date = date(int(numbers[0]), int(numbers[1]), int(numbers[2]))
        end_date = date(int(numbers[3]), int(numbers[4]), int(numbers[5]))
    except (TypeError, ValueError):
        return ""
    days = (end_date - start_date).days + 1
    if not 350 <= days <= 380:
        return ""
    return f"{start_date.isoformat()}~{end_date.isoformat()}"


def _observations(
    financials: Mapping[str, object] | None,
) -> dict[tuple[str, str, str, str, str, str, str], _Observation]:
    if not isinstance(financials, Mapping) or financials.get("status") != "000":
        return {}
    top_report_code = str(financials.get("reprt_code") or "").strip()
    if top_report_code != _ANNUAL_REPORT_CODE:
        return {}
    raw_rows = (financials or {}).get("list") if isinstance(financials, Mapping) else None
    if not isinstance(raw_rows, list):
        return {}
    observations: dict[tuple[str, str, str, str, str, str, str], _Observation] = {}
    logical_observations: dict[tuple[str, str, str, str, str, str], _Observation] = {}
    for raw in raw_rows:
        if not isinstance(raw, Mapping):
            return {}
        metric_id = str(raw.get("account_id") or "").strip()
        metric_label = _SUPPORTED_METRICS.get(metric_id, "")
        if not metric_label:
            continue
        account_name = str(raw.get("account_nm") or "").strip()
        statement_kind = str(raw.get("sj_div") or "").strip().upper()
        scope_code = str(raw.get("fs_div") or "").strip().upper()
        period = _period(raw.get("thstrm_dt"))
        amount = str(raw.get("thstrm_amount") or "").strip()
        business_year = str(raw.get("bsns_year") or "").strip()
        report_code = str(raw.get("reprt_code") or "").strip()
        currency = str(raw.get("currency") or "").strip().upper()
        if (
            not account_name
            or statement_kind not in {"IS", "CIS"}
            or scope_code not in {"CFS", "OFS"}
            or not period
            or not _INTEGER.fullmatch(amount)
            or report_code != _ANNUAL_REPORT_CODE
            or currency not in _WON_CURRENCIES
            or not re.fullmatch(r"20\d{2}", business_year)
            or business_year != period.split("~", 1)[1][:4]
        ):
            return {}
        value = int(amount.replace(",", ""))
        if metric_id == "ifrs-full_Revenue" and value <= 0:
            return {}
        observation = _Observation(
            metric_id=metric_id,
            metric_label=metric_label,
            account_name=account_name,
            statement_kind=statement_kind,
            scope_code=scope_code,
            period=period,
            value=value,
            business_year=business_year,
            report_code=report_code,
            currency=currency,
        )
        existing = observations.get(observation.definition_key)
        if existing is not None and existing != observation:
            # 같은 정의·기간·범위가 서로 다른 값이면 어느 행이 정본인지 추측하지 않는다.
            return {}
        logical_key = (
            observation.metric_id,
            observation.statement_kind,
            observation.scope_code,
            observation.period,
            observation.report_code,
            observation.currency,
        )
        logical_existing = logical_observations.get(logical_key)
        if logical_existing is not None and logical_existing != observation:
            # 같은 표준 계정 ID를 이름만 달리해 두 번 보낸 응답도 모호하다. 어느
            # 이름·값을 고를지 추측하지 않고 양사 비교 전체를 닫는다.
            return {}
        observations[observation.definition_key] = observation
        logical_observations[logical_key] = observation
    return observations


def _filing_year(filing: Mapping[str, object] | None) -> str:
    report_name = str((filing or {}).get("report_nm") or "")
    years = re.findall(r"20\d{2}", report_name)
    return years[-1] if years else ""


def _period_year(period: str) -> str:
    end = period.split("~", 1)[-1]
    return end[:4] if re.match(r"^20\d{2}-", end) else ""


def _filing_is_annual_for_period(bundle: OfficialCompanyBundle, period: str) -> bool:
    filing = bundle.filing or {}
    report_name = str(filing.get("report_nm") or "")
    is_annual = "사업보고서" in report_name or "감사보고서" in report_name
    receipt_number = str(
        filing.get("rcept_no") or filing.get("rceptNo") or ""
    ).strip()
    report_code = str(filing.get("reprt_code") or "").strip()
    selected_period_kind = str(
        filing.get(_SELECTED_REPORT_PERIOD_KIND_KEY) or ""
    ).strip()
    return (
        bool(bundle.official_text.strip())
        and is_annual
        and bool(receipt_number)
        and bool(_source_date(filing.get("rcept_dt")))
        and _filing_year(filing) == _period_year(period)
        # 실제 list.json에는 reprt_code가 없다. 새 엔진은 annual 선택 경계를
        # 별도 내부 필드로 운반하고, 옛 저장/fixture만 재무 API 코드를 쓴다.
        and (
            selected_period_kind == _SELECTED_REPORT_PERIOD_KIND_ANNUAL
            or report_code == _ANNUAL_REPORT_CODE
        )
    )


def _source_date(raw: object) -> str:
    text = str(raw or "").strip()
    if re.fullmatch(r"\d{8}", text):
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return ""


def _safe_id(value: str) -> str:
    return hashlib.sha256(_normalized(value).encode("utf-8")).hexdigest()[:16]


def _financial_source(
    bundle: OfficialCompanyBundle,
    observation: _Observation,
    *,
    number: int,
    role: str,
    collected_on: str,
    evidence: str,
) -> Source:
    scope = "연결재무제표(CFS)" if observation.scope_code == "CFS" else "별도재무제표(OFS)"
    year = _period_year(observation.period)
    source_id = f"comparison-{role}-{_safe_id(bundle.corp_code + year + observation.scope_code)}"
    filing = bundle.filing or {}
    report_name = str(filing.get("report_nm") or "").strip()
    receipt_number = str(
        filing.get("rcept_no") or filing.get("rceptNo") or ""
    ).strip()
    return seal_collected_source(Source(
        number=number,
        kind=SourceKind.FILING,
        label=f"{bundle.company_name} {report_name} · 단일회사 주요계정",
        disclosed_at=_source_date(filing.get("rcept_dt")),
        collected_at=collected_on,
        source_id=source_id,
        title=report_name,
        publisher=bundle.company_name,
        host="dart.fss.or.kr",
        url=f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt_number}",
        document_id=receipt_number,
        location=(
            f"단일회사 주요계정 API(11011) / {scope} / {observation.period}"
        ),
        source_type="공식 공시·재무 API",
        fact_status="공시 실제값",
        used_in=[COMPETITIVE_SECTION_ID],
        evidence_hashes=[evidence_text_hash(evidence)],
        # V2 FULL은 같은 URL만으로 원문을 승인하지 않는다. 양사 비교에 실제로
        # 넣은 결정론적 DART bundle 바이트를 Source seal에 함께 잠근다.
        exact_evidence_hashes=[exact_evidence_text_hash(evidence)],
    ))


def _fact_id(*parts: str) -> str:
    joined = "\x1f".join(_normalized(part) for part in parts)
    return "fact-compare-" + hashlib.sha256(joined.encode("utf-8")).hexdigest()[:16]


def _ratio(self_value: int, comparator_value: int) -> Decimal:
    try:
        rounded = (Decimal(self_value) / Decimal(comparator_value)).quantize(
            Decimal("0.1"), rounding=ROUND_HALF_UP
        )
    except (InvalidOperation, ZeroDivisionError):
        raise ComparisonBlockedError("비교 배수를 안전하게 계산할 수 없습니다") from None
    if rounded <= 0:
        raise ComparisonBlockedError(
            "소수 첫째 자리 표시에서 0으로 사라지는 비교 배수는 공개하지 않습니다"
        )
    return rounded


def _operating_margin(operating_income: int, revenue: int) -> Decimal:
    try:
        return (
            Decimal(operating_income) * Decimal(100) / Decimal(revenue)
        ).quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ZeroDivisionError):
        raise ComparisonBlockedError("영업이익률을 안전하게 계산할 수 없습니다") from None


def _definition(*rows: _Observation) -> str:
    return ";".join(
        "|".join(
            (
                row.metric_id,
                row.account_name,
                row.statement_kind,
                row.report_code,
                row.currency,
            )
        )
        for row in rows
    )


def _comparison_conditions(
    context: Mapping[str, str],
    *,
    period: str,
    definition: str,
    accounting_scope: str,
) -> dict[str, str]:
    return {
        "customer": context["customer"],
        "product": context["product"],
        "market": context["market"],
        "self_period": period,
        "comparator_period": period,
        "self_definition": definition,
        "comparator_definition": definition,
        "self_accounting_scope": accounting_scope,
        "comparator_accounting_scope": accounting_scope,
    }


def _matching_pairs(
    self_bundle: OfficialCompanyBundle,
    comparator_bundle: OfficialCompanyBundle,
) -> list[tuple[_Observation, _Observation]]:
    self_rows = _observations(self_bundle.financials)
    comparator_rows = _observations(comparator_bundle.financials)
    pairs: list[tuple[_Observation, _Observation]] = []
    metric_order = tuple(_SUPPORTED_METRICS)
    for key, self_row in self_rows.items():
        comparator_row = comparator_rows.get(key)
        if comparator_row is None:
            continue
        if not _filing_is_annual_for_period(self_bundle, self_row.period):
            continue
        if not _filing_is_annual_for_period(comparator_bundle, comparator_row.period):
            continue
        pairs.append((self_row, comparator_row))
    pairs.sort(key=lambda pair: metric_order.index(pair[0].metric_id))
    return pairs


def build_competitive_position(
    report: Report,
    *,
    self_bundle: OfficialCompanyBundle,
    catalog: Iterable[DartCompanyRecord],
    fetch_comparator: Callable[[DartCompanyRecord], OfficialCompanyBundle | None],
    collected_on: str,
    max_comparators: int = MAX_COMPARATORS,
    official_candidate_sentences: Iterable[OfficialCandidateSentence] = (),
    candidate_source_registry: Sequence[Source] = (),
) -> ComparisonBuildResult:
    """확정된 1~8장 뒤, 양사 공식 원문이 맞는 비교만 9장으로 만든다."""

    self_payload = _bundle_evidence(self_bundle)
    self_observations = _observations(self_bundle.financials)
    if (
        not self_bundle.official_text.strip()
        or not self_observations
        or not self_payload
    ):
        raise ComparisonBlockedError("자사 공식 원문과 비교 가능한 주요계정이 모두 필요합니다")
    if not any(
        _filing_is_annual_for_period(self_bundle, row.period)
        for row in self_observations.values()
    ):
        # 비교사 DART 요청 전에 자사 공식 문서·완료 사업연도·
        # 접수번호 결속을 먼저 닫아 불필요한 유료 수집을 막는다.
        raise ComparisonBlockedError(
            "자사 공식 원문이 완료 사업연도·접수번호·보고서 코드에 결속되지 않았습니다"
        )

    section_candidates = discover_candidates(
        report,
        catalog,
        self_corp_code=self_bundle.corp_code,
        limit=max_comparators,
    )
    source_candidates = discover_official_source_candidates(
        official_candidate_sentences,
        candidate_source_registry,
        catalog,
        self_corp_code=self_bundle.corp_code,
        self_company=report.company,
        limit=max_comparators,
        as_of_date=collected_on,
    )
    candidates_list: list[CandidateEvidence] = []
    seen_candidate_codes: set[str] = set()
    for candidate in (*section_candidates, *source_candidates):
        if candidate.candidate_corp_code in seen_candidate_codes:
            continue
        seen_candidate_codes.add(candidate.candidate_corp_code)
        candidates_list.append(candidate)
        if len(candidates_list) >= max(1, min(max_comparators, MAX_COMPARATORS)):
            break
    candidates = tuple(candidates_list)
    if not candidates:
        raise ComparisonBlockedError(
            "확정된 1~8장 또는 봉인된 자사 공식 원문에서 경쟁 관계와 "
            "DART 법인을 함께 확인하지 못했습니다"
        )

    candidate_registry_by_id = {
        source.source_id: source for source in candidate_source_registry
    }
    next_number = max(
        (
            source.number
            for source in (
                *(item for item in report.citations if isinstance(item, Source)),
                *candidate_source_registry,
            )
        ),
        default=0,
    ) + 1
    facts: list[FactRecord] = []
    prose_lines: list[tuple[str, str]] = []
    sources_by_id: dict[str, Source] = {}
    failure_reasons: list[str] = []
    used_axes: set[str] = set()

    report_source_ids = {
        source.source_id
        for source in report.citations
        if isinstance(source, Source)
    }
    pending_source_ids = [
        source_id
        for candidate in candidates
        if not candidate.evidence_fact_id
        for source_id in (
            candidate.evidence_source_id,
            candidate.self_attestation_source_id,
        )
        if source_id
    ]
    while pending_source_ids:
        source_id = pending_source_ids.pop(0)
        source = candidate_registry_by_id.get(source_id)
        if source is None or source_id in sources_by_id or source_id in report_source_ids:
            continue
        sources_by_id[source_id] = seal_collected_source(
            replace(
                source,
                used_in=sorted({*source.used_in, COMPETITIVE_SECTION_ID}),
            )
        )
        attester_id = source.domain_attestation_source_id.strip()
        if attester_id:
            pending_source_ids.append(attester_id)

    def register_source(
        bundle: OfficialCompanyBundle,
        observation: _Observation,
        *,
        role: str,
        evidence: str,
    ) -> Source:
        """같은 회사·연도·범위의 Source를 재사용하며 근거 해시를 합친다."""

        nonlocal next_number
        source = _financial_source(
            bundle,
            observation,
            number=next_number,
            role=role,
            collected_on=collected_on,
            evidence=evidence,
        )
        existing = sources_by_id.get(source.source_id)
        if existing is not None:
            source = seal_collected_source(
                replace(
                    existing,
                    evidence_hashes=sorted(
                        set(existing.evidence_hashes)
                        | {evidence_text_hash(evidence)}
                    ),
                )
            )
        else:
            next_number += 1
        sources_by_id[source.source_id] = source
        return source

    for candidate in candidates:
        try:
            comparator = fetch_comparator(candidate.record)
        except (
            ComparisonSourceTransientError,
            ComparisonSourceConfigurationError,
            ComparisonSourceInternalError,
        ):
            # DART 운영 장애·설정 오류·내부 계약 오류를 후보별 「자료 없음」으로
            # 접지 않는다. 복구 방식이 서로 다르므로 타입을 그대로 올려 보낸다.
            raise
        except Exception as exc:
            # callback 포트는 복구 가능한 외부 상태를 위의 닫힌 세 타입으로
            # 번역할 책임이 있다. 그 밖의 예외는 TypeError 같은 우리 배선 결함일
            # 수 있으므로 후보 하나의 「자료 없음」으로 삼키지 않는다.
            raise ComparisonSourceInternalError(
                "비교사 공식 자료 수집기가 닫힌 오류 계약을 지키지 못했습니다"
            ) from exc
        if comparator is None or not comparator.official_text.strip():
            failure_reasons.append(f"{candidate.record.corp_name}: 비교사 공식 원문이 없습니다")
            continue
        if _normalized(comparator.company_name) == _normalized(self_bundle.company_name):
            failure_reasons.append(f"{candidate.record.corp_name}: 자사와 비교사 발행 주체가 같습니다")
            continue
        comparator_payload = _bundle_evidence(comparator)
        context = _shared_context(self_bundle, comparator)
        if not comparator_payload or not context:
            failure_reasons.append(
                f"{candidate.record.corp_name}: 양사 공식 원문에 같은 고객·제품·시장 범위가 구조화되지 않았습니다"
            )
            continue
        pairs = _matching_pairs(self_bundle, comparator)
        if not pairs:
            failure_reasons.append(
                f"{candidate.record.corp_name}: 지표 정의·기간·연결범위가 같은 양사 공식 수치가 없습니다"
            )
            continue
        pair_by_metric = {self_row.metric_id: (self_row, other_row) for self_row, other_row in pairs}
        revenue_pair = pair_by_metric.get("ifrs-full_Revenue")
        operating_pair = next(
            (
                pair_by_metric[metric_id]
                for metric_id in (
                    "dart_OperatingIncomeLoss",
                    "ifrs-full_ProfitLossFromOperatingActivities",
                )
                if metric_id in pair_by_metric
            ),
            None,
        )

        # 9장의 최소 비교축은 같은 완료 사업연도·연결범위의 수익성이다.
        # 매출·영업이익·순이익 규모를 각각 세어 같은 축을 부풀리지 않는다.
        if (
            "profitability" not in used_axes
            and revenue_pair is not None
            and operating_pair is not None
        ):
            self_revenue, comparator_revenue = revenue_pair
            self_operating, comparator_operating = operating_pair
            same_basis = (
                self_revenue.scope_code
                == self_operating.scope_code
                == comparator_revenue.scope_code
                == comparator_operating.scope_code
                == "CFS"
                and self_revenue.period
                == self_operating.period
                == comparator_revenue.period
                == comparator_operating.period
            )
            if same_basis:
                self_margin = _operating_margin(self_operating.value, self_revenue.value)
                comparator_margin = _operating_margin(
                    comparator_operating.value,
                    comparator_revenue.value,
                )
                signed_difference = self_margin - comparator_margin
                if signed_difference != 0:
                    difference = abs(signed_difference).quantize(
                        Decimal("0.1"), rounding=ROUND_HALF_UP
                    )
                    direction = "높았다" if signed_difference > 0 else "낮았다"
                    scope_label = "연결재무제표"
                    scope = "연결재무제표(CFS)"
                    period = self_revenue.period
                    self_evidence = self_payload
                    comparator_evidence = comparator_payload
                    self_source = register_source(
                        self_bundle,
                        self_revenue,
                        role="self",
                        evidence=self_evidence,
                    )
                    comparator_source = register_source(
                        comparator,
                        comparator_revenue,
                        role="comparator",
                        evidence=comparator_evidence,
                    )
                    claim = comparison_profitability_claim(
                        comparison_target=comparator.company_name,
                        difference=f"{difference:.1f}",
                        direction=direction,
                    )
                    display_value = (
                        f"자사 {self_margin:.1f}%; 비교사 {comparator_margin:.1f}%; "
                        f"차이 {difference:.1f}%p"
                    )
                    definition = _definition(self_revenue, self_operating)
                    comparator_definition = _definition(
                        comparator_revenue, comparator_operating
                    )
                    if definition != comparator_definition:
                        continue
                    fact = FactRecord(
                        fact_id=_fact_id(
                            self_bundle.corp_code,
                            comparator.corp_code,
                            "operating_margin",
                            period,
                            "CFS",
                        ),
                        legal_entity=self_bundle.company_name,
                        subject_scope=f"영업이익률·{period}·{scope} 동일조건 비교",
                        relationship_or_action="수익성 차이 비교",
                        claim=claim,
                        claim_type=COMPETITIVE_COMPARISON_CLAIM_TYPE,
                        section_owner=COMPETITIVE_SECTION_ID,
                        time_state="standing",
                        as_of=period.split("~", 1)[-1],
                        source_id=self_source.source_id,
                        source_type=self_source.source_type,
                        source_title=self_source.title,
                        source_publisher=self_source.publisher,
                        source_host=self_source.host,
                        source_url=self_source.url,
                        source_document_id=self_source.document_id,
                        location=self_source.location,
                        status="verified",
                        fact_status="actual",
                        verification_status="verified",
                        state_evidence=self_evidence,
                        source_date=(
                            self_source.published_at
                            or self_source.disclosed_at
                            or self_source.collected_at
                        ),
                        evidence_support_terms=["매출액", "영업이익"],
                        raw_value=(
                            f"{self_revenue.value};{self_operating.value};"
                            f"{comparator_revenue.value};{comparator_operating.value}"
                        ),
                        calculation=(
                            "양사 영업이익÷매출액×100 후 자사 영업이익률에서 "
                            "비교사 영업이익률을 차감"
                        ),
                        display_value=display_value,
                        rounding_rule=(
                            "각 영업이익률과 차이를 %·%p 소수 첫째 자리 "
                            "ROUND_HALF_UP"
                        ),
                        numeric_checks=[
                            f"{self_revenue.value}|1|0|{self_revenue.value}",
                            f"{self_operating.value}|1|0|{self_operating.value}",
                            f"{comparator_revenue.value}|1|0|{comparator_revenue.value}",
                            f"{comparator_operating.value}|1|0|{comparator_operating.value}",
                            f"{self_operating.value}|{Decimal(self_revenue.value) / Decimal(100)}|1|{self_margin:.1f}",
                            f"{comparator_operating.value}|{Decimal(comparator_revenue.value) / Decimal(100)}|1|{comparator_margin:.1f}",
                            f"{difference:.1f}|1|1|{difference:.1f}",
                        ],
                        limitations=(
                            "한 사업연도의 계산 수익성 차이이며 제품 경쟁력·지속적 "
                            "경쟁우위·원인 판단으로 해석하지 않음"
                        ),
                        limitation=(
                            "한 사업연도의 계산 수익성 차이이며 제품 경쟁력·지속적 "
                            "경쟁우위·원인 판단으로 해석하지 않음"
                        ),
                        supports_causality=False,
                        comparison_target=comparator.company_name,
                        comparison_metric="영업이익률",
                        comparison_definition=definition,
                        comparison_basis=comparison_basis(candidate),
                        comparison_period=period,
                        comparison_scope=scope,
                        comparison_judgment=(
                            "competitive_advantage"
                            if signed_difference > 0
                            else "operating_characteristic"
                        ),
                        comparator_source_id=comparator_source.source_id,
                        comparator_state_evidence=comparator_evidence,
                        comparator_evidence_support_terms=[
                            "매출액",
                            "영업이익",
                        ],
                        comparison_conditions=_comparison_conditions(
                            context,
                            period=period,
                            definition=definition,
                            accounting_scope=scope,
                        ),
                    )
                    fact = replace(fact, evidence_binding=fact_evidence_binding(fact))
                    facts.append(fact)
                    prose_lines.append((claim, str(self_source.number)))
                    used_axes.add("profitability")

        # 규모는 수익성과 다른 보조 축으로 매출 한 건만 허용한다.
        if (
            "profitability" in used_axes
            and "scale" not in used_axes
            and revenue_pair is not None
            and len(facts) < MAX_COMPARISON_FACTS
        ):
            self_row, comparator_row = revenue_pair
            scope_label = (
                "연결재무제표" if self_row.scope_code == "CFS" else "별도재무제표"
            )
            scope = f"{scope_label}({self_row.scope_code})"
            self_evidence = self_payload
            comparator_evidence = comparator_payload
            self_source = register_source(
                self_bundle,
                self_row,
                role="self",
                evidence=self_evidence,
            )
            comparator_source = register_source(
                comparator,
                comparator_row,
                role="comparator",
                evidence=comparator_evidence,
            )
            ratio = _ratio(self_row.value, comparator_row.value)
            ratio_text = f"{ratio:.1f}배"
            claim = comparison_scale_claim(
                comparison_scope=scope,
                comparison_target=comparator.company_name,
                ratio_text=ratio_text,
            )
            definition = _definition(self_row)
            if definition != _definition(comparator_row):
                continue
            fact = FactRecord(
                fact_id=_fact_id(
                    self_bundle.corp_code,
                    comparator.corp_code,
                    "revenue_scale",
                    self_row.period,
                    self_row.scope_code,
                ),
                legal_entity=self_bundle.company_name,
                subject_scope=f"매출 규모·{self_row.period}·{scope} 동일조건 비교",
                relationship_or_action="매출 규모 차이 비교",
                claim=claim,
                claim_type=COMPETITIVE_COMPARISON_CLAIM_TYPE,
                section_owner=COMPETITIVE_SECTION_ID,
                time_state="standing",
                as_of=self_row.period.split("~", 1)[-1],
                source_id=self_source.source_id,
                source_type=self_source.source_type,
                source_title=self_source.title,
                source_publisher=self_source.publisher,
                source_host=self_source.host,
                source_url=self_source.url,
                source_document_id=self_source.document_id,
                location=self_source.location,
                status="verified",
                fact_status="actual",
                verification_status="verified",
                state_evidence=self_evidence,
                source_date=(
                    self_source.published_at
                    or self_source.disclosed_at
                    or self_source.collected_at
                ),
                evidence_support_terms=[self_row.scope_code, "매출액"],
                raw_value=f"{self_row.value};{comparator_row.value}",
                calculation="자사 매출액÷비교사 매출액 = " + ratio_text,
                display_value=ratio_text,
                rounding_rule="자사 매출액÷비교사 매출액, 소수 첫째 자리 ROUND_HALF_UP",
                numeric_checks=[
                    f"{self_row.value}|1|0|{self_row.value}",
                    f"{comparator_row.value}|1|0|{comparator_row.value}",
                    f"{self_row.value}|{comparator_row.value}|1|{ratio:.1f}",
                ],
                limitations=(
                    "매출 규모와 운영 특성의 차이만 뜻하며 수익성·제품 경쟁력·"
                    "경쟁우위 판단으로 해석하지 않음"
                ),
                limitation=(
                    "매출 규모와 운영 특성의 차이만 뜻하며 수익성·제품 경쟁력·"
                    "경쟁우위 판단으로 해석하지 않음"
                ),
                supports_causality=False,
                comparison_target=comparator.company_name,
                comparison_metric="매출 규모",
                comparison_definition=definition,
                comparison_basis=comparison_basis(candidate),
                comparison_period=self_row.period,
                comparison_scope=scope,
                comparison_judgment="operating_characteristic",
                comparator_source_id=comparator_source.source_id,
                comparator_state_evidence=comparator_evidence,
                comparator_evidence_support_terms=[self_row.scope_code, "매출액"],
                comparison_conditions=_comparison_conditions(
                    context,
                    period=self_row.period,
                    definition=definition,
                    accounting_scope=scope,
                ),
            )
            fact = replace(fact, evidence_binding=fact_evidence_binding(fact))
            facts.append(fact)
            prose_lines.append((claim, str(self_source.number)))
            used_axes.add("scale")

        if len(facts) >= MAX_COMPARISON_FACTS:
            break

    if "profitability" not in used_axes:
        raise ComparisonBlockedError(
            [
                *failure_reasons,
                (
                    "양사 공식 원문에서 동일 완료 사업연도·연결범위의 매출액과 "
                    "영업이익으로 유의미한 영업이익률 차이를 만들지 못했습니다"
                ),
            ]
        )

    spec = SECTION_BY_ID[COMPETITIVE_SECTION_ID]
    section = ReportSection(
        cell=spec.section_id,
        title=spec.title,
        display_number=spec.display_number,
        tag=spec.tag,
        prose_lines=prose_lines,
        fact_ids=[fact.fact_id for fact in facts],
    )
    return ComparisonBuildResult(
        section=section,
        facts=tuple(facts),
        sources=tuple(sources_by_id.values()),
        candidates=candidates,
    )
