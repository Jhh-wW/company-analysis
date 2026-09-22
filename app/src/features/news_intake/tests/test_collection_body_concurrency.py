"""같은 기간·같은 분석 묶음 안에서 본문 요청을 겹쳐도 선택·순서·예산 계약이 그대로인지 검증한다.

``collect_from_snapshot`` 전체를 통과하는 통합 시험이다(사슬·원장 단위 시험은
``test_body_prefetch.py``). 가짜 본문 콜백은 외부 통신 없이 각 요청을 시험이 열어
줄 때까지 붙잡는다. 그래서 «몇 개가 동시에 떠 있는가»·«완료 순서를 뒤집어도 소비
순서가 같은가»를 결정적으로 관측한다. 유료 분석 호출은 ``test_collection.analyzer``의
가짜 응답으로 대신한다.
"""

from __future__ import annotations

import threading
import time as real_time
from collections import Counter
from concurrent.futures import CancelledError
from dataclasses import replace
from types import SimpleNamespace
from typing import Callable

import pytest

from src.features.news_intake import collection as collection_service
from src.features.news_intake import constants as c
from src.features.news_intake.body_prefetch import (
    ArticleFetchJob, BodyFetchConcurrency, CallBudgetPool, CallLease, fetch_article_body,
)
from src.features.news_intake.collection import collect_from_snapshot
from src.features.news_intake.models import NewsBodyFetchResult
from src.features.news_intake.search_snapshot import diverse_candidates, domain_host
from src.features.news_intake.tests.test_collection import AS_OF, BODY, COMPANY, POLICY, analyzer, item, snapshot
from src.features.news_intake.tests.test_window_budget import paged_snapshot, unique_body
from src.shared.engine_build_identity import EngineBuildIdentityChangedError
from src.shared.generation_coordination import GenerationWaitCancelled


THREE_WIDE = BodyFetchConcurrency(max_in_flight=3, max_per_host=1)
WAIT_SECONDS = 10.0
SETTLE_SECONDS = 0.15
HOSTS = tuple(f"h{number}.example" for number in range(8))
WIDE_POLICY = replace(POLICY, trusted_publisher_domains=POLICY.trusted_publisher_domains + HOSTS)


class GatedFetch:
    """요청을 기록하고 시험이 열어 줄 때까지 각 요청을 붙잡는 가짜 본문 콜백."""

    def __init__(self, body_for: Callable[[str], object] = unique_body, *, gated: bool = True) -> None:
        self.lock = threading.Lock()
        self.started: list[str] = []
        self.finished: list[str] = []
        self.gates: dict[str, threading.Event] = {}
        self.active = 0
        self.max_active = 0
        self.active_by_host: Counter[str] = Counter()
        self.max_active_by_host: Counter[str] = Counter()
        self.body_for = body_for
        self.gated = gated

    def __call__(self, url: str) -> object:
        host = domain_host(url)
        with self.lock:
            self.started.append(url)
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.active_by_host[host] += 1
            self.max_active_by_host[host] = max(self.max_active_by_host[host], self.active_by_host[host])
            gate = self.gates.setdefault(url, threading.Event())
        if self.gated:
            assert gate.wait(WAIT_SECONDS), f"시험이 요청을 열어 주지 않았습니다: {url}"
        with self.lock:
            self.active -= 1
            self.active_by_host[host] -= 1
            self.finished.append(url)
        return self.body_for(url)

    def release(self, url: str) -> None:
        with self.lock:
            gate = self.gates.setdefault(url, threading.Event())
        gate.set()

    def release_all(self) -> None:
        with self.lock:
            urls = list(self.started)
        for url in urls:
            self.release(url)

    def wait_started(self, count: int) -> list[str]:
        deadline = real_time.monotonic() + WAIT_SECONDS
        while real_time.monotonic() < deadline:
            with self.lock:
                if len(self.started) >= count:
                    return list(self.started)
            real_time.sleep(0.005)
        with self.lock:
            pytest.fail(f"요청 {count}개가 시작되지 않았습니다: {self.started}")

    def started_count(self) -> int:
        with self.lock:
            return len(self.started)

    def wait_finished(self, count: int) -> None:
        deadline = real_time.monotonic() + WAIT_SECONDS
        while real_time.monotonic() < deadline:
            with self.lock:
                if len(self.finished) >= count:
                    return
            real_time.sleep(0.005)
        with self.lock:
            pytest.fail(f"요청 {count}개가 끝나지 않았습니다: {self.finished}")


