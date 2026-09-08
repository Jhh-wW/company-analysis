"""뉴스 조사에 필요한 공식 회사 문맥과 캐시 입력 결속."""

from __future__ import annotations

import hashlib
import json
from itertools import zip_longest
from typing import Any, Mapping
from urllib.parse import urlsplit

from src.features.pipeline.constants import (
    NEWS_IDENTITY_CONTEXT_CHARS,
    NEWS_IDENTITY_CONTEXT_SECTIONS,
    NEWS_IDENTITY_FRAGMENT_CHARS,
    NEWS_RESEARCH_CACHE_CONTRACT,
    NEWS_RESEARCH_BUDGET_REASON_CODES,
)
from src.shared.report_evidence.constants import OFFICIAL_WEB_SOURCE_KINDS


def public_news_research_status(
    steps: list[dict[str, Any]], *, enabled: bool
) -> dict[str, object]:
    """자료 희소와 접근·분석 장애를 공개용 닫힌 상태로 구분한다."""

    if not enabled:
        return {"상태": "disabled", "독립기사": 0}
    diagnostic = next(
        (step for step in reversed(steps) if step.get("step") == "5b_뉴스_수집"),
        None,
    )
    if diagnostic is None:
        return {"상태": "failed", "독립기사": 0, "실패": "news_collection_missing"}
    articles = diagnostic.get("독립기사", 0)
    articles = articles if type(articles) is int and articles >= 0 else 0
    failure = diagnostic.get("실패")
    reasons = diagnostic.get("실패사유")
    reasons = list(reasons) if isinstance(reasons, (list, tuple)) else []
    if failure and failure not in reasons:
        reasons.insert(0, failure)
    # 요청량 한도에 도달한 것과 실제 조회·접속·분석 실패는 다른 관측이다.
    # 둘이 같이 있으면 한도만 표시해 실제 장애를 숨기지도 않는다.
    errors = [reason for reason in reasons if reason not in NEWS_RESEARCH_BUDGET_REASON_CODES]
    limited = any(reason in NEWS_RESEARCH_BUDGET_REASON_CODES for reason in reasons)
    failure = errors[0] if errors else None
    if failure == "news_search_not_configured":
        status = "unconfigured"
    elif failure:
        status = "partial" if articles else "failed"
    elif limited or diagnostic.get("검증미완료") or diagnostic.get("상한사유"):
        status = "partial"
    elif diagnostic.get("완전성") in {"partial", "failed"}:
        status = str(diagnostic["완전성"])
    elif diagnostic.get("자료부족") or diagnostic.get("완전성") == "insufficient":
        status = "insufficient"
    else:
        status = "ok" if articles else "insufficient"
    return {"상태": status, "독립기사": articles, "실패": bool(failure)}


def official_news_context(
    profile: Mapping[str, Any], official_evidence: Any
) -> tuple[str, str]:
    """공식 수집에서 결속된 문서만 회사 문맥과 공식 도메인으로 사용한다.

    길이가 긴 한 공시 절이 문맥을 독점하지 않게 장별로 번갈아 읽는다.
    기업개황의 홈페이지 주소만으로 그 사이트의 현재 소유자를 확정하지 않는다.
    """

    # 회사명·대표·별칭은 NewsCompanyContext의 독립 필드로 이미 전달한다.
    # 업종 코드와 필드 이름은 사업 문맥이 아니다. 원문이 없는데 이것만 넣으면
    # 관련성 검사가 정상 기사에도 임원명·코드의 어휘 일치를 요구하게 된다.
    context: list[str] = []
    homepage = str(profile.get("hm_url") or "").strip()
    try:
        preferred_host = urlsplit(
            homepage if "://" in homepage else f"https://{homepage}"
        ).hostname
    except ValueError:
        preferred_host = None
    verified_domains: set[str] = set()
    groups: dict[str, list[str]] = {key: [] for key in NEWS_IDENTITY_CONTEXT_SECTIONS}
    seen: set[str] = set()
    for candidate in getattr(official_evidence, "candidates", ()):
        documents = {item.document_id: item for item in candidate.documents}
        for fragment in candidate.fragments:
            document = documents.get(fragment.document_id)
            if document is None or not document.identity_binding:
                continue
            if document.source_kind in OFFICIAL_WEB_SOURCE_KINDS:
                parsed = urlsplit(document.canonical_url)
                if parsed.scheme == "https" and parsed.hostname:
                    verified_domains.add(parsed.hostname.lower())
            if candidate.section_id not in groups or fragment.text in seen:
                continue
            seen.add(fragment.text)
            groups[candidate.section_id].append(
                fragment.text[:NEWS_IDENTITY_FRAGMENT_CHARS]
            )
    for row in zip_longest(*(groups[key] for key in NEWS_IDENTITY_CONTEXT_SECTIONS)):
        context.extend(text for text in row if text)
        if sum(map(len, context)) >= NEWS_IDENTITY_CONTEXT_CHARS:
            break
    domain = (
        f"https://{preferred_host.lower()}"
        if preferred_host and preferred_host.lower() in verified_domains
        else ""
    )
    return domain, "\n".join(context)[:NEWS_IDENTITY_CONTEXT_CHARS]


def news_generation_digest(
    official_digest: str, *, news_snapshot_digest: str, as_of: str
) -> str:
    """공시가 같아도 뉴스·기준일이 바뀌면 같은 보고서를 재사용하지 않는다."""

    payload = {
        "contract": NEWS_RESEARCH_CACHE_CONTRACT,
        "official": official_digest,
        "news": news_snapshot_digest,
        "as_of": as_of,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
