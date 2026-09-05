"""수집된 원문을 canonical(v4) 보고서로 잠그는 마지막 조립 단계.

AI는 원문 번호 선택과 가독성 편집에만 관여한다. 핵심 요약은 Reviewer 또는 원문
완전일치 코드 검증을 통과한 본문 문장을 그대로 재사용한다. 공개되는 문장과 표는
모두 하나의 ``FactRecord``와 하나 이상의 검증된 ``Source``에 연결되며, 출고
게이트를 통과하지 못하면 보고서 객체를 만들지 않는다.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, fields as dataclass_fields, replace
from datetime import date
from itertools import combinations
from typing import Any, Callable, Iterable

from src.core.citations import citation_number
from src.features.company_specificity.logic import assess_claim, verified_latin_names
from src.features.pipeline.port import (
    FactRecord,
    Grade,
    Report,
    ReportSection,
    ReportTable,
    SummaryItem,
)
from src.features.provenance.sources import (
    Source,
    evidence_text_hash,
    is_canonical_official_with_registry,
    official_ir_source_is_usable,
    official_web_currentness_is_usable,
    source_type_is_official_ir,
)
from src.features.report_standard import (
    CANONICAL_SCHEMA_VERSION,
    PublishBlockedError,
    PublishValidation,
    SUMMARY_VERIFICATION_STATUS,
    build_published_report,
)
from src.features.report_standard.publish import (
    fact_evidence_binding,
    historical_performance_numeric_evidence_is_bound,
    summary_evidence_text,
    summary_verification_binding,
)
from src.features.report_summary.logic import (
    VerifiedSummarySource,
    build_summary_from_verified_claims,
)
from src.features.spanselect.canonical import (
    CanonicalPick,
    PRIORITY_SIGNAL_PATTERNS,
    historical_performance_basis_sid,
    structured_company_binding_allows_specificity_failure,
    verified_official_ir_is_bound_to_company,
)
from src.features.writer import constants as writer_constants
from src.features.writer import logic as writer_logic
from src.features.writer import revision as writer_revision
from src.features.writer import verify as writer_verify


_NUMBER_RE = re.compile(r"(?<![A-Za-z가-힣])[-+]?\d[\d,]*(?:\.\d+)?\s*(?:%|％|원|억|만|명|개|건|회|배|곳|개국|도시|석)?")
_DEFAULT_PERFORMANCE_YEAR_COUNT = 3
_AUDIT_PERFORMANCE_YEAR_COUNT = 2
_AUDIT_REPORT_STATEMENT_SOURCE = "audit_report_statement"
_CAUSAL_TERMS = ("때문", "기여", "영향", "개선", "전환", "견인", "덕분")
_DIRECT_CAUSAL_RE = re.compile(
    r"(?P<cause>[^.!?]{2,120}?)(?P<connector>때문에|덕분에|(?:으)?로 인해|이에 따라|"
    r"그 결과|결과로|에 기여(?:했|한|한다)?|을 견인(?:했|한|한다)?|를 견인(?:했|한|한다)?)"
    r"(?P<outcome>[^.!?]{2,160})",
    re.IGNORECASE,
)
_ENGLISH_DUE_TO_RE = re.compile(
    r"(?P<outcome>[^.!?]{2,160}?)\s+due\s+to\s+"
    r"(?P<cause>[^.!?]{2,160})",
    re.IGNORECASE,
)
_RESPONSE_TERMS = ("대응", "추진 중", "협의 중", "개선 중", "준비 중", "도입", "투자", "개편")
_TERM_RE = re.compile(r"[A-Za-z]{2,}|[가-힣]{2,}|[-+]?\d[\d,]*(?:\.\d+)?")
_TERM_STOP = frozenset(
    {"회사는", "회사의", "해당", "현재", "관련", "대한", "통해", "기준", "공식"}
)
_TRANSLATED_PLAN_ACTION_PATTERNS: tuple[
    tuple[re.Pattern[str], re.Pattern[str]], ...
] = (
    (re.compile(r"\bmaximiz", re.I), re.compile(r"극대화|\bmaximiz", re.I)),
    (re.compile(r"\bexpand|\bexpansion", re.I), re.compile(r"확대|확장|\bexpand", re.I)),
    (re.compile(r"\breleas", re.I), re.compile(r"발매|출시|\breleas", re.I)),
    (re.compile(r"\btour", re.I), re.compile(r"투어|\btour", re.I)),
    (re.compile(r"\bpop[- ]?up", re.I), re.compile(r"팝업|\bpop[- ]?up", re.I)),
    (re.compile(r"\badd", re.I), re.compile(r"추가|\badd", re.I)),
    (re.compile(r"\bcollaborat", re.I), re.compile(r"협업|협력|\bcollaborat", re.I)),
    (re.compile(r"\blicens", re.I), re.compile(r"라이선스|\blicens", re.I)),
)
_VERIFIED_IR_WRITER_FAILURE_REASONS: dict[str, str] = {
    "priority_product": "현재 실행 근거가 있는 제품·서비스가 아님",
    "current_issue": "회사 고유 위험·변화 근거가 없음",
}

# 결과물이 다시 원문 덤프가 되지 않도록 공개 prose의 장별 상한을 고정한다.
# 표 행은 별도 사실 단위이며 이 수에 포함하지 않는다.
_PROSE_LIMITS: dict[str, int] = {
    "identity": 2,
    "business_model": 4,
    "portfolio": 3,
    "past_changes": 4,
    # 과제 최대 3개와 각 대응을 분리해 적을 수 있는 상한이다.
    "current_challenges": 6,
    "future_strategy": 3,
    "operations_partners": 4,
    "culture": 4,
    "competitive_position": 3,
}

_TIME_STATE: dict[str, str] = {
    "identity": "standing",
    "business_model": "standing",
    "portfolio": "standing",
    "past_changes": "completed",
    "future_strategy": "future_plan",
    "operations_partners": "standing",
    "culture": "standing",
    "competitive_position": "standing",
}

_SUMMARY_SECTION_PRIORITY: tuple[str, ...] = (
    "identity",
    "business_model",
    "portfolio",
    "current_challenges",
    "future_strategy",
    "operations_partners",
    "culture",
    "past_changes",
    "competitive_position",
)

_SUMMARY_CLAIM_PRIORITY: dict[str, int] = {
    "identity_summary": 0,
    "revenue_model": 0,
    "customer_market": 1,
    "priority_product": 0,
    "current_response": 0,
    "current_issue": 1,
    "future_plan": 0,
    "operating_core": 0,
    "partner_role": 1,
    "official_value": 0,
    "work_example": 1,
    "change_interpretation": 0,
    "completed_execution": 1,
    "historical_performance": 2,
    "competitive_comparison": 0,
}


@dataclass(frozen=True)
class WrittenClaim:
    """검증을 통과한 표시 문장과 그 문장이 실제로 사용한 원문 한 건."""

    section_id: str
    text: str
    cite: str
    evidence: str
    fragment_id: int
    sid: str = ""
    claim_type: str = ""
    subject_label: str = ""
    market_stage: str = ""
    market_observation: str = ""
    product_role: str = ""
    portfolio_stage: str = ""
    revenue_model_sid: str = ""
    response_to_sid: str = ""
    basis_sids: tuple[str, ...] = ()
    priority_signals: tuple[str, ...] = ()
    event_date: str = ""
    response_action: str = ""
    initial_signal: str = ""
    next_check_metric: str = ""
    plan_status: str = ""
    plan_timing: str = ""
    plan_condition: str = ""
    plan_expected_effect: str = ""
    plan_execution_signal: str = ""
    operation_role: str = ""
    value_chain_stage: str = ""
    relationship_type: str = ""


MINIMUM_WRITTEN_ROLE_IDENTITY = "identity"
MINIMUM_WRITTEN_ROLE_REVENUE = "revenue"
_MINIMUM_WRITTEN_IDENTITY_TYPES = frozenset(
    {"identity_summary", "official_self_definition", "operating_scope"}
)


def missing_minimum_written_roles(
    claims: Iterable[WrittenClaim],
) -> tuple[str, ...]:
    """Writer·Reviewer 뒤 부분 보고서의 두 핵심 역할만 닫힌 값으로 센다."""

    claim_types = {item.claim_type for item in claims}
    missing: list[str] = []
    if not (_MINIMUM_WRITTEN_IDENTITY_TYPES & claim_types):
        missing.append(MINIMUM_WRITTEN_ROLE_IDENTITY)
    if "revenue_model" not in claim_types:
        missing.append(MINIMUM_WRITTEN_ROLE_REVENUE)
    return tuple(missing)


def _prune_unbound_optional_claims(
    claims: Iterable[WrittenClaim],
) -> list[WrittenClaim]:
    """Writer·Reviewer 뒤 끊어진 관계 중 독립 검증할 수 없는 쪽만 생략한다.

    선택 단계에서 완결됐던 제품→수익, 문제→대응, 실행→해석 관계도 Writer나
    Reviewer가 한쪽 문장을 삭제하면 다시 불완전해질 수 있다. 제품의 수익 연결,
    대응의 문제 연결, 해석의 실행 근거는 계속 필수로 두되, 원문만으로 완결되는
    미해결 문제와 완료 실행은 단독으로 보존한다.
    """

    items = list(claims)
    # 제품은 실제 공개되는 수익 구조만 참조할 수 있다.
    revenue_sids = {
        item.sid for item in items if item.claim_type == "revenue_model" and item.sid
    }

    # 대응은 같은 답에 남은 문제를 가리켜야 한다. 문제 자체는 원문·미해결 상태·
    # 다음 확인 지표를 독립 검증했으므로 대응이 탈락해도 보존한다.
    issues = {
        item.sid: item
        for item in items
        if item.claim_type == "current_issue" and item.sid
    }
    valid_response_sids = {
        item.sid
        for item in items
        if item.claim_type == "current_response"
        and item.sid
        and item.response_to_sid in issues
    }
    # 변화 해석은 남아 있는 완료 실행이나 프로그램 생성 3개년 사실만 참조한다.
    # 완료 실행 자체는 원문·완료 상태·사건일 검증을 통과했으므로 단독 보존한다.
    execution_sids = {
        item.sid
        for item in items
        if item.claim_type == "completed_execution" and item.sid
    }
    valid_interpretation_sids: set[str] = set()
    for item in items:
        if item.claim_type != "change_interpretation" or not item.sid:
            continue
        bases = tuple(str(value or "").strip() for value in item.basis_sids)
        if not bases or not all(bases):
            continue
        internal = {
            value
            for value in bases
            if re.fullmatch(r"historical-performance:20\d{2}", value) is None
        }
        if not execution_sids or not internal <= execution_sids:
            continue
        valid_interpretation_sids.add(item.sid)

    out: list[WrittenClaim] = []
    for item in items:
        if item.claim_type == "priority_product" and (
            not item.revenue_model_sid or item.revenue_model_sid not in revenue_sids
        ):
            continue
        if item.claim_type == "current_response" and item.sid not in valid_response_sids:
            continue
        if item.claim_type == "change_interpretation" and (
            item.sid not in valid_interpretation_sids
        ):
            continue
        out.append(item)
    return out


def _drop_stale_completed_executions(
    claims: Iterable[WrittenClaim],
    *,
    as_of_date: str,
) -> tuple[list[WrittenClaim], int]:
    """36개월 밖의 완료 실행만 생략하고 연결이 끊긴 해석도 함께 정리한다.

    잘못된 날짜나 기준일 뒤 날짜는 여기서 조용히 버리지 않는다. 그대로 남겨
    canonical 출고 게이트가 차단하게 하여 미래 사실이나 무근거 사실이 부분
    보고서라는 이름으로 통과하지 못하게 한다.
    """

    items = list(claims)
    try:
        report_date = date.fromisoformat(str(as_of_date or "").strip())
    except ValueError:
        return items, 0
    try:
        cutoff = report_date.replace(year=report_date.year - 3)
    except ValueError:
        cutoff = report_date.replace(year=report_date.year - 3, day=28)

    kept: list[WrittenClaim] = []
    stale_count = 0
    for item in items:
        if item.claim_type != "completed_execution":
            kept.append(item)
            continue
        event_value = str(item.event_date or "").strip()
        try:
            event = (
                date.fromisoformat(event_value)
                if re.fullmatch(r"20\d{2}-\d{2}-\d{2}", event_value)
                else date(int(event_value), 1, 1)
                if re.fullmatch(r"20\d{2}", event_value)
                else None
            )
        except ValueError:
            event = None
        if event is not None and event < cutoff:
            stale_count += 1
            continue
        kept.append(item)

    if not stale_count:
        return items, 0
    return _prune_unbound_optional_claims(kept), stale_count


def majority_picks(rounds: Iterable[Iterable[CanonicalPick]], *, minimum: int = 2) -> list[CanonicalPick]:
    """선택 결과에서 소유권과 각 구조 필드가 ``minimum``회 나온 사실을 남긴다.

    같은 원문이 서로 다른 섹션에서 동률이면 소유권을 추측하지 않고 버린다.
    자유형 구조 발췌는 전체 객체가 우연히 완전히 같을 것을 요구하지 않고 필드별
    합의를 확인한다. 어느 필드든 필요한 표 수가 없으면 그 사실 전체를 버린다.
    출력 순서는 원문 조각 번호와 원문 안 등장 순서가 안정적으로 결정한다.
    """

    by_sentence: dict[tuple[str, int], list[CanonicalPick]] = defaultdict(list)
    for round_items in rounds:
        for item in set(round_items):
            by_sentence[(item.sentence, item.fragment_id)].append(item)

    kept: list[CanonicalPick] = []
    identity_fields = {"section_id", "sentence", "fragment_id", "sid", "claim_type"}
    for _sentence_key, candidates in by_sentence.items():
        ownership_counts = Counter(
            (item.section_id, item.sid, item.claim_type) for item in candidates
        )
        if not ownership_counts:
            continue
        best = max(ownership_counts.values())
        ownership_winners = [
            value
            for value, count in ownership_counts.items()
            if count == best and count >= minimum
        ]
        if len(ownership_winners) != 1:
            continue
        section_id, sid, claim_type = ownership_winners[0]
        owned = [
            item
            for item in candidates
            if (item.section_id, item.sid, item.claim_type)
            == (section_id, sid, claim_type)
        ]
        changes: dict[str, object] = {}
        fields_agree = True
        for info in dataclass_fields(CanonicalPick):
            if info.name in identity_fields:
                continue
            value_counts = Counter(getattr(item, info.name) for item in owned)
            field_best = max(value_counts.values())
            field_winners = [
                value
                for value, count in value_counts.items()
                if count == field_best and count >= minimum
            ]
            if len(field_winners) != 1:
                fields_agree = False
                break
            changes[info.name] = field_winners[0]
        if not fields_agree:
            continue
        kept.append(
            replace(
                owned[0],
                section_id=section_id,
                sid=sid,
                claim_type=claim_type,
                **changes,
            )
        )
    kept_by_sid = {item.sid: item for item in kept}
    bound: list[CanonicalPick] = []
    for item in kept:
        target: CanonicalPick | None = None
        if item.claim_type == "priority_product":
            target = kept_by_sid.get(item.revenue_model_sid)
            if target is None or target.claim_type != "revenue_model":
                continue
        elif item.claim_type == "current_response":
            target = kept_by_sid.get(item.response_to_sid)
            if target is None or target.claim_type != "current_issue":
                continue
        elif item.claim_type == "change_interpretation":
            internal_bases = tuple(
                value
                for value in item.basis_sids
                if re.fullmatch(r"historical-performance:20\d{2}", value) is None
            )
            if any(
                (target := kept_by_sid.get(value)) is None
                or target.claim_type != "completed_execution"
                for value in internal_bases
            ):
                continue
        bound.append(item)
    return sorted(
        bound,
        key=lambda item: (item.fragment_id, item.section_id, item.sentence),
    )


def combine_validated_picks(
    rounds: Iterable[Iterable[CanonicalPick]],
) -> list[CanonicalPick]:
    """각 회차에서 이미 검증된 사실을 충돌 없이 누적한다.

    재선택 회차는 서로 다른 필수 역할을 보완할 수 있으므로 한 회차에 한 번만
    나온 사실도 보존한다. 다만 같은 SID가 서로 다른 원문을 가리키거나 같은
    원문 조각의 소유 장·SID·사실 유형이 갈리면 어느 쪽도 추측하지 않고 버린다.
    마지막 결합은 ``majority_picks(minimum=1)``에 맡겨 구조 필드 합의와
    제품→수익 구조, 대응→문제, 변화 해석→완료 실행 연결 검사도 그대로 거친다.
    """

    materialized = [tuple(round_items) for round_items in rounds]
    sid_bindings: dict[str, set[tuple[str, str, int, str]]] = defaultdict(set)
    evidence_owners: dict[
        tuple[str, int], set[tuple[str, str, str]]
    ] = defaultdict(set)
    for round_items in materialized:
        for item in round_items:
            if not item.sid:
                continue
            sid_bindings[item.sid].add(
                (
                    item.section_id,
                    item.sentence,
                    item.fragment_id,
                    item.claim_type,
                )
            )
            evidence_owners[(item.sentence, item.fragment_id)].add(
                (item.section_id, item.sid, item.claim_type)
            )

    conflicting_sids = {
        sid for sid, bindings in sid_bindings.items() if len(bindings) != 1
    }
    conflicting_evidence = {
        evidence
        for evidence, owners in evidence_owners.items()
        if len(owners) != 1
    }
    safe_rounds = [
        [
            item
            for item in round_items
            if item.sid
            and item.sid not in conflicting_sids
            and (item.sentence, item.fragment_id) not in conflicting_evidence
        ]
        for round_items in materialized
    ]
    return majority_picks(safe_rounds, minimum=1)


def historical_performance_required_year_count(table: Any) -> int:
    """검증 가능한 감사보고서 표식에만 2개년 정책을 적용한다."""

    rows = tuple(getattr(table, "rows", ()) or ())
    evidence_rows = tuple(getattr(table, "evidence_rows", ()) or ())
    if (
        len(rows) != _AUDIT_PERFORMANCE_YEAR_COUNT
        or len(evidence_rows) != len(rows)
    ):
        return _DEFAULT_PERFORMANCE_YEAR_COUNT
    for evidence in evidence_rows:
        try:
            payload = json.loads(str(evidence))
        except (json.JSONDecodeError, TypeError, ValueError):
            return _DEFAULT_PERFORMANCE_YEAR_COUNT
        if not isinstance(payload, dict):
            return _DEFAULT_PERFORMANCE_YEAR_COUNT
        excerpt = payload.get("source_excerpt")
        digest = str(payload.get("source_sha256") or "")
        if (
            payload.get("source") != _AUDIT_REPORT_STATEMENT_SOURCE
            or not isinstance(excerpt, str)
            or not excerpt
            or hashlib.sha256(excerpt.encode("utf-8")).hexdigest() != digest
        ):
            return _DEFAULT_PERFORMANCE_YEAR_COUNT
    return _AUDIT_PERFORMANCE_YEAR_COUNT


def historical_performance_bases_are_complete(
    historical_performance_bases: Iterable[str],
    *,
    required_year_count: int = _DEFAULT_PERFORMANCE_YEAR_COUNT,
) -> bool:
    """완료 실적 참조가 정책이 요구한 수만큼 정확히 연속인지 확인한다."""

    values = {
        str(value or "").strip()
        for value in historical_performance_bases
        if str(value or "").strip()
    }
    years = sorted(
        int(match.group(1))
        for value in values
        if (match := re.fullmatch(r"historical-performance:(20\d{2})", value))
    )
    return (
        required_year_count
        in {_DEFAULT_PERFORMANCE_YEAR_COUNT, _AUDIT_PERFORMANCE_YEAR_COUNT}
        and len(values) == required_year_count
        and len(years) == required_year_count
        and years == list(range(years[0], years[0] + required_year_count))
    )


def basic_report_selection_subset(
    picks: Iterable[CanonicalPick],
    *,
    historical_performance_bases: Iterable[str],
    required_performance_year_count: int = _DEFAULT_PERFORMANCE_YEAR_COUNT,
) -> list[CanonicalPick]:
    """Writer에 실제 넘길 최소 안전·참조 완결 부분집합을 만든다.

    최소 성립 조건은 검증된 공식 정체성 사실, 수익 구조, 프로그램이 별도 표로
    제공하는 정책상 연속 완료 실적이다. 제품→수익 구조, 고객·시장, 완료 실행+변화 해석,
    현재 과제+대응, 미래 계획, 운영 구조, 문화는 검증된 완결 묶음이 있을 때만
    장별 공개 상한 안에서 보탠다. 구조 validator를 완화하지 않으며, 선택 항목이
    많은 기존 완전 보고서는 같은 원문 순서와 상한을 유지한다.
    """

    items = list(picks)
    performance_bases = {
        str(value or "").strip()
        for value in historical_performance_bases
        if str(value or "").strip()
    }
    if not items or not historical_performance_bases_are_complete(
        performance_bases,
        required_year_count=required_performance_year_count,
    ):
        return []

    by_section: dict[str, list[tuple[int, CanonicalPick]]] = defaultdict(list)
    by_sid: dict[str, tuple[int, CanonicalPick]] = {}
    for index, item in enumerate(items):
        if not item.sid or item.sid in by_sid:
            return []
        by_section[item.section_id].append((index, item))
        by_sid[item.sid] = (index, item)

    chosen: set[int] = set()

    # 1장: 부분 보고서는 검증된 공식 정체성 사실 한 건으로 성립한다. 쉬운 말
    # 정체성 요약을 우선하되, 없으면 공식 자기정의·사업범위를 그대로 사용한다.
    # FULL 판정은 아래 별도 함수에서 계속 identity_summary를 요구한다.
    identity = by_section.get("identity", [])
    identity_summary = next(
        (pair for pair in identity if pair[1].claim_type == "identity_summary"),
        None,
    )
    identity_anchor = identity_summary or next(
        (
            pair
            for pair in identity
            if pair[1].claim_type in {"official_self_definition", "operating_scope"}
        ),
        None,
    )
    if identity_anchor is None:
        return []
    chosen.add(identity_anchor[0])
    for index, item in identity:
        if len(chosen.intersection(i for i, _ in identity)) >= _PROSE_LIMITS["identity"]:
            break
        if item.claim_type in {
            "identity_summary",
            "official_self_definition",
            "operating_scope",
        }:
            chosen.add(index)

    # 2·3장: 수익 구조는 필수다. 제품은 실제 참조 수익 구조와 완결될 때만 싣는다.
    customer = next(
        (
            pair
            for pair in by_section.get("business_model", [])
            if pair[1].claim_type == "customer_market"
        ),
        None,
    )
    selected_products: list[tuple[int, CanonicalPick]] = []
    selected_revenues: set[int] = set()
    product_subjects: set[str] = set()
    for index, product in by_section.get("portfolio", []):
        if product.claim_type != "priority_product":
            continue
        subject = " ".join(product.subject_label.split()).casefold()
        revenue_pair = by_sid.get(product.revenue_model_sid)
        if (
            not subject
            or subject in product_subjects
            or not product.product_role.strip()
            or revenue_pair is None
            or revenue_pair[1].section_id != "business_model"
            or revenue_pair[1].claim_type != "revenue_model"
        ):
            continue
        prospective_revenues = selected_revenues | {revenue_pair[0]}
        # 고객·시장 근거가 있으면 기존처럼 그 한 칸을 먼저 예약한다.
        reserved_customer_slots = int(customer is not None)
        if (
            len(prospective_revenues) + reserved_customer_slots
            > _PROSE_LIMITS["business_model"]
        ):
            continue
        selected_products.append((index, product))
        selected_revenues = prospective_revenues
        product_subjects.add(subject)
        if len(selected_products) >= _PROSE_LIMITS["portfolio"]:
            break
    revenue_models = [
        pair
        for pair in by_section.get("business_model", [])
        if pair[1].claim_type == "revenue_model"
    ]
    if not revenue_models:
        return []
    if not selected_revenues:
        selected_revenues.add(revenue_models[0][0])
    chosen.update(selected_revenues)
    if customer is not None:
        chosen.add(customer[0])
    chosen.update(index for index, _item in selected_products)
    business_count = sum(
        index in chosen for index, _item in by_section.get("business_model", [])
    )
    for index, item in by_section.get("business_model", []):
        if business_count >= _PROSE_LIMITS["business_model"]:
            break
        if index not in chosen and item.claim_type in {"revenue_model", "customer_market"}:
            chosen.add(index)
            business_count += 1

    # 4장: 변화 해석과 그 내부 완료 실행 근거를 먼저 묶고, 남는 검증 완료
    # 실행은 해석을 만들지 않은 채 장별 상한까지 보존한다.
    past = by_section.get("past_changes", [])
    executions = {
        item.sid: (index, item)
        for index, item in past
        if item.claim_type == "completed_execution"
    }

    def interpretation_dependencies(
        interpretation: CanonicalPick,
    ) -> set[int] | None:
        if not interpretation.basis_sids:
            return None
        dependencies: set[int] = set()
        for basis_sid in interpretation.basis_sids:
            if basis_sid in performance_bases:
                continue
            target = executions.get(basis_sid)
            if target is None:
                return None
            dependencies.add(target[0])
        return dependencies

    past_selected: set[int] = set()
    if executions:
        for index, interpretation in past:
            if interpretation.claim_type != "change_interpretation":
                continue
            dependencies = interpretation_dependencies(interpretation)
            if dependencies is None:
                continue
            if not dependencies:
                dependencies = {next(iter(executions.values()))[0]}
            if len(dependencies) + 1 <= _PROSE_LIMITS["past_changes"]:
                past_selected.update(dependencies)
                past_selected.add(index)
                break
        # 완결된 첫 묶음이 있으면 기존 순서·상한대로 추가 실행과 해석을 보탠다.
        if past_selected:
            for index, item in past:
                if len(past_selected) >= _PROSE_LIMITS["past_changes"]:
                    break
                if index in past_selected:
                    continue
                if item.claim_type == "completed_execution":
                    past_selected.add(index)
                    continue
                if item.claim_type == "change_interpretation":
                    dependencies = interpretation_dependencies(item)
                    if dependencies is None:
                        continue
                    missing = dependencies - past_selected
                    if (
                        len(past_selected) + len(missing) + 1
                        <= _PROSE_LIMITS["past_changes"]
                    ):
                        past_selected.update(missing)
                        past_selected.add(index)
        for index, item in past:
            if len(past_selected) >= _PROSE_LIMITS["past_changes"]:
                break
            if item.claim_type == "completed_execution":
                past_selected.add(index)
    chosen.update(past_selected)

    # 5장: 결속된 문제·대응 쌍을 우선하고, 남는 검증 문제는 대응을 지어내지
    # 않은 채 최대 세 과제까지 보존한다.
    current = by_section.get("current_challenges", [])
    issues = {
        item.sid: (index, item)
        for index, item in current
        if item.claim_type == "current_issue"
    }
    current_selected: set[int] = set()
    selected_issue_sids: set[str] = set()
    for index, response in current:
        if response.claim_type != "current_response":
            continue
        issue = issues.get(response.response_to_sid)
        if issue is None or response.response_to_sid in selected_issue_sids:
            continue
        current_selected.update({issue[0], index})
        selected_issue_sids.add(response.response_to_sid)
        if len(current_selected) >= _PROSE_LIMITS["current_challenges"]:
            break
    maximum_issues = _PROSE_LIMITS["current_challenges"] // 2
    for issue_sid, (index, _issue) in issues.items():
        if len(selected_issue_sids) >= maximum_issues:
            break
        current_selected.add(index)
        selected_issue_sids.add(issue_sid)
    chosen.update(current_selected)

    # 6~7장은 근거가 있을 때 허용 사실을 원문 순서대로 공개 상한까지만 둔다.
    for section_id, allowed_types in (
        ("future_strategy", {"future_plan"}),
        ("operations_partners", {"operating_core", "partner_role"}),
    ):
        selected = [
            index
            for index, item in by_section.get(section_id, [])
            if item.claim_type in allowed_types
        ][:_PROSE_LIMITS[section_id]]
        chosen.update(selected)

    # 8장: 공식 채용·문화 근거가 있을 때만 허용 유형을 싣고, 없으면 생략한다.
    culture_selected = [
        index
        for index, item in by_section.get("culture", [])
        if item.claim_type in {"official_value", "work_example"}
    ][:_PROSE_LIMITS["culture"]]
    chosen.update(culture_selected)

    return [item for index, item in enumerate(items) if index in chosen]


def basic_report_selection_is_minimum_usable(
    picks: Iterable[CanonicalPick],
    *,
    historical_performance_bases: Iterable[str],
    required_performance_year_count: int = _DEFAULT_PERFORMANCE_YEAR_COUNT,
) -> bool:
    """검증된 부분 보고서의 최소 안전 부분집합이 성립하는지 판정한다."""

    return bool(
        basic_report_selection_subset(
            picks,
            historical_performance_bases=historical_performance_bases,
            required_performance_year_count=required_performance_year_count,
        )
    )


def basic_report_selection_is_complete(
    picks: Iterable[CanonicalPick],
    *,
    historical_performance_bases: Iterable[str],
    required_performance_year_count: int = _DEFAULT_PERFORMANCE_YEAR_COUNT,
) -> bool:
    """기존 FULL 기본 보고서의 핵심 장과 참조가 모두 완결됐는지 판정한다.

    부분 보고서 가능 여부와 의도적으로 구분한다. 정체성·고객·수익 결속 제품,
    완료 실행과 변화 해석, 미래 계획, 운영 구조가 모두 안전 부분집합에 남아야
    하며 연속 3개년 완료 실적도 있어야 한다.
    """

    subset = basic_report_selection_subset(
        picks,
        historical_performance_bases=historical_performance_bases,
        required_performance_year_count=required_performance_year_count,
    )
    if not subset:
        return False
    claim_types = {item.claim_type for item in subset}
    required_claim_types = {
        "identity_summary",
        "customer_market",
        "revenue_model",
        "priority_product",
        "completed_execution",
        "change_interpretation",
        "future_plan",
    }
    return required_claim_types.issubset(claim_types) and bool(
        {"operating_core", "partner_role"}.intersection(claim_types)
    )


def sections_from_picks(
    picks: Iterable[CanonicalPick],
    fragments: dict[int, dict[str, str]],
    *,
    tables_by_section: dict[str, list[ReportTable]] | None = None,
) -> list[ReportSection]:
    """검증된 원문 선택을 의미 섹션으로 묶는다. 빈 섹션은 만들지 않는다."""

    from src.features.report_standard.constants import SECTION_BY_ID, SECTION_SPECS

    lines: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for item in picks:
        kind = str(fragments.get(item.fragment_id, {}).get("종류") or "")
        lines[item.section_id].append((item.sentence, f"조각 {item.fragment_id}·{kind}"))

    table_map = tables_by_section or {}
    out: list[ReportSection] = []
    for spec in SECTION_SPECS:
        section_lines = lines.get(spec.section_id, [])
        tables = [table for table in table_map.get(spec.section_id, []) if table.is_valid]
        if not section_lines and not tables:
            continue
        out.append(
            ReportSection(
                cell=spec.section_id,
                title=spec.title,
                display_number=spec.display_number,
                tag=spec.tag,
                lines=section_lines,
                tables=tables,
            )
        )
    return out


def _direct_causal_fields(
    claim: str, evidence: str, *, subject_label: str = ""
) -> tuple[str, str, str, str] | None:
    """원문 한 문장 안의 직접 인과만 구조화한다."""

    claim_connectors = {
        match.group("connector").casefold()
        for match in _DIRECT_CAUSAL_RE.finditer(claim)
    }
    if not claim_connectors:
        return None
    for match in _DIRECT_CAUSAL_RE.finditer(evidence):
        connector = match.group("connector").casefold()
        if connector not in claim_connectors:
            continue
        cause = " ".join(match.group("cause").split()).strip(" ,")
        outcome = " ".join(match.group("outcome").split()).strip(" ,")
        causal_evidence = " ".join(match.group(0).split()).strip()
        if cause and outcome and causal_evidence:
            return (
                subject_label or cause,
                f"{cause} {match.group('connector')}",
                outcome,
                causal_evidence,
            )
    # 영문 공식 자료의 ``outcome due to cause``를 작가가 자연스럽게
    # ``cause로 인해 outcome``으로 옮겨도 같은 직접 인과다. 두 문장에 실제
    # 인과 연결자가 각각 있을 때만 순서를 뒤집어 구조화한다.
    english_match = _ENGLISH_DUE_TO_RE.search(evidence)
    if english_match is not None:
        cause = " ".join(english_match.group("cause").split()).strip(" ,")
        outcome = " ".join(english_match.group("outcome").split()).strip(" ,")
        causal_evidence = " ".join(english_match.group(0).split()).strip()
        if cause and outcome and causal_evidence:
            return (
                subject_label or outcome,
                f"due to {cause}",
                outcome,
                causal_evidence,
            )
    return None


def _structured_company_binding_is_visible(
    pick: CanonicalPick, text: str
) -> bool:
    """Selector의 구조 예외를 쓸 때 최종 Writer 문장에도 고유 결속을 요구한다.

    Reviewer가 사실성만 통과시켜도 구체 제품·행동·파트너가 빠진 일반론은 공식
    보고서 품질 계약을 만족하지 않는다. 대상명은 표면 문자열로, 행동·역할은
    원문 구조 발췌의 핵심 단어 두 개 이상으로 최종 문장에 남아 있어야 한다.
    """

    normalized_text = " ".join(str(text or "").split()).casefold()
    compact_text = re.sub(r"[^0-9a-z가-힣]", "", normalized_text)

    def contains_surface(value: str) -> bool:
        compact_value = re.sub(
            r"[^0-9a-z가-힣]",
            "",
            " ".join(str(value or "").split()).casefold(),
        )
        return bool(compact_value) and compact_value in compact_text

    def contains_distinct_terms(value: str) -> bool:
        terms: list[str] = []
        for match in _TERM_RE.finditer(str(value or "")):
            term = match.group(0).casefold()
            compact = re.sub(r"\W", "", term)
            if (
                len(compact) < 2
                or term in _TERM_STOP
                or term in {"사업", "계획", "추진", "대응", "운영", "현재"}
                or term in terms
            ):
                continue
            terms.append(term)
        return sum(term in normalized_text for term in terms) >= 2

    def translated_plan_action_is_visible(value: str) -> bool:
        source = " ".join(str(value or "").split())
        matched_family = False
        for source_pattern, target_pattern in _TRANSLATED_PLAN_ACTION_PATTERNS:
            if source_pattern.search(source) is None:
                continue
            matched_family = True
            if target_pattern.search(normalized_text) is not None:
                return True
        return not matched_family and contains_distinct_terms(value)

    if pick.claim_type == "current_response":
        return contains_distinct_terms(pick.response_action)
    if pick.claim_type == "priority_product":
        supported_signals = sum(
            PRIORITY_SIGNAL_PATTERNS[signal].search(normalized_text) is not None
            for signal in pick.priority_signals
            if signal in PRIORITY_SIGNAL_PATTERNS
        )
        return contains_surface(pick.subject_label) and supported_signals >= 2
    if pick.claim_type == "current_issue":
        return bool(
            contains_surface(pick.next_check_metric)
            and re.search(r"감소|하락|둔화|부진|위험|부담|과제", normalized_text)
        )
    if pick.claim_type == "future_plan":
        timing_years = set(re.findall(r"20\d{2}", pick.plan_timing))
        timing_is_visible = not timing_years or all(
            year in normalized_text for year in timing_years
        )
        action_is_visible = translated_plan_action_is_visible(
            pick.plan_execution_signal
        )
        return (
            contains_surface(pick.subject_label)
            and timing_is_visible
            and action_is_visible
        )
    if pick.claim_type in {"operating_core", "partner_role"}:
        return contains_surface(pick.subject_label) and contains_distinct_terms(
            pick.operation_role
        )
    return False


def write_and_verify_sections(
    *,
    engine: Any,
    client: Any,
    company: str,
    sections: list[ReportSection],
    fragments: dict[int, dict[str, str]],
    picks: Iterable[CanonicalPick] = (),
    steps: list[dict[str, Any]],
    model: str,
) -> tuple[list[ReportSection], list[WrittenClaim]]:
    """작가와 별도 검토자를 차례로 호출하고 원문 연결을 보존한다."""

    evidence = writer_logic.collect_evidence(
        {section.cell: list(section.lines) for section in sections if section.lines}
    )
    if not evidence:
        return sections, []

    def ask(prompt: str, schema: dict[str, Any], max_tokens: int):
        previous = getattr(engine, "MODEL", "")
        try:
            if model:
                engine.MODEL = model
            payload, usage = engine._ask(client, prompt, schema, max_tokens=max_tokens)
        finally:
            if model:
                engine.MODEL = previous
        if isinstance(usage, dict):
            usage = {**usage, writer_constants.USAGE_MODEL_KEY: model or previous}
        return payload, usage

    written, write_step = writer_logic.write_with_ai(
        lambda prompt, schema: ask(prompt, schema, writer_constants.WRITE_MAX_TOKENS),
        company=company,
        job="",
        evidence=evidence,
    )
    steps.append({"step": writer_constants.WRITE_STEP, **write_step})
    if not written:
        return sections, []

    passed, review_steps = writer_revision.review_with_single_rewrite(
        lambda prompt, schema: ask(prompt, schema, writer_verify.VERIFY_MAX_TOKENS),
        lambda prompt, schema: ask(prompt, schema, writer_revision.REWRITE_MAX_TOKENS),
        # 첫 검수 AI와 대화를 이어 붙이지 않는 새 단발 호출이다.
        lambda prompt, schema: ask(prompt, schema, writer_verify.VERIFY_MAX_TOKENS),
        written=written,
        evidence=evidence,
    )
    steps.extend(review_steps)

    pick_list = list(picks)
    pick_by_exact = {
        (pick.section_id, pick.fragment_id, pick.sentence): pick
        for pick in pick_list
    }
    picks_by_fragment: dict[tuple[str, int], list[CanonicalPick]] = defaultdict(list)
    for pick in pick_list:
        picks_by_fragment[(pick.section_id, pick.fragment_id)].append(pick)
    specificity_verified_names = verified_latin_names(
        str(fragment.get("원문") or "") for fragment in fragments.values()
    )

    claims: list[WrittenClaim] = []
    prose_by_section: dict[str, list[tuple[str, str]]] = defaultdict(list)
    seen_pick_roles: set[tuple[str, str, str]] = set()
    duplicate_pick_sentences = 0
    for section_id, sentences in passed.items():
        evidence_by_sid = {item.sid: item for item in evidence.get(section_id, [])}
        for sentence in sentences[: _PROSE_LIMITS.get(section_id, 3)]:
            source = evidence_by_sid.get(sentence.sid)
            if source is None:
                continue
            number = citation_number(source.cite)
            if not number:
                continue
            fragment_id = int(number)
            fragment = fragments.get(fragment_id, {})
            fragment_kind = str(fragment.get("종류") or "")
            clean = " ".join(sentence.text.split())
            pick = pick_by_exact.get((section_id, fragment_id, source.text))
            if pick is None:
                candidates = picks_by_fragment.get((section_id, fragment_id), [])
                if len(candidates) == 1 and candidates[0].sentence == source.text:
                    pick = candidates[0]
            # 선택 metadata가 작가 단계에서 사라지면 의미 계약을 검증할 수 없다.
            if pick is None:
                continue
            decision = assess_claim(
                section_id,
                clean,
                source_kind=fragment_kind,
                company=company,
                verified_names=specificity_verified_names,
            )
            structured_writer_binding = bool(
                structured_company_binding_allows_specificity_failure(
                    pick.claim_type,
                    decision.reason,
                )
                and _structured_company_binding_is_visible(pick, clean)
                and (
                    fragment_kind != "공식 IR"
                    or verified_official_ir_is_bound_to_company(fragment, company)
                )
            )
            verified_ir_writer_binding = bool(
                _VERIFIED_IR_WRITER_FAILURE_REASONS.get(pick.claim_type)
                == decision.reason
                and verified_official_ir_is_bound_to_company(fragment, company)
                and _structured_company_binding_is_visible(pick, clean)
            )
            if not decision.passed and not (
                structured_writer_binding or verified_ir_writer_binding
            ):
                continue
            if _DIRECT_CAUSAL_RE.search(clean) and _direct_causal_fields(
                clean, source.text, subject_label=pick.subject_label
            ) is None:
                continue
            pick_role = (section_id, pick.sid, pick.claim_type)
            if pick.sid and pick_role in seen_pick_roles:
                duplicate_pick_sentences += 1
                continue
            if pick.sid:
                seen_pick_roles.add(pick_role)
            prose_by_section[section_id].append((clean, source.cite))
            claims.append(
                WrittenClaim(
                    section_id,
                    clean,
                    source.cite,
                    source.text,
                    fragment_id,
                    sid=pick.sid,
                    claim_type=pick.claim_type,
                    subject_label=pick.subject_label,
                    market_stage=pick.market_stage,
                    market_observation=pick.market_observation,
                    product_role=pick.product_role,
                    portfolio_stage=pick.portfolio_stage,
                    revenue_model_sid=pick.revenue_model_sid,
                    response_to_sid=pick.response_to_sid,
                    basis_sids=pick.basis_sids,
                    priority_signals=pick.priority_signals,
                    event_date=pick.event_date,
                    response_action=pick.response_action,
                    initial_signal=pick.initial_signal,
                    next_check_metric=pick.next_check_metric,
                    plan_status=pick.plan_status,
                    plan_timing=pick.plan_timing,
                    plan_condition=pick.plan_condition,
                    plan_expected_effect=pick.plan_expected_effect,
                    plan_execution_signal=pick.plan_execution_signal,
                    operation_role=pick.operation_role,
                    value_chain_stage=pick.value_chain_stage,
                    relationship_type=pick.relationship_type,
                )
            )

    if duplicate_pick_sentences:
        steps.append(
            {
                "step": "11_작성_동일선택중복제거",
                "생략": duplicate_pick_sentences,
            }
        )

    before_prune = len(claims)
    claims = _prune_unbound_optional_claims(claims)
    if len(claims) != before_prune:
        steps.append(
            {
                "step": "작가_선택묶음_후검증",
                "생략": before_prune - len(claims),
                "사유": "Writer·Reviewer 뒤 참조 관계 불완결",
            }
        )

    prose_by_section = defaultdict(list)
    raw_by_section: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for claim in claims:
        prose_by_section[claim.section_id].append((claim.text, claim.cite))
        raw_by_section[claim.section_id].append((claim.evidence, claim.cite))

    return (
        [
            replace(
                section,
                lines=raw_by_section.get(section.cell, []),
                prose_lines=prose_by_section.get(section.cell, []),
            )
            for section in sections
        ],
        claims,
    )


def supplement_missing_minimum_claims_once(
    *,
    engine: Any,
    client: Any,
    company: str,
    sections: list[ReportSection],
    fragments: dict[int, dict[str, str]],
    picks: Iterable[CanonicalPick],
    written_claims: Iterable[WrittenClaim],
    steps: list[dict[str, Any]],
    model: str,
) -> tuple[list[ReportSection], list[WrittenClaim], tuple[str, ...]]:
    """이미 검증된 span에서 빠진 최소 역할만 Writer·Reviewer로 한 번 보충한다.

    새 수집이나 span 재선택은 하지 않는다. 정체성·수익 구조별로 기존 검증 pick
    한 건만 다시 쓰고 독립 검수를 그대로 통과한 문장만 기존 결과에 합친다.
    """

    original_claims = list(written_claims)
    missing_before = missing_minimum_written_roles(original_claims)
    if not missing_before:
        return sections, original_claims, ()

    pick_list = list(picks)
    supplement_picks: list[CanonicalPick] = []
    if MINIMUM_WRITTEN_ROLE_IDENTITY in missing_before:
        for claim_type in (
            "identity_summary",
            "official_self_definition",
            "operating_scope",
        ):
            candidate = next(
                (item for item in pick_list if item.claim_type == claim_type),
                None,
            )
            if candidate is not None:
                supplement_picks.append(candidate)
                break
    if MINIMUM_WRITTEN_ROLE_REVENUE in missing_before:
        candidate = next(
            (item for item in pick_list if item.claim_type == "revenue_model"),
            None,
        )
        if candidate is not None:
            supplement_picks.append(candidate)

    if not supplement_picks:
        steps.append(
            {
                "step": "작가_핵심사실_1회보충",
                "보충전누락": list(missing_before),
                "공식후보": 0,
                "보충후누락": list(missing_before),
            }
        )
        return sections, original_claims, missing_before

    supplement_sections = sections_from_picks(supplement_picks, fragments)
    _written_sections, supplement_claims = write_and_verify_sections(
        engine=engine,
        client=client,
        company=company,
        sections=supplement_sections,
        fragments=fragments,
        picks=supplement_picks,
        steps=steps,
        model=model,
    )

    combined: list[WrittenClaim] = []
    seen: set[tuple[str, str, str]] = set()
    for claim in [*original_claims, *supplement_claims]:
        key = (claim.section_id, claim.sid, claim.claim_type)
        if key in seen:
            continue
        seen.add(key)
        combined.append(claim)
    combined = _prune_unbound_optional_claims(combined)

    raw_by_section: dict[str, list[tuple[str, str]]] = defaultdict(list)
    prose_by_section: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for claim in combined:
        raw_by_section[claim.section_id].append((claim.evidence, claim.cite))
        prose_by_section[claim.section_id].append((claim.text, claim.cite))
    merged_sections = [
        replace(
            section,
            lines=raw_by_section.get(section.cell, []),
            prose_lines=prose_by_section.get(section.cell, []),
        )
        for section in sections
    ]
    missing_after = missing_minimum_written_roles(combined)
    steps.append(
        {
            "step": "작가_핵심사실_1회보충",
            "보충전누락": list(missing_before),
            "공식후보": len(supplement_picks),
            "검수통과": len(supplement_claims),
            "보충후누락": list(missing_after),
        }
    )
    return merged_sections, combined, missing_after


def _source_date(source: Source) -> str:
    if source_type_is_official_ir(source.source_type):
        return (
            source.published_at
            if official_ir_source_is_usable(
                source,
                reference_date=source.collected_at or source.published_at,
            )
            else ""
        )
    if not official_web_currentness_is_usable(
        source_type=source.source_type,
        url=source.url,
        published_at=source.published_at,
        disclosed_at=source.disclosed_at,
        collected_at=source.collected_at,
    ):
        return ""
    return source.published_at or source.disclosed_at or source.collected_at


def _time_state(section_id: str, claim: str, claim_type: str = "") -> str:
    if claim_type in {"current_issue", "current_response"}:
        return claim_type
    if claim_type in {"completed_execution", "change_interpretation", "historical_performance"}:
        return "completed"
    if claim_type == "future_plan":
        return "future_plan"
    if section_id == "current_challenges":
        return "current_response" if any(term in claim for term in _RESPONSE_TERMS) else "current_issue"
    return _TIME_STATE.get(section_id, "standing")


def _fact_status(source: Source, time_state: str) -> str:
    status = source.fact_status.casefold()
    if "잠정" in status:
        return "provisional"
    if "추정" in status:
        return "estimated"
    if "미공개" in status:
        return "scope_undisclosed"
    # 한 공시 조각에는 현재 실행과 향후 계획 문장이 함께 있을 수 있다.
    # Source의 포괄 상태 문자열보다 claim_type에서 확정한 시간 상태를 우선해
    # `추진 중`인 현재 대응을 아직 실행되지 않은 계획으로 바꾸지 않는다.
    if time_state == "future_plan":
        return "planned"
    return "actual"


def _fact_id(*parts: str) -> str:
    material = "\x1f".join(" ".join(str(part or "").split()) for part in parts)
    return "fact-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]


def _numeric_value(text: str) -> str:
    return " | ".join(match.group(0).strip() for match in _NUMBER_RE.finditer(text))


def _common_support_terms(claim: str, evidence: str) -> list[str]:
    """claim과 원문에 그대로 남은 근거어만 보존한다. 두 개 미만이면 게이트가 막는다."""

    evidence_normalized = " ".join(evidence.split()).casefold()
    out: list[str] = []
    for token in _TERM_RE.findall(claim):
        clean = token.strip().casefold()
        if clean in _TERM_STOP or len(re.sub(r"\W", "", clean)) < 2:
            continue
        if clean in evidence_normalized and clean not in out:
            out.append(clean)
    return out[:8]


def _exact_numeric_checks(text: str) -> list[str]:
    """원문 그대로 옮긴 숫자를 ROUND_HALF_UP 무변환 검산식으로 만든다."""

    checks: list[str] = []
    for token in re.findall(r"[-+]?\d[\d,]*(?:\.\d+)?", text):
        places = len(token.rsplit(".", 1)[1]) if "." in token else 0
        checks.append(f"{token}|1|{places}|{token}")
    return checks


_AMOUNT_WITH_EOK_RE = re.compile(
    r"^\s*(?P<raw>[-+]?\d[\d,]*)(?:\s*\((?P<eok>[-+]?\d[\d,]*)억\))?\s*$"
)


def _table_numeric_fields(
    values: list[str],
    *,
    raw_values: list[str] | None = None,
    scale_divisor: str = "",
    scale_places: int = 0,
) -> tuple[str, str, list[str]]:
    """표 셀의 원값과 억 표시를 실제 단위 변환식에 결속한다."""

    if raw_values and len(raw_values) == len(values) and scale_divisor:
        checks = [
            f"{raw}|{scale_divisor}|{scale_places}|{shown}"
            for raw, shown in zip(raw_values, values)
        ]
        return " | ".join(raw_values), " | ".join(values), checks

    raw_values: list[str] = []
    display_values: list[str] = []
    checks: list[str] = []
    for value in values:
        match = _AMOUNT_WITH_EOK_RE.fullmatch(value)
        if match is None:
            tokens = re.findall(r"[-+]?\d[\d,]*(?:\.\d+)?", value)
            for token in tokens:
                places = len(token.rsplit(".", 1)[1]) if "." in token else 0
                raw_values.append(token)
                display_values.append(token)
                checks.append(f"{token}|1|{places}|{token}")
            continue
        raw = match.group("raw")
        eok = match.group("eok")
        raw_values.append(raw)
        display_values.append(raw)
        checks.append(f"{raw}|1|0|{raw}")
        if eok:
            display_values.append(eok)
            checks.append(f"{raw}|100000000|0|{eok}")
    return " | ".join(raw_values), " | ".join(display_values), checks


def _table_row_claim(table: ReportTable, row: list[str]) -> str:
    return f"{table.caption}: " + " | ".join(row)


def _causality_supported(claim: str, evidence: str) -> bool:
    used = {term for term in _CAUSAL_TERMS if term in claim}
    return bool(used) and used.issubset(
        {term for term in _CAUSAL_TERMS if term in evidence}
    )


def _written_fact_id(company: str, claim: WrittenClaim) -> str:
    return _fact_id(
        company,
        claim.evidence,
        str(claim.fragment_id),
        claim.claim_type,
        claim.text,
    )


def _fact_from_claim(
    company: str,
    claim: WrittenClaim,
    source: Source,
    sid_fact_ids: dict[str, str],
    historical_basis_fact_ids: dict[str, str] | None = None,
) -> FactRecord:
    numbers = _numeric_value(claim.text)
    time_state = _time_state(claim.section_id, claim.text, claim.claim_type)
    causal = _direct_causal_fields(
        claim.text, claim.evidence, subject_label=claim.subject_label
    )
    basis_resolver = {**sid_fact_ids, **(historical_basis_fact_ids or {})}
    raw_basis_sids = [str(value or "").strip() for value in claim.basis_sids]
    basis_links_are_valid = (
        claim.claim_type == "change_interpretation"
        and bool(raw_basis_sids)
        and all(raw_basis_sids)
        and len(raw_basis_sids) == len(set(raw_basis_sids))
        and not any(value.startswith("fact-") for value in raw_basis_sids)
        and all(value in basis_resolver for value in raw_basis_sids)
    )
    fact = FactRecord(
        fact_id=_written_fact_id(company, claim),
        legal_entity=company,
        # 검증된 이름이 없으면 원문 한 문장 전체를 범위로 써서 일반화를 막는다.
        subject_scope=claim.subject_label or claim.evidence,
        relationship_or_action=(
            claim.response_action
            if claim.claim_type == "current_response"
            else claim.operation_role
            if claim.claim_type in {"operating_core", "partner_role"}
            else claim.claim_type or claim.section_id
        ),
        claim=claim.text,
        claim_type=claim.claim_type,
        section_owner=claim.section_id,
        time_state=time_state,
        as_of=_source_date(source),
        source_id=source.source_id,
        source_type=source.source_type,
        source_title=source.title or source.label,
        source_publisher=source.publisher,
        source_host=source.host,
        source_url=source.url,
        source_document_id=source.document_id,
        location=source.location,
        status="verified",
        fact_status=_fact_status(source, time_state),
        verification_status="verified",
        state_evidence=claim.evidence,
        source_date=_source_date(source),
        evidence_support_terms=_common_support_terms(claim.text, claim.evidence),
        raw_value=numbers,
        calculation="원문 표시값을 1로 나누어 직접 대조" if numbers else "",
        display_value=numbers,
        rounding_rule="ROUND_HALF_UP 미적용(원문 표시값 그대로)" if numbers else "",
        numeric_checks=_exact_numeric_checks(numbers),
        event_date=claim.event_date,
        market_stage=claim.market_stage,
        market_observation=claim.market_observation,
        product_role=claim.product_role,
        portfolio_stage=claim.portfolio_stage,
        revenue_model_fact_id=sid_fact_ids.get(claim.revenue_model_sid, ""),
        priority_signals=list(claim.priority_signals),
        # 하나라도 없거나 중복·내부 ID가 섞이면 일부만 연결하지 않는다. 빈 값은
        # 출고 게이트가 근거 없는 변화 해석으로 차단한다.
        basis_fact_ids=(
            [basis_resolver[sid] for sid in raw_basis_sids]
            if basis_links_are_valid
            else []
        ),
        response_to_fact_id=sid_fact_ids.get(claim.response_to_sid, ""),
        response_action=claim.response_action,
        initial_signal=claim.initial_signal,
        next_check_metric=claim.next_check_metric,
        plan_status=claim.plan_status,
        plan_timing=claim.plan_timing,
        plan_condition=claim.plan_condition,
        plan_expected_effect=claim.plan_expected_effect,
        plan_execution_signal=claim.plan_execution_signal,
        value_chain_stage=claim.value_chain_stage,
        relationship_type=claim.relationship_type,
        supports_causality=causal is not None,
        causal_subject=causal[0] if causal else "",
        causal_mechanism=causal[1] if causal else "",
        causal_outcome=causal[2] if causal else "",
        causal_evidence=causal[3] if causal else "",
    )
    return replace(fact, evidence_binding=fact_evidence_binding(fact))


def _historical_basis_fact_ids(facts: Iterable[FactRecord]) -> dict[str, str]:
    """유일한 FY 실적 사실을 선택기의 공개 참조와 내부 fact_id로 잇는다."""

    candidates: dict[str, list[str]] = defaultdict(list)
    for fact in facts:
        if fact.claim_type != "historical_performance":
            continue
        reference = historical_performance_basis_sid(fact.fiscal_year)
        if reference:
            candidates[reference].append(fact.fact_id)
    return {
        reference: fact_ids[0]
        for reference, fact_ids in candidates.items()
        if len(fact_ids) == 1
    }


def _table_facts(
    company: str,
    section: ReportSection,
    sources_by_number: dict[int, Source],
) -> tuple[list[ReportTable], list[FactRecord]]:
    tables: list[ReportTable] = []
    facts: list[FactRecord] = []
    for table in section.tables:
        number = citation_number(table.cite)
        source = sources_by_number.get(int(number)) if number else None
        if (
            source is None
            or not source.is_canonical_valid
            or len(table.evidence_rows) != len(table.rows)
        ):
            continue
        row_facts: list[FactRecord] = []
        for row_index, row in enumerate(table.rows, start=1):
            state_evidence = str(table.evidence_rows[row_index - 1]).strip()
            if (
                not state_evidence
                or evidence_text_hash(state_evidence) not in source.evidence_hashes
            ):
                continue
            claim = _table_row_claim(table, row)
            value_cells = list(row[1:])
            raw_row = (
                table.raw_rows[row_index - 1]
                if len(table.raw_rows) >= row_index
                else []
            )
            raw_values, display_values, numeric_checks = _table_numeric_fields(
                value_cells,
                raw_values=list(raw_row[1:]) if raw_row else None,
                scale_divisor=table.scale_divisor,
                scale_places=table.scale_places,
            )
            fiscal_year = 0
            claim_type = f"{section.cell}_table"
            if section.cell == "past_changes" and row:
                matched_year = re.fullmatch(r"\s*(20\d{2})\s*", row[0])
                if matched_year:
                    fiscal_year = int(matched_year.group(1))
                    claim_type = "historical_performance"
            time_state = _time_state(section.cell, claim, claim_type)
            rounding = (
                "ROUND_HALF_UP_for_eok; source_exact_for_won"
                if table.scale_divisor == "100000000" or any("억" in value for value in value_cells)
                else "source_exact_no_rounding"
            )
            fact = FactRecord(
                    fact_id=_fact_id(company, table.caption, str(row_index), claim, source.source_id),
                    legal_entity=company,
                    subject_scope=row[0] if row else table.caption,
                    relationship_or_action=table.caption,
                    claim=claim,
                    claim_type=claim_type,
                    section_owner=section.cell,
                    time_state=time_state,
                    as_of=_source_date(source),
                    source_id=source.source_id,
                    source_type=source.source_type,
                    source_title=source.title or source.label,
                    source_publisher=source.publisher,
                    source_host=source.host,
                    source_url=source.url,
                    source_document_id=source.document_id,
                    location=source.location,
                    status="verified",
                    fact_status=_fact_status(source, time_state),
                    verification_status="verified",
                    state_evidence=state_evidence,
                    source_date=_source_date(source),
                    evidence_support_terms=_common_support_terms(claim, state_evidence),
                    raw_value=raw_values,
                    calculation="원값은 직접 대조하고 괄호 억 단위는 원값÷100,000,000으로 재계산",
                    display_value=display_values,
                    rounding_rule=(
                        "ROUND_HALF_UP 적용 여부는 원문 표시값 단위로 직접 대조; "
                        + rounding
                    ),
                    numeric_checks=numeric_checks,
                    fiscal_year=fiscal_year,
                    supports_causality=False,
                )
            row_facts.append(replace(fact, evidence_binding=fact_evidence_binding(fact)))
        if row_facts:
            tables.append(table)
            facts.extend(row_facts)
    return tables, facts


def assemble_report(
    *,
    company: str,
    corp_type: str,
    sections: list[ReportSection],
    written_claims: list[WrittenClaim],
    sources: list[Source],
    summary_ask: Callable[[str, dict[str, Any], int], tuple[dict[str, Any] | None, dict[str, Any]]],
    steps: list[dict[str, Any]],
    as_of_date: str,
    analysis_period: str,
    latest_performance_period: str,
    publish: bool = True,
) -> Report:
    """사실 장부·요약을 만들고, 요청된 경우 canonical 출고 게이트까지 실행한다.

    ``publish=False``는 실서비스가 1~8장을 먼저 사실 장부로 잠근 뒤 양사 공식
    원문 비교인 9장을 붙이기 위한 내부 경계다. 이 중간 객체를 캐시·렌더러로
    보내면 안 되며 호출부는 반드시 9장 결합 뒤 ``build_published_report``를
    실행해야 한다.
    """

    raw_numbers = [source.number for source in sources]
    raw_source_ids = [source.source_id for source in sources if source.source_id]
    raw_source_reasons: list[str] = []
    if len(raw_numbers) != len(set(raw_numbers)):
        raw_source_reasons.append(
            "[duplicate] 조립 전 원문 목록의 출처 번호가 중복됐습니다"
        )
    if len(raw_source_ids) != len(set(raw_source_ids)):
        raw_source_reasons.append(
            "[duplicate] 조립 전 원문 목록의 source_id가 중복됐습니다"
        )
    if raw_source_reasons:
        raise PublishBlockedError(
            PublishValidation(False, tuple(raw_source_reasons), ())
        )

    # Source.evidence_hashes는 provenance.build_citations가 실제 수집 payload와
    # 대조해 만든 값만 받는다. 조립기는 WrittenClaim이나 공개 표 행을 원문인
    # 것처럼 등록할 권한이 없으며, 해시가 없으면 아래 Fact 생성이 fail-closed다.
    # 1~8장의 단일-source FactRecord는 회사·공시·공식 파트너·규제기관
    # 원문만 허용한다. canonical 메타데이터가 완전한 뉴스라도 검증 보조일
    # 뿐이며, 현재 자료 모델에는 보조 근거 역할을 보존할 필드가 없으므로
    # 공개 사실로 승격하지 않고 fail-closed 한다.
    source_registry = tuple(sources)
    valid_sources = [
        source
        for source in source_registry
        if is_canonical_official_with_registry(source, source_registry)
        and (
            official_ir_source_is_usable(source, reference_date=as_of_date)
            if source_type_is_official_ir(source.source_type)
            else official_web_currentness_is_usable(
                source_type=source.source_type,
                url=source.url,
                published_at=source.published_at,
                disclosed_at=source.disclosed_at,
                collected_at=source.collected_at,
                reference_date=as_of_date,
            )
        )
    ]
    source_numbers = [source.number for source in valid_sources]
    source_ids = [source.source_id for source in valid_sources]
    source_reasons: list[str] = []
    if len(source_numbers) != len(set(source_numbers)):
        source_reasons.append("[duplicate] 조립 전 원문 목록의 출처 번호가 중복됐습니다")
    if len(source_ids) != len(set(source_ids)):
        source_reasons.append("[duplicate] 조립 전 원문 목록의 source_id가 중복됐습니다")
    if source_reasons:
        raise PublishBlockedError(PublishValidation(False, tuple(source_reasons), ()))
    sources_by_number = {source.number: source for source in valid_sources}
    written_claims, stale_execution_count = _drop_stale_completed_executions(
        written_claims,
        as_of_date=as_of_date,
    )
    if stale_execution_count:
        steps.append(
            {
                "step": "완료실행_기간후검증",
                "생략": stale_execution_count,
                "사유": "보고서 기준일 전 최근 36개월 밖의 완료 실행",
            }
        )
    claim_by_section: dict[str, list[WrittenClaim]] = defaultdict(list)
    for claim in written_claims:
        claim_by_section[claim.section_id].append(claim)
    sid_fact_ids = {
        claim.sid: _written_fact_id(company, claim)
        for claim in written_claims
        if claim.sid
    }

    facts: list[FactRecord] = []
    locked_sections: list[ReportSection] = []
    for section in sections:
        section_facts: list[FactRecord] = []
        visible_raw: list[tuple[str, str]] = []
        visible_prose: list[tuple[str, str]] = []
        visible_tables, table_facts = _table_facts(company, section, sources_by_number)
        historical_basis_fact_ids = _historical_basis_fact_ids(table_facts)
        # 문장 sid와 프로그램 생성 실적 참조가 충돌하면 후자를 사용하지 않는다.
        historical_basis_fact_ids = {
            reference: fact_id
            for reference, fact_id in historical_basis_fact_ids.items()
            if reference not in sid_fact_ids
        }
        for claim in claim_by_section.get(section.cell, []):
            source = sources_by_number.get(claim.fragment_id)
            if (
                source is None
                or evidence_text_hash(claim.evidence) not in source.evidence_hashes
            ):
                continue
            section_facts.append(
                _fact_from_claim(
                    company,
                    claim,
                    source,
                    sid_fact_ids,
                    historical_basis_fact_ids,
                )
            )
            visible_raw.append((claim.evidence, claim.cite))
            visible_prose.append((claim.text, claim.cite))
        section_facts.extend(table_facts)
        # 원문은 내부 감사용으로 보존하되, 공개 렌더러는 prose_lines와 표만 사용한다.
        if not section_facts:
            continue
        locked_sections.append(
            replace(
                section,
                lines=visible_raw,
                prose_lines=visible_prose,
                tables=visible_tables,
                fact_ids=[fact.fact_id for fact in section_facts],
                empty_reason="",
                guidance_lines=[],
            )
        )
        facts.extend(section_facts)

    used_by_source: dict[str, set[str]] = defaultdict(set)
    for fact in facts:
        used_by_source[fact.source_id].add(fact.section_owner)
    # 공식 웹·IR Fact가 직접 참조한 Source뿐 아니라 그 도메인을 증명한 DART
    # attester도 같은 장의 transitive provenance로 보존한다.
    source_by_id = {source.source_id: source for source in valid_sources}
    pending = list(used_by_source)
    while pending:
        source_id = pending.pop(0)
        source = source_by_id.get(source_id)
        if source is None:
            continue
        dependency_id = source.domain_attestation_source_id.strip()
        if not dependency_id or dependency_id not in source_by_id:
            continue
        before = set(used_by_source.get(dependency_id, set()))
        used_by_source[dependency_id].update(used_by_source[source_id])
        if used_by_source[dependency_id] != before:
            pending.append(dependency_id)
    registered_sources = [
        replace(source, used_in=sorted(used_by_source[source.source_id]))
        for source in sources_by_number.values()
        if source.source_id in used_by_source
    ]

    draft = Report(
        company=company,
        job="",
        corp_type=corp_type,
        grade=Grade.COMPLETE,
        sections=locked_sections,
        requirements=[],
        citations=registered_sources,
        cells={section.cell: True for section in locked_sections},
        generated_at=as_of_date,
        schema_version=CANONICAL_SCHEMA_VERSION,
        summary_items=[],
        fact_records=facts,
        as_of_date=as_of_date,
        analysis_period=analysis_period,
        latest_performance_period=latest_performance_period,
    )
    return finalize_report(draft, summary_ask=summary_ask, steps=steps) if publish else draft


def _minimal_summary_fact_ids(
    text: str, fact_ids: list[str], facts: dict[str, FactRecord]
) -> list[str]:
    """요약과 근거어 두 개 이상이 겹치는 최소 fact 부분집합을 고른다."""

    candidates = [
        (fact_id, set(_common_support_terms(text, facts[fact_id].claim)))
        for fact_id in fact_ids
        if fact_id in facts
    ]
    candidates = [(fact_id, terms) for fact_id, terms in candidates if terms]
    for size in range(1, len(candidates) + 1):
        qualifying: list[tuple[tuple[int, ...], set[str]]] = []
        for indexes in combinations(range(len(candidates)), size):
            terms: set[str] = set()
            for index in indexes:
                terms.update(candidates[index][1])
            if len(terms) >= 2:
                qualifying.append((indexes, terms))
        if qualifying:
            indexes, _terms = max(
                qualifying,
                key=lambda item: (len(item[1]), tuple(-index for index in item[0])),
            )
            return [candidates[index][0] for index in indexes]
    return []


def finalize_report(
    draft: Report,
    *,
    summary_ask: Callable[[str, dict[str, Any], int], tuple[dict[str, Any] | None, dict[str, Any]]],
    steps: list[dict[str, Any]],
) -> Report:
    """검증된 1~9장에서 요약을 잠그고 최종 출고한다. 9장이 빠진 상태는 연습 모드(REPORT_RELEASE_MODE=SHADOW)에서만 생긴다.

    ``summary_ask``는 과거 호출자 호환용이다. 요약은 Reviewer 또는 원문 완전일치
    코드 검증을 통과한 FactRecord 문장을 바꾸지 않고 재사용하므로 추가 AI를
    호출하지 않는다.
    """

    _ = summary_ask
    section_rank = {
        section_id: index
        for index, section_id in enumerate(_SUMMARY_SECTION_PRIORITY)
    }
    ordered_facts = [
        fact
        for _original_index, fact in sorted(
            enumerate(draft.fact_records),
            key=lambda item: (
                section_rank.get(item[1].section_owner, len(section_rank)),
                _SUMMARY_CLAIM_PRIORITY.get(item[1].claim_type, 99),
                item[0],
            ),
        )
    ]
    summary_candidates: list[VerifiedSummarySource] = []
    for fact in ordered_facts:
        if (
            fact.status != "verified"
            or fact.verification_status != "verified"
            or not fact.claim.strip()
        ):
            continue
        support_terms = list(fact.evidence_support_terms)
        if (
            len(set(support_terms)) < 2
            and historical_performance_numeric_evidence_is_bound(fact)
        ):
            # 실적표는 원 단위 원문과 억원 표시값이 달라 일반 문자열 근거어가
            # 회계연도 하나만 남을 수 있다. Fact→Source 숫자체인을 통과한 경우에만
            # Summary→Fact 결속용 근거어를 검증 완료 claim에서 결정론적으로 만든다.
            support_terms = _common_support_terms(fact.claim, fact.claim)
        summary_candidates.append(
            VerifiedSummarySource(
                section_id=fact.section_owner,
                text=fact.claim,
                fact_id=fact.fact_id,
                support_terms=tuple(support_terms),
            )
        )
    summary, summary_steps = build_summary_from_verified_claims(summary_candidates)
    steps.extend(summary_steps)

    facts_by_id = {fact.fact_id: fact for fact in draft.fact_records}
    locked_summaries: list[SummaryItem] = []
    for item in summary:
        fact_ids = list(item.fact_ids)
        if not fact_ids:
            continue
        evidence_text = summary_evidence_text(fact_ids, facts_by_id)
        support_terms = _common_support_terms(item.text, evidence_text)
        if len(set(support_terms)) < 2:
            continue
        status = SUMMARY_VERIFICATION_STATUS
        binding = summary_verification_binding(
            item.text,
            item.section_id,
            fact_ids,
            evidence_text,
            status,
            support_terms,
        )
        locked_summaries.append(
            SummaryItem(
                text=item.text,
                section_id=item.section_id,
                fact_ids=fact_ids,
                evidence_text=evidence_text,
                verification_status=status,
                verification_binding=binding,
                support_terms=support_terms,
            )
        )

    usage: dict[str, set[str]] = defaultdict(set)
    for fact in draft.fact_records:
        usage[fact.source_id].add(fact.section_owner)
        if fact.comparator_source_id:
            usage[fact.comparator_source_id].add(fact.section_owner)
    citations = [
        replace(source, used_in=sorted(usage.get(source.source_id, set())))
        if isinstance(source, Source)
        else source
        for source in draft.citations
    ]
    return build_published_report(
        replace(draft, summary_items=locked_summaries, citations=citations)
    )


def assemble_report_draft(
    *,
    company: str,
    corp_type: str,
    sections: list[ReportSection],
    written_claims: list[WrittenClaim],
    sources: list[Source],
    steps: list[dict[str, Any]],
    as_of_date: str,
    analysis_period: str,
    latest_performance_period: str,
) -> Report:
    """요약 없이 1~8 사실 장부만 조립한다. 렌더링·캐시 대상이 아니다."""

    return assemble_report(
        company=company,
        corp_type=corp_type,
        sections=sections,
        written_claims=written_claims,
        sources=sources,
        summary_ask=lambda *_args: ({"items": []}, {}),
        steps=steps,
        as_of_date=as_of_date,
        analysis_period=analysis_period,
        latest_performance_period=latest_performance_period,
        publish=False,
    )


__all__ = [
    "PublishBlockedError",
    "WrittenClaim",
    "assemble_report",
    "assemble_report_draft",
    "combine_validated_picks",
    "finalize_report",
    "majority_picks",
    "sections_from_picks",
    "supplement_missing_minimum_claims_once",
    "missing_minimum_written_roles",
    "write_and_verify_sections",
]