def run_in_thread(target: Callable[[], object]) -> tuple[threading.Thread, dict[str, object]]:
    box: dict[str, object] = {}

    def runner() -> None:
        try:
            box["result"] = target()
        except BaseException as error:  # noqa: BLE001 - 시험 스레드의 예외를 본 스레드로 옮긴다
            box["error"] = error

    thread = threading.Thread(target=runner, name="collect-under-test")
    thread.start()
    return thread, box


def finish(thread: threading.Thread, box: dict[str, object]):
    thread.join(WAIT_SECONDS)
    assert not thread.is_alive(), "수집이 제한 시간 안에 끝나지 않았습니다"
    if "error" in box:
        raise box["error"]  # type: ignore[misc]
    return box["result"]


def comparable(result) -> tuple:
    """동시 실행 정산 블록만 빼고 순차 실행과 완전히 같아야 하는 부분."""

    diagnostics = dict(result.diagnostics)
    diagnostics.pop("본문동시수집")
    return result.fragments, result.articles, result.document_hashes, diagnostics


def analysis_order(calls: list[dict]) -> list[list[str]]:
    return [[article["url"] for article in call["articles"]] for call in calls]


def collect_with(snap, *, fetch, policy=WIDE_POLICY, body_fetch=None, analysis=None):
    return collect_from_snapshot(snap, company=COMPANY, as_of=AS_OF, fetch_text=fetch,
                                 analyze_grounded=analyzer(calls=analysis), policy=policy, body_fetch=body_fetch)


@pytest.mark.parametrize("body_fetch", [None, THREE_WIDE])
@pytest.mark.parametrize("error_type", [GenerationWaitCancelled, EngineBuildIdentityChangedError, CancelledError])
def test_analysis_stop_propagates_before_another_body_batch(body_fetch, error_type):
    snap, _ = snapshot(distinct_host_items(8), policy=WIDE_POLICY)
    fetched = []
    analyses = []
    error = error_type("시험용 분석 중 요청 전체 중단")

    def fetch(url):
        fetched.append(url)
        return unique_body(url)

    def analyze(*args):
        analyses.append(args)
        raise error

    with pytest.raises(error_type) as caught:
        collect_from_snapshot(
            snap, company=COMPANY, as_of=AS_OF, fetch_text=fetch,
            analyze_grounded=analyze, policy=WIDE_POLICY, body_fetch=body_fetch,
        )
    assert caught.value is error and len(analyses) == 1
    assert len(fetched) == WIDE_POLICY.batch_size


def distinct_host_items(count: int, **overrides):
    return [item(number, host=HOSTS[number % len(HOSTS)], **overrides) for number in range(count)]


# ------------------------------------------------------------- opt-in 계약


@pytest.mark.parametrize("in_flight,per_host", [(0, 1), (c.BODY_FETCH_CONCURRENCY_MAX + 1, 1), (True, 1),
                                                (2, 0), (2, 3), (1, 2), (3, 2.0)])
def test_동시요청_폭은_절대상한과_호스트상한을_지킨다(in_flight, per_host):
    with pytest.raises(ValueError, match="상한|폭"):
        BodyFetchConcurrency(max_in_flight=in_flight, max_per_host=per_host)


def test_기본_optin은_두개_동시와_호스트당_하나다():
    default = BodyFetchConcurrency()
    assert default.max_in_flight == c.BODY_FETCH_CONCURRENCY_DEFAULT == 2
    assert default.max_per_host == c.BODY_FETCH_PER_HOST_LIMIT == 1
    assert c.BODY_FETCH_SEQUENTIAL == 1 and c.BODY_FETCH_CONCURRENCY_MAX == 3


