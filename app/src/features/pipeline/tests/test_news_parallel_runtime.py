"""실제 session→collector→운영 본문 콜백을 외부 통신 없이 검증한다."""

from __future__ import annotations

import contextvars
import datetime as dt
import json
import threading
from concurrent.futures import CancelledError, ThreadPoolExecutor
from contextlib import contextmanager
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from src.core import news_research_adapter
from src.features.homepage import safe_http
from src.features.homepage.wide_fetch import WideRawResponse
from src.features.news_intake.body_prefetch import BodyFetchLane, HostSlots
from src.features.news_intake.models import NewsCollectionPolicy
from src.features.news_intake.search_snapshot import diverse_candidates, domain_host
from src.features.observability import run_diagnostics
from src.features.pipeline import real
from src.shared import engine_build_identity, generation_coordination


WAIT_SECONDS = 5
AS_OF = dt.date(2026, 9, 22)
SIGNAL = contextvars.ContextVar("뉴스_시험_취소신호")
URLS = ("https://a.example/article/0", "https://b.example/article/1", "https://c.example/article/2")


@pytest.fixture(autouse=True)
def isolated_environment(monkeypatch):
    monkeypatch.delenv(real.NEWS_BODY_CONCURRENCY_ENV, raising=False)
    monkeypatch.setenv("RENDER_GIT_COMMIT", "a" * 40)
    monkeypatch.delenv("APP_GIT_COMMIT", raising=False)
    monkeypatch.setattr(real, "default_wide_transport", lambda *args: pytest.fail("가짜 전송을 설치해야 합니다"))


def session(urls=URLS[:2]):
    searches = []

    def search(query, **options):
        searches.append(query)
        return SimpleNamespace(
            state="success", reason_code="news_search_ok", transport_attempts=1,
            retry_recovered=False, attempt_reason_codes=("news_search_ok",),
            items=[SimpleNamespace(title=f"가나다전자 산업설비 {i} 공급", description="기업용 산업설비 공급",
                                   originallink=url, link="", pubDate="2026-09-01")
                   for i, url in enumerate(urls)] if len(searches) == 1 else [],
        )

    return news_research_adapter.prepare_news_research(
        search_news=search, company_name="가나다전자", aliases=(), domain="",
        executive_names=(), identity_context="기업용 산업설비 제조", as_of=AS_OF,
        policy=NewsCollectionPolicy(trusted_publisher_domains=tuple(dict.fromkeys(domain_host(url) for url in urls))),
    )


def analyze_recording(calls):
    def analyze(prompt, schema, max_tokens):
        articles = json.loads(prompt.split("자료 시작:\n", 1)[1])["articles"]
        calls.append(articles)
        return {"items": [{
            "id": article["id"], "same_company": True, "material": True,
            "entity_evidence": article["body"], "source_type": "news_report",
            "excerpts": [{"text": article["body"], "section_id": "portfolio",
                          "claim_slot": "portfolio:product_role", "claim_kind": "reported_fact",
                          "temporal_status": "completed", "topic": "products", "event_key": article["url"],
                          "event_on": "", "time_evidence": "", "subject": "", "subject_evidence": ""}],
        } for article in articles]}
    return analyze


def collect(prepared, steps, calls, fetch=None):
    return real._collect_grounded_news(
        session=prepared, analyze=analyze_recording(calls),
        fetch_text=real._fetch_news_article_text if fetch is None else fetch,
        corp_id="00123456", official_web_documents=0, collected_on=AS_OF.isoformat(), steps=steps,
    )


class Transport:
    def __init__(self, *, width=None, on_request=None):
        self.barrier = threading.Barrier(width) if width else None
        self.on_request = on_request
        self.lock = threading.Lock()
        self.requests = []
        self.observed = []

    def __call__(self, url, allowed=None):
        assert allowed is None or allowed(url)
        with self.lock:
            self.requests.append(url)
        if self.on_request:
            self.on_request(url)
        if url.endswith("/robots.txt"):
            return WideRawResponse(200, "User-agent: *\nAllow: /", url, "text/plain")
        # 실제 safe_urlopen도 부모가 없으면 전송마다 원래의 예산을 만든다.
        budget = safe_http.active_deadline_budget() or safe_http._DeadlineBudget.after(real.NEWS_BODY_FETCH_TIMEOUT_SEC)
        steps = run_diagnostics.current_steps()
        with self.lock:
            self.observed.append((threading.get_ident(), budget, steps))
        if budget is not None:
            budget.dns_cache[(urlsplit(url).hostname, 443)] = ()
        steps.append({"step": "기사조회", "주소": url})
        if self.barrier:
            self.barrier.wait(WAIT_SECONDS)
        number = int(url.rsplit("/", 1)[-1]) + 120
        text = f"가나다전자는 기업용 산업설비 제조 사업을 운영하며 2026년 9월 1일 자동화 설비 {number}대를 공급했다."
        return WideRawResponse(200, f"<article><p>{text}</p></article>", url, "text/html")


