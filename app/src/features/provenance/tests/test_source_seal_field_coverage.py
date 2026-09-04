"""Source 필드 추가가 provenance seal 밖으로 조용히 새지 않게 한다."""

from __future__ import annotations

from dataclasses import fields, replace

import pytest

from src.features.provenance.sources import (
    Source,
    SourceKind,
    has_valid_provenance_seal,
    seal_collected_source,
)
from src.shared.report_evidence.constants import FORMAL_DOCUMENT_SOURCE_KINDS


_INTENTIONALLY_UNSEALED_FIELDS = frozenset(
    {
        # seal 결과 자체는 payload 입력일 수 없다.
        "provenance_seal",
        # 최종 FactRecord로부터 계산하는 공개 투영이며 수집 provenance가 아니다.
        "used_in",
    }
)


def _rich_formal_source(source_kind: str) -> Source:
    return seal_collected_source(
        Source(
            number=1,
            kind=SourceKind.FILING,
            label="공식 원문",
            disclosed_at="2026-03-15",
            collected_at="2026-09-04",
            published_at="2026-03-15",
            domain="company.example",
            source_id="formal-source-1",
            title="공식 원문 제목",
            publisher="테스트 회사",
            host="company.example",
            url="https://company.example/document",
            document_id="document-1",
            location="본문",
            source_type="공식 공시",
            fact_status="공시 실제값",
            used_in=["identity"],
            evidence_hashes=["a" * 64],
            exact_evidence_hashes=["b" * 64],
            domain_attestation_source_id="attester-1",
            domain_attestation_evidence="회사 공식 주소 원문",
            provenance_role="citation",
            reporting_period="2026-Q2",
            ir_metadata_verification="verified-ir-document-metadata-v1",
            attachment_url="https://company.example/document.pdf",
            domain_redirect_verification="verified-dart-apex-to-www-v1",
            domain_redirect_from_host="company.example",
            domain_redirect_to_host="www.company.example",
            formal_source_kind=source_kind,
            identity_binding="verified-company-binding",
            document_content_sha256="c" * 64,
        )
    )


def _different_value(value: object) -> object:
    if isinstance(value, SourceKind):
        return (
            SourceKind.OTHER
            if value is not SourceKind.OTHER
            else SourceKind.FILING
        )
    if isinstance(value, int):
        return value + 1
    if isinstance(value, list):
        return [*value, "d" * 64]
    if isinstance(value, str):
        return value + "-변조"
    raise AssertionError(f"새 Source 필드형의 seal 변조값을 정의해야 합니다: {type(value)}")


@pytest.mark.parametrize("source_kind", sorted(FORMAL_DOCUMENT_SOURCE_KINDS))
def test_formal_8종의_모든_수집필드는_하나씩_바꾸면_seal이_깨진다(
    source_kind: str,
) -> None:
    source = _rich_formal_source(source_kind)
    checked: set[str] = set()

    for source_field in fields(Source):
        if source_field.name in _INTENTIONALLY_UNSEALED_FIELDS:
            continue
        changed = replace(
            source,
            **{
                source_field.name: _different_value(
                    getattr(source, source_field.name)
                )
            },
        )
        assert not has_valid_provenance_seal(changed), source_field.name
        checked.add(source_field.name)

    assert checked == {
        source_field.name
        for source_field in fields(Source)
        if source_field.name not in _INTENTIONALLY_UNSEALED_FIELDS
    }


def test_used_in은_수집뒤_계산하는_투영이라_seal을_깨지않는다() -> None:
    source = _rich_formal_source(next(iter(sorted(FORMAL_DOCUMENT_SOURCE_KINDS))))

    assert has_valid_provenance_seal(
        replace(source, used_in=["identity", "business_model"])
    )