def test_optin이_없으면_순차이고_진단에_동시상한1을_남긴다():
    result = collect_with(snapshot(distinct_host_items(2), policy=WIDE_POLICY)[0], fetch=unique_body)
    assert result.diagnostics["본문동시수집"] == {
        "동시상한": 1, "호스트동시상한": 1, "출발": 2, "선행미사용": 0, "선행미사용호출": 0, "취소": 0,
    }
    assert result.diagnostics["기간별"]["12"]["선행미사용"] == 0


# ------------------------------------------------------------- 동시 실행과 순서


def test_세_기사가_실제로_겹쳐_요청되고_역순완료에도_분석순서와_결과는_순차와_같다():
    snap, _ = snapshot(distinct_host_items(3), policy=WIDE_POLICY)
    sequential_analysis: list[dict] = []
    sequential = collect_with(snap, fetch=unique_body, analysis=sequential_analysis)

    fetch = GatedFetch()
    concurrent_analysis: list[dict] = []
    thread, box = run_in_thread(lambda: collect_with(snap, fetch=fetch, body_fetch=THREE_WIDE,
                                                     analysis=concurrent_analysis))
    started = fetch.wait_started(3)
    assert fetch.max_active == 3
    # 마지막에 출발한 요청부터 하나씩 끝내 완료 순서를 확실히 뒤집는다.
    for finished_count, url in enumerate(reversed(started), start=1):
        fetch.release(url)
        fetch.wait_finished(finished_count)
    concurrent = finish(thread, box)

    ranked = [candidate.source_url for candidate in diverse_candidates(list(snap.candidates), 3)]
    assert started == ranked, "출발 순서는 후보 순위 순서다"
    assert fetch.finished == list(reversed(ranked)), "완료 순서는 뒤집혔다"
    assert analysis_order(concurrent_analysis) == analysis_order(sequential_analysis) == [ranked]
    assert comparable(concurrent) == comparable(sequential)
    assert concurrent.diagnostics["본문동시수집"] == {
        "동시상한": 3, "호스트동시상한": 1, "출발": 3, "선행미사용": 0, "선행미사용호출": 0, "취소": 0,
    }


def test_같은_호스트는_동시폭이_3이어도_한번에_하나만_요청한다():
    snap, _ = snapshot([item(number) for number in range(3)], policy=WIDE_POLICY)
    sequential = collect_with(snap, fetch=unique_body)
    fetch = GatedFetch()
    thread, box = run_in_thread(lambda: collect_with(snap, fetch=fetch, body_fetch=THREE_WIDE))
    for count in range(1, 4):
        started = fetch.wait_started(count)
        real_time.sleep(SETTLE_SECONDS)
        assert fetch.started_count() == count, "같은 호스트의 다음 요청은 앞 요청이 끝나야 떠난다"
        fetch.release(started[-1])
    concurrent = finish(thread, box)
    assert fetch.max_active == fetch.max_active_by_host["media.example"] == 1
    assert comparable(concurrent) == comparable(sequential)


def test_호스트가_다른_기사만_겹치고_같은_호스트는_직렬화한다():
    items = [item(0, host="h0.example"), item(1, host="h0.example"), item(2, host="h1.example")]
    snap, _ = snapshot(items, policy=WIDE_POLICY)
    sequential = collect_with(snap, fetch=unique_body)
    fetch = GatedFetch()
    thread, box = run_in_thread(lambda: collect_with(snap, fetch=fetch, body_fetch=THREE_WIDE))
    fetch.wait_started(2)
    real_time.sleep(SETTLE_SECONDS)
    assert fetch.started_count() == 2
    assert {domain_host(url) for url in fetch.started} == {"h0.example", "h1.example"}
    fetch.release_all()
    fetch.wait_started(3)
    fetch.release_all()
    concurrent = finish(thread, box)
    assert fetch.max_active == 2
    assert max(fetch.max_active_by_host.values()) == 1
    assert comparable(concurrent) == comparable(sequential)


