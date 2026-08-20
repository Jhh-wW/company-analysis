"""회사 후보 공급자 선택과 어댑터.

기본은 꺼짐이다. Google 검색 HTML을 긁는 구현은 제공하지 않는다. 운영에서 붙일 때는
공식 API/계약된 데이터 공급자를 pipeline의 ``search_business_candidates`` 메서드로
주입하고, 무료·읽기 전용임을 명시해야 한다.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Sequence
from typing import Any

from src.features.business_candidate.constants import (
    ENV_PROVIDER,
    ENV_GOOGLE_PLACES_API_KEY,
    ENV_GOOGLE_PLACES_BILLING_ACK,
    LOCAL_DART_PROVIDER_TIMEOUT_SEC,
    PROVIDER_DISABLED,
    PROVIDER_GOOGLE_PLACES,
    PROVIDER_PIPELINE,
)
from src.features.business_candidate.logic import RawBusinessCandidate


logger = logging.getLogger(__name__)


class PipelineProviderAdapter:
    """운영 pipeline이 제공한 공식 검색 어댑터를 최소 계약으로 감싼다."""

    costs_money = False
    provider_name = "DART"
    # 각 후보 주소를 DART 기업개황으로 조회하므로 한 요청에서 외부 호출을 세 번으로
    # 고정한다. resolver 전체 worker도 세 개뿐이라 timeout 뒤 thread 누적도 제한된다.
    max_results = 3
    # corpCode cold-start + local parse/index + bounded profile enrichment.
    # Google and any generic provider keep the common 8-second boundary.
    resolution_timeout_sec = LOCAL_DART_PROVIDER_TIMEOUT_SEC

    def __init__(self, search: Callable[..., Sequence[object]]) -> None:
        self._search = search

    def search(
        self, *, company: str, address_hint: str, limit: int, timeout_sec: float
    ) -> Sequence[RawBusinessCandidate]:
        rows = self._search(
            company=company,
            address_hint=address_hint,
            limit=limit,
            timeout_sec=timeout_sec,
        )
        out: list[RawBusinessCandidate] = []
        for row in list(rows or ())[:limit]:
            if isinstance(row, RawBusinessCandidate):
                out.append(row)
                continue
            if not isinstance(row, dict):
                continue
            # snippet/HTML 본문 등 나머지 필드는 여기서 의도적으로 버린다.
            out.append(
                RawBusinessCandidate(
                    candidate_name=str(
                        row.get("candidate_name", row.get("legal_name", ""))
                    ),
                    address=str(row.get("address", "")),
                    homepage=str(row.get("homepage", "")),
                    source_label=str(row.get("source_label", "")),
                    source_url=str(row.get("source_url", "")),
                    # This adapter is the DART-local trust boundary. A row cannot
                    # relabel itself as Google to bypass corp_code requirements.
                    provider_name=self.provider_name,
                    attributions=tuple(row.get("attributions", ()) or ()),
                    candidate_ref=str(row.get("candidate_ref", row.get("corp_code", ""))),
                    stock_code=str(row.get("stock_code", "")),
                    modify_date=str(row.get("modify_date", "")),
                    english_name=str(row.get("english_name", row.get("corp_eng_name", ""))),
                    name_match_kind=str(row.get("name_match_kind", "")),
                    name_similarity=(
                        float(row.get("name_similarity", 0.0))
                        if isinstance(row.get("name_similarity", 0.0), (int, float))
                        else 0.0
                    ),
                )
            )
        return out


def configured_local_provider(pipeline: Any):
    """pipeline의 무과금 DART-local 후보 기능만 연다.

    Google 설정과 무관하게 먼저 시도한다. 실제 pipeline이 이 메서드를 제공하지 않거나
    비용형이라고 표시하면 fail-closed한다.
    """
    search = getattr(pipeline, "search_business_candidates", None)
    if not callable(search):
        return None
    if bool(getattr(pipeline, "business_candidate_provider_costs_money", True)):
        logger.error("pipeline 후보 공급자가 무과금 계약이 아니어서 기능을 닫았습니다")
        return None
    return PipelineProviderAdapter(search)


def configured_provider(pipeline: Any, *, allow_paid_google: bool = False):
    """환경설정과 pipeline 능력이 모두 있을 때만 외부 후보 공급자를 연다.

    Google은 로컬 실시간 평가의 화면 동의 검사를 마친 호출부만
    ``allow_paid_google=True``로 열 수 있다. 키/운영 ack만으로는 열리지 않는다.
    """
    mode = os.environ.get(ENV_PROVIDER, PROVIDER_DISABLED).strip().lower()
    if mode in {"", PROVIDER_DISABLED, "none", "off", "0"}:
        return None
    if mode == PROVIDER_GOOGLE_PLACES:
        # Places는 Gemini와 무관한 별도 유료 API다. 키만 있다고 조용히 과금하지
        # 않고 운영자가 명시적으로 비용 사용을 승인한 경우에만 어댑터를 만든다.
        if not allow_paid_google:
            return None
        key = os.environ.get(ENV_GOOGLE_PLACES_API_KEY, "").strip()
        acknowledged = os.environ.get(ENV_GOOGLE_PLACES_BILLING_ACK, "").strip() == "1"
        if not key or not acknowledged:
            logger.error("Google Places 키 또는 명시적 비용 승인이 없어 기능을 닫았습니다")
            return None
        from src.features.business_candidate.google_places import (  # noqa: PLC0415
            GooglePlacesTextSearchProvider,
        )

        return GooglePlacesTextSearchProvider(key)
    if mode != PROVIDER_PIPELINE:
        logger.error("회사 후보 공급자 설정을 인식할 수 없어 기능을 닫았습니다")
        return None
    provider = configured_local_provider(pipeline)
    if provider is None:
        logger.error("pipeline에 무료 회사 후보 공급자 구현이 없어 기능을 닫았습니다")
        return None
    return provider
