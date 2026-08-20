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
import unicodedata
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Callable, Iterable, Mapping, Sequence

from src.features.business_candidate.dart_identity import DartCompanyRecord
from src.features.pipeline.port import FactRecord, Report, ReportSection
from src.features.provenance.sources import (
    Source,
    SourceKind,
    evidence_text_hash,
    seal_collected_source,
)
from src.features.report_standard.constants import SECTION_BY_ID
from src.features.report_standard.publish import fact_evidence_binding


COMPETITIVE_SECTION_ID = "competitive_position"
MAX_COMPARATORS = 3
MAX_COMPARISON_FACTS = 3

# 단순히 고객이나 파트너로 한 번 언급된 회사를 경쟁사로 바꾸지 않는다. 공식 원문이
# 경쟁 관계를 직접 표현한 경우에만 후보가 된다.
_COMPETITION_MARKERS = (
    "경쟁사",
    "경쟁업체",
    "경쟁 관계",
    "경쟁관계",
    "동종업체",
    "동종 업체",
    "경합",
    "시장점유율",
    "시장 점유율",
)
_PRODUCT_MARKERS = ("제품", "서비스", "품목", "브랜드", "기술")
_CUSTOMER_MARKERS = ("고객", "수요처", "납품처", "발주처")
_LEGAL_MARKERS = ("주식회사", "유한회사", "(주)", "㈜", "corp.", "co.,ltd.", "co., ltd.")
_PERIOD_NUMBER = re.compile(r"20\d{2}|\d{1,2}")
_INTEGER = re.compile(r"^-?[\d,]+$")
_ANNUAL_REPORT_CODE = "11011"
_WON_CURRENCIES = frozenset({"KRW", "WON", "원"})

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


@dataclass(frozen=True)
class CandidateEvidence:
    """확정된 자사 사실이 공식적으로 지목한 비교 후보."""

    record: DartCompanyRecord
    overlap_dimension: str
    evidence_fact_id: str
    evidence_source_id: str
    evidence_text: str


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


def _normalized(value: object) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).casefold().split())


_CONTEXT_MARKERS: Mapping[str, tuple[str, ...]] = {
    "customer": ("고객", "수요처", "납품처", "발주처"),
    "product": ("제품", "서비스", "품목", "브랜드", "장비", "소재"),
    "market": ("시장", "산업", "지역"),
}
_CONTEXT_STOP = frozenset(
    {
        "회사는",
        "회사의",
        "회사",
        "공식",
        "사업보고서",
        "연결재무제표",
        "별도재무제표",
        "대상",
        "대상으로",
        "기반",
        "고객",
        "수요처",
        "납품처",
        "발주처",
        "제품",
        "서비스",
        "품목",
        "브랜드",
        "장비",
        "소재",
        "시장",
        "산업",
        "지역",
        "공급",
        "공급한다",
        "판매",
        "판매한다",
        "제공",
        "제공한다",
        "공시",
        "공시한다",
    }
)


def _context_lexeme(token: str) -> str:
    """조사만 다른 같은 범위어를 맞추고 일반 축 표지는 버릴 수 있게 한다."""

    clean = token.casefold()
    for suffix in ("으로", "에서", "에게", "부터", "까지", "과", "와", "을", "를", "은", "는", "이", "가", "의", "에"):
        if len(clean) >= len(suffix) + 2 and clean.endswith(suffix):
            clean = clean[: -len(suffix)]
            break
    return clean


def _axis_terms(text: str, markers: tuple[str, ...]) -> set[str]:
    """축 표지가 있는 문장 안에서 실제 범위어만 결정론적으로 뽑는다."""

    clauses = re.split(r"[.!?\n]", _normalized(text))
    relevant = [clause for clause in clauses if any(marker in clause for marker in markers)]
    terms: set[str] = set()
    for clause in relevant:
        for token in re.findall(r"[가-힣A-Za-z]{2,}", clause):
            lexeme = _context_lexeme(token)
            if lexeme and lexeme not in _CONTEXT_STOP:
                terms.add(lexeme)
    return terms


