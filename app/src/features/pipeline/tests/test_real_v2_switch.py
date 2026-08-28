"""ENGINE_V2 스위치와 v2 분기 연결을 못 박는다 (엔진 v2 소단계 3-4b).

★ 여기서 지키는 것:
  ① 미설정(기본)이면 v1 경로 그대로 — composer 분기를 «호출조차» 하지 않는다.
  ② 정확히 "1"일 때만 v2 분기를 탄다. "0" 같은 다른 값은 v1이다.
  ③ 분기 함수(_run_v2_composer)가 v1 자산(조각·실적표·기간 라벨)을 재사용해
     composer.run_v2에 넘기고, 결과를 RunResult로 올바르게 매핑한다.
  ④ v2 출고 검증 실패는 GATE_STOPPED + publish_blocked 사유 코드다.
  ⑤ v2 ask 클로저는 기존 계량 client 경계를 지난다 (비용 0원 위장 금지).

★ 진짜 엔진·AI·네트워크는 부르지 않는다 — test_real_cache의 FakeEngine 재사용.
"""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest

import src.features.composer.pipeline as composer_pipeline
from src.core.provider_gateway import attempt_context
from src.core.provider_gateway.attempt_context import ProviderAttemptCallbacks
from src.features.budget import provider_budget
from src.features.composer.port import AskFatalError
from src.features.composer.validate import V2ValidationError
from src.features.pipeline import real
from src.features.pipeline.port import (
    CompanyCard,
    Grade,
    Outcome,
    Report,
    RunResult,
    UserInput,
)
from src.features.pipeline.tests.test_real_cache import (
    CORP_ID,
    JOB,
    POSTING,
    FakeEngine,
)
from src.features.report_standard.constants import CANONICAL_SCHEMA_VERSION
from src.shared.final_gate_diagnostics import FINAL_GATE_REASON_PUBLISH_BLOCKED

_V2_SENTINEL_MESSAGE = "v2-분기-표식"
_DATE = dt.date(2026, 8, 24)


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch) -> FakeEngine:
    """진짜 엔진 대신 가짜를 끼운다 — 이 시험에서 돈이 나갈 길이 없다."""
    fake = FakeEngine()
    monkeypatch.setattr(real, "_engine", lambda: fake)
    monkeypatch.setattr(
        real,
        "_company_catalog",
        lambda: (
            (CORP_ID, "가나다전자", "", "000001", "20260819"),
            ("00999999", "베타전자", "", "999999", "20260819"),
        ),
    )
    return fake


@pytest.fixture(autouse=True)
def _paid_provider_budget_context():
    """직접 RealPipeline 시험도 웹 worker와 같은 유료 문맥에서 실행한다."""
    callbacks = ProviderAttemptCallbacks(
        lambda _provider, _operation, _reserved: object(),
        lambda _token: None,
        lambda _token: None,
        lambda _token, _observation: None,
    )
    with provider_budget.activate(100_000.0), attempt_context.activate(callbacks):
        yield


def _card() -> CompanyCard:
    return CompanyCard(
        legal_name="가나다전자",
        typed_name="가나다전자",
        address="서울특별시 강남구 테헤란로 1",
        ceo="홍길동",
        founded="20000101",
        ref=CORP_ID,
    )


def _run() -> RunResult:
    user_input = UserInput(
        company="가나다전자", job=JOB, region="서울 강남구", posting_text=POSTING
    )
    return real.RealPipeline().run(user_input, _card())


