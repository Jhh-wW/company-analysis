"""같은 기간·같은 분석 묶음 안의 기사 본문 요청을 제한적으로 겹치는 실행 차선.

``collection.collect_from_snapshot``가 기사 한 건의 주소 사슬(언론사 원문 →
포털 폴백)을 읽는 부분을 이 모듈의 ``fetch_article_body``로 떼어 냈다. 사슬
안의 판정(신뢰 도메인·메타 설명·미처리 HTML·해독 깨짐·발행일·너무 짧은
본문)은 후보와 정책만으로 결정되므로 어느 스레드에서 돌려도 같다. 반면
«앞 기사의 실제 주소와 겹치는가»·«본문 글자 예산»·«분석 묶음»처럼 앞 기사
결과에 기대는 판정은 여기서 하지 않고 호출자가 후보 순서대로 소비하면서
한다 — 완료 순서가 뒤집혀도 기사 선택·순서·원문이 순차 실행과 같아야 한다.

★ 동시 실행은 호출자의 명시적 opt-in(``BodyFetchConcurrency``)이 있을 때만
  켠다. ``fetch_text`` 콜백을 여러 스레드에서 동시에 불러도 안전하다는
  보증은 콜백을 만든 쪽만 할 수 있다. ``contextvars.copy_context()``로 문맥을
  복사해 넘기지만, 복사는 ContextVar 값 «참조»를 복사할 뿐 그 안의 공유
  dict·가변 카운터를 격리하지 않는다 — 그 격리 책임은 opt-in한 호출자에게 있다.

★ 요청 상한(``max_body_calls``)은 ``CallBudgetPool``이 원장으로 관리한다.
  사슬을 띄울 때 그 기사가 쓸 수 있는 최대 요청 수를 먼저 예약하고, 남는
  예약은 사슬이 끝나면 돌려준다. 예약할 몫이 모자라면 다른 사슬이 모두 끝난
  뒤 순차 규칙(주소마다 남은 호출 확인)으로만 띄운다 — 마지막 호출 몫이 뒤
  순위 기사에 먼저 넘어가는 일을 막는다.
"""

from __future__ import annotations

import contextvars
import datetime as dt
import threading
from collections import Counter
from concurrent.futures import CancelledError, Future, ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import Callable, Iterator

from src.features.news_intake import constants as c
from src.features.news_intake.fetch import body_fetch_urls, decode_looks_broken, normalize_body_result
from src.features.news_intake.models import NewsCandidate, NewsCollectionPolicy, NewsCompanyContext
from src.features.news_intake.search_snapshot import (
    candidate_window, domain_host, host_matches, source_category,
)
from src.shared.report_quality.source_identity import canonical_url
from src.shared.engine_build_identity import EngineBuildIdentityChangedError
from src.shared.generation_coordination import GenerationCoordinationError


@dataclass(frozen=True)
class BodyFetchConcurrency:
    """호출자가 «본문 콜백을 여러 스레드에서 동시에 불러도 안전하다»고 선언하는 opt-in.

    이 객체를 넘기지 않으면 수집은 오늘과 같이 순차로 돈다. 정책
    (``NewsCollectionPolicy``)과 달리 검색·정책 지문에 들어가지 않는다 —
    실행 폭은 결과를 바꾸지 않아야 하는 «실행 방식»이고, 지문에 넣으면
    캐시·재현 결속만 깨진다.
    """

    max_in_flight: int = c.BODY_FETCH_CONCURRENCY_DEFAULT
    max_per_host: int = c.BODY_FETCH_PER_HOST_LIMIT

    def __post_init__(self) -> None:
        if type(self.max_in_flight) is not int or not (
            c.BODY_FETCH_SEQUENTIAL <= self.max_in_flight <= c.BODY_FETCH_CONCURRENCY_MAX
        ):
            raise ValueError(
                f"본문 동시 요청 폭은 {c.BODY_FETCH_SEQUENTIAL} 이상 {c.BODY_FETCH_CONCURRENCY_MAX} 이하의 정수여야 합니다"
            )
        if type(self.max_per_host) is not int or not (
            1 <= self.max_per_host <= min(self.max_in_flight, c.BODY_FETCH_PER_HOST_MAX)
        ):
            raise ValueError(
                "같은 호스트 동시 요청 상한은 1 이상이고 동시 요청 폭과 "
                f"{c.BODY_FETCH_PER_HOST_MAX} 중 작은 값을 넘을 수 없습니다"
            )


