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
from src.features.news_intake import industry_constants as ic
from src.features.news_intake.identity_names import company_query_names, mentions_target
from src.features.news_intake.observation import NewsObserver, observe_news
from src.features.news_intake.models import (
    NewsCandidate, NewsCollectionPolicy, NewsCompanyContext, NewsQueryAttempt,
    NewsSearchSnapshot,
)
from src.shared.report_generation.models import exact_text_sha256
from src.shared.report_quality.source_identity import canonical_url


def stable_digest(value: object) -> str:
    return exact_text_sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def company_digest(company: NewsCompanyContext) -> str:
    value = asdict(company)
    if not company.business_anchors:
        value.pop("business_anchors", None)
    return stable_digest(value)


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


def industry_search_expression(business_item: str) -> str:
    """검색용 공백 경계만 파생한다. 원 앵커와 사실 검수의 사업명은 그대로 둔다."""
    match = ic.INDUSTRY_SEARCH_ACTIVITY_RE.fullmatch(business_item)
    if match is None:
        return business_item
    return match["object"] + " " + match["activity"]


def industry_query_derivation(company: NewsCompanyContext, query: str, topic: str) -> dict[str, str] | None:
    """실제로 전달한 질의와 공식 원 앵커의 관계를 관측에만 기록한다."""
    if not topic.startswith(ic.INDUSTRY_TOPIC_PREFIX) or ":" not in topic:
        return None
    anchor = next((item for item in company.business_anchors
                   if item.anchor_id == topic.split(":", 1)[1]), None)
    if anchor is None:
        return None
    expression = industry_search_expression(anchor.business_item)
    return {
        "anchor_id": anchor.anchor_id, "original_business_item": anchor.business_item,
        "search_expression": expression, "query": query,
        "transformation": "activity_suffix_spacing" if expression != anchor.business_item else "unchanged",
        "anchor_location": anchor.location, "anchor_text_sha256": anchor.text_sha256,
    }


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
    plan = list(dict.fromkeys(plan))
    if company.business_anchors:
        original_length = len(plan)
        industry_queries = []
        query_count = min(ic.INDUSTRY_QUERY_COUNT, original_length - ic.INDUSTRY_COMPANY_QUERY_COUNT)
        for index in range(query_count):
            region_count = len(ic.INDUSTRY_QUERY_REGIONS)
            group = index // region_count
            anchor = company.business_anchors[group % len(company.business_anchors)]
            region_index = index % region_count
            _, topic = ic.INDUSTRY_QUERY_REGIONS[region_index]
            region, theme = ic.INDUSTRY_QUERY_EXPRESSIONS[index % len(ic.INDUSTRY_QUERY_EXPRESSIONS)]
            expression = industry_search_expression(anchor.business_item)
            # 첫 질의의 국내 topic은 탐색 배분이다. 지역 없는 검색어로 지리를 승인하지 않는다.
            query = " ".join(part for part in (expression, region, theme) if part)
            industry_queries.append((query, "sim",
                                     f"industry_{topic}:{anchor.anchor_id}", recent_months))
        # 회사 검색 두 개를 유지하고 기존 뒤쪽 탐색을 대체한다. 총 호출은 늘리지 않는다.
        industry_queries = list(dict.fromkeys(industry_queries))
        plan = plan[:ic.INDUSTRY_COMPANY_QUERY_COUNT] + industry_queries + plan[ic.INDUSTRY_COMPANY_QUERY_COUNT:]
        plan = plan[:original_length]
    return tuple(plan)


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


def _industry_business_linked(item: NewsCandidate, company: NewsCompanyContext | None,
                              metadata: str | None = None) -> bool:
    if metadata is None:
        metadata = item.title + " " + item.description
    anchors = {anchor.anchor_id: anchor.business_item for anchor in company.business_anchors} if company else {}
    linked = [anchors.get(topic.split(":", 1)[1], "") for topic in item.topics
              if topic.startswith(ic.INDUSTRY_TOPIC_PREFIX) and ":" in topic]
    return any(business_item and re.search(
        r"(?<![가-힣A-Za-z0-9])" + re.escape(business_item)
        + ic.INDUSTRY_BUSINESS_TOKEN_END, metadata, re.I) for business_item in linked)