@contextmanager
def active_request(check):
    callbacks = generation_coordination.GenerationCallbacks(
        coordinate=lambda *_: None,
        ensure_paid_phase=lambda: pytest.fail("본문 수집은 비용 단계를 열 수 없습니다"),
        engine_build_identity=engine_build_identity.process_engine_build_identity(),
        check_active=check,
    )
    with generation_coordination.activate(callbacks):
        yield


@pytest.mark.parametrize("setting,width", [(None, 3), ("2", 2), ("3", 3)],
                         ids=("default-three", "explicit-two", "explicit-three"))
def test_runtime_width_overlaps_through_real_session_and_preserves_parent_context(monkeypatch, setting, width):
    if setting is not None:
        monkeypatch.setenv(real.NEWS_BODY_CONCURRENCY_ENV, setting)
    urls = URLS[:width]
    prepared = session(urls)
    transport = Transport(width=width)
    monkeypatch.setattr(real, "default_wide_transport", transport)
    parent_steps, calls = [{"step": "부모"}], []
    signal = threading.Event()
    seen_signals = []
    token = SIGNAL.set(signal)
    try:
        with active_request(lambda: seen_signals.append(SIGNAL.get())):
            with safe_http.collection_cache_scope() as cache, safe_http.request_deadline_scope(60) as parent:
                cache.dns_cache[("seed.example", 443)] = ()
                cache.robots_cache["seed"] = object()
                expires = parent.expires_at
                with run_diagnostics.use_steps(parent_steps):
                    fragments = collect(prepared, parent_steps, calls)
                    assert run_diagnostics.current_steps() is parent_steps
                assert safe_http.active_deadline_budget() is parent
                assert parent.expires_at == expires
                assert set(cache.dns_cache) == {("seed.example", 443)}
                assert set(cache.robots_cache) == {"seed"}
    finally:
        SIGNAL.reset(token)
    assert len(fragments) == width and len(calls) == 1
    assert len({row[0] for row in transport.observed}) == width
    budgets = [row[1] for row in transport.observed]
    assert all(budget is not parent for budget in budgets)
    assert len({id(budget.dns_cache) for budget in budgets}) == width
    assert len({id(budget.robots_cache) for budget in budgets}) == width
    assert all(row[2] is not parent_steps for row in transport.observed)
    assert all(row[1].expires_at <= expires for row in transport.observed)
    assert all(item is signal for item in seen_signals) and len(seen_signals) > 2
    assert parent_steps[-1]["본문동시수집"]["동시상한"] == width
    assert parent_steps[-1]["본문동시수집"]["호스트동시상한"] == 1
    assert [row["주소"] for row in parent_steps if row["step"] == "기사조회"] == sorted(urls)
    assert not real._NEWS_BODY_RUNTIME_ACTIVE.get()


@pytest.mark.parametrize("setting", ["1", "0", "4", "broken", ""])
def test_environment_sequential_fallback_uses_calling_thread(monkeypatch, setting):
    monkeypatch.setenv(real.NEWS_BODY_CONCURRENCY_ENV, setting)
    transport = Transport()
    monkeypatch.setattr(real, "default_wide_transport", transport)
    steps, calls = [], []
    assert len(collect(session(), steps, calls)) == 2
    assert {row[0] for row in transport.observed} == {threading.get_ident()}
    assert steps[-1]["본문동시수집"]["동시상한"] == 1
    assert len(calls) == 1