@dataclass
class CallLease:
    """사슬 하나가 들고 있는 요청 예약. 원장(``CallBudgetPool``) 잠금 안에서만 바꾼다."""

    reserved: int = 0


class CallBudgetPool:
    """본문 요청 상한을 여러 사슬이 나눠 쓰되 절대 넘지 않게 하는 원장."""

    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._made = 0
        self._reserved = 0
        self._lock = threading.Lock()

    @property
    def made(self) -> int:
        """실제로 보낸 요청 수 — 진단의 「본문호출」."""

        with self._lock:
            return self._made

    @property
    def unreserved(self) -> int:
        """아직 아무 사슬도 예약하지 않은 남은 몫."""

        with self._lock:
            return self._limit - self._made - self._reserved

    def try_reserve(self, count: int, lease: CallLease) -> bool:
        """사슬을 띄우기 전에 그 기사가 쓸 최대 요청 수를 한 번에 예약한다."""

        with self._lock:
            if self._limit - self._made - self._reserved < count:
                return False
            self._reserved += count
            lease.reserved += count
            return True

    def available(self, lease: CallLease) -> bool:
        """요청을 보낼 몫이 남았는지 «보기만» 한다(순차 규칙의 상한 확인과 같은 자리)."""

        with self._lock:
            return lease.reserved > 0 or self._limit - self._made - self._reserved >= 1

    def consume(self, lease: CallLease) -> bool:
        """요청 하나를 보내기 직전에 예약분 또는 남은 몫에서 하나를 실제로 쓴다."""

        with self._lock:
            if lease.reserved > 0:
                lease.reserved -= 1
                self._reserved -= 1
                self._made += 1
                return True
            if self._limit - self._made - self._reserved >= 1:
                self._made += 1
                return True
            return False

    def release(self, lease: CallLease) -> None:
        """사슬이 끝났거나 시작도 못 했을 때 쓰지 않은 예약을 돌려준다."""

        with self._lock:
            self._reserved -= lease.reserved
            lease.reserved = 0


class HostSlots:
    """호스트별 동시 요청 상한. 같은 언론사·포털에는 정해진 수만 동시에 요청한다."""

    def __init__(self, per_host: int) -> None:
        self._per_host = per_host
        self._lock = threading.Lock()
        self._slots: dict[str, threading.Semaphore] = {}

    @contextmanager
    def slot(self, url: str) -> Iterator[None]:
        host = domain_host(url) or url
        with self._lock:
            semaphore = self._slots.setdefault(host, threading.Semaphore(self._per_host))
        semaphore.acquire()
        try:
            yield
        finally:
            semaphore.release()


@dataclass(frozen=True)
class ArticleFetchOutcome:
    """기사 한 건의 주소 사슬을 끝까지 돌린 결과. 호출자가 후보 순서대로 합친다."""

    candidate: NewsCandidate
    read_candidate: NewsCandidate | None = None
    full_body: str = ""
    stage: str = ""
    #: 사슬 안에서 센 제외 사유(주소 신뢰·메타 설명·HTML·발행일·짧은 본문).
    excluded: Counter[str] = field(default_factory=Counter)
    #: 시도 경고(전송 실패 코드, 원문 날짜 보정) — 순차 실행과 같은 삽입 순서.
    warnings: Counter[str] = field(default_factory=Counter)
    #: 실패 사유 코드를 만난 순서. 마지막 코드가 「실패」 진단에 남는다.
    article_failures: tuple[str, ...] = ()
    #: 요청 상한·수집 마감에 걸려 사슬을 멈췄다.
    budget_exhausted: bool = False
    #: 호출자가 중단을 알려 새 요청을 보내지 않고 멈췄다.
    stop_requested: bool = False
    #: 사슬이 살펴본 주소 수(신뢰 검사에서 걸러진 주소 포함). 0이면 상한·마감
    #: 검사가 첫 주소 이전에 걸린 것이라 순차 규칙의 «시도 전 검사»와 같다.
    urls_examined: int = 0
    #: 실제로 보낸 요청 수.
    calls_made: int = 0