def _industry_problem_linked(item: NewsCandidate, company: NewsCompanyContext | None) -> bool:
    return any(_industry_business_linked(item, company, clause) and ic.INDUSTRY_SEARCH_PROBLEM_RE.search(clause)
               for clause in ic.INDUSTRY_SEARCH_CLAUSE_RE.split(item.title + "\n" + item.description))


def _industry_exploration_linked(item: NewsCandidate, company: NewsCompanyContext | None,
                                 metadata: str | None = None) -> bool:
    """공백·사업 접미어의 탐색 신호만 비교하며 본문 사업 관계는 승인하지 않는다."""
    if company is None:
        return False
    metadata = item.title + "\n" + item.description if metadata is None else metadata
    anchors = {anchor.anchor_id: anchor.business_item for anchor in company.business_anchors}
    for topic in item.topics:
        business = anchors.get(topic.split(":", 1)[1], "") if ":" in topic and topic.startswith(ic.INDUSTRY_TOPIC_PREFIX) else ""
        if not business:
            continue
        expressions = [business]
        suffix = ic.INDUSTRY_SEARCH_BUSINESS_SUFFIX_RE.fullmatch(business)
        if suffix is not None:
            expressions.append(suffix[1].strip())
        for expression in expressions:
            characters = "".join(expression.split())
            if len(characters) < ic.INDUSTRY_SEARCH_MIN_CORE_CHARS:
                continue
            spaced = r"\s*".join(re.escape(character) for character in characters)
            ending = ic.INDUSTRY_BUSINESS_TOKEN_END
            if expression != business:
                ending = f"(?:{ic.INDUSTRY_SEARCH_REVENUE_SUFFIX})?" + ending
            if re.search(ic.INDUSTRY_SEARCH_TOKEN_START + spaced + ending, metadata, re.I):
                return True
    return False


def _industry_exploration_problem(item: NewsCandidate, company: NewsCompanyContext | None) -> bool:
    return any(_industry_exploration_linked(item, company, clause)
               and (ic.INDUSTRY_SEARCH_PROBLEM_RE.search(clause)
                    or ic.INDUSTRY_SEARCH_QUESTION_RE.search(clause))
               for clause in ic.INDUSTRY_SEARCH_CLAUSE_RE.split(item.title + "\n" + item.description))


def _industry_self_roles(exact_text: str) -> tuple[str, ...]:
    """자기 앵커의 닫힌 업종 정의에서 명사 원문만 가져온다. 수단·대상은 제외한다."""
    roles = []
    for pattern in (ic.INDUSTRY_SEARCH_SELF_ROLE_RE, ic.INDUSTRY_SEARCH_PROFESSIONAL_ROLE_RE):
        for match in pattern.finditer(exact_text):
            role = match["role"]
            if (any(role.casefold().endswith(generic) for generic in ic.INDUSTRY_SEARCH_GENERIC_ROLES)
                    or ic.INDUSTRY_SEARCH_ROLE_FOREIGN_RE.search(match["prefix"])
                    or ic.INDUSTRY_SEARCH_ROLE_RECIPIENT_RE.match(exact_text[match.end():])):
                continue
            if pattern is ic.INDUSTRY_SEARCH_PROFESSIONAL_ROLE_RE:
                tail = ic.INDUSTRY_SEARCH_CLAUSE_RE.split(exact_text[match.end():], maxsplit=1)[0]
                if (not ic.INDUSTRY_SEARCH_ROLE_CURRENT_END_RE.search(tail)
                        or ic.INDUSTRY_SEARCH_ROLE_TAIL_NEGATIVE_RE.search(tail)):
                    continue
            roles.append(role)
    return tuple(dict.fromkeys(roles))


