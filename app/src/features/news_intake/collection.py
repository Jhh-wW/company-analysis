"""고정된 검색 후보에서 최근 기사부터 본문을 읽고 검증하는 수집 서비스."""

from __future__ import annotations

import datetime as dt
import json
import re
import time
from collections import Counter
from dataclasses import replace
from typing import Any, Callable

from src.core.provider_gateway import gateway
from src.features.news_intake import constants as c
from src.features.news_intake.identity_names import derived_company_names
from src.features.news_intake.fetch import body_fetch_urls, decode_looks_broken, normalize_body_result
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
from src.features.news_intake.search_snapshot import (
    candidate_window, collect_search_snapshot, company_digest, diverse_candidates, domain_host, host_matches,
    policy_digest, snapshot_digest, source_category,
)
from src.shared.report_generation.models import exact_text_sha256
from src.shared.report_quality.source_identity import canonical_url


GroundedAnalyzer = Callable[[str, dict[str, Any], int], str | dict[str, Any]]


def collect_from_snapshot(snapshot: NewsSearchSnapshot, *, company: NewsCompanyContext,
                          as_of: dt.date, fetch_text: Callable[[str], object],
                          analyze_grounded: GroundedAnalyzer,
                          policy: NewsCollectionPolicy | None = None) -> NewsCollectionResult:
    """검색·본문 callback을 섞지 않고, 검증 실패를 예전 휴리스틱으로 보충하지 않는다."""

    policy = policy or NewsCollectionPolicy()
    if (snapshot.digest != snapshot_digest(snapshot) or snapshot.company_digest != company_digest(company)
            or snapshot.policy_digest != policy_digest(policy) or snapshot.as_of != as_of.isoformat()):
        raise ValueError("뉴스 스냅샷의 회사·기준일·정책·내용 결속이 일치하지 않습니다")
    excluded = Counter(snapshot.exclusion_counts)
    failures = [code for code in snapshot.reason_codes if code not in c.SEARCH_BUDGET_REASON_CODES]
    warnings: Counter[str] = Counter()
    budget_codes: list[str] = [code for code in snapshot.reason_codes if code in c.SEARCH_BUDGET_REASON_CODES]
    stages: Counter[str] = Counter()
    body_calls = 0
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
    # 빈 창만 기본 몫을 반납한다. 과거 후보가 하나라도 있으면 그 창의 몫을 남긴다.
    reusable_window_budget = sum(
        budget for months, budget in zip(c.WINDOW_MONTHS, c.WINDOW_ARTICLE_BUDGETS)
        if not candidates_by_window[months]
    )
    deadline = time.monotonic() + policy.max_collection_seconds
    stopped = False

    def analyze_batch(batch: list[tuple[NewsCandidate, str]]) -> None:
        nonlocal analysis_calls, prompt_chars, response_chars, stopped
        if not batch:
            return
        if analysis_calls >= policy.max_analysis_calls or time.monotonic() >= deadline:
            budget_codes.append("analysis_budget_exhausted")
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
        except gateway.ProviderCallFailed:
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
        excerpts, rejected = validate_grounded_response(response, articles=batch, company=company, as_of=as_of)
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

    for months, base_window_budget in zip(c.WINDOW_MONTHS, c.WINDOW_ARTICLE_BUDGETS):
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
        window_budget = min(base_window_budget + reusable_window_budget,
                            policy.max_body_articles - body_articles) if candidates else 0
        window_counts[str(months)] = {
            "후보": len(candidates) + len(carried), "본문": len(carried), "검증기사": 0, "이월": 0,
            "시도상한": window_budget, "시도": 0, "미시도": len(candidates),
        }
        batch: list[tuple[NewsCandidate, str]] = []
        examined = 0
        relevant_before = len(relevant_articles)
        for candidate in ranked_candidates:
            if stopped or evidence_is_sufficient(all_excerpts, policy):
                break
            if candidate.id in carried_bodies:
                queue_body(candidate, carried_bodies[candidate.id], batch)
                continue
            if examined >= window_budget:
                if len(candidates) > examined:
                    budget_codes.append("body_budget_exhausted" if body_articles >= policy.max_body_articles
                                        else "window_body_budget")
                # 새 본문 요청은 멈추되 이미 읽어 이월한 본문은 재요청 없이 검증한다.
                continue
            if (body_articles >= policy.max_body_articles or body_calls >= policy.max_body_calls
                    or body_chars >= policy.max_total_body_chars or time.monotonic() >= deadline):
                budget_codes.append("body_budget_exhausted")
                stopped = True
                break
            examined += 1
            body_articles += 1
            if candidate.source_url in seen_urls:
                excluded["duplicate_effective_url"] += 1
                continue
            seen_urls.add(candidate.source_url)
            article_failures: list[str] = []
            read_candidate: NewsCandidate | None = None
            full_body = ""
            for url in body_fetch_urls(candidate):
                if body_calls >= policy.max_body_calls or time.monotonic() >= deadline:
                    budget_codes.append("body_budget_exhausted")
                    stopped = True
                    break
                # 원문에 확인된 발행자가 있어야 포털의 같은 기사 URL을 폴백으로 쓸 수 있다.
                original_source = source_category(url, company, policy)
                portal = any(host_matches(domain_host(url), domain) for domain in c.NEWS_PORTAL_DOMAINS)
                if not original_source and not (portal and candidate.source_category == "news_report"):
                    excluded["untrusted_body_url"] += 1
                    continue
                body_calls += 1
                try:
                    result = normalize_body_result(fetch_text(url))
                except Exception:
                    article_failures.append("fetch_failed")
                    continue
                if not result.succeeded:
                    reason = result.reason_code
                    article_failures.append(reason if reason in c.BODY_FAILURE_CODES or re.fullmatch(r"fetch_http_[1-5][0-9]{2}", reason) else "fetch_failed")
                    continue
                if result.stage == c.BODY_STAGE_META_DESCRIPTION:
                    excluded["metadata_only_not_body"] += 1
                    continue
                if re.search(r"<(?:html|body|article|script)(?:\s|>)", result.text, re.I):
                    excluded["unparsed_html_not_body"] += 1
                    continue
                if decode_looks_broken(result.text):
                    article_failures.append(c.EXCLUDED_FETCH_DECODE_ERROR)
                    continue
                effective = canonical_url(result.effective_url or url)
                final_source = source_category(effective, company, policy)
                final_portal = any(host_matches(domain_host(effective), domain) for domain in c.NEWS_PORTAL_DOMAINS)
                if not effective or (not final_source and not (final_portal and candidate.source_category == "news_report")):
                    excluded["untrusted_effective_url"] += 1
                    continue
                published_on = result.published_on or candidate.published_on
                try:
                    published_day = dt.date.fromisoformat(published_on)
                except ValueError:
                    excluded["invalid_body_published_date"] += 1
                    continue
                dated_candidate = replace(candidate, published_on=published_on)
                if published_day > as_of or not candidate_window(dated_candidate, as_of):
                    excluded["body_published_outside_window"] += 1
                    continue
                if result.published_on and result.published_on != candidate.published_on:
                    warnings["search_body_date_corrected"] += 1
                if len(result.text) < c.GROUNDED_MIN_EXCERPT_CHARS:
                    excluded["body_too_short"] += 1
                    continue
                if effective != candidate.source_url and effective in seen_urls:
                    excluded["duplicate_effective_url"] += 1
                    break
                full_body = result.text
                read_candidate = replace(dated_candidate, source_url=effective,
                                         published_on_source="article_metadata" if result.published_on else "search_index")
                stages[result.stage] += 1
                seen_urls.add(effective)
                break
            warnings.update(article_failures)
            if read_candidate is None:
                if article_failures:
                    failures.append(article_failures[-1])
                excluded["article_body_unavailable"] += 1
                continue
            actual_months = candidate_window(read_candidate, as_of)
            if actual_months > months:
                # 검색 등록일이 새로워도 실제 기사는 과거 자료일 수 있다. 이미 읽은
                # 본문을 해당 기간까지 보류하고 검색·본문 요청은 반복하지 않는다.
                deferred.setdefault(actual_months, []).append((read_candidate, full_body))
                window_counts[str(months)]["이월"] += 1
                continue
            window_counts[str(months)]["본문"] += 1
            queue_body(read_candidate, full_body, batch)
            if stopped:
                break
        analyze_batch(batch)
        reusable_window_budget -= max(0, examined - base_window_budget)
        window_counts[str(months)]["시도"] = examined
        window_counts[str(months)]["미시도"] = len(candidates) - examined
        window_counts[str(months)]["검증기사"] = len(relevant_articles) - relevant_before

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
        "메타이름일치후보": sum(item.metadata_name_match for item in snapshot.candidates),
        "메타이름비일치후보": sum(not item.metadata_name_match for item in snapshot.candidates),
        "이름미확인후보": excluded.get("grounded_identity_unverified", 0),
        "기간개월": tuple(windows), "기간별": window_counts,
        "창": "확장" if any(month > c.WINDOW_MONTHS[0] for month in windows) else "기본",
        "실패": reason_codes[0] if reason_codes else None, "실패사유": reason_codes,
        "제외": {key: count for key, count in excluded.items() if count}, "본문단계": dict(stages),
        "시도경고": dict(warnings), "상한사유": budgets,
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