def _shared_context(
    self_bundle: OfficialCompanyBundle,
    comparator_bundle: OfficialCompanyBundle,
) -> dict[str, str]:
    """양사 원문에 모두 직접 나타나는 고객·제품·시장 범위를 구조화한다."""

    context: dict[str, str] = {}
    company_terms = {
        token
        for name in (self_bundle.company_name, comparator_bundle.company_name)
        for token in re.findall(r"[가-힣A-Za-z]{2,}", _normalized(name))
    }
    for axis, markers in _CONTEXT_MARKERS.items():
        common = (
            _axis_terms(self_bundle.official_text, markers)
            & _axis_terms(comparator_bundle.official_text, markers)
        ) - company_terms
        # 표지 자체 하나만 같다고 동일 범위로 보지 않는다. 서로 다른 공통어 두 개가
        # 있어야 고객/제품/시장 범위를 임의로 지어내지 않고 고정할 수 있다.
        chosen = sorted(term for term in common if len(term) >= 2)
        if len(chosen) < 2:
            return {}
        context[axis] = "·".join(chosen[:6])
    return context


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


def _name_aliases(name: str) -> tuple[str, ...]:
    raw = _normalized(name)
    stripped = raw
    for marker in _LEGAL_MARKERS:
        stripped = stripped.replace(_normalized(marker), " ")
    stripped = " ".join(stripped.split()).strip(" ,.-")
    aliases = [raw, stripped]
    # 경쟁 관계 표현이 같은 확정 문장에 있어야 하므로 한화·효성처럼 두 글자인
    # 실제 상호도 허용한다. 한 글자 별칭만 버려 보통명사 오탐을 막는다.
    return tuple(dict.fromkeys(alias for alias in aliases if alias and (alias == raw or len(alias) >= 2)))


def _official_source(source: Source | None) -> bool:
    return bool(source is not None and source.is_canonical_official)


def _corporate_core(value: str) -> str:
    core = _normalized(value)
    for marker in _LEGAL_MARKERS:
        core = core.replace(_normalized(marker), " ")
    return re.sub(r"[^0-9a-z가-힣]", "", core)


def _overlap_dimension(text: str) -> str:
    if any(marker in text for marker in _CUSTOMER_MARKERS):
        return "고객 겹침"
    if any(marker in text for marker in _PRODUCT_MARKERS):
        return "제품·서비스 겹침"
    return "시장 겹침"