@pytest.mark.parametrize("failed_article", [False, True])
def test_two_and_three_preserve_exact_evidence_and_analyzer_inputs_after_reordered_completion(monkeypatch, failed_article):
    urls = tuple(f"https://h{index}.example/article/{index}" for index in range(8))
    prepared = session(urls)
    ranked = diverse_candidates(list(prepared.snapshot.candidates), len(urls))
    first, second = (candidate.source_url for candidate in ranked[:2])
    failed_url = ranked[-1].source_url if failed_article else None
    results = []
    for width in (2, 3):
        second_finished = threading.Event()
        finished = []

        class ReorderedTransport(Transport):
            def __call__(self, url, allowed=None):
                if url == first:
                    assert second_finished.wait(WAIT_SECONDS), "둘째 기사가 먼저 끝나지 않았습니다"
                response = super().__call__(url, allowed)
                if not url.endswith("/robots.txt"):
                    with self.lock:
                        finished.append(url)
                    if url == second:
                        second_finished.set()
                # 같은 기사의 www 변형도 실패시켜 성공 폴백과 구분한다.
                if failed_url and not url.endswith("/robots.txt") and domain_host(url) == domain_host(failed_url):
                    return WideRawResponse(403, "", url, "text/html")
                return response

        transport = ReorderedTransport()
        monkeypatch.setenv(real.NEWS_BODY_CONCURRENCY_ENV, str(width))
        monkeypatch.setattr(real, "default_wide_transport", transport)
        steps, calls, analyzer_inputs = [], [], []
        answer = analyze_recording(calls)

        def analyze(prompt, schema, max_tokens):
            analyzer_inputs.append((str(prompt), schema, max_tokens))
            return answer(prompt, schema, max_tokens)

        fragments = real._collect_grounded_news(
            session=prepared, analyze=analyze, fetch_text=real._fetch_news_article_text,
            corp_id="00123456", official_web_documents=0, collected_on=AS_OF.isoformat(), steps=steps,
        )
        assert finished.index(second) < finished.index(first)
        assert len(calls) == 2
        diagnostics = dict(steps[-1])
        assert diagnostics.pop("본문동시수집")["동시상한"] == width
        assert diagnostics["분석AI호출"] == 2
        assert diagnostics["본문호출"] == len(urls)
        assert len(fragments) == len(urls) - int(failed_article)
        if failed_article:
            assert diagnostics["시도경고"]["fetch_http_403"] == 1
        results.append((fragments, analyzer_inputs, diagnostics, sorted(transport.requests)))
    assert results[0] == results[1]


def test_parallel_runtime_preserves_sequential_fragments_and_analysis_inputs(monkeypatch):
    prepared = session()
    sequential_steps, parallel_steps, sequential_calls, parallel_calls = [], [], [], []
    monkeypatch.setenv(real.NEWS_BODY_CONCURRENCY_ENV, "1")
    monkeypatch.setattr(real, "default_wide_transport", Transport())
    sequential = collect(prepared, sequential_steps, sequential_calls)
    monkeypatch.delenv(real.NEWS_BODY_CONCURRENCY_ENV)
    monkeypatch.setattr(real, "default_wide_transport", Transport(width=2))
    parallel = collect(prepared, parallel_steps, parallel_calls)
    assert parallel == sequential
    assert parallel_calls == sequential_calls and len(parallel_calls) == 1
    sequential_diagnostics = dict(sequential_steps[-1])
    parallel_diagnostics = dict(parallel_steps[-1])
    sequential_diagnostics.pop("본문동시수집")
    parallel_diagnostics.pop("본문동시수집")
    assert parallel_diagnostics == sequential_diagnostics


@pytest.mark.parametrize("width", ["1", "2", "3"])
def test_same_origin_reuses_completed_robots_without_mutating_parent(monkeypatch, width):
    monkeypatch.setenv(real.NEWS_BODY_CONCURRENCY_ENV, width)
    urls = tuple(f"https://a.example/article/{index}" for index in range(3))
    transport = Transport()
    monkeypatch.setattr(real, "default_wide_transport", transport)
    prepared = session(urls)
    calls, steps = [], []
    with safe_http.collection_cache_scope() as parent_cache:
        assert len(collect(prepared, steps, calls)) == 3
        assert not parent_cache.dns_cache and not parent_cache.robots_cache
    assert transport.requests.count("https://a.example/robots.txt") == 1
    assert len(transport.observed) == 3 and len(calls) == 1
    assert len({id(row[1].robots_cache) for row in transport.observed}) == 3


