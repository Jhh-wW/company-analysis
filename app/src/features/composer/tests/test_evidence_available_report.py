"""확보 근거 보고서(2026-09-14 사용자 계약) — 자료가 적어도 보고서가 나온다.

지키는 것:
  ① 조각 0개 + 자료 확보 상태가 주어지면 AI를 한 번도 부르지 않고 아홉 장
     안내 + 회사 신원만 담은 부분 보고서가 나온다(v2 출고 검증·PDF 통과).
  ② 검증된 문장이 한 장뿐이어도 «요약 3문장» 하한 때문에 전체가 막히지 않는다.
  ③ 요약 고르기 단계에서 AI 전역 장애(돈 문제)가 나도 이미 검증한 본문은 보존된다.
  ④ 작성 도중 AI 전역 장애가 나면 미검증 초안은 버리고 확보 자료로 마무리한다.
  ⑤ 기본 인자(전환 없음)에서는 예전처럼 예외로 끝난다 — 동작 불변.
  ⑥ 정책 값은 저장 payload를 거쳐도 살아남고, 웹·PDF 재검사도 같은 값으로 통과한다.
"""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from src.core import news_intake_switch

from src.features.composer.constants import (
    DEFAULT_CITATION_STYLE,
    DEGRADED_REASON_PROVIDER_UNAVAILABLE,
    DEGRADED_REASON_QUALITY_FLOOR,
    DEGRADED_REASON_REQUEST_BUDGET_EXHAUSTED,
    GRADE_CONFIRMED,
    NOTICE_AI_UNAVAILABLE,
    NOTICE_EVIDENCE_NONE,
    NOTICE_EVIDENCE_NOT_COMPOSED,
    SECTION_IDS,
    SUMMARY_NOTICE_EMPTY,
)
from src.features.composer.evidence_availability import (
    COVERAGE_LINE_NONE,
    COVERAGE_SCOPE_PREFIX,
    EvidenceAvailability,
    UnverifiedScope,
    unverified_scope_from_attempt,
)
from src.features.composer import pipeline as composer_pipeline
from src.features.composer.pipeline import (
    V2RunOutput,
    _finish_evidence_available,
    compose_evidence_available_report,
    run_v2,
)
from src.features.composer.port import (
    AskFatalError,
    ComposedReport,
    ComposedSection,
)
from src.features.composer.render import ENGINE_V2_SCHEMA_VERSION
from src.features.composer.tests.test_pipeline import (
    _REVIEW_NUMBER_RE,
    _FakeReviewer,
    _FakeWriter,
    _raw_fragments,
)
from src.features.composer.validate import V2ValidationError
from src.features.export_pdf.logic import build_pdf
from src.features.pipeline.port import Grade
from src.features.storage import reports as report_storage
from src.shared.report_evidence.constants import ReleaseMode
from src.shared.report_claim_policy import CLAIM_SLOTS_BY_SECTION
from src.shared.report_quality.constants import VERIFIED_PROSE_CLAIM_TYPE
from src.shared.report_quality.models import PublicationPolicy


_PARTIAL = EvidenceAvailability(
    "partial",
    (
        unverified_scope_from_attempt(
            "dart_business_report", "TRUNCATED", "deadline_exceeded"
        ),
    ),
)


def _one_sentence_writer() -> _FakeWriter:
    """아홉 장이 같은 사실 한 문장만 쓰는 작가 — 본문이 1문장으로 모인다."""

    return _FakeWriter(
        section_response=json.dumps(
            {
                "문장들": [
                    {
                        "글": "가나다전자는 반도체 검사 장비 전문기업이다.",
                        "인용": ["1"],
                        "등급": GRADE_CONFIRMED,
                    }
                ]
            },
            ensure_ascii=False,
        )
    )


def _assert_evidence_available_shape(output: V2RunOutput) -> None:
    report = output.report
    assert report.schema_version == ENGINE_V2_SCHEMA_VERSION
    assert report.grade is Grade.PARTIAL
    assert report.publication_policy == PublicationPolicy.EVIDENCE_AVAILABLE.value
    assert [section.cell for section in report.sections] == list(SECTION_IDS)
    assert output.effective_release_mode == ReleaseMode.SHADOW.value


