"""엔진 v2 오케스트레이션 (소단계 3-4b) — 쓰기→검증→요약→렌더→출고검증.

★ 이 파일은 composer 조각들을 «정해진 순서로 잇기만» 한다:
    compose_sections → verify_report → compose_summary → (요약 재검증·보충)
    → render_report → validate_v2
  각 단계의 규칙은 각 소유 파일(logic/verify/render/validate)에 있다.
★ AI 호출은 두 개의 주입 함수로만 한다 — 작가(writer_ask)와 검수(reviewer_ask)는
  «다른 클로저»여야 한다 (Generator/Evaluator 분리, rules/harness.md).
  provider 연결은 부르는 쪽(real.py)의 몫이다. 여기서 provider를 모른다.
★ 닫힌 정규식 게이트 없음 — 문장 내용을 거르는 검사를 하지 않는다.
  마지막 validate_v2(내부 키·인용-부록 1:1·요약 존재 3검사)만 fail-closed다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from src.features.composer.logic import (
    AskFn,
    FragmentsInput,
    _normalize_fragments,
    SUMMARY_MAX_SENTENCES,
    SUMMARY_MIN_SENTENCES,
    # 요약 보충 규칙(본문 «확인» 문장 재사용·서로 다른 장 우선)은 3-3이 정의한
    # 단일 구현을 그대로 쓴다 — 같은 feature 내부 재사용이라 별도 복제를 두지 않는다.
    _supplement_summary,
    compose_sections,
    compose_summary,
)
from src.features.composer.constants import DEFAULT_CITATION_STYLE
from src.features.composer.dedupe import drop_cross_section_duplicates
from src.features.composer.diagram_check import check_diagrams
from src.features.composer.port import ComposedReport, FilingMeta, PerformanceTable
from src.features.composer.render import render_report
from src.features.composer.validate import validate_v2
from src.features.composer.verify import verify_report, verify_sentences
from src.features.pipeline.port import Grade, Report

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class V2RunOutput:
    """run_v2의 결과 묶음 — 보고서 + 관측 지표용 문장 수."""

    #: 렌더·출고검증(validate_v2)까지 끝난 pipeline Report
    report: Report
    #: 작가가 만든 초안 문장 수 (본문 9장 + 요약 초안) — 「원문 일치율」 분모
    composed_sentences: int
    #: 검증을 통과해 최종 보고서에 실린 문장 수 (본문 + 요약) — 분자
    verified_sentences: int


def _total_sentences(report: ComposedReport) -> int:
    """본문 전 장 + 요약의 문장 수를 센다."""
    return (
        sum(len(section.sentences) for section in report.sections)
        + len(report.summary)
    )


def run_v2(
    company_name: str,
    fragments: FragmentsInput,
    performance_table: Optional[PerformanceTable],
    *,
    writer_ask: AskFn,
    reviewer_ask: AskFn,
    corp_type: str = "",
    grade: Grade = Grade.COMPLETE,
    generated_at: str = "",
    as_of_date: str = "",
    analysis_period: str = "",
    latest_performance_period: str = "",
    table_presentation: str = "table",
    filing_meta: Optional[FilingMeta] = None,
    composition_table: Optional[PerformanceTable] = None,
    citation_style: str = DEFAULT_CITATION_STYLE,
) -> V2RunOutput:
    """엔진 v2 전체 흐름을 한 번 돌려 최종 보고서를 만든다 (04장 3-4절).

    흐름:
        ① compose_sections — 작가 AI가 9개 장을 산문으로 쓴다 (장 삭제 없음).
        ② verify_report — 출처 실존·수치·의미 검수·라벨 정합을 «문장 단위»로.
        ③ compose_summary — 검증된 본문을 재료로 핵심 요약 3~5문장을 새로 쓴다.
        ④ 요약 재검증 — 새로 쓴 요약 문장에 같은 검증을 적용하고, 부족하면
           이미 검증된 본문 «확인» 문장으로 보충한다 (이때만 재사용 허용).
        ⑤ render_report — 웹·PDF 공용 pipeline Report로 변환.
        ⑥ validate_v2 — 내부 키·인용-부록 1:1·요약 존재 3검사 (fail-closed).

    Args:
        company_name: 분석 대상 법인 이름.
        fragments: 수집 조각 — real.py 원시 dict[int, dict]를 «그대로» 주는 것을
            권장한다 (홈페이지 조각의 문서일이 부록 날짜로 실린다).
        performance_table: 프로그램이 검증해 만든 3개년 실적표. 없으면 None.
        writer_ask: 작가 AI 호출 (프롬프트 문자열 → 응답 문자열).
        reviewer_ask: 검수·재작성 AI 호출 — 작가와 «별도 클로저»여야 한다.
        corp_type / grade / generated_at / as_of_date / analysis_period /
            latest_performance_period / table_presentation: 렌더 메타 —
            render_report에 그대로 전달된다.
        filing_meta: 내려받은 공시의 신원. 주면 부록 출처에 전자공시 원문
            주소가 실린다 (없으면 주소 없이 나간다).
        composition_table: 2장에 실을 매출 구성표. 주면 표와 «구성 도식»이
            함께 나간다 (도식 판정은 report_standard/visualization.py 몫).
        citation_style: 본문 인용 번호 표기 방식. 기본은 절충안이며,
            시험이 «문장마다 번호가 실리는가»를 볼 때 inline으로 고정한다.

    Returns:
        V2RunOutput — 검증 끝난 Report와 초안·생존 문장 수.

    Raises:
        V2ValidationError: 최종 출고 3검사에 걸린 경우 (예: 본문이 통째로 비어
            요약 3문장을 만들 수 없는 경우). 그 외 생성·검증 단계는 예외를
            밖으로 던지지 않는다 (장 삭제·전체 중단 금지 원칙).
    """
    # ① 본문 9장 작성 (작가)
    draft = compose_sections(company_name, fragments, performance_table, writer_ask)
    draft_body_count = _total_sentences(draft)  # 이 시점 summary는 빈 튜플이다

    # ② 본문 검증 (검수 — 문장 단위 제거/강등만, 장 삭제 없음)
    verified = verify_report(draft, fragments, performance_table, reviewer_ask)

    # ②-b 사실 단일 소유 강제 — 여러 장에 반복된 같은 사실을 소유 장 하나만
    #     남기고 뺀다. 요약 «앞»에 둔다 — 곧 사라질 문장을 요약 재료로 고르면
    #     본문에 없는 요약이 남는다.
    verified, moved_sentences = drop_cross_section_duplicates(verified)
    if moved_sentences:
        logger.info("장 간 중복 %d문장을 소유 장으로 모았습니다", moved_sentences)

    # ②-c 도식 검증 — 관계 도식의 각 줄이 «인용한 원문에 실제로 있는가».
    #     적대 검증에서 결함이 전부 관계 도식에서만 나왔다(수치 0 / 관계 7).
    #     근거 없는 줄만 빼며, 줄이 다 빠지면 도식을 안 그릴 뿐 장은 남는다.
    verified, diagram_problems = check_diagrams(
        verified, _normalize_fragments(fragments)
    )
    for problem in diagram_problems:
        logger.warning("도식 검증에서 뺀 경로 — %s", problem)

    # ③ 핵심 요약 — «검증된» 본문을 재료로 새로 쓴다 (본문 재탕 금지)
    with_summary = compose_summary(verified, writer_ask)
    summary_draft_count = len(with_summary.summary)

    # ④ 요약 재검증 — 새로 쓴 문장이므로 본문과 같은 검증을 적용한다
    summary = with_summary.summary
    if summary:
        summary = verify_sentences(
            summary, fragments, performance_table, reviewer_ask
        )
    if len(summary) < SUMMARY_MIN_SENTENCES:
        # 검증이 요약을 깎았으면 «이미 검증된» 본문 확인 문장으로 보충한다.
        # 보충분은 본문 검증(②)을 통과한 문장이라 추가 검수 호출이 필요 없다.
        summary = _supplement_summary(summary, verified)
    final = ComposedReport(
        sections=verified.sections,
        summary=tuple(summary)[:SUMMARY_MAX_SENTENCES],
    )

    # ⑤ 렌더 — 웹·PDF가 이미 소비하는 공용 구조로
    rendered = render_report(
        company_name,
        final,
        fragments,
        performance_table,
        corp_type=corp_type,
        grade=grade,
        generated_at=generated_at,
        as_of_date=as_of_date,
        analysis_period=analysis_period,
        latest_performance_period=latest_performance_period,
        table_presentation=table_presentation,
        filing_meta=filing_meta,
        composition_table=composition_table,
        citation_style=citation_style,
    )

    # ⑥ 출고 검증 — 실패하면 V2ValidationError (사유는 예외 problems에 전부)
    validate_v2(rendered)

    return V2RunOutput(
        report=rendered,
        composed_sentences=draft_body_count + summary_draft_count,
        verified_sentences=_total_sentences(final),
    )
