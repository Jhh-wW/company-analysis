from __future__ import annotations

import datetime as dt
from dataclasses import replace
from types import SimpleNamespace

import pytest

from src.core import news_intake_switch
from src.features.composer.port import filing_meta_from_raw
from src.features.pipeline import real
from src.features.pipeline.evidence_transport import (
    RAW_EVIDENCE_COLLECTED_ON_KEY,
    RAW_EVIDENCE_DOCUMENT_CONTENT_SHA256_KEY,
    RAW_EVIDENCE_PUBLISHER_KEY,
    RAW_EVIDENCE_SECTION_IDS_KEY,
    RAW_EVIDENCE_SLOT_IDS_KEY,
)
from src.shared.report_evidence.policy import REQUIRED_EVIDENCE_SECTION_IDS
from src.shared.report_evidence.constants import (
    GenerationGateStatus,
    ReleaseMode,
    ReportExecutionOutcome,
)
from src.shared.report_generation.models import exact_text_sha256
from src.features.pipeline.tests.test_official_evidence_runtime import (
    _Collector,
    _freeze_runtime,
    _official_result,
    _request,
    _wire_runtime,
    FakeEngine,
)


AS_OF = dt.date(2026, 9, 6)
ARTICLE_URL = "https://media.example/news/company-strategy"
ARTICLE_BODY = "가나다전자는 고객 업무를 잇는 새 제품군을 중심 사업으로 운영한다."


@pytest.fixture(autouse=True)
def _reset_news_switch(monkeypatch: pytest.MonkeyPatch):
    news_intake_switch._reset_process_news_intake_switch_for_tests()  # noqa: SLF001
    monkeypatch.delenv(news_intake_switch.NEWS_INTAKE_ENV_NAME, raising=False)
    yield
    news_intake_switch._reset_process_news_intake_switch_for_tests()  # noqa: SLF001


def _ready(*, portfolio: bool = False) -> dict[str, bool]:
    return {
        section_id: section_id != "portfolio" or portfolio
        for section_id in REQUIRED_EVIDENCE_SECTION_IDS
    }


def _item() -> SimpleNamespace:
    return SimpleNamespace(
        title="가나다전자 새 제품군 공개",
        originallink=ARTICLE_URL,
        link=ARTICLE_URL,
        description="가나다전자가 고객 업무를 잇는 제품군을 공개했다.",
        pubDate="2026-09-01",
    )


def _result(
    *,
    state: str = "success",
    reason_code: str = "news_search_ok",
    items: list[object] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        state=state,
        reason_code=reason_code,
        items=[] if items is None else items,
        elapsed_ms=1,
    )


def _collect(
    *,
    search_news,
    classify=lambda _prompt: (
        '{"items":[{"id":"news-001","sections":["portfolio"],'
        '"kind":"press_release"}]}'
    ),
    fetch_text=lambda _url: ARTICLE_BODY,
    section_ready: dict[str, bool] | None = None,
):
    steps: list[dict[str, object]] = []
    fragments = real._collect_news_intake(
        search_news=search_news,
        classify=classify,
        fetch_text=fetch_text,
        company_name="가나다전자",
        company_aliases=("가나다",),
        company_domain="https://company.example",
        executive_names=("김대표",),
        corp_id="00123456",
        section_ready=section_ready or _ready(),
        as_of=AS_OF,
        collected_on=AS_OF.isoformat(),
        steps=steps,
    )
    return fragments, steps