def test_선행요청은_분석묶음의_빈자리를_넘지_않고_분석중에는_진행중_요청이_없다():
    policy = replace(WIDE_POLICY, batch_size=2)
    snap, _ = snapshot(distinct_host_items(6), policy=policy)
    sequential = collect_with(snap, fetch=unique_body, policy=policy)
    fetch = GatedFetch()
    violations: list[int] = []
    base = analyzer()

    def analyze(prompt, schema, max_tokens):
        with fetch.lock:
            if fetch.active:
                violations.append(fetch.active)
        return base(prompt, schema, max_tokens)

    thread, box = run_in_thread(lambda: collect_from_snapshot(
        snap, company=COMPANY, as_of=AS_OF, fetch_text=fetch, analyze_grounded=analyze,
        policy=policy, body_fetch=THREE_WIDE))
    for round_start in (0, 2, 4):
        started = fetch.wait_started(round_start + 2)
        real_time.sleep(SETTLE_SECONDS)
        assert fetch.started_count() == round_start + 2, "묶음 크기 2를 넘는 세 번째 요청은 뜨지 않는다"
        fetch.release(started[round_start])
        fetch.release(started[round_start + 1])
    concurrent = finish(thread, box)
    assert violations == []
    assert fetch.max_active == 2
    assert comparable(concurrent) == comparable(sequential)


def test_다음_기간의_후보는_앞_기간이_끝나기_전에_요청하지_않는다():
    items = distinct_host_items(2) + [item(number, host=HOSTS[number], date="2025-01-01") for number in (2, 3)]
    snap, _ = snapshot(items, policy=WIDE_POLICY)
    sequential_analysis: list[dict] = []
    sequential = collect_with(snap, fetch=unique_body, analysis=sequential_analysis)
    fetch = GatedFetch()
    concurrent_analysis: list[dict] = []
    thread, box = run_in_thread(lambda: collect_with(snap, fetch=fetch, body_fetch=THREE_WIDE,
                                                     analysis=concurrent_analysis))
    fetch.wait_started(2)
    real_time.sleep(SETTLE_SECONDS)
    assert fetch.started_count() == 2, "24개월 후보는 12개월 기간이 끝나기 전에 뜨지 않는다"
    recent = {candidate.source_url for candidate in snap.candidates if candidate.published_on == "2026-09-01"}
    assert set(fetch.started) == recent
    fetch.release_all()
    fetch.wait_started(4)
    fetch.release_all()
    concurrent = finish(thread, box)
    assert set(fetch.started[2:]) == {candidate.source_url for candidate in snap.candidates} - recent
    assert analysis_order(concurrent_analysis) == analysis_order(sequential_analysis)
    assert comparable(concurrent) == comparable(sequential)


def test_최근기사로_충분하면_동시모드도_과거본문을_읽지_않고_같은_기사수로_끝난다():
    items = distinct_host_items(6) + [item(6, host=HOSTS[6], date="2024-01-01")]
    policy = replace(WIDE_POLICY, batch_size=2)
    snap, _ = snapshot(items, policy=policy)

    def varied(rows, payload):
        for row, article in zip(rows, payload["articles"]):
            number = int(article["url"].rsplit("/", 1)[-1])
            row["excerpts"][0]["topic"] = ("products", "partnerships", "strategy")[number % 3]
        return rows

    def bodies(url):
        return BODY.replace("120대", str(120 + int(url.rsplit("/", 1)[-1])) + "대")

    results = {}
    calls = {}
    for label, body_fetch in (("sequential", None), ("concurrent", THREE_WIDE)):
        seen: list[str] = []

        def fetch(url, seen=seen):
            seen.append(url)
            return bodies(url)

        results[label] = collect_from_snapshot(snap, company=COMPANY, as_of=AS_OF, fetch_text=fetch,
                                               analyze_grounded=analyzer(varied), policy=policy,
                                               body_fetch=body_fetch)
        calls[label] = seen
    for label in results:
        assert len(calls[label]) == 6 and not any(url.endswith("/6") for url in calls[label])
        assert results[label].diagnostics["독립기사"] == 6
        assert results[label].diagnostics["기간개월"] == (12,)
        assert results[label].diagnostics["완전성"] == "sufficient"
    assert comparable(results["concurrent"]) == comparable(results["sequential"])
    assert results["concurrent"].diagnostics["본문동시수집"]["선행미사용"] == 0


