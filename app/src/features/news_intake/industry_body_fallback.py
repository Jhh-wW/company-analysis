"""산업 예약의 본문 실패만 같은 질의군의 별개 후보로 제한해서 보충한다."""

from dataclasses import replace
from typing import Mapping

from src.features.news_intake.models import NewsCandidate, NewsCompanyContext
from src.features.news_intake.fetch import article_url_variants, body_fetch_urls
from src.features.news_intake.search_snapshot import _industry_rank


def body_chain_urls(candidate: NewsCandidate) -> frozenset[str]:
    """알려진 원문·포털 주소와 기존 수집기의 주소 변형을 함께 제외한다."""
    return frozenset(variant for url in body_fetch_urls(candidate) for variant in article_url_variants(url))


class IndustryBodyFallback:
    """한 기사에 한 군만 배정하며 선정은 접근 가능성·산업 승인을 뜻하지 않는다."""

    def __init__(self, reservations: Mapping[str, str], *, company: NewsCompanyContext,
                 remaining_allowance: int) -> None:
        self.groups = dict(reservations)
        self.company = company
        self.limit = min(len(reservations), max(0, remaining_allowance))
        self.records: list[dict[str, str]] = []

    def select(self, failed_id: str, remaining: list[NewsCandidate], *,
               excluded_ids: set[str], excluded_urls: set[str]) -> NewsCandidate | None:
        topic = self.groups.get(failed_id)
        if not topic or len(self.records) >= self.limit:
            return None
        eligible = [item for item in remaining if topic in item.topics
                    and item.id not in excluded_ids and item.id not in self.groups
                    and item.source_url not in excluded_urls
                    and not (body_chain_urls(item) & excluded_urls)]
        if not eligible:
            return None
        chosen = max(eligible, key=lambda item: _industry_rank(replace(item, topics=(topic,)), self.company))
        self.groups[chosen.id] = topic
        self.records.append({"failed_id": failed_id, "candidate_id": chosen.id, "topic": topic})
        return chosen
