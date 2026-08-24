"""장별 정본 질문을 같은 Report 사실 원장에서 읽는 공개 표현 모델.

웹·PDF·Notion이 ``ReportSection.prose_lines``를 한 문단으로 합치면 사실은
남아 있어도 제품·역할·상태·한계 같은 장별 답이 독자에게 보이지 않는다.
이 모듈은 새 사실을 만들지 않고 이미 검증된 ``FactRecord``의 구조 필드를
장별 카드로 투영한다. 따라서 세 채널은 같은 블록·같은 ``fact_id``·같은
복수 출처를 사용한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import Iterable

from src.features.pipeline.port import FactRecord, Report, ReportSection
from src.features.pipeline.section567_contract import (
    PLAN_STATUS_LABELS,
    RELATIONSHIP_TYPE_LABELS,
    VALUE_CHAIN_STAGE_LABELS,
)
from src.features.provenance.sources import Source
from src.features.report_standard.constants import (
    COMPARISON_JUDGMENT_LABELS,
    RELATIONSHIP_KEY_FALLBACK_LABEL,
    RELATIONSHIP_KEY_LABELS,
)
from src.shared.comparison_candidate_basis import parse_comparison_basis


@dataclass(frozen=True)
class ContentField:
    """장별 카드의 한 항목."""

    label: str
    value: str


@dataclass(frozen=True)
class SectionContentBlock:
    """같은 사실 묶음을 세 출력 채널이 공통으로 표현하는 단위."""

    title: str
    fields: tuple[ContentField, ...]
    fact_ids: tuple[str, ...]
    source_numbers: tuple[int, ...]
    tone: str = ""


SUMMARY_TOPICS: dict[str, str] = {
    "identity": "기업정체",
    "business_model": "수익구조",
    "portfolio": "제품역할",
    "past_changes": "주요변화",
    "current_challenges": "현재과제",
    "future_strategy": "성장계획",
    "operations_partners": "운영구조",
    "culture": "일하는법",
    "competitive_position": "경쟁특성",
}


def summary_topic(section_id: str) -> str:
    """핵심요약 카드에 쓰는 2~6자 짧은 제목."""

    return SUMMARY_TOPICS.get(str(section_id or "").strip(), "핵심결론")


def _compact(value: object) -> str:
    return re.sub(r"\s+", "", str(value or "")).casefold()


def _clean(value: object, fallback: str = "") -> str:
    cleaned = " ".join(str(value or "").split())
    return cleaned or fallback


def _joined(values: Iterable[object], fallback: str = "") -> str:
    cleaned: list[str] = []
    for value in values:
        item = _clean(value)
        if item and item not in cleaned:
            cleaned.append(item)
    return " · ".join(cleaned) or fallback


def _field(label: str, value: object, fallback: str = "") -> ContentField:
    return ContentField(label=label, value=_clean(value, fallback))


#: 조립기 폴백이 남기는 영문 내부 키 모양. 정상 한국어 문장·캡션은 걸리지 않는다.
_INTERNAL_KEY_SHAPE = re.compile(r"^[a-z][a-z0-9_]*$")


def _relationship_display(value: object) -> str:
    """relationship_or_action에 폴백된 내부 키를 화면용 한국어로 바꾼다.

    조립기(canonical_report._fact_from_claim)는 이 필드를 채울 값이 없으면
    claim_type·section_id 내부 키를 그대로 남긴다. v1에서는 폴백 자체를
    지우면 사실 원장·해시 봉인이 연쇄로 깨지므로 렌더에서만 변환한다.
    빈 문자열을 새로 만들어 돌려주면 출고 게이트의 빈 항목 검사에 걸려
    보고서 전체가 차단되므로, 맵에 없는 내부 키도 기본 라벨로 바꾼다.
    """

    cleaned = _clean(value)
    label = RELATIONSHIP_KEY_LABELS.get(cleaned)
    if label is not None:
        return label
    if _INTERNAL_KEY_SHAPE.fullmatch(cleaned):
        return RELATIONSHIP_KEY_FALLBACK_LABEL
    return cleaned


def _source_number_map(report: Report) -> dict[str, int]:
    return {
        item.source_id: item.number
        for item in report.citations
        if isinstance(item, Source) and item.source_id and item.number > 0
    }


def _numbers(
    facts: Iterable[FactRecord], source_numbers: dict[str, int]
) -> tuple[int, ...]:
    out: list[int] = []
    for fact in facts:
        basis = parse_comparison_basis(fact.comparison_basis)
        candidate_source_id = str((basis or {}).get("candidate_source_id") or "")
        for source_id in (
            fact.source_id,
            fact.comparator_source_id,
            candidate_source_id,
        ):
            number = source_numbers.get(source_id)
            if number is not None and number not in out:
                out.append(number)
    return tuple(out)


def _visible_facts(report: Report, section: ReportSection) -> list[FactRecord]:
    """표 전용 행은 빼고 공개 prose가 소유한 사실만 되살린다."""

    prose = {_compact(text) for text, _cite in section.prose_lines if _clean(text)}
    candidates = [
        fact
        for fact in report.fact_records
        if fact.section_owner == section.cell and fact.fact_id in section.fact_ids
    ]
    visible = [fact for fact in candidates if _compact(fact.claim) in prose]
    # 옛 canonical 저장본 중 prose가 유실된 객체는 새 구조로 조용히 사실을
    # 늘리지 않는다. 현재 조립본에서는 claim과 prose가 정확히 결속돼 있다.
    return visible


def _identity_blocks(
    facts: list[FactRecord], source_numbers: dict[str, int]
) -> list[SectionContentBlock]:
    out: list[SectionContentBlock] = []
    for fact in facts:
        if fact.claim_type == "identity_summary":
            title = "회사 한눈에 보기"
            fields = (
                _field("정체성 요약", fact.claim),
                _field("근거 사업 범위", fact.subject_scope),
                _field("산업 내 역할", _relationship_display(fact.relationship_or_action)),
            )
        elif fact.claim_type == "official_self_definition":
            title = "회사의 공식 자기정의"
            fields = (
                _field("공식 자기정의", fact.claim),
                _field("적용 범위", fact.subject_scope),
                _field("산업 내 역할", _relationship_display(fact.relationship_or_action)),
            )
        elif fact.claim_type == "operating_scope":
            title = "산업에서 맡는 역할"
            fields = (
                _field("산업 내 역할", fact.claim),
                _field("사업 범위", fact.subject_scope),
                _field("확인 근거", _relationship_display(fact.relationship_or_action)),
            )
        else:
            continue
        out.append(
            SectionContentBlock(
                title=title,
                fields=fields,
                fact_ids=(fact.fact_id,),
                source_numbers=_numbers((fact,), source_numbers),
            )
        )
    return out


def _business_blocks(
    facts: list[FactRecord], source_numbers: dict[str, int]
) -> list[SectionContentBlock]:
    out: list[SectionContentBlock] = []
    for fact in facts:
        if fact.claim_type == "revenue_model":
            fields = (
                _field("수익 경로", fact.claim),
                _field("가치·거래 방식", _relationship_display(fact.relationship_or_action)),
                _field(
                    "가격·계약·반복 조건",
                    fact.limitations or fact.limitation,
                    "공식 근거에서 구체 조건을 확인하지 못함",
                ),
            )
            title = "수익이 만들어지는 경로"
        elif fact.claim_type == "customer_market":
            fields_list = [
                _field("고객·시장", fact.claim),
                _field("시장 관찰", fact.market_observation),
            ]
            if fact.market_stage:
                fields_list.append(_field("시장 단계", fact.market_stage))
            fields_list.append(
                _field(
                    "구매자·사용자·수혜자",
                    fact.limitations or fact.limitation,
                    "공식 근거에서 역할을 별도로 구분하지 않음",
                )
            )
            fields = tuple(fields_list)
            title = "고객과 시장"
        else:
            continue
        out.append(
            SectionContentBlock(
                title=title,
                fields=fields,
                fact_ids=(fact.fact_id,),
                source_numbers=_numbers((fact,), source_numbers),
            )
        )
    return out


def _portfolio_blocks(
    facts: list[FactRecord],
    source_numbers: dict[str, int],
    all_facts: dict[str, FactRecord],
) -> list[SectionContentBlock]:
    out: list[SectionContentBlock] = []
    for fact in facts:
        if fact.claim_type != "priority_product":
            continue
        revenue_fact = all_facts.get(fact.revenue_model_fact_id)
        role = fact.product_role
        if fact.portfolio_stage:
            role += f" · 보고서 선택 단계: {fact.portfolio_stage}"
        limitation = _clean(
            fact.limitations or fact.limitation,
            "공식 근거가 확인한 범위로 한정",
        )
        fields = (
            _field("제품·서비스 범위", fact.subject_scope),
            _field("사업적 역할", role),
            _field(
                "2장 수익 분류 참조",
                revenue_fact.subject_scope if revenue_fact is not None else "",
            ),
            _field(
                "중점 추진 근거·현재 확인·한계",
                f"신호: {_joined(fact.priority_signals)} · "
                f"확인: {fact.claim} · 한계: {limitation}",
            ),
        )
        out.append(
            SectionContentBlock(
                title=_clean(fact.subject_scope, "확인된 제품·서비스"),
                fields=fields,
                fact_ids=(fact.fact_id,),
                source_numbers=_numbers((fact,), source_numbers),
            )
        )
    return out


def _past_blocks(
    facts: list[FactRecord],
    source_numbers: dict[str, int],
    section_facts: dict[str, FactRecord],
) -> list[SectionContentBlock]:
    executions = [fact for fact in facts if fact.claim_type == "completed_execution"]
    interpretations = [
        fact for fact in facts if fact.claim_type == "change_interpretation"
    ]

    def basis_facts(interpretation: FactRecord) -> list[FactRecord]:
        """해석 근거를 선언 순서대로 한 번씩 실제 사실로 되살린다."""

        out: list[FactRecord] = []
        seen: set[str] = set()
        for fact_id in interpretation.basis_fact_ids:
            clean_id = str(fact_id or "").strip()
            basis = section_facts.get(clean_id)
            if not clean_id or clean_id in seen or basis is None:
                continue
            seen.add(clean_id)
            out.append(basis)
        return out

    def basis_label(basis: FactRecord) -> str:
        """prose와 표 숫자를 반복하지 않는 공개용 근거 표지를 만든다."""

        if basis.claim_type == "historical_performance":
            year = str(basis.fiscal_year or "").strip()
            return (
                f"{year} 완료 사업연도 실적표"
                if year
                else "완료 사업연도 실적표"
            )
        event = _clean(basis.event_date)
        scope = _clean(
            basis.subject_scope,
            _clean(basis.relationship_or_action, "확인된 주요 실행"),
        )
        return f"{event} {scope}" if event else scope

    out: list[SectionContentBlock] = []
    used: set[str] = set()
    for execution in executions:
        linked = [
            fact
            for fact in interpretations
            if [basis.fact_id for basis in basis_facts(fact)] == [execution.fact_id]
        ]
        used.update(fact.fact_id for fact in linked)
        grouped = [execution, *linked]
        out.append(
            SectionContentBlock(
                title=(
                    f"{execution.event_date} 주요 실행"
                    if execution.event_date
                    else "확인된 주요 실행"
                ),
                fields=(
                    _field("실행", execution.claim),
                    _field(
                        "확인된 결과·의미",
                        _joined((fact.claim for fact in linked)),
                        "공식 근거에서 결과를 별도로 확인하지 못함",
                    ),
                    _field(
                        "범위·한계",
                        _joined(
                            (
                                fact.limitations or fact.limitation
                                for fact in grouped
                            )
                        ),
                        "확인된 실행 범위로 한정",
                    ),
                ),
                fact_ids=tuple(fact.fact_id for fact in grouped),
                source_numbers=_numbers(grouped, source_numbers),
            )
        )
    for fact in interpretations:
        if fact.fact_id in used:
            continue
        bases = basis_facts(fact)
        out.append(
            SectionContentBlock(
                title="변화 해석",
                fields=(
                    _field("확인 내용", fact.claim),
                    _field(
                        "근거 사실",
                        _joined((basis_label(basis) for basis in bases)),
                        "결속된 근거 사실을 확인하지 못함",
                    ),
                    _field(
                        "범위·한계",
                        fact.limitations or fact.limitation,
                        "결속된 근거 사실 범위로 한정",
                    ),
                ),
                fact_ids=(fact.fact_id,),
                source_numbers=_numbers((*bases, fact), source_numbers),
            )
        )
    return out


def _current_blocks(
    facts: list[FactRecord], source_numbers: dict[str, int]
) -> list[SectionContentBlock]:
    issues = [fact for fact in facts if fact.claim_type == "current_issue"]
    responses = [fact for fact in facts if fact.claim_type == "current_response"]
    out: list[SectionContentBlock] = []
    for issue in issues:
        linked = [
            fact for fact in responses if fact.response_to_fact_id == issue.fact_id
        ]
        grouped = [issue, *linked]
        remaining = _joined(
            fact.limitations or fact.limitation for fact in grouped
        )
        initial_signals = _joined(
            fact.initial_signal for fact in linked if fact.initial_signal
        )
        signal_limit = (
            "동시 관찰·효과/인과 미확정"
            if initial_signals
            else "대응 진행 중·효과 미확인"
        )
        out.append(
            SectionContentBlock(
                title=_clean(issue.subject_scope, "현재 과제"),
                fields=(
                    _field("현재 과제·증거", issue.claim),
                    _field(
                        "진행 중 대응",
                        _joined(fact.response_action for fact in linked),
                        "공식 근거에서 착수한 대응을 확인하지 못함",
                    ),
                    _field(
                        "초기 신호·남은 문제",
                        f"초기 신호: {initial_signals or '대응 진행 중·효과 미확인'} · "
                        f"해석 한계: {signal_limit} · "
                        f"남은 문제: {remaining or '해결 결과는 아직 확인되지 않음'}",
                    ),
                    _field("다음 확인 지표", issue.next_check_metric),
                ),
                fact_ids=tuple(fact.fact_id for fact in grouped),
                source_numbers=_numbers(grouped, source_numbers),
            )
        )
    return out


def _future_blocks(
    facts: list[FactRecord], source_numbers: dict[str, int]
) -> list[SectionContentBlock]:
    return [
        SectionContentBlock(
            title=_clean(fact.subject_scope, "공식 성장 계획"),
            fields=(
                _field("공식 계획", fact.claim),
                _field(
                    "시점·조건·현재 상태",
                    f"시점: {_clean(fact.plan_timing, '일정 미공개')} · "
                    f"조건: {_clean(fact.plan_condition, '공식 조건 미공개')} · "
                    f"상태: {_clean(PLAN_STATUS_LABELS.get(fact.plan_status), '상태 미확인')}",
                ),
                _field(
                    "회사 제시 효과·한계",
                    f"효과: {_clean(fact.plan_expected_effect, '공식 효과 미공개')} · "
                    f"한계: {_clean(fact.limitations or fact.limitation, '미실행 계획')}",
                ),
                _field(
                    "실행 확인 신호",
                    fact.plan_execution_signal,
                ),
            ),
            fact_ids=(fact.fact_id,),
            source_numbers=_numbers((fact,), source_numbers),
        )
        for fact in facts
        if fact.claim_type == "future_plan"
    ]


def _operations_blocks(
    facts: list[FactRecord], source_numbers: dict[str, int]
) -> list[SectionContentBlock]:
    out: list[SectionContentBlock] = []
    for fact in facts:
        if fact.claim_type not in {"operating_core", "partner_role"}:
            continue
        out.append(
            SectionContentBlock(
                title=_clean(fact.subject_scope, "운영 구조"),
                fields=(
                    _field(
                        "가치사슬 단계",
                        VALUE_CHAIN_STAGE_LABELS.get(fact.value_chain_stage, ""),
                    ),
                    _field(
                        "관계 유형",
                        RELATIONSHIP_TYPE_LABELS.get(fact.relationship_type, ""),
                    ),
                    _field("확인된 역할", _relationship_display(fact.relationship_or_action)),
                    _field(
                        "운영 범위·한계",
                        "현재 상태: 공식 근거에서 현재 운영 확인 · "
                        f"한계: {_clean(fact.limitations or fact.limitation, '공식 근거가 확인한 현재 관계로 한정')}",
                    ),
                ),
                fact_ids=(fact.fact_id,),
                source_numbers=_numbers((fact,), source_numbers),
            )
        )
    return out


def _culture_blocks(
    facts: list[FactRecord], source_numbers: dict[str, int]
) -> list[SectionContentBlock]:
    return [
        SectionContentBlock(
            title=("공식 조직 사례" if fact.claim_type == "work_example" else "공식 가치"),
            fields=(
                _field("적용 범위", fact.subject_scope),
                _field("확인 내용", fact.claim),
                _field(
                    "범위·한계",
                    fact.limitations or fact.limitation,
                    "전사 공통 공식 기준",
                ),
            ),
            fact_ids=(fact.fact_id,),
            source_numbers=_numbers((fact,), source_numbers),
        )
        for fact in facts
    ]


def _comparison_reason(fact: FactRecord) -> str:
    conditions = fact.comparison_conditions
    product = _clean(conditions.get("product"), "같은 제품·서비스")
    market = _clean(conditions.get("market"), "같은 시장")
    return f"{product}·{market} 범위에서 양사 공식 근거와 동일 조건을 확인"


def _comparison_conditions(fact: FactRecord) -> str:
    conditions = fact.comparison_conditions
    values = (
        conditions.get("customer"),
        conditions.get("product"),
        conditions.get("market"),
        fact.comparison_period,
        fact.comparison_definition,
        fact.comparison_scope,
    )
    return _joined(values)


def _competitive_blocks(
    facts: list[FactRecord], source_numbers: dict[str, int]
) -> list[SectionContentBlock]:
    out: list[SectionContentBlock] = []
    for fact in facts:
        if fact.claim_type != "competitive_comparison":
            continue
        judgment = COMPARISON_JUDGMENT_LABELS.get(fact.comparison_judgment, "")
        out.append(
            SectionContentBlock(
                title=_clean(fact.comparison_target, "공식 비교"),
                fields=(
                    _field(
                        "비교군 선정 이유·동일 조건",
                        f"선정 이유: {_comparison_reason(fact)} · "
                        f"동일 조건: {_comparison_conditions(fact)}",
                    ),
                    _field("비교축", fact.comparison_metric),
                    _field("확인된 차이", fact.claim),
                    _field(
                        "판정·비교 한계",
                        f"{judgment} · "
                        f"{_clean(fact.limitations or fact.limitation, '공식 근거가 확인한 비교축으로 한정')}",
                    ),
                ),
                fact_ids=(fact.fact_id,),
                source_numbers=_numbers((fact,), source_numbers),
            )
        )
    return out


def section_content_blocks(
    report: Report, section: ReportSection
) -> tuple[SectionContentBlock, ...]:
    """한 장의 검증된 prose 사실을 장별 질문 순서로 투영한다."""

    facts = _visible_facts(report, section)
    source_numbers = _source_number_map(report)
    section_facts = {
        fact.fact_id: fact
        for fact in report.fact_records
        if fact.section_owner == section.cell and fact.fact_id in section.fact_ids
    }
    builders = {
        "identity": _identity_blocks,
        "business_model": _business_blocks,
        "portfolio": _portfolio_blocks,
        "current_challenges": _current_blocks,
        "future_strategy": _future_blocks,
        "operations_partners": _operations_blocks,
        "culture": _culture_blocks,
        "competitive_position": _competitive_blocks,
    }
    if section.cell == "past_changes":
        blocks = _past_blocks(facts, source_numbers, section_facts)
        if blocks:
            blocks[0] = replace(
                blocks[0],
                fields=(
                    _field("분석 범위", report.analysis_period),
                    *blocks[0].fields,
                ),
            )
        return tuple(blocks)
    if section.cell == "portfolio":
        all_facts = {fact.fact_id: fact for fact in report.fact_records}
        return tuple(_portfolio_blocks(facts, source_numbers, all_facts))
    builder = builders.get(section.cell)
    if builder is None:
        return ()
    return tuple(builder(facts, source_numbers))


#: 엔진 v2 문장 뒤에 붙는 «해석» 표지. composer.render.INTERPRETATION_MARKER의
#: 값(" — 해석")을 그대로 옮겨 적었다 — composer/render.py는 report_standard를
#: import하지 않는 방향으로 설계돼 있고(render.py 머리말 "report_standard・
#: publish는 import 하지 않는다"), 그 반대 방향으로 새 cross-feature import를
#: 만드는 대신 값만 미러링했다. render.py가 SECTION_TAGS를 report_standard
#: SECTION_SPECS에서 미러링하는 것과 같은 방식(render.py:87 주석 참고).
#: composer 쪽 값이 바뀌면 이 상수도 같이 바꿔야 한다.
_V2_INTERPRETATION_MARKER = " — 해석"

#: 본문 표시 문장에 박힌 인용 번호 ``[1]`` ``[12]`` 같은 것을 읽는다.
_CITATION_NUMBER_PATTERN = re.compile(r"\[(\d+)\]")


def source_verification_label(report: Report, source_id: str) -> str:
    """부록에서 자료 상태와 별도로 사실 검증 상태를 표시한다.

    ★ v1/v2 분기(2026-08-25, 실측 결함 수정) — v1은 사실을
      ``report.fact_records``(사실 카드)로 쪼개 카드마다 검증 상태를 붙이지만,
      엔진 v2는 카드를 만들지 않고 문장 뒤에 «확인/해석» 등급만 붙인다
      (``fact_records``가 v2 보고서에서는 항상 빈 리스트). 그래서 이 함수가
      카드만 셌을 때는 v2 보고서에서 무조건 「본문 사실 없음」이 나왔는데,
      같은 줄 「본문 사용 장」 칸은 실제로 인용된 장을 보여줘 한 줄 안에서
      모순된 표시가 났다(부록 9건 전부 재현). ``fact_records`` 유무로
      명시적으로 분기해 v1 동작은 손대지 않고, v2에서는 문장 단위로 다시 센다.
    """

    if report.fact_records:
        return _source_verification_label_v1(report, source_id)
    return _source_verification_label_v2(report, source_id)


def _source_verification_label_v1(report: Report, source_id: str) -> str:
    """v1(사실 카드) 경로 — 이 함수를 나누기 전과 동일한 판정.

    ★ v2에는 없는 개념 — 「후보 선정 근거」・「근거 불충분」은 v1의
      ``FactRecord.comparison_basis``(후보 비교 근거)・``insufficient``
      상태를 읽는데, 엔진 v2의 문장(``ComposedSentence``)·부록(``Source``)
      어디에도 이 두 개념이 없다(composer 패키지 전체에 candidate_source_id·
      comparison_basis·insufficient 문자열이 하나도 없음 — 확인함). v2가 아직
      후보 비교 리포트를 만들지 않으므로 v2 경로(아래)에는 이 두 라벨이
      없다. v1과 같은 개념이 v2에 생기면 그때 v2 쪽에도 추가해야 한다.
    """

    facts = []
    is_candidate_evidence = False
    for fact in report.fact_records:
        if source_id in {fact.source_id, fact.comparator_source_id}:
            facts.append(fact)
        basis = parse_comparison_basis(fact.comparison_basis)
        if str((basis or {}).get("candidate_source_id") or "") == source_id:
            is_candidate_evidence = True
    if not facts:
        if is_candidate_evidence:
            return "후보 선정 근거"
        return "본문 사실 없음"
    if all(
        fact.status == "verified" and fact.verification_status == "verified"
        for fact in facts
    ):
        return "사실 검증 완료"
    if any(
        fact.status == "insufficient" or fact.verification_status == "insufficient"
        for fact in facts
    ):
        return "근거 불충분"
    return "부분 검증"


def _source_verification_label_v2(report: Report, source_id: str) -> str:
    """엔진 v2(사실 카드 없음) 경로 — 문장 뒤 «확인/해석» 등급을 센다.

    v2는 fact_records 대신 부록 번호(``report.citations``의 ``Source.number``,
    ``_source_number_map``과 같은 방식으로 읽는다)와 본문 ``prose_lines``
    표시 문자열에 박힌 ``[번호]``로 «이 문장이 이 자료에서 왔다»를 되짚는다.
    render.py는 최종 화면 문자열만 ``pipeline.Report``로 넘기고 문장별
    원본 등급 객체(``ComposedSentence.grade``)는 그 뒤로 가져오지 않는다
    (render.py:612-629 확인) — 그 파일은 이 기능 담당이 아니라 고치지 않고,
    이미 있는 표시 문자열에서 되짚는 방식을 택했다.

    ★ 알려진 한계(확인함, 지어내지 않음) — render.py의 절충안 인용 규칙상
      «해석» 문장은 원래 ``[n]``을 안 보여준다(render.py:274-276). 그 자료가
      «해석» 문장에서만 인용되면 render.py의 ``_ensure_no_orphan_markers``가
      최소 한 곳에서는 번호를 되살려 반드시 보이게 만들어 주므로(부록이
      고아 번호를 만들면 출고 검증이 막는다 — render.py:363-388) 그 경우는
      이 함수가 잡는다. 다만 같은 번호가 «확인» 문장에서 이미 한 번 보이고
      있으면 되살릴 필요가 없어, 그 «해석» 문장의 번호는 계속 숨겨진 채로
      남는다 — 이때는 그 해석 인용을 텍스트에서 찾을 수 없어 「사실 검증
      완료」로 나올 수 있다(실제로는 「부분 검증」이 맞을 수 있다). 이 칸이
      「본문 사실 없음」처럼 명백히 틀린 표시를 내지는 않지만, «완료» 쪽으로
      쏠릴 수 있는 구조적 한계다. render.py가 문장별 등급을
      ``pipeline.Report``까지 들고 오지 않는 한 이 함수만으로는 못 고친다.
    """

    number = _source_number_map(report).get(source_id)
    if number is None:
        return "본문 사실 없음"

    found_any = False
    has_interpreted = False
    for section in report.sections:
        for text, _cite in section.prose_lines:
            numbers_in_line = {
                int(match) for match in _CITATION_NUMBER_PATTERN.findall(text)
            }
            if number not in numbers_in_line:
                continue
            found_any = True
            if text.endswith(_V2_INTERPRETATION_MARKER):
                has_interpreted = True
    if not found_any:
        return "본문 사실 없음"
    if has_interpreted:
        return "부분 검증"
    return "사실 검증 완료"
