"""뉴스가 필수 작성과 보충 검수의 공통 호출 몫을 먼저 쓰지 않는다."""

import datetime as dt
from types import SimpleNamespace

import pytest

from src.core.news_research_adapter import prepare_news_research
from src.features.budget.provider_budget import RequestCallLimitReached
from src.features.news_intake.models import NewsCollectionPolicy
from src.features.pipeline import real
from src.shared.report_recovery import MAX_TOTAL_AI_CALLS


def _search(_query, **_kwargs):
    return SimpleNamespace(
        state="success", reason_code="news_search_ok", items=[],
        transport_attempts=1, retry_recovered=False,
        attempt_reason_codes=("news_search_ok",),
    )


def _prepare(limit, *, policy=None):
    return prepare_news_research(
        search_news=_search, company_name="가나다전자", aliases=(), domain="",
        executive_names=(), identity_context="", as_of=dt.date(2026, 9, 8),
        policy=policy, max_analysis_calls=limit,
    )


@pytest.mark.parametrize("already_used", [0, 1, 3, 5])
def test_이전사용량을_빼고도_FULL_보충검수까지_남겨둔다(already_used):
    engine = real._MeteredEngine(SimpleNamespace())
    for _ in range(already_used):
        engine.reserve_provider_call()
    available = engine.available_provider_calls(reserved_calls=MAX_TOTAL_AI_CALLS)
    session = _prepare(available)
    for _ in range(session.policy.max_analysis_calls):
        engine.reserve_provider_call()
    for _ in range(MAX_TOTAL_AI_CALLS):
        engine.reserve_provider_call()
    with pytest.raises(RequestCallLimitReached):
        engine.reserve_provider_call()


def test_뉴스예산을_별도요청과_공유하지_않는다():
    first = real._MeteredEngine(SimpleNamespace())
    second = real._MeteredEngine(SimpleNamespace())
    before = second.available_provider_calls(reserved_calls=MAX_TOTAL_AI_CALLS)
    first.reserve_provider_call()
    assert first.available_provider_calls(reserved_calls=MAX_TOTAL_AI_CALLS) == before - 1
    assert second.available_provider_calls(reserved_calls=MAX_TOTAL_AI_CALLS) == before


def test_뉴스예산도_검색정책_지문에_결속하고_좁은정책을_늘리지_않는다():
    first = _prepare(5)
    smaller = _prepare(4)
    assert first.snapshot.digest != smaller.snapshot.digest
    assert _prepare(5, policy=NewsCollectionPolicy(max_analysis_calls=2)).policy.max_analysis_calls == 2


@pytest.mark.parametrize("limit", [-1, True, "5"])
def test_잘못된_뉴스예산으로_검색하기_전에_거절한다(limit):
    with pytest.raises(ValueError):
        _prepare(limit)