def _industry_role_problem(item: NewsCandidate, company: NewsCompanyContext | None) -> bool:
    """같은 사업·문제 절에 자기 업종 원문이 있는 메타만 추가 우선한다."""
    if company is None:
        return False
    anchors = {anchor.anchor_id: anchor for anchor in company.business_anchors}
    for topic in item.topics:
        anchor = anchors.get(topic.split(":", 1)[1]) if ":" in topic and topic.startswith(ic.INDUSTRY_TOPIC_PREFIX) else None
        if anchor is None:
            continue
        specific = replace(item, topics=(topic,))
        roles = _industry_self_roles(anchor.exact_text)
        for clause in ic.INDUSTRY_SEARCH_CLAUSE_RE.split(item.title + "\n" + item.description):
            if not (_industry_exploration_linked(specific, company, clause)
                    and (ic.INDUSTRY_SEARCH_PROBLEM_RE.search(clause)
                         or ic.INDUSTRY_SEARCH_QUESTION_RE.search(clause))):
                continue
            if any(re.search(ic.INDUSTRY_SEARCH_TOKEN_START + re.escape(role)
                             + ic.INDUSTRY_BUSINESS_TOKEN_END, clause, re.I) for role in roles):
                return True
    return False


def _industry_rank(item: NewsCandidate, company: NewsCompanyContext | None) -> tuple:
    return (
        not _industry_information_only(item, company),
        _industry_business_linked(item, company),
        _industry_problem_linked(item, company) and _industry_business_linked(item, company, item.title),
        _industry_problem_linked(item, company),
        _industry_business_linked(item, company, item.title),
        _industry_role_problem(item, company),
        _industry_exploration_problem(item, company),
        _industry_exploration_linked(item, company),
        _industry_problem_signal(item, title_only=True),
        _industry_problem_signal(item),
        item.published_on, item.source_url,
    )


def _industry_problem_signal(item: NewsCandidate, *, title_only: bool = False) -> bool:
    """사업명 exact 신호가 없어도 문제 논점이 있는 본문의 조사 기회를 먼저 준다."""
    metadata = item.title if title_only else item.title + "\n" + item.description
    return bool(ic.INDUSTRY_SEARCH_PROBLEM_RE.search(metadata)
                or ic.INDUSTRY_SEARCH_QUESTION_RE.search(metadata))


def _industry_information_only(item: NewsCandidate, company: NewsCompanyContext | None) -> bool:
    """명시 인물 프로필·관련주 목록의 요약 키워드가 사건 제목을 앞서지 않게 한다."""
    return bool(ic.INDUSTRY_SEARCH_INFORMATION_TITLE_RE.search(item.title)
                and not _industry_problem_signal(item, title_only=True)
                and not _industry_problem_linked(item, company))


def _industry_candidates(candidates: list[NewsCandidate], *,
                         company: NewsCompanyContext | None = None) -> list[NewsCandidate]:
    """문제 신호가 있는 산업 검색 후보에 본문 조사 기회를 먼저 준다.

    제목·요약 신호가 없어도 후보는 보존한다. 회사 관련성·산업 적용 관계·지역은
    기존 본문 검증에서 판정하며 검색 주제나 이 순위로 승인하지 않는다.
    """
    industry = [item for item in candidates if any(
        topic.startswith(ic.INDUSTRY_TOPIC_PREFIX) for topic in item.topics)]
    # 검색 요약에 공식 사업명의 통단어가 없어도 제한된 본문 탐색은 가능하다.
    # 실제 공식 앵커에 연결된 질의만 탐색하며, 사업·문제·지역 승인은 본문 검수에 남긴다.
    if company is not None:
        anchor_ids = {anchor.anchor_id for anchor in company.business_anchors}
        industry = [item for item in industry if any(
            topic == f"{ic.INDUSTRY_TOPIC_PREFIX}{region}:{anchor_id}"
            for topic in item.topics for _, region in ic.INDUSTRY_QUERY_REGIONS
            for anchor_id in anchor_ids)]

    return sorted(industry, key=lambda item: _industry_rank(item, company), reverse=True)