# ------------------------------------------------------------- 실패·예외·순차 동치


def test_한_기사의_예외와_실패는_다른_기사와_순서에_영향을_주지_않는다():
    snap, _ = snapshot(distinct_host_items(4), policy=WIDE_POLICY)

    def fetch(url):
        number = int(url.rsplit("/", 1)[-1])
        if number == 1:
            raise RuntimeError("전송 계층 오류")
        if number == 2:
            return NewsBodyFetchResult(reason_code="fetch_http_404")
        return unique_body(url)

    sequential_analysis: list[dict] = []
    sequential = collect_with(snap, fetch=fetch, analysis=sequential_analysis)
    concurrent_analysis: list[dict] = []
    concurrent = collect_with(snap, fetch=fetch, body_fetch=THREE_WIDE, analysis=concurrent_analysis)
    assert sequential.diagnostics["시도경고"] == {"fetch_failed": 1, "fetch_http_404": 1}
    assert sequential.diagnostics["실패"] == "fetch_http_404"
    assert analysis_order(concurrent_analysis) == analysis_order(sequential_analysis)
    assert comparable(concurrent) == comparable(sequential)


def test_포털폴백_날짜보정_이월_중복본문이_섞여도_동시모드는_순차와_같은_결과를_낸다():
    items = []
    for number in range(30):
        row = item(number, host=("media.example", "specialist.example", "h0.example")[number % 3])
        row.link = f"https://n.news.naver.com/mnews/article/001/{number}"
        items.append(row)
    items.append(item(30, host="h1.example", date="2025-01-01"))
    snap = paged_snapshot(items, WIDE_POLICY)

    def fetch(url):
        number = int(url.rsplit("/", 1)[-1])
        if "naver.com" in url:
            return unique_body(url) if number % 5 == 0 else NewsBodyFetchResult(reason_code="fetch_http_403")
        if number % 5 == 0:
            return NewsBodyFetchResult(reason_code="fetch_http_403")
        if number % 7 == 0:
            return NewsBodyFetchResult(text=unique_body(url), stage=c.BODY_STAGE_ARTICLE_TAG,
                                       published_on="2024-01-01")
        if number % 11 == 0:
            return unique_body(url.replace(str(number), str(number - 1)))
        return unique_body(url)

    sequential_analysis: list[dict] = []
    sequential = collect_with(snap, fetch=fetch, analysis=sequential_analysis)
    concurrent_analysis: list[dict] = []
    concurrent = collect_with(snap, fetch=fetch, body_fetch=THREE_WIDE, analysis=concurrent_analysis)
    assert sequential.diagnostics["기간별"]["12"]["이월"] >= 1
    assert sequential.diagnostics["제외"]["duplicate_article_body"] >= 1
    assert sequential.diagnostics["시도경고"]["fetch_http_403"] >= 1
    assert analysis_order(concurrent_analysis) == analysis_order(sequential_analysis)
    assert comparable(concurrent) == comparable(sequential)
    assert concurrent.diagnostics["본문호출"] == sequential.diagnostics["본문호출"]


# ------------------------------------------------------------- 상한·마감·중단 정산


