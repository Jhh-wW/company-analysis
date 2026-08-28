"""이전 공개 경로를 위한 호환 facade. 정본은 shared에 있다."""

from src.shared.report_quality.source_identity import (
    DocumentIdentityInput,
    canonical_url,
    document_identity,
    document_identity_from_parts,
)

__all__ = [
    "DocumentIdentityInput",
    "canonical_url",
    "document_identity",
    "document_identity_from_parts",
]