def _v2_branch_recorder(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """_run_v2_composer 호출을 기록만 하고 표식 결과를 돌려주는 가짜."""
    calls: list[dict[str, Any]] = []

    def fake_branch(**kwargs: Any) -> RunResult:
        calls.append(kwargs)
        return RunResult(
            outcome=Outcome.REPORT, message=_V2_SENTINEL_MESSAGE, charged=True
        )

    monkeypatch.setattr(real, "_run_v2_composer", fake_branch)
    return calls


# ══════════════════════════════════════════════════════════
# ①② 스위치
# ══════════════════════════════════════════════════════════


def test_ENGINE_V2_미설정이면_v1_경로_그대로다(
    engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(real.ENGINE_V2_ENV_NAME, raising=False)
    calls = _v2_branch_recorder(monkeypatch)

    result = _run()

    assert calls == []  # v2 분기를 호출조차 하지 않는다
    assert result.outcome is Outcome.REPORT
    assert result.report is not None
    assert result.report.schema_version == CANONICAL_SCHEMA_VERSION  # v1 정본
    assert engine.generate_ai_calls > 0  # 기존 생성 경로가 실제로 돌았다
    assert result.dart_receipt_numbers == ("20260315000123",)
    assert len(result.financial_payload_digest) == 64


def test_ENGINE_V2가_1이면_composer_분기를_탄다(
    engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(real.ENGINE_V2_ENV_NAME, real.ENGINE_V2_ENV_ON)
    calls = _v2_branch_recorder(monkeypatch)

    result = _run()

    assert len(calls) == 1
    assert result.message == _V2_SENTINEL_MESSAGE
    assert result.dart_receipt_numbers == ("20260315000123",)
    assert len(result.financial_payload_digest) == 64
    # 분기는 수집·판정이 «끝난 뒤»다 — 조각과 판정 결과가 그대로 전달된다
    assert calls[0]["company_name"] == "가나다전자"
    assert calls[0]["corp_type"] == "상장사"
    assert calls[0]["frags"]  # 수집 조각 재사용
    # v1 생성(사실 선택·작가·검수) AI는 한 번도 나가지 않는다
    assert engine.generate_ai_calls == 0


def test_1이_아닌_값은_v1_경로다(
    engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(real.ENGINE_V2_ENV_NAME, "0")
    calls = _v2_branch_recorder(monkeypatch)

    result = _run()

    assert calls == []
    assert result.outcome is Outcome.REPORT
    assert result.report is not None
    assert result.report.schema_version == CANONICAL_SCHEMA_VERSION


# ══════════════════════════════════════════════════════════
# ③④ 분기 함수 — run_v2 연결과 RunResult 매핑
# ══════════════════════════════════════════════════════════


def _branch_ingredients(fake: FakeEngine):
    """분기 함수에 넣을 v1 산출물(조각·재무·공시)을 가짜 엔진에서 만든다."""
    engine = real._MeteredEngine(fake)
    client = real._metered_client(engine, fake._client())
    counter = fake.UsageCounter()
    frags = fake.make_fragments("", None)
    financials, _years = fake.fetch_financials(CORP_ID, counter)
    filing = fake.latest_report_rcept(CORP_ID, "상장사", counter)
    return engine, client, frags, financials, filing


def test_분기_함수는_v1_자산을_재사용해_run_v2에_넘긴다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeEngine()
    engine, client, frags, financials, filing = _branch_ingredients(fake)
    dummy_report = Report(
        company="가나다전자",
        job="",
        corp_type="상장사",
        grade=Grade.COMPLETE,
        sections=[],
        citations=[object(), object()],
    )
    captured: dict[str, Any] = {}

    def fake_run_v2(company_name, fragments, performance_table, **kwargs):
        captured.update(
            company_name=company_name,
            fragments=fragments,
            performance_table=performance_table,
            **kwargs,
        )
        return composer_pipeline.V2RunOutput(
            report=dummy_report, composed_sentences=21, verified_sentences=18
        )

    monkeypatch.setattr(composer_pipeline, "run_v2", fake_run_v2)
    steps: list[dict[str, Any]] = []

    result = real._run_v2_composer(
        engine=engine,
        client=client,
        company_name="가나다전자",
        corp_type="상장사",
        frags=frags,
        financials=financials,
        filing=filing,
        revenue_tables=[],
        sources=[],
        business_date=_DATE,
        model="가짜모델",
        steps=steps,
    )

    # run_v2 입력 — 조각 원본 그대로 + 프로그램 실적표 + 기간 라벨 재사용
    assert captured["company_name"] == "가나다전자"
    assert captured["fragments"] is frags
    assert captured["performance_table"] is not None
    assert captured["performance_table"].rows  # build_three_year_table 재사용
    assert captured["analysis_period"] == "2023~2025 완료 회계연도"
    assert captured["as_of_date"] == _DATE.isoformat()
    # 작가·검수 ask는 «서로 다른» 클로저다 (Generator/Evaluator 분리)
    assert callable(captured["writer_ask"]) and callable(captured["reviewer_ask"])
    assert captured["writer_ask"] is not captured["reviewer_ask"]
    # RunResult 매핑
    assert result.outcome is Outcome.REPORT
    assert result.report == dummy_report
    assert result.report is not None and result.report.sources == []
    assert result.charged is True
    assert result.corp_type == "상장사"
    assert result.fragments_collected == len(frags)
    assert result.fragments_cited == 2
    assert result.sentences_made == 21
    assert result.sentences_passed == 18
    assert steps[-1]["step"] == "v2_composer_완료"


def test_v2도_일시적수집실패_보고서를_장기캐시에_저장하지않는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeEngine()
    engine, client, frags, financials, _filing = _branch_ingredients(fake)
    section_ids = real.REQUIRED_SECTION_IDS | real.OPTIONAL_BASIC_SECTION_IDS
    report = Report(
        company="가나다전자",
        job="",
        corp_type="상장사",
        grade=Grade.COMPLETE,
        sections=[
            real.ReportSection(cell=section_id, title=section_id)
            for section_id in sorted(section_ids)
        ],
    )
    monkeypatch.setattr(
        composer_pipeline,
        "run_v2",
        lambda *_args, **_kwargs: composer_pipeline.V2RunOutput(
            report=report,
            composed_sentences=21,
            verified_sentences=18,
        ),
    )
    cache_saves: list[dict[str, Any]] = []
    monkeypatch.setattr(
        real,
        "_v2_cache_save",
        lambda **kwargs: cache_saves.append(kwargs),
    )
    steps = [
        {"step": "6_수집_홈페이지", "후보범위완전": True},
        {"step": "6_수집_공식IR", "후보범위완전": True},
    ]
    source_statuses = [
        real.SourceStatus("회사 공식 IR", "failed", "시간 초과")
    ]

    result = real._run_v2_composer(
        engine=engine,
        client=client,
        company_name="가나다전자",
        corp_type="상장사",
        frags=frags,
        financials=financials,
        filing=None,
        revenue_tables=[],
        sources=source_statuses,
        business_date=_DATE,
        model="가짜모델",
        steps=steps,
        corp_id=CORP_ID,
        current_fiscal_year=2025,
        source_identity_digest="d" * 64,
    )

    assert result.outcome is Outcome.REPORT
    assert result.report is not None
    assert result.report.sources == source_statuses
    assert result.generation_cache_eligible is False
    assert cache_saves == []


def test_v2의_완전한수집결과는_수집신원과함께_장기캐시에_저장한다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeEngine()
    engine, client, frags, financials, _filing = _branch_ingredients(fake)
    section_ids = real.REQUIRED_SECTION_IDS | real.OPTIONAL_BASIC_SECTION_IDS
    report = Report(
        company="가나다전자",
        job="",
        corp_type="상장사",
        grade=Grade.COMPLETE,
        sections=[
            real.ReportSection(cell=section_id, title=section_id)
            for section_id in sorted(section_ids)
        ],
    )
    monkeypatch.setattr(
        composer_pipeline,
        "run_v2",
        lambda *_args, **_kwargs: composer_pipeline.V2RunOutput(
            report=report,
            composed_sentences=21,
            verified_sentences=18,
        ),
    )
    cache_saves: list[dict[str, Any]] = []
    monkeypatch.setattr(
        real,
        "_v2_cache_save",
        lambda **kwargs: cache_saves.append(kwargs),
    )
    source_statuses = [real.SourceStatus("회사 공식 IR", "none", "자료 없음")]

    result = real._run_v2_composer(
        engine=engine,
        client=client,
        company_name="가나다전자",
        corp_type="상장사",
        frags=frags,
        financials=financials,
        filing=None,
        revenue_tables=[],
        sources=source_statuses,
        business_date=_DATE,
        model="가짜모델",
        steps=[
            {"step": "6_수집_홈페이지", "후보범위완전": True},
            {"step": "6_수집_공식IR", "후보범위완전": True},
        ],
        corp_id=CORP_ID,
        current_fiscal_year=2025,
        source_identity_digest="d" * 64,
    )

    assert result.outcome is Outcome.REPORT
    assert result.report is not None and result.report.sources == source_statuses
    assert result.generation_cache_eligible is True
    assert len(cache_saves) == 1
    assert cache_saves[0]["report"].sources == source_statuses


def test_v2_분기는_AskFatalError_원인을_그대로_다시_던진다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """예산 소진 같은 요청 전역 장애는 GATE_STOPPED(출고 검증 실패)가 아니라
    원인 예외 그대로 재전파돼야 v1과 같은 FAILED 처리로 흐른다(실측 결함)."""
    fake = FakeEngine()
    engine, client, frags, financials, filing = _branch_ingredients(fake)
    cause = provider_budget.ProviderBudgetExceeded("이번 단계 예약 잔액을 넘었습니다")

    def failing_run_v2(*_args: Any, **_kwargs: Any):
        raise AskFatalError(cause)

    monkeypatch.setattr(composer_pipeline, "run_v2", failing_run_v2)
    steps: list[dict[str, Any]] = []

    with pytest.raises(provider_budget.ProviderBudgetExceeded):
        real._run_v2_composer(
            engine=engine,
            client=client,
            company_name="가나다전자",
            corp_type="상장사",
            frags=frags,
            financials=financials,
            filing=filing,
            revenue_tables=[],
            sources=[],
            business_date=_DATE,
            model="가짜모델",
            steps=steps,
        )


def test_v2_요청전역_장애는_출고검증실패_아니라_v1과_같은_FAILED로_끝난다(
    engine: FakeEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """run() 전체를 통해 확인 — «검증 실패»로 오표기되지 않는다."""
    monkeypatch.setenv(real.ENGINE_V2_ENV_NAME, real.ENGINE_V2_ENV_ON)

    def failing_run_v2(*_args: Any, **_kwargs: Any):
        raise AskFatalError(
            provider_budget.ProviderBudgetExceeded("이번 단계 예약 잔액을 넘었습니다")
        )

    monkeypatch.setattr(composer_pipeline, "run_v2", failing_run_v2)

    result = _run()

    assert result.outcome is Outcome.FAILED
    assert result.final_gate_reason == ""  # publish_blocked 사유로 오표기되지 않는다


def test_v2_출고검증_실패는_GATE_STOPPED로_끝난다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeEngine()
    engine, client, frags, financials, filing = _branch_ingredients(fake)

    def failing_run_v2(*_args: Any, **_kwargs: Any):
        raise V2ValidationError(("핵심 요약이 부족합니다",))

    monkeypatch.setattr(composer_pipeline, "run_v2", failing_run_v2)
    steps: list[dict[str, Any]] = []

    result = real._run_v2_composer(
        engine=engine,
        client=client,
        company_name="가나다전자",
        corp_type="상장사",
        frags=frags,
        financials=financials,
        filing=filing,
        revenue_tables=[],
        sources=[],
        business_date=_DATE,
        model="가짜모델",
        steps=steps,
    )

    assert result.outcome is Outcome.GATE_STOPPED
    assert result.report is None
    assert result.charged is False  # 보고서가 안 나가면 차감하지 않는다
    assert result.final_gate_reason == FINAL_GATE_REASON_PUBLISH_BLOCKED
    assert "엔진 v2" in result.message
    assert steps[-1]["step"] == "v2_출고검증_차단"
    assert steps[-1]["사유"] == ["핵심 요약이 부족합니다"]


# ══════════════════════════════════════════════════════════
# ⑤ ask 클로저 — 계량 client 경계
# ══════════════════════════════════════════════════════════


def test_v2_ask는_계량_client_경계를_지난다() -> None:
    fake = FakeEngine()
    engine = real._MeteredEngine(fake)
    client = real._metered_client(engine, fake._client())
    ask = real._v2_ask_via_provider(
        engine, client, stage="v2_compose", max_tokens=real.V2_WRITER_MAX_TOKENS
    )

    answer = ask("프롬프트 본문")

    assert fake.client.messages.calls == 1
    request = fake.client.messages.requests[0]
    assert request["max_tokens"] == real.V2_WRITER_MAX_TOKENS
    assert request["messages"] == [{"role": "user", "content": "프롬프트 본문"}]
    assert request["model"] == "가짜모델"  # 요청 로컬 모델이 경계에서 고정된다
    # 가짜 응답에는 content가 없다 — 문자열 계약(빈 문자열)만 확인한다
    assert answer == ""
    # 사용량이 요청별 계량기에 stage와 함께 쌓인다 (0원 위장 금지)
    assert engine.usages
    assert engine.usages[0]["stage"] == "v2_compose"


def test_v2_ask는_예산소진을_AskFatalError로_감싸_재전파한다() -> None:
    """예산 소진·billing-uncertain은 문장 하나의 실패가 아니라 요청 전역
    장애다 — composer가 삼키지 못하게 AskFatalError로 감싼다(실측 결함)."""
    fake = FakeEngine()
    engine = real._MeteredEngine(fake)
    client = real._metered_client(engine, fake._client())
    cause = provider_budget.ProviderBudgetExceeded("이번 단계 예약 잔액을 넘었습니다")

    def exhausted(**_kwargs: Any) -> Any:
        raise cause

    client.messages.create = exhausted  # type: ignore[method-assign]
    ask = real._v2_ask_via_provider(
        engine, client, stage="v2_compose", max_tokens=real.V2_WRITER_MAX_TOKENS
    )

    with pytest.raises(AskFatalError) as caught:
        ask("프롬프트 본문")
    assert caught.value.cause is cause


def test_v2_ask는_billing_uncertain_차단도_AskFatalError로_감싼다() -> None:
    fake = FakeEngine()
    engine = real._MeteredEngine(fake)
    client = real._metered_client(engine, fake._client())
    cause = provider_budget.ProviderBudgetUnavailable(
        "미확정 provider 호출 뒤에는 같은 요청에서 다시 호출할 수 없습니다"
    )

    def blocked(**_kwargs: Any) -> Any:
        raise cause

    client.messages.create = blocked  # type: ignore[method-assign]
    ask = real._v2_ask_via_provider(
        engine, client, stage="v2_review", max_tokens=real.V2_REVIEWER_MAX_TOKENS
    )

    with pytest.raises(AskFatalError) as caught:
        ask("프롬프트 본문")
    assert caught.value.cause is cause