@dataclass(frozen=True)
class ArticleFetchJob:
    """한 수집 실행 안의 모든 사슬이 공유하는 읽기 전용 문맥."""

    company: NewsCompanyContext
    policy: NewsCollectionPolicy
    as_of: dt.date
    fetch_text: Callable[[str], object]
    budget: CallBudgetPool
    deadline: float
    clock: Callable[[], float]
    stop_event: threading.Event
    host_slots: HostSlots | None = None


def _portal_fallback_allowed(url: str, candidate: NewsCandidate) -> bool:
    """원문에 확인된 발행자가 있어야 포털의 같은 기사 URL을 폴백으로 쓸 수 있다."""

    portal = any(host_matches(domain_host(url), domain) for domain in c.NEWS_PORTAL_DOMAINS)
    return portal and candidate.source_category == "news_report"


def _url_trusted(url: str, candidate: NewsCandidate, company: NewsCompanyContext,
                 policy: NewsCollectionPolicy) -> bool:
    return bool(source_category(url, company, policy)) or _portal_fallback_allowed(url, candidate)


def eligible_body_urls(candidate: NewsCandidate, company: NewsCompanyContext,
                       policy: NewsCollectionPolicy) -> tuple[str, ...]:
    """사슬이 실제로 요청할 수 있는 주소들 — 요청 예약 수를 정할 때 쓴다."""

    return tuple(url for url in body_fetch_urls(candidate) if _url_trusted(url, candidate, company, policy))


@contextmanager
def _maybe_slot(host_slots: HostSlots | None, url: str) -> Iterator[None]:
    if host_slots is None:
        yield
        return
    with host_slots.slot(url):
        yield


def fetch_article_body(job: ArticleFetchJob, candidate: NewsCandidate, lease: CallLease) -> ArticleFetchOutcome:
    """후보의 주소를 순서대로 읽어 첫 성공 본문을 돌려준다. 공유 상태는 원장만 건드린다.

    판정 순서와 제외·경고 코드는 예전 ``collection`` 안의 순차 사슬과 같다.
    앞 기사의 실제 주소와 겹치는지(``duplicate_effective_url``)는 호출자가
    소비 시점에 판정한다 — 그 답은 앞 기사가 끝나야 알 수 있다.
    """

    excluded: Counter[str] = Counter()
    warnings: Counter[str] = Counter()
    failures: list[str] = []
    urls_examined = 0
    calls_made = 0
    budget_exhausted = False
    stop_requested = False
    read_candidate: NewsCandidate | None = None
    full_body = ""
    stage = ""
    try:
        for url in body_fetch_urls(candidate):
            if job.stop_event.is_set():
                stop_requested = True
                break
            # 순차 규칙과 같은 자리에서 상한·마감을 «보기만» 한다(주소 신뢰 검사보다 먼저).
            if job.clock() >= job.deadline or not job.budget.available(lease):
                budget_exhausted = True
                break
            if not _url_trusted(url, candidate, job.company, job.policy):
                excluded["untrusted_body_url"] += 1
                urls_examined += 1
                continue
            with _maybe_slot(job.host_slots, url):
                # 호스트 자리를 기다리는 동안 상황이 바뀌었을 수 있다. 요청을 실제로
                # 보내기 직전에 중단·마감·상한을 다시 확인하고 몫을 하나 쓴다.
                if job.stop_event.is_set():
                    stop_requested = True
                    break
                if job.clock() >= job.deadline or not job.budget.consume(lease):
                    budget_exhausted = True
                    break
                # 여기부터가 «시도»다 — 신뢰 판정을 내렸거나 요청을 실제로 보낸 주소만 센다.
                urls_examined += 1
                calls_made += 1
                try:
                    result = normalize_body_result(job.fetch_text(url))
                except (GenerationCoordinationError, EngineBuildIdentityChangedError, CancelledError):
                    # 요청 전체 중단을 한 기사 실패로 바꾸면 다음 요청이 계속 나간다.
                    raise
                except Exception:
                    failures.append(c.EXCLUDED_FETCH_FAILED)
                    continue
            if not result.succeeded:
                reason = result.reason_code
                failures.append(
                    reason if reason in c.BODY_FAILURE_CODES or c.HTTP_STATUS_REASON_RE.fullmatch(reason)
                    else c.EXCLUDED_FETCH_FAILED
                )
                continue
            if result.stage == c.BODY_STAGE_META_DESCRIPTION:
                excluded["metadata_only_not_body"] += 1
                continue
            if c.UNPARSED_BODY_HTML_RE.search(result.text):
                excluded["unparsed_html_not_body"] += 1
                continue
            if decode_looks_broken(result.text):
                failures.append(c.EXCLUDED_FETCH_DECODE_ERROR)
                continue
            effective = canonical_url(result.effective_url or url)
            if not effective or not _url_trusted(effective, candidate, job.company, job.policy):
                excluded["untrusted_effective_url"] += 1
                continue
            published_on = result.published_on or candidate.published_on
            try:
                published_day = dt.date.fromisoformat(published_on)
            except ValueError:
                excluded["invalid_body_published_date"] += 1
                continue
            dated_candidate = replace(candidate, published_on=published_on)
            if published_day > job.as_of or not candidate_window(dated_candidate, job.as_of):
                excluded["body_published_outside_window"] += 1
                continue
            if result.published_on and result.published_on != candidate.published_on:
                warnings["search_body_date_corrected"] += 1
            if len(result.text) < c.GROUNDED_MIN_EXCERPT_CHARS:
                excluded["body_too_short"] += 1
                continue
            full_body = result.text
            stage = result.stage
            read_candidate = replace(
                dated_candidate, source_url=effective,
                published_on_source="article_metadata" if result.published_on else "search_index",
            )
            break
    finally:
        job.budget.release(lease)
    warnings.update(failures)
    return ArticleFetchOutcome(
        candidate=candidate, read_candidate=read_candidate, full_body=full_body, stage=stage,
        excluded=excluded, warnings=warnings, article_failures=tuple(failures),
        budget_exhausted=budget_exhausted, stop_requested=stop_requested,
        urls_examined=urls_examined, calls_made=calls_made,
    )