def discover_candidates(
    report: Report,
    catalog: Iterable[DartCompanyRecord],
    *,
    self_corp_code: str,
    limit: int = MAX_COMPARATORS,
) -> tuple[CandidateEvidence, ...]:
    """확정 1~8장 공식 사실에서 직접 언급된 DART 법인만 비교 후보로 고른다."""

    sources = {
        source.source_id: source
        for source in report.citations
        if isinstance(source, Source)
        and _official_source(source)
        and _corporate_core(source.publisher) == _corporate_core(report.company)
    }
    evidence_rows: list[tuple[str, str, str]] = []
    for fact in report.fact_records:
        if fact.section_owner == COMPETITIVE_SECTION_ID or fact.source_id not in sources:
            continue
        text = " ".join(part for part in (fact.claim, fact.state_evidence) if str(part).strip())
        normalized = _normalized(text)
        if not any(_normalized(marker) in normalized for marker in _COMPETITION_MARKERS):
            continue
        evidence_rows.append((fact.fact_id, fact.source_id, text))

    if not evidence_rows:
        return ()

    matches: list[tuple[int, int, str, CandidateEvidence]] = []
    self_code = str(self_corp_code).strip()
    for record in catalog:
        corp_code = str(record.corp_code or "").strip()
        if not corp_code or corp_code == self_code:
            continue
        aliases = _name_aliases(record.corp_name)
        if not aliases:
            continue
        for evidence_index, (fact_id, source_id, text) in enumerate(evidence_rows):
            normalized_text = _normalized(text)
            matching_aliases = [alias for alias in aliases if alias in normalized_text]
            if not matching_aliases:
                continue
            evidence = CandidateEvidence(
                    record=record,
                    overlap_dimension=_overlap_dimension(text),
                    evidence_fact_id=fact_id,
                    evidence_source_id=source_id,
                    evidence_text=text,
                )
            # 같은 문장에 짧은 계열명과 정확한 법인명이 함께 맞으면 더 긴 공식
            # 이름을 우선한다. DART XML의 저장 순서가 후보 순위를 결정하지 않는다.
            matches.append(
                (
                    evidence_index,
                    -max(len(alias) for alias in matching_aliases),
                    corp_code,
                    evidence,
                )
            )
            break

    found: list[CandidateEvidence] = []
    seen_codes: set[str] = set()
    cap = max(1, min(int(limit), MAX_COMPARATORS))
    for _evidence_index, _alias_length, corp_code, evidence in sorted(matches):
        if corp_code in seen_codes:
            continue
        seen_codes.add(corp_code)
        found.append(evidence)
        if len(found) >= cap:
            break
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
    return (
        bool(bundle.official_text.strip())
        and is_annual
        and bool(receipt_number)
        and bool(_source_date(filing.get("rcept_dt")))
        and _filing_year(filing) == _period_year(period)
        and report_code == _ANNUAL_REPORT_CODE
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
) -> ComparisonBuildResult:
    """확정된 1~8장 뒤, 양사 공식 원문이 맞는 비교만 9장으로 만든다."""

    self_payload = _bundle_evidence(self_bundle)
    if (
        not self_bundle.official_text.strip()
        or not _observations(self_bundle.financials)
        or not self_payload
    ):
        raise ComparisonBlockedError("자사 공식 원문과 비교 가능한 주요계정이 모두 필요합니다")

    candidates = discover_candidates(
        report,
        catalog,
        self_corp_code=self_bundle.corp_code,
        limit=max_comparators,
    )
    if not candidates:
        raise ComparisonBlockedError(
            "확정된 1~8장 공식 사실에서 경쟁 관계와 DART 법인을 함께 확인하지 못했습니다"
        )

    next_number = max(
        (source.number for source in report.citations if isinstance(source, Source)),
        default=0,
    ) + 1
    facts: list[FactRecord] = []
    prose_lines: list[tuple[str, str]] = []
    sources_by_id: dict[str, Source] = {}
    failure_reasons: list[str] = []
    used_axes: set[str] = set()

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
        except Exception as exc:  # 공급자 실패도 근거 없음과 섞지 않고 내부 사유에 남긴다.
            failure_reasons.append(
                f"{candidate.record.corp_name}: 공식 원문 수집 실패({type(exc).__name__})"
            )
            continue
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
                    claim = (
                        f"{scope_label}(CFS) 매출액과 영업이익으로 계산한 영업이익률은 "
                        f"{comparator.company_name}보다 {difference:.1f}%p {direction}."
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
                        claim_type="competitive_comparison",
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
                        comparison_basis=(
                            f"{candidate.overlap_dimension}: {candidate.evidence_fact_id}/"
                            f"{candidate.evidence_source_id}; 양사 DART 연간 연결 주요계정"
                        ),
                        comparison_period=period,
                        comparison_scope=scope,
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
            claim = (
                f"{scope_label}({self_row.scope_code}) 매출액 규모는 {comparator.company_name} 대비 "
                f"{ratio_text}였다. 이는 규모 차이이며 경쟁우위 판정이 아니다."
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
                claim_type="competitive_comparison",
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
                comparison_basis=(
                    f"{candidate.overlap_dimension}: {candidate.evidence_fact_id}/"
                    f"{candidate.evidence_source_id}; 양사 DART 연간 주요계정"
                ),
                comparison_period=self_row.period,
                comparison_scope=scope,
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