# ══════════════════════════════════════════════════════════
# ① 조각 0개
# ══════════════════════════════════════════════════════════


def test_조각이_없으면_AI_없이_회사_신원과_안내만_담은_보고서가_나온다() -> None:
    writer = _FakeWriter()
    reviewer = _FakeReviewer()

    output = run_v2(
        "가나다전자",
        {},
        None,
        writer_ask=writer,
        reviewer_ask=reviewer,
        corp_type="상장사",
        company_id="00123456",
        evidence_availability=EvidenceAvailability("none"),
    )

    assert writer.prompts == [] and reviewer.prompts == []
    _assert_evidence_available_shape(output)
    report = output.report
    assert report.company == "가나다전자"
    assert report.summary_items == []
    assert report.citations == []
    assert COVERAGE_LINE_NONE in report.shortfall_reasons
    assert SUMMARY_NOTICE_EMPTY in report.shortfall_reasons
    for section in report.sections:
        assert section.prose_lines == [(NOTICE_EVIDENCE_NONE, "")]
        # 안내뿐인 장은 empty_reason에도 병기한다(파이프라인 빈 등록부 guard용).
        assert section.empty_reason == NOTICE_EVIDENCE_NONE
        assert not section.fact_ids and not section.tables
    assert output.degraded_reason == ""
    assert output.ai_stages_skipped == ()


def test_결정론_진입점도_같은_모양이고_PDF와_저장을_통과한다() -> None:
    output = compose_evidence_available_report(
        "가나다전자",
        (),
        None,
        evidence_availability=EvidenceAvailability("none"),
        corp_type="상장사",
        company_id="00123456",
        as_of_date="2026-09-14",
    )

    _assert_evidence_available_shape(output)
    # ⑥ 저장 payload 왕복 — 정책 값이 살아남아야 웹·PDF 재검사가 같은 판정을 한다.
    payload = report_storage.report_to_dict(output.report)
    restored = report_storage.report_from_dict(payload)
    assert restored.publication_policy == PublicationPolicy.EVIDENCE_AVAILABLE.value
    assert restored.grade is Grade.PARTIAL
    assert restored.summary_items == []
    # PDF 조립은 validate_v2를 다시 태운다 — 요약 0문장도 이 정책에서는 통과한다.
    pdf_bytes = build_pdf(restored)
    assert pdf_bytes.startswith(b"%PDF")


# ══════════════════════════════════════════════════════════
# ② 얇은 본문
# ══════════════════════════════════════════════════════════


def test_검증_문장이_한_장뿐이어도_전체가_막히지_않는다() -> None:
    writer = _one_sentence_writer()

    output = run_v2(
        "가나다전자",
        _raw_fragments(),
        None,
        writer_ask=writer,
        reviewer_ask=_FakeReviewer(),
        evidence_availability=_PARTIAL,
    )

    _assert_evidence_available_shape(output)
    report = output.report
    body = [text for section in report.sections for text, _ in section.prose_lines]
    assert any("반도체 검사 장비 전문기업" in text for text in body)
    assert len(report.summary_items) <= 2
    assert any(
        reason.startswith(COVERAGE_SCOPE_PREFIX) and "사업보고서" in reason
        for reason in report.shortfall_reasons
    )
    # 요약 «고르기» AI는 후보가 3개 장 미만이라 부르지 않는다(비용 보존).
    assert not any("핵심 요약" in prompt for prompt in writer.prompts)


def test_기본_인자면_얇은_본문은_예전처럼_예외로_끝난다() -> None:
    with pytest.raises(V2ValidationError):
        run_v2(
            "가나다전자",
            _raw_fragments(),
            None,
            writer_ask=_one_sentence_writer(),
            reviewer_ask=_FakeReviewer(),
        )


# ══════════════════════════════════════════════════════════
# ③④ AI 전역 장애
# ══════════════════════════════════════════════════════════


