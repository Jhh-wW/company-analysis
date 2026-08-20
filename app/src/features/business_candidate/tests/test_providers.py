from __future__ import annotations

from src.features.business_candidate import providers
from src.features.business_candidate.constants import (
    ENV_GOOGLE_PLACES_API_KEY,
    ENV_GOOGLE_PLACES_BILLING_ACK,
    ENV_PROVIDER,
    LOCAL_DART_PROVIDER_TIMEOUT_SEC,
)


class PipelineFixture:
    business_candidate_provider_costs_money = False

    def __init__(self):
        self.kwargs = None

    def search_business_candidates(self, **kwargs):
        self.kwargs = kwargs
        return [
            {
                "candidate_name": "(주)제이와이피엔터테인먼트",
                "address": "서울 강동구 강동대로 205",
                "homepage": "https://www.jype.com/",
                "source_label": "공식 API",
                "source_url": "https://www.jype.com/",
                "snippet": "이 필드는 경계 밖으로 버려야 함",
            }
        ]


def test_운영공급자는_명시설정과_무료표시가_모두_있어야_열린다(monkeypatch):
    pipeline = PipelineFixture()
    monkeypatch.delenv(ENV_PROVIDER, raising=False)
    assert providers.configured_provider(pipeline) is None

    monkeypatch.setenv(ENV_PROVIDER, "pipeline")
    adapter = providers.configured_provider(pipeline)
    assert adapter is not None
    rows = adapter.search(company="JYP", address_hint="서울", limit=3, timeout_sec=2)
    assert rows[0].candidate_name == "(주)제이와이피엔터테인먼트"
    assert adapter.provider_name == "DART"
    assert adapter.resolution_timeout_sec == LOCAL_DART_PROVIDER_TIMEOUT_SEC == 30.0
    assert not hasattr(rows[0], "snippet")


def test_과금표시나_알수없는모드는_fail_closed(monkeypatch):
    pipeline = PipelineFixture()
    pipeline.business_candidate_provider_costs_money = True
    monkeypatch.setenv(ENV_PROVIDER, "pipeline")
    assert providers.configured_provider(pipeline) is None
    monkeypatch.setenv(ENV_PROVIDER, "google-html-scrape")
    assert providers.configured_provider(PipelineFixture()) is None


def test_google_places는_키와_명시적비용승인이_모두_있어야_열린다(monkeypatch):
    monkeypatch.setenv(ENV_PROVIDER, "google_places")
    monkeypatch.delenv(ENV_GOOGLE_PLACES_API_KEY, raising=False)
    monkeypatch.delenv(ENV_GOOGLE_PLACES_BILLING_ACK, raising=False)
    assert providers.configured_provider(object()) is None

    monkeypatch.setenv(ENV_GOOGLE_PLACES_API_KEY, "not-called-test-key")
    assert providers.configured_provider(object()) is None
    monkeypatch.setenv(ENV_GOOGLE_PLACES_BILLING_ACK, "1")
    provider = providers.configured_provider(object(), allow_paid_google=True)
    assert provider is not None
    assert provider.costs_money is True
    assert provider.accounting_cost_krw == 49.0
