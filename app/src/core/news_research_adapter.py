"""뉴스 기능과 조사 조립부를 연결하는 실행 어댑터.

검색 snapshot은 중복 생성 조정 전에, 유료 본문 분석은 조정 뒤에 실행한다.
뉴스의 선별 정책과 검증 로직은 news_intake 기능 폴더가 소유한다.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, replace
from typing import Any, Callable


def article_published_on(raw_html: str) -> str:
    """실제 읽은 기사 HTML의 발행일을 수집 경계에 전달한다."""

    from src.features.news_intake.fetch import extract_article_published_on

    return extract_article_published_on(raw_html)


@dataclass(frozen=True)
class NewsResearchSession:
    """동일 회사·기준일·검색 결과를 두 실행 단계 사이에 보존한다."""

    company: Any
    as_of: dt.date
    snapshot: Any
    policy: Any = None

    def collect(
        self,
        *,
        fetch_text: Callable[..., Any],
        analyze_grounded: Callable[..., Any],
    ) -> Any:
        from src.features.news_intake.collection import collect_from_snapshot

        return collect_from_snapshot(
            self.snapshot,
            company=self.company,
            as_of=self.as_of,
            fetch_text=fetch_text,
            analyze_grounded=analyze_grounded,
            policy=self.policy,
        )


def prepare_news_research(
    *,
    search_news: Callable[..., Any],
    company_name: str,
    aliases: tuple[str, ...],
    domain: str,
    executive_names: tuple[str, ...],
    identity_context: str,
    as_of: dt.date,
    policy: Any = None,
    max_analysis_calls: int | None = None,
) -> NewsResearchSession:
    """AI 없이 검색을 고정하고 나중 분석에 같은 입력을 전달한다."""

    from src.features.news_intake.collection import collect_search_snapshot
    from src.features.news_intake.models import NewsCollectionPolicy, NewsCompanyContext

    if max_analysis_calls is not None:
        if type(max_analysis_calls) is not int or max_analysis_calls < 0:
            raise ValueError("뉴스 분석에 배정한 호출 수는 0 이상의 정수여야 합니다")
        current_policy = policy or NewsCollectionPolicy()
        policy = replace(
            current_policy,
            max_analysis_calls=min(current_policy.max_analysis_calls, max_analysis_calls),
        )

    company = NewsCompanyContext(
        company_name=company_name,
        aliases=aliases,
        domain=domain,
        executive_names=executive_names,
        identity_context=identity_context,
    )
    snapshot = collect_search_snapshot(
        search_news=search_news, company=company, as_of=as_of, policy=policy
    )
    return NewsResearchSession(company=company, as_of=as_of, snapshot=snapshot, policy=policy)
