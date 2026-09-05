from __future__ import annotations

import pytest

from src.core import news_intake_switch
from src.features.composer.port import CollectedFragment
from src.features.composer.public_manifest import _expected_source
from src.features.composer.render import _build_source, _fragment_metas
from src.features.provenance.sources import (
    SourceKind,
    is_publishable_supplementary,
)
from src.shared.report_evidence.constants import SOURCE_KIND_NEWS
from src.shared.report_quality.source_identity import document_identity_from_parts


@pytest.fixture(autouse=True)
def _news_intake_on(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(news_intake_switch.NEWS_INTAKE_ENV_NAME, "1")
    news_intake_switch._reset_process_news_intake_switch_for_tests()  # noqa: SLF001
    yield
    news_intake_switch._reset_process_news_intake_switch_for_tests()  # noqa: SLF001


def _news_fragment() -> CollectedFragment:
    url = "https://news.example.com/articles/n-7"
    return CollectedFragment(
        fragment_id="7",
        kind="뉴스",
        text="회사는 신규 사업을 시작했다고 밝혔다.",
        source_url=url,
        document_title="회사의 신규 사업을 다룬 기사",
        location="본문 2문단",
        document_date="2026-08-30",
        document_identity=document_identity_from_parts(
            document_id="n-7",
            host="news.example.com",
            url=url,
        ),
        document_content_sha256="a" * 64,
        counts_toward_document_floor=False,
        supported_claim_slots=("business_model:revenue_model",),
        formal_source_kind=SOURCE_KIND_NEWS,
        source_document_id="n-7",
        source_publisher="예시경제",
        source_collected_on="2026-09-01",
    )


def test_manifest와_renderer가_같은_언론_부록_Source를_만든다() -> None:
    fragment = _news_fragment()
    expected = _expected_source(
        fragment,
        number=7,
        company_name="예시회사",
        used_in=("business_model",),
        filing_meta=None,
    )
    [meta] = _fragment_metas((fragment,))
    rendered = _build_source(
        meta,
        7,
        "예시회사",
        ("business_model",),
    )

    assert rendered == expected
    assert expected.kind is SourceKind.NEWS
    assert expected.publisher == "예시경제"
    assert expected.title == "회사의 신규 사업을 다룬 기사"
    assert expected.published_at == "2026-08-30"
    assert expected.url == "https://news.example.com/articles/n-7"
    assert is_publishable_supplementary(expected, [expected]) is True
