"""언론사 뉴스 후보를 선별하고 근거 조각으로 바꾸는 순수 로직."""

from src.features.news_intake.classify import (
    build_classification_prompt,
    classify_and_read,
    classify_candidates,
)
from src.features.news_intake.mapping import (
    map_article_to_fragments,
    map_articles_to_fragments,
    map_news_fragments,
)
from src.features.news_intake.models import (
    ClassifiedNewsCandidate,
    FetchedNewsArticle,
    NewsCandidate,
    NewsClassificationResult,
    NewsEvidenceFragment,
    NewsIntakeDiagnostics,
    NewsMappingResult,
    NewsSelectionResult,
    build_diagnostics,
)
from src.features.news_intake.select import (
    needs_extended_window,
    news_eligible_sections,
    news_trigger,
    select_candidates,
    select_news_items,
)

__all__ = [
    "ClassifiedNewsCandidate",
    "FetchedNewsArticle",
    "NewsCandidate",
    "NewsClassificationResult",
    "NewsEvidenceFragment",
    "NewsIntakeDiagnostics",
    "NewsMappingResult",
    "NewsSelectionResult",
    "build_classification_prompt",
    "build_diagnostics",
    "classify_and_read",
    "classify_candidates",
    "map_article_to_fragments",
    "map_articles_to_fragments",
    "map_news_fragments",
    "needs_extended_window",
    "news_eligible_sections",
    "news_trigger",
    "select_candidates",
    "select_news_items",
]
