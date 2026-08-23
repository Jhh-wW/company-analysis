"""canonical 보고서 출고 게이트와 공개본 정규화."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from src.core.citations import citation_number
from src.features.pipeline.market_contract import (
    MARKET_STAGE_EVIDENCE_PATTERNS,
    MARKET_STAGES,
)
from src.features.pipeline.port import FactRecord, Grade, Report, ReportSection
from src.features.pipeline.section567_contract import (
    FUTURE_OPERATION_PATTERN,
    PLAN_EXECUTION_SIGNAL_PATTERN,
    PLAN_STATUSES,
    PLAN_TIMING_PATTERN,
    RELATIONSHIP_TYPE_PATTERNS,
    RELATIONSHIP_TYPES_BY_CLAIM,
    SUPPLIER_INBOUND_PATTERN,
    VALUE_CHAIN_STAGE_PATTERNS,
    excerpts_are_in_distinct_clauses,
    excerpts_overlap,
    expected_plan_status,
    has_executed_current_distribution_partnership,
    has_plan_condition,
    has_current_operating_role,
    internal_operation_is_company_controlled,
    is_company_stated_plan_effect,
    is_observed_initial_signal,
    is_objective_next_check_metric,
    looks_like_customer_outbound,
    ownership_is_company_held,
    plan_is_active,
    plan_timing_has_passed,
    response_is_bound_to_issue,
)
from src.features.provenance.sources import (
    Source,
    SourceKind,
    evidence_text_hash,
    exact_evidence_text_hash,
    is_canonical_official_with_registry,
    official_domain_attestation_problem,
    official_web_currentness_is_usable,
    source_type_is_official_ir,
    source_type_is_official_web,
)
from src.features.report_standard.constants import (
    CANONICAL_CLAIM_TYPES_BY_SECTION,
    CANONICAL_SCHEMA_VERSION,
    CANONICAL_SECTION_IDS,
    COMPARISON_JUDGMENTS,
    CONDITIONAL_SECTION_IDS,
    CONDITIONAL_SECTION_SHORTFALL_REASONS,
    INTERNAL_ONLY_CLAIM_TYPES_BY_SECTION,
    REQUIRED_SECTION_IDS,
    SECTION_BY_ID,
    SUMMARY_VERIFICATION_STATUS,
    SECTION_SPECS,
    SECTION_TIME_STATES,
    SUMMARY_MAX_ITEMS,
    SUMMARY_MIN_ITEMS,
    TIME_SECTION_IDS,
)
from src.features.report_standard.section_content import section_content_blocks
from src.features.spanselect.canonical import (
    PORTFOLIO_STAGES,
    PRIORITY_SIGNAL_PATTERNS,
)
from src.shared.comparison_candidate_basis import (
    COMPARISON_SOURCE_BASIS_VERSION,
    comparison_candidate_sentence_matches,
    comparison_comparator_source_id,
    comparison_dart_profile_attestation_is_valid,
    comparison_overlap_dimension,
    comparison_source_basis_is_allowed,
    comparison_source_overlap_dimension,
    comparison_source_sentence_has_self_subject,
    comparison_source_sentence_has_marker,
    parse_comparison_basis,
    parse_comparison_basis_v1,
    parse_comparison_source_basis_v2,
)


_FORBIDDEN_JOB_TOPIC = re.compile(
    r"직무|KPI|핵심성과지표|자소서|자기소개서|면접",
    re.IGNORECASE,
)
# 회사가 파는 '복지 플랫폼'은 사업 정보다. 지원자 개인의 처우를 뜻하는
# 평균 보수·근속·복리후생만 문맥을 좁혀 차단한다.
_FORBIDDEN_PREFERENCE_TOPIC = re.compile(
    r"평균\s*(?:급여|보수|연봉)|(?:임직원|직원|근로자|지원자).{0,16}"
    r"(?:급여|보수|연봉|근속(?:연수)?|복리후생|복지\s*(?:혜택|제도))|"
    r"근속\s*연수|복리\s*후생|연봉\s*(?:수준|정보)",
    re.IGNORECASE,
)
_FORBIDDEN_META = (
    re.compile(r"(?:이|본)\s*보고서.{0,24}(?:AI|인공지능|작성|생성|검증)"),
    re.compile(r"(?:AI|인공지능)\s*(?:가|로|를)?\s*(?:작성|생성|검증)"),
    re.compile(r"(?:작성|생성|검증)\s*(?:방법|과정|기준|절차|에이전트|상태)"),
    re.compile(r"(?:자료|문장|본문|내용).{0,16}(?:없어|부족|찾지 못|확인하지 못|보류)"),
)
_CAUSAL_PATTERN = re.compile(
    r"개선(?:했|되|시켰|에\s*기여)|기여(?:했|한|한다)|영향(?:을|이)|좌우(?:했|한|한다)|"
    r"견인(?:했|한|한다)|덕분|때문|(?:으)?로\s*인해|결과로|이에\s*따라|"
    r"유발(?:했|한|한다)|초래(?:했|한|한다)|증가시켰|감소시켰|끌어올렸|낮췄|"
    r"회복시켰|수익으로\s*전환|비용을\s*흡수|"
    r"(?:편입|확대|도입|투자|인수|합병)(?:으)?로",
    re.IGNORECASE,
)
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_NUMBER_TOKEN = re.compile(r"(?<![A-Za-z])[-+]?\d[\d,]*(?:\.\d+)?(?![A-Za-z])")
_NUMERIC_CHECK = re.compile(
    r"^\s*(?P<raw>[-+]?\d[\d,]*(?:\.\d+)?)\s*\|\s*"
    r"(?P<divisor>\d[\d,]*(?:\.\d+)?)\s*\|\s*"
    r"(?P<places>\d+)\s*\|\s*"
    r"(?P<display>[-+]?\d[\d,]*(?:\.\d+)?)\s*$"
)
_PUBLIC_NUMBER = re.compile(r"^[-+]?\d[\d,]*(?:\.\d+)?$")
_PUBLIC_AMOUNT_WITH_UNIT = re.compile(
    r"[-+]?\d[\d,]*(?:\.\d+)?\s*(?:억\s*)?원"
)
_SELF_PUBLISHED_SOURCE_TYPES = frozenset(
    {
        "공식 공시",
        "공식 공시·재무 api",
        "공식 계획",
        "회사 공식 ir",
        "공식 ir",
        "회사 공식 웹",
        "공식 웹",
        "회사 공식 자료",
    }
)
_STATUS_STAGE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("repeated_revenue", re.compile(r"반복\s*매출")),
    # 일반 실적표의 `매출액`은 상태 전이 표현이 아니다. 고객 검증/납품 이후
    # 상업화 단계로서 매출이 *발생·인식*했다는 문장만 별도 상태로 본다.
    (
        "revenue",
        re.compile(
            r"매출\s*(?:발생|인식|전환|개시)|"
            r"(?:발생|인식|확인)(?:한|된)?\s*매출"
        ),
    ),
    ("delivery", re.compile(r"납품")),
    ("main_contract", re.compile(r"본계약|계약")),
    ("mou", re.compile(r"(?<![A-Za-z])MOU(?![A-Za-z])|양해각서", re.IGNORECASE)),
    ("customer_validation", re.compile(r"고객\s*검증|검증\s*(?:중|단계|완료)?")),
    ("development_complete", re.compile(r"개발\s*완료")),
    ("development_in_progress", re.compile(r"개발\s*(?:중|진행\s*중)")),
)
_CLAIM_NOISE = re.compile(r"[\W_]+", re.UNICODE)
_COMPLETED_MARKER = re.compile(
    r"완료|마쳤|종료|체결|편입|포함됐|포함\s*완료|인수했|설립했|"
    r"구축했|도입했|출시했|가동했|납품했|발생했|인식했"
)
_CONDITIONAL_COMPLETION_MARKER = re.compile(
    r"(?:인허가|허가|승인).{0,16}(?:완료|취득).{0,12}(?:조건|전제|후|시|전)"
)
_COMPLETED_PREREQUISITE_MARKER = re.compile(
    r"(?:이사회|규제기관|규제|인허가|허가|승인|파트너|필수\s*계약).{0,24}"
    r"(?:승인(?:했|됐|된)|의결(?:했|됐|된)|완료(?:했|됐|된)|"
    r"확정(?:했|됐|된)|취득(?:했|됐|한)|체결(?:했|됐|된))"
)
_UNFINISHED_MARKER = re.compile(
    r"계획|예정|목표|향후|로드맵|추진\s*중|개발\s*중|준비\s*중|"
    r"협의(?:\s*중|·시험\s*단계)|시험\s*단계"
)
_UNRESOLVED_MARKER = re.compile(
    r"미해결|미확인|확인되지\s*않|해결되지\s*않|부족|위험|과제|문제|"
    r"난항|의존|변동|협의·시험\s*단계|본계약.{0,12}(?:않|전)"
)
_RESOLVED_ISSUE_MARKER = re.compile(
    r"(?:문제|과제|위험).{0,12}(?:해결|해소|종료)\s*(?:완료|했|됐)|"
    r"(?:완전히|모두)\s*(?:해결|해소)|"
    r"더\s*이상.{0,20}(?:문제|과제|위험|리스크).{0,12}(?:아니|없)|"
    r"(?:문제|과제|위험|리스크).{0,20}더\s*이상.{0,12}(?:아니|없)|"
    r"no\s+longer.{0,24}(?:problem|issue|risk)|"
    r"(?:problem|issue|risk).{0,24}no\s+longer",
    re.IGNORECASE,
)
_STARTED_RESPONSE_MARKER = re.compile(
    r"착수|진행\s*중|추진\s*중|대응\s*중|협의\s*중|개선\s*중|"
    r"준비\s*중|도입(?:했|됐|\s*완료|\s*착수|\s*중)|"
    r"투자(?:했|\s*집행|\s*착수|\s*중)|"
    r"체결(?:했|됐|\s*완료|\s*됨|[.!?]?$)|"
    r"시험(?:을)?\s*마쳤|시험\s*완료|운영\s*중"
)
_PLAN_MARKER = re.compile(
    r"계획|예정|목표|향후|로드맵|방침|추진(?:할|하려|하고자)|"
    r"확대(?:할|한다는)|구축(?:할|한다는)|도입(?:할|한다는)|개발(?:할|한다는)"
)
_ALREADY_COMPLETED_PLAN_MARKER = re.compile(
    r"이미.{0,20}(?:모두|전부)?.{0,12}(?:실현|달성|이행|완수|완료|가동|출시|도입)|"
    r"(?:계획|목표|로드맵).{0,24}(?:이미|모두|전부).{0,16}"
    r"(?:실현|달성|이행|완수|완료)|"
    r"already.{0,24}(?:realized|realised|achieved|implemented|completed)",
    re.IGNORECASE,
)
_COMPARISON_CONDITION_KEYS = frozenset(
    {
        "customer",
        "product",
        "market",
        "self_period",
        "comparator_period",
        "self_definition",
        "comparator_definition",
        "self_accounting_scope",
        "comparator_accounting_scope",
    }
)


def _priority_signal_clauses(value: str) -> tuple[str, ...]:
    """추진 신호가 서로 다른 근거 사건에서 나왔는지 볼 최소 절 단위."""

    return tuple(
        clause.strip()
        for clause in re.split(
            r"(?:[.!?;:\n]+|(?<=\S)(?:와|과|하고|하며|했고|하면서)\s+|"
            r"\s+(?:및|그리고|또한)\s+)",
            str(value or ""),
        )
        if clause.strip()
    )


def _has_independent_priority_signal_clauses(
    signals: list[str], evidence: str
) -> bool:
    """서로 다른 신호 둘을 서로 다른 근거 절에 배정할 수 있어야 한다."""

    clauses = _priority_signal_clauses(evidence)
    supported: dict[str, tuple[int, ...]] = {}
    for signal in dict.fromkeys(signals):
        pattern = PRIORITY_SIGNAL_PATTERNS.get(signal)
        supported[signal] = tuple(
            index
            for index, clause in enumerate(clauses)
            if pattern is not None and pattern.search(clause) is not None
        )

    ordered = sorted(supported, key=lambda signal: len(supported[signal]))

    def assign(index: int, used: frozenset[int]) -> bool:
        if index >= len(ordered):
            return len(used) >= 2
        signal = ordered[index]
        if assign(index + 1, used):
            return True
        return any(
            clause_index not in used
            and assign(index + 1, used | {clause_index})
            for clause_index in supported[signal]
        )

    return assign(0, frozenset())


@dataclass(frozen=True)
class PublishValidation:
    """출고 가능 여부와 공개본에 남길 본문 장."""

    publishable: bool
    reasons: tuple[str, ...] = ()
    included_section_ids: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return self.publishable

    @property
    def ok(self) -> bool:
        return self.publishable


class PublishBlockedError(ValueError):
    """canonical 출고 조건을 만족하지 못한 보고서."""

    def __init__(self, validation: PublishValidation):
        self.validation = validation
        super().__init__("; ".join(validation.reasons) or "canonical 출고가 차단되었습니다")


def _normalized(value: str) -> str:
    return " ".join(str(value or "").split()).casefold()


def _is_self_published_company_source(
    source: Source | None,
    *,
    legal_entity: str,
    registered_sources: tuple[Source, ...],
) -> bool:
    """분석 법인이 책임지는 공시·공식 웹 원문인지 닫힌 조건으로 확인한다."""

    if source is None or _corporate_name(source.publisher) != _corporate_name(
        legal_entity
    ):
        return False
    if not is_canonical_official_with_registry(source, registered_sources):
        return False
    return source.kind is SourceKind.FILING or source_type_is_official_web(
        source.source_type
    )


def _compact(value: str) -> str:
    return _CLAIM_NOISE.sub("", _normalized(value))


def _corporate_name(value: str) -> str:
    """(주)·주식회사처럼 표기만 다른 법인명을 같은 이름으로 본다."""

    cleaned = re.sub(r"\(\s*주\s*\)|㈜|주식회사", "", str(value or ""), flags=re.IGNORECASE)
    return _compact(cleaned)


def _source_date(source: Source) -> str:
    """원문 자체의 날짜를 반환한다. 수집일은 원문 날짜가 없을 때만 쓴다."""

    if source_type_is_official_ir(source.source_type):
        return ""
    if not official_web_currentness_is_usable(
        source_type=source.source_type,
        url=source.url,
        published_at=source.published_at,
        disclosed_at=source.disclosed_at,
        collected_at=source.collected_at,
    ):
        return ""
    return (source.published_at or source.disclosed_at or source.collected_at).strip()


def _parse_iso_date(value: str) -> date | None:
    try:
        if not _ISO_DATE.fullmatch(str(value or "").strip()):
            return None
        return date.fromisoformat(str(value).strip())
    except ValueError:
        return None


def _binding_digest(payload: object) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def fact_evidence_binding(fact: FactRecord) -> str:
    """검증 뒤 claim이나 원문 근거가 바뀌면 무효가 되는 결정론적 지문."""

    return _binding_digest(
        {
            "fact_id": fact.fact_id,
            "legal_entity": fact.legal_entity,
            "subject_scope": fact.subject_scope,
            "relationship_or_action": fact.relationship_or_action,
            "claim": fact.claim,
            "claim_type": fact.claim_type,
            "section_owner": fact.section_owner,
            "time_state": fact.time_state,
            "as_of": fact.as_of,
            "source_id": fact.source_id,
            "source_type": fact.source_type,
            "source_title": fact.source_title,
            "source_publisher": fact.source_publisher,
            "source_host": fact.source_host,
            "source_url": fact.source_url,
            "source_document_id": fact.source_document_id,
            "source_date": fact.source_date,
            "location": fact.location,
            "state_evidence": fact.state_evidence,
            "fact_status": fact.fact_status,
            "verification_status": fact.verification_status,
            "evidence_support_terms": list(fact.evidence_support_terms),
            "raw_value": fact.raw_value,
            "calculation": fact.calculation,
            "display_value": fact.display_value,
            "rounding_rule": fact.rounding_rule,
            "numeric_checks": list(fact.numeric_checks),
            "fiscal_year": fact.fiscal_year,
            "event_date": fact.event_date,
            "market_priority": fact.market_priority,
            "market_stage": fact.market_stage,
            "market_observation": fact.market_observation,
            "product_role": fact.product_role,
            "portfolio_stage": fact.portfolio_stage,
            "revenue_model_fact_id": fact.revenue_model_fact_id,
            "priority_signals": list(fact.priority_signals),
            "basis_fact_ids": list(fact.basis_fact_ids),
            "response_to_fact_id": fact.response_to_fact_id,
            "response_action": fact.response_action,
            "initial_signal": fact.initial_signal,
            "next_check_metric": fact.next_check_metric,
            "plan_status": fact.plan_status,
            "plan_timing": fact.plan_timing,
            "plan_condition": fact.plan_condition,
            "plan_expected_effect": fact.plan_expected_effect,
            "plan_execution_signal": fact.plan_execution_signal,
            "value_chain_stage": fact.value_chain_stage,
            "relationship_type": fact.relationship_type,
            "supports_causality": fact.supports_causality,
            "causal_subject": fact.causal_subject,
            "causal_mechanism": fact.causal_mechanism,
            "causal_outcome": fact.causal_outcome,
            "causal_evidence": fact.causal_evidence,
            "comparison_target": fact.comparison_target,
            "comparison_metric": fact.comparison_metric,
            "comparison_definition": fact.comparison_definition,
            "comparison_basis": fact.comparison_basis,
            "comparison_period": fact.comparison_period,
            "comparison_scope": fact.comparison_scope,
            "comparison_judgment": fact.comparison_judgment,
            "comparator_source_id": fact.comparator_source_id,
            "comparator_state_evidence": fact.comparator_state_evidence,
            "comparator_evidence_support_terms": list(
                fact.comparator_evidence_support_terms
            ),
            "comparison_conditions": dict(sorted(fact.comparison_conditions.items())),
            "limitations": fact.limitations,
            "limitation": fact.limitation,
        }
    )


def summary_evidence_text(fact_ids: list[str], facts: dict[str, FactRecord]) -> str:
    """요약 검증기가 보는 정본 근거 묶음."""

    return "\n".join(f"{fact_id}: {facts[fact_id].claim}" for fact_id in fact_ids)


def summary_verification_binding(
    text: str,
    section_id: str,
    fact_ids: list[str],
    evidence_text: str,
    verification_status: str,
    support_terms: list[str],
) -> str:
    """검증된 본문 재사용 요약과 그 근거의 사후 변조를 감지한다."""

    return _binding_digest(
        {
            "text": text,
            "section_id": section_id,
            "fact_ids": list(fact_ids),
            "evidence_text": evidence_text,
            "verification_status": verification_status,
            "support_terms": list(support_terms),
        }
    )


def _forbidden_text_problem(text: str) -> str:
    """지원자용 회사 사실이 아닌 직무 정보와 제작 메타문구를 찾는다."""

    if match := _FORBIDDEN_JOB_TOPIC.search(str(text or "")):
        return f"제외 대상 표현 '{match.group(0)}'이 있습니다"
    if match := _FORBIDDEN_PREFERENCE_TOPIC.search(str(text or "")):
        return f"제외 대상 표현 '{match.group(0)}'이 있습니다"
    if any(pattern.search(str(text or "")) for pattern in _FORBIDDEN_META):
        return "AI·작성·검증 관련 제작 메타문구가 있습니다"
    return ""


def _source_registry(report: Report) -> dict[str, Source]:
    registry: dict[str, Source] = {}
    for item in report.citations:
        if not isinstance(item, Source):
            continue
        source_id = item.source_id.strip()
        if source_id and item.is_canonical_valid:
            registry[source_id] = item
    return registry


def _source_document_identity(source: Source) -> tuple[str, str, str, str]:
    """같은 원문 조각을 새 source_id로 복제하지 못하게 하는 신원.

    하나의 사업보고서를 서로 다른 절·원문 조각으로 나눈 Source는 허용하되,
    URL·문서 ID·위치·원문 해시 묶음까지 같은 행의 ID만 바꾼 복제는 막는다.
    """

    raw_url = str(source.url or "").strip()
    try:
        parsed = urlsplit(raw_url)
        host = (parsed.hostname or "").casefold()
        port = f":{parsed.port}" if parsed.port is not None else ""
        path = parsed.path.rstrip("/") or "/"
        query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
        canonical_url = urlunsplit(
            ((parsed.scheme or "https").casefold(), host + port, path, query, "")
        )
    except (TypeError, ValueError):
        canonical_url = raw_url.casefold().rstrip("/")
    return (
        canonical_url,
        _normalized(source.document_id),
        _normalized(source.location),
        "|".join(sorted(source.evidence_hashes)),
    )


def _fact_core_is_complete(fact: FactRecord) -> bool:
    required = (
        fact.fact_id,
        fact.legal_entity,
        fact.subject_scope,
        fact.relationship_or_action,
        fact.claim,
        fact.claim_type,
        fact.section_owner,
        fact.time_state,
        fact.as_of,
        fact.source_id,
        fact.source_type,
        fact.source_title,
        fact.source_publisher,
        fact.source_host,
        fact.source_url,
        fact.source_document_id,
        fact.location,
        fact.fact_status,
        fact.verification_status,
        fact.state_evidence,
    )
    return all(str(value).strip() for value in required)


def _atomic_key(fact: FactRecord) -> tuple[str, ...]:
    """같은 사실을 다른 fact_id로 복제했는지 보는 안정적인 키."""

    event = fact.relationship_or_action or fact.claim
    value_or_status = fact.display_value or fact.raw_value or fact.fact_status
    return tuple(
        _normalized(value)
        for value in (
            fact.legal_entity,
            fact.subject_scope,
            event,
            fact.as_of,
            value_or_status,
        )
    )


def _semantic_duplicate_key(fact: FactRecord) -> tuple[str, ...]:
    """출처 ID를 복제해도 표현만 바꾼 같은 사실을 중복으로 잡는 키.

    ``source_id``와 원문 hash는 수집·인용의 신원이지 사실의 의미가 아니다.
    그것들을 키에 넣으면 같은 문서를 새 ID로 복제하는 것만으로 중복 검사를
    피할 수 있으므로 의미 키에서는 의도적으로 제외한다.
    """

    terms = sorted(
        {
            token.casefold()
            for token in re.findall(r"[가-힣A-Za-z]{2,}|[-+]?\d[\d,]*(?:\.\d+)?", fact.claim)
            if token.casefold() not in {"회사는", "회사의", "기준", "공식", "해당"}
        }
    )
    raw_values = sorted(
        str(value.normalize())
        for token in _NUMBER_TOKEN.findall(fact.raw_value)
        if (value := _decimal(token)) is not None
    )
    return (
        _corporate_name(fact.legal_entity),
        _compact(fact.subject_scope),
        _normalized(fact.claim_type),
        _normalized(fact.time_state),
        str(fact.fiscal_year or ""),
        _normalized(fact.event_date or fact.as_of),
        "|".join(raw_values),
        "|".join(terms),
    )


def _comparison_official_text(evidence: str) -> str:
    """비교 payload에서 공식 서술 원문만 꺼내고, 구형 원문은 그대로 쓴다."""

    raw = str(evidence or "").strip()
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return raw
    if not isinstance(payload, dict):
        return ""
    official_text = payload.get("official_text")
    return str(official_text or "").strip()


def _comparison_candidate_source_id(value: object) -> str:
    """v1/v2 근거의 후보 출처 ID. legacy/demo 문자열은 빈 값이다."""

    payload = parse_comparison_basis(value)
    return str((payload or {}).get("candidate_source_id") or "")


def _comparison_candidate_attester_source_id(value: object) -> str:
    """v2 후보의 자사 DART 신원 attester ID. v1에는 없다."""

    payload = parse_comparison_source_basis_v2(value)
    return str((payload or {}).get("self_attestation_source_id") or "")


def _comparison_candidate_basis_problems(
    fact: FactRecord,
    sources: dict[str, Source],
    facts: dict[str, FactRecord] | None = None,
    confirmed_candidate_fact_ids: set[str] | None = None,
    report_as_of: str = "",
) -> list[str]:
    """v1 1~8장 사실 또는 v2 봉인 공식 문장을 Source에서 역참조한다."""

    v1_payload = parse_comparison_basis_v1(fact.comparison_basis)
    v2_payload = parse_comparison_source_basis_v2(fact.comparison_basis)
    payload = v1_payload or v2_payload
    real_comparison = fact.fact_id.startswith("fact-compare-") and fact.source_id.startswith(
        "comparison-self-"
    )
    if payload is None:
        return (
            [
                f"[comparison] {fact.fact_id}: 실서비스 비교 후보 근거가 "
                "허용된 company-comparison 후보 근거에 결속되지 않았습니다"
            ]
            if real_comparison
            else []
        )

    problems: list[str] = []
    known_comparison_companies = tuple(
        dict.fromkeys(
            [
                fact.legal_entity,
                fact.comparison_target,
                payload["candidate_name"],
                *(
                    candidate.comparison_target
                    for candidate in (facts or {}).values()
                    if candidate.comparison_target
                ),
            ]
        )
    )
    expected_comparator_source_id = comparison_comparator_source_id(
        corp_code=payload["candidate_corp_code"],
        comparison_period=fact.comparison_period,
        comparison_scope=fact.comparison_scope,
    )
    if (
        not expected_comparator_source_id
        or fact.comparator_source_id != expected_comparator_source_id
    ):
        problems.append(
            f"[comparison] {fact.fact_id}: 후보 DART 고유번호가 실제 비교사 "
            "재무 Source ID와 결속되지 않았습니다"
        )

    candidate_source = sources.get(payload["candidate_source_id"])
    if candidate_source is None:
        problems.append(
            f"[comparison] {fact.fact_id}: 비교 후보 원문 source_id가 등록부에 없습니다"
        )
        return problems
    expected_document_id = (
        payload["filing_document_id"]
        if v1_payload is not None
        else payload["source_document_id"]
    )
    if candidate_source.document_id.strip() != expected_document_id:
        problems.append(
            f"[comparison] {fact.fact_id}: 비교 후보 Source 문서 ID가 근거와 다릅니다"
        )
    if (
        _normalized(candidate_source.publisher) != _normalized(fact.legal_entity)
        if v2_payload is not None
        else _corporate_name(candidate_source.publisher)
        != _corporate_name(fact.legal_entity)
    ):
        problems.append(
            f"[comparison] {fact.fact_id}: 비교 후보 원문의 발행 법인이 자사가 아닙니다"
        )
    if not is_canonical_official_with_registry(
        candidate_source, tuple(sources.values())
    ):
        problems.append(
            f"[comparison] {fact.fact_id}: 비교 후보 원문이 검증된 공식 자료가 아닙니다"
        )
    if not official_web_currentness_is_usable(
        source_type=candidate_source.source_type,
        url=candidate_source.url,
        published_at=candidate_source.published_at,
        disclosed_at=candidate_source.disclosed_at,
        collected_at=candidate_source.collected_at,
        reference_date=report_as_of,
    ):
        problems.append(
            f"[comparison] {fact.fact_id}: 비교 후보 공식 웹 원문이 보고서 "
            "기준일 현재성 상한을 넘었습니다"
        )
    if payload["evidence_sha256"] not in candidate_source.evidence_hashes:
        problems.append(
            f"[comparison] {fact.fact_id}: 비교 후보 문장 해시가 Source 원문 등록부에 없습니다"
        )
    if v2_payload is not None:
        if (
            payload["evidence_exact_sha256"]
            not in candidate_source.exact_evidence_hashes
            or exact_evidence_text_hash(payload["evidence_text"])
            != payload["evidence_exact_sha256"]
        ):
            problems.append(
                f"[comparison] {fact.fact_id}: v2 후보 원문의 byte-exact 해시가 "
                "봉인 Source와 다릅니다"
            )
        expected_metadata = {
            "source_kind": candidate_source.kind.value,
            "source_type": candidate_source.source_type,
            "source_publisher": candidate_source.publisher,
            "source_host": candidate_source.host,
            "source_url": candidate_source.url,
            "source_document_id": candidate_source.document_id,
            "source_location": candidate_source.location,
            "source_date": _source_date(candidate_source),
        }
        for key, expected in expected_metadata.items():
            if payload[key] != expected:
                problems.append(
                    f"[comparison] {fact.fact_id}: v2 후보 {key}가 봉인 Source와 다릅니다"
                )
        if not comparison_source_basis_is_allowed(payload):
            problems.append(
                f"[comparison] {fact.fact_id}: v2 후보가 허용된 DART·공식 HTML IR·웹 출처가 아닙니다"
            )
        if not comparison_candidate_sentence_matches(
            payload,
            comparison_target=fact.comparison_target,
            evidence_text=payload["evidence_text"],
            self_company=fact.legal_entity,
            known_company_aliases=known_comparison_companies,
        ):
            problems.append(
                f"[comparison] {fact.fact_id}: v2 후보 법인명·경쟁 표지·문장 해시가 "
                "봉인된 공식 원문 한 문장에 함께 결속되지 않았습니다"
            )
        if not comparison_source_sentence_has_marker(payload["evidence_text"]):
            problems.append(
                f"[comparison] {fact.fact_id}: v2 후보 문장에 명시적 경쟁 관계 표지가 없습니다"
            )
        if not comparison_source_sentence_has_self_subject(
            payload["evidence_text"], fact.legal_entity
        ):
            problems.append(
                f"[comparison] {fact.fact_id}: v2 후보 문장에 자사 주어가 함께 결속되지 않았습니다"
            )
        if comparison_source_overlap_dimension(payload["evidence_text"]) != payload[
            "overlap_dimension"
        ]:
            problems.append(
                f"[comparison] {fact.fact_id}: v2 후보 겹침 축이 공식 원문 문장과 다릅니다"
            )
        attester_id = payload["self_attestation_source_id"]
        attester = sources.get(attester_id)
        if (
            attester is None
            or attester.provenance_role != "attestation_only"
            or attester.document_id.strip() != payload["self_corp_code"]
            or _normalized(attester.publisher) != _normalized(fact.legal_entity)
            or not is_canonical_official_with_registry(
                attester, tuple(sources.values())
            )
            or attester.domain_attestation_evidence
            != payload["self_attestation_evidence"]
            or evidence_text_hash(payload["self_attestation_evidence"])
            not in attester.evidence_hashes
        ):
            problems.append(
                f"[comparison] {fact.fact_id}: v2 DART 자사 고유번호·법인명 attester 결속이 다릅니다"
            )
        if attester is not None and not comparison_dart_profile_attestation_is_valid(
            source_kind=attester.kind.value,
            source_type=attester.source_type,
            source_publisher=attester.publisher,
            source_host=attester.host,
            source_url=attester.url,
            source_document_id=attester.document_id,
            evidence=payload["self_attestation_evidence"],
            self_corp_code=payload["self_corp_code"],
            self_company=fact.legal_entity,
        ):
            problems.append(
                f"[comparison] {fact.fact_id}: v2 attester가 닫힌 OpenDART 기업개황 계약과 다릅니다"
            )
        if (
            candidate_source.kind is SourceKind.OTHER
            and candidate_source.domain_attestation_source_id.strip() != attester_id
        ):
            problems.append(
                f"[comparison] {fact.fact_id}: v2 공식 웹의 도메인 attester가 자사 신원 attester와 다릅니다"
            )
        try:
            attestation = json.loads(payload["self_attestation_evidence"])
        except (TypeError, ValueError, json.JSONDecodeError):
            attestation = None
        if (
            not isinstance(attestation, dict)
            or set(attestation) != {"corp_code", "corp_name", "hm_url"}
            or str(attestation.get("corp_code") or "").strip()
            != payload["self_corp_code"]
            or _normalized(str(attestation.get("corp_name") or ""))
            != _normalized(fact.legal_entity)
        ):
            problems.append(
                f"[comparison] {fact.fact_id}: v2 봉인 기업개황 payload가 자사와 다릅니다"
            )
        return problems

    if facts is None:
        problems.append(
            f"[comparison] {fact.fact_id}: 비교 후보 fact_id를 역참조할 사실 등록부가 없습니다"
        )
        return problems

    candidate_fact = facts.get(payload["candidate_fact_id"])
    if candidate_fact is None:
        problems.append(
            f"[comparison] {fact.fact_id}: 비교 후보 fact_id가 보고서 사실 등록부에 없습니다"
        )
        return problems
    if (
        confirmed_candidate_fact_ids is None
        or candidate_fact.fact_id not in confirmed_candidate_fact_ids
    ):
        problems.append(
            f"[comparison] {fact.fact_id}: 비교 후보 fact_id가 실제 1~8장 fact_ids에 없습니다"
        )
    if candidate_fact.section_owner == "competitive_position" or (
        candidate_fact.section_owner not in SECTION_BY_ID
    ):
        problems.append(
            f"[comparison] {fact.fact_id}: 비교 후보 fact_id는 확정된 1~8장 사실이어야 합니다"
        )
    if candidate_fact.source_id != payload["candidate_source_id"]:
        problems.append(
            f"[comparison] {fact.fact_id}: 비교 후보 fact_id와 source_id 결속이 다릅니다"
        )
    if candidate_fact.source_document_id.strip() != payload["filing_document_id"]:
        problems.append(
            f"[comparison] {fact.fact_id}: 비교 후보 사실의 문서 ID가 v1 근거와 다릅니다"
        )
    if not comparison_candidate_sentence_matches(
        payload,
        comparison_target=fact.comparison_target,
        evidence_text=candidate_fact.state_evidence,
        self_company=fact.legal_entity,
        known_company_aliases=known_comparison_companies,
    ):
        problems.append(
            f"[comparison] {fact.fact_id}: 비교 후보 법인명·경쟁 표지·"
            "문장 해시가 확정된 1~8장 근거 한 문장에 함께 결속되지 않았습니다"
        )
    if comparison_overlap_dimension(candidate_fact.state_evidence) != payload[
        "overlap_dimension"
    ]:
        problems.append(
            f"[comparison] {fact.fact_id}: 후보 겹침 축이 확정 근거 문장과 다릅니다"
        )
    if (
        candidate_fact.status != "verified"
        or candidate_fact.verification_status != "verified"
        or not candidate_fact.evidence_binding
        or candidate_fact.evidence_binding != fact_evidence_binding(candidate_fact)
    ):
        problems.append(
            f"[comparison] {fact.fact_id}: 비교 후보 fact_id가 verified 결속 사실이 아닙니다"
        )
    if _corporate_name(candidate_fact.legal_entity) != _corporate_name(
        fact.legal_entity
    ):
        problems.append(
            f"[comparison] {fact.fact_id}: 비교 후보 사실의 법인이 자사가 아닙니다"
        )
    if _corporate_name(candidate_fact.source_publisher) != _corporate_name(
        candidate_source.publisher
    ):
        problems.append(
            f"[comparison] {fact.fact_id}: 비교 후보 사실의 발행 법인이 Source와 다릅니다"
        )
    if candidate_fact.section_owner not in candidate_source.used_in:
        problems.append(
            f"[comparison] {fact.fact_id}: 비교 후보 Source가 1~8장 사실 소유 장에 등록되지 않았습니다"
        )
    return problems


def _derived_comparison_context(fact: FactRecord) -> dict[str, str]:
    """양사 공식 원문으로부터 고객·제품·시장 공통범위를 다시 계산한다."""

    # company_comparison은 publish의 binding 함수를 사용하므로 모듈 import는
    # 호출 시점으로 늦춰 순환 초기화를 피한다. 실제 비교 조립기와 동일한 추출기를
    # 써야 조립 때의 조건과 출고 때 다시 계산한 조건이 어긋나지 않는다.
    try:
        from src.features.company_comparison.logic import (  # noqa: PLC0415
            OfficialCompanyBundle,
            _shared_context,
        )

        own = OfficialCompanyBundle(
            corp_code="",
            company_name=fact.legal_entity,
            financials=None,
            filing=None,
            official_text=_comparison_official_text(fact.state_evidence),
        )
        comparator = OfficialCompanyBundle(
            corp_code="",
            company_name=fact.comparison_target,
            financials=None,
            filing=None,
            official_text=_comparison_official_text(fact.comparator_state_evidence),
        )
        return _shared_context(own, comparator)
    except (ImportError, TypeError, ValueError):
        return {}


def _comparison_period_is_bound(period: str, evidence: str) -> bool:
    """비교 기간이 양사 payload의 실제 날짜·연도에 나타나는지 확인한다."""

    expected_dates = {
        "-".join(match)
        for match in re.findall(r"(20\d{2})[-.](\d{2})[-.](\d{2})", period)
    }
    evidence_dates = {
        "-".join(match)
        for match in re.findall(r"(20\d{2})[-.](\d{2})[-.](\d{2})", evidence)
    }
    if expected_dates:
        return expected_dates <= evidence_dates
    expected_years = set(re.findall(r"20\d{2}", period))
    evidence_years = set(re.findall(r"20\d{2}", evidence))
    return bool(expected_years) and expected_years <= evidence_years


def _comparison_definition_is_bound(definition: str, evidence: str) -> bool:
    """지표 정의의 각 구조 필드가 실제 양사 payload에 있는지 확인한다."""

    clean_definition = str(definition or "").strip()
    normalized_evidence = _normalized(evidence)
    if not clean_definition or not normalized_evidence:
        return False
    if "|" not in clean_definition:
        return _normalized(clean_definition) in normalized_evidence
    rows = [row.strip() for row in clean_definition.split(";") if row.strip()]
    if not rows:
        return False
    for row in rows:
        fields = [field.strip() for field in row.split("|")]
        if len(fields) != 5 or any(not field for field in fields):
            return False
        if any(_normalized(field) not in normalized_evidence for field in fields):
            return False
    return True


def _comparison_scope_is_bound(scope: str, evidence: str) -> bool:
    """회계·사업 범위가 선언값이 아니라 원 payload에 직접 결속됐는지 확인한다."""

    clean_scope = str(scope or "").strip()
    normalized_evidence = _normalized(evidence)
    if not clean_scope or not normalized_evidence:
        return False
    scope_code = re.search(r"\b(CFS|OFS)\b", clean_scope, re.IGNORECASE)
    if scope_code is not None:
        return _normalized(scope_code.group(1)) in normalized_evidence
    return _normalized(clean_scope) in normalized_evidence


def _canonical_comparison_period(value: object) -> str:
    matches = re.findall(r"20\d{2}|\d{1,2}", str(value or ""))
    if len(matches) < 6:
        return ""
    try:
        start = date(int(matches[0]), int(matches[1]), int(matches[2]))
        end = date(int(matches[3]), int(matches[4]), int(matches[5]))
    except ValueError:
        return ""
    return f"{start.isoformat()}~{end.isoformat()}"


def _structured_comparison_basis_is_bound(
    *, period: str, definition: str, scope: str, evidence: str
) -> bool:
    """재무 비교는 기간·정의·범위가 같은 *한 행*들에 동시에 결속돼야 한다."""

    try:
        payload = json.loads(str(evidence or ""))
    except (TypeError, ValueError, json.JSONDecodeError):
        payload = None
    if not isinstance(payload, dict) or not isinstance(payload.get("financials"), dict):
        # 정성 비교도 세 필드를 공식 원 payload에 직접 적어 둔 경우만 허용한다.
        return (
            _comparison_period_is_bound(period, evidence)
            and _comparison_definition_is_bound(definition, evidence)
            and _comparison_scope_is_bound(scope, evidence)
        )

    financials = payload["financials"]
    if financials.get("status") != "000" or str(
        financials.get("reprt_code") or ""
    ).strip() != "11011":
        return False
    raw_rows = financials.get("list")
    if not isinstance(raw_rows, list):
        return False

    canonical_period = _canonical_comparison_period(period)
    scope_match = re.search(r"\b(CFS|OFS)\b", scope, re.IGNORECASE)
    definitions = [row.strip() for row in str(definition or "").split(";") if row.strip()]
    if not canonical_period or scope_match is None or not definitions:
        return False
    scope_code = scope_match.group(1).upper()

    expected_rows: list[tuple[str, str, str, str, str]] = []
    for raw_definition in definitions:
        fields = tuple(field.strip() for field in raw_definition.split("|"))
        if len(fields) != 5 or any(not field for field in fields):
            return False
        expected_rows.append(fields)  # type: ignore[arg-type]

    for metric_id, account_name, statement_kind, report_code, currency in expected_rows:
        matches = [
            row
            for row in raw_rows
            if isinstance(row, dict)
            and str(row.get("account_id") or "").strip() == metric_id
            and _normalized(row.get("account_nm")) == _normalized(account_name)
            and str(row.get("sj_div") or "").strip().upper()
            == statement_kind.upper()
            and str(row.get("reprt_code") or "").strip() == report_code
            and str(row.get("currency") or "").strip().upper() == currency.upper()
            and str(row.get("fs_div") or "").strip().upper() == scope_code
            and _canonical_comparison_period(row.get("thstrm_dt")) == canonical_period
        ]
        if len(matches) != 1:
            return False
    return True


def _temporal_lexical_problems(fact: FactRecord) -> list[str]:
    """시간 상태를 모델 추측이 아니라 원문·claim의 닫힌 어휘로 확인한다."""

    claim = " ".join(fact.claim.split())
    evidence = " ".join(fact.state_evidence.split())
    both = (claim, evidence)
    problems: list[str] = []
    if fact.time_state == "completed":
        if fact.claim_type == "completed_execution" and not all(
            _COMPLETED_MARKER.search(text) for text in both
        ):
            problems.append(
                f"[state] {fact.fact_id}: 완료 실행 표지가 claim과 원문에 모두 없습니다"
            )
        if any(_UNFINISHED_MARKER.search(text) for text in both):
            problems.append(
                f"[state] {fact.fact_id}: 미완료 계획·진행 문장을 완료 사실로 쓸 수 없습니다"
            )
    elif fact.time_state == "current_issue":
        if not all(_UNRESOLVED_MARKER.search(text) for text in both):
            problems.append(
                f"[state] {fact.fact_id}: 현재 과제에는 기준일 현재 미해결 표지가 필요합니다"
            )
        if any(_RESOLVED_ISSUE_MARKER.search(text) for text in both):
            problems.append(
                f"[state] {fact.fact_id}: 해결 완료된 문제를 현재 과제로 쓸 수 없습니다"
            )
    elif fact.time_state == "current_response":
        if not all(_STARTED_RESPONSE_MARKER.search(text) for text in both):
            problems.append(
                f"[state] {fact.fact_id}: 현재 대응은 이미 착수했음을 claim과 원문이 보여야 합니다"
            )
        if all(_PLAN_MARKER.search(text) for text in both) and not any(
            _STARTED_RESPONSE_MARKER.search(text) for text in both
        ):
            problems.append(
                f"[state] {fact.fact_id}: 아직 착수하지 않은 계획은 현재 대응이 아닙니다"
            )
    elif fact.time_state == "future_plan":
        if not all(_PLAN_MARKER.search(text) for text in both):
            problems.append(
                f"[state] {fact.fact_id}: 미래 전략에는 공식 미완료 계획 표지가 필요합니다"
            )
        condition = " ".join(str(fact.plan_condition or "").split())
        plan_actions = tuple(
            _COMPLETED_PREREQUISITE_MARKER.sub(
                "",
                _CONDITIONAL_COMPLETION_MARKER.sub(
                    "", text.replace(condition, "") if condition else text
                ),
            )
            for text in both
        )
        if any(_COMPLETED_MARKER.search(text) for text in plan_actions):
            problems.append(
                f"[state] {fact.fact_id}: 완료된 실행을 미래 계획으로 쓸 수 없습니다"
            )
        if any(_ALREADY_COMPLETED_PLAN_MARKER.search(text) for text in both):
            problems.append(
                f"[state] {fact.fact_id}: 이미 실현·완료된 내용을 미래 계획으로 쓸 수 없습니다"
            )
    return problems


def _status_stages(text: str) -> frozenset[str]:
    """원문 상태 단계를 더 강하거나 약한 단계로 바꾸지 않았는지 비교한다."""

    normalized = " ".join(str(text or "").split())
    stages = {
        name for name, pattern in _STATUS_STAGE_PATTERNS if pattern.search(normalized)
    }
    if "repeated_revenue" in stages:
        stages.discard("revenue")
    return frozenset(stages)


def _location_is_bound(fact_location: str, source_location: str) -> bool:
    fact_value = _normalized(fact_location)
    source_value = _normalized(source_location)
    if not fact_value or not source_value:
        return False
    if fact_value == source_value:
        return True
    return any(
        fact_value.startswith(source_value + separator)
        for separator in (" > ", " / ", " - ", ": ")
    )


def _evidence_support_problems(fact: FactRecord) -> list[str]:
    problems: list[str] = []
    terms = [_normalized(term) for term in fact.evidence_support_terms if _normalized(term)]
    if len(set(terms)) < 2:
        problems.append(
            f"[evidence] {fact.fact_id}: claim과 원문을 잇는 서로 다른 근거어가 두 개 이상 필요합니다"
        )
    claim = _normalized(fact.claim)
    evidence = _normalized(fact.state_evidence)
    for term in terms:
        if len(_compact(term)) < 2 or term not in claim or term not in evidence:
            problems.append(
                f"[evidence] {fact.fact_id}: 근거어 '{term}'가 claim과 원문 근거 양쪽에 없습니다"
            )
    if not fact.evidence_binding or fact.evidence_binding != fact_evidence_binding(fact):
        problems.append(
            f"[evidence] {fact.fact_id}: claim·원문·출처 결속 지문이 없거나 일치하지 않습니다"
        )
    return problems


def _decimal(value: str) -> Decimal | None:
    try:
        return Decimal(value.replace(",", ""))
    except (InvalidOperation, AttributeError):
        return None


def _numeric_problems(fact: FactRecord) -> list[str]:
    """공개 수치를 원시값부터 ROUND_HALF_UP 표시값까지 재계산한다."""

    problems: list[str] = []
    claim_numbers = _NUMBER_TOKEN.findall(fact.claim)
    has_numeric_payload = bool(
        claim_numbers
        or _NUMBER_TOKEN.search(fact.raw_value)
        or _NUMBER_TOKEN.search(fact.display_value)
    )
    if not has_numeric_payload:
        if fact.numeric_checks:
            problems.append(f"[number] {fact.fact_id}: 수치가 없는데 numeric_checks가 있습니다")
        return problems

    if not all(
        value.strip()
        for value in (
            fact.raw_value,
            fact.calculation,
            fact.display_value,
            fact.rounding_rule,
        )
    ):
        problems.append(
            f"[number] {fact.fact_id}: 수치는 raw_value·calculation·display_value·rounding_rule이 모두 필요합니다"
        )
    if "ROUND_HALF_UP" not in fact.rounding_rule.upper():
        problems.append(
            f"[number] {fact.fact_id}: rounding_rule에 ROUND_HALF_UP 적용 여부가 명시되지 않았습니다"
        )
    if not fact.numeric_checks:
        problems.append(
            f"[number] {fact.fact_id}: 결정론적 numeric_checks가 없습니다"
        )
        return problems

    checked_raw: list[Decimal] = []
    checked_display: list[Decimal] = []
    for index, check in enumerate(fact.numeric_checks, start=1):
        match = _NUMERIC_CHECK.fullmatch(str(check))
        if match is None:
            problems.append(
                f"[number] {fact.fact_id}: numeric_checks {index}번 형식이 잘못됐습니다"
            )
            continue
        raw = _decimal(match.group("raw"))
        divisor = _decimal(match.group("divisor"))
        display = _decimal(match.group("display"))
        places = int(match.group("places"))
        if raw is None or divisor is None or display is None or divisor <= 0:
            problems.append(
                f"[number] {fact.fact_id}: numeric_checks {index}번 값을 계산할 수 없습니다"
            )
            continue
        quantum = Decimal(1).scaleb(-places)
        expected = (raw / divisor).quantize(quantum, rounding=ROUND_HALF_UP)
        if expected != display:
            problems.append(
                f"[number] {fact.fact_id}: numeric_checks {index}번 표시값이 ROUND_HALF_UP 재계산과 다릅니다"
            )
        checked_raw.append(raw)
        checked_display.append(display)

    raw_numbers = [_decimal(value) for value in _NUMBER_TOKEN.findall(fact.raw_value)]
    display_numbers = [
        _decimal(value) for value in _NUMBER_TOKEN.findall(fact.display_value)
    ]
    if any(value is None or value not in checked_raw for value in raw_numbers):
        problems.append(
            f"[number] {fact.fact_id}: raw_value의 모든 수치가 numeric_checks에 없습니다"
        )
    if any(value is None or value not in checked_display for value in display_numbers):
        problems.append(
            f"[number] {fact.fact_id}: display_value의 모든 수치가 numeric_checks에 없습니다"
        )

    # raw_value는 내부 원장이라는 이름만 붙였다고 원값이 되지 않는다. 양사 비교는
    # 양쪽 state_evidence를 합쳐 보고, 그 안에 실제 값이 있거나 아래 검산식의
    # 피연산자로 재현되는 값만 허용한다.
    evidence_numbers = {
        value
        for token in _NUMBER_TOKEN.findall(
            fact.state_evidence + "\n" + fact.comparator_state_evidence
        )
        if (value := _decimal(token)) is not None
    }
    missing_raw_evidence = [
        value
        for value in raw_numbers
        if value is not None and value not in evidence_numbers
    ]
    if missing_raw_evidence:
        problems.append(
            f"[number] {fact.fact_id}: raw_value 원값이 state_evidence에서 확인되지 않습니다"
        )

    # 본문 숫자는 표시값 또는 구조화된 기준기간에만 존재해야 한다.
    allowed_claim_numbers = set(checked_raw) | set(checked_display)
    as_of = _parse_iso_date(fact.as_of)
    if as_of is not None:
        allowed_claim_numbers.add(Decimal(as_of.year))
    if fact.fiscal_year:
        allowed_claim_numbers.add(Decimal(fact.fiscal_year))
    uncovered = [
        number
        for token in claim_numbers
        if (number := _decimal(token)) is not None and number not in allowed_claim_numbers
    ]
    if uncovered:
        problems.append(
            f"[number] {fact.fact_id}: claim의 수치가 수치 장부에 모두 결속되지 않았습니다"
        )
    return problems


def _fact_problems(
    fact: FactRecord,
    sources: dict[str, Source],
    facts: dict[str, FactRecord] | None = None,
    confirmed_candidate_fact_ids: set[str] | None = None,
    report_as_of: str = "",
) -> list[str]:
    problems: list[str] = []
    if not _fact_core_is_complete(fact):
        problems.append(
            f"[fact] {fact.fact_id or '<빈 ID>'}: 원자 사실의 필수 필드가 비었습니다"
        )
    if fact.section_owner not in SECTION_BY_ID:
        problems.append(f"[fact] {fact.fact_id}: 알 수 없는 소유 장입니다")
    elif fact.claim_type in INTERNAL_ONLY_CLAIM_TYPES_BY_SECTION.get(
        fact.section_owner, frozenset()
    ):
        problems.append(
            f"[comparison] {fact.fact_id}: 비교 근거 부족은 내부 탈락 상태라 공개할 수 없습니다"
        )
    elif fact.claim_type not in CANONICAL_CLAIM_TYPES_BY_SECTION[fact.section_owner]:
        problems.append(
            f"[fact] {fact.fact_id}: {fact.section_owner}에서 허용되지 않는 claim_type입니다"
        )
    if str(fact.market_priority or "").strip():
        problems.append(
            f"[market] {fact.fact_id}: market_priority는 의미가 섞인 이전 필드라 "
            "공개할 수 없습니다"
        )
    market_stage = str(fact.market_stage or "").strip()
    market_observation = _normalized(fact.market_observation)
    if fact.claim_type == "customer_market":
        if not market_observation:
            problems.append(
                f"[market] {fact.fact_id}: 고객·시장 사실에는 원문 그대로의 "
                "market_observation이 필요합니다"
            )
        elif market_observation not in _normalized(fact.state_evidence):
            problems.append(
                f"[market] {fact.fact_id}: market_observation이 원문 근거에 "
                "결속되지 않았습니다"
            )
        if market_stage and market_stage not in MARKET_STAGES:
            problems.append(
                f"[market] {fact.fact_id}: market_stage는 핵심·성장·진입만 "
                "허용합니다"
            )
        elif market_stage and not MARKET_STAGE_EVIDENCE_PATTERNS[
            market_stage
        ].search(str(fact.market_observation or "")):
            problems.append(
                f"[market] {fact.fact_id}: market_stage가 시장 단계의 직접 "
                "원문 근거에 결속되지 않았습니다"
            )
    elif market_stage or market_observation:
        problems.append(
            f"[market] {fact.fact_id}: 시장 단계·관찰은 customer_market 사실에만 "
            "둘 수 있습니다"
        )

    response_action = _normalized(fact.response_action)
    initial_signal = _normalized(fact.initial_signal)
    next_check_metric = _normalized(fact.next_check_metric)
    evidence = _normalized(fact.state_evidence)
    if fact.claim_type == "current_issue":
        if response_action or initial_signal:
            problems.append(
                f"[current] {fact.fact_id}: 대응 행동·초기 신호는 문제 사실이 "
                "아니라 대응 사실에만 둘 수 있습니다"
            )
        if not next_check_metric:
            problems.append(
                f"[current] {fact.fact_id}: 해결 여부를 볼 next_check_metric이 "
                "필요합니다"
            )
        elif next_check_metric not in evidence:
            problems.append(
                f"[current] {fact.fact_id}: next_check_metric이 문제의 원문 근거에 "
                "결속되지 않았습니다"
            )
        elif not is_objective_next_check_metric(
            fact.next_check_metric, fact.subject_scope
        ):
            problems.append(
                f"[current] {fact.fact_id}: next_check_metric은 문제 문구 반복이 "
                "아닌 객관적 상태·지표여야 합니다"
            )
    elif fact.claim_type == "current_response":
        if next_check_metric:
            problems.append(
                f"[current] {fact.fact_id}: 다음 확인 지표는 대응이 아니라 "
                "연결된 문제 사실에 둬야 합니다"
            )
        if not response_action:
            problems.append(
                f"[current] {fact.fact_id}: 원문에 결속된 response_action이 "
                "필요합니다"
            )
        elif response_action not in evidence:
            problems.append(
                f"[current] {fact.fact_id}: response_action이 대응 원문 근거에 "
                "결속되지 않았습니다"
            )
        if initial_signal:
            if initial_signal not in evidence:
                problems.append(
                    f"[current] {fact.fact_id}: initial_signal이 대응 원문 근거에 "
                    "결속되지 않았습니다"
                )
            if not excerpts_are_in_distinct_clauses(
                fact.state_evidence, fact.response_action, fact.initial_signal
            ):
                problems.append(
                    f"[current] {fact.fact_id}: response_action과 initial_signal은 "
                    "서로 다른 원문 절이어야 합니다"
                )
            if not is_observed_initial_signal(fact.initial_signal):
                problems.append(
                    f"[current] {fact.fact_id}: initial_signal은 미래 계획이 "
                    "아닌 관찰된 초기 진척·결과여야 합니다"
                )
    elif response_action or initial_signal or next_check_metric:
        problems.append(
            f"[current] {fact.fact_id}: 5장 구조 필드는 current_issue·"
            "current_response 사실에만 둘 수 있습니다"
        )

    plan_values = (
        fact.plan_status,
        fact.plan_timing,
        fact.plan_condition,
        fact.plan_expected_effect,
        fact.plan_execution_signal,
    )
    if fact.claim_type == "future_plan":
        plan_status = str(fact.plan_status or "").strip()
        if not plan_is_active(fact.state_evidence):
            problems.append(
                f"[future] {fact.fact_id}: 취소·철회·중단된 계획을 현재 전략으로 "
                "쓸 수 없습니다"
            )
        if (
            not _normalized(fact.subject_scope)
            or _normalized(fact.subject_scope) == evidence
            or _normalized(fact.subject_scope) not in evidence
        ):
            problems.append(
                f"[future] {fact.fact_id}: 원문에 결속된 구체 계획 대상이 "
                "필요합니다"
            )
        if plan_status not in PLAN_STATUSES:
            problems.append(
                f"[future] {fact.fact_id}: plan_status는 발표·승인·조건부 중 "
                "하나여야 합니다"
            )
        elif plan_status != expected_plan_status(fact.state_evidence):
            problems.append(
                f"[future] {fact.fact_id}: plan_status가 원문의 승인·조건 상태와 "
                "다릅니다"
            )
        for field_name, value in (
            ("plan_timing", fact.plan_timing),
            ("plan_condition", fact.plan_condition),
            ("plan_expected_effect", fact.plan_expected_effect),
            ("plan_execution_signal", fact.plan_execution_signal),
        ):
            normalized_value = _normalized(value)
            if normalized_value and normalized_value not in evidence:
                problems.append(
                    f"[future] {fact.fact_id}: {field_name}이 계획 원문 근거에 "
                    "결속되지 않았습니다"
                )
        if fact.plan_timing and PLAN_TIMING_PATTERN.search(fact.plan_timing) is None:
            problems.append(
                f"[future] {fact.fact_id}: plan_timing이 객관적 시점 표현이 "
                "아닙니다"
            )
        if fact.plan_condition and not has_plan_condition(fact.plan_condition):
            problems.append(
                f"[future] {fact.fact_id}: plan_condition이 선행 조건 표현이 "
                "아닙니다"
            )
        if (
            fact.plan_expected_effect
            and not is_company_stated_plan_effect(
                fact.state_evidence, fact.plan_expected_effect
            )
        ):
            problems.append(
                f"[future] {fact.fact_id}: plan_expected_effect가 회사 제시 "
                "효과 표현이 아닙니다"
            )
        if fact.plan_expected_effect and excerpts_overlap(
            fact.plan_expected_effect, fact.plan_execution_signal
        ):
            problems.append(
                f"[future] {fact.fact_id}: 계획 행동을 예상 효과로 중복 저장할 "
                "수 없습니다"
            )
        if plan_status == "conditional" and not _normalized(fact.plan_condition):
            problems.append(
                f"[future] {fact.fact_id}: 조건부 계획에는 plan_condition이 "
                "필요합니다"
            )
        if (
            not _normalized(fact.plan_execution_signal)
            or PLAN_EXECUTION_SIGNAL_PATTERN.search(
                fact.plan_execution_signal
            )
            is None
        ):
            problems.append(
                f"[future] {fact.fact_id}: 실행 여부를 볼 plan_execution_signal이 "
                "필요합니다"
            )
    elif any(str(value or "").strip() for value in plan_values):
        problems.append(
            f"[future] {fact.fact_id}: 6장 계획 필드는 future_plan 사실에만 "
            "둘 수 있습니다"
        )

    value_chain_stage = str(fact.value_chain_stage or "").strip()
    relationship_type = str(fact.relationship_type or "").strip()
    if fact.claim_type in RELATIONSHIP_TYPES_BY_CLAIM:
        operation_role = _normalized(fact.relationship_or_action)
        stage_pattern = VALUE_CHAIN_STAGE_PATTERNS.get(value_chain_stage)
        if not operation_role or operation_role not in evidence:
            problems.append(
                f"[operations] {fact.fact_id}: 현재 운영 역할 발췌가 원문에 "
                "결속되지 않았습니다"
            )
        if stage_pattern is None:
            problems.append(
                f"[operations] {fact.fact_id}: 닫힌 value_chain_stage가 필요합니다"
            )
        elif stage_pattern.search(fact.relationship_or_action) is None:
            problems.append(
                f"[operations] {fact.fact_id}: value_chain_stage가 원문 역할에 "
                "결속되지 않았습니다"
            )
        operation_source = sources.get(fact.source_id)
        current_role_is_bound = has_current_operating_role(
            fact.state_evidence, fact.relationship_or_action
        ) or (
            fact.claim_type == "partner_role"
            and relationship_type == "distribution"
            and _is_self_published_company_source(
                operation_source,
                legal_entity=fact.legal_entity,
                registered_sources=tuple(sources.values()),
            )
            and has_executed_current_distribution_partnership(
                fact.state_evidence,
                fact.relationship_or_action,
                fact.subject_scope,
            )
        )
        if not current_role_is_bound:
            problems.append(
                f"[operations] {fact.fact_id}: MOU·일회성 체결이 아닌 현재 "
                "반복 운영 역할 근거가 없습니다"
            )
        allowed_relationships = RELATIONSHIP_TYPES_BY_CLAIM[fact.claim_type]
        relationship_pattern = RELATIONSHIP_TYPE_PATTERNS.get(relationship_type)
        if relationship_type not in allowed_relationships:
            problems.append(
                f"[operations] {fact.fact_id}: claim_type과 relationship_type의 "
                "내부·외부 역할이 맞지 않습니다"
            )
        elif relationship_pattern is None or relationship_pattern.search(
            fact.state_evidence
            if relationship_type == "subsidiary"
            else fact.relationship_or_action
        ) is None:
            problems.append(
                f"[operations] {fact.fact_id}: relationship_type이 원문 관계에 "
                "결속되지 않았습니다"
            )
        if looks_like_customer_outbound(
            fact.state_evidence, fact.subject_scope
        ):
            problems.append(
                f"[operations] {fact.fact_id}: 최종 고객은 7장 내부 운영·파트너 "
                "주체로 분류할 수 없습니다"
            )
        if FUTURE_OPERATION_PATTERN.search(fact.state_evidence):
            problems.append(
                f"[operations] {fact.fact_id}: 미실행 계획·준비 상태는 현재 "
                "운영 구조에 둘 수 없습니다"
            )
        if (
            relationship_type == "internal_operation"
            and not internal_operation_is_company_controlled(
                fact.state_evidence,
                fact.legal_entity,
                source_publisher=fact.source_publisher,
            )
        ):
            problems.append(
                f"[operations] {fact.fact_id}: 분석 법인이 직접 통제하는 운영 "
                "근거가 없습니다"
            )
        if (
            relationship_type == "ownership"
            and not ownership_is_company_held(
                fact.state_evidence,
                fact.legal_entity,
                source_publisher=fact.source_publisher,
            )
        ):
            problems.append(
                f"[operations] {fact.fact_id}: 분석 법인이 보유 주체인 소유·"
                "지분 근거가 없습니다"
            )
        if (
            relationship_type == "supplier"
            and SUPPLIER_INBOUND_PATTERN.search(fact.state_evidence) is None
        ):
            problems.append(
                f"[operations] {fact.fact_id}: 공급·조달 관계의 방향이 원문에 "
                "결속되지 않았습니다"
            )
    elif value_chain_stage or relationship_type:
        problems.append(
            f"[operations] {fact.fact_id}: 7장 구조 필드는 운영·파트너 사실에만 "
            "둘 수 있습니다"
        )
    if fact.section_owner == "competitive_position" and (
        fact.claim_type == "competitive_comparison"
        and fact.comparison_judgment not in COMPARISON_JUDGMENTS
    ):
        problems.append(
            f"[comparison] {fact.fact_id}: 경쟁우위·운영 특성 공개 판정이 확정되지 않았습니다"
        )
    elif fact.section_owner != "competitive_position" and fact.comparison_judgment:
        problems.append(
            f"[comparison] {fact.fact_id}: 비교 판정은 9장 사실에만 둘 수 있습니다"
        )
    source = sources.get(fact.source_id)
    if source is None:
        problems.append(
            f"[source] {fact.fact_id}: 검증된 원문 source_id와 연결되지 않았습니다"
        )
    else:
        registered_sources = tuple(sources.values())
        if source.provenance_role != "citation":
            problems.append(
                f"[source] {fact.fact_id}: attestation_only Source는 사실의 직접 "
                "근거로 연결할 수 없습니다"
            )
        if not is_canonical_official_with_registry(source, registered_sources):
            problems.append(
                f"[source] {fact.fact_id}: 핵심 사실은 회사·공시·IR·공식 웹·"
                "공식 파트너·규제기관 원문만 단독 근거로 쓸 수 있습니다"
            )
            attestation_problem = official_domain_attestation_problem(
                source, registered_sources
            )
            if attestation_problem:
                problems.append(f"[source] {fact.fact_id}: {attestation_problem}")
        normalized_source_type = _normalized(source.source_type)
        if source_type_is_official_ir(source.source_type):
            problems.append(
                f"[time] {fact.fact_id}: 문서일 미결속 IR PDF는 공개 사실의 "
                "직접 근거로 쓸 수 없습니다"
            )
        if not official_web_currentness_is_usable(
            source_type=source.source_type,
            url=source.url,
            published_at=source.published_at,
            disclosed_at=source.disclosed_at,
            collected_at=source.collected_at,
            reference_date=report_as_of,
        ):
            problems.append(
                f"[time] {fact.fact_id}: 보도·공지·archive형 공식 웹은 검증된 "
                "최근 문서일 없이는 현재 사실의 직접 근거로 쓸 수 없습니다"
            )
        if fact.section_owner != "competitive_position":
            if normalized_source_type.startswith("비교사 공식"):
                problems.append(
                    f"[source] {fact.fact_id}: 1~8장에 비교사 원문을 자사 핵심 근거로 쓸 수 없습니다"
                )
            elif (
                normalized_source_type in _SELF_PUBLISHED_SOURCE_TYPES
                and _corporate_name(source.publisher)
                != _corporate_name(fact.legal_entity)
            ):
                problems.append(
                    f"[source] {fact.fact_id}: 자사 공식 자료의 발행 법인이 사실 법인과 다릅니다"
                )
        source_title = source.title or source.label
        if _normalized(fact.source_title) != _normalized(source_title):
            problems.append(f"[source] {fact.fact_id}: 원문 제목이 등록부와 다릅니다")
        if _normalized(fact.source_publisher) != _normalized(source.publisher):
            problems.append(f"[source] {fact.fact_id}: 발행처가 등록부와 다릅니다")
        if _normalized(fact.source_host) != _normalized(source.host):
            problems.append(f"[source] {fact.fact_id}: 원문 host가 등록부와 다릅니다")
        if fact.source_url.strip() != source.url.strip():
            problems.append(f"[source] {fact.fact_id}: 원문 URL이 등록부와 다릅니다")
        if fact.source_document_id.strip() != source.document_id.strip():
            problems.append(f"[source] {fact.fact_id}: 원문 document_id가 등록부와 다릅니다")
        if _normalized(fact.source_type) != _normalized(source.source_type):
            problems.append(f"[source] {fact.fact_id}: 자료 유형이 등록부와 다릅니다")
        source_status = _normalized(source.fact_status)
        expected_markers = {
            "actual": ("actual", "실제", "현재", "확정"),
            "provisional": ("provisional", "잠정"),
            "planned": ("planned", "계획", "예정", "미실행"),
            "estimated": ("estimated", "추정"),
            "scope_undisclosed": ("scope_undisclosed", "범위 미공개", "미공개"),
        }
        if fact.fact_status in expected_markers and not any(
            marker in source_status for marker in expected_markers[fact.fact_status]
        ):
            problems.append(
                f"[state] {fact.fact_id}: fact_status가 Source.fact_status와 맞지 않습니다"
            )
        if fact.section_owner not in source.used_in:
            problems.append(
                f"[source] {fact.fact_id}: 원문 used_in에 실제 소유 장이 없습니다"
            )
        if evidence_text_hash(fact.state_evidence) not in source.evidence_hashes:
            problems.append(
                f"[evidence] {fact.fact_id}: state_evidence가 Source 원문 해시 등록부에 없습니다"
            )
        if not _location_is_bound(fact.location, source.location):
            problems.append(
                f"[source] {fact.fact_id}: 사실 위치가 Source.location과 같거나 그 하위 위치가 아닙니다"
            )
        expected_source_date = _source_date(source)
        if fact.source_date != expected_source_date:
            problems.append(
                f"[time] {fact.fact_id}: source_date가 원문 날짜와 다릅니다"
            )
        fact_date = _parse_iso_date(fact.as_of)
        source_date = _parse_iso_date(expected_source_date)
        if fact_date is None or source_date is None:
            problems.append(
                f"[time] {fact.fact_id}: 사실 기준일과 원문 날짜는 ISO 날짜여야 합니다"
            )
        elif fact_date > source_date:
            problems.append(
                f"[time] {fact.fact_id}: 사실 기준일이 원문 날짜보다 뒤입니다"
            )
    if fact.verification_status != "verified":
        problems.append(
            f"[verification] {fact.fact_id}: verified 사실만 확정 본문에 쓸 수 있습니다"
        )
    if fact.status and fact.status != fact.verification_status:
        problems.append(
            f"[verification] {fact.fact_id}: legacy status가 verification_status와 다릅니다"
        )
    allowed_fact_statuses = {
        "actual",
        "provisional",
        "planned",
        "estimated",
        "scope_undisclosed",
    }
    if fact.fact_status not in allowed_fact_statuses:
        problems.append(f"[state] {fact.fact_id}: 알 수 없는 fact_status입니다")
    allowed_by_time = {
        "standing": {"actual", "scope_undisclosed"},
        "completed": {"actual", "provisional"},
        "current_issue": {"actual", "provisional"},
        "current_response": {"actual", "provisional"},
        "future_plan": {"planned"},
    }
    if fact.fact_status not in allowed_by_time.get(fact.time_state, set()):
        problems.append(
            f"[state] {fact.fact_id}: fact_status가 시간 상태와 모순됩니다"
        )
    required_states = SECTION_TIME_STATES.get(fact.section_owner)
    if required_states is not None and fact.time_state not in required_states:
        problems.append(
            f"[time] {fact.fact_id}: {fact.section_owner}의 시간 상태와 맞지 않습니다"
        )
    if fact.claim_type == "completed_execution":
        event_value = str(fact.event_date or "").strip()
        if not re.fullmatch(r"20\d{2}(?:-\d{2}-\d{2})?", event_value):
            problems.append(
                f"[time] {fact.fact_id}: 완료 실행에는 원문 사건 연도·날짜가 필요합니다"
            )
        else:
            if event_value[:4] not in fact.state_evidence:
                problems.append(
                    f"[time] {fact.fact_id}: event_date 연도가 원문 근거에 없습니다"
                )
            event_date = (
                _parse_iso_date(event_value)
                if len(event_value) == 10
                else date(int(event_value), 1, 1)
            )
            report_as_of = _parse_iso_date(fact.as_of)
            if event_date is None or report_as_of is None:
                problems.append(
                    f"[time] {fact.fact_id}: 사건일 또는 사실 기준일을 계산할 수 없습니다"
                )
            else:
                if event_date > report_as_of:
                    problems.append(
                        f"[time] {fact.fact_id}: 완료 실행 사건일이 원문 기준일보다 뒤입니다"
                    )
    elif fact.event_date:
        problems.append(
            f"[time] {fact.fact_id}: 완료 실행 외 사실에 event_date를 넣을 수 없습니다"
        )
    if fact.claim_type == "priority_product":
        signals = [
            signal.strip() for signal in fact.priority_signals if signal.strip()
        ]
        if len(set(signals)) < 2:
            problems.append(
                f"[section] {fact.fact_id}: 현재 중점 제품에는 서로 다른 추진 신호가 두 개 이상 필요합니다"
            )
        for signal in set(signals):
            pattern = PRIORITY_SIGNAL_PATTERNS.get(signal)
            if pattern is None or pattern.search(fact.state_evidence) is None:
                problems.append(
                    f"[section] {fact.fact_id}: 추진 신호 '{signal}'가 원문에서 확인되지 않습니다"
                )
        if len(set(signals)) >= 2 and not _has_independent_priority_signal_clauses(
            signals, fact.state_evidence
        ):
            problems.append(
                f"[section] {fact.fact_id}: 서로 다른 추진 신호 두 개가 "
                "서로 다른 원문 사건 절에 결속되지 않았습니다"
            )
    problems.extend(_evidence_support_problems(fact))
    problems.extend(_temporal_lexical_problems(fact))
    if _status_stages(fact.claim) != _status_stages(fact.state_evidence):
        problems.append(
            f"[state] {fact.fact_id}: 개발·검증·MOU·계약·납품·매출 상태를 원문과 다르게 바꿨습니다"
        )
    has_causal_claim = bool(_CAUSAL_PATTERN.search(fact.claim))
    if has_causal_claim or fact.supports_causality:
        causal_fields = (
            fact.causal_subject,
            fact.causal_mechanism,
            fact.causal_outcome,
            fact.causal_evidence,
        )
        if not fact.supports_causality or not all(value.strip() for value in causal_fields):
            problems.append(
                f"[causality] {fact.fact_id}: 직접 인과 근거와 주체·기제·결과가 모두 필요합니다"
            )
        elif _normalized(fact.causal_evidence) not in _normalized(fact.state_evidence):
            problems.append(
                f"[causality] {fact.fact_id}: causal_evidence가 결속된 원문 근거에 없습니다"
            )
    problems.extend(_numeric_problems(fact))
    if fact.section_owner == "competitive_position":
        comparison_fields = (
            fact.comparison_target,
            fact.comparison_definition,
            fact.comparison_period,
            fact.comparison_scope,
            fact.comparator_source_id,
        )
        if not all(str(value).strip() for value in comparison_fields):
            problems.append(
                f"[comparison] {fact.fact_id}: 동일 조건 비교 필드가 부족합니다"
            )
        conditions = {
            str(key).strip(): str(value).strip()
            for key, value in fact.comparison_conditions.items()
            if str(key).strip()
        }
        if set(conditions) != _COMPARISON_CONDITION_KEYS or any(
            not value for value in conditions.values()
        ):
            problems.append(
                f"[comparison] {fact.fact_id}: 고객·제품·시장·양사 기간·정의·회계범위 조건이 완전하지 않습니다"
            )
        else:
            for left, right, top_level, label in (
                (
                    "self_period",
                    "comparator_period",
                    fact.comparison_period,
                    "기간",
                ),
                (
                    "self_definition",
                    "comparator_definition",
                    fact.comparison_definition,
                    "지표 정의",
                ),
                (
                    "self_accounting_scope",
                    "comparator_accounting_scope",
                    fact.comparison_scope,
                    "회계 범위",
                ),
            ):
                if _normalized(conditions[left]) != _normalized(conditions[right]):
                    problems.append(
                        f"[comparison] {fact.fact_id}: 양사 {label}가 같지 않습니다"
                    )
                if _normalized(conditions[left]) != _normalized(top_level):
                    problems.append(
                        f"[comparison] {fact.fact_id}: {label} 조건이 FactRecord 정본 필드와 다릅니다"
                    )

            if not all(
                _comparison_period_is_bound(fact.comparison_period, evidence)
                for evidence in (fact.state_evidence, fact.comparator_state_evidence)
            ):
                problems.append(
                    f"[comparison] {fact.fact_id}: 비교 기간이 양사 공식 원 payload에 결속되지 않았습니다"
                )
            if not all(
                _comparison_definition_is_bound(fact.comparison_definition, evidence)
                for evidence in (fact.state_evidence, fact.comparator_state_evidence)
            ):
                problems.append(
                    f"[comparison] {fact.fact_id}: 지표 정의가 양사 공식 원 payload에 결속되지 않았습니다"
                )
            if not all(
                _comparison_scope_is_bound(fact.comparison_scope, evidence)
                for evidence in (fact.state_evidence, fact.comparator_state_evidence)
            ):
                problems.append(
                    f"[comparison] {fact.fact_id}: 회계·사업 범위가 양사 공식 원 payload에 결속되지 않았습니다"
                )
            if not all(
                _structured_comparison_basis_is_bound(
                    period=fact.comparison_period,
                    definition=fact.comparison_definition,
                    scope=fact.comparison_scope,
                    evidence=evidence,
                )
                for evidence in (fact.state_evidence, fact.comparator_state_evidence)
            ):
                problems.append(
                    f"[comparison] {fact.fact_id}: 기간·정의·회계범위가 같은 공식 원자료 행에 동시에 결속되지 않았습니다"
                )

            derived_context = _derived_comparison_context(fact)
            for axis in ("customer", "product", "market"):
                expected_axis = derived_context.get(axis, "")
                if not expected_axis or _normalized(conditions[axis]) != _normalized(
                    expected_axis
                ):
                    problems.append(
                        f"[comparison] {fact.fact_id}: 동일 {axis} 범위가 양사 공식 원문에서 다시 계산한 값과 다릅니다"
                    )
        comparator_source = sources.get(fact.comparator_source_id)
        if fact.comparator_source_id == fact.source_id:
            problems.append(
                f"[comparison] {fact.fact_id}: 자사 원문과 비교사 원문 source_id가 같습니다"
            )
        if comparator_source is None:
            problems.append(
                f"[comparison] {fact.fact_id}: 비교사 원문이 검증되지 않았습니다"
            )
        else:
            if comparator_source.provenance_role != "citation":
                problems.append(
                    f"[comparison] {fact.fact_id}: attestation_only Source는 비교사의 "
                    "직접 근거로 연결할 수 없습니다"
                )
            if not is_canonical_official_with_registry(
                comparator_source, tuple(sources.values())
            ):
                problems.append(
                    f"[comparison] {fact.fact_id}: 비교사 원문이 공식 자료가 아닙니다"
                )
            if fact.section_owner not in comparator_source.used_in:
                problems.append(
                    f"[comparison] {fact.fact_id}: 비교사 원문 used_in에 실제 소유 장이 없습니다"
                )
            own_publisher = _corporate_name(fact.source_publisher)
            comparator_publisher = _corporate_name(comparator_source.publisher)
            legal_entity = _corporate_name(fact.legal_entity)
            comparison_target = _corporate_name(fact.comparison_target)
            if not comparator_publisher or comparator_publisher in {
                own_publisher,
                legal_entity,
            }:
                problems.append(
                    f"[comparison] {fact.fact_id}: 비교사 발행 법인이 자사와 구분되지 않습니다"
                )
            if comparison_target != comparator_publisher:
                problems.append(
                    f"[comparison] {fact.fact_id}: comparison_target이 비교사 발행 법인과 다릅니다"
                )
            if not fact.comparator_state_evidence.strip() or evidence_text_hash(
                fact.comparator_state_evidence
            ) not in comparator_source.evidence_hashes:
                problems.append(
                    f"[comparison] {fact.fact_id}: 비교사 state_evidence가 비교사 원문 해시 등록부에 없습니다"
                )
            comparator_terms = [
                _normalized(term)
                for term in fact.comparator_evidence_support_terms
                if _normalized(term)
            ]
            if len(set(comparator_terms)) < 2:
                problems.append(
                    f"[comparison] {fact.fact_id}: 비교사 원문 근거어가 두 개 이상 필요합니다"
                )
            claim = _normalized(fact.claim)
            comparator_evidence = _normalized(fact.comparator_state_evidence)
            for term in comparator_terms:
                if (
                    len(_compact(term)) < 2
                    or term not in claim
                    or term not in comparator_evidence
                ):
                    problems.append(
                        f"[comparison] {fact.fact_id}: 비교사 근거어 '{term}'가 claim과 비교사 원문 양쪽에 없습니다"
                    )
        problems.extend(
            _comparison_candidate_basis_problems(
                fact,
                sources,
                facts,
                confirmed_candidate_fact_ids,
                report_as_of,
            )
        )
    return problems


def _section_content_problems(
    section: ReportSection,
    supported_fact_ids: list[str],
    facts: dict[str, FactRecord],
    sources: dict[str, Source],
) -> list[str]:
    """공개 문장·표 행과 원자 사실을 한 건씩 대응시킨다."""

    problems: list[str] = []
    if section.guidance_lines:
        problems.append(f"[content] {section.cell}: guidance_lines를 공개 장에 둘 수 없습니다")
    if section.empty_reason.strip():
        problems.append(f"[content] {section.cell}: empty_reason을 공개 장에 둘 수 없습니다")
    if section.lines and not section.prose_lines:
        problems.append(
            f"[content] {section.cell}: 원문 lines가 있으면 공개용 prose_lines가 필요합니다"
        )
    if section.cell in REQUIRED_SECTION_IDS and not section.prose_lines:
        problems.append(
            f"[content] {section.cell}: 필수 장에는 prose 문장이 최소 한 개 필요합니다"
        )

    unused = list(supported_fact_ids)

    def take_exact_claim(texts: list[str], unit: str, cite: str) -> str | None:
        normalized_texts = {_normalized(text) for text in texts if _normalized(text)}
        matching = [
            fact_id
            for fact_id in unused
            if _normalized(facts[fact_id].claim) in normalized_texts
        ]
        if len(matching) != 1:
            problems.append(
                f"[mapping] {section.cell}: {unit}가 FactRecord.claim 한 건과 정확히 대응하지 않습니다"
            )
            return None
        fact_id = matching[0]
        fact_source = sources.get(facts[fact_id].source_id)
        shown_number = citation_number(cite)
        if not shown_number:
            problems.append(
                f"[source] {section.cell}: {unit}의 공개 출처 번호가 없거나 잘못됐습니다"
            )
        elif fact_source is None or shown_number != str(fact_source.number):
            problems.append(
                f"[source] {section.cell}: {unit}의 공개 출처 번호가 사실 장부와 다릅니다"
            )
        unused.remove(fact_id)
        return fact_id

    for index, (text, cite) in enumerate(section.prose_lines, start=1):
        take_exact_claim([text], f"prose {index}번 문장", cite)

    for table_index, table in enumerate(section.tables, start=1):
        if not table.is_valid:
            problems.append(
                f"[mapping] {section.cell}: 표 {table_index}의 열 수와 행 구성이 맞지 않습니다"
            )
        visible_cells = [str(cell).strip() for row in table.rows for cell in row]
        if any(_PUBLIC_AMOUNT_WITH_UNIT.search(cell) for cell in visible_cells):
            problems.append(
                f"[numeric] {section.cell}: 공개 표 {table_index}의 셀에는 "
                "원·억원 단위를 붙이지 말고 캡션에 단위를 한 번만 표시해야 합니다"
            )
        if table.display_unit == "억원":
            if "단위: 억원" not in table.caption:
                problems.append(
                    f"[numeric] {section.cell}: 공개 표 {table_index}에 단위: 억원을 명시해야 합니다"
                )
            if not table.raw_rows or table.scale_divisor != "100000000":
                problems.append(
                    f"[numeric] {section.cell}: 공개 억원 표 {table_index}는 "
                    "원 단위 원값과 100000000 환산 정보를 내부에 보존해야 합니다"
                )
            if any(
                not _PUBLIC_NUMBER.fullmatch(str(cell).strip())
                for row in table.rows
                for cell in row[1:]
            ):
                problems.append(
                    f"[numeric] {section.cell}: 공개 억원 표 {table_index}에는 "
                    "억원 표시값만 숫자로 넣어야 합니다"
                )
        for row_index, row in enumerate(table.rows, start=1):
            # 표 claim은 한 셀·행 결합 또는 조립기의 ``caption: cell | cell`` 정본과
            # 정확히 같아야 한다. 부분 문자열 매칭은 다른 주장을 잘못 연결할 수 있어 금지한다.
            fact_id = take_exact_claim(
                [
                    *row,
                    " ".join(str(cell) for cell in row),
                    " | ".join(str(cell) for cell in row),
                    f"{table.caption}: " + " | ".join(str(cell) for cell in row),
                ],
                f"표 {table_index}의 {row_index}번 행",
                table.cite,
            )
            if fact_id is None:
                continue
            fact = facts[fact_id]
            raw_row = (
                table.raw_rows[row_index - 1]
                if len(table.raw_rows) >= row_index
                else []
            )
            expected_raw, expected_display, expected_checks = _table_numeric_fields_for_gate(
                list(row[1:]),
                list(raw_row[1:]) if raw_row else None,
                table.scale_divisor,
                table.scale_places,
            )
            if (
                fact.raw_value != expected_raw
                or fact.display_value != expected_display
                or fact.numeric_checks != expected_checks
            ):
                problems.append(
                    f"[mapping] {section.cell}: 표 {table_index}의 {row_index}번 행과 "
                    "FactRecord 원값·표시값·numeric_checks가 정확히 결속되지 않았습니다"
                )
            evidence = (
                table.evidence_rows[row_index - 1]
                if len(table.evidence_rows) >= row_index
                else ""
            )
            # ``evidence_rows`` is an assembly-only conduit and is intentionally
            # omitted from public/cache serialization.  When it is present it
            # must match exactly; after deserialization the independently bound
            # FactRecord → Source evidence hash remains the durable proof.
            if evidence and fact.state_evidence != evidence:
                problems.append(
                    f"[evidence] {section.cell}: 표 {table_index}의 {row_index}번 행에 "
                    "결속된 실제 수집 payload와 다른 근거가 들어 있습니다"
                )

    if unused:
        problems.append(
            f"[mapping] {section.cell}: 공개 문장·표 행에 대응하지 않은 fact_id가 있습니다: "
            + ", ".join(unused)
        )

    visible_texts = [text for text, _cite in section.prose_lines]
    visible_texts.extend(table.caption for table in section.tables)
    visible_texts.extend(header for table in section.tables for header in table.headers)
    visible_texts.extend(
        str(cell)
        for table in section.tables
        for row in table.rows
        for cell in row
    )
    for index, text in enumerate(visible_texts, start=1):
        if problem := _forbidden_text_problem(text):
            problems.append(f"[scope] {section.cell} 공개 내용 {index}: {problem}")
    return problems


def _table_numeric_fields_for_gate(
    shown_values: list[str],
    raw_values: list[str] | None,
    divisor: str,
    places: int,
) -> tuple[str, str, list[str]]:
    """조립기와 독립적으로 공개 표의 원값 변환 계약을 재구성한다."""

    if raw_values and len(raw_values) == len(shown_values) and divisor:
        return (
            " | ".join(raw_values),
            " | ".join(shown_values),
            [
                f"{raw}|{divisor}|{places}|{shown}"
                for raw, shown in zip(raw_values, shown_values)
            ],
        )
    direct_raw: list[str] = []
    direct_display: list[str] = []
    checks: list[str] = []
    for value in shown_values:
        for token in _NUMBER_TOKEN.findall(value):
            places_in_token = len(token.rsplit(".", 1)[1]) if "." in token else 0
            direct_raw.append(token)
            direct_display.append(token)
            checks.append(f"{token}|1|{places_in_token}|{token}")
    return " | ".join(direct_raw), " | ".join(direct_display), checks


def _section_fact_ids(
    section: ReportSection,
    facts: dict[str, FactRecord],
    sources: dict[str, Source],
    confirmed_candidate_fact_ids: set[str] | None = None,
    report_as_of: str = "",
) -> tuple[list[str], list[str]]:
    supported: list[str] = []
    problems: list[str] = []
    seen: set[str] = set()
    for fact_id in section.fact_ids:
        clean_id = str(fact_id).strip()
        if not clean_id or clean_id in seen:
            if clean_id in seen:
                problems.append(f"[duplicate] {section.cell}: fact_id {clean_id}가 반복됐습니다")
            continue
        seen.add(clean_id)
        fact = facts.get(clean_id)
        if fact is None:
            problems.append(f"[fact] {section.cell}: fact_id {clean_id}를 장부에서 찾지 못했습니다")
            continue
        if fact.section_owner != section.cell:
            problems.append(
                f"[ownership] {clean_id}: {fact.section_owner} 소유 사실을 {section.cell}에서 참조했습니다"
            )
            continue
        fact_problems = _fact_problems(
            fact,
            sources,
            facts,
            confirmed_candidate_fact_ids,
            report_as_of,
        )
        if fact_problems:
            problems.extend(fact_problems)
            continue
        supported.append(clean_id)
    return supported, problems


def _declared_candidate_fact_ids(report: Report) -> set[str]:
    """중복되지 않은 실제 1~8장 ``fact_ids``만 후보 역참조 대상으로 삼는다."""

    counts: dict[str, int] = {}
    for section in report.sections:
        if section.cell in SECTION_BY_ID and section.cell != "competitive_position":
            counts[section.cell] = counts.get(section.cell, 0) + 1
    return {
        str(fact_id or "").strip()
        for section in report.sections
        if section.cell != "competitive_position"
        and counts.get(section.cell) == 1
        for fact_id in section.fact_ids
        if str(fact_id or "").strip()
    }


def _section_projection_problems(
    report: Report,
    section: ReportSection,
    supported_fact_ids: list[str],
    facts: dict[str, FactRecord],
    sources: dict[str, Source],
) -> list[str]:
    """장별 정본 질문이 공개 구조 블록에 빠짐없이 드러나는지 검사한다."""

    problems: list[str] = []
    visible_claims = {
        _normalized(text) for text, _cite in section.prose_lines if text.strip()
    }
    expected = [
        fact_id
        for fact_id in supported_fact_ids
        if _normalized(facts[fact_id].claim) in visible_claims
    ]
    ownership = {fact_id: 0 for fact_id in expected}
    blocks = section_content_blocks(report, section)
    if section.cell in REQUIRED_SECTION_IDS and not blocks:
        problems.append(
            f"[presentation] {section.cell}: 장별 필수 질문을 보여 주는 공개 구조 블록이 없습니다"
        )
        return problems

    for block_index, block in enumerate(blocks, start=1):
        if not block.title.strip():
            problems.append(
                f"[presentation] {section.cell}: 구조 블록 {block_index}의 제목이 비었습니다"
            )
        if not block.fields:
            problems.append(
                f"[presentation] {section.cell}: 구조 블록 {block_index}의 내용이 비었습니다"
            )
        elif len(block.fields) > 4:
            problems.append(
                f"[presentation] {section.cell}: 구조 블록 {block_index}의 "
                "소제목은 최대 4개여야 합니다"
            )
        labels: set[str] = set()
        for field_index, field in enumerate(block.fields, start=1):
            label = field.label.strip()
            if not label or not field.value.strip():
                problems.append(
                    f"[presentation] {section.cell}: 구조 블록 {block_index}의 "
                    f"{field_index}번 항목이 비었습니다"
                )
            elif label in labels:
                problems.append(
                    f"[presentation] {section.cell}: 구조 블록 {block_index}에 "
                    f"'{label}' 항목이 중복됐습니다"
                )
            labels.add(label)

        block_fact_ids = tuple(block.fact_ids)
        if len(block_fact_ids) != len(set(block_fact_ids)):
            problems.append(
                f"[presentation] {section.cell}: 구조 블록 {block_index}의 fact_id가 중복됐습니다"
            )
        for fact_id in block_fact_ids:
            if fact_id not in ownership:
                problems.append(
                    f"[presentation] {section.cell}: 구조 블록 {block_index}가 "
                    f"공개 prose 소유 사실이 아닌 {fact_id}를 참조합니다"
                )
                continue
            ownership[fact_id] += 1

        shown_numbers = set(block.source_numbers)
        if not shown_numbers:
            problems.append(
                f"[presentation] {section.cell}: 구조 블록 {block_index}의 공개 출처 번호가 없습니다"
            )
        if block_fact_ids:
            expected_numbers: set[int] = set()
            for fact_id in block_fact_ids:
                fact = facts.get(fact_id)
                if fact is None:
                    continue
                source_fact_ids = [fact.fact_id]
                if fact.claim_type == "change_interpretation":
                    source_fact_ids.extend(fact.basis_fact_ids)
                for source_fact_id in source_fact_ids:
                    source_fact = facts.get(source_fact_id)
                    if source_fact is None:
                        continue
                    for source_id in (
                        source_fact.source_id,
                        source_fact.comparator_source_id,
                        _comparison_candidate_source_id(
                            source_fact.comparison_basis
                        ),
                    ):
                        source = sources.get(source_id)
                        if source is not None:
                            expected_numbers.add(source.number)
            if shown_numbers != expected_numbers:
                problems.append(
                    f"[presentation] {section.cell}: 구조 블록 {block_index}의 "
                    "출처 번호가 결속된 자사·비교사 원문과 다릅니다"
                )

    for fact_id, count in ownership.items():
        if count != 1:
            problems.append(
                f"[presentation] {section.cell}: 공개 prose 사실 {fact_id}가 "
                f"구조 블록에 정확히 한 번 나타나지 않습니다 (현재 {count}회)"
            )
    return problems


def _completed_fiscal_years(
    report: Report, performance: list[FactRecord]
) -> tuple[int, int, int] | None:
    """완료 상태로 입증된 연속 3개 FY를 반환한다.

    12월 결산을 가정하지 않는다. 최신 FY는 보고서 기준연도 또는 직전연도만
    허용하며, 실제 완료 상태인 원자 사실만 연도 집합에 포함한다.
    """

    report_date = _parse_iso_date(report.as_of_date)
    if report_date is None:
        return None
    if any(
        fact.fact_status != "actual" or fact.time_state != "completed"
        for fact in performance
    ):
        return None
    years = sorted({fact.fiscal_year for fact in performance if fact.fiscal_year})
    if len(years) != 3 or years != list(range(years[0], years[0] + 3)):
        return None
    if years[-1] not in {report_date.year - 1, report_date.year}:
        return None
    return years[0], years[1], years[2]


def _semantic_section_problems(
    report: Report,
    sections: dict[str, ReportSection],
    supported_by_section: dict[str, list[str]],
    facts: dict[str, FactRecord],
) -> list[str]:
    """제목만 채운 1~9장을 통과시키지 않는 장별 최소 내용 계약."""

    problems: list[str] = []

    identity = [facts[fid] for fid in supported_by_section.get("identity", [])]
    if not any(fact.claim_type == "identity_summary" for fact in identity):
        problems.append(
            "[section] identity: 공식 사업 근거를 쉬운 말로 합성한 "
            "identity_summary가 필요합니다"
        )

    business = [facts[fid] for fid in supported_by_section.get("business_model", [])]
    business_types = {fact.claim_type for fact in business}
    for required_type in ("revenue_model", "customer_market"):
        if required_type not in business_types:
            problems.append(
                f"[section] business_model: {required_type} 원자 사실이 필요합니다"
            )
    portfolio = [facts[fid] for fid in supported_by_section.get("portfolio", [])]
    products = [fact for fact in portfolio if fact.claim_type == "priority_product"]
    product_scopes = {_normalized(fact.subject_scope) for fact in products}
    if not (1 <= len(products) <= 3) or len(product_scopes) != len(products):
        problems.append(
            "[section] portfolio: 확인된 서로 다른 핵심 제품·서비스를 1~3개만 담아야 합니다"
        )
    if any(not fact.product_role.strip() for fact in products):
        problems.append(
            "[section] portfolio: 각 핵심 제품·서비스의 product_role이 필요합니다"
        )
    if any(fact.product_role.strip() in PORTFOLIO_STAGES for fact in products):
        problems.append(
            "[section] portfolio: product_role은 선택 단계가 아니라 기능적 사업 역할이어야 합니다"
        )
    if any(
        fact.portfolio_stage.strip()
        and fact.portfolio_stage.strip() not in PORTFOLIO_STAGES
        for fact in products
    ):
        problems.append(
            "[section] portfolio: portfolio_stage는 신규·성장·주력·안정만 허용합니다"
        )
    business_fact_ids = set(supported_by_section.get("business_model", []))
    for product in products:
        revenue_fact = facts.get(product.revenue_model_fact_id)
        if (
            revenue_fact is None
            or product.revenue_model_fact_id not in business_fact_ids
            or revenue_fact.claim_type != "revenue_model"
        ):
            problems.append(
                "[section] portfolio: 각 제품은 2장 revenue_model 사실을 참조해야 합니다"
            )

    past_ids = supported_by_section.get("past_changes", [])
    past = [facts[fid] for fid in past_ids]
    performance = [fact for fact in past if fact.claim_type == "historical_performance"]
    expected_years = _completed_fiscal_years(report, performance)
    if expected_years is None:
        problems.append(
            "[section] past_changes: 최신 연도가 기준연도 또는 직전연도인 "
            "연속 3개 완료 사업연도 실적이 필요합니다 "
            f"(선언 분석 기간: {report.analysis_period})"
        )
    compact_period = report.analysis_period.replace(" ", "")
    has_exact_years = expected_years is not None and (
        all(str(year) in compact_period for year in expected_years)
        or f"{expected_years[0]}~{expected_years[-1]}" in compact_period
        or f"{expected_years[0]}-{expected_years[-1]}" in compact_period
    )
    if expected_years is not None and not has_exact_years:
        problems.append(
            "[period] analysis_period에 완료 사업연도 세 개가 모두 표시되지 않았습니다"
        )
    if "완료" not in report.analysis_period:
        problems.append("[period] analysis_period에 완료 회계연도임을 명시해야 합니다")
    past_section = sections.get("past_changes")
    performance_tables = [
        table
        for table in (past_section.tables if past_section is not None else [])
        if table.display_unit == "억원"
        and table.headers
        and table.headers[0] == "사업연도"
    ]
    if expected_years is not None:
        expected_desc = [str(year) for year in reversed(expected_years)]
        matching_tables = [
            table
            for table in performance_tables
            if len(table.rows) == 3
            and [str(row[0]).strip() for row in table.rows if row] == expected_desc
            and len(table.raw_rows) == 3
            and len(table.evidence_rows) in {0, 3}
        ]
        if len(matching_tables) != 1:
            problems.append(
                "[section] past_changes: 연속 3개 완료 FY가 한 개의 공개 실적표·원값·실제 payload에 결속돼야 합니다"
            )
    executions = [fact for fact in past if fact.claim_type == "completed_execution"]
    interpretations = [fact for fact in past if fact.claim_type == "change_interpretation"]
    if not executions:
        problems.append("[section] past_changes: 확인된 완료 실행 사실이 필요합니다")
    report_date = _parse_iso_date(report.as_of_date)
    if report_date is None:
        problems.append("[period] as_of_date에서 최근 36개월을 계산할 수 없습니다")
    else:
        try:
            execution_cutoff = report_date.replace(year=report_date.year - 3)
        except ValueError:
            execution_cutoff = report_date.replace(year=report_date.year - 3, day=28)
        for fact in executions:
            event_value = str(fact.event_date or "").strip()
            event = (
                _parse_iso_date(event_value)
                if len(event_value) == 10
                else date(int(event_value), 1, 1)
                if re.fullmatch(r"20\d{2}", event_value)
                else None
            )
            if event is None or not (execution_cutoff <= event <= report_date):
                problems.append(
                    f"[period] {fact.fact_id}: 완료 실행은 보고서 기준일 전 최근 36개월 안이어야 합니다"
                )
    if not interpretations:
        problems.append("[section] past_changes: 변화·실행 해석 사실이 필요합니다")
    for fact in interpretations:
        if not fact.basis_fact_ids:
            problems.append(
                f"[section] {fact.fact_id}: 변화 해석의 basis_fact_ids가 비었습니다"
            )
            continue
        for basis_id in fact.basis_fact_ids:
            basis = facts.get(basis_id)
            if basis is None or basis_id not in past_ids or basis.claim_type not in {
                "completed_execution",
                "historical_performance",
            }:
                problems.append(
                    f"[section] {fact.fact_id}: 변화 해석 근거 {basis_id}가 완료 실행·실적 사실이 아닙니다"
                )

    current_ids = supported_by_section.get("current_challenges", [])
    current = [facts[fid] for fid in current_ids]
    # 5장은 공식 근거가 한 건도 없으면 조건부 생략한다. 다만 한 건이라도
    # 출고 후보로 들어왔으면 종전 계약(문제+대응, 내부 결속, 최대 3개)을 그대로
    # 적용한다. 불완전한 5장을 단순 누락으로 위장해 통과시키지 않는다.
    if current:
        issues = [fact for fact in current if fact.claim_type == "current_issue"]
        responses = [fact for fact in current if fact.claim_type == "current_response"]
        if not issues or not responses:
            problems.append(
                "[section] current_challenges: 미해결 문제와 실제 대응이 모두 필요합니다"
            )
        if len(issues) > 3:
            problems.append(
                "[section] current_challenges: 근거가 확인된 핵심 과제는 최대 3개입니다"
            )
        issue_ids = {fact.fact_id for fact in issues}
        paired_issue_ids: set[str] = set()
        for response in responses:
            if response.response_to_fact_id not in issue_ids:
                problems.append(
                    f"[section] {response.fact_id}: response_to_fact_id가 같은 "
                    "장의 미해결 문제를 가리키지 않습니다"
                )
            else:
                paired_issue_ids.add(response.response_to_fact_id)
                issue = facts[response.response_to_fact_id]
                if not response_is_bound_to_issue(
                    issue.subject_scope,
                    issue.state_evidence,
                    response.state_evidence,
                    issue.legal_entity,
                ):
                    problems.append(
                        f"[section] {response.fact_id}: 대응 원문이 연결된 문제의 "
                        "대상·범위와 결속되지 않았습니다"
                    )
        unpaired = issue_ids - paired_issue_ids
        if unpaired:
            problems.append(
                "[section] current_challenges: 실제 대응과 결속되지 않은 문제가 있습니다: "
                + ", ".join(sorted(unpaired))
            )

    future = [facts[fid] for fid in supported_by_section.get("future_strategy", [])]
    plans = [fact for fact in future if fact.claim_type == "future_plan"]
    if not (1 <= len(plans) <= 3):
        problems.append(
            "[section] future_strategy: 근거가 확인된 미실행 계획을 1~3개만 "
            "담아야 합니다"
        )
    if report_date is not None:
        for fact in plans:
            if fact.plan_timing and plan_timing_has_passed(
                fact.plan_timing, report_date
            ):
                problems.append(
                    f"[period] {fact.fact_id}: 기준일 전에 예정 시점이 지난 계획은 "
                    "최신 미래 전략으로 쓸 수 없습니다"
                )

    operations = [
        facts[fid] for fid in supported_by_section.get("operations_partners", [])
    ]
    if not any(
        fact.claim_type in {"operating_core", "partner_role"}
        for fact in operations
    ):
        problems.append(
            "[section] operations_partners: 현재 운영 구조 또는 파트너 역할이 "
            "필요합니다"
        )
    if len(operations) > 4:
        problems.append(
            "[section] operations_partners: 현재 운영 구조·파트너 역할은 최대 "
            "4개입니다"
        )
    if report_date is not None:
        try:
            operations_cutoff = report_date.replace(year=report_date.year - 3)
        except ValueError:
            operations_cutoff = report_date.replace(
                year=report_date.year - 3, day=28
            )
        for fact in operations:
            operation_date = _parse_iso_date(fact.as_of)
            if operation_date is None or not (
                operations_cutoff <= operation_date <= report_date
            ):
                problems.append(
                    f"[period] {fact.fact_id}: 7장 현재 운영 관계는 기준일 전 "
                    "최근 36개월 자료로 확인해야 합니다"
                )

    # 숫자표만으로 과거 장을 채우지 못하도록 공개 해석 문장을 별도로 요구한다.
    if past_section is not None and interpretations:
        shown = {_normalized(text) for text, _cite in past_section.prose_lines}
        if not any(_normalized(fact.claim) in shown for fact in interpretations):
            problems.append(
                "[section] past_changes: 변화·실행 해석이 공개 prose에 나타나야 합니다"
            )
    return problems


def _summary_problems(
    item: object,
    index: int,
    included_set: set[str],
    facts: dict[str, FactRecord],
) -> list[str]:
    problems: list[str] = []
    text = str(getattr(item, "text", "")).strip()
    section_id = str(getattr(item, "section_id", ""))
    fact_ids = list(getattr(item, "fact_ids", []) or [])
    evidence_text = str(getattr(item, "evidence_text", ""))
    status = str(getattr(item, "verification_status", ""))
    binding = str(getattr(item, "verification_binding", ""))
    support_terms = list(getattr(item, "support_terms", []) or [])

    if section_id not in included_set:
        problems.append(
            f"[summary] {index}번 요약이 공개본에 없는 장 {section_id}을 참조합니다"
        )
    if not fact_ids:
        problems.append(f"[summary] {index}번 요약에 fact_id가 없습니다")
        return problems
    if len(fact_ids) != 1:
        problems.append(
            f"[summary] {index}번 요약은 검증 본문 fact_id 정확히 한 개만 참조해야 합니다"
        )
    if len(fact_ids) != len(set(fact_ids)):
        problems.append(f"[summary] {index}번 요약의 fact_id가 중복됐습니다")
    bound_facts: dict[str, FactRecord] = {}
    for fact_id in fact_ids:
        fact = facts.get(fact_id)
        if fact is None:
            problems.append(f"[summary] {index}번 요약의 fact_id {fact_id}가 없습니다")
        elif fact.section_owner != section_id:
            problems.append(
                f"[summary] {index}번 요약이 다른 장의 사실 {fact_id}를 참조합니다"
            )
        else:
            bound_facts[fact_id] = fact
    if len(bound_facts) != len(fact_ids):
        return problems
    if len(fact_ids) == 1:
        bound_claim = str(bound_facts[fact_ids[0]].claim).strip()
        if text != bound_claim:
            problems.append(
                f"[summary] {index}번 요약은 결속된 검증 본문 문장을 글자 그대로 재사용해야 합니다"
            )

    expected_evidence = summary_evidence_text(fact_ids, facts)
    if evidence_text != expected_evidence:
        problems.append(
            f"[summary] {index}번 요약의 evidence_text가 결속된 claim 묶음과 다릅니다"
        )
    if status != SUMMARY_VERIFICATION_STATUS:
        problems.append(
            f"[summary] {index}번 요약은 검증된 본문 재사용 상태가 아닙니다"
        )
    normalized_terms = [_normalized(term) for term in support_terms if _normalized(term)]
    if len(set(normalized_terms)) < 2:
        problems.append(
            f"[summary] {index}번 요약에는 서로 다른 support_terms가 두 개 이상 필요합니다"
        )
    joined_claims = _normalized(expected_evidence)
    normalized_summary = _normalized(text)
    for term in normalized_terms:
        if len(_compact(term)) < 2 or term not in normalized_summary or term not in joined_claims:
            problems.append(
                f"[summary] {index}번 요약의 근거어 '{term}'가 요약과 claim 양쪽에 없습니다"
            )
    if _CAUSAL_PATTERN.search(text) and not any(
        fact.supports_causality for fact in bound_facts.values()
    ):
        problems.append(
            f"[summary] {index}번 요약이 결속 사실에 없는 인과를 추가했습니다"
        )
    expected_binding = summary_verification_binding(
        text,
        section_id,
        fact_ids,
        evidence_text,
        status,
        support_terms,
    )
    if not binding or binding != expected_binding:
        problems.append(
            f"[summary] {index}번 요약의 검증 본문 재사용 결속 지문이 일치하지 않습니다"
        )
    return problems


def validate_publishable(report: Report) -> PublishValidation:
    """정본상 출고 가능한지 전수 검사한다.

    1~8장 기본 보고서, 조건이 맞을 때의 9장, 검증 본문 재사용 요약 3~5개, 원문
    해시에 결속된 원자 사실과 장별 최소 내용 계약을 전수 검사한다.
    """

    reasons: list[str] = []
    report_date = _parse_iso_date(report.as_of_date)
    if report.schema_version != CANONICAL_SCHEMA_VERSION:
        reasons.append(
            f"[schema] {CANONICAL_SCHEMA_VERSION} 보고서만 canonical 출고할 수 있습니다"
        )
    for field_name, value in (
        ("as_of_date", report.as_of_date),
        ("analysis_period", report.analysis_period),
        ("latest_performance_period", report.latest_performance_period),
    ):
        if not str(value).strip():
            reasons.append(f"[period] {field_name}가 비었습니다")

    source_id_counts: dict[str, int] = {}
    source_number_counts: dict[int, int] = {}
    source_document_owners: dict[tuple[str, str, str, str], str] = {}
    for item in report.citations:
        if not isinstance(item, Source):
            continue
        if not item.is_canonical_valid:
            reasons.append(
                f"[source] {item.source_id or item.number}: host·URL·document_id·날짜·원문 해시 신원이 불완전합니다"
            )
        if report_date is not None:
            for label, value in (
                ("published_at", item.published_at),
                ("disclosed_at", item.disclosed_at),
                ("collected_at", item.collected_at),
            ):
                if not str(value).strip():
                    continue
                parsed = _parse_iso_date(value)
                if parsed is None or parsed > report_date:
                    reasons.append(
                        f"[time] {item.source_id or item.number}: {label}가 보고서 기준일 뒤이거나 ISO 날짜가 아닙니다"
                    )
        if item.source_id.strip():
            source_id = item.source_id.strip()
            source_id_counts[source_id] = source_id_counts.get(source_id, 0) + 1
            document_identity = _source_document_identity(item)
            previous_source = source_document_owners.get(document_identity)
            if previous_source is not None and previous_source != source_id:
                reasons.append(
                    f"[duplicate] 같은 URL·document_id·위치·원문해시 조각이 {previous_source}와 "
                    f"{source_id}로 복제 등록됐습니다"
                )
            else:
                source_document_owners[document_identity] = source_id
        if item.number <= 0:
            reasons.append("[source] 출처 번호는 1 이상의 정수여야 합니다")
        else:
            source_number_counts[item.number] = source_number_counts.get(item.number, 0) + 1
    for source_id, count in source_id_counts.items():
        if count > 1:
            reasons.append(f"[duplicate] source_id {source_id}가 두 번 등록됐습니다")
    for source_number, count in source_number_counts.items():
        if count > 1:
            reasons.append(f"[duplicate] 출처 번호 [{source_number}]가 두 번 등록됐습니다")

    sources = _source_registry(report)
    if not sources:
        reasons.append("[source] canonical 원문 등록부가 비었습니다")

    facts: dict[str, FactRecord] = {}
    atomic_owners: dict[tuple[str, ...], str] = {}
    semantic_owners: dict[tuple[str, ...], str] = {}
    claim_owners: dict[str, str] = {}
    numeric_owners: dict[tuple[str, str], str] = {}
    for fact in report.fact_records:
        fact_id = fact.fact_id.strip()
        if not fact_id:
            reasons.append("[fact] 빈 fact_id가 있습니다")
            continue
        if fact_id in facts:
            reasons.append(f"[duplicate] fact_id {fact_id}가 두 번 등록됐습니다")
            continue
        facts[fact_id] = fact
        if not _corporate_name(report.company) or (
            _corporate_name(fact.legal_entity) != _corporate_name(report.company)
        ):
            reasons.append(
                f"[ownership] {fact_id}: FactRecord.legal_entity가 report.company와 다릅니다"
            )
        if report_date is not None:
            for label, value in (("as_of", fact.as_of), ("source_date", fact.source_date)):
                parsed = _parse_iso_date(value)
                if parsed is None or parsed > report_date:
                    reasons.append(
                        f"[time] {fact_id}: {label}가 보고서 기준일 뒤이거나 ISO 날짜가 아닙니다"
                    )
            if fact.event_date:
                event_value = str(fact.event_date).strip()
                event = (
                    _parse_iso_date(event_value)
                    if len(event_value) == 10
                    else date(int(event_value), 1, 1)
                    if re.fullmatch(r"20\d{2}", event_value)
                    else None
                )
                if event is None or event > report_date:
                    reasons.append(
                        f"[time] {fact_id}: event_date가 보고서 기준일 뒤이거나 올바르지 않습니다"
                    )
            if (
                fact.claim_type == "future_plan"
                and fact.plan_timing
                and plan_timing_has_passed(fact.plan_timing, report_date)
            ):
                reasons.append(
                    f"[period] {fact_id}: 기준일 전에 예정 시점이 지난 계획은 "
                    "최신 미래 전략으로 쓸 수 없습니다"
                )
        atomic_key = _atomic_key(fact)
        previous = atomic_owners.get(atomic_key)
        if previous is not None:
            reasons.append(
                f"[duplicate] 같은 원자 사실이 {previous}와 {fact_id}로 중복 등록됐습니다"
            )
        else:
            atomic_owners[atomic_key] = fact_id
        semantic_key = _semantic_duplicate_key(fact)
        previous_semantic = semantic_owners.get(semantic_key)
        if previous_semantic is not None:
            reasons.append(
                f"[duplicate] 의미가 같은 사실이 {previous_semantic}와 {fact_id}로 중복 등록됐습니다"
            )
        else:
            semantic_owners[semantic_key] = fact_id
        claim_key = _compact(fact.claim)
        previous_claim = claim_owners.get(claim_key)
        if claim_key and previous_claim is not None:
            reasons.append(
                f"[duplicate] 같은 claim이 {previous_claim}와 {fact_id}로 중복 등록됐습니다"
            )
        elif claim_key:
            claim_owners[claim_key] = fact_id
        raw_numbers = tuple(
            str(value)
            for token in _NUMBER_TOKEN.findall(fact.raw_value)
            if (value := _decimal(token)) is not None
            and not (Decimal(1900) <= abs(value) <= Decimal(2100))
        )
        if raw_numbers:
            numeric_key = (
                _corporate_name(fact.legal_entity) + ":" + fact.source_id,
                "|".join(raw_numbers),
            )
            previous_numeric = numeric_owners.get(numeric_key)
            if previous_numeric is not None:
                reasons.append(
                    f"[duplicate] 같은 원시 수치 묶음이 {previous_numeric}와 {fact_id}에 반복됐습니다"
                )
            else:
                numeric_owners[numeric_key] = fact_id

    sections: dict[str, ReportSection] = {}
    supported_by_section: dict[str, list[str]] = {}
    confirmed_candidate_fact_ids = _declared_candidate_fact_ids(report)
    for section in report.sections:
        if section.cell not in SECTION_BY_ID:
            continue
        if section.cell in sections:
            reasons.append(f"[section] {section.cell} 장이 두 번 등록됐습니다")
            continue
        sections[section.cell] = section
        supported, problems = _section_fact_ids(
            section,
            facts,
            sources,
            confirmed_candidate_fact_ids,
            report.as_of_date,
        )
        supported_by_section[section.cell] = supported
        reasons.extend(problems)
        reasons.extend(_section_content_problems(section, supported, facts, sources))
        reasons.extend(
            _section_projection_problems(report, section, supported, facts, sources)
        )

    included = tuple(
        section_id
        for section_id in CANONICAL_SECTION_IDS
        if supported_by_section.get(section_id)
    )
    included_set = set(included)
    for section_id in sorted(REQUIRED_SECTION_IDS - included_set):
        reasons.append(f"[section] 필수 장 {section_id}에 검증된 근거가 없습니다")
    reasons.extend(
        _semantic_section_problems(report, sections, supported_by_section, facts)
    )

    if not SUMMARY_MIN_ITEMS <= len(report.summary_items) <= SUMMARY_MAX_ITEMS:
        reasons.append("[summary] 핵심 요약은 3~5개여야 합니다")
    summary_texts: set[str] = set()
    summary_sections: set[str] = set()
    for index, item in enumerate(report.summary_items, start=1):
        text = item.text.strip()
        if not text:
            reasons.append(f"[summary] {index}번 요약이 비었습니다")
        normalized_text = _normalized(text)
        if normalized_text in summary_texts:
            reasons.append(f"[summary] {index}번 요약이 중복됐습니다")
        summary_texts.add(normalized_text)
        section_id = str(item.section_id or "").strip()
        if section_id in summary_sections:
            reasons.append(
                f"[summary] {index}번 요약이 같은 장 {section_id}을 중복 참조합니다"
            )
        summary_sections.add(section_id)
        reasons.extend(_summary_problems(item, index, included_set, facts))
        if problem := _forbidden_text_problem(text):
            reasons.append(f"[scope] {index}번 요약: {problem}")

    unique_reasons = tuple(dict.fromkeys(reasons))
    return PublishValidation(not unique_reasons, unique_reasons, included)


def build_published_report(report: Report) -> Report:
    """검증된 핵심 장과 근거가 있는 조건부 5·8·9장을 정본 순서로 잠근다."""

    validation = validate_publishable(report)
    if not validation:
        raise PublishBlockedError(validation)

    by_id = {section.cell: section for section in report.sections}
    facts = {fact.fact_id: fact for fact in report.fact_records}
    sources = _source_registry(report)
    confirmed_candidate_fact_ids = _declared_candidate_fact_ids(report)
    published_sections: list[ReportSection] = []
    used_fact_ids: set[str] = set()
    for spec in SECTION_SPECS:
        if spec.section_id not in validation.included_section_ids:
            continue
        section = by_id[spec.section_id]
        supported, _problems = _section_fact_ids(
            section,
            facts,
            sources,
            confirmed_candidate_fact_ids,
            report.as_of_date,
        )
        used_fact_ids.update(supported)
        published_sections.append(
            replace(
                section,
                cell=spec.section_id,
                title=spec.title,
                display_number=spec.display_number,
                tag=spec.tag,
                fact_ids=supported,
                guidance_lines=[],
                empty_reason="",
            )
        )

    published_facts = [
        fact for fact in report.fact_records if fact.fact_id in used_fact_ids
    ]
    used_source_ids = {fact.source_id for fact in published_facts}
    used_source_ids.update(
        fact.comparator_source_id
        for fact in published_facts
        if fact.comparator_source_id
    )
    used_source_ids.update(
        attester_source_id
        for fact in published_facts
        if (
            attester_source_id := _comparison_candidate_attester_source_id(
                fact.comparison_basis
            )
        )
    )
    used_source_ids.update(
        candidate_source_id
        for fact in published_facts
        if (
            candidate_source_id := _comparison_candidate_source_id(
                fact.comparison_basis
            )
        )
    )
    source_usage: dict[str, set[str]] = {}
    for fact in published_facts:
        source_usage.setdefault(fact.source_id, set()).add(fact.section_owner)
        if fact.comparator_source_id:
            source_usage.setdefault(fact.comparator_source_id, set()).add(
                fact.section_owner
            )
        if candidate_source_id := _comparison_candidate_source_id(
            fact.comparison_basis
        ):
            source_usage.setdefault(candidate_source_id, set()).add(
                fact.section_owner
            )
        if attester_source_id := _comparison_candidate_attester_source_id(
            fact.comparison_basis
        ):
            source_usage.setdefault(attester_source_id, set()).add(
                fact.section_owner
            )
    # 직접 Fact가 없는 공식 웹 도메인 attester도 공개 객체의 provenance 등록부에
    # 남긴다. 렌더러는 Fact가 직접 인용한 번호만 본문에 표시한다.
    report_sources = {
        item.source_id: item
        for item in report.citations
        if isinstance(item, Source) and item.source_id
    }
    pending_source_ids = list(used_source_ids)
    while pending_source_ids:
        source_id = pending_source_ids.pop(0)
        source = report_sources.get(source_id)
        if source is None:
            continue
        dependency_id = source.domain_attestation_source_id.strip()
        if not dependency_id or dependency_id not in report_sources:
            continue
        before = set(source_usage.get(dependency_id, set()))
        source_usage.setdefault(dependency_id, set()).update(
            source_usage.get(source_id, set())
        )
        if dependency_id not in used_source_ids:
            used_source_ids.add(dependency_id)
        if source_usage[dependency_id] != before:
            pending_source_ids.append(dependency_id)
    published_citations = sorted([
        replace(
            item,
            used_in=[
                section_id
                for section_id in CANONICAL_SECTION_IDS
                if section_id in source_usage.get(item.source_id, set())
            ],
        )
        for item in report.citations
        if isinstance(item, Source) and item.source_id in used_source_ids
    ], key=lambda item: item.number)

    included_set = set(validation.included_section_ids)
    missing_conditional = [
        spec.section_id
        for spec in SECTION_SPECS
        if spec.section_id in CONDITIONAL_SECTION_IDS
        and spec.section_id not in included_set
    ]
    shortfall_reasons = [
        CONDITIONAL_SECTION_SHORTFALL_REASONS[section_id]
        for section_id in missing_conditional
    ]
    is_partial_report = bool(shortfall_reasons)
    return replace(
        report,
        job="",
        grade=Grade.PARTIAL if is_partial_report else Grade.COMPLETE,
        sections=published_sections,
        requirements=[],
        sources=[],
        citations=published_citations,
        cells={section.cell: True for section in published_sections},
        shortfall_reasons=shortfall_reasons,
        schema_version=CANONICAL_SCHEMA_VERSION,
        fact_records=published_facts,
    )