def _reserved_industry_candidates(industry: list[NewsCandidate], reserved: int, *,
                                  company: NewsCompanyContext | None) -> list[NewsCandidate]:
    """같은 예약 몫을 질의별로 나누고 빈 군의 몫은 남은 고유 기사에 돌린다.

    query 주제는 탐색 기회에만 사용한다. 실제 지역과 현재 문제는 본문 검수가
    판정하며, 타산업 후보나 부족한 지역의 근거를 보충하지 않는다.
    """
    if reserved <= 0:
        return []
    if company is not None:
        topics = [f"{ic.INDUSTRY_TOPIC_PREFIX}{region}:{anchor.anchor_id}"
                  for anchor in company.business_anchors
                  for _, region in ic.INDUSTRY_QUERY_REGIONS][:ic.INDUSTRY_QUERY_COUNT]
    else:
        topics = list(dict.fromkeys(topic for item in industry for topic in item.topics
                                    if topic.startswith(ic.INDUSTRY_TOPIC_PREFIX)))[:ic.INDUSTRY_QUERY_COUNT]
    groups = {topic: sorted((item for item in industry if topic in item.topics),
                            key=lambda item: _industry_rank(replace(item, topics=(topic,)), company),
                            reverse=True) for topic in topics}
    chosen: list[NewsCandidate] = []
    selected_ids: set[str] = set()
    assigned_regions: set[str] = set()
    while groups and len(chosen) < reserved:
        available = {topic: next((item for item in items if item.id not in selected_ids), None)
                     for topic, items in groups.items()}
        available = {topic: item for topic, item in available.items() if item is not None}
        if not available:
            break
        # 작은 예산에서도 두 지역을 먼저 탐색한다. 한 기사가 다른 질의의 새 몫을 대신하지 않는다.
        uncovered = {topic: item for topic, item in available.items()
                     if topic.split(":", 1)[0] not in assigned_regions}
        options = uncovered or available
        topic = max(options, key=lambda value: _industry_rank(
            replace(options[value], topics=(value,)), company))
        item = options[topic]
        chosen.append(item)
        selected_ids.add(item.id)
        assigned_regions.add(topic.split(":", 1)[0])
        del groups[topic]
    for item in industry:
        if len(chosen) >= reserved:
            break
        if item.id not in selected_ids:
            chosen.append(item)
            selected_ids.add(item.id)
    return chosen


