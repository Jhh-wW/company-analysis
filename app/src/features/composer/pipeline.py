"""엔진 v2 오케스트레이션 (소단계 3-4b) — 쓰기→검증→요약→렌더→중복경고→출고검증.

★ 이 파일은 composer 조각들을 «정해진 순서로 잇기만» 한다:
    compose_sections → verify_report → compose_summary → (요약 재검증·보충)
    → render_report → (versioned 품질 shadow 판정) → (중복 검출 경고) → validate_v2
  각 단계의 규칙은 각 소유 파일(logic/verify/render/validate)에 있다.
★ AI 호출은 두 개의 주입 함수로만 한다 — 작가(writer_ask)와 검수(reviewer_ask)는
  «다른 클로저»여야 한다 (Generator/Evaluator 분리).
  provider 연결은 부르는 쪽(real.py)의 몫이다. 여기서 provider를 모른다.
★ 산문 정규식으로 값의 뜻을 추측하거나 FactRecord를 만들지 않는다. 다만 새
  공개 문장에 숫자가 있으면 구조화 의미 결속을 요구하고, 없으면 그 문장만 뺀다.
  마지막 validate_v2의 기존 3검사는 그대로다.
★ 중복 검출(`dup_detect.find_numeric_duplicates`)은 여기서 «경고 로그로만»
  붙인다 — `validate_v2` 안에는 넣지 않는다. `validate_v2`는 정본이 fail-closed로
  못 박은 3검사 전용 게이트이고, 그 안에 넣으면 나중에 누가 실수로 raise를
  보태기 쉽다(실제로 두 번 「검사 하나 늘렸다가 정상 보고서까지 막힌」
  사고가 나서 되돌린 적이 있다). 호출을 이 함수(오케스트레이션
  층)에서 분리해두면 dup_detect가 예외를 던지는 버그가 있어도 validate_v2의
  fail-closed 계약과 무관하게 남는다. 막을지는 실제 보고서로 오탐률을 더
  쌓은 뒤 사람이 정한다(`composer/dup_detect.py` 모듈 docstring 참고).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from decimal import Decimal
from typing import Optional

from src.shared.report_evidence.constants import ReleaseMode
from src.shared.final_gate_diagnostics import (
    FINAL_GATE_DETAIL_PREFLIGHT_OFFICIAL_EVIDENCE_INSUFFICIENT,
)
from src.shared.report_quality.constants import (
    LEGACY_STRICT_QUALITY_CONTRACT_VERSION,
    STRICT_QUALITY_CONTRACT_VERSION,
)
from src.shared.report_evidence.policy import required_slots_for
from src.shared.revenue_table_provenance import revenue_table_section_id_from_caption
from src.shared.report_quality.generation import (
    GenerationQualityObservation,
    LEGACY_SHADOW_PUBLICATION_REASON,
    assess_and_observe_generation,
    assert_observation_matches_assessment,
)
from src.shared.report_quality.models import PublicationPolicy
from src.shared.report_quality.contract import contract_for_generation
from src.features.composer.logic import (
    AskFn,
    FragmentsInput,
    SectionEvidencePackets,
    _assert_composed_report_evidence_invariant,
    _normalize_fragments,
    _prepare_section_evidence_packets,
    _sanitize_report_to_section_evidence,
    _validate_table_citations_for_section,
    SUMMARY_MAX_SENTENCES,
    SUMMARY_MIN_SENTENCES,
    # 요약 보충 규칙(본문 «확인» 문장 재사용·서로 다른 장 우선)은 3-3이 정의한
    # 단일 구현을 그대로 쓴다 — 같은 feature 내부 재사용이라 별도 복제를 두지 않는다.
    _supplement_summary,
    compose_selected_sections,
    compose_sections,
    compose_summary,
)
from src.features.composer.constants import (
    DEFAULT_CITATION_STYLE,
    SECTION_IDS,
    SECTION_TITLES,
)
from src.features.composer.dedupe import drop_cross_section_duplicates
from src.features.composer.diagram_check import check_diagram_numbers, check_diagrams
from src.features.composer.dup_detect import CONFIDENCE_CONFIRMED, find_numeric_duplicates
from src.features.composer.extractive_summary import select_extractive_summary
from src.features.composer.port import (
    AskFatalError,
    ComposedReport,
    ComposedSentence,
    FilingMeta,
    PerformanceTable,
    SectionEvidencePacketSet,
)
from src.shared.report_generation.models import (
    GenerationCallLedger,
    GenerationCallRecord,
    GenerationProducerEvidence,
    GenerationRunMetrics,
    canonical_sha256,
    exact_text_sha256,
    require_sha256,
)
from src.shared.generation_validation_receipt import (
    GenerationValidationReceipt,
    ValidationRound,
)
from src.shared.report_recovery import (
    RecoveryAction,
    SupplementAuthorization,
    decide_post_validation,
)
from src.shared.report_generation.public_projection import build_report_digest
from src.shared.report_generation.canonical import (
    assert_report_matches_generation_evidence,
    public_content_digests,
    report_verification_payload,
)
from src.features.composer.quality_projection import (
    build_generation_quality_candidate,
)
from src.features.composer.public_manifest import (
    PublicManifestError,
    PublicStructureSeal,
    assert_report_matches_public_structure,
    build_public_structure_seal,
)
from src.features.composer.render import (
    citation_numbers_for_fragments,
    render_report,
)
from src.features.composer.structured_claims import (
    NumericSafetyFiltering,
    append_past_changes_numeric_claims,
    build_past_changes_numeric_claims,
    enforce_public_numeric_safety,
)
from src.features.composer.validate import V2ValidationError, validate_v2
from src.features.composer.verify import verify_report, verify_sentences
from src.features.pipeline.port import Grade, Report
# ★ 경계 메모 — ``composer/render.py``·``port.py`` 머리말은 「composer는
#   report_standard를 import 하지 않는다」고 적어 두었고, 그래서 그 두 파일은
#   장 id·태그를 «복사»해 쓴다. 여기(pipeline.py)는 그 규칙의 예외다:
#   공개 봉인 projection은 정의상 report_standard의 표시 순수 함수(도식·띠·
#   검증 라벨)를 «한 번에 굳힌» 값이라, 값을 복사해 오면 두 벌이 갈라진다
#   — 갈라지지 않게 하려고 만든 것이 이 봉인이므로 복사는 목적 자체를
#   무너뜨린다. pipeline.py는 이미 ``features.pipeline.port``를 import하는
#   조립 층이며, report_standard는 composer를 import하지 않아 순환도 없다.
from src.features.report_standard.public_projection import build_public_projection

logger = logging.getLogger(__name__)


def _section_block_sha256s(rendered: Report) -> tuple[tuple[str, str], ...]:
    """이 회차 렌더 결과의 장별 공개 봉인 블록 지문(정본 아홉 장 순서).

    ★ 왜 영수증에 이 값을 싣나 — 보충 결속 검사가 쓰던
      ``public_structure_seal.section_sha256s``는 pre-render 공개 content
      봉인(지문 A)에서 오고 지문 A는 «보이는 것»만 덮는다. 그래서 보충 회차가
      비대상 장의 글자는 그대로 두고 FactRecord나 등급 기여만 바꾸면 그 검사를
      통과했다. ``block_sha256``은 display와 감사 장부를 함께 덮어 그 구멍을
      닫는다.

    ★ 여기서 만드는 projection은 «지문 계산용»이고 보고서에 싣지 않는다.
      보고서에 실리는 봉인은 최종 등급까지 확정된 뒤 한 번만 만든다 — header가
      달라도 장 블록은 같은 값이라 두 값이 어긋나지 않는다(그 동치는
      ``test_영수증의_장별_블록_지문은_저장된_봉인과_같다``가 지킨다).
    """

    projection = build_public_projection(rendered)
    return tuple(
        (block.display.cell, block.block_sha256) for block in projection.sections
    )


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
    #: FULL 성공 후보에만 존재하는 비권위 생산 증거. 공개·차감 결정은 없다.
    generation_evidence: GenerationProducerEvidence | None = None
    #: cache/storage가 0으로 꾸미지 않고 다시 운반할 원 실행 계측.
    generation_metrics: GenerationRunMetrics | None = None
    #: 도식 검증이 «뺀 줄»의 사유 목록 (원문 없음, 운영 진단용).
    #:
    #: ★ 왜 결과에 싣나 (2026-09-05 하이브 3장 카드 0건) — 이 사유는 지금까지
    #:   서버 로그에만 남았다. 로컬 DB에 그 실행이 없어 「작가가 안 냈다」와
    #:   「우리가 걸렀다」를 가를 방법이 아예 없었다. 결과에 실어야 다음
    #:   진단을 저장된 실행 기록만으로 할 수 있다.
    #: ★ 기본값이 빈 tuple이라 이 필드를 모르는 기존 호출·저장 경로는
    #:   그대로 돈다(새 키 추가만, 읽기 호환 유지).
    diagram_drop_reasons: tuple[str, ...] = ()


class _CallLedgerRecorder:
    """FULL의 실제 writer/reviewer 호출을 원문 없이 순서대로 기록한다."""

    def __init__(self) -> None:
        self._records: list[GenerationCallRecord] = []
        self._role_counts: dict[tuple[ValidationRound, str], int] = {}

    def wrap(
        self,
        ask: AskFn,
        *,
        role: str,
        validation_round: ValidationRound,
        section_ids: tuple[str, ...],
    ) -> AskFn:
        if type(validation_round) is not ValidationRound:
            raise TypeError("AI 호출 장부에는 닫힌 validation round가 필요합니다")
        if type(section_ids) is not tuple or not section_ids or any(
            type(section_id) is not str or not section_id.strip()
            for section_id in section_ids
        ):
            raise ValueError("AI 호출 장부에는 명시적 소유 장 tuple이 필요합니다")

        def tracked(prompt: str) -> str:
            key = (validation_round, role)
            role_index = self._role_counts.get(key, 0) + 1
            if role_index > len(section_ids):
                raise RuntimeError(
                    "승인한 validation round·role의 AI 호출 수를 넘었습니다"
                )
            self._role_counts[key] = role_index
            section_id = section_ids[role_index - 1]
            sequence = len(self._records) + 1
            try:
                response = ask(prompt)
            except Exception as error:
                self._records.append(
                    GenerationCallRecord(
                        sequence=sequence,
                        role=role,
                        role_index=role_index,
                        section_id=section_id,
                        prompt_sha256=exact_text_sha256(prompt),
                        response_sha256="",
                        outcome="failed",
                        validation_round=validation_round,
                        error_kind=type(error).__name__,
                    )
                )
                raise
            text = str(response)
            self._records.append(
                GenerationCallRecord(
                    sequence=sequence,
                    role=role,
                    role_index=role_index,
                    section_id=section_id,
                    prompt_sha256=exact_text_sha256(prompt),
                    response_sha256=exact_text_sha256(text),
                    outcome="returned",
                    validation_round=validation_round,
                )
            )
            return text

        return tracked

    def freeze(self) -> GenerationCallLedger:
        return GenerationCallLedger(tuple(self._records))

    def calls_for(self, validation_round: ValidationRound, *, role: str) -> int:
        return sum(
            record.validation_round is validation_round and record.role == role
            for record in self._records
        )


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

    # ★ 아래 문구들은 «독자»가 읽는다. 눈가림 독립 평가에서
    #   세 평가자가 모두 「내부 문구 노출」을 감점 1위로 지목했다:
    #   「claim」·「결속」 같은 개발자 어휘, 「완성 기준 40개」 같은 내부 임계값,
    #   「새 안전 검사」 같은 우리 일정 사정이 그대로 인쇄되고 있었다.
    #   (내부 임계값 40은 화면 어디에도 설명이 없어 「40점 만점에 3점」으로 오독된다.)
    # ⚠️ 정직성은 «깎지 않는다» — 개수·장 이름·비율은 전부 그대로 남긴다.
    #   숨기는 것과 쉬운 말로 바꾸는 것은 다르다. 시험이 그 경계를 지킨다.
    reasons = list(rendered.shortfall_reasons)
    # ★ 장마다 한 줄씩(최대 9줄) 거의 같은 문장을 찍던 것을 «한 줄»로
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
            f"원문과 맞춰 보지 못한 숫자·날짜 문장 {removed_total}개를 뺐습니다 "
            f"({removed_titles}). 틀렸다는 뜻이 아니라 확인하지 못했다는 뜻입니다."
        )
    if numeric_filtering.removed_summary_count:
        reasons.append(
            "핵심 요약에서도 같은 이유로 숫자 문장 "
            f"{numeric_filtering.removed_summary_count}개를 뺐습니다."
        )

    contract = contract_for_generation(observation.contract_version)
    counts = dict(observation.section_public_sentence_counts)
    for section_id in observation.underfilled_sections:
        count = counts.get(section_id, 0)
        title = SECTION_TITLES.get(section_id, section_id)
        reasons.append(
            f"{title} 장은 확인된 문장이 {count}개뿐이라 내용이 얇습니다."
        )

    if len(observation.notice_only_sections) > contract.max_notice_only_sections:
        titles = [
            SECTION_TITLES.get(section_id, section_id)
            for section_id in observation.notice_only_sections
        ]
        reasons.append(
            f"내용 대신 안내문만 실린 장이 {len(titles)}개입니다 "
            f"({', '.join(titles)})."
        )
    if observation.substantive_claims < contract.min_substantive_claims:
        reasons.append(
            f"출처와 뜻이 함께 확인된 사실이 {observation.substantive_claims}건뿐이라, "
            "회사 전체를 설명하기에는 얇습니다."
        )
    try:
        verified_ratio = Decimal(observation.verified_ratio)
    except (ArithmeticError, ValueError):
        verified_ratio = Decimal(0)
    if verified_ratio < contract.min_verified_ratio:
        reasons.append(
            f"확인된 사실 중 검증을 마친 것이 {verified_ratio:.0%}뿐입니다."
        )
    if observation.document_sources < contract.min_document_sources:
        reasons.append(
            f"이 보고서가 참고한 원문 문서는 {observation.document_sources}개입니다. "
            "자료가 적으니 다른 자료와 함께 보시길 권합니다."
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


def _legacy_summary_stage(
    verified: ComposedReport,
    fragments: FragmentsInput,
    performance_table: Optional[PerformanceTable],
    *,
    writer_ask: AskFn,
    reviewer_ask: AskFn,
    body_numeric_filtering: NumericSafetyFiltering,
) -> tuple[ComposedReport, int, NumericSafetyFiltering]:
    """기존 SHADOW 요약 경로를 글자·호출 순서까지 그대로 보존한다."""

    # 호출 «횟수» 상한과 요청 로컬 «예약액» 소진을 함께 뜻한다 — 둘 다
    # «이 요청 몫을 다 썼다»일 뿐 돈·계정 장애가 아니라서 처리가 같다.
    summary_ask_limited = False
    try:
        with_summary = compose_summary(verified, writer_ask)
    except AskFatalError as error:
        if not getattr(error, "degradable", False):
            raise
        summary_ask_limited = True
        with_summary = verified
        logger.warning(
            "요청 AI 한도에 닿아 핵심 요약을 «새로 쓰지» 못했다 — "
            "검증을 마친 본문 문장으로 채운다"
        )
    summary_draft_count = len(with_summary.summary)

    summary = with_summary.summary
    if summary and not summary_ask_limited:
        try:
            summary = verify_sentences(
                summary, fragments, performance_table, reviewer_ask
            )
        except AskFatalError as error:
            if not getattr(error, "degradable", False):
                raise
            summary = ()
            logger.warning(
                "요청 AI 한도에 닿아 새 요약을 검증하지 못했다 — 검증하지 "
                "않은 요약을 내보내는 대신 본문 확인 문장으로 채운다"
            )
    if len(summary) < SUMMARY_MIN_SENTENCES:
        summary = _supplement_summary(summary, verified)
    final = ComposedReport(
        sections=verified.sections,
        summary=tuple(summary)[:SUMMARY_MAX_SENTENCES],
    )
    final, summary_numeric_filtering = enforce_public_numeric_safety(final)
    if len(final.summary) < SUMMARY_MIN_SENTENCES:
        final = ComposedReport(
            sections=final.sections,
            summary=_supplement_safe_summary(final.summary, verified),
        )
    return (
        final,
        summary_draft_count,
        body_numeric_filtering.merged(summary_numeric_filtering),
    )


def _merge_selected_sections(
    base: ComposedReport,
    replacements: ComposedReport,
    section_ids: tuple[str, ...],
) -> ComposedReport:
    """정책 순서의 승인 장만 교체하고 나머지 객체를 그대로 보존한다."""

    if tuple(section.section_id for section in base.sections) != SECTION_IDS:
        raise V2ValidationError(("report_recovery:base_section_order_invalid",))
    if tuple(section.section_id for section in replacements.sections) != section_ids:
        raise V2ValidationError(("report_recovery:replacement_section_order_invalid",))
    by_id = {section.section_id: section for section in replacements.sections}
    return ComposedReport(
        sections=tuple(
            by_id.get(section.section_id, section) for section in base.sections
        ),
        summary=(),
    )


def _generation_fragment_counts(
    fragments: FragmentsInput,
    rendered: Report,
) -> tuple[int, int]:
    """수집 조각과 그중 실제 공개 인용된 조각을 같은 번호 정본으로 센다.

    ``rendered.citations``는 사용자 인용뿐 아니라 공식 웹 소유권을 증명하는
    ``attestation_only`` Source와 프로그램 등록부 보조 Source도 보존한다.
    따라서 그 배열 길이는 「인용 조각 수」가 아니다. 렌더러가 실제 조각에
    부여한 공개 번호와 출처 등록부 번호의 교집합만 세면 프로그램 비교 근거도
    분모·분자에 들어가고, 조각 없는 증명 Source는 정확히 빠진다.
    """

    normalized = _normalize_fragments(fragments)
    collected_numbers = frozenset(
        citation_numbers_for_fragments(normalized).values()
    )
    cited_numbers = {
        number
        for source in rendered.citations
        if type(number := getattr(source, "number", None)) is int and number > 0
    }
    return len(normalized), len(collected_numbers & cited_numbers)


def _append_verified_program_sentences(
    report: ComposedReport,
    prepared_evidence: object | None,
) -> ComposedReport:
    """packet에 봉인된 프로그램 문장을 AI 검수 뒤 정확히 한 번 붙인다."""

    by_section = getattr(prepared_evidence, "program_evidence_by_section", {})
    if not by_section:
        return report
    program_ids = {
        sentence.verified_fact_id
        for evidence in by_section.values()
        for sentence in evidence.sentences
    }
    return replace(
        report,
        sections=tuple(
            replace(
                section,
                sentences=tuple(
                    sentence
                    for sentence in section.sentences
                    if sentence.verified_fact_id not in program_ids
                )
                + tuple(
                    by_section[section.section_id].sentences
                    if section.section_id in by_section
                    else ()
                ),
            )
            for section in report.sections
        ),
    )


def _raise_recovery_stop(
    reason_code: str, quality_problem_codes: tuple[str, ...] = ()
) -> None:
    """사람 원문 없이 닫힌 회복 사유만 운영 경계로 보낸다.

    ``quality_problem_codes``는 회복 정책이 «품질» 때문에 닫았을 때만 실린다.
    최종 게이트는 이 코드를 보고 «보고서 품질 최소 기준 미달»과 «출고 전 자동
    검증 거절»을 구분해 사용자에게 말한다 — 뭉뚱그리면 재시도해야 할 일과
    포기해야 할 일이 뒤바뀐다. 무엇이 품질 사유인지는 shared의 회복 정책이
    단일 권위로 정한다(``QUALITY_DERIVED_STOP_REASON_CODES``).
    """

    raise V2ValidationError(
        (f"report_recovery:{reason_code}",),
        problem_codes=quality_problem_codes,
    )


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
    release_mode: ReleaseMode = ReleaseMode.SHADOW,
    section_evidence_packets: Optional[SectionEvidencePackets] = None,
    company_id: str = "",
    build_identity_sha256: str = "",
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
        composition_tables: 제품 축은 3장, 지역 축은 2장에 실을 매출 구성표들.
            표마다 «구성 도식»이 함께 나간다(도식 판정은
            report_standard/visualization.py 몫).
        citation_style: 본문 인용 번호 표기 방식. 기본은 절충안이며,
            시험이 «문장마다 번호가 실리는가»를 볼 때 inline으로 고정한다.
        release_mode: SHADOW는 기존 생성·요약·공개 동작을 그대로 쓴다.
            그 밖의 엄격 모드는 검증된 본문 사실을 글자 그대로 골라 요약하고,
            엄격 품질 계약을 통과하지 못하면 결과를 반환하지 않는다.

    Returns:
        V2RunOutput — 검증 끝난 Report와 초안·생존 문장 수.

    Raises:
        V2ValidationError: 최종 출고 3검사에 걸린 경우 (예: 본문이 통째로 비어
            요약 3문장을 만들 수 없는 경우). 그 외 생성·검증 단계는 예외를
            밖으로 던지지 않는다 (장 삭제·전체 중단 금지 원칙).
    """
    if not isinstance(release_mode, ReleaseMode):
        raise TypeError("release_mode는 ReleaseMode 값이어야 합니다")

    call_recorder: _CallLedgerRecorder | None = None
    writer_for_run = writer_ask
    reviewer_for_run = reviewer_ask
    normalized_build_identity_sha256 = ""
    if release_mode is ReleaseMode.FULL:
        # 성공/실패 어느 쪽이든 첫 유료 호출 전에 typed 9장·회사·evidence
        # generation·build identity를 닫는다. raw Mapping은 같은 내용을 가졌어도
        # FULL 권위 입력이 아니다.
        if type(section_evidence_packets) is not SectionEvidencePacketSet:
            raise V2ValidationError(
                ("FULL 생성에는 gen8 회사에 결속된 typed 아홉 장 packet이 필요합니다",)
            )
        if (
            type(company_id) is not str
            or company_id != company_id.strip()
            or company_id != section_evidence_packets.company_id
        ):
            raise V2ValidationError(
                ("FULL 생성 대상 회사와 section packet company_id가 다릅니다",)
            )
        try:
            normalized_build_identity_sha256 = require_sha256(
                build_identity_sha256,
                label="FULL 생성 build identity",
            )
        except ValueError as error:
            raise V2ValidationError((str(error),)) from error
        call_recorder = _CallLedgerRecorder()
        writer_for_run = call_recorder.wrap(
            writer_ask,
            role="writer",
            validation_round=ValidationRound.PRIMARY,
            section_ids=SECTION_IDS,
        )
        reviewer_for_run = call_recorder.wrap(
            reviewer_ask,
            role="reviewer",
            validation_round=ValidationRound.PRIMARY,
            section_ids=("bundled",),
        )

    # packet 계약은 첫 유료 호출 전에 닫는다. 작성에는 장별 packet만,
    # 검증·부록에는 충돌 검사를 마친 결정론적 union만 전달한다.
    prepared_evidence = None
    packet_union_ids: frozenset[str] = frozenset()
    verification_fragments: FragmentsInput = fragments
    if section_evidence_packets is not None:
        try:
            prepared_evidence = _prepare_section_evidence_packets(
                section_evidence_packets
            )
        except (TypeError, ValueError) as error:
            if release_mode is ReleaseMode.FULL:
                raise V2ValidationError(
                    ("report_recovery:preflight_packet_invalid",)
                ) from error
            raise
        verification_fragments = prepared_evidence.flat_union
        packet_union_ids = frozenset(
            fragment.fragment_id for fragment in prepared_evidence.flat_union
        )
        try:
            _validate_table_citations_for_section(
                (performance_table,) if performance_table is not None else (),
                section_id="past_changes",
                allowed_fragment_ids=(
                    prepared_evidence.allowed_fragment_ids_by_section[
                        "past_changes"
                    ]
                ),
                table_label="실적",
                require_cite=True,
            )
            composition_tables_by_section: dict[str, list[PerformanceTable]] = {}
            for table in composition_tables:
                section_id = revenue_table_section_id_from_caption(table.caption)
                composition_tables_by_section.setdefault(section_id, []).append(table)
            for section_id, section_tables in composition_tables_by_section.items():
                _validate_table_citations_for_section(
                    tuple(section_tables),
                    section_id=section_id,
                    allowed_fragment_ids=(
                        prepared_evidence.allowed_fragment_ids_by_section[section_id]
                    ),
                    table_label="구성",
                    require_cite=True,
                )
        except (TypeError, ValueError) as error:
            if release_mode is ReleaseMode.FULL:
                raise V2ValidationError(
                    ("report_recovery:preflight_table_evidence_invalid",)
                ) from error
            raise

        if release_mode is ReleaseMode.FULL:
            # 작가가 쓸 수 있는 의미 칸과 프로그램이 실제 원자료에서 만들 수
            # 있는 구조화 claim을 AI 호출 전에 합친다. 장별 서로 다른 의미 칸
            # 하한에 애초에 도달할 수 없다면 보충 작가를 불러도 결과는 같으므로
            # 유료 9장+재작성 뒤 실패시키지 않는다.
            reachable_slots_by_section = {
                section_id: {
                    slot_id
                    for fragment in prepared_evidence.packets[section_id]
                    for slot_id in fragment.supported_claim_slots
                    if slot_id.startswith(f"{section_id}:")
                }
                for section_id in SECTION_IDS
            }
            precomputed_claims = build_past_changes_numeric_claims(
                performance_table,
                verification_fragments,
                filing_meta,
            )
            for claim in precomputed_claims:
                if claim.structured_claim is None:
                    continue
                reachable_slots_by_section[
                    claim.structured_claim.section_owner
                ].add(claim.structured_claim.claim_slot)
            # 비교 슬롯은 정책에 ``injected``라고 적혀 있다는 이유로 열지
            # 않는다. 실제 공식 양사 생산기가 FactRecord를 만든 경우만 도달
            # 가능한 슬롯으로 센다.
            for fact in prepared_evidence.program_facts:
                section_owner = str(getattr(fact, "section_owner", "") or "")
                claim_slot = str(getattr(fact, "claim_slot", "") or "")
                if section_owner in reachable_slots_by_section and claim_slot:
                    reachable_slots_by_section[section_owner].add(claim_slot)
            unreachable_slots_by_section = {
                section_id: tuple(
                    slot_id
                    for slot_id in required_slots_for(section_id)
                    if slot_id not in reachable_slots_by_section[section_id]
                )
                for section_id in SECTION_IDS
            }
            unreachable_slots_by_section = {
                section_id: slot_ids
                for section_id, slot_ids in unreachable_slots_by_section.items()
                if slot_ids
            }
            if unreachable_slots_by_section:
                # 외부 사용자에게는 닫힌 사유 코드만 보내되, 운영 로그에는
                # 정확히 어느 정책 칸이 비었는지 남긴다. 그렇지 않으면 회사
                # 자료 부족과 수집기 배선 누락을 같은 증상으로만 보게 된다.
                logger.warning(
                    "FULL 사전 필수 의미칸 미달: %s",
                    unreachable_slots_by_section,
                )
                raise V2ValidationError(
                    (
                        "report_recovery:"
                        + FINAL_GATE_DETAIL_PREFLIGHT_OFFICIAL_EVIDENCE_INSUFFICIENT,
                    )
                )

    # ① 본문 9장 작성 (작가)
    draft = compose_sections(
        company_name,
        fragments,
        performance_table,
        writer_for_run,
        # 준비 결과의 plain Mapping으로 낮추면 원본 typed PacketSet이라는 사실과
        # claim-slot 소유권 강제 플래그가 사라진다. 검증한 정본 입력을 그대로
        # 넘겨 작성 경계도 같은 계약을 보게 한다.
        section_evidence_packets=section_evidence_packets,
    )
    if release_mode is not ReleaseMode.SHADOW:
        if prepared_evidence is not None:
            draft = _sanitize_report_to_section_evidence(
                draft,
                prepared_evidence.allowed_fragment_ids_by_section,
                supported_claim_slots_by_fragment_id=(
                    prepared_evidence.supported_claim_slots_by_fragment_id
                ),
                enforce_claim_slot_support=(
                    prepared_evidence.enforce_claim_slot_support
                ),
            )
            _assert_composed_report_evidence_invariant(
                draft,
                prepared_evidence.allowed_fragment_ids_by_section,
                packet_union_ids,
                stage="draft-pre-review",
            )
        # flow 숫자는 기존 canonical 검사로 먼저 재검산한다. 관계 의미는
        # 바로 다음 bundled reviewer 한 번에 본문과 함께 판정한다.
        draft, diagram_problems = check_diagram_numbers(
            draft, _normalize_fragments(verification_fragments)
        )
        if prepared_evidence is not None:
            _assert_composed_report_evidence_invariant(
                draft,
                prepared_evidence.allowed_fragment_ids_by_section,
                packet_union_ids,
                stage="diagram-numeric-pre-review",
            )
    else:
        diagram_problems = ()
    draft_body_count = _total_sentences(draft)  # 이 시점 summary는 빈 튜플이다

    # ② 본문 검증 (검수 — 문장 단위 제거/강등만, 장 삭제 없음)
    if prepared_evidence is None:
        verified = verify_report(
            draft, verification_fragments, performance_table, reviewer_for_run
        )
    else:
        verified = verify_report(
            draft,
            verification_fragments,
            performance_table,
            reviewer_for_run,
            allowed_fragment_ids_by_section=(
                prepared_evidence.allowed_fragment_ids_by_section
            ),
        )
        _assert_composed_report_evidence_invariant(
            verified,
            prepared_evidence.allowed_fragment_ids_by_section,
            packet_union_ids,
            stage="post-verify",
        )

    # ②-b 사실 단일 소유 강제 — 여러 장에 반복된 같은 사실을 소유 장 하나만
    #     남기고 뺀다. 요약 «앞»에 둔다 — 곧 사라질 문장을 요약 재료로 고르면
    #     본문에 없는 요약이 남는다.
    verified, moved_sentences = drop_cross_section_duplicates(verified)
    if moved_sentences:
        logger.info("장 간 중복 %d문장을 소유 장으로 모았습니다", moved_sentences)
    if prepared_evidence is not None:
        _assert_composed_report_evidence_invariant(
            verified,
            prepared_evidence.allowed_fragment_ids_by_section,
            packet_union_ids,
            stage="post-verify-dedupe",
        )

    # ②-c 도식 검증 — 관계 도식의 각 줄이 «근거에 맞는가».
    #     적대 검증에서 결함이 전부 관계 도식에서만 나왔다(수치 0 / 관계 7).
    #     ① 칸 안의 숫자가 원문에 있나(기계) ② 이 경로가 근거에 맞나(검수 AI 1회).
    #     ★ 검수기를 «반드시» 넘긴다. 안 넘기면 숫자 검사만 돌아 관계 결함이
    #       그대로 통과한다 — 이 단계를 만든 이유가 사라진다.
    #     ★ diagram_ask는 출력 상한이 훨씬 작은 «도식 전용» 클로저다.
    #       검수용(8000토큰)을 그대로 쓰면 예약만으로 예산의 21.7%를 먹어
    #       비싼 회사에서 보고서 «전체»가 예산 초과로 실패한다(실측).
    #     근거 없는 줄만 빼며, 줄이 다 빠지면 도식을 안 그릴 뿐 장은 남는다.
    if release_mode is ReleaseMode.SHADOW:
        verified, diagram_problems = check_diagrams(
            verified,
            _normalize_fragments(verification_fragments),
            diagram_ask or reviewer_ask,
        )
    elif prepared_evidence is None:
        # ENFORCE_NO_PARTIAL은 이식기 호환 모드라 typed packet/장별 bundled
        # 의미 판정이 없다. 관계를 확인하지 못한 flow를 공개하거나 별도 diagram
        # AI를 장부 밖에서 부르지 않고, 행만 보수적으로 미공개 처리한다.
        hidden = sum(len(section.flow_rows) for section in verified.sections)
        verified = replace(
            verified,
            sections=tuple(
                replace(section, flow_rows=()) for section in verified.sections
            ),
        )
        if hidden:
            diagram_problems = (
                *diagram_problems,
                f"ENFORCE_NO_PARTIAL 미결속 관계 flow {hidden}행 공개 제외",
            )
    else:
        _assert_composed_report_evidence_invariant(
            verified,
            prepared_evidence.allowed_fragment_ids_by_section,
            packet_union_ids,
            stage="diagram",
        )

    # ★ 사유를 로그«에만» 남기지 않는다 — 결과에도 실어 저장 경로가 나중에
    #   그대로 읽을 수 있게 한다. 3장 카드가 0건이던 실행에서 「작가가 안
    #   냈다」와 「우리가 걸렀다」를 가를 유일한 표식이 이 목록이었는데,
    #   로그가 없으면 사후 진단이 불가능했다.
    recorded_diagram_drop_reasons = tuple(diagram_problems)
    for problem in diagram_problems:
        logger.warning("도식 검증에서 뺀 경로 — %s", problem)

    # ②-d 첫 구조화 claim 슬라이스 — 검증된 DART 3개년 표의 원값에서
    # 누적 증감률을 코드로 재계산한다. AI 산문에서 숫자를 역추출하지 않으며,
    # 표의 회계범위·원단위·원 payload가 하나라도 빠지면 아무것도 만들지 않는다.
    verified = append_past_changes_numeric_claims(
        verified,
        performance_table,
        verification_fragments,
        filing_meta,
    )
    if prepared_evidence is not None:
        _assert_composed_report_evidence_invariant(
            verified,
            prepared_evidence.allowed_fragment_ids_by_section,
            packet_union_ids,
            stage="numeric-append",
        )
    # ②-e 새 생성 수치 안전 경계. AI 산문에 숫자·날짜·백분율이 있으면
    # 의미가 결속된 StructuredClaim/NumericBinding 없이는 공개 후보에서 뺀다.
    # 산문을 역추출해 가짜 fact로 통과시키지 않는다. 프로그램이 만든 위 누적
    # 증감률은 동일한 versioned 결속을 재검산한 뒤 그대로 남는다.
    verified, body_numeric_filtering = enforce_public_numeric_safety(verified)
    # 공식 양사 비교는 AI 산문이 아니라 별도 다중 출처 수치 검산기가 만든다.
    # AI 수치 필터를 우회시키는 것이 아니라 그 필터가 끝난 뒤 packet에 이미
    # 봉인된 문장만 추가하고, 아래 품질 평가에서 다시 공식 재계산한다.
    verified = _append_verified_program_sentences(verified, prepared_evidence)
    if prepared_evidence is not None:
        _assert_composed_report_evidence_invariant(
            verified,
            prepared_evidence.allowed_fragment_ids_by_section,
            packet_union_ids,
            stage="numeric-safety",
        )
    # ★ 이 삭제는 보고서 «안»에만 적히고 서버 로그엔 흔적이 없었다.
    #   실측: 현대카드에서 최소 16문장이 여기서 사라졌는데 로그로는 안 보였다.
    if body_numeric_filtering.removed_section_counts:
        logger.warning(
            "수치 안전 제외: 구조화 근거 없는 수치·날짜 문장 %d개 (장별 %s)",
            sum(n for _, n in body_numeric_filtering.removed_section_counts),
            ", ".join(
                f"{sid}:{n}" for sid, n in body_numeric_filtering.removed_section_counts
            ),
        )

    # ③~④ 요약. SHADOW는 이미 운영 중인 AI 작성·재검증 경로를 그대로
    # 보존한다. 엄격 모드는 본문에 없던 말을 새로 만들지 않고, 렌더러가
    # 만든 검증 FactRecord에 정확히 결속된 본문 문장을 0원으로 재사용한다.
    if release_mode is ReleaseMode.SHADOW:
        final, summary_draft_count, numeric_filtering = _legacy_summary_stage(
            verified,
            verification_fragments,
            performance_table,
            writer_ask=writer_ask,
            reviewer_ask=reviewer_for_run,
            body_numeric_filtering=body_numeric_filtering,
        )
    else:
        body_rendered = render_report(
            company_name,
            verified,
            verification_fragments,
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
            company_id=(str(company_id).strip() if release_mode is ReleaseMode.FULL else ""),
            release_mode=release_mode.value,
            verified_program_facts=(
                prepared_evidence.program_facts
                if prepared_evidence is not None
                else ()
            ),
            program_registry_sources=(
                prepared_evidence.program_sources
                if prepared_evidence is not None
                else ()
            ),
        )
        extractive = select_extractive_summary(verified, body_rendered.fact_records)
        # FULL은 이 시점의 결과가 아직 ``primary`` 후보일 뿐이다. 요약이
        # 부족하더라도 먼저 품질 영수증을 만들고 복구 정책이 보충/중단을
        # 결정해야 한다. STOP이면 아래 출고 검증까지 도달하지 않으며,
        # RUN_SUPPLEMENTS이면 병합 뒤 요약을 새로 계산한다.
        if (
            not extractive.release_ready
            and release_mode is not ReleaseMode.FULL
        ):
            raise V2ValidationError(
                (
                    "엄격 출고용 핵심 요약에 서로 다른 장의 검증 사실이 "
                    f"3개 이상 필요하지만 {len(extractive.items)}개뿐입니다",
                )
            )
        final = ComposedReport(
            sections=verified.sections,
            summary=extractive.bound_sentences,
        )
        summary_draft_count = 0
        numeric_filtering = body_numeric_filtering

    if prepared_evidence is not None:
        _assert_composed_report_evidence_invariant(
            final,
            prepared_evidence.allowed_fragment_ids_by_section,
            packet_union_ids,
            stage="pre-render",
        )

    # FULL 공개 구조는 renderer를 부르기 전에 별도 canonicalizer로 봉인한다.
    # SHADOW는 이 객체를 만들지도 전달하지도 않아 기존 호출·문자를 보존한다.
    public_structure_seal: Optional[PublicStructureSeal] = None
    if release_mode is ReleaseMode.FULL:
        if prepared_evidence is None:  # preflight가 이미 막지만 타입 좁힘용 방어
            raise V2ValidationError(("FULL section packet 준비값이 없습니다",))
        public_structure_seal = build_public_structure_seal(
            final,
            verification_fragments,
            performance_table,
            filing_meta=filing_meta,
            composition_tables=composition_tables,
            table_presentation=table_presentation,
            company_id=prepared_evidence.company_id,
            evidence_generation_sha256=(
                prepared_evidence.evidence_generation_sha256
            ),
            evidence_packet_sha256s=prepared_evidence.packet_sha256s,
            company_name=company_name,
            corp_type=corp_type,
            generated_at=generated_at,
            as_of_date=as_of_date,
            analysis_period=analysis_period,
            latest_performance_period=latest_performance_period,
            citation_style=citation_style,
            program_registry_sources=prepared_evidence.program_sources,
        )

    # ⑤ 렌더 — 웹·PDF가 이미 소비하는 공용 구조로
    seal_render_kwargs = (
        {}
        if public_structure_seal is None
        else {"public_structure_seal": public_structure_seal}
    )
    rendered = render_report(
        company_name,
        final,
        verification_fragments,
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
        company_id=str(company_id).strip(),
        release_mode=("" if release_mode is ReleaseMode.SHADOW else release_mode.value),
        verified_program_facts=(
            prepared_evidence.program_facts
            if prepared_evidence is not None
            else ()
        ),
        program_registry_sources=(
            prepared_evidence.program_sources
            if prepared_evidence is not None
            else ()
        ),
        **seal_render_kwargs,
    )
    primary_block_sha256s: tuple[tuple[str, str], ...] = ()
    if public_structure_seal is not None:
        assert_report_matches_public_structure(rendered, public_structure_seal)
        # ★ 영수증 try 블록 «밖»에서 계산한다 — 안에서 하면 봉인 실패가
        #   `except (TypeError, ValueError)`에 걸려 「영수증이 잘못됐다」는
        #   엉뚱한 사유로 바뀐다. 봉인 실패는 봉인 실패로 드러나야 한다.
        primary_block_sha256s = _section_block_sha256s(rendered)

    # ⑤-a 품질·공개 안전 shadow 판정 — 생성 시점에만 한 번 실행한다.
    # past_changes의 프로그램 생성 누적 증감률은 원자 fact_id·claim slot·원문
    # 결속을 갖춘 첫 수직 슬라이스다. 나머지 산문·요약·표·도식은 텍스트를
    # 정규식으로 쪼개 가짜 fact를 만들지 않고 «결속되지 않은 공개 내용»으로
    # 남긴다. 따라서 전체 안전 결과는 계속 미완성/차단이며, 숫자 문장 경계
    # 밖의 공개 구조는 결속과 영향 측정 전까지 전체 hard gate로 승격하지 않는다.
    quality_candidate = build_generation_quality_candidate(rendered, final)
    generation_assessment, quality_observation = assess_and_observe_generation(
        quality_candidate,
        contract_version=(
            STRICT_QUALITY_CONTRACT_VERSION
            if release_mode is ReleaseMode.FULL
            else (
                LEGACY_STRICT_QUALITY_CONTRACT_VERSION
                if release_mode is ReleaseMode.ENFORCE_NO_PARTIAL
                else ""
            )
        ),
    )
    if not quality_observation.release_allowed:
        logger.warning(
            "v2 생성 품질 판정(전체 안전은 관측 전용): 계약=%s · 품질=%s · 안전=%s · 문제=%s",
            quality_observation.contract_version,
            quality_observation.quality_grade,
            quality_observation.safety_decision,
            quality_observation.safety_problems,
        )
    candidate_sha256 = ""
    validation_receipts: tuple[GenerationValidationReceipt, ...] = ()
    if release_mode is ReleaseMode.FULL:
        if (
            public_structure_seal is None
            or prepared_evidence is None
            or call_recorder is None
        ):
            raise V2ValidationError(("FULL 생산 증거 재료가 누락됐습니다",))
        candidate_sha256 = canonical_sha256(quality_candidate)
        try:
            primary_receipt = GenerationValidationReceipt(
                company_id=prepared_evidence.company_id,
                candidate_sha256=candidate_sha256,
                assessment=generation_assessment,
                round=ValidationRound.PRIMARY,
                writer_calls=call_recorder.calls_for(
                    ValidationRound.PRIMARY,
                    role="writer",
                ),
                reviewer_calls=call_recorder.calls_for(
                    ValidationRound.PRIMARY,
                    role="reviewer",
                ),
                section_sha256s=public_structure_seal.section_sha256s,
                evidence_packet_sha256s=prepared_evidence.packet_sha256s,
                section_block_sha256s=primary_block_sha256s,
            )
            recovery_decision = decide_post_validation(primary_receipt)
        except (TypeError, ValueError) as error:
            raise V2ValidationError(
                ("report_recovery:primary_receipt_invalid",)
            ) from error

        if recovery_decision.action is RecoveryAction.RUN_SUPPLEMENTS:
            authorization = recovery_decision.supplement_authorization
            if type(authorization) is not SupplementAuthorization:
                raise V2ValidationError(
                    ("report_recovery:supplement_authorization_missing",)
                )
            targets = authorization.section_ids
            supplement_writer = call_recorder.wrap(
                writer_ask,
                role="writer",
                validation_round=ValidationRound.SUPPLEMENT,
                section_ids=targets,
            )
            supplement_reviewer = call_recorder.wrap(
                reviewer_ask,
                role="reviewer",
                validation_round=ValidationRound.SUPPLEMENT,
                section_ids=("bundled",),
            )

            # 승인 장만 자기 typed packet으로 다시 쓴다. 이 단계의 report에는
            # 대상 밖 장이 아예 없으므로 writer가 그 장을 바꿀 통로도 없다.
            supplement_draft = compose_selected_sections(
                company_name,
                performance_table,
                supplement_writer,
                section_evidence_packets=section_evidence_packets,
                section_ids=targets,
            )
            draft_body_count += _total_sentences(supplement_draft)
            supplement_draft, supplement_diagram_problems = check_diagram_numbers(
                supplement_draft,
                _normalize_fragments(verification_fragments),
            )
            for problem in supplement_diagram_problems:
                logger.warning("보충 도식 검증에서 뺀 경로 — %s", problem)
            supplement_verified = verify_report(
                supplement_draft,
                verification_fragments,
                performance_table,
                supplement_reviewer,
                allowed_fragment_ids_by_section=(
                    prepared_evidence.allowed_fragment_ids_by_section
                ),
            )
            supplement_verified, supplement_moved = drop_cross_section_duplicates(
                supplement_verified
            )
            if supplement_moved:
                logger.info(
                    "보충 대상 장 사이 중복 %d문장을 소유 장으로 모았습니다",
                    supplement_moved,
                )
            supplement_verified = append_past_changes_numeric_claims(
                supplement_verified,
                performance_table,
                verification_fragments,
                filing_meta,
            )
            supplement_verified, supplement_numeric_filtering = (
                enforce_public_numeric_safety(supplement_verified)
            )

            base_body = verified
            merged_body = _merge_selected_sections(
                base_body,
                supplement_verified,
                targets,
            )
            # 병합 뒤 전역 수치 안전을 다시 계산한다. 비대상 장은 값뿐 아니라
            # ComposedSection 전체(본문·도식·structured fact)가 exact 동일해야 한다.
            merged_body, merged_numeric_filtering = enforce_public_numeric_safety(
                merged_body
            )
            merged_body = _append_verified_program_sentences(
                merged_body, prepared_evidence
            )
            base_by_id = {
                section.section_id: section for section in base_body.sections
            }
            merged_by_id = {
                section.section_id: section for section in merged_body.sections
            }
            target_set = set(targets)
            if any(
                merged_by_id[section_id] != base_by_id[section_id]
                for section_id in SECTION_IDS
                if section_id not in target_set
            ):
                _raise_recovery_stop("non_target_section_mutated")
            _assert_composed_report_evidence_invariant(
                merged_body,
                prepared_evidence.allowed_fragment_ids_by_section,
                packet_union_ids,
                stage="supplement-merged-numeric-safety",
            )
            verified = merged_body
            numeric_filtering = numeric_filtering.merged(
                supplement_numeric_filtering
            ).merged(merged_numeric_filtering)

            # 요약·manifest·render·quality candidate/assessment는 보충 병합본에서
            # 모두 새로 만든다. 첫 후보의 전역 파생물을 재사용하지 않는다.
            body_rendered = render_report(
                company_name,
                verified,
                verification_fragments,
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
                company_id=prepared_evidence.company_id,
                release_mode=release_mode.value,
                verified_program_facts=prepared_evidence.program_facts,
                program_registry_sources=prepared_evidence.program_sources,
            )
            extractive = select_extractive_summary(
                verified,
                body_rendered.fact_records,
            )
            supplement_summary_release_ready = extractive.release_ready
            final = ComposedReport(
                sections=verified.sections,
                summary=extractive.bound_sentences,
            )
            summary_draft_count = 0
            _assert_composed_report_evidence_invariant(
                final,
                prepared_evidence.allowed_fragment_ids_by_section,
                packet_union_ids,
                stage="supplement-pre-render",
            )
            public_structure_seal = build_public_structure_seal(
                final,
                verification_fragments,
                performance_table,
                filing_meta=filing_meta,
                composition_tables=composition_tables,
                table_presentation=table_presentation,
                company_id=prepared_evidence.company_id,
                evidence_generation_sha256=(
                    prepared_evidence.evidence_generation_sha256
                ),
                evidence_packet_sha256s=prepared_evidence.packet_sha256s,
                company_name=company_name,
                corp_type=corp_type,
                generated_at=generated_at,
                as_of_date=as_of_date,
                analysis_period=analysis_period,
                latest_performance_period=latest_performance_period,
                citation_style=citation_style,
                program_registry_sources=prepared_evidence.program_sources,
            )
            rendered = render_report(
                company_name,
                final,
                verification_fragments,
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
                company_id=prepared_evidence.company_id,
                release_mode=release_mode.value,
                public_structure_seal=public_structure_seal,
                verified_program_facts=prepared_evidence.program_facts,
                program_registry_sources=prepared_evidence.program_sources,
            )
            assert_report_matches_public_structure(
                rendered,
                public_structure_seal,
            )
            # 기본 경로와 같은 이유로 영수증 try 밖에서 계산한다.
            supplement_block_sha256s = _section_block_sha256s(rendered)
            quality_candidate = build_generation_quality_candidate(
                rendered,
                final,
            )
            generation_assessment, quality_observation = (
                assess_and_observe_generation(
                    quality_candidate,
                    contract_version=STRICT_QUALITY_CONTRACT_VERSION,
                )
            )
            candidate_sha256 = canonical_sha256(quality_candidate)
            try:
                supplement_receipt = GenerationValidationReceipt(
                    company_id=prepared_evidence.company_id,
                    candidate_sha256=candidate_sha256,
                    assessment=generation_assessment,
                    round=ValidationRound.SUPPLEMENT,
                    writer_calls=call_recorder.calls_for(
                        ValidationRound.SUPPLEMENT,
                        role="writer",
                    ),
                    reviewer_calls=call_recorder.calls_for(
                        ValidationRound.SUPPLEMENT,
                        role="reviewer",
                    ),
                    section_sha256s=public_structure_seal.section_sha256s,
                    evidence_packet_sha256s=prepared_evidence.packet_sha256s,
                    base_receipt_sha256=primary_receipt.receipt_sha256,
                    supplemented_section_ids=targets,
                    section_block_sha256s=supplement_block_sha256s,
                )
                recovery_decision = decide_post_validation(
                    primary_receipt,
                    supplement_authorization=authorization,
                    supplement_receipt=supplement_receipt,
                )
            except (TypeError, ValueError) as error:
                logger.warning("FULL 보충 검증 영수증 결속 실패", exc_info=True)
                raise V2ValidationError(
                    ("report_recovery:supplement_receipt_invalid",)
                ) from error
            if recovery_decision.action is not RecoveryAction.RELEASE_COMPLETE:
                _raise_recovery_stop(
                    recovery_decision.reason_code,
                    recovery_decision.quality_problem_codes,
                )
            if not supplement_summary_release_ready:
                # 두 번째 후보의 manifest·render·품질 평가·receipt·정책 결정을
                # 모두 다시 만든 뒤에야 닫는다. 조기 예외로 파생물 재계산을
                # 건너뛰거나 세 번째 보충으로 흐르지 않는다.
                _raise_recovery_stop("supplement_summary_insufficient")
            validation_receipts = (primary_receipt, supplement_receipt)
        elif recovery_decision.action is RecoveryAction.RELEASE_COMPLETE:
            validation_receipts = (primary_receipt,)
        elif recovery_decision.action is RecoveryAction.STOP_NO_CHARGE:
            _raise_recovery_stop(
                recovery_decision.reason_code,
                recovery_decision.quality_problem_codes,
            )
        else:
            _raise_recovery_stop("unexpected_primary_decision")
    elif release_mode is not ReleaseMode.SHADOW and (
        quality_observation.quality_grade != "완성"
        or quality_observation.publication_grade != "완성"
        or not quality_observation.release_allowed
    ):
        strict_problems = list(
            dict.fromkeys(
                (
                    *quality_observation.quality_shortfalls,
                    *quality_observation.safety_problems,
                )
            )
        )
        if not strict_problems:
            strict_problems.append(
                "엄격 품질·공개 안전 계약이 완성 보고서로 판정하지 않았습니다"
            )
        # ★ 이 raise «만» 품질 코드를 함께 싣는다 — 최종 게이트가 «품질 하한
        #   미달»과 다른 구조·안전 오류를 구분하려면 여기서 나온 코드가
        #   필요하다. 다른 raise 지점(구조 결속·생산 증거 등)은 quality
        #   assessor를 거치지 않으므로 코드를 지어내지 않는다.
        raise V2ValidationError(
            strict_problems,
            problem_codes=quality_observation.quality_problem_codes,
        )
    if release_mode is ReleaseMode.SHADOW:
        rendered = _apply_generation_quality_label(
            rendered,
            quality_observation,
            numeric_filtering,
        )
    else:
        # 엄격 계약을 실제로 통과한 결과만 여기 온다. 입력 grade의 옛 기본값
        # PARTIAL을 그대로 두면 내용은 완성인데 화면·결제층은 부분 보고서로
        # 보는 모순이 생긴다. 엄격 assessor를 단일 판정자로 삼아 완성으로 봉인한다.
        rendered = replace(
            rendered,
            grade=Grade.COMPLETE,
            shortfall_reasons=[],
            quality_contract_version=quality_observation.contract_version,
            safety_decision=quality_observation.safety_decision,
            publication_policy=PublicationPolicy.STRUCTURED_SAFETY.value,
        )
        if public_structure_seal is not None:
            assert_report_matches_public_structure(rendered, public_structure_seal)
            actual_content_sha256, actual_section_sha256s = public_content_digests(
                rendered
            )
            if (
                actual_content_sha256
                != public_structure_seal.public_content_sha256
                or actual_section_sha256s != public_structure_seal.section_sha256s
            ):
                raise PublicManifestError(
                    "renderer actual 본문·문단·요약·표·출처·출고표시가 "
                    "pre-render 공개 content 봉인과 다릅니다"
                )
            # ⑤-b 공개 봉인 projection — 웹·PDF·Notion이 «그대로 배치만» 하면
            # 되는 블록을 여기서 딱 한 번 만든다.
            #
            # ★ 왜 하필 이 자리인가 — 바로 위 ``replace``가 등급을 완성으로
            #   다시 봉인한 «최종» 보고서라서다. 첫 seal 단정(렌더 직후) 자리에서
            #   만들면 header에 입력 기본값 「부분」이 박히고 부분 보고서 고지
            #   문구까지 딸려 들어가, 저장본은 완성인데 화면 블록만 부분이라고
            #   말하는 보고서가 남는다. 보충 회복 경로도 이 자리로 합류하므로
            #   두 경로가 한 번씩 봉인된다.
            # ★ 왜 아직 생성 증거보다 앞인가 — 아래 GenerationProducerEvidence가
            #   이 projection의 digest를 실어야 해서다. 반대로 두면 증거가
            #   자기 자신을 해싱하는 순환이 된다.
            # ★ 예외를 삼키지 않는다 — 봉인이 안 되는 보고서는 공개하지
            #   않는다. try/except로 감싸 projection 없이 내보내면 채널이
            #   갈라진 채 출고된다.
            rendered = replace(
                rendered,
                public_projection=build_public_projection(rendered),
            )

    # ``sentences_made``는 AI 호출 수가 아니라 공개 후보 문장 단위의 분모다.
    # extractive summary는 추가 AI 0회지만 보고서에 실리는 후보 다섯 단위이므로
    # 포함한다. 실제 AI 비용은 별도 call ledger가 정직하게 9/1을 봉인한다.
    composed_item_count = max(
        draft_body_count + summary_draft_count,
        _total_sentences(final),
    )
    fragments_collected, fragments_cited = _generation_fragment_counts(
        verification_fragments,
        rendered,
    )
    generation_metrics = GenerationRunMetrics(
        fragments_collected=fragments_collected,
        fragments_cited=fragments_cited,
        sentences_made=composed_item_count,
        sentences_passed=_total_sentences(final),
    )
    assert_observation_matches_assessment(
        quality_observation,
        generation_assessment,
    )
    # ★ SHADOW도 관측값을 저장한다 — 예전에는 여기서 SHADOW만
    #   None으로 비웠다. 그러면 「거짓 거절률」을 셀 대상 자체가 저장소에 하나도
    #   안 남아 O-F3(관측 안 됨)를 영구히 못 풀었다. 이 값은 여전히 «관측
    #   전용»이다 — release_allowed=False라도 SHADOW는 다음 줄들에서 여전히
    #   REPORT로 나가고 차감도 그대로다(게이트는 C4가 별도 사람 결정 뒤에만
    #   만든다). 이 필드가 채워진다고 판정 로직이 하나라도 바뀌지 않는다.
    # ★ report_sha256 정정(독립 검토가 잡음) — 이 필드가 늘어나면
    #   `export_pdf.automatic_release.report_sha256`(report_to_dict 전체 해시)은
    #   «실제로 바뀐다». 그 해시를 비교하는 `web/routers/reports.py:_release_state`·
    #   `generation_singleflight.py`·`report_delivery_adapter.py`는 release_mode로
    #   안 걸러져 SHADOW도 그 경로를 탄다 — "SHADOW는 자동 PDF 출고가 없어 무관하다"는
    #   448d10b 커밋 메시지의 근거는 틀렸다. 안전한 진짜 이유는 따로 있다: 저장된
    #   보고서는 payload가 다시 안 바뀌므로 report_sha256(report)은 그 report_id의
    #   평생 동안 항상 같은 값이다(자기일관적) — 「이 코드 이전 해시」와 「이후 해시」를
    #   비교하는 경로가 아니다. 그래도 불일치가 나면 `_release_state`는
    #   already_completed 저장값을 정본으로 흡수하고, singleflight/delivery adapter는
    #   재사용을 포기(fail-safe)할 뿐 잘못된 값을 내보내지 않는다(fail-open 아님).
    rendered = replace(
        rendered,
        generation_metrics=generation_metrics,
        quality_observation=quality_observation,
    )

    # 성공 생산 증거는 최종 가시 출고 검증까지 통과한 뒤에만 만든다. 두 번째
    # 품질 실패나 validate_v2 실패에서는 부분 후보·영수증을 외부로 운반하지 않는다.
    _log_duplicate_findings(rendered)
    validate_v2(rendered)

    generation_evidence: GenerationProducerEvidence | None = None
    if release_mode is ReleaseMode.FULL:
        if (
            public_structure_seal is None
            or prepared_evidence is None
            or call_recorder is None
            or not validation_receipts
            # 공개 봉인 projection이 없으면 증거가 「어떤 공개본을 냈는지」를
            # 지목할 수 없다 — 지목 못 하는 증거로는 나중에 블록을 갈아 끼운
            # 것을 잡지 못하므로 여기서 닫는다.
            or rendered.public_projection is None
        ):
            raise V2ValidationError(("FULL 생산 증거 재료가 누락됐습니다",))
        generation_evidence = GenerationProducerEvidence(
            company_id=prepared_evidence.company_id,
            evidence_generation_sha256=(
                prepared_evidence.evidence_generation_sha256
            ),
            build_identity_sha256=normalized_build_identity_sha256,
            candidate_sha256=candidate_sha256,
            assessment=generation_assessment,
            public_manifest_sha256=exact_text_sha256(
                public_structure_seal.canonical_json
            ),
            public_content_sha256=(
                public_structure_seal.public_content_sha256
            ),
            # 이 값은 화면(display)뿐 아니라 그 장이 기여한 감사 장부까지
            # 덮는다 — 장부만 바꾼 위조도 이 지문 하나로 드러난다.
            public_projection_sha256=build_report_digest(
                rendered.public_projection
            ).content_sha256,
            section_sha256s=public_structure_seal.section_sha256s,
            evidence_packet_sha256s=prepared_evidence.packet_sha256s,
            validation_receipts=validation_receipts,
            call_ledger=call_recorder.freeze(),
        )
        rendered = replace(
            rendered,
            generation_evidence=generation_evidence,
        )
        assert_report_matches_generation_evidence(
            report_verification_payload(rendered),
            generation_evidence,
            manifest_bytes=rendered.public_structure_manifest.encode("utf-8"),
        )

    return V2RunOutput(
        report=rendered,
        composed_sentences=composed_item_count,
        verified_sentences=_total_sentences(final),
        quality_observation=quality_observation,
        generation_evidence=generation_evidence,
        generation_metrics=generation_metrics,
        diagram_drop_reasons=recorded_diagram_drop_reasons,
    )


__all__ = ["V2RunOutput", "run_v2"]