class _SummaryStageMoneyFailure(_FakeWriter):
    """장 작성은 정상, 핵심 요약 «고르기»에서 돈 문제로 죽는 작가."""

    def __call__(self, prompt: str) -> str:
        if "핵심 요약" in prompt:
            self.prompts.append(prompt)
            raise AskFatalError(RuntimeError("계정 결제 장애(시험용)"))
        return super().__call__(prompt)


def test_요약_단계_돈_문제여도_전환이_허용되면_검증_본문을_보존한다() -> None:
    writer = _SummaryStageMoneyFailure()

    output = run_v2(
        "가나다전자",
        _raw_fragments(),
        None,
        writer_ask=writer,
        reviewer_ask=_FakeReviewer(),
        preserve_on_ask_failure=True,
    )

    _assert_evidence_available_shape(output)
    report = output.report
    body = [text for section in report.sections for text, _cite in section.prose_lines]
    assert any("가나다전자는 반도체 검사 장비 전문기업" in text for text in body), (
        "검수를 통과한 본문 문장이 그대로 남아야 한다"
    )
    assert not any(section.empty_reason for section in report.sections if section.prose_lines and "[1]" in section.prose_lines[0][0])
    assert output.degraded_reason == DEGRADED_REASON_PROVIDER_UNAVAILABLE
    assert output.degraded_cause_kind == "RuntimeError"
    assert "summary_selection" in output.ai_stages_skipped
    assert report.summary_items, "규칙 요약이 검증 본문 문장으로 채워져야 한다"
    # 규칙 요약은 검증 본문 문장 그대로다 — 새 글자를 만들지 않는다.
    for item in report.summary_items:
        assert any(text.startswith(item.text[:12]) for text in body), item.text


class _BudgetDiesAtThirdSection(_FakeWriter):
    """세 번째 장에서 요청 예산 소진으로 죽는 작가."""

    def __call__(self, prompt: str) -> str:
        if self.section_calls >= 2 and "핵심 요약" not in prompt:
            self.prompts.append(prompt)
            raise AskFatalError(
                RuntimeError("요청 예약액 소진(시험용)"), request_budget=True
            )
        return super().__call__(prompt)


def test_작성_도중_예산_소진이면_미검증_초안을_버리고_확보_자료로_마무리한다() -> None:
    writer = _BudgetDiesAtThirdSection()
    reviewer = _FakeReviewer()

    output = run_v2(
        "가나다전자",
        _raw_fragments(),
        None,
        writer_ask=writer,
        reviewer_ask=reviewer,
        evidence_available_fallback=True,
    )

    _assert_evidence_available_shape(output)
    report = output.report
    assert reviewer.prompts == [], "검수 없이 초안을 실을 수 없으므로 검수도 부르지 않는다"
    for section in report.sections:
        assert section.prose_lines == [(NOTICE_AI_UNAVAILABLE, "")]
    assert output.degraded_reason == DEGRADED_REASON_REQUEST_BUDGET_EXHAUSTED
    assert output.ai_stages_skipped == ("compose_verify",)
    assert report.citations == []


def test_기본_인자면_AI_전역_장애는_예전처럼_밖으로_나간다() -> None:
    with pytest.raises(AskFatalError):
        run_v2(
            "가나다전자",
            _raw_fragments(),
            None,
            writer_ask=_BudgetDiesAtThirdSection(),
            reviewer_ask=_FakeReviewer(),
        )


# ══════════════════════════════════════════════════════════
# 자료 확보 상태 계약
# ══════════════════════════════════════════════════════════


def test_확인을_마친_시도는_미확인_범위가_되지_않는다() -> None:
    assert unverified_scope_from_attempt("dart_audit_report", "OK", "list_query_ok") is None
    assert unverified_scope_from_attempt("dart_audit_report", "MISSING", "list_query_missing") is None
    scope = unverified_scope_from_attempt(
        "dart_business_report", "TRUNCATED", "deadline_exceeded",
        document_title="사업보고서 (2025.12)",
    )
    assert scope is not None
    assert scope.kind == "dart_business_report:truncated:deadline_exceeded"
    assert scope.label.startswith("사업보고서 (2025.12): 일부만 확인했습니다")