class BodyFetchLane:
    """한 수집 실행의 사슬 실행기. opt-in이 없으면 스레드 없이 호출 스레드에서 그대로 돈다."""

    def __init__(self, concurrency: BodyFetchConcurrency | None) -> None:
        self.max_in_flight = concurrency.max_in_flight if concurrency is not None else c.BODY_FETCH_SEQUENTIAL
        self.max_per_host = concurrency.max_per_host if concurrency is not None else c.BODY_FETCH_SEQUENTIAL
        self.stop_event = threading.Event()
        self._closed = False
        self._state_lock = threading.Lock()
        self._executor: ThreadPoolExecutor | None = None
        self.host_slots: HostSlots | None = None
        if self.max_in_flight > c.BODY_FETCH_SEQUENTIAL:
            self._executor = ThreadPoolExecutor(
                max_workers=self.max_in_flight, thread_name_prefix=c.BODY_FETCH_THREAD_NAME_PREFIX,
            )
            self.host_slots = HostSlots(self.max_per_host)

    @property
    def concurrent(self) -> bool:
        return self._executor is not None and not self._closed

    def submit(self, task: Callable[[], ArticleFetchOutcome]) -> Future[ArticleFetchOutcome]:
        """순차 모드에서는 지금 이 자리에서 실행해 결과를 담은 Future를 돌려준다."""

        with self._state_lock:
            if self._closed:
                raise RuntimeError("종료된 본문 수집 실행기에는 작업을 제출할 수 없습니다")
            if self._executor is not None:
                # 문맥 값의 가변 객체 격리는 opt-in한 호출자가 보장한다.
                return self._executor.submit(contextvars.copy_context().run, task)
        settled: Future[ArticleFetchOutcome] = Future()
        settled.set_result(task())
        return settled

    def request_stop(self) -> None:
        """이미 떠난 사슬이 «새» 요청을 더 보내지 않게 한다. 진행 중인 요청은 취소하지 못한다."""

        self.stop_event.set()

    def close(self) -> None:
        """아직 시작하지 않은 사슬은 취소하고, 진행 중인 사슬은 끝나길 기다린다."""

        with self._state_lock:
            self._closed = True
            self.request_stop()
            executor = self._executor
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)