@pytest.mark.parametrize("width", ["1", "2", "3"])
def test_robots_and_article_keep_separate_original_time_allowances(monkeypatch, width):
    monkeypatch.setenv(real.NEWS_BODY_CONCURRENCY_ENV, width)
    prepared = session(URLS[:1])
    now = [100.0]
    monkeypatch.setattr(safe_http.time, "monotonic", lambda: now[0])
    # 각 요청은 기존 10초 이내지만 두 요청의 합은 10초를 넘는다.
    transport = Transport(on_request=lambda url: now.__setitem__(0, now[0] + 8.0))
    monkeypatch.setattr(real, "default_wide_transport", transport)
    steps, calls = [], []
    assert len(collect(prepared, steps, calls)) == 1
    assert now[0] == 116.0 and len(calls) == 1
    assert transport.requests == ["https://a.example/robots.txt", URLS[0]]
    assert safe_http.active_deadline_budget() is None


@pytest.mark.parametrize("replace_global", [False, True])
def test_unknown_injection_stays_sequential_even_with_matching_name(monkeypatch, replace_global):
    threads = []

    def _fetch_news_article_text(url):
        threads.append(threading.get_ident())
        assert not real._NEWS_BODY_RUNTIME_ACTIVE.get()
        number = 120 + int(url.rsplit("/", 1)[-1])
        return f"가나다전자는 기업용 산업설비 제조 사업을 운영하며 자동화 설비 {number}대를 공급했다고 밝혔다."

    if replace_global:
        monkeypatch.setattr(real, "_fetch_news_article_text", _fetch_news_article_text)
    steps, calls = [], []
    assert len(collect(session(), steps, calls, fetch=_fetch_news_article_text)) == 2
    assert threads == [threading.get_ident()] * 2
    assert steps[-1]["본문동시수집"]["동시상한"] == 1


@pytest.mark.parametrize("kind", [CancelledError, generation_coordination.GenerationWaitCancelled,
                                  generation_coordination.GenerationExecutionDeadlineExceeded])
def test_preexisting_stop_propagates_without_network_or_analysis(monkeypatch, kind):
    prepared = session()
    transport = Transport()
    monkeypatch.setattr(real, "default_wide_transport", transport)
    steps, calls = [], []

    def check():
        raise kind("요청이 중단됐습니다")

    with active_request(check), pytest.raises(kind):
        collect(prepared, steps, calls)
    assert not transport.requests and not calls
    assert not real._NEWS_BODY_RUNTIME_ACTIVE.get()


def test_cancellation_during_robots_prevents_article_and_variants(monkeypatch):
    prepared = session()
    signal = threading.Event()
    transport = Transport(on_request=lambda url: signal.set())
    monkeypatch.setattr(real, "default_wide_transport", transport)
    calls, steps = [], []

    def check():
        if signal.is_set():
            raise generation_coordination.GenerationWaitCancelled("조회 중 취소됐습니다")

    with active_request(check), pytest.raises(generation_coordination.GenerationWaitCancelled):
        collect(prepared, steps, calls)
    assert transport.requests and all(url.endswith("/robots.txt") for url in transport.requests)
    assert len(transport.requests) <= 2 and not calls


@pytest.mark.parametrize("expire_during_robots", [False, True])
def test_parent_absolute_deadline_blocks_new_transport_and_variants(monkeypatch, expire_during_robots):
    prepared = session()
    now = [100.0]
    transport = Transport(on_request=lambda url: now.__setitem__(0, 106.0))
    monkeypatch.setattr(real, "default_wide_transport", transport)
    calls, steps = [], []
    with safe_http.request_deadline_scope(5, clock=lambda: now[0]) as parent:
        if not expire_during_robots:
            now[0] = 106.0
        assert collect(prepared, steps, calls) == []
        assert safe_http.active_deadline_budget() is parent and parent.expires_at == 105.0
    assert not calls
    assert all(url.endswith("/robots.txt") for url in transport.requests)
    assert bool(transport.requests) is expire_during_robots
    assert "fetch_timeout" in repr(steps)


@pytest.mark.parametrize("url", ["http://media.example/a", "https://www.media.example/a",
                               "http://MEDIA.example:8080/a", "https://media.example./a"])
