"""회사 신원에 묶은 검색 계획·출처 확인·재사용 가능한 후보 스냅샷."""

from __future__ import annotations

import calendar
import datetime as dt
import html
import inspect
import json
import re
import time
from collections import Counter, defaultdict, deque
from dataclasses import asdict, replace
from email.utils import parsedate_to_datetime
from typing import Any, Callable
from urllib.parse import urlsplit

from src.features.news_intake import constants as c
from src.features.news_intake.identity_names import company_query_names, mentions_target
from src.features.news_intake.models import (
    NewsCandidate, NewsCollectionPolicy, NewsCompanyContext, NewsQueryAttempt,
    NewsSearchSnapshot,
)
from src.shared.report_generation.models import exact_text_sha256
from src.shared.report_quality.source_identity import canonical_url


def stable_digest(value: object) -> str:
    return exact_text_sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def company_digest(company: NewsCompanyContext) -> str:
    return stable_digest(asdict(company))


def policy_digest(policy: NewsCollectionPolicy) -> str:
    return stable_digest({"version": c.COLLECTION_POLICY_VERSION, **asdict(policy)})


def month_boundary(as_of: dt.date, months: int) -> dt.date:
    """윤년과 월말을 보존하는 달력 기준 기간 경계."""

    month_index = as_of.year * 12 + as_of.month - 1 - months
    year, month_zero = divmod(month_index, 12)
    month = month_zero + 1
    return dt.date(year, month, min(as_of.day, calendar.monthrange(year, month)[1]))


def candidate_window(candidate: NewsCandidate, as_of: dt.date) -> int:
    published = dt.date.fromisoformat(candidate.published_on)
    return next((months for months in c.WINDOW_MONTHS if published >= month_boundary(as_of, months)), 0)


def domain_host(value: str) -> str:
    try:
        host = (urlsplit(value if "://" in value else "https://" + value).hostname or "").casefold().rstrip(".")
    except ValueError:
        return ""
    return host.removeprefix("www.")


def host_matches(host: str, domain: str) -> bool:
    expected = domain_host(domain)
    clean = domain_host(host)
    return bool(expected and (clean == expected or clean.endswith("." + expected)))


def source_category(url: str, company: NewsCompanyContext, policy: NewsCollectionPolicy) -> str:
    host = domain_host(url)
    if not host or any(host_matches(host, blocked) for blocked in c.BLOCKED_PUBLISHER_DOMAINS):
        return ""
    if company.domain and host_matches(host, company.domain):
        return "official_release"
    if any(host_matches(host, domain) for domain in policy.trusted_publisher_domains):
        return "news_report"
    return ""


def search_plan(company: NewsCompanyContext, as_of: dt.date) -> tuple[tuple[str, str, str, int], ...]:
    """최근 후보를 먼저 찾고, 본문 탈락에 대비한 과거 후보도 미리 고정한다."""

    names = company_query_names(company)
    if not names:
        raise ValueError("검색할 회사명이 없습니다")
    primary = names[0]
    recent_months, archive_months = c.WINDOW_MONTHS[0], c.WINDOW_MONTHS[-1]
    plan = [(primary, "date", "recent", recent_months), (primary, "sim", "business", recent_months)]
    for alias in names[1:1 + c.SEARCH_ALIAS_BUDGET]:
        plan.append((alias, "sim", "alias", recent_months))
    for topic, suffix in c.SEARCH_TOPICS:
        plan.append((f"{primary} {suffix}", "sim", topic, recent_months))
    if company.domain:
        plan.append((f"site:{domain_host(company.domain)} {primary} 발표", "sim", "official", recent_months))
    # NAVER 뉴스 API에는 날짜 범위 매개변수가 없다. 연도는 검색어로만 보내고,
    # 실제 기간은 모든 응답의 발행일로 다시 검사한다.
    for years in range(1, len(c.WINDOW_MONTHS) + 1):
        plan.append((f"{primary} {as_of.year - years}", "sim", "archive", archive_months))
    return tuple(dict.fromkeys(plan))


def _value(item: object, field: str) -> str:
    value = item.get(field, "") if isinstance(item, dict) else getattr(item, field, "")
    return value if isinstance(value, str) else ""


def _published_on(value: str) -> str:
    try:
        return dt.date.fromisoformat(value).isoformat()
    except ValueError:
        try:
            return parsedate_to_datetime(value).date().isoformat()
        except (ValueError, TypeError, OverflowError):
            return ""