def test_news_intake_wires_one_classification_and_typed_supplementary_fragment() -> None:
    calls = {"search": 0, "classify": 0, "fetch": 0}

    def search_news(_query: str, **kwargs: object) -> SimpleNamespace:
        calls["search"] += 1
        assert kwargs["display"] == real.NEWS_SEARCH_PAGE_SIZE
        assert kwargs["start"] in {1, 21}
        return _result(items=[_item()] if kwargs["start"] == 1 else [])

    def classify(prompt: str) -> str:
        calls["classify"] += 1
        assert "가나다전자 새 제품군 공개" in prompt
        return (
            '{"items":[{"id":"news-001","sections":["portfolio"],'
            '"kind":"press_release"}]}'
        )

    def fetch_text(url: str) -> str:
        calls["fetch"] += 1
        assert url == ARTICLE_URL
        return ARTICLE_BODY

    fragments, steps = _collect(
        search_news=search_news,
        classify=classify,
        fetch_text=fetch_text,
    )

    assert calls == {"search": 2, "classify": 1, "fetch": 1}
    assert len(fragments) == 1
    fragment = fragments[0]
    assert fragment["종류"] == "news"
    assert fragment["출처"] == ARTICLE_URL
    assert fragment["발행처"] == "media.example"
    assert fragment["문서명"] == "가나다전자 새 제품군 공개"
    assert fragment["문서일"] == "2026-09-01"
    assert fragment[RAW_EVIDENCE_SECTION_IDS_KEY] == ("portfolio",)
    assert fragment[RAW_EVIDENCE_SLOT_IDS_KEY]
    assert fragment[RAW_EVIDENCE_PUBLISHER_KEY] == "media.example"
    assert fragment[RAW_EVIDENCE_COLLECTED_ON_KEY] == AS_OF.isoformat()
    assert fragment[RAW_EVIDENCE_DOCUMENT_CONTENT_SHA256_KEY] == exact_text_sha256(
        ARTICLE_BODY
    )

    assert len(steps) == 1
    step = steps[0]
    assert step["step"] == "5b_뉴스_수집"
    assert step["창"] == "확장"
    assert step["검색"] == 1
    assert step["선별"] == 1
    assert step["분류AI호출"] == 1
    assert step["본문읽기"] == 1
    assert step["조각"] == 1
    assert step["실패"] is None
    assert step["검색호출"] == 2
    assert step["분류프롬프트글자"] > 0
    assert step["조각글자"] <= real.NEWS_FRAGMENT_TOTAL_CHARS_LIMIT