def test_안내문이_내부_키_모양이면_거절한다() -> None:
    with pytest.raises(ValueError):
        UnverifiedScope(kind="x", label="dart_business_report")
    with pytest.raises(ValueError):
        EvidenceAvailability("unknown")


# ══════════════════════════════════════════════════════════
# 전환을 «허용하지 않는» 전역 신호 — 취소·소유권 상실·배포 epoch 변경
# ══════════════════════════════════════════════════════════


class _CancelledAtSummary(_FakeWriter):
    """요약 고르기에서 실행 취소(조정 오류)로 죽는 작가."""

    def __init__(self, cause: BaseException) -> None:
        super().__init__()
        self._cause = cause

    def __call__(self, prompt: str) -> str:
        if "핵심 요약" in prompt:
            raise AskFatalError(self._cause)
        return super().__call__(prompt)


@pytest.mark.parametrize(
    "cause",
    [
        pytest.param(
            __import__(
                "src.shared.generation_coordination", fromlist=["GenerationWaitCancelled"]
            ).GenerationWaitCancelled("취소(시험용)"),
            id="wait_cancelled",
        ),
        pytest.param(
            __import__(
                "src.shared.generation_coordination", fromlist=["GenerationCoordinationError"]
            ).GenerationCoordinationError("owner lease 상실(시험용)"),
            id="coordination",
        ),
        pytest.param(
            __import__(
                "src.shared.engine_build_identity", fromlist=["EngineBuildIdentityChangedError"]
            ).EngineBuildIdentityChangedError("배포 epoch 변경(시험용)"),
            id="epoch_changed",
        ),
    ],
)
def test_전역_취소나_epoch_변경은_전환을_허용해도_그대로_올린다(cause) -> None:
    with pytest.raises(AskFatalError) as caught:
        run_v2(
            "가나다전자",
            _raw_fragments(),
            None,
            writer_ask=_CancelledAtSummary(cause),
            reviewer_ask=_FakeReviewer(),
            preserve_on_ask_failure=True,
            evidence_availability=_PARTIAL,
        )
    assert caught.value.cause is cause


def test_긴_문서_제목도_안내_상한_200자를_넘기지_않는다() -> None:
    scope = unverified_scope_from_attempt(
        "dart_business_report", "FAILED", "document_fetch_failed",
        document_title="가" * 400,
    )
    assert scope is not None
    assert len(scope.label) <= 200
    assert scope.label.endswith(": 확인하지 못했습니다 (원문 내려받기 실패)")


# ══════════════════════════════════════════════════════════
# 재작성 단계의 돈 문제 — 검증을 마친 문장은 남고 미다듬 문장만 빠진다
# ══════════════════════════════════════════════════════════


class _FirstConfirmedFalseReviewer(_FakeReviewer):
    """첫 장의 첫(«확인») 문장만 «거짓»으로 판정해 재작성 대상 하나를 만든다.

    verify_report는 «확인» 등급의 거짓 판정만 재작성하고, «해석»의 거짓은 바로
    뺀다. 다른 여덟 장의 확인 문장은 «참»이라 검증 본문으로 남는다.
    """

    def __call__(self, prompt: str) -> str:
        import re as _re

        self.prompts.append(prompt)
        grouped = _re.findall(
            r"\[(\d+)\] \(장: ([^,]+), 종류: ([^,]+), 인용: ([^)]+)\)", prompt,
        )
        if grouped:
            verdicts = []
            first_section = grouped[0][1]
            marked = False
            for number, section_id, _kind, citations in grouped:
                falsify = section_id == first_section and not marked
                marked = marked or falsify
                verdicts.append(
                    {
                        "번호": int(number),
                        "장": section_id,
                        "근거": _re.findall(r"조각 (\d+)", citations),
                        "결과": "거짓" if falsify else "참",
                    }
                )
            return json.dumps({"판정": verdicts}, ensure_ascii=False)
        numbers = [int(value) for value in _REVIEW_NUMBER_RE.findall(prompt)]
        return json.dumps(
            {
                "판정": [
                    {"번호": number, "결과": "거짓" if index == 0 else "참"}
                    for index, number in enumerate(numbers)
                ]
            },
            ensure_ascii=False,
        )


