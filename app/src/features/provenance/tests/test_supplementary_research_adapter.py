"""보완조사에서도 기존 출처 봉인·등록부 정본을 그대로 검증한다."""

from dataclasses import replace

import pytest

from src.core.source_verification_adapter import supplementary_research_source_verifier
from src.features.provenance.sources import (
    exact_evidence_text_hash,
    seal_collected_source,
)
from src.features.provenance.supplementary_research_adapter import (
    verify_supplementary_research_source,
)
from src.features.provenance.tests.test_sources import (
    _formal_source_registry,
    _publishable_news_source,
)
from src.shared.report_evidence.constants import (
    FORMAL_DOCUMENT_SOURCE_KINDS,
    OFFICIAL_WEB_SOURCE_KINDS,
    SOURCE_KIND_DART_BUSINESS_REPORT,
    SOURCE_KIND_OFFICIAL_IDENTITY_VERIFIED_WEB_PAGE,
)


def _formal_text(kind: str) -> str:
    if kind == SOURCE_KIND_OFFICIAL_IDENTITY_VERIFIED_WEB_PAGE:
        return "공식 홈페이지 원문"
    return "회사 공식 원문" if kind in OFFICIAL_WEB_SOURCE_KINDS else "공시 원문"


@pytest.mark.parametrize("kind", sorted(FORMAL_DOCUMENT_SOURCE_KINDS))
def test_existing_eight_official_kinds_connect_to_shared_verifier(kind: str) -> None:
    registry = _formal_source_registry(kind)
    verified = verify_supplementary_research_source(
        registry[0], registry, reference_date="2026-09-04",
        evidence_text=_formal_text(kind),
    )
    assert verified is not None
    assert verified.official and verified.evidence_bound and not verified.news
    assert verified.formal_kind == kind
    assert verified.content_sha256 == registry[0].document_content_sha256
    assert verified.document_identity

    # 공식 웹이 의존하는 비공개 attester도 같은 완성 등록부에서 검증한다.
    for dependency in registry[1:]:
        dependency_result = verify_supplementary_research_source(
            dependency, registry, reference_date="2026-09-04", evidence_text="",
        )
        assert dependency_result is not None
        assert not dependency_result.official
        assert not dependency_result.news
        assert not dependency_result.evidence_bound


@pytest.mark.parametrize("kind", sorted(FORMAL_DOCUMENT_SOURCE_KINDS))
def test_tampering_official_eight_kinds_document_fingerprint_is_blocked_by_existing_seal(kind: str) -> None:
    registry = _formal_source_registry(kind)
    changed = replace(registry[0], document_content_sha256="e" * 64)
    assert verify_supplementary_research_source(
        changed, (changed, *registry[1:]), reference_date="2026-09-04",
        evidence_text=_formal_text(kind),
    ) is None


def test_different_body_exact_hash_distinguishes_official_classification_and_body_binding() -> None:
    registry = _formal_source_registry(SOURCE_KIND_DART_BUSINESS_REPORT)
    verified = verify_supplementary_research_source(
        registry[0], registry, reference_date="2026-09-04", evidence_text="공시  원문",
    )
    assert verified is not None and verified.official
    assert not verified.evidence_bound


@pytest.mark.parametrize("field,value", [
    ("formal_source_kind", "future_unregistered_kind"),
    ("source_type", "회사 공식 웹"),
    ("identity_binding", ""),
])
def test_typed_registry_contract_error_is_not_downgraded_to_unofficial(
    field: str, value: str,
) -> None:
    registry = _formal_source_registry(SOURCE_KIND_DART_BUSINESS_REPORT)
    changed = seal_collected_source(replace(registry[0], **{field: value}))
    assert verify_supplementary_research_source(
        changed, (changed,), reference_date="2026-09-04", evidence_text="공시 원문",
    ) is None


def _news():
    text = "가나다전자가 계약을 발표했다"
    source = seal_collected_source(replace(
        _publishable_news_source(),
        exact_evidence_hashes=[exact_evidence_text_hash(text)],
    ))
    return source, text


def test_sealed_news_is_body_evidence_but_not_promoted_to_official_original() -> None:
    source, text = _news()
    verified = verify_supplementary_research_source(
        source, (source,), reference_date="2026-09-04", evidence_text=text,
    )
    assert verified is not None
    assert verified.news and verified.evidence_bound and not verified.official


def test_future_news_is_not_accepted_as_verified_body() -> None:
    source, text = _news()
    source = seal_collected_source(replace(source, published_at="2027-09-04"))
    verified = verify_supplementary_research_source(
        source, (source,), reference_date="2026-09-04", evidence_text=text,
    )
    assert verified is not None and not verified.news and not verified.official


@pytest.mark.parametrize("field,value", [
    ("source_id", None), ("evidence_hashes", None), ("number", True),
    ("published_at", []), ("provenance_seal", "변조"),
])
def test_corrupt_stored_source_fails_closed_without_exception(field: str, value: object) -> None:
    source, text = _news()
    changed = replace(source, **{field: value})
    assert verify_supplementary_research_source(
        changed, (changed,), reference_date="2026-09-04", evidence_text=text,
    ) is None


@pytest.mark.parametrize("registry_kind", ["empty", "duplicate", "foreign", "dict"])
def test_missing_duplicate_mixed_type_registry_is_rejected(registry_kind: str) -> None:
    source, text = _news()
    registry = {
        "empty": (), "duplicate": (source, source),
        "foreign": (replace(source, number=8),), "dict": (source, {}),
    }[registry_kind]
    assert verify_supplementary_research_source(
        source, registry, reference_date="2026-09-04", evidence_text=text,
    ) is None


def test_core_assembly_does_not_duplicate_canonical_function() -> None:
    assert supplementary_research_source_verifier() is verify_supplementary_research_source