def _metadata(item: object) -> dict[str, str]:
    return {
        "title": html.unescape(re.sub(r"</?b\s*>", "", _value(item, "title"), flags=re.I)).strip(),
        "description": html.unescape(re.sub(r"</?b\s*>", "", _value(item, "description"), flags=re.I)).strip(),
        "originallink": canonical_url(_value(item, "originallink")),
        "link": canonical_url(_value(item, "link")),
        "published_on": _published_on(_value(item, "pubDate")),
        # 날짜 정규화 실패도 동일한 빈 값으로 뭉개지 않고 지문에 결속한다.
        "raw_published_on": _value(item, "pubDate"),
    }


def _candidate(metadata: dict[str, str], *, company: NewsCompanyContext,
               policy: NewsCollectionPolicy, as_of: dt.date, topic: str,
               excluded: Counter[str], unverified_publishers: Counter[str]) -> NewsCandidate | None:
    url = metadata["originallink"] or metadata["link"]
    if not url or len(url) > c.SEARCH_URL_CHARS:
        excluded["invalid_url"] += 1
        return None
    source = source_category(url, company, policy)
    if not source:
        excluded["untrusted_publisher"] += 1
        host = domain_host(url)
        if any(host_matches(host, blocked) for blocked in c.BLOCKED_PUBLISHER_DOMAINS):
            excluded["blog_or_community"] += 1
        else:
            excluded["publisher_verification_required"] += 1
            unverified_publishers[host] += 1
        return None
    published = metadata["published_on"]
    if not published:
        excluded["invalid_date"] += 1
        return None
    day = dt.date.fromisoformat(published)
    if day > as_of or day < month_boundary(as_of, c.WINDOW_MONTHS[-1]):
        excluded["outside_window"] += 1
        return None
    title = metadata["title"][:c.SEARCH_TITLE_CHARS]
    description = metadata["description"][:c.SEARCH_DESCRIPTION_CHARS]
    if not title:
        excluded["missing_title"] += 1
        return None
    # 검색 요약에 없던 회사명이 본문에는 있을 수 있다. 메타 일치는 순위만
    # 정하고, 신뢰 출처·기간을 통과한 후보의 법인·실질내용은 본문에서 검증한다.
    metadata_name_match = mentions_target(title + " " + description, company)
    return NewsCandidate(
        id="news-" + exact_text_sha256(url)[:20], title=title, description=description,
        originallink=metadata["originallink"], link=metadata["link"], published_on=published,
        publisher=domain_host(url), priority=c.PRIORITY_NEWSROOM if source == "official_release" else c.PRIORITY_OTHER,
        source_url=url, topics=(topic,), source_category=source,
        metadata_name_match=metadata_name_match,
    )


def diverse_candidates(candidates: list[NewsCandidate], limit: int) -> tuple[NewsCandidate, ...]:
    """같은 기간 안에서 이름 관측을 우선하고 각 순위의 주제를 번갈아 읽는다."""

    ordered = sorted(candidates, key=lambda item: (item.published_on, item.source_url), reverse=True)
    selected: list[NewsCandidate] = []
    for name_match in (True, False):
        groups: dict[str, deque[NewsCandidate]] = defaultdict(deque)
        for candidate in ordered:
            if candidate.metadata_name_match is name_match:
                groups[candidate.topics[0] if candidate.topics else "business"].append(candidate)
        while groups and len(selected) < limit:
            for topic in tuple(groups):
                selected.append(groups[topic].popleft())
                if not groups[topic]:
                    del groups[topic]
                if len(selected) >= limit:
                    break
    return tuple(selected)


def snapshot_digest(snapshot: NewsSearchSnapshot) -> str:
    return stable_digest({
        "version": c.COLLECTION_POLICY_VERSION,
        "company": snapshot.company_digest, "policy": snapshot.policy_digest,
        "as_of": snapshot.as_of, "windows": snapshot.window_months,
        "attempts": [asdict(attempt) for attempt in snapshot.query_attempts],
        "candidates": [asdict(candidate) for candidate in snapshot.candidates],
        "status": snapshot.status, "reasons": snapshot.reason_codes,
        "cache_eligible": snapshot.cache_eligible, "excluded": dict(snapshot.exclusion_counts),
        "unverified_publishers": dict(snapshot.unverified_publishers),
    })


