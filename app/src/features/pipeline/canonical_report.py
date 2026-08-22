"""수집된 원문을 canonical(v3) 보고서로 잠그는 마지막 조립 단계.

AI는 원문 번호 선택, 가독성 편집, 숫자 없는 요약에만 관여한다. 공개되는
문장과 표는 모두 하나의 ``FactRecord``와 하나 이상의 검증된 ``Source``에
연결되며, 출고 게이트를 통과하지 못하면 보고서 객체를 만들지 않는다.
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, fields as dataclass_fields, replace
from itertools import combinations
from typing import Any, Callable, Iterable

from src.core.citations import citation_number
from src.features.company_specificity.logic import assess_claim
from src.features.pipeline.port import (
    FactRecord,
    Grade,
    Report,
    ReportSection,
    ReportTable,
    SummaryItem,
)
from src.features.provenance.sources import Source, evidence_text_hash
from src.features.report_standard import (
    CANONICAL_SCHEMA_VERSION,
    PublishBlockedError,
    PublishValidation,
    build_published_report,
)
from src.features.report_standard.publish import (
    fact_evidence_binding,
    summary_evidence_text,
    summary_verification_binding,
)
from src.features.report_summary.logic import build_summary_with_ai
from src.features.spanselect.canonical import (
    CanonicalPick,
    historical_performance_basis_sid,
)
from src.features.writer import constants as writer_constants
from src.features.writer import logic as writer_logic
from src.features.writer import verify as writer_verify


_NUMBER_RE = re.compile(r"(?<![A-Za-z가-힣])[-+]?\d[\d,]*(?:\.\d+)?\s*(?:%|％|원|억|만|명|개|건|회|배|곳|개국|도시|석)?")
_CAUSAL_TERMS = ("때문", "기여", "영향", "개선", "전환", "견인", "덕분")
_DIRECT_CAUSAL_RE = re.compile(
    r"(?P<cause>[^.!?]{2,120}?)(?P<connector>때문에|덕분에|(?:으)?로 인해|이에 따라|"
    r"그 결과|결과로|에 기여(?:했|한|한다)?|을 견인(?:했|한|한다)?|를 견인(?:했|한|한다)?)"
    r"(?P<outcome>[^.!?]{2,160})",
    re.IGNORECASE,
)
_RESPONSE_TERMS = ("대응", "추진 중", "협의 중", "개선 중", "준비 중", "도입", "투자", "개편")
_TERM_RE = re.compile(r"[가-힣A-Za-z]{2,}|[-+]?\d[\d,]*(?:\.\d+)?")
_TERM_STOP = frozenset(
    {"회사는", "회사의", "해당", "현재", "관련", "대한", "통해", "기준", "공식"}
)

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


def majority_picks(rounds: Iterable[Iterable[CanonicalPick]], *, minimum: int = 2) -> list[CanonicalPick]:
    """세 독립 선택에서 소유권과 각 구조 필드가 각각 반복된 사실만 남긴다.

    같은 원문이 서로 다른 섹션에서 동률이면 소유권을 추측하지 않고 버린다.
    자유형 구조 발췌는 전체 객체가 우연히 완전히 같을 것을 요구하지 않고 필드별
    2표를 확인한다. 어느 필드든 2표 합의가 없으면 그 사실 전체를 버린다.
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
    return None


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

    passed, verify_step = writer_verify.verify_with_ai(
        lambda prompt, schema: ask(prompt, schema, writer_verify.VERIFY_MAX_TOKENS),
        written=written,
        evidence=evidence,
    )
    steps.append({"step": writer_verify.VERIFY_STEP, **verify_step})

    pick_list = list(picks)
    pick_by_exact = {
        (pick.section_id, pick.fragment_id, pick.sentence): pick
        for pick in pick_list
    }
    picks_by_fragment: dict[tuple[str, int], list[CanonicalPick]] = defaultdict(list)
    for pick in pick_list:
        picks_by_fragment[(pick.section_id, pick.fragment_id)].append(pick)

    claims: list[WrittenClaim] = []
    prose_by_section: dict[str, list[tuple[str, str]]] = defaultdict(list)
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
            fragment_kind = str(fragments.get(fragment_id, {}).get("종류") or "")
            decision = assess_claim(
                section_id,
                sentence.text,
                source_kind=fragment_kind,
                company=company,
            )
            if not decision.passed:
                continue
            clean = " ".join(sentence.text.split())
            pick = pick_by_exact.get((section_id, fragment_id, source.text))
            if pick is None:
                candidates = picks_by_fragment.get((section_id, fragment_id), [])
                if len(candidates) == 1 and candidates[0].sentence == source.text:
                    pick = candidates[0]
            # 선택 metadata가 작가 단계에서 사라지면 의미 계약을 검증할 수 없다.
            if pick is None:
                continue
            if _DIRECT_CAUSAL_RE.search(clean) and _direct_causal_fields(
                clean, source.text, subject_label=pick.subject_label
            ) is None:
                continue
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

    return (
        [
            replace(section, prose_lines=prose_by_section.get(section.cell, []))
            for section in sections
        ],
        claims,
    )


def _source_date(source: Source) -> str:
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
    valid_sources = [
        source for source in sources if source.is_canonical_official
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
            visible_prose.append((claim.text, claim.cite))
        section_facts.extend(table_facts)
        # 원문은 내부 감사용으로 보존하되, 공개 렌더러는 prose_lines와 표만 사용한다.
        if not section_facts:
            continue
        locked_sections.append(
            replace(
                section,
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
    """1~9장이 붙은 내부 초안에서 요약을 새로 만들고 최종 출고한다."""

    summary_input = {
        section.cell: [text for text, _cite in section.prose_lines]
        for section in draft.sections
        if section.prose_lines
    }
    summary, summary_steps = build_summary_with_ai(
        summary_ask,
        company=draft.company,
        sections=summary_input,
    )
    steps.extend(summary_steps)

    facts_by_id = {fact.fact_id: fact for fact in draft.fact_records}
    fact_ids_by_section: dict[str, list[str]] = defaultdict(list)
    for fact in draft.fact_records:
        fact_ids_by_section[fact.section_owner].append(fact.fact_id)
    locked_summaries: list[SummaryItem] = []
    for item in summary:
        fact_ids = _minimal_summary_fact_ids(
            item.text,
            fact_ids_by_section.get(item.section_id, []),
            facts_by_id,
        )
        if not fact_ids:
            continue
        evidence_text = summary_evidence_text(fact_ids, facts_by_id)
        support_terms = _common_support_terms(item.text, evidence_text)
        if len(set(support_terms)) < 2:
            continue
        status = "independently_verified"
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
    "finalize_report",
    "majority_picks",
    "sections_from_picks",
    "write_and_verify_sections",
]