def _money_failure_ask(prompt: str) -> str:
    raise AskFatalError(RuntimeError("계정 결제 장애(시험용)"))


def test_재작성_단계_돈_문제여도_검증을_마친_문장은_남는다() -> None:
    writer = _FakeWriter()
    reviewer = _FirstConfirmedFalseReviewer()

    output = run_v2(
        "가나다전자",
        _raw_fragments(),
        None,
        writer_ask=writer,
        reviewer_ask=reviewer,
        rewrite_ask=_money_failure_ask,
        recheck_ask=_money_failure_ask,
        preserve_on_ask_failure=True,
    )

    _assert_evidence_available_shape(output)
    body = [text for section in output.report.sections for text, _ in section.prose_lines]
    assert any("가나다전자는 반도체 검사 장비 전문기업" in text for text in body)
    assert output.degraded_reason == DEGRADED_REASON_PROVIDER_UNAVAILABLE
    assert output.degraded_cause_kind == "RuntimeError"
    assert "sentence_rewrite" in output.ai_stages_skipped


def test_기본_인자면_재작성_돈_문제는_예전처럼_밖으로_나간다() -> None:
    with pytest.raises(AskFatalError):
        run_v2(
            "가나다전자",
            _raw_fragments(),
            None,
            writer_ask=_FakeWriter(),
            reviewer_ask=_FirstConfirmedFalseReviewer(),
            rewrite_ask=_money_failure_ask,
            recheck_ask=_money_failure_ask,
        )


# ══════════════════════════════════════════════════════════
# FULL 요청이 작성 뒤 품질 하한에 걸리면 검증 본문으로 부분 보고서를 낸다
# ══════════════════════════════════════════════════════════


class _StrictThinWriter(_FakeWriter):
    """장마다 «확인» 1문장만 쓰는 얇은 작가 — 품질 하한에 반드시 걸린다.

    두 시험이 같은 후보를 써야 «전환이 일어나는 조건»이 한 벌로만 유지된다.
    복제하면 한쪽만 고쳐져 다른 쪽이 전환 없이 초록불이 된다.
    """

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        section_id = SECTION_IDS[self.section_calls]
        text = (
            "고객 존중과 회사의 법인 정체성을 공식 자료에서 확인했다.",
            "고객에게 가치를 전달하는 판매 경로를 공식 자료에서 확인했다.",
            "고객이 선택할 제품 묶음과 역할을 공식 자료에서 확인했다.",
            "회사가 과거에 실행한 변화 흐름을 공식 자료에서 확인했다.",
            "현재 해결해야 할 운영 과제와 대응을 공식 자료에서 확인했다.",
            "앞으로 추진한다고 밝힌 전략 조건을 공식 자료에서 확인했다.",
            "협력사와 유통사의 운영 연결 관계를 공식 자료에서 확인했다.",
            "조직의 의사결정과 문화 원칙을 공식 자료에서 확인했다.",
            "경쟁 비교 기준과 회사의 차별점을 공식 자료에서 확인했다.",
        )[self.section_calls]
        self.section_calls += 1
        return json.dumps(
            {
                "문장들": [
                    {
                        "글": text,
                        "인용": ["2"],
                        "등급": GRADE_CONFIRMED,
                        "주장슬롯": CLAIM_SLOTS_BY_SECTION[section_id][0],
                    }
                ]
            },
            ensure_ascii=False,
        )