def test_production_url_variants_stay_in_collector_host_slot(url):
    variants = real.news_url_variants(url)
    assert variants
    assert all(domain_host(variant) == domain_host(url) for variant in variants)


@pytest.mark.parametrize("width", [2, 3])
@pytest.mark.parametrize("stop", [False, True])
def test_www_variants_share_host_slot_and_waiting_request_observes_cancellation(monkeypatch, stop, width):
    monkeypatch.setenv(real.NEWS_BODY_CONCURRENCY_ENV, str(width))
    urls = ("https://media.example/article/0", "https://www.media.example/article/1",
            "https://media.example/article/2")
    prepared = session(urls)
    entered, waiting, release, cancel = (threading.Event() for _ in range(4))
    original_slot = HostSlots.slot
    slot_calls = []

    @contextmanager
    def observed_slot(self, url):
        slot_calls.append(url)
        if len(slot_calls) == width:
            waiting.set()
        with original_slot(self, url):
            yield

    monkeypatch.setattr(HostSlots, "slot", observed_slot)

    def gate(url):
        if not url.endswith("/robots.txt") and not entered.is_set():
            entered.set()
            assert release.wait(WAIT_SECONDS), "첫 본문 요청이 해제되지 않았습니다"

    transport = Transport(on_request=gate)
    monkeypatch.setattr(real, "default_wide_transport", transport)

    def check():
        if cancel.is_set():
            raise generation_coordination.GenerationWaitCancelled("호스트 대기 중 취소됐습니다")

    steps, calls = [], []
    with active_request(check), ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(contextvars.copy_context().run, lambda: collect(prepared, steps, calls))
        try:
            assert entered.wait(WAIT_SECONDS) and waiting.wait(WAIT_SECONDS)
            assert len([url for url in transport.requests if not url.endswith("/robots.txt")]) == 1
            if stop:
                cancel.set()
        finally:
            release.set()
        if stop:
            with pytest.raises(generation_coordination.GenerationWaitCancelled):
                future.result(WAIT_SECONDS)
        else:
            assert len(future.result(WAIT_SECONDS)) == 3
    assert len(transport.observed) == (1 if stop else 3)
    assert len(calls) == (0 if stop else 1)


@pytest.mark.parametrize("kind", [CancelledError, generation_coordination.GenerationWaitCancelled])
def test_default_three_joins_inflight_articles_on_failure_before_returning(monkeypatch, kind):
    urls = (*URLS, "https://d.example/article/3")
    prepared = session(urls)
    ranked = diverse_candidates(list(prepared.snapshot.candidates), len(urls))
    first_wave = {candidate.source_url for candidate in ranked[:3]}
    failing_url = ranked[0].source_url
    barrier = threading.Barrier(3)
    failed, joining, release = (threading.Event() for _ in range(3))
    settled = []
    error = kind("첫 기사 요청에서 중단됐습니다")
    original_close = BodyFetchLane.close

    def observed_close(lane):
        joining.set()
        original_close(lane)
        assert set(settled) == first_wave, "실행기를 닫기 전에 모든 요청이 끝나야 합니다"

    monkeypatch.setattr(BodyFetchLane, "close", observed_close)

    def gate(url):
        if url.endswith("/robots.txt"):
            return
        try:
            barrier.wait(WAIT_SECONDS)
            if url == failing_url:
                raise error
            assert release.wait(WAIT_SECONDS), "진행 중 기사를 해제하지 않았습니다"
        finally:
            settled.append(url)
            if url == failing_url:
                failed.set()

    transport = Transport(on_request=gate)
    monkeypatch.setattr(real, "default_wide_transport", transport)
    steps, calls = [], []
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(contextvars.copy_context().run, lambda: collect(prepared, steps, calls))
        try:
            assert failed.wait(WAIT_SECONDS) and joining.wait(WAIT_SECONDS)
            assert not future.done(), "진행 중 본문 요청을 기다리지 않고 반환했습니다"
            assert set(settled) == {failing_url}
        finally:
            release.set()
        with pytest.raises(kind) as caught:
            future.result(WAIT_SECONDS)
    assert caught.value is error
    assert set(settled) == first_wave
    assert {url for url in transport.requests if not url.endswith("/robots.txt")} == first_wave
    assert not calls and not real._NEWS_BODY_RUNTIME_ACTIVE.get()
