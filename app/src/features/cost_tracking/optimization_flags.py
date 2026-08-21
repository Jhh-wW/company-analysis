"""Quality-preserving optimization switches; every switch defaults off."""

from __future__ import annotations

import os
from typing import Final


ENV_POPULAR_COMPANY_BATCH: Final[str] = "POPULAR_COMPANY_BATCH_ENABLED"
ENV_ADAPTIVE_VOTING: Final[str] = "ADAPTIVE_VOTING_ENABLED"


def popular_company_batch_enabled() -> bool:
    """Batch is reserved for pre-generation and has no live request path."""

    return os.environ.get(ENV_POPULAR_COMPANY_BATCH, "").strip() == "1"


def adaptive_voting_enabled(
    *,
    shadow_cases: int,
    same_quality_cases: int,
    major_incorrect_releases: int,
) -> bool:
    """Require explicit flag plus the locked 25-case shadow evidence."""

    requested = os.environ.get(ENV_ADAPTIVE_VOTING, "").strip() == "1"
    return (
        requested
        and shadow_cases >= 25
        and same_quality_cases == shadow_cases
        and major_incorrect_releases == 0
    )
