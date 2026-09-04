"""FULL packet이 typed 문서 전체 지문을 유료 호출 전에 강제한다."""

from __future__ import annotations

import pytest

from src.features.composer.port import CollectedFragment, SectionEvidencePacket
from src.shared.official_ir import IR_METADATA_VERIFICATION_VALUE
from src.shared.report_evidence.constants import (
    FORMAL_DOCUMENT_SOURCE_KINDS,
    SOURCE_KIND_OFFICIAL_IR_PDF,
)
from src.shared.report_evidence.source_kind_policy import (
    document_slots_for_formal_source_kind,
)


def _formal_fragment(source_kind: str, *, content_sha256: str) -> CollectedFragment:
    slot_id = sorted(document_slots_for_formal_source_kind(source_kind))[0]
    is_ir = source_kind == SOURCE_KIND_OFFICIAL_IR_PDF
    return CollectedFragment(
        fragment_id="1",
        kind="typed-evidence-v1:test",
        text="테스트 회사의 공식 원문이다.",
        source_url="https://company.example/document",
        document_title="공식 원문",
        location="본문",
        document_date="2026-06-30",
        document_identity=f"{source_kind}:document-1",
        document_content_sha256=content_sha256,
        supported_claim_slots=(slot_id,),
        formal_source_kind=source_kind,
        source_document_id=f"{source_kind}:document-1",
        source_publisher="테스트 회사",
        identity_binding="verified-company-binding",
        source_collected_on="2026-09-04",
        domain_attestation_source_id="profile-attester" if is_ir else "",
        domain_attestation_evidence="공식 기업개황 원문" if is_ir else "",
        reporting_period="2026-Q2" if is_ir else "",
        attachment_url=(
            "https://company.example/ir/2026-q2.pdf" if is_ir else ""
        ),
        ir_metadata_verification=(
            IR_METADATA_VERIFICATION_VALUE if is_ir else ""
        ),
    )


@pytest.mark.parametrize("source_kind", sorted(FORMAL_DOCUMENT_SOURCE_KINDS))
def test_formal_8종은_문서전체지문을_지우면_packet이_즉시_거절한다(
    source_kind: str,
) -> None:
    fragment = _formal_fragment(source_kind, content_sha256="")
    section_id = fragment.supported_claim_slots[0].split(":", 1)[0]

    with pytest.raises(ValueError, match="문서지문"):
        SectionEvidencePacket(
            company_id="00126380",
            evidence_generation_sha256="a" * 64,
            section_id=section_id,
            fragments=(fragment,),
        )


def test_legacy_nonformal_조각은_문서전체지문이_없어도_호환된다() -> None:
    fragment = CollectedFragment(
        fragment_id="1",
        kind="legacy-evidence",
        text="이 필드는 문서 전체 지문 도입 전 수집된 원문이다.",
        document_identity="legacy:document-1",
        supported_claim_slots=("identity:corporate_identity",),
    )

    packet = SectionEvidencePacket(
        company_id="00126380",
        evidence_generation_sha256="a" * 64,
        section_id="identity",
        fragments=(fragment,),
    )

    assert packet.fragments[0].document_content_sha256 == ""
