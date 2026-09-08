"""보완 조사 출처 검증기의 독립 경계·양성 대조 시험.

정본 8종 전체를 복제하지 않고, 실제 ``Source`` 생성물이 통과하는 경로와
오염된 DTO·뉴스 신뢰 경계·core 어댑터 계약을 별도로 점검한다.
"""

from dataclasses import replace
import inspect

import pytest

from src.core.source_verification_adapter import supplementary_research_source_verifier
from src.features.provenance.sources import (
    Source,
    SourceKind,
    exact_evidence_text_hash,
    evidence_text_hash,
    seal_collected_source,
)
from src.features.provenance.supplementary_research_adapter import (
    verify_supplementary_research_source,
)


def _news_source(*, published_at: str = "2026-09-04", domain: str = "news.example") -> tuple[Source, str]:
    evidence = "Example Corp launched Project A."
    source = Source(
        number=1,
        kind=SourceKind.NEWS,
        label="Example Corp launches Project A",
        published_at=published_at,
        domain=domain,
        source_id="source-news-1",
        title="Example Corp launches Project A",
        publisher="Example News",
        host=domain,
        url=f"https://{domain}/article/project-a",
        document_id="article-project-a",
        location="article body",
        source_type="언론 보도",
        fact_status="보도 확인",
        evidence_hashes=[evidence_text_hash(evidence)],
        exact_evidence_hashes=[exact_evidence_text_hash(evidence)],
    )
    return seal_collected_source(source), evidence


def test_actual_canonical_news_shape_preserves_exact_hash_and_evidence_binding() -> None:
    source, evidence = _news_source()

    verified = verify_supplementary_research_source(
        source,
        (source,),
        reference_date="2026-09-08",
        evidence_text=evidence,
    )

    assert verified is not None
    assert verified.news is True
    assert verified.official is False
    assert verified.evidence_bound is True
    assert verified.exact_evidence_hashes == (exact_evidence_text_hash(evidence),)
    assert verified.document_identity


def test_exact_hash_is_case_sensitive_even_when_legacy_hash_is_normalized() -> None:
    source, evidence = _news_source()

    verified = verify_supplementary_research_source(
        source,
        (source,),
        reference_date="2026-09-08",
        evidence_text=evidence.lower(),
    )

    assert verified is not None
    assert verified.news is True
    assert verified.evidence_bound is False


def test_future_news_is_retained_but_not_marked_publishable_as_of_reference_date() -> None:
    source, evidence = _news_source(published_at="2026-09-09")

    verified = verify_supplementary_research_source(
        source,
        (source,),
        reference_date="2026-09-08",
        evidence_text=evidence,
    )

    assert verified is not None
    assert verified.news is False
    assert verified.evidence_bound is True


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("source_id", None),
        ("evidence_hashes", None),
        ("exact_evidence_hashes", None),
        ("number", True),
        ("published_at", []),
        ("formal_source_kind", 7),
        ("provenance_seal", None),
        ("kind", None),
    ],
)
def test_malformed_source_fields_fail_closed_without_exception(field: str, value: object) -> None:
    source, evidence = _news_source()
    malformed = replace(source, **{field: value})

    assert verify_supplementary_research_source(
        malformed,
        (malformed,),
        reference_date="2026-09-08",
        evidence_text=evidence,
    ) is None


@pytest.mark.parametrize(
    ("registry", "reference_date", "evidence_text"),
    [
        ([], "2026-09-08", "Example Corp launched Project A."),
        ({}, "2026-09-08", "Example Corp launched Project A."),
        (None, "2026-09-08", "Example Corp launched Project A."),
        ("not-a-registry", "2026-09-08", "Example Corp launched Project A."),
        ("valid-registry", None, "Example Corp launched Project A."),
        ("valid-registry", [], "Example Corp launched Project A."),
        ("valid-registry", "2026-09-08", None),
        ("valid-registry", "2026-09-08", []),
    ],
)
def test_malformed_call_boundary_fails_closed_without_exception(
    registry: object, reference_date: object, evidence_text: object
) -> None:
    source, _evidence = _news_source()
    actual_registry = (source,) if registry == "valid-registry" else registry

    assert verify_supplementary_research_source(
        source,
        actual_registry,
        reference_date=reference_date,
        evidence_text=evidence_text,
    ) is None


def test_news_trust_is_upstream_policy_and_adapter_does_not_duplicate_allowlist() -> None:
    source, evidence = _news_source(domain="unlisted.example")

    verified = verify_supplementary_research_source(
        source,
        (source,),
        reference_date="2026-09-08",
        evidence_text=evidence,
    )

    # search_snapshot.source_category가 허용 도메인만 Source로 넘긴다는 전제에서,
    # adapter가 별도 언론사 목록을 복제하지 않는 현재 계약을 고정한다.
    assert verified is not None and verified.news is True


def test_core_adapter_is_callable_but_does_not_implement_pipeline_release_port() -> None:
    verifier = supplementary_research_source_verifier()

    assert verifier is verify_supplementary_research_source
    assert callable(verifier)
    assert "evidence_text" in inspect.signature(verifier).parameters
    assert not hasattr(verifier, "verify_source")
    assert not hasattr(verifier, "fact_evidence_is_bound")