def diverse_candidates(candidates: list[NewsCandidate], limit: int, *,
                       company: NewsCompanyContext | None = None) -> tuple[NewsCandidate, ...]:
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
    industry = _industry_candidates(ordered, company=company)
    reserved = min(len(industry), limit // ic.INDUSTRY_BODY_DIVISOR)
    if reserved:
        first = _reserved_industry_candidates(industry, reserved, company=company)
        selected = first + [item for item in selected if item.id not in {candidate.id for candidate in first}]
    return tuple(selected[:limit])


def metadata_probe_budget(*, total_budget: int, attempted: int,
                          window_budget: int, probes_attempted: int) -> int:
    """분할된 기간 몫에서도 전체 기본 시도 상한 안의 탐색몫을 잃지 않는다."""
    if total_budget <= 0 or window_budget <= 0:
        return 0
    total_probes = min(c.METADATA_MISMATCH_MAX_BODY_PROBES,
                       total_budget // c.METADATA_MISMATCH_BODY_PROBE_DIVISOR)
    target = min(total_budget, attempted + window_budget) * total_probes // total_budget
    local_ceiling = (window_budget * total_probes + total_budget - 1) // total_budget
    return min(max(0, target - probes_attempted), local_ceiling, window_budget)


def body_ranked_candidates(candidates: list[NewsCandidate], *, attempt_budget: int,
                           probe_budget: int,
                           replacement: bool = False,
                           company: NewsCompanyContext | None = None) -> tuple[NewsCandidate, ...]:
    """기본 본문 탐색에서 메타 이름 비일치 후보를 제한적으로 분산한다.

    검색 예비집합과 접근 거절 보충 순위는 바꾸지 않는다. 비일치 후보도 기존
    신뢰 출처·기간·본문 법인 검증을 그대로 거쳐야만 조각이 된다.
    """
    ranked = diverse_candidates(candidates, len(candidates), company=company)
    budget = min(max(attempt_budget, 0), len(ranked))
    if replacement or budget == 0:
        return ranked
    matched = deque(item for item in ranked if item.metadata_name_match)
    unmatched = deque(item for item in ranked if not item.metadata_name_match)
    probe_count = min(max(0, probe_budget), budget, len(unmatched))
    if probe_count == 0 or not matched:
        return ranked
    probe_positions = {
        ((2 * index + 1) * budget) // (2 * probe_count)
        for index in range(probe_count)
    }
    selected: list[NewsCandidate] = []
    for position in range(budget):
        preferred, fallback = (unmatched, matched) if position in probe_positions else (matched, unmatched)
        selected.append((preferred if preferred else fallback).popleft())
    selected.extend(matched)
    selected.extend(unmatched)
    industry = _industry_candidates(list(ranked), company=company)
    reserved = min(len(industry), budget // ic.INDUSTRY_BODY_DIVISOR)
    if reserved:
        first = _reserved_industry_candidates(industry, reserved, company=company)
        selected = first + [item for item in selected if item.id not in {candidate.id for candidate in first}]
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
                            as_of: dt.date, policy: NewsCollectionPolicy | None = None,
                            observer: NewsObserver | None = None) -> NewsSearchSnapshot:
    policy = policy or NewsCollectionPolicy()
    attempts: list[NewsQueryAttempt] = []
    excluded: Counter[str] = Counter()
    unverified_publishers: Counter[str] = Counter()
    reasons: list[str] = []
    by_url: dict[str, NewsCandidate] = {}
    observed_queries: list[dict[str, object]] = []
    observed_query_failures = 0
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
        if observer is not None:
            try:
                observed_queries.append({
                    "attempt": asdict(attempt),
                    "query_derivation": industry_query_derivation(company, query, topic),
                    # 검색 제공자가 해석한 허용 필드만 보관한다. HTTP 원응답이 아니다.
                    "parsed_rows": [{field: _value(item, field) for field in
                                     ("title", "description", "originallink", "link", "pubDate")}
                                    for item in items],
                })
            except Exception:
                observed_query_failures += 1
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
    ordered = [list(diverse_candidates(buckets[months], policy.max_candidates, company=company)) for months in c.WINDOW_MONTHS]
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
    snapshot = replace(snapshot, digest=snapshot_digest(snapshot))
    if observer is not None:
        observe_news(observer, "search_snapshot", lambda: {
            "queries": observed_queries, "query_capture_failures": observed_query_failures,
            "snapshot": {
                "digest": snapshot.digest, "company_digest": snapshot.company_digest,
                "policy_digest": snapshot.policy_digest, "as_of": snapshot.as_of,
                "window_months": snapshot.window_months,
                "status": snapshot.status, "reason_codes": snapshot.reason_codes,
                "cache_eligible": snapshot.cache_eligible,
                "candidates": [asdict(item) for item in snapshot.candidates],
                "query_attempts": [asdict(item) for item in snapshot.query_attempts],
                "exclusion_counts": dict(snapshot.exclusion_counts),
                "unverified_publishers": dict(snapshot.unverified_publishers),
            },
            "business_anchors": [asdict(anchor) for anchor in company.business_anchors],
        })
    return snapshot