def test_news_intake_makes_no_calls_when_every_extendable_section_is_ready() -> None:
    def unexpected(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("뉴스 호출이 발생하면 안 됩니다")

    fragments, steps = _collect(
        search_news=unexpected,
        classify=unexpected,
        fetch_text=unexpected,
        section_ready=_ready(portfolio=True),
    )

    assert fragments == []
    assert steps[0]["검색호출"] == 0
    assert steps[0]["분류AI호출"] == 0
    assert steps[0]["본문읽기"] == 0


def test_missing_news_credentials_are_a_normal_no_news_result() -> None:
    fragments, steps = _collect(
        search_news=lambda *_args, **_kwargs: _result(
            state="skipped",
            reason_code=real.NEWS_SEARCH_NOT_CONFIGURED_CODE,
        )
    )

    assert fragments == []
    assert steps[0]["실패"] is None
    assert steps[0]["검색호출"] == 1
    assert steps[0]["분류AI호출"] == 0


@pytest.mark.parametrize(
    "reason_code",
    [
        "news_search_authentication_failed",
        "news_search_rate_limited",
        "news_search_temporarily_unavailable",
        "news_search_daily_cap",
    ],
)
def test_search_failures_keep_the_report_path_open(reason_code: str) -> None:
    fragments, steps = _collect(
        search_news=lambda *_args, **_kwargs: _result(
            state="failed",
            reason_code=reason_code,
        )
    )

    assert fragments == []
    assert steps[0]["실패"] == reason_code
    assert steps[0]["분류AI호출"] == 0
    assert steps[0]["본문읽기"] == 0


def test_classification_parse_failure_is_recorded_without_fetching_body() -> None:
    fetch_calls = 0

    def fetch_text(_url: str) -> str:
        nonlocal fetch_calls
        fetch_calls += 1
        return ARTICLE_BODY

    fragments, steps = _collect(
        search_news=lambda _query, **kwargs: _result(
            items=[_item()] if kwargs["start"] == 1 else []
        ),
        classify=lambda _prompt: "not-json",
        fetch_text=fetch_text,
    )

    assert fragments == []
    assert fetch_calls == 0
    assert steps[0]["분류AI호출"] == 1
    assert steps[0]["실패"] == real.NEWS_CLASSIFICATION_INVALID_CODE


def test_body_failure_is_recorded_after_one_classification() -> None:
    fragments, steps = _collect(
        search_news=lambda _query, **kwargs: _result(
            items=[_item()] if kwargs["start"] == 1 else []
        ),
        fetch_text=lambda _url: None,
    )

    assert fragments == []
    assert steps[0]["분류AI호출"] == 1
    assert steps[0]["본문읽기"] == 0
    assert steps[0]["실패"] == real.NEWS_BODY_FETCH_FAILED_CODE


def test_full_runtime_adds_news_after_official_preflight_before_composer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(news_intake_switch.NEWS_INTAKE_ENV_NAME, "1")
    _freeze_runtime(
        monkeypatch,
        mode=real.engine_mode.EngineMode.V2,
        release_mode=ReleaseMode.FULL,
    )
    engine = FakeEngine()
    collector = _Collector([_official_result()])
    calls = _wire_runtime(monkeypatch, engine=engine)
    original_assess = real.assess_official_evidence

    def assess_with_portfolio_gap(result):
        base = original_assess(result)
        decision = replace(
            base.decision,
            status=GenerationGateStatus.STOP_INSUFFICIENT_EVIDENCE,
            outcome=ReportExecutionOutcome.INSUFFICIENT_EVIDENCE,
            ready_section_ids=tuple(
                section_id
                for section_id in base.decision.ready_section_ids
                if section_id != "portfolio"
            ),
            insufficient_section_ids=("portfolio",),
            reason_codes=("fixture_portfolio_gap",),
        )
        return replace(
            base,
            decision=decision,
            dart_partial_fallback=True,
            dart_partial_reason="insufficient_with_ready_sections",
        )

    monkeypatch.setattr(real, "assess_official_evidence", assess_with_portfolio_gap)

    search_calls = 0

    def search_news(_query: str, **kwargs: object) -> SimpleNamespace:
        nonlocal search_calls
        search_calls += 1
        return _result(items=[_item()] if kwargs["start"] == 1 else [])

    user_input, card = _request()
    result = real.RealPipeline(
        official_evidence_collector=collector,
        news_search=search_news,
        news_classify=lambda _prompt: (
            '{"items":[{"id":"news-001","sections":["portfolio"],'
            '"kind":"press_release"}]}'
        ),
        news_fetch_text=lambda _url: ARTICLE_BODY,
    ).run(user_input, card)

    assert result.outcome is real.Outcome.REPORT
    assert search_calls == 2
    assert len(calls.composers) == 1
    composer = calls.composers[0]
    news_fragments = [
        fragment
        for fragment in composer["frags"].values()
        if fragment.get("종류") == "news"
    ]
    assert len(news_fragments) == 1
    assert news_fragments[0]["발행처"] == "media.example"
    packets = real._full_section_evidence_packets(
        corp_id=composer["corp_id"],
        source_identity_digest=composer["source_identity_digest"],
        frags=composer["frags"],
        filing_meta=filing_meta_from_raw(composer["filing"]),
    )
    transported_news = [
        fragment
        for packet in packets.packets
        for fragment in packet.fragments
        if fragment.formal_source_kind == "news"
    ]
    assert len(transported_news) == 1
    assert transported_news[0].counts_toward_document_floor is False
    assert transported_news[0].source_publisher == "media.example"
    assert transported_news[0].document_date == "2026-09-01"
    news_step = next(
        step for step in composer["steps"] if step.get("step") == "5b_뉴스_수집"
    )
    assert news_step["분류AI호출"] == 1
    assert news_step["본문읽기"] == 1
    assert not any(
        step.get("step") == "6_수집_뉴스" for step in composer["steps"]
    )


def test_full_runtime_off_never_calls_news_dependencies(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _freeze_runtime(
        monkeypatch,
        mode=real.engine_mode.EngineMode.V2,
        release_mode=ReleaseMode.FULL,
    )
    engine = FakeEngine()
    collector = _Collector([_official_result()])
    calls = _wire_runtime(monkeypatch, engine=engine)

    def unexpected(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("NEWS_INTAKE OFF에서 뉴스 의존성을 호출하면 안 됩니다")

    user_input, card = _request()
    result = real.RealPipeline(
        official_evidence_collector=collector,
        news_search=unexpected,
        news_classify=unexpected,
        news_fetch_text=unexpected,
    ).run(user_input, card)

    assert result.outcome is real.Outcome.REPORT
    assert len(calls.composers) == 1
    assert not any(
        step.get("step") == "5b_뉴스_수집"
        for step in calls.composers[0]["steps"]
    )
    assert not any(
        raw.get("종류") == "news"
        for raw in calls.composers[0]["frags"].values()
    )
