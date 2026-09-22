"""고정된 검색 후보에서 최근 기사부터 본문을 읽고 검증하는 수집 서비스."""

from __future__ import annotations

import datetime as dt
import json
import time
from collections import Counter, deque
from concurrent.futures import CancelledError, Future
from dataclasses import dataclass
from functools import partial
from typing import Any, Callable

from src.core.provider_gateway import gateway
from src.features.news_intake import constants as c
from src.features.news_intake.body_prefetch import (
    ArticleFetchJob, ArticleFetchOutcome, BodyFetchConcurrency, BodyFetchLane,
    CallBudgetPool, CallLease, eligible_body_urls, fetch_article_body,
)
from src.features.news_intake.identity_names import derived_company_names
from src.features.news_intake.grounded import (
    build_grounded_prompt, build_grounded_schema, validate_grounded_response,
)
from src.features.news_intake.grounded_mapping import (
    evidence_is_sufficient, select_diverse_excerpts, to_evidence_fragment,
)
from src.features.news_intake.models import (
    GroundedNewsArticle, GroundedNewsExcerpt, NewsCandidate, NewsCollectionPolicy,
    NewsCollectionResult, NewsCompanyContext, NewsSearchSnapshot,
)
from src.features.news_intake.search_snapshot import (  # noqa: F401 - collect_search_snapshot은 어댑터·시험이 이 모듈에서 가져간다
    candidate_window, collect_search_snapshot, company_digest, diverse_candidates,
    policy_digest, snapshot_digest,
)
from src.shared.report_generation.models import exact_text_sha256
from src.shared.engine_build_identity import EngineBuildIdentityChangedError
from src.shared.generation_coordination import GenerationCoordinationError


GroundedAnalyzer = Callable[[str, dict[str, Any], int], str | dict[str, Any]]

@dataclass
class _PlannedCandidate:
    candidate: NewsCandidate
    kind: str
    budget_code: str | None = None
    future: Future[ArticleFetchOutcome] | None = None
    lease: CallLease | None = None


