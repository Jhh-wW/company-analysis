"""엔진 v2 오케스트레이션 (소단계 3-4b) — 쓰기→검증→요약→렌더→중복경고→출고검증.

★ 이 파일은 composer 조각들을 «정해진 순서로 잇기만» 한다:
    compose_sections → verify_report → compose_summary → (요약 재검증·보충)
    → render_report → (versioned 품질 shadow 판정) → (중복 검출 경고) → validate_v2
  각 단계의 규칙은 각 소유 파일(logic/verify/render/validate)에 있다.
★ AI 호출은 두 개의 주입 함수로만 한다 — 작가(writer_ask)와 검수(reviewer_ask)는
  «다른 클로저»여야 한다 (Generator/Evaluator 분리, rules/harness.md).
  provider 연결은 부르는 쪽(real.py)의 몫이다. 여기서 provider를 모른다.
★ 산문 정규식으로 값의 뜻을 추측하거나 FactRecord를 만들지 않는다. 다만 새
  공개 문장에 숫자가 있으면 구조화 의미 결속을 요구하고, 없으면 그 문장만 뺀다.
  마지막 validate_v2의 기존 3검사는 그대로다.
★ 중복 검출(`dup_detect.find_numeric_duplicates`)은 여기서 «경고 로그로만»
  붙인다 — `validate_v2` 안에는 넣지 않는다. `validate_v2`는 정본이 fail-closed로
  못 박은 3검사 전용 게이트이고, 그 안에 넣으면 나중에 누가 실수로 raise를
  보태기 쉽다(실제로 오늘 두 번 「검사 하나 늘렸다가 정상 보고서까지 막힌」
  사고가 났다 — `docs/실행계획_엔진v2/되돌린_작업분/`). 호출을 이 함수(오케스트레이션
  층)에서 분리해두면 dup_detect가 예외를 던지는 버그가 있어도 validate_v2의
  fail-closed 계약과 무관하게 남는다. 막을지는 실제 보고서로 오탐률을 더
  쌓은 뒤 사람이 정한다(`composer/dup_detect.py` 모듈 docstring 참고).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Optional

from src.shared.report_quality.generation import (
    GenerationQualityObservation,
    LEGACY_SHADOW_PUBLICATION_REASON,
    observe_generation,
)
from src.shared.report_quality.models import PublicationPolicy
from src.shared.report_quality.dto import (
    ClaimFact,
    ReportCandidate,
    ReportSectionCandidate,
    SourceDocument,
)
from src.shared.report_quality.fact_binding import fact_evidence_binding
from src.shared.report_quality.contract import contract_for_generation
from src.shared.report_quality.source_identity import (
    document_identity,
    document_identity_from_parts,
)
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
from src.features.composer.constants import DEFAULT_CITATION_STYLE, SECTION_TITLES
from src.features.composer.dedupe import drop_cross_section_duplicates
from src.features.composer.diagram_check import check_diagrams
from src.features.composer.dup_detect import CONFIDENCE_CONFIRMED, find_numeric_duplicates
from src.features.composer.port import (
    AskFatalError,
    ComposedReport,
    ComposedSentence,
    FilingMeta,
    PerformanceTable,
)
from src.features.composer.render import render_report
from src.features.composer.structured_claims import (
    NumericSafetyFiltering,
    append_past_changes_numeric_claims,
    enforce_public_numeric_safety,
)
from src.features.composer.validate import validate_v2
from src.features.composer.verify import verify_report, verify_sentences
from src.features.pipeline.port import Grade, Report
from src.features.provenance.sources import Source

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
    #: 새 생성 시점에만 실행한 versioned 품질·공개 안전 shadow 판정.
    #: 과거 GET에서는 다시 계산하지 않는다.
    quality_observation: GenerationQualityObservation | None = None


def _total_sentences(report: ComposedReport) -> int:
    """본문 전 장 + 요약의 문장 수를 센다."""
    return (
        sum(len(section.sentences) for section in report.sections)
        + len(report.summary)
    )


def _log_duplicate_findings(rendered: Report) -> None:
    """중복 검출 결과를 «경고 로그»로만 남긴다 — 출고를 막지 않는다.

    ★ 예외를 삼킨다 — `find_numeric_duplicates`는 스스로 예외를 던지지
      않는다고 문서화돼 있지만(dup_detect.py), 여기서 검출기 버그까지
      출고를 끊으면 이 배선의 목적(«막지 않는다»)이 깨진다. 원인은
      exception 로그로 남으므로 조용히 사라지지 않는다
      (diagram_check._safe_ask와 같은 이유로 broad except).
    """
    try:
        findings = find_numeric_duplicates(rendered)
    except Exception:  # noqa: BLE001 — 검출기 오류가 출고를 막으면 안 된다
        logger.exception("중복 검출 중 오류 — 경고를 건너뛰고 출고는 계속합니다")
        return
    if not findings:
        return
    confirmed = sum(1 for f in findings if f.confidence == CONFIDENCE_CONFIRMED)
    suspected = len(findings) - confirmed
    sections = sorted(
        {occ.section_label for finding in findings for occ in finding.occurrences}
    )
    logger.warning(
        "중복 검출(경고 전용 — 출고는 막지 않음): 확정 %d건 · 의심 %d건 · "
        "대상 장 [%s]",
        confirmed,
        suspected,
        ", ".join(sections),
    )


def _apply_generation_quality_label(
    rendered: Report,
    observation: GenerationQualityObservation,
    numeric_filtering: NumericSafetyFiltering,
) -> Report:
    """새 생성물에만 PARTIAL 꼬리표와 사람이 읽을 이유를 붙인다.

    생성 당시 계약 결과를 저장될 ``Report``에 한 번 반영한다. 과거 GET은 이
    함수를 호출하지 않으므로 새 코드가 이미 발급된 링크를 소급 차단하지 않는다.
    """

    reasons = list(rendered.shortfall_reasons)
    # ★ 2026-08-29 — 장마다 한 줄씩(최대 9줄) 거의 같은 문장을 찍던 것을 «한 줄»로
    #   모은다. 빼는 정보는 없다 — 총 개수와 장 이름을 그대로 싣는다.
    #   눈가림 독립 평가에서 「제외 사유 나열문이 완결성·서술품질을 깎는다」는
    #   지적을 받았고, 읽는 사람에게 필요한 것은 «무엇이 몇 개 빠졌나»지
    #   같은 문장을 아홉 번 읽는 것이 아니다.
    if numeric_filtering.removed_section_counts:
        removed_total = sum(n for _, n in numeric_filtering.removed_section_counts)
        removed_titles = ", ".join(
            SECTION_TITLES.get(section_id, section_id)
            for section_id, _ in numeric_filtering.removed_section_counts
        )
        reasons.append(
            f"검산할 구조화 근거가 없는 수치·날짜 문장 {removed_total}개를 "
            f"공개본에서 제외했습니다 ({removed_titles})."
        )
    if numeric_filtering.removed_summary_count:
        reasons.append(
            "핵심 요약에서 검증된 본문 수치 claim과 결속되지 않은 수치·날짜 "
            f"문장 {numeric_filtering.removed_summary_count}개를 제외했습니다."
        )

    contract = contract_for_generation(observation.contract_version)
    counts = dict(observation.section_public_sentence_counts)
    for section_id in observation.underfilled_sections:
        count = counts.get(section_id, 0)
        title = SECTION_TITLES.get(section_id, section_id)
        reasons.append(
            f"{title} 장의 공개 문장이 {count}개라 완성 기준 "
            f"{contract.min_claims_per_covered_section}개에 못 미칩니다."
        )

    if len(observation.notice_only_sections) > contract.max_notice_only_sections:
        titles = [
            SECTION_TITLES.get(section_id, section_id)
            for section_id in observation.notice_only_sections
        ]
        reasons.append(
            "실질 내용 대신 안내만 있는 장이 "
            f"{len(titles)}개({', '.join(titles)})라 완성 기준 "
            f"{contract.max_notice_only_sections}개를 넘습니다."
        )
    if observation.substantive_claims < contract.min_substantive_claims:
        reasons.append(
            "근거와 의미가 구조로 확인된 실질 내용이 "
            f"{observation.substantive_claims}개라 완성 기준 "
            f"{contract.min_substantive_claims}개에 못 미칩니다."
        )
    try:
        verified_ratio = Decimal(observation.verified_ratio)
    except (ArithmeticError, ValueError):
        verified_ratio = Decimal(0)
    if verified_ratio < contract.min_verified_ratio:
        reasons.append(
            "구조로 확인된 실질 내용 중 검증을 마친 비율이 "
            f"{verified_ratio:.0%}라 완성 기준 "
            f"{contract.min_verified_ratio:.0%}에 못 미칩니다."
        )
    if observation.document_sources < contract.min_document_sources:
        reasons.append(
            "서로 다른 원문 문서가 "
            f"{observation.document_sources}개라 완성 기준 "
            f"{contract.min_document_sources}개에 못 미칩니다."
        )

    unique_reasons = list(dict.fromkeys(reason for reason in reasons if reason))
    publication_policy = PublicationPolicy.STRUCTURED_SAFETY
    if not observation.release_allowed:
        publication_policy = PublicationPolicy.LEGACY_SHADOW_EXCEPTION
        if LEGACY_SHADOW_PUBLICATION_REASON not in unique_reasons:
            unique_reasons.append(LEGACY_SHADOW_PUBLICATION_REASON)
    grade = rendered.grade
    if (
        unique_reasons
        or observation.quality_grade != "완성"
        or observation.publication_grade != "완성"
    ) and grade is Grade.COMPLETE:
        grade = Grade.PARTIAL
    return replace(
        rendered,
        grade=grade,
        shortfall_reasons=unique_reasons,
        quality_contract_version=observation.contract_version,
        safety_decision=observation.safety_decision,
        publication_policy=publication_policy.value,
    )


def _supplement_safe_summary(
    summary: tuple[ComposedSentence, ...],
    report: ComposedReport,
) -> tuple[ComposedSentence, ...]:
    """수치 안전 경계 뒤 요약이 짧으면 안전한 본문으로 최소치만 채운다.

    먼저 기존 계약대로 «확인» 문장을 고른다. reviewer가 전역 실패하면 모든
    문장이 «해석»으로 강등돼 확인 문장이 0개일 수 있다. 그 경우에도 이미
    미결속 수치 문장을 제거한 본문에서 장을 번갈아 골라, 예전의 «강등하되
    보고서 전체는 막지 않는다» 안전선을 지킨다.
    """

    chosen = list(_supplement_summary(summary, report))
    if len(chosen) >= SUMMARY_MIN_SENTENCES:
        return tuple(chosen)
    seen = {" ".join(sentence.text.split()) for sentence in chosen}
    pools = [list(section.sentences) for section in report.sections]
    deepest = max((len(pool) for pool in pools), default=0)
    for round_index in range(deepest):
        for pool in pools:
            if len(chosen) >= SUMMARY_MIN_SENTENCES:
                return tuple(chosen)
            if round_index >= len(pool):
                continue
            candidate = pool[round_index]
            key = " ".join(candidate.text.split())
            if not key or key in seen:
                continue
            chosen.append(candidate)
            seen.add(key)
    return tuple(chosen)


def run_v2(
    company_name: str,
    fragments: FragmentsInput,
    performance_table: Optional[PerformanceTable],
    *,
    writer_ask: AskFn,
    reviewer_ask: AskFn,
    diagram_ask: Optional[AskFn] = None,
    corp_type: str = "",
    grade: Grade = Grade.PARTIAL,
    generated_at: str = "",
    as_of_date: str = "",
    analysis_period: str = "",
    latest_performance_period: str = "",
    table_presentation: str = "table",
    filing_meta: Optional[FilingMeta] = None,
    composition_tables: tuple[PerformanceTable, ...] = (),
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
        ⑤-a 품질·안전 판정 — 구조화된 누적 증감률은 원자 claim으로, 나머지는
           결속되지 않은 공개 내용으로 정직하게 측정한다. 전체 안전 판정은
           관측으로 남기되 장별 하한은 PARTIAL 표시로 반영한다.
        ⑤-b 중복 검출 경고 — 값+단위(+기간) 반복 후보를 로그로만 남긴다.
           출고를 막지 않는다(아직 오탐률을 사람이 확인 중).
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
        composition_tables: 2장에 실을 매출 구성표들(제품별·지역별 등). 주면
            표마다 «구성 도식»이 함께 나간다(도식 판정은
            report_standard/visualization.py 몫). 표는 여러 개일 수 있고
            2장에 «전부» 붙는다 — 첫 표만 쓰지 않는다(2026-08-25 설계 변경).
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

    # ②-c 도식 검증 — 관계 도식의 각 줄이 «근거에 맞는가».
    #     적대 검증에서 결함이 전부 관계 도식에서만 나왔다(수치 0 / 관계 7).
    #     ① 칸 안의 숫자가 원문에 있나(기계) ② 이 경로가 근거에 맞나(검수 AI 1회).
    #     ★ 검수기를 «반드시» 넘긴다. 안 넘기면 숫자 검사만 돌아 관계 결함이
    #       그대로 통과한다 — 이 단계를 만든 이유가 사라진다.
    #     ★ diagram_ask는 출력 상한이 훨씬 작은 «도식 전용» 클로저다.
    #       검수용(8000토큰)을 그대로 쓰면 예약만으로 예산의 21.7%를 먹어
    #       비싼 회사에서 보고서 «전체»가 예산 초과로 실패한다(실측).
    #     근거 없는 줄만 빼며, 줄이 다 빠지면 도식을 안 그릴 뿐 장은 남는다.
    verified, diagram_problems = check_diagrams(
        verified, _normalize_fragments(fragments), diagram_ask or reviewer_ask
    )

    for problem in diagram_problems:
        logger.warning("도식 검증에서 뺀 경로 — %s", problem)

    # ②-d 첫 구조화 claim 슬라이스 — 검증된 DART 3개년 표의 원값에서
    # 누적 증감률을 코드로 재계산한다. AI 산문에서 숫자를 역추출하지 않으며,
    # 표의 회계범위·원단위·원 payload가 하나라도 빠지면 아무것도 만들지 않는다.
    verified = append_past_changes_numeric_claims(
        verified,
        performance_table,
        fragments,
        filing_meta,
    )
    # ②-e 새 생성 수치 안전 경계. AI 산문에 숫자·날짜·백분율이 있으면
    # 의미가 결속된 StructuredClaim/NumericBinding 없이는 공개 후보에서 뺀다.
    # 산문을 역추출해 가짜 fact로 통과시키지 않는다. 프로그램이 만든 위 누적
    # 증감률은 동일한 versioned 결속을 재검산한 뒤 그대로 남는다.
    verified, body_numeric_filtering = enforce_public_numeric_safety(verified)
    # ★ 2026-08-29 — 이 삭제는 보고서 «안»에만 적히고 서버 로그엔 흔적이 없었다.
    #   실측: 현대카드에서 최소 16문장이 여기서 사라졌는데 로그로는 안 보였다.
    if body_numeric_filtering.removed_section_counts:
        logger.warning(
            "수치 안전 제외: 구조화 근거 없는 수치·날짜 문장 %d개 (장별 %s)",
            sum(n for _, n in body_numeric_filtering.removed_section_counts),
            ", ".join(
                f"{sid}:{n}" for sid, n in body_numeric_filtering.removed_section_counts
            ),
        )

    # ③ 핵심 요약 — «검증된» 본문을 재료로 새로 쓴다 (본문 재탕 금지)
    #
    # ★ 2026-08-29 실측 — 여기서 «호출 횟수 상한»을 만나면 완성된 9개 장이
    #   통째로 버려졌다. 요약은 새로 «쓰지» 못해도 «이미 검증된 본문 확인
    #   문장»으로 채울 길이 아래(_supplement_summary)에 이미 있고, 그 길은
    #   AI 를 한 번도 부르지 않는다. 그러니 본문을 버릴 이유가 없다.
    #   돈·계정 장애(call_limit=False)는 그대로 재전파한다.
    summary_call_limited = False
    try:
        with_summary = compose_summary(verified, writer_ask)
    except AskFatalError as error:
        if not getattr(error, "call_limit", False):
            raise
        summary_call_limited = True
        with_summary = verified
        logger.warning(
            "AI 호출 횟수 상한이라 핵심 요약을 «새로 쓰지» 못했다 — "
            "검증을 마친 본문 문장으로 채운다"
        )
    summary_draft_count = len(with_summary.summary)

    # ④ 요약 재검증 — 새로 쓴 문장이므로 본문과 같은 검증을 적용한다
    summary = with_summary.summary
    if summary and not summary_call_limited:
        try:
            summary = verify_sentences(
                summary, fragments, performance_table, reviewer_ask
            )
        except AskFatalError as error:
            # ★ 검증하지 못한 새 요약을 그대로 내보내지 않는다 — 버리고,
            #   «이미 검증을 마친» 본문 확인 문장으로 채운다(AI 호출 0회).
            #   돈·계정 장애는 그대로 재전파한다.
            if not getattr(error, "call_limit", False):
                raise
            summary = ()
            logger.warning(
                "AI 호출 횟수 상한이라 새 요약을 검증하지 못했다 — 검증하지 "
                "않은 요약을 내보내는 대신 본문 확인 문장으로 채운다"
            )
    if len(summary) < SUMMARY_MIN_SENTENCES:
        # 검증이 요약을 깎았으면 «이미 검증된» 본문 확인 문장으로 보충한다.
        # 보충분은 본문 검증(②)을 통과한 문장이라 추가 검수 호출이 필요 없다.
        summary = _supplement_summary(summary, verified)
    final = ComposedReport(
        sections=verified.sections,
        summary=tuple(summary)[:SUMMARY_MAX_SENTENCES],
    )
    # 요약은 새 AI 산문이라 본문과 별도로 같은 경계를 탄다. 미결속 수치를
    # 제거해 3문장보다 짧아지면 이미 안전 검사를 통과한 본문 확인 문장으로만
    # 보충한다. 그중 수치 문장은 본문 StructuredClaim을 그대로 상속한다.
    final, summary_numeric_filtering = enforce_public_numeric_safety(final)
    if len(final.summary) < SUMMARY_MIN_SENTENCES:
        final = ComposedReport(
            sections=final.sections,
            summary=_supplement_safe_summary(final.summary, verified),
        )
    numeric_filtering = body_numeric_filtering.merged(summary_numeric_filtering)

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
        composition_tables=composition_tables,
        citation_style=citation_style,
    )

    # ⑤-a 품질·공개 안전 shadow 판정 — 생성 시점에만 한 번 실행한다.
    # past_changes의 프로그램 생성 누적 증감률은 원자 fact_id·claim slot·원문
    # 결속을 갖춘 첫 수직 슬라이스다. 나머지 산문·요약·표·도식은 텍스트를
    # 정규식으로 쪼개 가짜 fact를 만들지 않고 «결속되지 않은 공개 내용»으로
    # 남긴다. 따라서 전체 안전 결과는 계속 미완성/차단이며, 숫자 문장 경계
    # 밖의 공개 구조는 결속과 영향 측정 전까지 전체 hard gate로 승격하지 않는다.
    quality_observation = observe_generation(
        _generation_quality_candidate(rendered, final)
    )
    if not quality_observation.release_allowed:
        logger.warning(
            "v2 생성 품질 판정(전체 안전은 관측 전용): 계약=%s · 품질=%s · 안전=%s",
            quality_observation.contract_version,
            quality_observation.quality_grade,
            quality_observation.safety_decision,
        )
    rendered = _apply_generation_quality_label(
        rendered,
        quality_observation,
        numeric_filtering,
    )

    # ⑤-b 중복 검출 경고 — «찾아서 로그만 남긴다», 출고는 막지 않는다.
    #     validate_v2 «안»에 넣지 않은 이유는 위 모듈 docstring 참고.
    _log_duplicate_findings(rendered)

    # ⑥ 출고 검증 — 실패하면 V2ValidationError (사유는 예외 problems에 전부)
    validate_v2(rendered)

    return V2RunOutput(
        report=rendered,
        composed_sentences=draft_body_count + summary_draft_count,
        verified_sentences=_total_sentences(final),
        quality_observation=quality_observation,
    )


def _generation_quality_candidate(
    rendered: Report,
    composed: ComposedReport,
) -> ReportCandidate:
    """렌더 결과를 텍스트 역추출 없이 공유 품질 DTO로 투영한다."""

    rendered_sections = {section.cell: section for section in rendered.sections}
    sections: list[ReportSectionCandidate] = []
    for section in composed.sections:
        rendered_section = rendered_sections.get(section.section_id)
        fact_ids = tuple(rendered_section.fact_ids) if rendered_section else ()
        bound_fact_ids = set(fact_ids)
        has_unbound_sentences = any(
            sentence.structured_claim is None
            or sentence.structured_claim.fact_id not in bound_fact_ids
            for sentence in section.sentences
        )
        # 표·도식은 아직 셀/행 단위 fact_id 결속이 없다. 섹션 장부에 사실이
        # 있다는 이유만으로 해당 공개 구조까지 검증됐다고 꾸미지 않는다.
        has_unbound_structures = bool(
            section.flow_rows
            or (rendered_section is not None and rendered_section.tables)
        )
        sections.append(
            ReportSectionCandidate(
                section_id=section.section_id,
                fact_ids=fact_ids,
                notice_only=not section.sentences and not has_unbound_structures,
                has_unbound_public_content=(
                    has_unbound_sentences or has_unbound_structures
                ),
                public_sentence_count=len(section.sentences),
            )
        )

    sources = tuple(
        SourceDocument(
            source_id=source.source_id,
            document_identity=document_identity(source),
        )
        for source in rendered.citations
        if isinstance(source, Source)
    )
    facts = tuple(
        ClaimFact(
            fact_id=fact.fact_id,
            section_owner=fact.section_owner,
            source_id=fact.source_id,
            source_identity=document_identity_from_parts(
                document_id=fact.source_document_id,
                host=fact.source_host,
                url=fact.source_url,
            ),
            verification_state=fact.verification_status or fact.status,
            claim_slot=fact.claim_slot,
            evidence_binding_valid=bool(fact.evidence_binding)
            and fact.evidence_binding == fact_evidence_binding(fact),
            claim=fact.claim,
            subject_scope=fact.subject_scope,
            raw_value=fact.raw_value,
            calculation=fact.calculation,
            display_value=fact.display_value,
            rounding_rule=fact.rounding_rule,
            numeric_checks=tuple(fact.numeric_checks),
            metric=fact.metric,
            period_start=fact.period_start,
            period_end=fact.period_end,
            sign=fact.sign,
            unit=fact.unit,
            unit_dimension=fact.unit_dimension,
            formula=fact.formula,
        )
        for fact in rendered.fact_records
    )
    rendered_fact_ids = {fact.fact_id for fact in rendered.fact_records}
    summary_fact_ids = tuple(
        sentence.structured_claim.fact_id
        for sentence in composed.summary
        if sentence.structured_claim is not None
        and sentence.structured_claim.fact_id in rendered_fact_ids
    )
    return ReportCandidate(
        sections=tuple(sections),
        facts=facts,
        sources=sources,
        summary_fact_ids=summary_fact_ids,
        has_unbound_summary_content=any(
            sentence.structured_claim is None
            or sentence.structured_claim.fact_id not in rendered_fact_ids
            for sentence in composed.summary
        ),
    )