def test_호출상한은_동시모드에서도_절대_넘지_않고_마지막_몫은_순차규칙으로_쓴다():
    items = distinct_host_items(12)
    for number, row in enumerate(items):
        row.link = f"https://n.news.naver.com/mnews/article/001/{number}"
    policy = replace(WIDE_POLICY, max_body_calls=7)
    snap, _ = snapshot(items, policy=policy)
    results = {}
    calls = {}
    for label, body_fetch in (("sequential", None), ("concurrent", THREE_WIDE)):
        seen: list[str] = []
        lock = threading.Lock()

        def fetch(url, seen=seen, lock=lock):
            with lock:
                seen.append(url)
            real_time.sleep(0.002)
            return NewsBodyFetchResult(reason_code="fetch_http_403")

        results[label] = collect_with(snap, fetch=fetch, policy=policy, body_fetch=body_fetch)
        calls[label] = seen
    for label in results:
        assert len(calls[label]) == results[label].diagnostics["본문호출"] == 7
        assert results[label].diagnostics["본문시도기사"] == 4
        assert results[label].diagnostics["상한사유"] == ("body_budget_exhausted",)
    assert comparable(results["concurrent"]) == comparable(results["sequential"])


def test_마감이_지나면_새_사슬은_출발하지_않고_이미_떠난_요청만_정산한다(monkeypatch):
    snap, _ = snapshot(distinct_host_items(4), policy=WIDE_POLICY)
    elapsed = 0.0
    monkeypatch.setattr(collection_service, "time", SimpleNamespace(monotonic=lambda: elapsed))
    fetch = GatedFetch()
    analysis: list[dict] = []
    thread, box = run_in_thread(lambda: collect_with(
        snap, fetch=fetch, body_fetch=BodyFetchConcurrency(max_in_flight=2, max_per_host=1), analysis=analysis))
    fetch.wait_started(2)
    elapsed = float(WIDE_POLICY.max_collection_seconds)
    fetch.release_all()
    result = finish(thread, box)
    assert fetch.started_count() == 2, "마감 뒤에는 세 번째·네 번째 후보를 요청하지 않는다"
    assert result.diagnostics["본문호출"] == result.diagnostics["본문시도기사"] == 2
    assert not analysis and result.diagnostics["분석AI호출"] == 0
    assert "body_budget_exhausted" in result.diagnostics["상한사유"]
    assert result.diagnostics["본문동시수집"]["출발"] == 2
    assert result.diagnostics["본문동시수집"]["취소"] == 0


def test_호스트_자리를_기다리던_사슬은_마감_뒤_요청없이_취소로_정산된다(monkeypatch):
    snap, _ = snapshot([item(number) for number in range(3)], policy=WIDE_POLICY)
    elapsed = 0.0
    monkeypatch.setattr(collection_service, "time", SimpleNamespace(monotonic=lambda: elapsed))
    fetch = GatedFetch()
    thread, box = run_in_thread(lambda: collect_with(snap, fetch=fetch, body_fetch=THREE_WIDE))
    started = fetch.wait_started(1)
    real_time.sleep(SETTLE_SECONDS)
    assert fetch.started_count() == 1, "같은 호스트의 나머지 두 사슬은 자리를 기다린다"
    elapsed = float(WIDE_POLICY.max_collection_seconds)
    fetch.release(started[0])
    result = finish(thread, box)
    assert fetch.started_count() == 1
    assert result.diagnostics["본문호출"] == result.diagnostics["본문시도기사"] == 1
    assert result.diagnostics["본문동시수집"] == {
        "동시상한": 3, "호스트동시상한": 1, "출발": 3, "선행미사용": 0, "선행미사용호출": 0, "취소": 1,
    }
    assert result.diagnostics["기간별"]["12"]["시도"] == 1
    assert "body_budget_exhausted" in result.diagnostics["상한사유"]