def _supports_transport_budget(search_news: Callable[..., Any]) -> bool:
    """전송 뒤 TypeError 재호출 없이, 호출 전에 지원 서명만 확인한다."""
    try:
        parameters = inspect.signature(search_news).parameters
    except (TypeError, ValueError):
        return False
    parameter = parameters.get("remaining_transport_budget")
    return bool(
        parameter is not None and parameter.kind in {
            inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY,
        }
        or any(parameter.kind == inspect.Parameter.VAR_KEYWORD for parameter in parameters.values())
    )


def _transport_observation(result: object, *, supported: bool, remaining: int) -> dict[str, object]:
    """알려진 전송만 세고, 관측 불가나 상한 미지원은 잔여 예산을 보수 차감한다."""
    diagnostics: list[str] = []
    if not supported:
        diagnostics.append(c.SEARCH_TRANSPORT_BUDGET_UNENFORCED)
    metadata_unreadable = False
    try:
        count = getattr(result, "transport_attempts", None)
        recovered = getattr(result, "retry_recovered", None)
        reasons = getattr(result, "attempt_reason_codes", None)
        zero_observed = getattr(result, "transport_observed", False) is True
        raw_state = getattr(result, "state", None)
        raw_reason = getattr(result, "reason_code", None)
    except Exception:  # 대역의 속성 접근 실패도 비밀값 없이 관측 불가로 남긴다.
        count, recovered, reasons, raw_state, raw_reason = None, None, None, None, None
        zero_observed, metadata_unreadable = False, True
    valid = (
        type(count) is int and count >= 0 and type(recovered) is bool
        and isinstance(reasons, (list, tuple)) and len(reasons) == count
        and all(isinstance(code, str) and code in c.SEARCH_TRANSPORT_ATTEMPT_REASON_CODES for code in reasons)
    )
    if valid:
        # 기존 네 인자 DTO의 기본 0을 성공한 검색의 실측 0회로 승격하지 않는다.
        valid = bool(
            (count > 0 or zero_observed and raw_state != "success"
             and isinstance(raw_reason, str) and raw_reason in c.SEARCH_ZERO_TRANSPORT_REASON_CODES)
            and recovered == (raw_state == "success" and "news_search_temporarily_unavailable" in reasons[:-1])
            and (raw_state != "success" or count > 0 and reasons[-1] == "news_search_ok")
        )
    if not valid:
        diagnostics.append(c.SEARCH_TRANSPORT_UNOBSERVED)
        if metadata_unreadable or any(value is not None for value in (count, recovered, reasons)):
            diagnostics.append(c.SEARCH_TRANSPORT_METADATA_INVALID)
        count, recovered, reasons = None, None, ()
    elif count > remaining:
        # 위반을 이미 보고한 callback의 수치를 상한으로 잘라 숨기지 않는다.
        diagnostics.append(c.SEARCH_TRANSPORT_BUDGET_EXCEEDED)
    return {
        "transport_attempts": count, "retry_recovered": recovered,
        "attempt_reason_codes": tuple(reasons), "transport_budget_supported": supported,
        "transport_budget_charged": min(count, remaining) if valid and supported else remaining,
        "transport_diagnostic_codes": tuple(diagnostics),
    }


