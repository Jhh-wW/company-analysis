"""v2의 구조화 원자료를 공개 claim과 결속 DTO로 만드는 첫 수직 슬라이스.

산문에서 숫자를 정규식으로 되짚지 않는다. ``company_performance``가 검증해
넘긴 원값·기간·회계범위만 사용해 누적 증감률을 다시 계산하고, 계산 계약을
통과한 문장만 ``past_changes``에 더한다. 구조 필드가 하나라도 없으면 아무
claim도 만들지 않아 부분/차단 상태가 그대로 드러난다.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from decimal import (
    Decimal,
    DecimalException,
    InvalidOperation,
    ROUND_HALF_UP,
    localcontext,
)

import logging

from src.core.citations import citation_number
from src.features.composer.constants import (
    DART_FINANCIAL_API_DOCUMENT_ID,
    DART_FINANCIAL_API_HOST,
    DART_FINANCIAL_API_PREFIX,
    DART_FINANCIAL_API_URL,
    GRADE_CONFIRMED,
)
from src.features.composer.logic import FragmentsInput, _normalize_fragments
from src.features.composer.port import (
    ComposedReport,
    ComposedSection,
    ComposedSentence,
    FilingMeta,
    PerformanceTable,
    StructuredClaim,
)
from src.shared.dart_financial_provenance import dart_payload_matches_table
from src.shared.report_quality.constants import (
    NUMERIC_BINDING_VERSION,
    ROUNDING_MODE,
)
from src.shared.report_quality.assessment import has_public_numeric_token
from src.shared.report_quality.dto import ClaimFact
from src.shared.report_quality.models import VerificationState
from src.shared.report_quality.numeric import (
    claim_fact_from_binding,
    numeric_binding_problems,
)
from src.shared.report_quality.numeric_models import (
    EntityScope,
    NumericBinding,
    NumericFormula,
    NumericOperand,
    NumericSign,
    UnitDimension,
)
from src.shared.report_quality.source_identity import document_identity_from_parts
from src.shared.report_quality.numeric_validation import validate_versioned_numeric_claim


PAST_CHANGES_SECTION_ID = "past_changes"
RATE_ROUNDING_PLACES = 2
RATE_TOLERANCE = "0.000001"


@dataclass(frozen=True)
class NumericSafetyFiltering:
    """새 생성 공개본에서 결속 부족 수치 문장을 걷어낸 기록."""

    removed_section_counts: tuple[tuple[str, int], ...] = ()
    removed_summary_count: int = 0

    @property
    def removed_total(self) -> int:
        return sum(count for _section_id, count in self.removed_section_counts) + int(
            self.removed_summary_count
        )

    def merged(self, other: "NumericSafetyFiltering") -> "NumericSafetyFiltering":
        counts: dict[str, int] = {}
        for section_id, count in (
            *self.removed_section_counts,
            *other.removed_section_counts,
        ):
            counts[section_id] = counts.get(section_id, 0) + int(count)
        return NumericSafetyFiltering(
            removed_section_counts=tuple(
                (section_id, count)
                for section_id, count in counts.items()
                if count > 0
            ),
            removed_summary_count=(
                self.removed_summary_count + other.removed_summary_count
            ),
        )


logger = logging.getLogger(__name__)


#: 4장 누적 증감률 claim 이 «왜» 안 만들어졌는지 남기는 사유 코드.
#: ★ 2026-08-29 — 저장 보고서 38건이 전부 구조화 사실 0개인데 오프라인
#:   시험은 3개를 만든다. 즉 «실제 자료에서만» 죽는데 그 이유가 로그에
#:   한 줄도 없었다. 조기 반환마다 사유 코드를 남긴다.
#: ⚠️ 회사 원문·금액은 남기지 않는다 — 사유 코드와 개수만.
def _log_no_claim(reason: str) -> None:
    logger.warning("4장 누적 증감률 claim 을 만들지 못했습니다: %s", reason)


def _cumulative_rate_claim_text(
    *,
    entity_scope: str,
    metric: str,
    period_start: str,
    period_end: str,
    display_value: str,
) -> str:
    scope_label = {
        EntityScope.CONSOLIDATED.value: "연결",
        EntityScope.SEPARATE.value: "별도",
    }.get(entity_scope, "")
    if not all((scope_label, metric, period_start, period_end, display_value)):
        return ""
    return (
        f"{scope_label} {metric}의 {period_start}년부터 {period_end}년까지 "
        f"누적 증감률은 {display_value}%입니다."
    )


def _structured_numeric_fact(
    sentence: ComposedSentence,
    *,
    section_id: str,
) -> ClaimFact | None:
    """프로그램 생성 수치 문장을 손실 없는 중립 DTO로 투영한다.

    AI 산문을 정규식으로 FactRecord로 꾸미지 않는다. 이미 붙어 있는
    ``StructuredClaim``의 모든 이름표와 versioned NumericBinding만 옮긴다.
    지금 공개를 허용하는 첫 계약은 코드가 만든 누적 증감률 문장 하나뿐이다.
    """

    claim = sentence.structured_claim
    if (
        claim is None
        or not claim.fact_id.strip()
        or not claim.claim_slot.strip()
        or claim.section_owner != section_id
        or sentence.planned_claim_slot != claim.claim_slot
        or sentence.verification_state != VerificationState.VERIFIED.value
        or claim.verification_state != VerificationState.VERIFIED.value
        or sentence.citations != (claim.source_fragment_id,)
        or not claim.source_identity.strip()
        or not claim.state_evidence.strip()
        or not claim.numeric_checks
    ):
        return None
    expected_text = _cumulative_rate_claim_text(
        entity_scope=claim.subject_scope,
        metric=claim.metric,
        period_start=claim.period_start,
        period_end=claim.period_end,
        display_value=claim.display_value,
    )
    # NumericBinding과 공개 문장을 따로 검증하면, 결속은 24.28인데 글만 25로
    # 바꾼 손상이 통과한다. 현재 코드 생산자의 정확한 문장 계약까지 함께 잠근다.
    if claim.formula != NumericFormula.RATE.value or sentence.text != expected_text:
        return None
    return ClaimFact(
        fact_id=claim.fact_id,
        section_owner=claim.section_owner,
        source_id=f"v2-frag-{claim.source_fragment_id}",
        source_identity=claim.source_identity,
        verification_state=claim.verification_state,
        claim_slot=claim.claim_slot,
        claim=sentence.text,
        subject_scope=claim.subject_scope,
        raw_value=claim.raw_value,
        calculation=claim.calculation,
        display_value=claim.display_value,
        rounding_rule=claim.rounding_rule,
        numeric_checks=claim.numeric_checks,
        metric=claim.metric,
        period_start=claim.period_start,
        period_end=claim.period_end,
        sign=claim.sign,
        unit=claim.unit,
        unit_dimension=claim.unit_dimension,
        formula=claim.formula,
    )


#: ③ «이미 통과한 검사»를 인정할 것인가 (2026-08-29, 사용자 결정).
#:
#: True  — `verification_state == "verified"` 인 문장은 통과시킨다.
#:         그 표식은 ① 숫자를 인용 조각·실적표와 대조 통과 ② 검수 AI 가 참으로 판정
#:         을 «둘 다» 거쳐야만 붙는다(기본값은 "unverified", 작가가 못 붙인다).
#: False — v2-98 원래 동작. 구조화 사실이 붙은 문장만 통과 →
#:         작가가 쓴 숫자 문장은 «전부» 삭제된다(실측: 45→25문장, 점수 33/100).
#:
#: ⚠️ 되돌리려면 이 값을 False 로 바꾸면 된다. 다른 코드는 건드릴 필요 없다.
ALLOW_VERIFIED_NUMERIC_SENTENCES: bool = True


def is_release_ready_numeric_sentence(
    sentence: ComposedSentence,
    *,
    section_id: str,
) -> bool:
    """숫자가 든 공개 문장이 의미 결속까지 완전한가."""

    if not has_public_numeric_token(sentence.text):
        return True
    # ③ 이미 두 번 검사를 통과한 문장에 «또» 증명서를 요구하지 않는다.
    #   ⚠️ 네 조건을 «모두» 요구한다 — 하나라도 빼면 검사를 빼는 것이 된다.
    #     · 증명서 없음 : 증명서가 «발급된» 문장은 아래 대조 경로로 보낸다.
    #       발급됐다는 건 표시 숫자를 계산값과 맞춰 볼 수 있다는 뜻이고,
    #       맞춰 볼 수 있으면 반드시 맞춰 본다. 이걸 빼면 표시값만 25%로
    #       바꿔치기한 문장이 «검수 통과» 표식을 달고 그대로 나간다.
    #     · verified : 숫자 대조 + 검수 AI 판정을 둘 다 통과했다는 표식
    #     · 확인 등급 : 「해석」은 사실 주장이 아니므로 숫자를 실을 자격이 없다
    #     · 인용 있음 : 어느 근거에서 온 숫자인지 되짚을 수 있어야 한다
    if (
        ALLOW_VERIFIED_NUMERIC_SENTENCES
        and sentence.structured_claim is None
        and sentence.verification_state == VerificationState.VERIFIED.value
        and sentence.grade == GRADE_CONFIRMED
        and sentence.citations
    ):
        return True
    fact = _structured_numeric_fact(sentence, section_id=section_id)
    return fact is not None and validate_versioned_numeric_claim(fact) == ()


def enforce_public_numeric_safety(
    report: ComposedReport,
) -> tuple[ComposedReport, NumericSafetyFiltering]:
    """새 v2 생성물의 미결속 수치·날짜 문장을 문장 단위로 제외한다.

    저장된 옛 보고서를 다시 읽거나 고치지 않고, ``run_v2``가 조립 중인
    ``ComposedReport``에만 적용한다. 숫자 토큰은 결속 필요 여부만 가르며,
    텍스트에서 값·지표·기간을 추측해 구조화 사실을 만들지 않는다.
    """

    sections: list[ComposedSection] = []
    removed_sections: list[tuple[str, int]] = []
    safe_owner_by_fact_id: dict[str, str] = {}
    for section in report.sections:
        kept = tuple(
            sentence
            for sentence in section.sentences
            if is_release_ready_numeric_sentence(
                sentence,
                section_id=section.section_id,
            )
        )
        removed = len(section.sentences) - len(kept)
        if removed:
            removed_sections.append((section.section_id, removed))
        sections.append(replace(section, sentences=kept))
        for sentence in kept:
            claim = sentence.structured_claim
            if (
                claim is not None
                and has_public_numeric_token(sentence.text)
                and is_release_ready_numeric_sentence(
                    sentence,
                    section_id=section.section_id,
                )
            ):
                safe_owner_by_fact_id[claim.fact_id] = section.section_id

    summary: list[ComposedSentence] = []
    for sentence in report.summary:
        if not has_public_numeric_token(sentence.text):
            summary.append(sentence)
            continue
        claim = sentence.structured_claim
        owner = claim.section_owner if claim is not None else ""
        if (
            claim is not None
            and safe_owner_by_fact_id.get(claim.fact_id) == owner
            and is_release_ready_numeric_sentence(sentence, section_id=owner)
        ):
            summary.append(sentence)

    return (
        ComposedReport(sections=tuple(sections), summary=tuple(summary)),
        NumericSafetyFiltering(
            removed_section_counts=tuple(removed_sections),
            removed_summary_count=len(report.summary) - len(summary),
        ),
    )


def _decimal_cell(value: str) -> Decimal | None:
    raw = str(value or "").strip().replace(",", "")
    if not raw or len(raw) > 128:
        return None
    try:
        parsed = Decimal(raw)
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() else None


def _sign(value: Decimal) -> NumericSign:
    if value > 0:
        return NumericSign.POSITIVE
    if value < 0:
        return NumericSign.NEGATIVE
    return NumericSign.ZERO


def _source_context(
    table: PerformanceTable,
    fragments: FragmentsInput,
    _filing_meta: FilingMeta | None,
) -> tuple[str, str, str] | None:
    """표 cite가 가리키는 실제 조각·독립 문서·원 payload를 결속한다."""

    fragment_id = citation_number(table.cite)
    if not fragment_id:
        _log_no_claim("표의 인용 번호가 비었음")
        return None
    fragment = next(
        (
            item
            for item in _normalize_fragments(fragments)
            if item.fragment_id == fragment_id
        ),
        None,
    )
    if fragment is None:
        _log_no_claim("표가 가리키는 인용 조각을 못 찾음")
        return None
    # 주요계정 값은 선택된 사업보고서와 별도의 OpenDART API 호출에서 왔다.
    # filing_meta 접수번호로 fallback하면 서로 다른 문서를 같은 근거로 꾸민다.
    if not fragment.text.startswith(DART_FINANCIAL_API_PREFIX):
        _log_no_claim("인용 조각이 DART 주요계정 API 원문이 아님")
        return None
    source_identity = document_identity_from_parts(
        document_id=DART_FINANCIAL_API_DOCUMENT_ID,
        host=DART_FINANCIAL_API_HOST,
        url=DART_FINANCIAL_API_URL,
    )
    evidence = tuple(dict.fromkeys(value for value in table.evidence_rows if value.strip()))
    # 하나의 claim을 서로 다른 원문 payload에 억지로 묶지 않는다. 현재 DART
    # 3개년 표는 한 API payload를 세 행에 그대로 보존한다.
    if not source_identity:
        _log_no_claim("독립 문서 신원을 만들지 못함")
        return None
    if len(evidence) != 1:
        _log_no_claim(f"표의 근거 payload 가 1개가 아님 ({len(evidence)}개)")
        return None
    if not dart_payload_matches_table(table, evidence[0]):
        _log_no_claim("표와 DART 원 payload 의 대조가 실패함")
        return None
    return fragment_id, source_identity, evidence[0]


def _fact_id(
    *,
    source_identity: str,
    claim_slot: str,
    binding: NumericBinding,
    state_evidence: str,
) -> str:
    """주장 범주와 실제 수치 사실을 분리해 안정적인 사실 ID를 만든다.

    ``claim_slot``은 같은 종류의 여러 사실이 함께 쓰는 닫힌 범주다. 따라서
    범주와 문서 신원만으로 ID를 만들면 매출액과 영업이익처럼 서로 다른
    사실이 같은 ID로 충돌한다. 원값·기간·공식과 실제 원문 해시까지 포함해
    내용이 다른 사실은 반드시 다른 ID가 되게 한다.
    """

    payload = json.dumps(
        {
            "version": NUMERIC_BINDING_VERSION,
            "source_identity": source_identity,
            "claim_slot": claim_slot,
            "metric": binding.metric,
            "entity_scope": binding.entity_scope.value,
            "period_start": binding.period_start,
            "period_end": binding.period_end,
            "formula": binding.formula.value,
            "operands": [
                {
                    "role": operand.role,
                    "metric": operand.metric,
                    "entity_scope": operand.entity_scope.value,
                    "period": operand.period,
                    "value": operand.value,
                    "sign": operand.sign.value,
                    "unit": operand.unit,
                    "unit_dimension": operand.unit_dimension.value,
                    "source_identity": operand.source_identity,
                }
                for operand in binding.operands
            ],
            "calculated_value": binding.calculated_value,
            "display_value": binding.display_value,
            "rounding_mode": binding.rounding_mode,
            "rounding_places": binding.rounding_places,
            "tolerance": binding.tolerance,
            "state_evidence_sha256": hashlib.sha256(
                state_evidence.encode("utf-8")
            ).hexdigest(),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return "v2-fact-" + hashlib.sha256(payload).hexdigest()[:24]


def build_past_changes_numeric_claims(
    table: PerformanceTable | None,
    fragments: FragmentsInput,
    filing_meta: FilingMeta | None,
) -> tuple[ComposedSentence, ...]:
    """검증된 3개년 표에서 지표별 누적 증감률 claim을 결정론적으로 만든다."""

    if (
        table is None
        or len(table.headers) < 2
        or len(table.rows) < 2
        or len(table.raw_rows) != len(table.rows)
        or not table.entity_scope
        or not table.raw_unit
        or not table.unit_dimension
    ):
        _log_no_claim("실적표가 없거나 범위·단위 정보가 비었음")
        return ()
    source = _source_context(table, fragments, filing_meta)
    if source is None:
        # 사유는 _source_context 가 이미 남겼다.
        return ()
    fragment_id, source_identity, state_evidence = source
    try:
        entity_scope = EntityScope(table.entity_scope)
        operand_dimension = UnitDimension(table.unit_dimension)
    except ValueError:
        _log_no_claim("표의 범위·단위 값이 알려진 목록에 없음")
        return ()

    indexed_rows: list[tuple[int, tuple[str, ...], tuple[str, ...]]] = []
    for display_row, raw_row in zip(table.rows, table.raw_rows):
        if (
            len(display_row) != len(table.headers)
            or len(raw_row) != len(table.headers)
            or display_row[0] != raw_row[0]
            or len(raw_row[0]) != 4
            or not raw_row[0].isdigit()
        ):
            _log_no_claim("표의 행 모양이 «연도 + 지표»가 아님")
            return ()
        indexed_rows.append((int(raw_row[0]), display_row, raw_row))
    indexed_rows.sort(key=lambda item: item[0])
    start_year, _start_display, start_raw = indexed_rows[0]
    end_year, _end_display, end_raw = indexed_rows[-1]
    if start_year >= end_year:
        _log_no_claim("시작 연도가 끝 연도보다 뒤임")
        return ()

    if entity_scope not in (EntityScope.CONSOLIDATED, EntityScope.SEPARATE):
        _log_no_claim("표의 범위가 연결·별도가 아님")
        return ()

    claims: list[ComposedSentence] = []
    for column, metric in enumerate(table.headers[1:], start=1):
        metric = metric.strip()
        start_value = _decimal_cell(start_raw[column])
        end_value = _decimal_cell(end_raw[column])
        if (
            not metric
            or start_value is None
            or end_value is None
            # 음수 기준 또는 0 교차를 일반 「증감률」로 VERIFIED 처리하면
            # -100→+100을 200% 성장이라고 부르는 의미 오류가 생긴다.
            or start_value <= 0
            or end_value < 0
        ):
            continue
        # 입력 길이는 제한하지만 Decimal의 지수는 매우 클 수 있다. 한 지표가
        # 계산 불가능하다는 이유로 보고서 전체를 죽이지 않고, 그 claim만
        # 만들지 않아 품질 상태가 정직하게 PARTIAL/BLOCKED로 남게 한다.
        try:
            with localcontext() as context:
                context.prec = 160
                calculated = (
                    (end_value - start_value) / abs(start_value) * Decimal(100)
                )
                calculated_stored = calculated.quantize(
                    Decimal("0.000001"), rounding=ROUND_HALF_UP
                )
                display_decimal = calculated.quantize(
                    Decimal("0.01"), rounding=ROUND_HALF_UP
                )
        except (DecimalException, OverflowError, ValueError):
            continue
        calculated_text = f"{calculated_stored:.6f}"
        if display_decimal == 0:
            display_decimal = Decimal("0.00")
        display_value = f"{display_decimal:.2f}"
        # claim_slot은 사실의 고유 번호가 아니라 원자 주장 범주다. 지표·기간을
        # 끼워 넣은 동적 문자열은 닫힌 9장 정책을 어기고, 검사기와 생성기의
        # 계약을 서로 다르게 만든다. 사실별 고유성은 아래 fact_id가 맡는다.
        claim_slot = "past_changes:cumulative_change"
        operands = (
            NumericOperand(
                role="start",
                metric=metric,
                entity_scope=entity_scope,
                period=str(start_year),
                value=format(start_value, "f"),
                sign=_sign(start_value),
                unit=table.raw_unit,
                unit_dimension=operand_dimension,
                source_identity=source_identity,
            ),
            NumericOperand(
                role="end",
                metric=metric,
                entity_scope=entity_scope,
                period=str(end_year),
                value=format(end_value, "f"),
                sign=_sign(end_value),
                unit=table.raw_unit,
                unit_dimension=operand_dimension,
                source_identity=source_identity,
            ),
        )
        binding = NumericBinding(
            version=NUMERIC_BINDING_VERSION,
            metric=metric,
            entity_scope=entity_scope,
            period_start=str(start_year),
            period_end=str(end_year),
            sign=_sign(calculated),
            unit="%",
            unit_dimension=UnitDimension.PERCENT,
            formula=NumericFormula.RATE,
            operands=operands,
            calculated_value=calculated_text,
            display_value=display_value,
            rounding_mode=ROUNDING_MODE,
            rounding_places=RATE_ROUNDING_PLACES,
            tolerance=RATE_TOLERANCE,
            source_identity=source_identity,
            verification_state=VerificationState.VERIFIED,
        )
        if numeric_binding_problems(binding):
            continue
        fact_id = _fact_id(
            source_identity=source_identity,
            claim_slot=claim_slot,
            binding=binding,
            state_evidence=state_evidence,
        )
        claim_text = _cumulative_rate_claim_text(
            entity_scope=entity_scope.value,
            metric=metric,
            period_start=str(start_year),
            period_end=str(end_year),
            display_value=display_value,
        )
        projected = claim_fact_from_binding(
            fact_id=fact_id,
            section_owner=PAST_CHANGES_SECTION_ID,
            source_id=f"v2-frag-{fragment_id}",
            claim=claim_text,
            claim_slot=claim_slot,
            binding=binding,
        )
        claims.append(
            ComposedSentence(
                text=claim_text,
                citations=(fragment_id,),
                grade=GRADE_CONFIRMED,
                planned_claim_slot=claim_slot,
                verification_state=VerificationState.VERIFIED.value,
                structured_claim=StructuredClaim(
                    fact_id=fact_id,
                    claim_slot=claim_slot,
                    section_owner=PAST_CHANGES_SECTION_ID,
                    source_fragment_id=fragment_id,
                    source_identity=source_identity,
                    verification_state=VerificationState.VERIFIED.value,
                    state_evidence=state_evidence,
                    subject_scope=projected.subject_scope,
                    metric=binding.metric,
                    period_start=binding.period_start,
                    period_end=binding.period_end,
                    sign=binding.sign.value,
                    unit=binding.unit,
                    unit_dimension=binding.unit_dimension.value,
                    formula=binding.formula.value,
                    raw_value=projected.raw_value,
                    calculation=projected.calculation,
                    display_value=projected.display_value,
                    rounding_rule=projected.rounding_rule,
                    numeric_checks=projected.numeric_checks,
                ),
            )
        )
    return tuple(claims)


def append_past_changes_numeric_claims(
    report: ComposedReport,
    table: PerformanceTable | None,
    fragments: FragmentsInput,
    filing_meta: FilingMeta | None,
) -> ComposedReport:
    """프로그램 검증 claim을 해당 장에 더하되 다른 장과 요약은 보존한다."""

    claims = build_past_changes_numeric_claims(table, fragments, filing_meta)
    if not claims:
        return report
    sections = tuple(
        replace(section, sentences=section.sentences + claims)
        if section.section_id == PAST_CHANGES_SECTION_ID
        else section
        for section in report.sections
    )
    return ComposedReport(sections=sections, summary=report.summary)
