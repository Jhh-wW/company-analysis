"""엔진 v2 오케스트레이션 (소단계 3-4b) — 쓰기→검증→요약→렌더→중복경고→출고검증.

★ 이 파일은 composer 조각들을 «정해진 순서로 잇기만» 한다:
    compose_sections → verify_report → (요약 문장 고르기·보충)
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
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
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
from src.shared.report_quality.review_diagnostic_constants import REVIEW_SCOPE_ITEMS
from src.shared.report_quality.composition_diagnostic_constants import (
    DIAGRAM_STAGE_SECTION_EVIDENCE,
    SUMMARY_STEP,
)
from src.features.composer.logic import (
    AskFn,
    FragmentsInput,
    SectionEvidencePackets,
    _assert_composed_report_evidence_invariant,
    _normalize_fragments,
    # 요약 «장당 최대 1개» 열쇠는 보충 쪽과 «같은» 정규화를 써야 한다 —
    # 다른 정규화를 쓰면 같은 문장이 다른 열쇠가 되어 제한이 조용히 빗나간다.
    _normalized_text,
    _prepare_section_evidence_packets,
    _sanitize_report_to_section_evidence,
    record_flow_row_counts,
    _validate_table_citations_for_section,
    SUMMARY_MAX_SENTENCES,
    SUMMARY_MIN_SENTENCES,
    # 요약 보충 규칙(본문 «확인» 문장 재사용·서로 다른 장 우선)은 3-3이 정의한
    # 단일 구현을 그대로 쓴다 — 같은 feature 내부 재사용이라 별도 복제를 두지 않는다.
    _supplement_summary,
    _supplement_summary_any_grade,
    compose_selected_sections,
    compose_sections,
    select_summary_sentences,
    summary_candidates,
)
from src.features.composer.constants import (
    AI_STAGE_COMPOSE_VERIFY,
    AI_STAGE_DIAGRAM,
    AI_STAGE_FULL_SUPPLEMENT,
    AI_STAGE_REWRITE,
    AI_STAGE_SUMMARY,
    DEFAULT_CITATION_STYLE,
    DEGRADED_REASON_CALL_LIMIT,
    DEGRADED_REASON_EVIDENCE_UNREACHABLE,
    DEGRADED_REASON_PROVIDER_UNAVAILABLE,
    DEGRADED_REASON_QUALITY_FLOOR,
    DEGRADED_REASON_REQUEST_BUDGET_EXHAUSTED,
    NOTICE_AI_UNAVAILABLE,
    NOTICE_EVIDENCE_NONE,
    NOTICE_EVIDENCE_NOT_COMPOSED,
    PORTFOLIO_TABLE_SECTION_ID,
    SECTION_IDS,
    SECTION_TITLES,
    SHORTFALL_AI_DEGRADED,
    SHORTFALL_TABLE_EVIDENCE_UNBOUND,
    SUMMARY_NOTICE_EMPTY,
    SUMMARY_NOTICE_THIN,
)
from src.features.composer.evidence_availability import (
    COLLECTION_STATE_PARTIAL,
    EvidenceAvailability,
)
from src.features.composer.dedupe import (
    drop_cross_section_duplicates,
    sections_with_program_tables,
)
from src.features.composer.news_usage import supplement_news_candidates, retain_verified_news, news_usage_diagnostics, news_citation_ids
from src.features.composer.review_outcomes import final_review_outcomes
from src.features.composer.stray_citation_marker import sanitize_stray_citation_markers
from src.features.composer.empty_section_recovery import (
    recovery_evidence, recover_empty_sections, rejected_sentence_fingerprint,
)
from src.features.composer.empty_section_recovery_constants import EMPTY_RECOVERY_STEP, MAX_EMPTY_RECOVERY_SECTIONS
from src.features.composer.diagram_check import check_diagram_numbers, check_diagrams
from src.features.composer.dup_detect import CONFIDENCE_CONFIRMED, find_numeric_duplicates
from src.features.composer.extractive_summary import select_extractive_summary
from src.features.composer.news_block import (
    NewsBlockResult,
    augment_news_blocks,
    news_ownership_from_claim_slots,
)
from src.features.composer.portfolio_name_table import (
    BLOCKED_LABEL_NOT_IN_LOCATION,
    BLOCKED_NAME_NOT_IN_SOURCE,
    PortfolioNameTableResult,
    build_portfolio_name_table,
)
from src.features.composer.portfolio_names import portfolio_name_usage
from src.features.composer.port import (
    AskFatalError,
    ComposedReport,
    ComposedSection,
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
    QUALITY_DERIVED_STOP_REASON_CODES,
    RecoveryAction,
    RecoveryDecision,
    SupplementAuthorization,
    decide_post_validation,
    supplement_unchanged_sections,
)
from src.core.citations import citation_number
from src.shared.engine_build_identity import EngineBuildIdentityChangedError
from src.shared.generation_coordination import GenerationCoordinationError
from src.shared.report_generation.public_projection import build_report_digest
from src.shared.report_generation.canonical import (
    assert_report_matches_generation_evidence,
    public_content_digests,
    report_verification_payload,
)
from src.features.composer.quality_observation_log import (
    log_generation_quality_observation,
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
    is_release_ready_summary_sentence,
    safe_numeric_owners_by_fact_id,
)
from src.features.composer.validate import V2ValidationError, validate_v2
# ★ verify_sentences를 «일부러» 들여오지 않는다 — 요약 재검증 단계가 없어졌고,
#   import가 남아 있으면 다음 사람이 무심코 다시 부를 자리가 된다.
from src.features.composer.verify import verify_report
from src.features.pipeline.port import FactRecord, Grade, Report
from src.features.provenance.sources import Source
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
    #: ★ 왜 결과에 싣나 (2026-09-05 상장 엔터사 3장 카드 0건) — 이 사유는 지금까지
    #:   서버 로그에만 남았다. 로컬 DB에 그 실행이 없어 「작가가 안 냈다」와
    #:   「우리가 걸렀다」를 가를 방법이 아예 없었다. 결과에 실어야 다음
    #:   진단을 저장된 실행 기록만으로 할 수 있다.
    #: ★ 기본값이 빈 tuple이라 이 필드를 모르는 기존 호출·저장 경로는
    #:   그대로 돈다(새 키 추가만, 읽기 호환 유지).
    diagram_drop_reasons: tuple[str, ...] = ()
    #: 3장 packet에 대표 이름이 하한 이상 왔는데 카드가 하나도 안 쓴 경우의
    #: «이름 수». 안 그러면 0이다.
    #:
    #: ★ 왜 개수만 싣나 — 이름 자체는 이미 보고서 근거 안에 있고, 실행 기록은
    #:   운영 진단용이라 원문을 늘릴 이유가 없다. 「몇 개를 줬는데 안 썼나」만
    #:   알면 안내문이 듣는지 아닌지를 다음 실행에서 바로 잰다.
    unused_portfolio_name_count: int = 0
    #: 그때의 종류 라벨별 이름 수. dataclass를 얼린 채로 실어 나르려고
    #: dict가 아니라 (라벨, 수) 쌍의 tuple이다.
    unused_portfolio_name_counts_by_label: tuple[tuple[str, int], ...] = ()
    #: 3장에 «결정적으로» 덧붙인 「회사가 공시한 대표 이름」 표에 실린 이름 수.
    #: 표를 안 만들었으면 0이다.
    #:
    #: ★ 위 `unused_…`와 «함께» 0이 아닐 수 있다 — 이 표는 작가가 이름을
    #:   썼든 안 썼든 항상 만든다. 두 값은 서로 다른 것을 잰다: `unused_…`는
    #:   「작가가 안내문을 지켰나」, 이쪽은 「독자가 이름을 볼 수 있나」다.
    portfolio_name_table_name_count: int = 0
    #: 그 표에 실린 이름의 종류 라벨별 수.
    portfolio_name_table_counts_by_label: tuple[tuple[str, int], ...] = ()
    #: 이름이 나온 공시 표의 제목들. 실행 기록의 「표제목」 값.
    portfolio_name_table_titles: tuple[str, ...] = ()
    #: 이름은 왔는데 표를 «못» 만들었을 때의 이유 코드
    #: (`portfolio_name_table.BLOCKED_*`). 만들었거나 이름이 애초에 없으면 ""다.
    portfolio_name_table_blocked_reason: str = ""
    #: 장 끝 「최근 보도 (보조)」 표에 실린 장별 행 수. 붙인 장만 담는다.
    #: dataclass를 얼린 채로 실어 나르려고 dict가 아니라 (장, 수) 쌍의 tuple이다.
    news_block_row_counts_by_section: tuple[tuple[str, int], ...] = ()
    #: 보도표에서 «뺀 행»과 «못 붙인 장»의 사유별 수(`news_block.BLOCKED_*`).
    news_block_blocked_counts_by_reason: tuple[tuple[str, int], ...] = ()
    news_usage_diagnostics: dict[str, object] = field(default_factory=dict)
    #: 원문을 저장하지 않는 검수 제외 진단. 본문·요약·도식에서 같은 계약을 쓴다.
    review_diagnostics: tuple[dict[str, object], ...] = ()
    # ── 확보 근거 보고서 전환 기록(2026-09-14 사용자 계약) ──
    #: 왜 결정론 경로로 마무리했는가. 빈 값이면 전환이 없었다.
    #: 값: request_budget_exhausted · call_limit · provider_unavailable ·
    #:     quality_floor · required_evidence_unreachable
    degraded_reason: str = ""
    #: AI 전역 장애의 원인 예외 클래스 이름(원문·메시지는 싣지 않는다).
    degraded_cause_kind: str = ""
    #: 건너뛴 AI 단계(compose_verify·diagram_review·summary_selection·full_supplement).
    ai_stages_skipped: tuple[str, ...] = ()
    #: 실제로 적용된 운영 모드. FULL 요청이 확보 근거 보고서로 내려가면 SHADOW다.
    #: 파이프라인은 요청 모드가 아니라 «이 값»으로 결과를 다뤄야 한다.
    effective_release_mode: str = ""
    #: 확보 근거 보고서로 내려온 경우 원래 요청 모드(예: FULL). 아니면 빈 값.
    downgraded_from_release_mode: str = ""


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
    review_diagnostics: Sequence[Mapping[str, object]] = (),
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

    # 수치 후처리보다 앞선 의미 검수에서 제외된 문장도 사유를 잃지 않는다.
    # 이는 계산이 틀렸다고 확정한 횟수가 아니라 근거를 결속하지 못한 횟수다.
    review_counts: dict[str, int] = {}
    scope_review_counts: dict[str, int] = {}
    for diagnostic in review_diagnostics:
        kind = str(diagnostic.get("kind", ""))
        counts_for_reason = (
            scope_review_counts
            if diagnostic.get("reason_code") in REVIEW_SCOPE_ITEMS else review_counts
        )
        counts_for_reason[kind] = counts_for_reason.get(kind, 0) + 1
    if review_counts:
        detail = ", ".join(f"{kind} {count}개" for kind, count in review_counts.items())
        reasons.append(
            "숫자·날짜 문장의 항목·기간·계산 관계를 원문과 맞춰 확인하지 못해 "
            f"제외했습니다 ({detail}). 자료 자체가 없다는 뜻은 아닙니다."
        )
    if scope_review_counts:
        detail = ", ".join(f"{kind} {count}개" for kind, count in scope_review_counts.items())
        reasons.append(
            "원문과 계획·조건·공식 설명의 범위가 일치하는지 확인하지 못한 "
            f"문장을 제외했습니다 ({detail}). 자료 자체가 없다는 뜻은 아닙니다."
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
    *,
    excluded_keys: frozenset[str] = frozenset(),
    accept: Callable[[ComposedSentence], bool] | None = None,
    section_by_key: Mapping[str, str] | None = None,
) -> tuple[ComposedSentence, ...]:
    """AI가 고른 요약이 짧으면 안전한 본문 문장으로 최소치만 채운다.

    먼저 기존 계약대로 «확인» 문장을 고른다. reviewer가 전역 실패하면 모든
    문장이 «해석»으로 강등돼 확인 문장이 0개일 수 있다. 그 경우에도 이미
    미결속 수치 문장을 제거한 본문에서 장을 번갈아 골라, 예전의 «강등하되
    보고서 전체는 막지 않는다» 안전선을 지킨다.

    ★ «각 장의 첫 문장을 마지막 순위로» 규칙은 이제 두 경로가 «같은» 생산자
      (logic._by_section_rounds)를 쓴다. 예전에는 이 함수 안에 같은 뜻의
      정렬이 한 벌 더 있었는데, 바로 위 «확인» 문장 위임이 이미 최소 문장
      수를 채워 버려서 그 벌은 한 번도 실행되지 않았다(죽은 코드). 그래서
      실측 실행의 요약 3건이 그대로 본문 첫 문장 서명이었다.
    ★ 요약이 빌 위험은 그대로 0이다 — 순서만 바뀌고 «쓸 수 있는 문장 집합»은
      같기 때문이다. 본문 재사용을 막는 근사 복제 검사는 이 경로에 걸지
      않는다. 여기 오는 문장은 «정의상» 본문 문장이라, 검사를 걸면 요약이
      통째로 비고 이 함수의 존재 이유(빈 요약 차단 방지)가 사라진다.

    ``excluded_keys``: 앞 단계의 수치 안전 검사가 «뺀» 문장의 정규화 본문.
    그 문장이 이 보충으로 되살아나는 자리를 막는다.
    ``accept``: 후보가 «요약 잣대»를 통과하는지 보는 술어. 본문 잣대와 요약
    잣대가 다르기 때문에, 본문에 남았다는 사실만으로 요약에 실을 수 없다.
    ``section_by_key``: 정규화 본문 → 소유 장. 주면 «장당 최대 1개» 계약을
    두 경로 모두에서 지킨다(`docs/출력물 기준/00_핵심_요약/README.md` 절차 4).
    """

    chosen = _supplement_summary(
        summary, report, excluded_keys=excluded_keys, accept=accept,
        section_by_key=section_by_key,
    )
    if len(chosen) >= SUMMARY_MIN_SENTENCES:
        return chosen
    return _supplement_summary_any_grade(
        chosen, report, excluded_keys=excluded_keys, accept=accept,
        section_by_key=section_by_key,
    )


def _legacy_summary_stage(
    verified: ComposedReport,
    *,
    writer_ask: AskFn,
    body_numeric_filtering: NumericSafetyFiltering,
    summary_diagnostics: list[dict] | None = None,
) -> tuple[ComposedReport, int, NumericSafetyFiltering]:
    """검증된 본문 문장 중 AI가 고른 3~5문장으로 핵심 요약을 채운다.

    흐름:
        ① 후보 만들기 — 본문 문장 중 «요약 잣대»를 통과한 것만 모은다.
           수치 안전 검사를 뒤가 아니라 «앞»에 걸어, 통과하지 못할 문장이
           애초에 요약 후보로 나가지 않게 한다.
        ② 고르기 — AI 호출 1회. 후보 번호만 답하게 하고 번호만 읽는다.
        ③ 보충 — 못 골랐거나 모자라면 검증된 본문 문장으로 채운다(규칙).
        ④ 수치 안전 재검사 — ①의 술어와 같은 재료를 쓰므로 정상적으로는
           아무것도 빠지지 않는다. 두 잣대가 갈라지면 조용히 새는 대신
           여기서 빠지고 경고가 남는다(fail-closed 뒷문).

    ★ 요약 «재검증»(verify_sentences) 호출이 없다. 요약 문장은 본문에서
      글자 그대로 가져온 것이고, 그 본문은 이미 verify_report를 통과했다.
      같은 문장을 두 번 검수하면 호출값만 쓰고 잣대가 둘로 갈라진다 —
      실측(4차 멀티캠퍼스)에서 재검수가 초안 4문장 중 3문장을 지웠고 남은
      1문장도 수치 안전 검사가 지워 최종 기여가 0문장이었다.

    ``summary_diagnostics``가 주어지면 원문·오류문·인용 id 없이 단계별
    «개수»와 도달 단계만 하나의 dict로 기록한다(계약:
    ``src.shared.report_quality.composition_diagnostic_constants``). 실행하지
    못한 단계의 개수는 ``None``으로 남겨 실제 0건과 구분한다. 외부(비분해)
    예외가 나면 그 지점까지 채운 필드만 호출자 목록에 이미 남아 있다 —
    이 함수는 그 값을 되돌리거나 지우지 않는다.
    """

    record: dict[str, object] | None = None
    if summary_diagnostics is not None:
        record = {
            "step": SUMMARY_STEP,
            "경로": "legacy",
            "도달단계": "시작",
            "본문후보수": sum(
                len(section.sentences) for section in verified.sections
            ),
            "초안수": None,
            # ★ «검수» 두 칸은 이제 영구히 None·False다 — 요약 재검증 단계
            #   자체가 없어졌기 때문이다. 0으로 적으면 «검수했는데 다 떨어졌다»
            #   처럼 읽혀 실제로 안 돈 단계와 구분이 사라진다. 계약 필드는
            #   그대로 두어 옛 실행 기록과 같은 모양으로 남긴다.
            "검수후수": None,
            "첫보충후수": None,
            "수치검사후수": None,
            "최종수": None,
            "작성한도도달": False,
            "검수한도도달": False,
        }
        summary_diagnostics.append(record)

    # ★ 후보 잣대는 수치 안전 검사가 요약에 쓰는 술어와 «같은 함수»다.
    #   한 벌로 두어야 앞뒤가 갈라지지 않는다.
    safe_owner_by_fact_id = safe_numeric_owners_by_fact_id(verified.sections)

    def _summary_ready(sentence: ComposedSentence) -> bool:
        return is_release_ready_summary_sentence(
            sentence, safe_owner_by_fact_id=safe_owner_by_fact_id
        )

    candidates = summary_candidates(verified, accept=_summary_ready)
    #: 정규화 본문 → 소유 장. «장당 최대 1개» 계약을 규칙 보충에서도 지킨다.
    section_by_key = {
        _normalized_text(candidate.sentence.text): candidate.section_id
        for candidate in candidates
    }
    #: 후보가 «몇 개 장»에 걸쳐 있는가. 장당 최대 1개라 요약이 채울 수 있는
    #: 문장 수의 상한이 곧 이 값이다.
    candidate_section_count = len({
        candidate.section_id for candidate in candidates
    })
    selected: tuple[ComposedSentence, ...] = ()
    if candidate_section_count < SUMMARY_MIN_SENTENCES:
        # ★ 후보가 «서로 다른 장» 최소 문장 수만큼 없으면 AI를 «부르지 않는다».
        #   장당 최대 1개가 코드 강제이므로(계약 조사 절차 4), 무엇을 골라도
        #   요약이 3문장을 못 채워 출고 검증이 보고서 전체를 막기 때문이다
        #   (계약: 근거가 충분한 결론이 3개 미만이면 억지로 채우지 않는다).
        #   부르면 그 실행의 유료 1회가 결과를 바꾸지 못한 채 사라진다.
        # ★ 조건이 «후보 수»가 아니라 «장 수»인 근거 (2026-09-11 재검토 P3-1) —
        #   후보 4개가 두 장에만 있으면 후보 수로는 통과해 유료 1회를 쓰고도
        #   2문장으로 끝나 어차피 막혔다(실측 재현).
        # 계약 필드에 «후보 수» 칸이 없어 로그로 남긴다 — 요약이 짧게 끝난
        # 실행에서 「본문이 얇아서」인지 「고르기가 실패해서」인지 가른다.
        logger.warning(
            "요약 후보가 %d개 장에만 있다(후보 %d문장·본문 %d문장 중) — 장당 "
            "하나씩 %d문장을 채울 수 없어 고르기 호출을 건너뛴다",
            candidate_section_count,
            len(candidates),
            sum(len(section.sentences) for section in verified.sections),
            SUMMARY_MIN_SENTENCES,
        )
    else:
        # 호출 «횟수» 상한과 요청 로컬 «예약액» 소진을 함께 뜻한다 — 둘 다
        # «이 요청 몫을 다 썼다»일 뿐 돈·계정 장애가 아니라서 처리가 같다.
        try:
            selected = select_summary_sentences(candidates, writer_ask)
        except AskFatalError as error:
            if not getattr(error, "degradable", False):
                raise
            selected = ()
            if record is not None:
                record["작성한도도달"] = True
            logger.warning(
                "요청 AI 한도에 닿아 핵심 요약 문장을 «고르지» 못했다 — "
                "검증을 마친 본문 문장으로 채운다"
            )
    summary_draft_count = len(selected)
    if record is not None:
        record["초안수"] = summary_draft_count
        record["도달단계"] = "작성"

    summary: tuple[ComposedSentence, ...] = selected
    if len(summary) < SUMMARY_MIN_SENTENCES:
        # 보충도 «요약 잣대»를 통과한 문장만 쓴다. 본문에 남았다는 사실만으로
        # 요약에 실을 수 없다 — 본문은 「검수 통과 표식」만으로도 남지만
        # 요약은 구조화 사실과 소유 장 일치를 요구한다.
        # 그리고 «장당 최대 1개»도 여기서 함께 지킨다 — 안 그러면 AI가 고른
        # 장의 다른 문장이 보충으로 다시 들어와 계약이 깨진다.
        summary = _supplement_safe_summary(
            summary, verified,
            accept=_summary_ready,
            section_by_key=section_by_key,
        )
        if record is not None:
            record["첫보충후수"] = len(summary)
            record["도달단계"] = "첫보충"
    before_numeric_safety = tuple(summary)[:SUMMARY_MAX_SENTENCES]
    final = ComposedReport(
        sections=verified.sections,
        summary=before_numeric_safety,
    )
    final, summary_numeric_filtering = enforce_public_numeric_safety(final)
    if record is not None:
        record["수치검사후수"] = len(final.summary)
        record["도달단계"] = "수치검사"
    if len(final.summary) < len(before_numeric_safety):
        # ★ 여기서 뭔가 빠졌다면 후보 술어와 요약 잣대가 갈라졌다는 뜻이다.
        #   다시 보충하지 않는다 — 같은 술어로 고른 문장이 또 걸릴 자리라
        #   길이만 맞추다 같은 구멍을 반복한다. 짧게 나가고 크게 남긴다.
        logger.warning(
            "요약 수치 안전 검사가 %d문장을 뺐다 — 후보 술어와 요약 잣대가 "
            "갈라졌다는 뜻이다",
            len(before_numeric_safety) - len(final.summary),
        )
    if record is not None:
        record["최종수"] = len(final.summary)
        record["도달단계"] = "최종"
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
            # 보도표는 병합 뒤 검수 결과를 반영해 다시 만든다. 이 복사는
            # 병합 도중 표만 누락되는 것을 막으며 공개 승인으로 쓰지 않는다.
            replace(
                by_id[section.section_id], news_rows=section.news_rows,
            )
            if section.section_id in by_id
            else section
            for section in base.sections
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


def _build_name_table(
    fragments: FragmentsInput,
    prepared_evidence: object,
    *,
    enabled: bool,
) -> PortfolioNameTableResult:
    """3장 이름 표를 «장별 근거 소유권 안에서» 만든다.

    ★ 왜 allowed 집합을 넘기나 — 이름 조각은 3장 packet 소속이지만 이 단계에
      오는 조각은 flat union이다. 거르지 않고 인용하면 다른 장 소유의 조각을
      3장 표가 인용하는 줄이 만들어져, 바로 다음 evidence invariant가 보고서
      전체를 막는다. «만들고 나서 걸리는» 대신 애초에 안 만든다.

    ★ 이 표는 본문(`ComposedReport`)을 건드리지 않는다. 작가 카드 옆에 놓는
      «별도의 표»이므로 보충 회차가 3장을 통째로 다시 써도 사라지지 않는다.

    Args:
        fragments: 검증용 조각(대개 flat union).
        prepared_evidence: 장별 packet 준비값. ``None``이면 장별 소유권이
            없는 legacy 경로라 거르지 않는다.
        enabled: 이 실행이 근거 결속 없는 공개를 «할 수 없는» 경로인가.
            ENFORCE_NO_PARTIAL(관계 미결속)은 3장 flow 줄을 방금 통째로
            비운 정책이라, 같은 실행에서 이름 표만 내보내면 그 정책을
            우회하게 된다.

    Returns:
        표 생성 결과. 꺼져 있으면 표 없이 사유도 없는 빈 결과다.
    """

    if not enabled:
        return PortfolioNameTableResult()
    allowed = None
    if prepared_evidence is not None:
        allowed = getattr(
            prepared_evidence, "allowed_fragment_ids_by_section", {}
        ).get(PORTFOLIO_TABLE_SECTION_ID)
    result = build_portfolio_name_table(
        _normalize_fragments(fragments),
        allowed_fragment_ids=allowed,
    )
    if result.table is not None:
        logger.info(
            "3장에 공시 대표 이름 표를 덧붙였습니다 — 이름 %d개(%s)",
            result.table.name_count,
            result.table.counts_by_label,
        )
    elif result.blocked_reason in {
        BLOCKED_NAME_NOT_IN_SOURCE,
        BLOCKED_LABEL_NOT_IN_LOCATION,
    }:
        # 이 두 사유만 «상류가 깨졌다»는 뜻이라 경고로 올린다. 나머지 사유
        # (이름 부족)는 대부분의 회사에서 정상 흐름이다.
        logger.warning(
            "3장 이름 표를 만들지 못했습니다 — 사유 %s", result.blocked_reason
        )
    return result


def _augment_news_blocks(
    report: ComposedReport,
    fragments: FragmentsInput,
    prepared_evidence: object,
    *,
    enabled: bool,
    review_candidates: frozenset[str] = frozenset(),
) -> NewsBlockResult:
    """장 끝 보도표 보강을 «장별 근거 소유권 안에서» 부른다.

    ★ 왜 소유권 표를 통째로 넘기나 — 뉴스 조각은 여러 장 packet에 동시에 실릴
      수 있고, 이 단계에 오는 조각은 flat union이라 어느 장 것인지 조각만 봐서는
      알 수 없다. 소유 밖 조각을 인용하는 행을 만들면 바로 다음 evidence
      invariant가 보고서 «전체»를 막는다. 만들고 나서 걸리는 대신 애초에
      안 만든다.

    ★ packet이 없는 실행은 «부분 보고서» 경로다 — 사전검사가 자료 부족을 보고
      갈래를 내리면 packet을 만들지 않는다. 그 경로에서 표를 통째로 포기하면
      언론 보도가 가장 필요한 실행에서만 보도표가 사라진다(운영 실측:
      ``뉴스_보도표_불가 {"no_section_ownership": 6}``). 그래서 packet이 없을
      때는 조각이 스스로 봉인해 온 의미 칸에서 소유권을 되찾는다
      (`news_ownership_from_claim_slots`). 소유권은 여전히 fail-closed다 —
      의미 칸이 없는 조각은 어느 장에도 속하지 않는다.

    Args:
        report: 도식 검증·이름 카드까지 끝난 본문.
        fragments: 검증용 조각(대개 flat union).
        prepared_evidence: 장별 packet 준비값. ``None``이면 부분 보고서
            경로이므로 조각의 의미 칸에서 소유권 표를 만든다.
        enabled: 이 실행이 공개 표를 «결속할 수 있는» 경로인가
            (`augment_news_blocks`의 같은 이름 인자 설명 참고).
        review_candidates: 첫 작성과 보충 작성에서 본문 검수 대상으로 낸
            뉴스 조각. 최종 본문에 남지 못한 후보는 목록에서도 제외한다.

    Returns:
        최종 본문 기준으로 다시 만든 보도표와 제외 사유.
    """

    normalized = _normalize_fragments(fragments)
    rejected = review_candidates - news_citation_ids(report, normalized)
    normalized = tuple(fragment for fragment in normalized if fragment.fragment_id not in rejected)
    # 재작성된 본문에서 탈락한 후보가 이전 회차의 표로 남지 않도록 재생성한다.
    report = replace(report, sections=tuple(
        replace(section, news_rows=()) if section.news_rows else section
        for section in report.sections
    ))
    if prepared_evidence is not None:
        allowed_by_section = getattr(
            prepared_evidence, "allowed_fragment_ids_by_section", None
        )
    else:
        allowed_by_section = news_ownership_from_claim_slots(normalized)
    result = augment_news_blocks(
        report,
        normalized,
        allowed_fragment_ids_by_section=allowed_by_section,
        enabled=enabled,
    )
    if rejected:
        result = replace(result, blocked_counts_by_reason=(
            *result.blocked_counts_by_reason, ("body_review_rejected", len(rejected)),
        ))
    if result.added:
        logger.info(
            "언론 보조 보도표를 %d개 장에 %d행 붙였습니다 (%s)",
            result.section_count,
            result.row_count,
            dict(result.row_counts_by_section),
        )
    elif result.blocked_counts_by_reason:
        logger.warning(
            "언론 보조 보도표를 못 붙였습니다 — 사유별 %s",
            dict(result.blocked_counts_by_reason),
        )
    return result


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


def _notice_only_report(notice: str) -> ComposedReport:
    """아홉 장 전부를 «문장 없음 + 안내»로 둔 본문. 장을 지우지 않는다."""

    return ComposedReport(
        sections=tuple(
            ComposedSection(section_id=section_id, sentences=(), notice=notice)
            for section_id in SECTION_IDS
        ),
        summary=(),
    )


def _fallback_blocked(error: AskFatalError) -> bool:
    """전역 취소·소유권 상실·대기 시간 초과·배포 epoch 변경은 우회하지 않는다.

    이 원인들은 자료원·제공자 장애가 아니라 «이 실행이 더는 결과를 내면 안
    된다»는 신호다. 확보 근거 보고서로 내려가면 중복 과금·소유권 충돌을 만든다.
    """

    return isinstance(
        error.cause, (GenerationCoordinationError, EngineBuildIdentityChangedError)
    )


def _degradable_on_fallback(ask: Optional[AskFn], sink: list[AskFatalError]) -> Optional[AskFn]:
    """재작성·재검수 호출의 전역 장애를 «강등 가능»으로 바꿔 검증 본문을 지킨다.

    재작성은 «거짓» 판정 문장을 살려 보려는 선택적 다듬기라, 못 하면 그 문장만
    제거되고 나머지 검증 문장은 남는다(verify_report의 degradable 갈래). 돈·제공자
    장애도 확보 근거 전환에서는 같은 처분이 정직하다 — 원래 예외는 ``sink``에
    남겨 전환 사유로 쓴다. 전역 취소·epoch 변경은 그대로 올린다.
    """

    if ask is None:
        return None

    def call(prompt: str) -> str:
        try:
            return ask(prompt)
        except AskFatalError as error:
            if _fallback_blocked(error) or error.degradable:
                raise
            sink.append(error)
            raise AskFatalError(
                error.cause, call_limit=True, request_budget=error.request_budget,
            ) from error

    return call


def _degraded_reason_of(error: AskFatalError) -> str:
    """AI 전역 장애를 닫힌 전환 사유로 바꾼다. 예산 feature를 import하지 않는다."""

    cause_kind = type(error.cause).__name__
    if error.request_budget or cause_kind == "ProviderBudgetExceeded":
        return DEGRADED_REASON_REQUEST_BUDGET_EXHAUSTED
    if error.call_limit:
        return DEGRADED_REASON_CALL_LIMIT
    return DEGRADED_REASON_PROVIDER_UNAVAILABLE


def _is_quality_stop(decision: RecoveryDecision) -> bool:
    """회복 정책의 중단이 «품질 하한» 때문인가(구조·증거 결함이면 False)."""

    return bool(decision.quality_problem_codes) or (
        decision.reason_code in QUALITY_DERIVED_STOP_REASON_CODES
    )


def _rule_summary_stage(
    verified: ComposedReport,
    body_numeric_filtering: NumericSafetyFiltering,
) -> tuple[ComposedReport, NumericSafetyFiltering]:
    """AI 없이 검증 본문 문장으로만 요약을 채운다(0~5문장).

    ``_legacy_summary_stage``의 «고르기 AI» 단계만 뺀 같은 규칙이다 — 같은
    요약 잣대·같은 «장당 최대 1개»·같은 수치 안전 재검사를 쓴다. 후보가
    3개 장에 못 미치면 짧게 나가고, 출고 검증은 확보 근거 정책이 그 길이를
    허용한다(shared/report_quality/output_validation.summary_floor_relaxed).
    """

    safe_owner_by_fact_id = safe_numeric_owners_by_fact_id(verified.sections)

    def _summary_ready(sentence: ComposedSentence) -> bool:
        return is_release_ready_summary_sentence(
            sentence, safe_owner_by_fact_id=safe_owner_by_fact_id
        )

    candidates = summary_candidates(verified, accept=_summary_ready)
    section_by_key = {
        _normalized_text(candidate.sentence.text): candidate.section_id
        for candidate in candidates
    }
    summary = _supplement_safe_summary(
        (), verified, accept=_summary_ready, section_by_key=section_by_key,
    )
    final = ComposedReport(
        sections=verified.sections,
        summary=tuple(summary)[:SUMMARY_MAX_SENTENCES],
    )
    final, summary_numeric_filtering = enforce_public_numeric_safety(final)
    return final, body_numeric_filtering.merged(summary_numeric_filtering)


def _table_cite_is_bound(
    table: Optional[PerformanceTable], fragments: FragmentsInput,
) -> bool:
    """표의 cite 번호가 이번 입력 조각에 실제로 있는가. 없으면 표를 싣지 않는다."""

    if table is None:
        return False
    raw_number = citation_number(str(table.cite or ""))
    if not raw_number:
        return False
    numbers = set(citation_numbers_for_fragments(fragments).values())
    return int(raw_number) in numbers


def _notice_only_sections(final: ComposedReport) -> dict[str, str]:
    """문장이 하나도 없고 안내만 남은 장 → 안내문. 파이프라인 빈 등록부 guard용."""

    return {
        section.section_id: section.notice
        for section in final.sections
        if not section.sentences and section.notice
    }


def _apply_evidence_available_policy(
    rendered: Report,
    *,
    availability: EvidenceAvailability,
    degraded_reason: str,
    extra_reasons: Sequence[str] = (),
    notice_only: Mapping[str, str] | None = None,
) -> Report:
    """확보 근거 보고서의 등급·공개 정책·확인 범위 안내를 한 번에 붙인다.

    ★ 여기서 바꾸는 것은 등급(항상 부분 완성)·정책 값·«미제공 사유» 문장뿐이다.
      본문·표·인용·요약의 글자는 하나도 바뀌지 않는다.
    """

    reasons = list(rendered.shortfall_reasons)
    reasons.extend(availability.coverage_lines)
    summary_count = len(rendered.summary_items)
    if summary_count < SUMMARY_MIN_SENTENCES:
        reasons.append(
            SUMMARY_NOTICE_EMPTY
            if summary_count == 0
            else SUMMARY_NOTICE_THIN.format(count=summary_count)
        )
    if degraded_reason in {
        DEGRADED_REASON_REQUEST_BUDGET_EXHAUSTED,
        DEGRADED_REASON_CALL_LIMIT,
        DEGRADED_REASON_PROVIDER_UNAVAILABLE,
    }:
        reasons.append(SHORTFALL_AI_DEGRADED)
    reasons.extend(extra_reasons)
    unique_reasons = list(dict.fromkeys(reason for reason in reasons if reason))
    # 문장이 하나도 없는 장은 안내를 ``empty_reason``에도 병기한다 — 파이프라인
    # 빈 등록부 guard가 «안내뿐인 장»을 기계적으로 알아보게 하려는 것이다.
    # 화면·PDF는 무봉인 v2 보고서의 prose만 그리므로 prose 쪽 안내는 그대로 둔다
    # (render는 lines에도 같은 줄을 복사하므로 lines 유무로는 가릴 수 없다).
    notices = dict(notice_only or {})
    sections = [
        replace(section, empty_reason=notices[section.cell])
        if section.cell in notices and not section.tables and not section.fact_ids
        else section
        for section in rendered.sections
    ]
    return replace(
        rendered,
        grade=Grade.PARTIAL,
        sections=sections,
        shortfall_reasons=unique_reasons,
        publication_policy=PublicationPolicy.EVIDENCE_AVAILABLE.value,
    )


def _finish_evidence_available(
    company_name: str,
    verified: ComposedReport,
    fragments: FragmentsInput,
    performance_table: Optional[PerformanceTable],
    *,
    availability: EvidenceAvailability,
    degraded_reason: str,
    degraded_cause_kind: str,
    ai_stages_skipped: tuple[str, ...],
    downgraded_from: str,
    tail_already_applied: bool,
    corp_type: str,
    generated_at: str,
    as_of_date: str,
    analysis_period: str,
    latest_performance_period: str,
    table_presentation: str,
    filing_meta: Optional[FilingMeta],
    composition_tables: tuple[PerformanceTable, ...],
    citation_style: str,
    company_id: str,
    review_diagnostics: list[dict],
    composition_diagnostics: list[dict],
    draft_body_count: int,
    news_review_candidates: frozenset[str] | set[str],
    name_table: object | None = None,
    verified_program_facts: Sequence[FactRecord] = (),
    program_registry_sources: Sequence[Source] = (),
) -> V2RunOutput:
    """검증된 본문(또는 안내뿐인 본문)에서 AI 0회로 확보 근거 보고서를 마무리한다.

    ``tail_already_applied``가 참이면(FULL 후처리에서 내려온 본문) 수치 claim·
    보도표·조사 안내를 다시 붙이지 않는다 — 두 번 붙이면 같은 문장이 겹친다.

    ★ ``verified_program_facts`` / ``program_registry_sources``를 «반드시» 그대로
      받아 renderer에 넘긴다. FULL 후처리에서 내려온 본문에는 프로그램 등록부에
      결속된 문장(``verified_fact_id``가 있는 비교·수치 문장)이 남아 있는데,
      등록부를 빼고 렌더하면 renderer가 그 문장의 짝을 못 찾아
      「프로그램 공개 문장과 비교 FactRecord가 다릅니다」로 멈춘다. 그러면
      품질 하한 «무차감 중단»이 «생성 실패»로 뒤집혀 사용자 화면과 과금
      판정이 함께 틀어진다. 등록부가 없는 AI 0회 경로는 기본값 ``()``로 둔다.
    """

    extra_reasons: list[str] = []
    body = verified
    numeric_filtering: NumericSafetyFiltering
    if tail_already_applied:
        body, numeric_filtering = enforce_public_numeric_safety(body)
    else:
        if performance_table is not None and not _table_cite_is_bound(
            performance_table, fragments
        ):
            # 근거 조각이 없는 표는 인용-부록 1:1을 깨므로 표를 싣지 않고 알린다.
            performance_table = None
            extra_reasons.append(SHORTFALL_TABLE_EVIDENCE_UNBOUND)
        composition_tables = tuple(
            table for table in composition_tables
            if _table_cite_is_bound(table, fragments)
        )
        body = append_past_changes_numeric_claims(
            body, performance_table, fragments, filing_meta,
        )
        body, numeric_filtering = enforce_public_numeric_safety(body)
        if name_table is None:
            name_table = _build_name_table(fragments, None, enabled=True).table
        news_block = _augment_news_blocks(
            body, fragments, None, enabled=True,
            review_candidates=frozenset(news_review_candidates),
        )
        body = news_block.report
    # 확보 근거 보고서도 «같은» 정리를 거친다. FULL에서 내려온 본문
    # (tail_already_applied)은 이미 정리돼 있어 이 호출이 무동작이다(멱등).
    body = sanitize_stray_citation_markers(
        body, fragments, diagnostics=review_diagnostics,
    )
    final, numeric_filtering = _rule_summary_stage(body, numeric_filtering)
    rendered = render_report(
        company_name,
        final,
        fragments,
        performance_table,
        corp_type=corp_type,
        grade=Grade.PARTIAL,
        generated_at=generated_at,
        as_of_date=as_of_date,
        analysis_period=analysis_period,
        latest_performance_period=latest_performance_period,
        table_presentation=table_presentation,
        filing_meta=filing_meta,
        composition_tables=composition_tables,
        citation_style=citation_style,
        company_id=str(company_id).strip(),
        release_mode="",
        verified_program_facts=verified_program_facts,
        program_registry_sources=program_registry_sources,
        name_table=name_table,
    )
    quality_candidate = build_generation_quality_candidate(rendered, final)
    generation_assessment, quality_observation = assess_and_observe_generation(
        quality_candidate, contract_version="",
    )
    log_generation_quality_observation(
        quality_observation, ReleaseMode.SHADOW, logger=logger,
    )
    final_review_diagnostics = final_review_outcomes(final, review_diagnostics)
    rendered = _apply_generation_quality_label(
        rendered, quality_observation, numeric_filtering, final_review_diagnostics,
    )
    rendered = _apply_evidence_available_policy(
        rendered,
        availability=availability,
        degraded_reason=degraded_reason,
        extra_reasons=extra_reasons,
        notice_only=_notice_only_sections(final),
    )
    composed_item_count = max(draft_body_count, _total_sentences(final))
    fragments_collected, fragments_cited = _generation_fragment_counts(
        fragments, rendered,
    )
    generation_metrics = GenerationRunMetrics(
        fragments_collected=fragments_collected,
        fragments_cited=fragments_cited,
        sentences_made=composed_item_count,
        sentences_passed=_total_sentences(final),
    )
    assert_observation_matches_assessment(quality_observation, generation_assessment)
    rendered = replace(
        rendered,
        generation_metrics=generation_metrics,
        quality_observation=quality_observation,
    )
    _log_duplicate_findings(rendered)
    # 남는 출고 검증은 내부 키 노출·인용↔부록·문단 투영뿐이다 — 수량 하한이 아니다.
    validate_v2(rendered)
    return V2RunOutput(
        report=rendered,
        composed_sentences=composed_item_count,
        verified_sentences=_total_sentences(final),
        quality_observation=quality_observation,
        generation_evidence=None,
        generation_metrics=generation_metrics,
        review_diagnostics=final_review_diagnostics,
        degraded_reason=degraded_reason,
        degraded_cause_kind=degraded_cause_kind,
        ai_stages_skipped=tuple(ai_stages_skipped),
        effective_release_mode=ReleaseMode.SHADOW.value,
        downgraded_from_release_mode=downgraded_from,
    )


def compose_evidence_available_report(
    company_name: str,
    fragments: FragmentsInput,
    performance_table: Optional[PerformanceTable],
    *,
    evidence_availability: EvidenceAvailability,
    corp_type: str = "",
    generated_at: str = "",
    as_of_date: str = "",
    analysis_period: str = "",
    latest_performance_period: str = "",
    table_presentation: str = "table",
    filing_meta: Optional[FilingMeta] = None,
    composition_tables: tuple[PerformanceTable, ...] = (),
    citation_style: str = DEFAULT_CITATION_STYLE,
    company_id: str = "",
    review_diagnostics_sink: list[dict] | None = None,
    composition_diagnostics_sink: list[dict] | None = None,
    degraded_reason: str = "",
    degraded_cause_kind: str = "",
) -> V2RunOutput:
    """AI를 한 번도 부르지 않고 확보한 자료만으로 부분 보고서를 만든다.

    사용자 계약(2026-09-14): 분석 대상 회사면 자료가 적어도 보고서가 나온다.
    본문 문장은 «검증을 마친 문장»만 실을 수 있는데 AI를 부르지 않으므로
    산문은 없고, 프로그램이 만든 표·수치 claim·이름 표·보도표·회사 신원과
    장별 안내만 싣는다. 원문 조각을 검수 없이 본문 문장으로 올리지 않는다.

    Args:
        fragments: 빈 값 허용. 있으면 부록·표·보도표의 재료가 된다.
        evidence_availability: 파이프라인이 확인한 자료 확보 상태.
        degraded_reason / degraded_cause_kind: 호출자가 AI 장애를 먼저 알았을 때
            그 사유를 그대로 실어 나른다(파이프라인 단계 기록용).

    Returns:
        V2RunOutput — grade 부분 완성, publication_policy evidence-available-v1.

    Raises:
        V2ValidationError: 내부 키 노출·인용↔부록 불일치 같은 배선 결함뿐이다.
    """

    if not isinstance(evidence_availability, EvidenceAvailability):
        raise TypeError("evidence_availability는 EvidenceAvailability 값이어야 합니다")
    has_fragments = bool(_normalize_fragments(fragments))
    if degraded_reason:
        notice = NOTICE_AI_UNAVAILABLE
    elif has_fragments:
        notice = NOTICE_EVIDENCE_NOT_COMPOSED
    else:
        notice = NOTICE_EVIDENCE_NONE
    return _finish_evidence_available(
        company_name,
        _notice_only_report(notice),
        fragments,
        performance_table,
        availability=evidence_availability,
        degraded_reason=degraded_reason,
        degraded_cause_kind=degraded_cause_kind,
        ai_stages_skipped=(AI_STAGE_COMPOSE_VERIFY,) if degraded_reason else (),
        downgraded_from="",
        tail_already_applied=False,
        corp_type=corp_type,
        generated_at=generated_at,
        as_of_date=as_of_date,
        analysis_period=analysis_period,
        latest_performance_period=latest_performance_period,
        table_presentation=table_presentation,
        filing_meta=filing_meta,
        composition_tables=composition_tables,
        citation_style=citation_style,
        company_id=company_id,
        review_diagnostics=(
            review_diagnostics_sink if review_diagnostics_sink is not None else []
        ),
        composition_diagnostics=(
            composition_diagnostics_sink
            if composition_diagnostics_sink is not None
            else []
        ),
        draft_body_count=0,
        news_review_candidates=frozenset(),
    )


def run_v2(
    company_name: str,
    fragments: FragmentsInput,
    performance_table: Optional[PerformanceTable],
    *,
    writer_ask: AskFn,
    reviewer_ask: AskFn,
    initial_reviewer_ask: Optional[AskFn] = None,
    initial_retry_reviewer_ask: Optional[AskFn] = None,
    rewrite_ask: Optional[AskFn] = None,
    recheck_ask: Optional[AskFn] = None,
    diagram_ask: Optional[AskFn] = None,
    empty_recovery_writer_ask: Optional[AskFn] = None,
    empty_recovery_reviewer_ask: Optional[AskFn] = None,
    empty_recovery_can_start: Optional[Callable[[], bool]] = None,
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
    review_diagnostics_sink: list[dict] | None = None,
    composition_diagnostics_sink: list[dict] | None = None,
    evidence_availability: EvidenceAvailability | None = None,
    evidence_available_fallback: bool = False,
    preserve_on_ask_failure: bool = False,
    _downgraded_from: str = "",
) -> V2RunOutput:
    """엔진 v2 전체 흐름을 한 번 돌려 최종 보고서를 만든다 (04장 3-4절).

    ★ 확보 근거 보고서(2026-09-14 사용자 계약) — 아래 두 인자가 기본값이면
      이 함수의 동작은 종전과 완전히 같다.
        evidence_availability: 파이프라인이 확인한 자료 확보 상태. 주면 결과는
            항상 부분 완성 + publication_policy evidence-available-v1 이고, 요약
            3문장 하한을 두지 않으며, 조각이 하나도 없으면 AI를 부르지 않는다.
        evidence_available_fallback (별칭 preserve_on_ask_failure): 참이면
            «수량 하한·AI 전역 장애» 때문에 예외로 끝나던 자리에서 확보 근거
            보고서로 내려간다 — 작성·검수 중 AI 장애는 미검증 초안을 버리고
            결정론 꼬리로, 도식·요약 단계 장애는 검증 본문을 보존하고, FULL의
            품질 하한 중단은 검증 본문으로 부분 보고서를 만든다. 내부 계약
            결함(구조 결속·생산 증거·manifest)은 여전히 예외로 끝난다.
      결과의 ``effective_release_mode``가 실제 적용 모드다 — FULL 요청이
      내려가면 SHADOW이며 파이프라인은 그 값으로 저장·차감을 다뤄야 한다.

    흐름:
        ① compose_sections — 작가 AI가 9개 장을 산문으로 쓴다 (장 삭제 없음).
        ② verify_report — 출처 실존·수치·의미 검수·라벨 정합을 «문장 단위»로.
        ③ 핵심 요약 — 검증된 본문 문장 중 AI가 고른 3~5문장을 글자 그대로
           싣는다 (AI 호출 1회, 번호만 답한다). 못 골랐거나 모자라면 검증된
           본문 «확인» 문장으로 규칙 보충한다.
        ④ (요약 재검증 단계는 없다 — 고른 문장이 이미 ②를 통과한 본문
           문장이라 다시 검수할 새 글자가 없다.)
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
        initial_reviewer_ask: «최초 본문 검수»만 쓰는 별도 호출자. 그 한 번은
            후보 전부와 근거 배열을 한 응답에 담아야 해서 뒤따르는 재작성·
            재검수·요약 검수보다 큰 출력 여유가 필요하다. 넘기지 않으면
            (None) 예전과 똑같이 reviewer_ask 하나로 전부 처리한다 —
            기존 호출 계약이 그대로 유지된다.
        initial_retry_reviewer_ask: «최초 본문 검수의 파싱 재요청»만 쓰는 별도
            호출자. 재요청은 같은 질문을 형식만 고쳐 다시 받는 것이라 첫 답보다
            크게 길어지지 않는데, 부르는 쪽의 예약액은 «출력 상한»으로 잡힌다.
            첫 답의 실제 출력에 맞춘 작은 상한을 가진 호출자를 여기에 넣으면
            그 한 번의 예약액만 줄어든다(답의 내용·형식 요구는 그대로다).
            (None) 예전과 똑같이 initial_reviewer_ask 로 재요청한다.
        rewrite_ask: «거짓» 판정 문장 재작성 전용 호출자. 재작성은 선택적
            다듬기라 못 해도 그 문장이 제거될 뿐이지만, 뒤따르는 도식 검수·
            요약 작성·요약 검수는 못 하면 보고서에서 통째로 빠진다. 부르는
            쪽이 «그 몫을 남기고 멈추는» 호출자를 넣어 순서를 지킨다.
            (None) 예전과 똑같이 reviewer_ask 를 쓴다.
        recheck_ask: 재작성문 재검수 전용 호출자. 같은 이유로 분리한다.
            (None) 예전과 똑같이 reviewer_ask 를 쓴다.
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
        review_diagnostics_sink: 최종 출고 게이트가 예외를 내도 검수 중간 관측을
            보존할 요청 로컬 목록. 성공 반환의 ``review_diagnostics``와 달리
            보충 뒤 살아남은 후보를 아직 포함할 수 있다.
        composition_diagnostics_sink: 판독 시도와 요약 단계별 개수를 보존할
            요청 로컬 목록. 보고서가 없어도 실행 기록에 전달한다.

    Returns:
        V2RunOutput — 검증 끝난 Report와 초안·생존 문장 수.

    Raises:
        V2ValidationError: 최종 출고 3검사에 걸린 경우 (예: 본문이 통째로 비어
            요약 3문장을 만들 수 없는 경우). 그 외 생성·검증 단계는 예외를
            밖으로 던지지 않는다 (장 삭제·전체 중단 금지 원칙).
    """
    if not isinstance(release_mode, ReleaseMode):
        raise TypeError("release_mode는 ReleaseMode 값이어야 합니다")
    fallback_allowed = bool(evidence_available_fallback or preserve_on_ask_failure)
    if evidence_availability is not None and not isinstance(
        evidence_availability, EvidenceAvailability
    ):
        raise TypeError("evidence_availability는 EvidenceAvailability 값이어야 합니다")
    if (
        evidence_availability is not None or fallback_allowed
    ) and release_mode is ReleaseMode.ENFORCE_NO_PARTIAL:
        raise TypeError("ENFORCE_NO_PARTIAL은 확보 근거 보고서 전환을 지원하지 않습니다")
    if evidence_availability is not None and release_mode is ReleaseMode.SHADOW:
        if not _normalize_fragments(fragments):
            # 조각이 하나도 없으면 작가·검수를 부를 재료가 없다 — AI 0회.
            return compose_evidence_available_report(
                company_name,
                fragments,
                performance_table,
                evidence_availability=evidence_availability,
                corp_type=corp_type,
                generated_at=generated_at,
                as_of_date=as_of_date,
                analysis_period=analysis_period,
                latest_performance_period=latest_performance_period,
                table_presentation=table_presentation,
                filing_meta=filing_meta,
                composition_tables=composition_tables,
                citation_style=citation_style,
                company_id=company_id,
                review_diagnostics_sink=review_diagnostics_sink,
                composition_diagnostics_sink=composition_diagnostics_sink,
            )
    ai_failure: AskFatalError | None = None
    ai_stages_skipped: list[str] = []

    review_diagnostics = (
        review_diagnostics_sink if review_diagnostics_sink is not None else []
    )
    composition_diagnostics = (
        composition_diagnostics_sink if composition_diagnostics_sink is not None else []
    )
    call_recorder: _CallLedgerRecorder | None = None
    writer_for_run = writer_ask
    reviewer_for_run = reviewer_ask
    initial_reviewer_for_run = initial_reviewer_ask
    initial_retry_reviewer_for_run = initial_retry_reviewer_ask
    rewrite_for_run = rewrite_ask
    recheck_for_run = recheck_ask
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
        rewrite_for_run = (
            call_recorder.wrap(
                rewrite_ask, role="reviewer",
                validation_round=ValidationRound.PRIMARY,
                section_ids=("bundled",),
            )
            if rewrite_ask is not None else None
        )
        recheck_for_run = (
            call_recorder.wrap(
                recheck_ask, role="reviewer",
                validation_round=ValidationRound.PRIMARY,
                section_ids=("bundled",),
            )
            if recheck_ask is not None else None
        )
        if initial_reviewer_ask is not None:
            # 최초 본문 검수는 «실제로 보낸 그 호출»이 영수증에 남아야 한다.
            # 감싸지 않으면 FULL 장부에서 검수 1회가 통째로 빠진다.
            initial_reviewer_for_run = call_recorder.wrap(
                initial_reviewer_ask,
                role="reviewer",
                validation_round=ValidationRound.PRIMARY,
                section_ids=("bundled",),
            )
        if initial_retry_reviewer_ask is not None:
            # 재요청 호출자도 «똑같이» 감싼다. 장부의 role 계수가 FULL의
            # 「검수 1회 고정」을 지키는 장치이므로(두 번째 reviewer 호출은
            # 장부에서 RuntimeError → _safe_ask 가 None 으로 삼킨다), 여기만
            # 감싸지 않으면 FULL에서 재요청이 «영수증 없이» 나가 그 고정이
            # 풀린다. 감싸 두면 지금 동작이 그대로 유지된다.
            initial_retry_reviewer_for_run = call_recorder.wrap(
                initial_retry_reviewer_ask,
                role="reviewer",
                validation_round=ValidationRound.PRIMARY,
                section_ids=("bundled",),
            )

    rewrite_failures: list[AskFatalError] = []
    if fallback_allowed and release_mode is ReleaseMode.SHADOW:
        rewrite_for_run = _degradable_on_fallback(rewrite_for_run, rewrite_failures)
        recheck_for_run = _degradable_on_fallback(recheck_for_run, rewrite_failures)

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
                if fallback_allowed:
                    # 유료 호출 «전»이다 — 같은 입력을 SHADOW 흐름으로 한 번만
                    # 돌려 확보 근거 보고서를 만든다(AI 비용은 한 번뿐).
                    return run_v2(
                        company_name,
                        fragments,
                        performance_table,
                        writer_ask=writer_ask,
                        reviewer_ask=reviewer_ask,
                        initial_reviewer_ask=initial_reviewer_ask,
                        initial_retry_reviewer_ask=initial_retry_reviewer_ask,
                        rewrite_ask=rewrite_ask,
                        recheck_ask=recheck_ask,
                        diagram_ask=diagram_ask,
                        empty_recovery_writer_ask=empty_recovery_writer_ask,
                        empty_recovery_reviewer_ask=empty_recovery_reviewer_ask,
                        empty_recovery_can_start=empty_recovery_can_start,
                        corp_type=corp_type,
                        grade=Grade.PARTIAL,
                        generated_at=generated_at,
                        as_of_date=as_of_date,
                        analysis_period=analysis_period,
                        latest_performance_period=latest_performance_period,
                        table_presentation=table_presentation,
                        filing_meta=filing_meta,
                        composition_tables=composition_tables,
                        citation_style=citation_style,
                        release_mode=ReleaseMode.SHADOW,
                        section_evidence_packets=None,
                        company_id=company_id,
                        review_diagnostics_sink=review_diagnostics,
                        composition_diagnostics_sink=composition_diagnostics,
                        evidence_availability=(
                            evidence_availability
                            or EvidenceAvailability(COLLECTION_STATE_PARTIAL)
                        ),
                        evidence_available_fallback=True,
                        _downgraded_from=release_mode.value,
                    )
                raise V2ValidationError(
                    (
                        "report_recovery:"
                        + FINAL_GATE_DETAIL_PREFLIGHT_OFFICIAL_EVIDENCE_INSUFFICIENT,
                    )
                )

    baseline_date = as_of_date or None
    recovery_targets: tuple[str, ...] = ()
    recovery_sources = {}
    recovery_enabled = (
        release_mode is ReleaseMode.SHADOW and (fallback_allowed or evidence_availability is not None)
        and empty_recovery_writer_ask is not None and empty_recovery_reviewer_ask is not None
        and empty_recovery_can_start is not None
    )
    try:
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
            # 장별 도식 «행 수»를 작성 직후·장 근거 정리 직후에 남긴다. 이 기록이
            # 없으면 어떤 장의 도식 0줄이 «작가가 안 냈다»인지 «우리가 걸렀다»인지
            # 되짚을 방법이 없다.
            composition_diagnostics=composition_diagnostics,
        )
        draft, news_supplemented = supplement_news_candidates(
            draft, _normalize_fragments(verification_fragments),
            prepared_evidence.allowed_fragment_ids_by_section if prepared_evidence else None,
        )
        news_review_candidates = news_citation_ids(draft, _normalize_fragments(verification_fragments))
        news_review_rejections = []
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
                # 이 자리는 «두 번째» 정리다(작성 단계에서 이미 한 번 돈다). 뉴스
                # 보충이 끼어든 뒤의 줄 수라 앞 기록과 값이 다를 수 있으므로 따로
                # 남긴다 — 기록은 관측이며 중복 제거하지 않는다.
                record_flow_row_counts(
                    composition_diagnostics, draft,
                    stage=DIAGRAM_STAGE_SECTION_EVIDENCE,
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
                draft, _normalize_fragments(verification_fragments),
                derived_ratio_diagnostics=composition_diagnostics,
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
        rewrite_gate = None
        if recovery_enabled:
            recovery_sources = recovery_evidence(_normalize_fragments(verification_fragments))
            attempted = {section.section_id for section in draft.sections if section.sentences}

            def rewrite_gate(empty_ids: tuple[str, ...]) -> bool:
                nonlocal recovery_targets
                eligible = tuple(section_id for section_id in SECTION_IDS
                                 if section_id in empty_ids and section_id in attempted
                                 and section_id in recovery_sources)[:MAX_EMPTY_RECOVERY_SECTIONS]
                if eligible and empty_recovery_can_start():
                    recovery_targets = eligible
                    composition_diagnostics.append({"step": EMPTY_RECOVERY_STEP, "상태": "문장재작성대신예약",
                                                    "대상장": list(eligible)})
                    return False
                return True

        # ② 본문 검증 (검수 — 문장 단위 제거/강등만, 장 삭제 없음)
        # ★ 보고서 기준일을 검증기에 함께 넘긴다. 이 값이 없으면 임원 재직 가드가
        #   날짜 문턱 없이 이탈 «표지»만 보고 판정해, 「기준일 이후에 물러날 예정」인
        #   임원 문장까지 근거 없음으로 뺀다. render 메타로만 쓰이던 값을 판정에도
        #   쓰는 것이라 형식은 그대로 ISO(YYYY-MM-DD)다.
        baseline_date = as_of_date or None
        if prepared_evidence is None:
            verified = verify_report(
                draft, verification_fragments, performance_table, reviewer_for_run,
                diagnostics=review_diagnostics,
                initial_ask=initial_reviewer_for_run,
                initial_retry_ask=initial_retry_reviewer_for_run,
                rewrite_ask=rewrite_for_run,
                recheck_ask=recheck_for_run,
                sentence_rewrite_gate=rewrite_gate,
                protocol_diagnostics=composition_diagnostics,
                baseline_date=baseline_date,
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
                diagnostics=review_diagnostics,
                initial_ask=initial_reviewer_for_run,
                initial_retry_ask=initial_retry_reviewer_for_run,
                rewrite_ask=rewrite_for_run,
                recheck_ask=recheck_for_run,
                protocol_diagnostics=composition_diagnostics,
                baseline_date=baseline_date,
            )
            _assert_composed_report_evidence_invariant(
                verified,
                prepared_evidence.allowed_fragment_ids_by_section,
                packet_union_ids,
                stage="post-verify",
            )

    except AskFatalError as error:
        if not fallback_allowed or _fallback_blocked(error):
            raise
        # 작성·검수 도중 AI 전역 장애 — 검수를 못 마친 초안은 «절대» 싣지
        # 않는다. 안내뿐인 본문으로 바꾸고 확보한 표·조각으로 마무리한다.
        ai_failure = error
        ai_stages_skipped.append(AI_STAGE_COMPOSE_VERIFY)
        logger.warning(
            "AI 작성·검수 중단(kind=%s) — 미검증 초안을 버리고 확보 자료로 마무리한다",
            type(error.cause).__name__,
        )
        draft = _notice_only_report(NOTICE_AI_UNAVAILABLE)
        verified = draft
        draft_body_count = 0
        news_supplemented = ()
        news_review_candidates = set()
        news_review_rejections = []
        diagram_problems = ()
    if rewrite_failures and ai_failure is None:
        # 재작성 단계의 전역 장애 — verify_report가 미다듬 문장만 제거했고
        # 검증을 마친 나머지 문장은 그대로 남았다. 전환 사유만 기록한다.
        ai_failure = rewrite_failures[0]
        ai_stages_skipped.append(AI_STAGE_REWRITE)

    # ②-b 사실 단일 소유 강제 — 여러 장에 반복된 같은 사실을 소유 장 하나만
    #     남기고 뺀다. 요약 «앞»에 둔다 — 곧 사라질 문장을 요약 재료로 고르면
    #     본문에 없는 요약이 남는다.
    verified = retain_verified_news(
        verified, _normalize_fragments(verification_fragments),
        review_input=draft, diagnostics=news_review_rejections,
    )
    # ★ 조각을 함께 넘긴다. 안 넘기면 «같은 문서의 다른 조각» 중복(3장↔7장
    #   수주 문장 실측)이 그대로 남는다 — 문서 열쇠가 없으면 그 판정을 못 한다.
    verified, moved_sentences = drop_cross_section_duplicates(
        verified, fragments=_normalize_fragments(verification_fragments),
        # ★ 장이 «들고 있지 않은» 표(실적표·매출 구성표)가 어느 장에 실리는지
        #   함께 넘긴다. 안 넘기면 그 장이 비었을 때 안내문이 「그쪽으로
        #   모았습니다」만 말하고 바로 아래에 표가 실려 화면과 어긋난다.
        sections_with_tables=sections_with_program_tables(
            performance_table, composition_tables
        ),
    )
    if moved_sentences:
        logger.info("장 간 중복 %d문장을 소유 장으로 모았습니다", moved_sentences)
    if recovery_targets and ai_failure is None and not rewrite_failures:
        still_empty = {section.section_id for section in verified.sections if not section.sentences}
        targets = tuple(section_id for section_id in recovery_targets if section_id in still_empty)
        if targets and empty_recovery_can_start():
            try:
                verified = recover_empty_sections(
                    company_name, verified, targets=targets, evidence=recovery_sources,
                    writer=empty_recovery_writer_ask, reviewer=empty_recovery_reviewer_ask,
                    performance_table=performance_table, baseline_date=baseline_date,
                    diagnostics=review_diagnostics, protocol_diagnostics=composition_diagnostics,
                    comparison_fragments=_normalize_fragments(verification_fragments),
                    rejected_fingerprints={
                        section.section_id: frozenset(rejected_sentence_fingerprint(sentence.text)
                                                     for sentence in section.sentences)
                        for section in draft.sections if section.section_id in targets
                    },
                )
            except AskFatalError as error:
                if _fallback_blocked(error):
                    raise
                # 선택적 복구 실패가 이미 검증된 본문을 없애지 않게 한다.
                composition_diagnostics.append({"step": EMPTY_RECOVERY_STEP, "상태": "호출중단",
                                                "대상장": list(targets), "오류종류": (
                                                    "호출한도" if error.call_limit else
                                                    "요청예산" if error.request_budget else "제공자오류")})
            except (TypeError, ValueError):
                composition_diagnostics.append({"step": EMPTY_RECOVERY_STEP, "상태": "복구형식실패",
                                                "대상장": list(targets)})
            draft_body_count += sum(
                item.get("작성문장수", 0) for item in composition_diagnostics
                if item.get("step") == EMPTY_RECOVERY_STEP and item.get("상태") == "작성완료"
            )
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
        try:
            verified, diagram_problems = check_diagrams(
                verified,
                _normalize_fragments(verification_fragments),
                diagram_ask or reviewer_ask,
                diagnostics=review_diagnostics,
                derived_ratio_diagnostics=composition_diagnostics,
                baseline_date=baseline_date,
            )
        except AskFatalError as error:
            if not fallback_allowed or _fallback_blocked(error):
                raise
            # 도식 의미 검수를 못 하면 «미확인 화살표만» 빼고 본문은 그대로 둔다.
            ai_failure = ai_failure or error
            ai_stages_skipped.append(AI_STAGE_DIAGRAM)
            hidden = sum(len(section.flow_rows) for section in verified.sections)
            verified = replace(
                verified,
                sections=tuple(
                    replace(section, flow_rows=()) for section in verified.sections
                ),
            )
            diagram_problems = (
                *diagram_problems,
                f"AI 장애로 의미 검수를 못 한 관계 flow {hidden}행 공개 제외",
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

    # ②-c-2 3장 「회사가 공시한 대표 이름」 표 — 조각의 글자와 인용만 투영한
    # 결정적 표 하나를 작가 카드 옆에 놓는다. 작가가 이름을 썼든 안 썼든
    # 항상 만든다 — 같은 회사를 두 번 돌렸을 때 화면이 달라지면 안 된다.
    name_table_result = _build_name_table(
        verification_fragments,
        prepared_evidence,
        enabled=(
            release_mode is ReleaseMode.SHADOW or prepared_evidence is not None
        ),
    )
    name_table = name_table_result.table

    def _downgrade_after_write(
        reason: str,
        *,
        stages: tuple[str, ...] = (),
    ) -> V2RunOutput:
        """FULL이 작성 뒤 품질 하한에 걸렸을 때 검증 본문으로 부분 보고서를 낸다."""

        return _finish_evidence_available(
            company_name,
            verified,
            verification_fragments,
            performance_table,
            availability=(
                evidence_availability
                or EvidenceAvailability(COLLECTION_STATE_PARTIAL)
            ),
            degraded_reason=reason,
            degraded_cause_kind="",
            ai_stages_skipped=(*ai_stages_skipped, *stages),
            downgraded_from=release_mode.value,
            tail_already_applied=True,
            corp_type=corp_type,
            generated_at=generated_at,
            as_of_date=as_of_date,
            analysis_period=analysis_period,
            latest_performance_period=latest_performance_period,
            table_presentation=table_presentation,
            filing_meta=filing_meta,
            composition_tables=composition_tables,
            citation_style=citation_style,
            company_id=company_id,
            review_diagnostics=review_diagnostics,
            composition_diagnostics=composition_diagnostics,
            draft_body_count=draft_body_count,
            news_review_candidates=news_review_candidates,
            name_table=name_table,
            # 본문은 FULL 작성본 그대로라 프로그램 등록부에 결속된 문장이 살아
            # 있다. 같은 등록부를 넘겨야 renderer가 그 문장의 짝을 찾는다 —
            # 빼면 무차감 중단이 생성 실패로 뒤집힌다.
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
    # 직접 인용·자동 보강 모두 최종 본문 검수와 수치 안전을 통과해야 한다.
    # 검수 탈락 후보의 목록 우회 공개는 모든 모드에서 같은 기준으로 막는다.
    news_block = _augment_news_blocks(
        verified, verification_fragments, prepared_evidence,
        enabled=True, review_candidates=news_review_candidates,
    )
    verified = news_block.report
    # ★ 본 경로의 본문이 확정된 직후 인용 아닌 대괄호 숫자를 글자로 굳힌다.
    #   여기서 한 번 정리하면 아래의 요약 고르기·렌더·봉인·출고 검증이 모두
    #   «같은 글자»를 본다. 보충 경로는 이 뒤에 문장을 더하므로 그쪽 병합본에도
    #   같은 호출이 한 번 더 있다(멱등이라 겹쳐도 값이 바뀌지 않는다).
    verified = sanitize_stray_citation_markers(
        verified, verification_fragments, diagnostics=review_diagnostics,
    )
    if prepared_evidence is not None:
        _assert_composed_report_evidence_invariant(
            verified,
            prepared_evidence.allowed_fragment_ids_by_section,
            packet_union_ids,
            stage="numeric-safety",
        )
    # ★ 이 삭제는 보고서 «안»에만 적히고 서버 로그엔 흔적이 없었다.
    #   실측: 카드사에서 최소 16문장이 여기서 사라졌는데 로그로는 안 보였다.
    if body_numeric_filtering.removed_section_counts:
        logger.warning(
            "수치 안전 제외: 구조화 근거 없는 수치·날짜 문장 %d개 (장별 %s)",
            sum(n for _, n in body_numeric_filtering.removed_section_counts),
            ", ".join(
                f"{sid}:{n}" for sid, n in body_numeric_filtering.removed_section_counts
            ),
        )

    # ③ 요약. 두 갈래 모두 «본문에 없던 말을 새로 만들지 않는다». SHADOW는
    # 검증된 본문 문장 중 AI가 고른 3~5문장을 쓰고(AI 1회), 엄격 모드는
    # 렌더러가 만든 검증 FactRecord에 정확히 결속된 본문 문장을 0원으로
    # 재사용한다. 고른 문장은 본문 문장 그 자체라 요약은 그 본문 문장의
    # 결속과 «같은 수준»으로 결속되고, 그래서 요약 재검증 호출이 없다.
    # (본문이 결속을 못 만든 실행에서는 요약도 결속되지 않는다 — 요약이
    #  본문보다 느슨해지지 않을 뿐, 없는 결속을 만들어 주지는 않는다.)
    if release_mode is ReleaseMode.SHADOW:
        try:
            final, summary_draft_count, numeric_filtering = _legacy_summary_stage(
                verified,
                writer_ask=writer_ask,
                body_numeric_filtering=body_numeric_filtering,
                summary_diagnostics=composition_diagnostics,
            )
        except AskFatalError as error:
            if not fallback_allowed or _fallback_blocked(error):
                raise
            # 고르기 AI를 못 부르면 검증 본문 문장으로만 규칙 요약을 채운다.
            ai_failure = ai_failure or error
            ai_stages_skipped.append(AI_STAGE_SUMMARY)
            final, numeric_filtering = _rule_summary_stage(
                verified, body_numeric_filtering,
            )
            summary_draft_count = 0
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
            name_table=name_table,
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
            if fallback_allowed:
                return _downgrade_after_write(DEGRADED_REASON_QUALITY_FLOOR)
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
            name_table=name_table,
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
        name_table=name_table,
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
    log_generation_quality_observation(
        quality_observation,
        release_mode,
        logger=logger,
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
            supplement_draft, supplement_news = supplement_news_candidates(
                supplement_draft, _normalize_fragments(verification_fragments),
                prepared_evidence.allowed_fragment_ids_by_section,
            )
            news_supplemented = tuple(dict.fromkeys((*news_supplemented, *supplement_news)))
            news_review_candidates |= news_citation_ids(
                supplement_draft, _normalize_fragments(verification_fragments),
            )
            draft_body_count += _total_sentences(supplement_draft)
            supplement_draft, supplement_diagram_problems = check_diagram_numbers(
                supplement_draft,
                _normalize_fragments(verification_fragments),
                derived_ratio_diagnostics=composition_diagnostics,
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
                diagnostics=review_diagnostics,
                protocol_diagnostics=composition_diagnostics,
                baseline_date=baseline_date,
            )
            supplement_verified, supplement_moved = drop_cross_section_duplicates(
                retain_verified_news(
                    supplement_verified, _normalize_fragments(verification_fragments),
                    review_input=supplement_draft, diagnostics=news_review_rejections,
                ),
                # ★ 본 경로와 같은 조각을 넘긴다 — 보충 경로만 문서 열쇠가
                #   없으면 같은 중복이 보충 장에서만 살아남는다.
                fragments=_normalize_fragments(verification_fragments),
                # ★ 표 자리도 본 경로와 같은 값을 넘긴다. 한쪽만 넘기면
                #   보충 경로에서만 옛 안내문이 나간다.
                sections_with_tables=sections_with_program_tables(
                    performance_table, composition_tables
                ),
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
            news_block = _augment_news_blocks(
                merged_body, verification_fragments, prepared_evidence,
                enabled=True, review_candidates=news_review_candidates,
            )
            merged_body = news_block.report
            # 본 경로와 «같은» 정리를 병합본에도 건다. 비대상 장은 이미 정리된
            # 글자라 이 호출이 아무것도 바꾸지 않는다(멱등) — 아래 비대상 장
            # 불변 검사가 그대로 통과한다.
            merged_body = sanitize_stray_citation_markers(
                merged_body, verification_fragments, diagnostics=review_diagnostics,
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
            # ★ 이름 표는 보충 뒤에 다시 만들 필요가 없다 — 본문이 아니라
            #   «별도의 표»라서, 3장이 통째로 다시 쓰여도 사라지지 않는다.
            #   재료(3장 packet 조각)도 그대로다. 앞선 설계는 이름을 작가
            #   카드 «안»에 넣었기 때문에 보충 회차마다 다시 붙여야 했다.
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
                name_table=name_table,
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
                name_table=name_table,
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
                name_table=name_table,
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
                    # 근거 결속 계약이 그 장의 후보를 «전부» 제외해 내용이
                    # 그대로인 경우를 사유 코드와 함께 적는다. 적지 않으면
                    # 결속 검사가 종전대로 «무동작 보충»으로 보고 닫는다.
                    unchanged_sections=supplement_unchanged_sections(
                        approved_section_ids=targets,
                        base_section_sha256s=primary_receipt.section_sha256s,
                        result_section_sha256s=(
                            public_structure_seal.section_sha256s
                        ),
                        review_diagnostics=review_diagnostics,
                    ),
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
                if fallback_allowed and _is_quality_stop(recovery_decision):
                    return _downgrade_after_write(
                        DEGRADED_REASON_QUALITY_FLOOR,
                        stages=(AI_STAGE_FULL_SUPPLEMENT,),
                    )
                _raise_recovery_stop(
                    recovery_decision.reason_code,
                    recovery_decision.quality_problem_codes,
                )
            if not supplement_summary_release_ready:
                # 두 번째 후보의 manifest·render·품질 평가·receipt·정책 결정을
                # 모두 다시 만든 뒤에야 닫는다. 조기 예외로 파생물 재계산을
                # 건너뛰거나 세 번째 보충으로 흐르지 않는다.
                if fallback_allowed:
                    return _downgrade_after_write(
                        DEGRADED_REASON_QUALITY_FLOOR,
                        stages=(AI_STAGE_FULL_SUPPLEMENT,),
                    )
                _raise_recovery_stop("supplement_summary_insufficient")
            validation_receipts = (primary_receipt, supplement_receipt)
        elif recovery_decision.action is RecoveryAction.RELEASE_COMPLETE:
            validation_receipts = (primary_receipt,)
        elif recovery_decision.action is RecoveryAction.STOP_NO_CHARGE:
            if fallback_allowed and _is_quality_stop(recovery_decision):
                return _downgrade_after_write(DEGRADED_REASON_QUALITY_FLOOR)
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
    final_review_diagnostics = final_review_outcomes(final, review_diagnostics)
    degraded_reason = ""
    if ai_failure is not None:
        degraded_reason = _degraded_reason_of(ai_failure)
    elif _downgraded_from:
        degraded_reason = DEGRADED_REASON_EVIDENCE_UNREACHABLE
    evidence_available_applied = (
        release_mode is ReleaseMode.SHADOW
        and (
            evidence_availability is not None
            or ai_failure is not None
            or bool(_downgraded_from)
        )
    )
    if release_mode is ReleaseMode.SHADOW:
        rendered = _apply_generation_quality_label(
            rendered,
            quality_observation,
            numeric_filtering,
            final_review_diagnostics,
        )
        if evidence_available_applied:
            rendered = _apply_evidence_available_policy(
                rendered,
                availability=(
                    evidence_availability
                    or EvidenceAvailability(COLLECTION_STATE_PARTIAL)
                ),
                degraded_reason=degraded_reason,
                notice_only=_notice_only_sections(final),
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

    # 3장 카드가 packet의 대표 이름을 실제로 썼는지 «판정만» 한다.
    # 안 썼어도 카드를 지어내지 않는다 — 기록만 남기고 그대로 내보낸다.
    name_usage = portfolio_name_usage(
        final, _normalize_fragments(verification_fragments)
    )
    if name_usage.unused:
        logger.warning(
            "3장 packet에 대표 이름 %d개(%s)가 갔는데 카드가 하나도 쓰지 않았습니다",
            name_usage.name_count,
            name_usage.counts_by_label,
        )

    return V2RunOutput(
        report=rendered,
        composed_sentences=composed_item_count,
        verified_sentences=_total_sentences(final),
        quality_observation=quality_observation,
        generation_evidence=generation_evidence,
        generation_metrics=generation_metrics,
        diagram_drop_reasons=recorded_diagram_drop_reasons,
        unused_portfolio_name_count=(
            name_usage.name_count if name_usage.unused else 0
        ),
        unused_portfolio_name_counts_by_label=(
            tuple(sorted(name_usage.counts_by_label.items()))
            if name_usage.unused
            else ()
        ),
        portfolio_name_table_name_count=(
            name_table.name_count if name_table is not None else 0
        ),
        portfolio_name_table_counts_by_label=(
            name_table.counts_by_label if name_table is not None else ()
        ),
        portfolio_name_table_titles=(
            name_table.table_titles if name_table is not None else ()
        ),
        portfolio_name_table_blocked_reason=name_table_result.blocked_reason,
        news_block_row_counts_by_section=news_block.row_counts_by_section,
        news_block_blocked_counts_by_reason=news_block.blocked_counts_by_reason,
        news_usage_diagnostics=news_usage_diagnostics(
            verified, _normalize_fragments(verification_fragments), news_supplemented,
            review_candidates=news_review_candidates,
            review_rejections=news_review_rejections,
        ),
        review_diagnostics=final_review_diagnostics,
        degraded_reason=degraded_reason if evidence_available_applied else "",
        degraded_cause_kind=(
            type(ai_failure.cause).__name__
            if ai_failure is not None and evidence_available_applied
            else ""
        ),
        ai_stages_skipped=tuple(ai_stages_skipped),
        effective_release_mode=release_mode.value,
        downgraded_from_release_mode=_downgraded_from,
    )


__all__ = [
    "V2RunOutput",
    "compose_evidence_available_report",
    "run_v2",
]