def test_FULL_품질_하한_미달은_전환이_허용되면_검증_본문으로_부분_보고서를_낸다() -> None:
    from src.features.composer.tests.test_pipeline import (
        _strict_fragments,
        _strict_packet_set,
    )

    writer = _StrictThinWriter()

    output = run_v2(
        "가나다전자",
        _strict_fragments(),
        None,
        writer_ask=writer,
        reviewer_ask=_FakeReviewer(),
        release_mode=ReleaseMode.FULL,
        section_evidence_packets=_strict_packet_set(),
        company_id="00123456",
        build_identity_sha256="b" * 64,
        evidence_available_fallback=True,
    )

    _assert_evidence_available_shape(output)
    assert output.downgraded_from_release_mode == ReleaseMode.FULL.value
    assert output.degraded_reason == "quality_floor"
    assert output.generation_evidence is None
    report = output.report
    assert report.release_mode == ""
    assert report.public_projection is None
    body = [text for section in report.sections for text, _ in section.prose_lines]
    assert any("공식 자료에서 확인했다" in text for text in body), "검증 본문이 보존돼야 한다"
    # 작가 9회 뒤 유료 보충을 부르지 않는다 — 보충으로도 하한을 못 넘는 얇은 후보다.
    assert len(writer.prompts) == 9


# ══════════════════════════════════════════════════════════
# ⑦ 전환 경로는 packet에 봉인된 프로그램 등록부를 renderer까지 가져간다
#
# FULL 작성본에는 packet이 봉인한 «프로그램 공개 문장»(``verified_fact_id``가
# 붙은 문장)이 남는다. 품질 하한 전환이 그 등록부를 빼고 렌더하면 renderer가
# 문장의 짝을 못 찾아 ValueError로 멈추고, «과금 없는 중단»이 «생성 실패»로
# 뒤집힌다. 아래 두 시험이 그 둘을 나눠 지킨다 — 하나는 렌더가 실제로 짝을
# 맞추는지(동작), 하나는 전환 호출부가 등록부를 실제로 넘기는지(배선).
# ══════════════════════════════════════════════════════════


@pytest.fixture
def _news_intake_on(monkeypatch: pytest.MonkeyPatch):
    """합성 프로그램 근거의 언론 Source 봉인 검사는 뉴스 스위치가 켜져야 통과한다."""

    monkeypatch.setenv(news_intake_switch.NEWS_INTAKE_ENV_NAME, "1")
    news_intake_switch._reset_process_news_intake_switch_for_tests()  # noqa: SLF001
    yield
    news_intake_switch._reset_process_news_intake_switch_for_tests()  # noqa: SLF001


def _program_body(program) -> ComposedReport:
    """프로그램 공개 문장 하나만 든 FULL 후처리 모양의 검증 본문."""

    return ComposedReport(
        sections=tuple(
            ComposedSection(
                section_id=section_id,
                sentences=(
                    program.sentences
                    if section_id == program.section_id
                    else ()
                ),
                notice=(
                    ""
                    if section_id == program.section_id
                    else NOTICE_EVIDENCE_NOT_COMPOSED
                ),
            )
            for section_id in SECTION_IDS
        ),
        summary=(),
    )


def test_전환_마무리는_프로그램_등록부_문장을_짝지어_렌더한다(
    _news_intake_on: None,
) -> None:
    """등록부를 함께 받으면 프로그램 문장이 그대로 부분 보고서에 실린다.

    등록부를 빼면 renderer가 「프로그램 공개 문장과 비교 FactRecord가 다릅니다」로
    멈춘다 — 그때 FULL 실행은 무차감 중단이 아니라 생성 실패가 된다.
    """

    from src.features.composer.tests.test_supplementary_program_evidence import (
        _program_evidence,
    )

    # strict FULL fixture가 1~8번을 쓰므로 9번을 골라 부록 번호 충돌을 피한다.
    program_claim = "회사는 신규 사업을 시작했다고 밝혔다."
    program = _program_evidence(program_claim, number=9)

    output = _finish_evidence_available(
        "가나다전자",
        _program_body(program),
        program.source_fragments,
        None,
        availability=_PARTIAL,
        degraded_reason=DEGRADED_REASON_QUALITY_FLOOR,
        degraded_cause_kind="",
        ai_stages_skipped=(),
        downgraded_from=ReleaseMode.FULL.value,
        tail_already_applied=True,
        corp_type="상장사",
        generated_at="",
        as_of_date="",
        analysis_period="",
        latest_performance_period="",
        table_presentation="table",
        filing_meta=None,
        composition_tables=(),
        citation_style=DEFAULT_CITATION_STYLE,
        company_id="00123456",
        review_diagnostics=[],
        composition_diagnostics=[],
        draft_body_count=1,
        news_review_candidates=frozenset(),
        name_table=None,
        verified_program_facts=program.facts,
        program_registry_sources=program.registry_sources,
    )

    _assert_evidence_available_shape(output)
    report = output.report
    body = [text for section in report.sections for text, _ in section.prose_lines]
    assert any(program_claim in text for text in body), (
        "프로그램 등록부 문장이 전환 본문에 그대로 남아야 한다"
    )
    assert program.facts[0].fact_id in {
        fact.fact_id for fact in report.fact_records
    }, "전환 보고서의 fact 장부가 packet 봉인 fact를 그대로 이어야 한다"


