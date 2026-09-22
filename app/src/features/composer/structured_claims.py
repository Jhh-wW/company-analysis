"""v2의 구조화 원자료를 공개 claim과 결속 DTO로 만드는 첫 수직 슬라이스.

산문에서 숫자를 정규식으로 되짚지 않는다. ``company_performance``가 검증해
넘긴 원값·기간·회계범위만 사용해 누적 증감률을 다시 계산하고, 계산 계약을
통과한 문장만 ``past_changes``에 더한다. 구조 필드가 하나라도 없으면 아무
claim도 만들지 않아 부분/차단 상태가 그대로 드러난다.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
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
    DART_DOCUMENT_HOST,
    DART_DOCUMENT_URL_TEMPLATE,
    DART_FINANCIAL_API_DOCUMENT_ID,
    DART_FINANCIAL_API_HOST,
    DART_FINANCIAL_API_PREFIX,
    DART_FINANCIAL_API_URL,
    GRADE_CONFIRMED,
    NOTICE_NUMERIC_BODY_WITHHELD,
)
from src.features.composer.logic import FragmentsInput, _normalize_fragments
from src.features.composer.port import (
    CollectedFragment,
    ComposedReport,
    ComposedSection,
    ComposedSentence,
    FilingMeta,
    PerformanceTable,
    StructuredClaim,
)
from src.shared.dart_financial_provenance import dart_payload_matches_table
from src.shared.display_scale import format_display_value, quantum_for
from src.shared.report_evidence.legacy_fragment_kinds import (
    LEGACY_KIND_AUDIT_FINANCIAL,
)
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
from src.shared.report_quality.numeric_codec import decode_numeric_check
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
#: 부호 변화 문장의 계산은 표시 단위 두 값의 뺄셈뿐이라 오차가 생기지 않는다.
#: 그래도 증감률과 같은 허용치를 쓰는 이유는, 두 계약이 다른 값을 쓰면 어느
#: 쪽이 «느슨한 쪽»인지 사람이 매번 다시 확인해야 하기 때문이다.
SIGNED_CHANGE_TOLERANCE = "0.000001"
#: 감사보고서 표의 행 근거 payload가 스스로 선언하는 원문 종류.
#: ★ 값의 정본은 ``audit_financials.constants.AUDIT_REPORT_STATEMENT_SOURCE``다.
#:   composer가 다른 feature를 직접 import 하지 않는다는 경계 규칙 때문에
#:   ``public_manifest``·``canonical_report``와 같은 방식으로 값을 다시 적고,
#:   두 값이 갈라지지 않도록 대조 시험이 정본과 같은지 확인한다.
AUDIT_STATEMENT_EVIDENCE_SOURCE = "audit_report_statement"
#: DART 접수번호는 14자리 숫자다. 이 모양이 아니면 원문 주소를 만들 수 없다.
DART_RECEIPT_NUMBER_LENGTH = 14
#: 한글 음절 영역과 받침 개수 — 조사(은/는·으로/로·이/가)를 고르는 데 쓴다.
HANGUL_SYLLABLE_FIRST = 0xAC00
HANGUL_SYLLABLE_LAST = 0xD7A3
HANGUL_JONGSEONG_COUNT = 28
#: 받침 «ㄹ»의 번호. ㄹ 받침은 「으로」가 아니라 「로」를 쓴다.
HANGUL_JONGSEONG_RIEUL = 8
#: 회계 범위 값 → 문장에 쓰는 한국어 표기.
SCOPE_LABELS = {
    EntityScope.CONSOLIDATED.value: "연결",
    EntityScope.SEPARATE.value: "별도",
}


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
#: ★ 저장 보고서 38건이 전부 구조화 사실 0개인데 오프라인
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
    scope_label = SCOPE_LABELS.get(entity_scope, "")
    if not all((scope_label, metric, period_start, period_end, display_value)):
        return ""
    # 본문은 「한다체」다. 프로그램이 만드는 이 문장만 「합니다체」면 독자에게는
    # 한 보고서 안에서 말투가 바뀌는 것으로 보인다(실측 결함 S9).
    return (
        f"{scope_label} {metric}의 {period_start}년부터 {period_end}년까지 "
        f"누적 증감률은 {display_value}%이다."
    )


def _final_jongseong(value: str) -> int | None:
    """낱말 마지막 글자의 받침 번호. 한글 음절이 아니면 ``None``."""

    text = str(value or "").strip()
    if not text:
        return None
    code = ord(text[-1])
    if not HANGUL_SYLLABLE_FIRST <= code <= HANGUL_SYLLABLE_LAST:
        return None
    return (code - HANGUL_SYLLABLE_FIRST) % HANGUL_JONGSEONG_COUNT


def _topic_particle(word: str) -> str:
    """「…은 / …는」. 받침을 모르면 빈 문자열로 닫는다."""

    jongseong = _final_jongseong(word)
    if jongseong is None:
        return ""
    return "는" if jongseong == 0 else "은"


def _directional_particle(word: str) -> str:
    """「…으로 / …로」. ㄹ 받침과 받침 없음은 「로」다."""

    jongseong = _final_jongseong(word)
    if jongseong is None:
        return ""
    return "로" if jongseong in (0, HANGUL_JONGSEONG_RIEUL) else "으로"


def _subject_particle(word: str) -> str:
    """「…이 / …가」. 받침을 모르면 빈 문자열로 닫는다."""

    jongseong = _final_jongseong(word)
    if jongseong is None:
        return ""
    return "가" if jongseong == 0 else "이"


def _scaled_text(value: Decimal, places: int) -> str:
    """표시 단위 값을 표와 «같은 글자»로 적는다(천 단위 쉼표 포함)."""

    return f"{value:,.{places}f}"


def _signed_change_claim_text(
    *,
    entity_scope: str,
    metric: str,
    period_start: str,
    period_end: str,
    start_value: Decimal,
    end_value: Decimal,
    display_unit: str,
    display_places: int,
) -> str:
    """손실·적자 구간의 변화를 증감률 대신 두 시점 값으로 서술한다.

    ★ 여기서 증감률을 계산하지 않는다 — -100에서 +100으로 간 값을 「200%
      성장」이라고 부르면 계산식은 맞아도 독자를 오도하기 때문이다. 대신 표에
      이미 있는 두 값을 그대로 읽고, 방향만 「늘었다/줄었다/돌아섰다」로 적는다.
    """

    scope_label = SCOPE_LABELS.get(entity_scope, "")
    if (
        not all((scope_label, metric, period_start, period_end, display_unit))
        or display_places < 0
        or start_value == end_value
    ):
        return ""
    topic = _topic_particle(metric)
    directional = _directional_particle(display_unit)
    subject = _subject_particle(display_unit)
    if not topic or not directional or not subject:
        return ""
    head = (
        f"{scope_label} {metric}{topic} "
        f"{period_start}년 {_scaled_text(start_value, display_places)}{display_unit}에서 "
        f"{period_end}년 {_scaled_text(end_value, display_places)}{display_unit}"
    )
    # 0에서 양수로 바뀌면 배율을 말할 수 없으므로 결과만 적는다.
    if start_value == 0 and end_value > 0:
        return f"{head}{subject} 됐다."
    if start_value < 0 and end_value < 0:
        tail = "손실이 늘었다" if end_value < start_value else "손실이 줄었다"
    elif start_value < 0 and end_value == 0:
        tail = "손실이 사라졌다"
    elif start_value < 0 < end_value:
        tail = "흑자로 돌아섰다"
    elif start_value >= 0 and end_value < 0:
        tail = "적자로 돌아섰다"
    else:
        # 양수 구간은 증감률 문장이 맡는다.
        return ""
    return f"{head}{directional} {tail}."


def _signed_change_text_from_binding(claim: StructuredClaim) -> str:
    """부호 변화 문장을 결속에 저장된 두 시점 값에서 그대로 되살린다.

    증감률 문장은 표시값 한 개가 문장의 숫자 전부라 이름표만으로 복원되지만,
    이 문장은 시작·종료 두 값을 글자로 읽는다. 그래서 결속 자체를 열어 같은
    생산자 함수로 다시 조립한다 — 문장과 결속이 따로 만들어지면 한쪽만 바뀐
    손상을 잡을 수 없다.
    """

    if len(claim.numeric_checks) != 1:
        return ""
    try:
        binding = decode_numeric_check(claim.numeric_checks[0])
    except ValueError:
        return ""
    if binding.formula is not NumericFormula.SIGNED_CHANGE:
        return ""
    operands = {operand.role: operand for operand in binding.operands}
    start = operands.get("start")
    end = operands.get("end")
    if start is None or end is None:
        return ""
    start_value = _decimal_cell(start.value)
    end_value = _decimal_cell(end.value)
    if start_value is None or end_value is None:
        return ""
    return _signed_change_claim_text(
        entity_scope=claim.subject_scope,
        metric=claim.metric,
        period_start=claim.period_start,
        period_end=claim.period_end,
        start_value=start_value,
        end_value=end_value,
        display_unit=binding.unit,
        display_places=binding.rounding_places,
    )


def _expected_claim_text(claim: StructuredClaim) -> str:
    """이 구조화 claim이 만들었어야 할 공개 문장 — 공식마다 정확히 하나다."""

    if claim.formula == NumericFormula.RATE.value:
        return _cumulative_rate_claim_text(
            entity_scope=claim.subject_scope,
            metric=claim.metric,
            period_start=claim.period_start,
            period_end=claim.period_end,
            display_value=claim.display_value,
        )
    if claim.formula == NumericFormula.SIGNED_CHANGE.value:
        return _signed_change_text_from_binding(claim)
    return ""


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
    # NumericBinding과 공개 문장을 따로 검증하면, 결속은 24.28인데 글만 25로
    # 바꾼 손상이 통과한다. 현재 코드 생산자의 정확한 문장 계약까지 함께 잠근다.
    expected_text = _expected_claim_text(claim)
    if not expected_text or sentence.text != expected_text:
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


#: ③ «이미 통과한 검사»를 인정할 것인가 (제품 결정).
#:
#: True  — `verification_state == "verified"` 인 문장은 통과시킨다.
#:         그 표식은 ① 숫자를 인용 조각·실적표와 대조 통과 ② 검수 AI 가 참으로 판정
#:         을 «둘 다» 거쳐야만 붙는다(기본값은 "unverified", 작가가 못 붙인다).
#: False — 수치 안전 필터의 원래 동작. 구조화 사실이 붙은 문장만 통과 →
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


def safe_numeric_owners_by_fact_id(
    sections: Sequence[ComposedSection],
) -> dict[str, str]:
    """공개해도 되는 수치 문장을 실제로 실은 «소유 장»을 fact_id마다 모은다.

    ★ 요약 잣대의 재료다. 요약 문장은 그 값을 실은 장이 «본문에서도» 그대로
      살아남았을 때만 실릴 수 있다 — 본문에서 빠진 값을 요약만 들고 있으면
      되짚을 자리가 없기 때문이다.
    """

    owners: dict[str, str] = {}
    for section in sections:
        for sentence in section.sentences:
            claim = sentence.structured_claim
            if (
                claim is not None
                and has_public_numeric_token(sentence.text)
                and is_release_ready_numeric_sentence(
                    sentence, section_id=section.section_id
                )
            ):
                owners[claim.fact_id] = section.section_id
    return owners


def is_release_ready_summary_sentence(
    sentence: ComposedSentence,
    *,
    safe_owner_by_fact_id: Mapping[str, str],
) -> bool:
    """요약에 실어도 되는 문장인가 — 본문 잣대보다 «좁다».

    ★ 본문 잣대(`is_release_ready_numeric_sentence`)는 구조화 사실이 없어도
      「검수 통과 표식 + 확인 등급 + 인용」이면 통과시킨다. 요약 잣대는 그
      길을 주지 않고 구조화 사실과 소유 장 일치를 요구한다. 두 잣대가 다르기
      때문에, 본문에 남은 문장을 요약으로 «옮기는» 경로는 반드시 이 함수를
      다시 통과해야 한다 — 안 그러면 요약이 본문보다 느슨해진다.
    """

    if not has_public_numeric_token(sentence.text):
        return True
    claim = sentence.structured_claim
    if claim is None:
        return False
    owner = claim.section_owner
    return (
        safe_owner_by_fact_id.get(claim.fact_id) == owner
        and is_release_ready_numeric_sentence(sentence, section_id=owner)
    )


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
        notice = section.notice
        if removed and not kept and not notice:
            notice = NOTICE_NUMERIC_BODY_WITHHELD
        sections.append(replace(section, sentences=kept, notice=notice))
    # ★ 요약 잣대의 재료는 «걸러 낸 뒤»의 본문이다. 요약 보충 경로가 같은
    #   잣대를 다시 쓰도록 두 조각(소유 장 모으기·요약 술어)을 함수로 뺐다 —
    #   같은 규칙이 두 벌로 갈라지면 한쪽만 고쳐져 표류한다.
    safe_owner_by_fact_id = safe_numeric_owners_by_fact_id(sections)

    summary = [
        sentence
        for sentence in report.summary
        if is_release_ready_summary_sentence(
            sentence, safe_owner_by_fact_id=safe_owner_by_fact_id
        )
    ]

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
    filing_meta: FilingMeta | None,
) -> tuple[str, str, str] | None:
    """표 cite가 가리키는 실제 조각·독립 문서·원문 지문을 결속한다.

    두 갈래뿐이다. 상장사 표는 OpenDART 주요계정 API 응답에서 오고, 비상장
    회사는 같은 값을 감사보고서 손익계산서 «평문»에서 읽는다. 둘 다 아니면
    claim을 만들지 않는다(사유를 남기고 닫는다).
    """

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
    if fragment.text.startswith(DART_FINANCIAL_API_PREFIX):
        return _financial_api_source_context(table, fragment_id)
    if fragment.kind.strip() == LEGACY_KIND_AUDIT_FINANCIAL:
        return _audit_report_source_context(table, fragment, filing_meta)
    _log_no_claim("인용 조각이 주요계정 API 원문도 감사보고서 재무 조각도 아님")
    return None


def _financial_api_source_context(
    table: PerformanceTable,
    fragment_id: str,
) -> tuple[str, str, str] | None:
    """OpenDART 주요계정 API 표 — 원 payload 하나가 모든 행의 근거다."""

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


def _audit_statement_excerpt(table: PerformanceTable) -> str | None:
    """감사보고서 표의 행 근거가 «하나의 원문 구간»인지 확인하고 그 글자를 준다.

    행마다 payload가 따로 있지만 그 안의 ``source_excerpt``는 같은 손익계산서
    구간이어야 한다. 행마다 다른 구간을 가리키면 한 문장이 여러 원문에 걸치게
    되므로 만들지 않는다.
    """

    if not table.evidence_rows or len(table.evidence_rows) != len(table.rows):
        _log_no_claim("감사보고서 표의 행 근거 개수가 공개 행과 다름")
        return None
    excerpts: list[str] = []
    for evidence in table.evidence_rows:
        try:
            payload = json.loads(str(evidence))
        except (json.JSONDecodeError, TypeError, ValueError):
            _log_no_claim("감사보고서 표의 행 근거를 JSON 으로 읽지 못함")
            return None
        if not isinstance(payload, Mapping):
            _log_no_claim("감사보고서 표의 행 근거가 payload 모양이 아님")
            return None
        excerpt = payload.get("source_excerpt")
        digest = str(payload.get("source_sha256") or "")
        if (
            payload.get("source") != AUDIT_STATEMENT_EVIDENCE_SOURCE
            or not isinstance(excerpt, str)
            or not excerpt
            or hashlib.sha256(excerpt.encode("utf-8")).hexdigest() != digest
            or not str(payload.get("source_location") or "").strip()
        ):
            _log_no_claim("감사보고서 행 근거의 원문 종류·지문·위치가 맞지 않음")
            return None
        excerpts.append(excerpt)
    if len(set(excerpts)) != 1:
        _log_no_claim(f"감사보고서 표의 원문 구간이 1개가 아님 ({len(set(excerpts))}개)")
        return None
    return excerpts[0]


def _raw_value_in_text(raw_cell: str, text: str) -> bool:
    """표의 원수치가 실제로 그 원문 글자 안에 있는가.

    감사보고서는 손실을 「영업손실 58,852,153,409」처럼 부호 없이 적고 표는
    ``-58,852,153,409``로 옮긴다. 그래서 부호를 뺀 숫자로 대조하고, 쉼표는
    있는 모양·없는 모양 둘 다 인정한다.
    """

    digits = str(raw_cell or "").strip().lstrip("+-")
    if not digits:
        return False
    plain = digits.replace(",", "")
    if not plain.replace(".", "", 1).isdigit():
        return False
    grouped = f"{Decimal(plain):,f}" if plain.isdigit() else digits
    return digits in text or plain in text or grouped in text


def _audit_report_source_context(
    table: PerformanceTable,
    fragment: CollectedFragment,
    filing_meta: FilingMeta | None,
) -> tuple[str, str, str] | None:
    """감사보고서 평문 표 — 인용 조각 그 자체가 원문 지문이다.

    ★ 왜 필요한가 (실측 결함 S5) — 비상장 회사는 주요계정 API 응답이 없어
      4장 표가 감사보고서 평문에서 만들어진다. 여기까지 못 오면 표에 숫자가
      다 있는데도 「3개년 주요 변화」 장에 변화 문장이 한 줄도 안 실린다.
    """

    excerpt = _audit_statement_excerpt(table)
    if excerpt is None:
        return None
    # 인용 조각과 표의 근거가 같은 원문이어야 한다. 조각은 저장될 때 앞뒤
    # 공백이 잘리므로 그만큼만 허용하고 나머지는 글자 그대로 본다.
    if excerpt.strip() != fragment.text.strip():
        _log_no_claim("감사보고서 표의 원문 구간이 인용 조각과 다름")
        return None
    for raw_row in table.raw_rows:
        for raw_cell in raw_row[1:]:
            if not _raw_value_in_text(raw_cell, fragment.text):
                _log_no_claim("표의 원수치가 인용 조각 원문에 없음")
                return None
    receipt_number = str(
        filing_meta.document_id if filing_meta is not None else ""
    ).strip()
    if (
        len(receipt_number) != DART_RECEIPT_NUMBER_LENGTH
        or not receipt_number.isdigit()
    ):
        _log_no_claim("감사보고서 조각의 공시 접수번호가 없음")
        return None
    # ★ 여기서 만드는 신원은 렌더러가 같은 조각으로 만드는 Source 신원과
    #   «글자까지» 같아야 한다(render._build_source의 공시 갈래). 다르면 문장은
    #   만들어지고 FactRecord만 조용히 사라진다.
    source_identity = document_identity_from_parts(
        document_id=receipt_number,
        host=DART_DOCUMENT_HOST,
        url=DART_DOCUMENT_URL_TEMPLATE.format(document_id=receipt_number),
    )
    if not source_identity:
        _log_no_claim("독립 문서 신원을 만들지 못함")
        return None
    return fragment.fragment_id, source_identity, fragment.text


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


#: claim_slot은 사실의 고유 번호가 아니라 원자 주장 범주다. 지표·기간을 끼워
#: 넣은 동적 문자열은 닫힌 9장 정책을 어기고, 검사기와 생성기의 계약을 서로
#: 다르게 만든다. 사실별 고유성은 ``_fact_id``가 맡는다. 필수 의미칸 정책은
#: 3개년 실적 구조화 검증기가 이 값을 주입한다고 약속하므로, 별도
#: ``cumulative_change``로 내면 표와 계산이 정상이어도 FULL 사전/최종 게이트가
#: 서로 다른 계약을 보게 된다.
PAST_CHANGES_CLAIM_SLOT = "past_changes:historical_performance"


@dataclass(frozen=True)
class _ClaimContext:
    """지표 하나의 claim을 만들 때 표 전체가 함께 쓰는 값."""

    fragment_id: str
    source_identity: str
    state_evidence: str
    entity_scope: EntityScope
    operand_dimension: UnitDimension
    raw_unit: str
    display_unit: str
    scale_divisor: Decimal | None
    scale_places: int
    period_start: str
    period_end: str


def _composed_claim(
    context: _ClaimContext,
    *,
    binding: NumericBinding,
    claim_text: str,
) -> ComposedSentence | None:
    """검산을 통과한 결속과 공개 문장을 하나의 구조화 문장으로 묶는다."""

    if not claim_text:
        _log_no_claim("공개 문장을 만들 이름표가 모자람")
        return None
    problems = numeric_binding_problems(binding)
    if problems:
        _log_no_claim(f"수치 결속 재검산 실패 ({len(problems)}건)")
        return None
    fact_id = _fact_id(
        source_identity=context.source_identity,
        claim_slot=PAST_CHANGES_CLAIM_SLOT,
        binding=binding,
        state_evidence=context.state_evidence,
    )
    projected = claim_fact_from_binding(
        fact_id=fact_id,
        section_owner=PAST_CHANGES_SECTION_ID,
        source_id=f"v2-frag-{context.fragment_id}",
        claim=claim_text,
        claim_slot=PAST_CHANGES_CLAIM_SLOT,
        binding=binding,
    )
    return ComposedSentence(
        text=claim_text,
        citations=(context.fragment_id,),
        grade=GRADE_CONFIRMED,
        planned_claim_slot=PAST_CHANGES_CLAIM_SLOT,
        verification_state=VerificationState.VERIFIED.value,
        structured_claim=StructuredClaim(
            fact_id=fact_id,
            claim_slot=PAST_CHANGES_CLAIM_SLOT,
            section_owner=PAST_CHANGES_SECTION_ID,
            source_fragment_id=context.fragment_id,
            source_identity=context.source_identity,
            verification_state=VerificationState.VERIFIED.value,
            state_evidence=context.state_evidence,
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


def _operand_pair(
    context: _ClaimContext,
    *,
    metric: str,
    start_value: Decimal,
    end_value: Decimal,
    unit: str,
    unit_dimension: UnitDimension,
) -> tuple[NumericOperand, NumericOperand]:
    """시작·종료 두 시점을 같은 이름표로 결속한다."""

    return (
        NumericOperand(
            role="start",
            metric=metric,
            entity_scope=context.entity_scope,
            period=context.period_start,
            value=format(start_value, "f"),
            sign=_sign(start_value),
            unit=unit,
            unit_dimension=unit_dimension,
            source_identity=context.source_identity,
        ),
        NumericOperand(
            role="end",
            metric=metric,
            entity_scope=context.entity_scope,
            period=context.period_end,
            value=format(end_value, "f"),
            sign=_sign(end_value),
            unit=unit,
            unit_dimension=unit_dimension,
            source_identity=context.source_identity,
        ),
    )


def _rate_claim(
    context: _ClaimContext,
    *,
    metric: str,
    start_value: Decimal,
    end_value: Decimal,
) -> ComposedSentence | None:
    """양수 구간의 누적 증감률 문장 하나."""

    # 입력 길이는 제한하지만 Decimal의 지수는 매우 클 수 있다. 한 지표가
    # 계산 불가능하다는 이유로 보고서 전체를 죽이지 않고, 그 claim만
    # 만들지 않아 품질 상태가 정직하게 PARTIAL/BLOCKED로 남게 한다.
    try:
        with localcontext() as decimal_context:
            decimal_context.prec = 160
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
        _log_no_claim("증감률을 Decimal 로 계산하지 못함")
        return None
    if display_decimal == 0:
        display_decimal = Decimal("0.00")
    display_value = f"{display_decimal:.2f}"
    binding = NumericBinding(
        version=NUMERIC_BINDING_VERSION,
        metric=metric,
        entity_scope=context.entity_scope,
        period_start=context.period_start,
        period_end=context.period_end,
        sign=_sign(calculated),
        unit="%",
        unit_dimension=UnitDimension.PERCENT,
        formula=NumericFormula.RATE,
        operands=_operand_pair(
            context,
            metric=metric,
            start_value=start_value,
            end_value=end_value,
            unit=context.raw_unit,
            unit_dimension=context.operand_dimension,
        ),
        calculated_value=f"{calculated_stored:.6f}",
        display_value=display_value,
        rounding_mode=ROUNDING_MODE,
        rounding_places=RATE_ROUNDING_PLACES,
        tolerance=RATE_TOLERANCE,
        source_identity=context.source_identity,
        verification_state=VerificationState.VERIFIED,
    )
    return _composed_claim(
        context,
        binding=binding,
        claim_text=_cumulative_rate_claim_text(
            entity_scope=context.entity_scope.value,
            metric=metric,
            period_start=context.period_start,
            period_end=context.period_end,
            display_value=display_value,
        ),
    )


def _signed_change_claim(
    context: _ClaimContext,
    *,
    metric: str,
    start_value: Decimal,
    end_value: Decimal,
    start_shown: str,
    end_shown: str,
) -> ComposedSentence | None:
    """적자·손실 구간의 변화 문장 하나 — 비율 대신 두 시점 값을 읽는다.

    결속의 피연산자는 «표에 인쇄된 표시 단위 값»이다. 문장이 읽는 숫자와 결속이
    재검산하는 숫자를 같게 두어야, 표시값만 바꿔치기한 손상을 검증기가 잡는다.
    표시값이 정말 원수치에서 나왔는지는 아래에서 같은 축척 함수로 다시 만든다.
    """

    if (
        not context.display_unit
        or context.scale_divisor is None
        or context.scale_divisor <= 0
        or context.scale_places < 0
    ):
        _log_no_claim("표의 표시 단위·축척 정보가 비어 부호 변화 문장을 못 만듦")
        return None
    start_display = _decimal_cell(start_shown)
    end_display = _decimal_cell(end_shown)
    if start_display is None or end_display is None:
        _log_no_claim("표의 표시값을 Decimal 로 읽지 못함")
        return None
    try:
        rebuilt = (
            format_display_value(
                start_value, context.scale_divisor, context.scale_places
            ),
            format_display_value(
                end_value, context.scale_divisor, context.scale_places
            ),
        )
    except (DecimalException, OverflowError, ValueError):
        _log_no_claim("표시값을 원수치에서 다시 만들지 못함")
        return None
    if rebuilt != (str(start_shown).strip(), str(end_shown).strip()):
        _log_no_claim("표의 표시값이 원수치·축척과 맞지 않음")
        return None
    delta = (end_display - start_display).quantize(
        quantum_for(context.scale_places), rounding=ROUND_HALF_UP
    )
    binding = NumericBinding(
        version=NUMERIC_BINDING_VERSION,
        metric=metric,
        entity_scope=context.entity_scope,
        period_start=context.period_start,
        period_end=context.period_end,
        sign=_sign(delta),
        unit=context.display_unit,
        unit_dimension=context.operand_dimension,
        formula=NumericFormula.SIGNED_CHANGE,
        operands=_operand_pair(
            context,
            metric=metric,
            start_value=start_display,
            end_value=end_display,
            unit=context.display_unit,
            unit_dimension=context.operand_dimension,
        ),
        calculated_value=f"{delta:.{context.scale_places}f}",
        display_value=f"{delta:.{context.scale_places}f}",
        rounding_mode=ROUNDING_MODE,
        rounding_places=context.scale_places,
        tolerance=SIGNED_CHANGE_TOLERANCE,
        source_identity=context.source_identity,
        verification_state=VerificationState.VERIFIED,
    )
    return _composed_claim(
        context,
        binding=binding,
        claim_text=_signed_change_claim_text(
            entity_scope=context.entity_scope.value,
            metric=metric,
            period_start=context.period_start,
            period_end=context.period_end,
            start_value=start_display,
            end_value=end_display,
            display_unit=context.display_unit,
            display_places=context.scale_places,
        ),
    )


def build_past_changes_numeric_claims(
    table: PerformanceTable | None,
    fragments: FragmentsInput,
    filing_meta: FilingMeta | None,
) -> tuple[ComposedSentence, ...]:
    """검증된 실적표에서 지표별 변화 claim을 결정론적으로 만든다.

    양수 구간은 누적 증감률로, 손실·적자 구간은 두 시점 값을 그대로 읽는
    문장으로 만든다. 어느 쪽도 산문에서 숫자를 되짚지 않는다.
    """

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
    start_year, start_display, start_raw = indexed_rows[0]
    end_year, end_display, end_raw = indexed_rows[-1]
    if start_year >= end_year:
        _log_no_claim("시작 연도가 끝 연도보다 뒤임")
        return ()

    if entity_scope not in (EntityScope.CONSOLIDATED, EntityScope.SEPARATE):
        _log_no_claim("표의 범위가 연결·별도가 아님")
        return ()

    context = _ClaimContext(
        fragment_id=fragment_id,
        source_identity=source_identity,
        state_evidence=state_evidence,
        entity_scope=entity_scope,
        operand_dimension=operand_dimension,
        raw_unit=table.raw_unit,
        display_unit=str(table.unit or "").strip(),
        scale_divisor=_decimal_cell(table.scale_divisor),
        scale_places=int(table.scale_places),
        period_start=str(start_year),
        period_end=str(end_year),
    )
    claims: list[ComposedSentence] = []
    for column, metric in enumerate(table.headers[1:], start=1):
        metric = metric.strip()
        start_value = _decimal_cell(start_raw[column])
        end_value = _decimal_cell(end_raw[column])
        if not metric or start_value is None or end_value is None:
            continue
        if start_value > 0 and end_value >= 0:
            claim = _rate_claim(
                context,
                metric=metric,
                start_value=start_value,
                end_value=end_value,
            )
        else:
            # 음수 기준 또는 0 교차를 일반 「증감률」로 VERIFIED 처리하면
            # -100→+100을 200% 성장이라고 부르는 의미 오류가 생긴다. 이 구간은
            # 비율을 쓰지 않고 표에 있는 두 값을 그대로 읽는다.
            claim = _signed_change_claim(
                context,
                metric=metric,
                start_value=start_value,
                end_value=end_value,
                start_shown=start_display[column],
                end_shown=end_display[column],
            )
        if claim is not None:
            claims.append(claim)
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
