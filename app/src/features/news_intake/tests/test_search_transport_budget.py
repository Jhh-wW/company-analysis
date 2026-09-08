"""논리 검색과 실제 NAVER 전송의 예산·지문·관측 불가 계약 시험."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.features.news_intake import constants as c
from src.features.news_intake import search_snapshot as search_module
from src.features.news_intake.collection import collect_from_snapshot
from src.features.news_intake.models import NewsCollectionPolicy
from src.features.news_intake.tests.test_collection import (
    AS_OF, BODY, COMPANY, POLICY, analyzer, item,
)


OK = "news_search_ok"
TEMPORARY = "news_search_temporarily_unavailable"


def response(*, attempts=1, recovered=False, reasons=(OK,), items=(), **overrides):
    values = dict(state="success", reason_code=OK, items=list(items),
                  transport_attempts=attempts, retry_recovered=recovered,
                  attempt_reason_codes=reasons)
    values.update(overrides)
    return SimpleNamespace(**values)


def collect(search, *, policy=POLICY):
    return search_module.collect_search_snapshot(
        search_news=search, company=COMPANY, as_of=AS_OF, policy=policy,
    )


def collect_body(snapshot, *, policy=POLICY):
    return collect_from_snapshot(
        snapshot, company=COMPANY, as_of=AS_OF, policy=policy,
        fetch_text=lambda url: BODY, analyze_grounded=analyzer(),
    )


@pytest.fixture
def single_query(monkeypatch):
    monkeypatch.setattr(search_module, "search_plan", lambda company, as_of: (
        (company.company_name, "date", "recent", c.WINDOW_MONTHS[0]),
    ))


@pytest.fixture
def naver(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[5] / "analysis_engine" / "src"))
    from core import naver_client

    monkeypatch.setenv(naver_client.c.NAVER_API_KEY_ID_ENV, "offline-test-id")
    monkeypatch.setenv(naver_client.c.NAVER_API_KEY_ENV, "offline-test-key")
    monkeypatch.setattr(naver_client, "_daily_counter_day", None)
    monkeypatch.setattr(naver_client, "_daily_counter_count", 0)

    def reject_network(*args, **kwargs):
        pytest.fail("실제 NAVER 전송은 이 오프라인 시험에서 금지합니다")

    monkeypatch.setattr(naver_client, "_urlopen", reject_network)
    return naver_client


@pytest.mark.parametrize("budget", [11, c.SEARCH_TRANSPORT_ATTEMPT_BUDGET])
def test_재시도가_논리검색보다_많아도_실제읽기_절대상한을_넘지_않는다(naver, monkeypatch, budget):
    reads = []
    remaining = []
    policy = replace(POLICY, max_search_transport_attempts=budget)

    def read(request):
        reads.append(None)
        if len(reads) % 2:
            raise naver._TransientNewsError
        article = vars(item())
        article["pubDate"] = "Tue, 01 Sep 2026 09:00:00 +0900"
        return {"items": [article]}

    def search(query, display, start, sort, *, remaining_transport_budget):
        remaining.append(remaining_transport_budget)
        return naver.search_news(query, display, start, sort,
                                 remaining_transport_budget=remaining_transport_budget)

    monkeypatch.setattr(naver, "_read_payload", read)
    snapshot = collect(search, policy=policy)
    diagnostics = snapshot.transport_diagnostics
    assert len(reads) == naver._daily_counter_count == budget
    assert remaining == list(range(budget, 0, -2))
    assert diagnostics["검색논리호출"] == (budget + 1) // 2
    assert diagnostics["검색실제전송"] == diagnostics["검색전송예산차감"] == budget
    assert diagnostics["검색재시도복구"] == budget // 2
    assert diagnostics["검색전송상한보장"] is True
    assert snapshot.reason_codes == (c.SEARCH_TRANSPORT_BUDGET_EXHAUSTED,)
    assert snapshot.status == "partial" and not snapshot.cache_eligible
    result = collect_body(snapshot, policy=policy)
    assert len(result.articles) == len(result.fragments) == 1
    assert result.articles[0].candidate.published_on == "2026-09-01"
    assert result.diagnostics["상한사유"] == (c.SEARCH_TRANSPORT_BUDGET_EXHAUSTED,)
    assert result.diagnostics["실패"] is None
    assert result.diagnostics["완전성"] == "partial"
    assert result.diagnostics["자료부족"] is False


@pytest.mark.parametrize("condition, expected", [
    ("missing", "news_search_not_configured"),
    ("daily", "news_search_daily_cap"),
    ("request", "news_search_invalid_response"),
])
def test_어댑터가_확인한_전송전_실패는_정확한_0회다(naver, monkeypatch, condition, expected):
    if condition == "missing":
        monkeypatch.delenv(naver.c.NAVER_API_KEY_ENV)
    elif condition == "daily":
        monkeypatch.setattr(naver.c, "NAVER_NEWS_DAILY_CAP", 0)
    else:
        def invalid_request(*args, **kwargs):
            raise ValueError("출력하면 안 되는 시험용 인증 정보")
        monkeypatch.setattr(naver, "_request", invalid_request)
    snapshot = collect(naver.search_news)
    diagnostics = snapshot.transport_diagnostics
    assert snapshot.reason_codes == (expected,)
    assert diagnostics["검색논리호출"] == 1
    assert diagnostics["검색실제전송"] == diagnostics["검색전송예산차감"] == 0
    assert diagnostics["검색전송관측완료"] is True
    assert diagnostics["검색전송진단"] == ()
    assert not snapshot.cache_eligible
    assert collect_body(snapshot).diagnostics["자료부족"] is False


def test_재시도_최종실패도_전송두번과_각시도사유를_보존한다(naver, monkeypatch):
    def read(request):
        raise naver._TransientNewsError
    monkeypatch.setattr(naver, "_read_payload", read)
    snapshot = collect(naver.search_news)
    attempt = snapshot.query_attempts[0]
    assert attempt.transport_attempts == 2 and attempt.retry_recovered is False
    assert attempt.attempt_reason_codes == (TEMPORARY, TEMPORARY)
    assert snapshot.transport_diagnostics["검색실제전송"] == 2
    assert snapshot.reason_codes == (TEMPORARY,)


def test_같은_기사와_논리쿼리라도_재시도_복구이력이_지문에_결속된다(single_query):
    plain = collect(lambda query, **options: response(items=[item()]))
    retried = collect(lambda query, **options: response(
        attempts=2, recovered=True, reasons=(TEMPORARY, OK), items=[item()],
    ))
    assert plain.candidates == retried.candidates
    assert plain.query_attempts[0].item_fingerprints == retried.query_attempts[0].item_fingerprints
    assert plain.digest != retried.digest
    assert plain.cache_eligible and retried.cache_eligible
    assert retried.transport_diagnostics["검색재시도복구"] == 1
    changed = replace(plain, query_attempts=retried.query_attempts)
    with pytest.raises(ValueError, match="결속"):
        collect_body(changed)


def test_엄격한_구형콜백은_한번만_호출하고_기사후보를_살리되_전송미관측을_표시한다():
    calls = []

    def search(query, display, start, sort):
        calls.append(query)
        return SimpleNamespace(state="success", reason_code=OK, items=[item()])

    snapshot = collect(search)
    diagnostics = snapshot.transport_diagnostics
    assert len(calls) == 1 and len(snapshot.candidates) == 1
    assert diagnostics["검색실제전송"] is None
    assert diagnostics["검색전송관측완료"] is False
    assert diagnostics["검색전송상한보장"] is False
    assert diagnostics["검색재시도복구"] is None
    assert diagnostics["검색전송예산차감"] == POLICY.max_search_transport_attempts
    assert set(diagnostics["검색전송진단"]) == {
        c.SEARCH_TRANSPORT_UNOBSERVED, c.SEARCH_TRANSPORT_BUDGET_UNENFORCED,
    }
    assert snapshot.status == "partial" and not snapshot.cache_eligible
    result = collect_body(snapshot)
    assert len(result.articles) == len(result.fragments) == 1
    assert result.diagnostics["자료부족"] is False
    assert c.SEARCH_TRANSPORT_UNOBSERVED in result.diagnostics["검증미완료"]


def test_예산옵션을_받아도_구형응답의_전송횟수는_관측불가다():
    calls = []

    def search(query, **options):
        calls.append(options)
        return SimpleNamespace(state="success", reason_code=OK, items=[])

    snapshot = collect(search)
    assert len(calls) == 1 and "remaining_transport_budget" in calls[0]
    assert snapshot.transport_diagnostics["검색실제전송"] is None
    assert snapshot.status == c.SEARCH_TRANSPORT_OBSERVATION_PENDING
    assert not snapshot.cache_eligible
    assert collect_body(snapshot).diagnostics["자료부족"] is False


@pytest.mark.parametrize("state, reason", [("success", OK), ("skipped", "news_search_not_configured")])
def test_구형_DTO_기본값0을_실측0으로_속이지_않는다(naver, state, reason):
    snapshot = collect(lambda query, **options: naver.NewsSearchResult(state, reason, [], 0))
    assert snapshot.transport_diagnostics["검색실제전송"] is None
    assert c.SEARCH_TRANSPORT_METADATA_INVALID in snapshot.transport_diagnostics["검색전송진단"]
    assert not snapshot.cache_eligible


def test_구형콜백이_횟수를_보고해도_예산지원없음은_분리한다():
    def search(query, display, start, sort):
        return response(items=[item()])
    snapshot = collect(search)
    assert snapshot.transport_diagnostics["검색실제전송"] == 1
    assert snapshot.transport_diagnostics["검색전송관측완료"] is True
    assert snapshot.transport_diagnostics["검색전송상한보장"] is False
    assert snapshot.transport_diagnostics["검색전송예산차감"] == POLICY.max_search_transport_attempts
    assert len(snapshot.query_attempts) == 1 and not snapshot.cache_eligible


def test_콜백_내부_TypeError_뒤에_재호출하지_않는다():
    calls = []

    def search(query, **options):
        calls.append(options)
        raise TypeError("출력하면 안 되는 시험용 인증 정보")

    snapshot = collect(search)
    assert len(calls) == 1
    assert snapshot.reason_codes == ("news_search_internal_error",)
    assert snapshot.transport_diagnostics["검색실제전송"] is None
    assert "인증 정보" not in repr(snapshot)


def test_서명을_읽을수없는_콜백은_호출전에_구형계약으로_판단한다(monkeypatch):
    calls = []

    def no_signature(callback):
        raise ValueError("서명 없음")

    def search(query, display, start, sort):
        calls.append(query)
        return SimpleNamespace(state="success", reason_code=OK, items=[])

    monkeypatch.setattr(search_module.inspect, "signature", no_signature)
    snapshot = collect(search)
    assert len(calls) == 1
    assert snapshot.transport_diagnostics["검색전송상한보장"] is False


@pytest.mark.parametrize("overrides", [
    {"attempts": -1}, {"attempts": True}, {"attempts": 2},
    {"reasons": ("출력하면 안 되는 시험용 인증 정보",)},
    {"recovered": True}, {"attempts": 0, "reasons": ()},
    {"attempts": 0, "reasons": (), "state": "failed", "reason_code": []},
])
def test_모순된_전송메타는_미관측이며_남은예산을_보수차감한다(overrides):
    snapshot = collect(lambda query, **options: response(**overrides))
    diagnostics = snapshot.transport_diagnostics
    assert len(snapshot.query_attempts) == 1
    assert diagnostics["검색실제전송"] is None
    assert diagnostics["검색전송예산차감"] == POLICY.max_search_transport_attempts
    assert c.SEARCH_TRANSPORT_METADATA_INVALID in diagnostics["검색전송진단"]
    assert "인증 정보" not in repr(snapshot)


def test_속성읽기_실패도_관측불가로_기록하며_예외원문은_숨긴다():
    class Result:
        state, reason_code, items = "success", OK, []

        @property
        def transport_attempts(self):
            raise ValueError("출력하면 안 되는 시험용 인증 정보")

    snapshot = collect(lambda query, **options: Result())
    assert snapshot.transport_diagnostics["검색실제전송"] is None
    assert c.SEARCH_TRANSPORT_METADATA_INVALID in snapshot.transport_diagnostics["검색전송진단"]
    assert "인증 정보" not in repr(snapshot)


def test_응답항목_읽기실패를_정상빈결과로_캐시하지_않는다():
    class Result:
        state, reason_code = "success", OK
        transport_attempts, retry_recovered, attempt_reason_codes = 1, False, (OK,)

        @property
        def items(self):
            raise ValueError("출력하면 안 되는 시험용 인증 정보")

    snapshot = collect(lambda query, **options: Result())
    assert snapshot.reason_codes == ("news_search_internal_error",)
    assert snapshot.transport_diagnostics["검색실제전송"] == 1
    assert snapshot.status == "failed" and not snapshot.cache_eligible
    assert "인증 정보" not in repr(snapshot)


def test_예산을_어긴_대역의_실제전송_보고값을_상한으로_잘라_숨기지_않는다():
    count = c.SEARCH_TRANSPORT_ATTEMPT_BUDGET + 1
    snapshot = collect(lambda query, **options: response(attempts=count, reasons=(OK,) * count))
    assert snapshot.transport_diagnostics["검색실제전송"] == count
    assert snapshot.transport_diagnostics["검색전송상한보장"] is False
    assert c.SEARCH_TRANSPORT_BUDGET_EXCEEDED in snapshot.transport_diagnostics["검색전송진단"]
    assert len(snapshot.query_attempts) == 1 and not snapshot.cache_eligible


@pytest.mark.parametrize("budget", [0, -1, True, 1.5, c.SEARCH_TRANSPORT_ATTEMPT_BUDGET + 1])
def test_전송정책도_양의정수와_절대상한을_강제한다(budget):
    with pytest.raises(ValueError, match="상한"):
        NewsCollectionPolicy(max_search_transport_attempts=budget)


def test_관측완료한_정상빈응답만_자료부족으로_분류한다(single_query):
    snapshot = collect(lambda query, **options: response())
    result = collect_body(snapshot)
    assert snapshot.status == "empty" and snapshot.cache_eligible
    assert result.diagnostics["검색실제전송"] == 1
    assert result.diagnostics["자료부족"] is True
    assert result.diagnostics["완전성"] == "insufficient"