def collect_from_snapshot(snapshot: NewsSearchSnapshot, *, company: NewsCompanyContext,
                          as_of: dt.date, fetch_text: Callable[[str], object],
                          analyze_grounded: GroundedAnalyzer,
                          policy: NewsCollectionPolicy | None = None,
                          body_fetch: BodyFetchConcurrency | None = None) -> NewsCollectionResult:
    """검색·본문 callback을 섞지 않고, 검증 실패를 예전 휴리스틱으로 보충하지 않는다.

    ``body_fetch``는 호출자가 «``fetch_text``를 여러 스레드에서 동시에 불러도
    안전하다»고 선언하는 opt-in이다. 없으면 오늘과 같은 순차 수집이다. 있어도
    기사 선택·소비 순서·원문·예산 계약은 같고, 같은 기간·같은 분석 묶음 안의
    본문 요청 대기만 겹친다(``body_prefetch`` 모듈 설명 참조).
    """

    policy = policy or NewsCollectionPolicy()
    if (snapshot.digest != snapshot_digest(snapshot) or snapshot.company_digest != company_digest(company)
            or snapshot.policy_digest != policy_digest(policy) or snapshot.as_of != as_of.isoformat()):
        raise ValueError("뉴스 스냅샷의 회사·기준일·정책·내용 결속이 일치하지 않습니다")
    excluded = Counter(snapshot.exclusion_counts)
    failures = [code for code in snapshot.reason_codes if code not in c.SEARCH_BUDGET_REASON_CODES]
    warnings: Counter[str] = Counter()
    identity_diagnostics: Counter[str] = Counter()
    budget_codes: list[str] = [code for code in snapshot.reason_codes if code in c.SEARCH_BUDGET_REASON_CODES]
    stages: Counter[str] = Counter()
    body_articles = 0
    body_chars = 0
    analysis_calls = 0
    prompt_chars = 0
    response_chars = 0
    relevant_articles: set[str] = set()
    document_hashes: dict[str, str] = {}
    seen_body_hashes: set[str] = set()
    seen_urls: set[str] = set()
    all_excerpts: list[GroundedNewsExcerpt] = []
    windows: list[int] = []
    window_counts: dict[str, dict[str, int]] = {}
    deferred: dict[int, list[tuple[NewsCandidate, str]]] = {}
    candidates_by_window = {
        months: [item for item in snapshot.candidates if candidate_window(item, as_of) == months]
        for months in c.WINDOW_MONTHS
    }
    # 각 기간의 실제 후보에 필요한 기본 몫만 예약하고, 남는 몫은 최근부터 재사용한다.
    reserved_window_budgets = {
        months: min(budget, len(candidates_by_window[months]))
        for months, budget in zip(c.WINDOW_MONTHS, c.WINDOW_ARTICLE_BUDGETS)
    }
    reusable_window_budget = sum(c.WINDOW_ARTICLE_BUDGETS) - sum(reserved_window_budgets.values())
    deadline = time.monotonic() + policy.max_collection_seconds
    stopped = False
    # 본문 요청 상한은 원장이 지킨다. 순차든 동시든 «실제로 보낸 요청»만 「본문호출」로 센다.
    call_pool = CallBudgetPool(policy.max_body_calls)
    lane = BodyFetchLane(body_fetch)
    #: 선행 요청 정산 — 출발한 사슬, 결과를 쓰지 않은 사슬과 그 요청 수, 요청 전 취소.
    prefetch_stats: Counter[str] = Counter()

    def clock() -> float:
        # 모듈의 time을 호출 시점에 찾는다 — 시험이 마감 시계를 바꿔 끼울 수 있게 한다.
        return time.monotonic()

    job = ArticleFetchJob(
        company=company, policy=policy, as_of=as_of, fetch_text=fetch_text, budget=call_pool,
        deadline=deadline, clock=clock, stop_event=lane.stop_event, host_slots=lane.host_slots,
    )

    def analyze_batch(batch: list[tuple[NewsCandidate, str]]) -> None:
        nonlocal analysis_calls, prompt_chars, response_chars, stopped
        if not batch:
            return
        if analysis_calls >= policy.max_analysis_calls or time.monotonic() >= deadline:
            budget_codes.append(c.ANALYSIS_BUDGET_EXHAUSTED_CODE)
            stopped = True
            return
        prompt = build_grounded_prompt(company, batch, as_of)
        if len(prompt) > policy.max_prompt_chars:
            if len(batch) > 1:
                middle = len(batch) // 2
                analyze_batch(batch[:middle])
                analyze_batch(batch[middle:])
                return
            excluded["analysis_prompt_budget"] += len(batch)
            budget_codes.append("analysis_prompt_budget")
            return
        analysis_calls += 1
        prompt_chars += len(prompt)
        try:
            response = analyze_grounded(prompt, build_grounded_schema(batch), policy.analysis_max_tokens)
        except (gateway.ProviderCallFailed, GenerationCoordinationError,
                EngineBuildIdentityChangedError, CancelledError):
            # gateway가 이미 안전한 observation을 원장에 기록했다. provider
            # fatal을 콘텐츠 무효로 접으면 첫 원인이 사라지고 다음 배치의
            # ProviderBudgetUnavailable이 최종 원인처럼 보이므로 즉시 전파한다.
            stopped = True
            raise
        except Exception:
            failures.append("grounded_analysis_failed")
            excluded["grounded_analysis_failed"] += len(batch)
            return
        if isinstance(response, str):
            response_chars += len(response)
        elif isinstance(response, dict):
            try:
                size = len(json.dumps(response, ensure_ascii=False))
                response_chars += size
                if size > c.GROUNDED_RESPONSE_CHARS_BUDGET:
                    response = None
            except (TypeError, ValueError):
                response = None
        excerpts, rejected = validate_grounded_response(
            response, articles=batch, company=company, as_of=as_of, identity_diagnostics=identity_diagnostics,
        )
        excluded.update(rejected)
        if any(code in rejected for code in ("grounded_invalid_response", "grounded_invalid_item", "grounded_missing_result")):
            failures.append("grounded_response_incomplete")
        all_excerpts.extend(excerpts)
        relevant_articles.update(item.candidate.source_url for item in excerpts)

    def queue_body(candidate: NewsCandidate, full_body: str,
                   batch: list[tuple[NewsCandidate, str]]) -> None:
        nonlocal body_chars, stopped
        normalized_hash = exact_text_sha256("".join(full_body.split()).casefold())
        if normalized_hash in seen_body_hashes:
            excluded["duplicate_article_body"] += 1
            return
        seen_body_hashes.add(normalized_hash)
        available = min(policy.max_body_chars, policy.max_total_body_chars - body_chars)
        body = full_body[:available]
        if len(body) < c.GROUNDED_MIN_EXCERPT_CHARS:
            budget_codes.append("body_character_budget")
            stopped = True
            return
        if len(body) != len(full_body):
            excluded["body_input_truncated"] += 1
        body_chars += len(body)
        document_hashes[candidate.source_url] = exact_text_sha256(full_body)
        batch.append((candidate, body))
        if len(batch) >= policy.batch_size:
            analyze_batch(batch)
            batch.clear()

    def discard_outcome(outcome: ArticleFetchOutcome) -> None:
        """이미 보낸 요청의 결과를 쓰지 않고 버린다 — 요청은 있었으니 호출 수·시도 경고에는 남긴다."""
        prefetch_stats["선행미사용"] += 1
        prefetch_stats["선행미사용호출"] += outcome.calls_made
        warnings.update(outcome.warnings)

    try:
        for months, reserved_window_budget in reserved_window_budgets.items():
            if stopped or evidence_is_sufficient(all_excerpts, policy):
                break
            windows.append(months)
            candidates = candidates_by_window[months]
            carried = deferred.pop(months, [])
            carried_bodies = {candidate.id: body for candidate, body in carried}
            # 원문 날짜 보정으로 넘어온 기사도 같은 기간의 이름·주제 순위를 따른다.
            ranked_candidates = diverse_candidates(
                candidates + [candidate for candidate, _ in carried], len(candidates) + len(carried),
            )
            window_budget = min(reserved_window_budget + reusable_window_budget,
                                policy.max_body_articles - body_articles) if candidates else 0
            window_counts[str(months)] = {
                "후보": len(candidates) + len(carried), "본문": len(carried), "검증기사": 0, "이월": 0,
                "시도상한": window_budget, "시도": 0, "미시도": len(candidates), "선행미사용": 0,
            }
            batch: list[tuple[NewsCandidate, str]] = []
            examined = 0
            relevant_before = len(relevant_articles)
            unused_before = prefetch_stats["선행미사용"]
            # 후보를 순위 순서로 «계획»하고 같은 순서로만 «소비»한다. 사슬 완료 순서가
            # 뒤집혀도 분석 묶음·중복 판정·이월은 순차 실행과 같은 순서로 일어난다.
            planned: deque[_PlannedCandidate] = deque()
            plan_index = 0
            in_flight = 0
            pending_carried = 0
            planning_halted = False

            def plan_more() -> None:
                nonlocal plan_index, examined, body_articles, in_flight, pending_carried, planning_halted, stopped
                while plan_index < len(ranked_candidates) and not planning_halted:
                    if stopped or evidence_is_sufficient(all_excerpts, policy):
                        return
                    # 다음 분석 호출이 불가능하면 사용할 수 없는 본문도 요청하지 않는다.
                    # 마지막 묶음으로 모든 후보를 처리한 경우에는 이 분기에 들어오지 않는다.
                    if analysis_calls >= policy.max_analysis_calls:
                        budget_codes.append(c.ANALYSIS_BUDGET_EXHAUSTED_CODE)
                        stopped = True
                        return
                    candidate = ranked_candidates[plan_index]
                    # 분석 묶음의 빈자리가 동시 출발의 상한이다. 그래서 분석이 도는 순간에는
                    # 진행 중인 사슬이 없고, 분석 결과(조기 충분·호출 상한)를 앞질러 읽는 본문도 없다.
                    batch_slack = policy.batch_size - len(batch) - in_flight - pending_carried
                    if candidate.id in carried_bodies:
                        if batch_slack <= 0:
                            return
                        planned.append(_PlannedCandidate(candidate, c.PLANNED_CARRIED))
                        pending_carried += 1
                        plan_index += 1
                        continue
                    if examined >= window_budget:
                        budget_code = None
                        if len(candidates) > examined:
                            budget_code = (c.BODY_BUDGET_EXHAUSTED_CODE if body_articles >= policy.max_body_articles
                                           else "window_body_budget")
                        # 새 본문 요청은 멈추되 이미 읽어 이월한 본문은 재요청 없이 검증한다.
                        planned.append(_PlannedCandidate(candidate, c.PLANNED_SKIPPED, budget_code=budget_code))
                        plan_index += 1
                        continue
                    calls_left = call_pool.unreserved
                    if (body_articles >= policy.max_body_articles or body_chars >= policy.max_total_body_chars
                            or clock() >= deadline or (calls_left <= 0 and in_flight == 0)):
                        planned.append(_PlannedCandidate(candidate, c.PLANNED_STOP))
                        plan_index += 1
                        planning_halted = True
                        return
                    if calls_left <= 0 or in_flight >= lane.max_in_flight or batch_slack <= 0:
                        # 진행 중인 사슬이 예약·묶음 자리를 돌려줄 때까지 기다린다.
                        return
                    examined += 1
                    body_articles += 1
                    if candidate.source_url in seen_urls:
                        planned.append(_PlannedCandidate(candidate, c.PLANNED_DUPLICATE))
                        plan_index += 1
                        continue
                    lease = CallLease()
                    needed = len(eligible_body_urls(candidate, company, policy))
                    if needed and not call_pool.try_reserve(needed, lease):
                        if in_flight > 0:
                            # 지금은 이 기사의 몫을 보장할 수 없다. 앞 사슬이 남는 예약을 돌려준 뒤 다시 본다.
                            examined -= 1
                            body_articles -= 1
                            return
                        # 마지막 몫은 순차 규칙 그대로 — 주소마다 남은 호출을 하나씩 확인한다.
                    future = lane.submit(partial(fetch_article_body, job, candidate, lease))
                    planned.append(_PlannedCandidate(candidate, c.PLANNED_LAUNCHED, future=future, lease=lease))
                    prefetch_stats["출발"] += 1
                    in_flight += 1
                    plan_index += 1

            while True:
                plan_more()
                if not planned:
                    break
                item = planned.popleft()
                if stopped or evidence_is_sufficient(all_excerpts, policy):
                    planned.appendleft(item)
                    break
                if item.kind == c.PLANNED_CARRIED:
                    pending_carried -= 1
                    queue_body(item.candidate, carried_bodies[item.candidate.id], batch)
                    continue
                if item.kind == c.PLANNED_SKIPPED:
                    if item.budget_code:
                        budget_codes.append(item.budget_code)
                    continue
                if item.kind == c.PLANNED_STOP:
                    budget_codes.append(c.BODY_BUDGET_EXHAUSTED_CODE)
                    stopped = True
                    break
                if item.kind == c.PLANNED_DUPLICATE:
                    excluded["duplicate_effective_url"] += 1
                    continue
                in_flight -= 1
                outcome = item.future.result()
                candidate = item.candidate
                if body_chars >= policy.max_total_body_chars:
                    # 앞 사슬의 본문이 글자 예산을 채웠다. 순차 규칙이면 시도 전에 멈췄을 자리다.
                    discard_outcome(outcome)
                    budget_codes.append(c.BODY_BUDGET_EXHAUSTED_CODE)
                    stopped = True
                    break
                if outcome.urls_examined == 0 and (outcome.budget_exhausted or outcome.stop_requested):
                    # 첫 주소를 보기 전에 상한·마감에 걸렸다. 순차 규칙의 «시도 전 검사»와 같은
                    # 자리이므로 시도로 세지 않는다(요청도 없었다).
                    examined -= 1
                    body_articles -= 1
                    budget_codes.append(c.BODY_BUDGET_EXHAUSTED_CODE)
                    stopped = True
                    break
                if candidate.source_url in seen_urls:
                    # 사슬이 떠난 뒤 앞 기사의 실제 주소가 이 후보와 같다고 밝혀졌다.
                    excluded["duplicate_effective_url"] += 1
                    discard_outcome(outcome)
                    continue
                seen_urls.add(candidate.source_url)
                excluded.update(outcome.excluded)
                warnings.update(outcome.warnings)
                if outcome.budget_exhausted:
                    budget_codes.append(c.BODY_BUDGET_EXHAUSTED_CODE)
                    stopped = True
                read_candidate = outcome.read_candidate
                if (read_candidate is not None and read_candidate.source_url != candidate.source_url
                        and read_candidate.source_url in seen_urls):
                    excluded["duplicate_effective_url"] += 1
                    read_candidate = None
                if read_candidate is None:
                    if outcome.article_failures:
                        failures.append(outcome.article_failures[-1])
                    excluded["article_body_unavailable"] += 1
                    continue
                stages[outcome.stage] += 1
                seen_urls.add(read_candidate.source_url)
                actual_months = candidate_window(read_candidate, as_of)
                if actual_months > months:
                    # 검색 등록일이 새로워도 실제 기사는 과거 자료일 수 있다. 이미 읽은
                    # 본문을 해당 기간까지 보류하고 검색·본문 요청은 반복하지 않는다.
                    deferred.setdefault(actual_months, []).append((read_candidate, outcome.full_body))
                    window_counts[str(months)]["이월"] += 1
                    continue
                window_counts[str(months)]["본문"] += 1
                queue_body(read_candidate, outcome.full_body, batch)
                if stopped:
                    break

            if planned:
                # 멈춘 뒤에도 이미 떠난 사슬은 되돌릴 수 없다. 새 요청은 막고, 보낸 요청은
                # 성공·실패·취소를 가리지 않고 모두 정산한다.
                lane.request_stop()
                for item in planned:
                    if item.kind != c.PLANNED_LAUNCHED:
                        continue
                    in_flight -= 1
                    if item.future.cancel():
                        call_pool.release(item.lease)
                        examined -= 1
                        body_articles -= 1
                        prefetch_stats["취소"] += 1
                        continue
                    outcome = item.future.result()
                    if outcome.calls_made == 0:
                        # 요청을 하나도 보내지 않고 멈췄다 — 시도로 세지 않는다.
                        examined -= 1
                        body_articles -= 1
                        prefetch_stats["취소"] += 1
                        continue
                    discard_outcome(outcome)
                planned.clear()
                lane.stop_event.clear()
            analyze_batch(batch)
            reusable_window_budget -= max(0, examined - reserved_window_budget)
            window_counts[str(months)]["시도"] = examined
            window_counts[str(months)]["미시도"] = len(candidates) - examined
            window_counts[str(months)]["검증기사"] = len(relevant_articles) - relevant_before
            window_counts[str(months)]["선행미사용"] = prefetch_stats["선행미사용"] - unused_before
    finally:
        lane.request_stop()
        lane.close()
    body_calls = call_pool.made

    chosen, selection_exclusions = select_diverse_excerpts(all_excerpts, policy)
    excluded.update(selection_exclusions)
    fragments = tuple(to_evidence_fragment(item) for item in chosen)
    chosen_urls = tuple(dict.fromkeys(item.candidate.source_url for item in chosen))
    articles = tuple(GroundedNewsArticle(
        candidate=next(item.candidate for item in chosen if item.candidate.source_url == url),
        document_content_sha256=document_hashes[url],
        excerpts=tuple(item for item in chosen if item.candidate.source_url == url),
    ) for url in chosen_urls)
    enough = evidence_is_sufficient(list(chosen), policy)
    incomplete_codes = tuple(code for code in excluded if excluded[code] and (
        code.startswith("grounded_invalid_") or code in {
            "grounded_text_not_exact", "grounded_missing_result", "grounded_unknown_or_duplicate_id",
            "grounded_subject_missing", "grounded_identity_unverified", "grounded_plan_mismatch",
            "grounded_event_date_unverified", "grounded_attribution_required",
            "article_body_unavailable", "body_input_truncated", "invalid_body_published_date",
        }
    ))
    if snapshot.unverified_publishers and not enough:
        incomplete_codes += ("source_review_pending",)
    incomplete_codes += tuple(snapshot.transport_diagnostics["검색전송진단"])
    reason_codes = tuple(dict.fromkeys(failures))
    budgets = tuple(dict.fromkeys(budget_codes))
    completeness = "sufficient" if enough else "insufficient"
    if reason_codes:
        completeness = "partial" if fragments else "failed"
    elif budgets or incomplete_codes:
        completeness = "partial"
    diagnostics: dict[str, object] = {
        "검색": sum(attempt.returned_count for attempt in snapshot.query_attempts),
        **snapshot.transport_diagnostics, "선별": len(snapshot.candidates),
        "본문시도기사": body_articles, "본문호출": body_calls,
        "본문읽기": sum(stage_count for stage_count in stages.values()),
        "본문글자": body_chars, "분류AI호출": analysis_calls, "분석AI호출": analysis_calls,
        "분석호출상한": policy.max_analysis_calls, "분석잔여호출": policy.max_analysis_calls - analysis_calls,
        "분류프롬프트글자": prompt_chars, "분석입력글자": prompt_chars, "분석응답글자": response_chars,
        "관련성통과": len(relevant_articles), "조각": len(fragments),
        "조각글자": sum(len(item.text) for item in fragments), "독립기사": len(articles),
        "실질사건": len(chosen), "주제": dict(Counter(item.topic for item in chosen)),
        "발행처": dict(Counter(article.candidate.publisher for article in articles)),
        "발행일근거": dict(Counter(article.candidate.published_on_source for article in articles)),
        "미확인매체": dict(snapshot.unverified_publishers),
        "출처정책": {
            "방식": "확인도메인허용목록", "등록도메인수": len(policy.trusted_publisher_domains),
            "미확인도메인수": len(snapshot.unverified_publishers),
            "미확인검색반환행수": sum(snapshot.unverified_publishers.values()),
        },
        "검증된이름변형": derived_company_names(company),
        "법인검증상세": dict(identity_diagnostics),
        "메타이름일치후보": sum(item.metadata_name_match for item in snapshot.candidates),
        "메타이름비일치후보": sum(not item.metadata_name_match for item in snapshot.candidates),
        "이름미확인후보": excluded.get("grounded_identity_unverified", 0),
        "기간개월": tuple(windows), "기간별": window_counts,
        "창": "확장" if any(month > c.WINDOW_MONTHS[0] for month in windows) else "기본",
        "실패": reason_codes[0] if reason_codes else None, "실패사유": reason_codes,
        "제외": {key: count for key, count in excluded.items() if count}, "본문단계": dict(stages),
        "시도경고": dict(warnings), "상한사유": budgets,
        "본문동시수집": {
            "동시상한": lane.max_in_flight, "호스트동시상한": lane.max_per_host,
            "출발": prefetch_stats["출발"], "선행미사용": prefetch_stats["선행미사용"],
            "선행미사용호출": prefetch_stats["선행미사용호출"], "취소": prefetch_stats["취소"],
        },
        "상한잘림": excluded.get("fragment_budget", 0),
        "완전성": completeness, "자료부족": not enough and not reason_codes and not budgets and not incomplete_codes,
        "검증미완료": incomplete_codes,
        "검색상태": snapshot.status, "검색스냅샷": snapshot.digest,
        "캐시재사용가능": snapshot.cache_eligible and not reason_codes and not budgets and not incomplete_codes,
        "장별조각": dict(Counter(item.section_id for item in chosen)),
    }
    return NewsCollectionResult(
        fragments=fragments, document_hashes={url: document_hashes[url] for url in chosen_urls},
        diagnostics=diagnostics, snapshot_digest=snapshot.digest, articles=articles,
    )