class _MarkedEmptyRegistry(tuple):
    """빈 등록부라 동작은 그대로지만 «어디서 온 값인지»는 식별되는 표식.

    빈 tuple 두 개는 서로 같고 파이썬이 같은 객체로 돌려주기도 해서, 값 비교로는
    «호출부가 준비값을 넘겼는지»와 «빈 값을 새로 만들었는지»를 가릴 수 없다.
    비어 있으므로 렌더 동작에는 아무 영향이 없고 신원만 확인할 수 있다.
    """


def test_품질_하한_전환은_packet_프로그램_등록부를_렌더에_넘긴다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FULL 전환 호출부가 준비된 등록부를 그대로 render 인자로 넘기는지 단정한다.

    ★ 시험 안에서 등록부를 따로 만들어 렌더만 확인하면(위 시험), 호출부가 그
      인자를 아예 빠뜨린 배선 결함은 지나간다. 여기서는 준비 단계가 만든 바로
      그 값이 전환 렌더까지 도달했는지를 실제 호출 인자의 신원으로 확인한다.
    """

    from src.features.composer.tests.test_pipeline import (
        _strict_fragments,
        _strict_packet_set,
    )

    facts_marker = _MarkedEmptyRegistry()
    sources_marker = _MarkedEmptyRegistry()
    real_prepare = composer_pipeline._prepare_section_evidence_packets  # noqa: SLF001

    def _prepare_with_marked_registry(*args: object, **kwargs: object):
        return replace(
            real_prepare(*args, **kwargs),
            program_facts=facts_marker,
            program_sources=sources_marker,
        )

    monkeypatch.setattr(
        composer_pipeline,
        "_prepare_section_evidence_packets",
        _prepare_with_marked_registry,
    )

    real_render = composer_pipeline.render_report
    render_calls: list[dict] = []

    def _recording_render(*args: object, **kwargs: object):
        render_calls.append(kwargs)
        return real_render(*args, **kwargs)

    monkeypatch.setattr(composer_pipeline, "render_report", _recording_render)

    output = run_v2(
        "가나다전자",
        _strict_fragments(),
        None,
        writer_ask=_StrictThinWriter(),
        reviewer_ask=_FakeReviewer(),
        release_mode=ReleaseMode.FULL,
        section_evidence_packets=_strict_packet_set(),
        company_id="00123456",
        build_identity_sha256="b" * 64,
        evidence_available_fallback=True,
    )

    assert output.degraded_reason == DEGRADED_REASON_QUALITY_FLOOR
    assert render_calls, "render_report 호출이 기록돼야 한다"
    downgrade_call = render_calls[-1]
    assert downgrade_call["release_mode"] == "", (
        "마지막 렌더가 전환(부분 보고서) 렌더여야 한다"
    )
    assert downgrade_call.get("verified_program_facts") is facts_marker, (
        "전환 렌더가 준비 단계의 프로그램 FactRecord 등록부를 그대로 받아야 한다"
    )
    assert downgrade_call.get("program_registry_sources") is sources_marker, (
        "전환 렌더가 준비 단계의 프로그램 Source 등록부도 그대로 받아야 한다"
    )