def collect_search_snapshot(*, search_news: Callable[..., Any], company: NewsCompanyContext,
                            as_of: dt.date, policy: NewsCollectionPolicy | None = None) -> NewsSearchSnapshot:
    policy = policy or NewsCollectionPolicy()
    attempts: list[NewsQueryAttempt] = []
    excluded: Counter[str] = Counter()
    unverified_publishers: Counter[str] = Counter()
    reasons: list[str] = []
    by_url: dict[str, NewsCandidate] = {}
    deadline = time.monotonic() + policy.max_search_seconds
    plan = search_plan(company, as_of)
    budget_supported = _supports_transport_budget(search_news)
    remaining_transports = policy.max_search_transport_attempts
    for query, sort, topic, months in plan:
        if len(attempts) >= policy.max_search_calls or time.monotonic() >= deadline:
            reasons.append("search_budget_exhausted")
            break
        if remaining_transports <= 0:
            reasons.append(c.SEARCH_TRANSPORT_BUDGET_EXHAUSTED)
            break
        state, reason, items = "failed", "news_search_internal_error", []
        result = None
        try:
            options = {"display": policy.search_page_size, "start": 1, "sort": sort}
            if budget_supported:
                options["remaining_transport_budget"] = remaining_transports
            result = search_news(query, **options)
            state = str(getattr(result, "state", "") or "")
            reason = str(getattr(result, "reason_code", "") or "")
            if reason not in c.SEARCH_REASON_CODES:
                reason = "news_search_ok" if state == "success" else "news_search_invalid_response"
            if state not in {"success", "failed", "skipped"}:
                state, reason = "failed", "news_search_invalid_response"
            raw_items = getattr(result, "items", ())
            if not isinstance(raw_items, (list, tuple)):
                state, reason = "failed", "news_search_invalid_response"
            elif len(raw_items) > policy.search_page_size:
                state, reason = "failed", "news_search_response_limit"
                items = list(raw_items[:policy.search_page_size])
            else:
                items = list(raw_items)
        except Exception:  # 예외 원문에는 인증 설명이 있을 수 있어 출력하지 않는다.
            state, reason, items = "failed", "news_search_internal_error", []
        observation = _transport_observation(result, supported=budget_supported, remaining=remaining_transports)
        remaining_transports -= observation["transport_budget_charged"]
        metadata = [_metadata(item) for item in items]
        attempt = NewsQueryAttempt(
            query=query, sort=sort, start=1, display=policy.search_page_size, topic=topic,
            window_months=months, state=state, reason_code=reason,
            returned_count=len(items), item_fingerprints=tuple(stable_digest(item) for item in metadata),
            **observation,
        )
        attempts.append(attempt)
        if state != "success":
            reasons.append(reason)
            # 인증·미설정·제공자 장애는 쿼리를 바꿔 반복하지 않는다.
            break
        for item in metadata:
            candidate = _candidate(item, company=company, policy=policy, as_of=as_of, topic=topic,
                                   excluded=excluded, unverified_publishers=unverified_publishers)
            if candidate is None:
                continue
            previous = by_url.get(candidate.source_url)
            if previous is not None:
                excluded["duplicate_url"] += 1
                topics = tuple(dict.fromkeys((*previous.topics, topic)))
                # 동일 URL의 어느 응답에서든 관측한 이름을 읽기 순위에 보존한다.
                # 각 응답 원형은 query_attempts의 fingerprint에 따로 결속돼 있다.
                name_match = previous.metadata_name_match or candidate.metadata_name_match
                if previous.published_on < candidate.published_on:
                    # 동일 URL의 내용 변경은 모든 응답 fingerprint에 남긴다.
                    previous = candidate
                candidate = replace(previous, topics=topics, metadata_name_match=name_match)
            by_url[candidate.source_url] = candidate
        if attempt.transport_diagnostic_codes:
            # 이미 찾은 후보는 본문 단계로 넘기되, 내부 전송을 모르는 검색을 반복하지 않는다.
            break
    # 과거 예비후보가 최신 메타데이터 양에 밀려 통째로 사라지지 않게 기간별로 배분한다.
    buckets = {months: [item for item in by_url.values() if candidate_window(item, as_of) == months]
               for months in c.WINDOW_MONTHS}
    ordered = [list(diverse_candidates(buckets[months], policy.max_candidates)) for months in c.WINDOW_MONTHS]
    candidates: list[NewsCandidate] = []
    while any(ordered) and len(candidates) < policy.max_candidates:
        for bucket in ordered:
            if bucket and len(candidates) < policy.max_candidates:
                candidates.append(bucket.pop(0))
    excluded["candidate_budget"] += len(by_url) - len(candidates)
    reason_codes = tuple(dict.fromkeys(reasons))
    status = ("partial" if candidates else "failed") if reason_codes else ("success" if candidates else "empty")
    if "news_search_not_configured" in reason_codes:
        status = "not_configured"
    elif not reason_codes and not candidates and unverified_publishers:
        status = "source_review_pending"
    transport_incomplete = any(attempt.transport_diagnostic_codes for attempt in attempts)
    if transport_incomplete and status in {"success", "empty"}:
        status = "partial" if candidates else c.SEARCH_TRANSPORT_OBSERVATION_PENDING
    snapshot = NewsSearchSnapshot(
        candidates=tuple(candidates), query_attempts=tuple(attempts), company_digest=company_digest(company),
        policy_digest=policy_digest(policy), as_of=as_of.isoformat(), digest="", status=status,
        reason_codes=reason_codes, cache_eligible=not reason_codes and status != "source_review_pending" and not transport_incomplete,
        exclusion_counts={key: count for key, count in excluded.items() if count},
        unverified_publishers=dict(unverified_publishers),
    )
    return replace(snapshot, digest=snapshot_digest(snapshot))