def test_글자예산으로_멈추면_이미_보낸_선행요청은_버리되_호출과_진단에_남긴다():
    preview, _ = snapshot(distinct_host_items(3), policy=WIDE_POLICY)
    ranked = diverse_candidates(list(preview.candidates), 3)
    # 첫 기사 본문이 예산을 거의 다 채우고 둘째 기사는 25자 미만만 남게 만든다. 검색
    # 스냅샷은 정책 지문에 묶이므로 바뀐 정책으로 다시 고정한다.
    policy = replace(WIDE_POLICY, max_total_body_chars=len(unique_body(ranked[0].source_url)) + 10)
    snap, _ = snapshot(distinct_host_items(3), policy=policy)
    sequential = collect_with(snap, fetch=unique_body, policy=policy)
    fetch = GatedFetch()
    thread, box = run_in_thread(lambda: collect_with(snap, fetch=fetch, policy=policy, body_fetch=THREE_WIDE))
    fetch.wait_started(3)
    fetch.release_all()
    concurrent = finish(thread, box)
    assert sequential.diagnostics["본문호출"] == sequential.diagnostics["본문시도기사"] == 2
    assert "body_character_budget" in sequential.diagnostics["상한사유"]
    assert concurrent.fragments == sequential.fragments and concurrent.articles == sequential.articles
    assert concurrent.diagnostics["상한사유"] == sequential.diagnostics["상한사유"]
    assert concurrent.diagnostics["본문호출"] == concurrent.diagnostics["본문시도기사"] == 3
    assert concurrent.diagnostics["본문동시수집"]["선행미사용"] == 1
    assert concurrent.diagnostics["본문동시수집"]["선행미사용호출"] == 1
    assert concurrent.diagnostics["기간별"]["12"]["선행미사용"] == 1


# ------------------------------------------------------------- 사슬·원장 단위(통합 경로와 같은 입력)


def job_for(fetch, *, pool=None, stop_event=None, deadline=1e9, policy=WIDE_POLICY):
    return ArticleFetchJob(company=COMPANY, policy=policy, as_of=AS_OF, fetch_text=fetch,
                           budget=pool or CallBudgetPool(policy.max_body_calls), deadline=deadline,
                           clock=real_time.monotonic, stop_event=stop_event or threading.Event())


def test_중단신호가_켜져_있으면_사슬은_요청을_보내지_않는다():
    snap, _ = snapshot([item()], policy=WIDE_POLICY)
    calls: list[str] = []
    stop_event = threading.Event()
    stop_event.set()
    outcome = fetch_article_body(job_for(calls.append, stop_event=stop_event), snap.candidates[0], CallLease())
    assert outcome.stop_requested and outcome.calls_made == 0 and outcome.urls_examined == 0
    assert calls == [] and outcome.read_candidate is None


def test_첫_주소_실패_뒤_중단신호가_켜지면_포털_폴백을_요청하지_않는다():
    row = item()
    row.link = "https://n.news.naver.com/mnews/article/001/1"
    snap, _ = snapshot([row], policy=WIDE_POLICY)
    stop_event = threading.Event()
    calls: list[str] = []

    def fetch(url):
        calls.append(url)
        stop_event.set()
        return NewsBodyFetchResult(reason_code="fetch_http_403")

    outcome = fetch_article_body(job_for(fetch, stop_event=stop_event), snap.candidates[0], CallLease())
    assert calls == [snap.candidates[0].source_url]
    assert outcome.stop_requested and outcome.calls_made == 1 and outcome.urls_examined == 1
    assert outcome.article_failures == ("fetch_http_403",)
    assert outcome.warnings == Counter({"fetch_http_403": 1})


def test_사슬은_남는_예약을_돌려주고_원장은_스레드가_몰려도_상한을_넘기지_않는다():
    pool = CallBudgetPool(2)
    lease = CallLease()
    assert pool.try_reserve(2, lease) and pool.unreserved == 0
    row = item()
    row.link = "https://n.news.naver.com/mnews/article/001/1"
    snap, _ = snapshot([row], policy=WIDE_POLICY)
    outcome = fetch_article_body(job_for(lambda url: unique_body(url), pool=pool), snap.candidates[0], lease)
    assert outcome.read_candidate is not None and outcome.calls_made == 1
    assert pool.made == 1 and pool.unreserved == 1 and lease.reserved == 0

    stress = CallBudgetPool(20)
    granted: list[bool] = []
    lock = threading.Lock()

    def worker():
        own = CallLease()
        for _ in range(50):
            ok = stress.consume(own)
            with lock:
                granted.append(ok)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(WAIT_SECONDS)
    assert stress.made == 20 and granted.count(True) == 20
    assert not stress.try_reserve(1, CallLease())
