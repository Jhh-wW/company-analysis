"""이미 검증된 무분류 원문을 산업 보조 조사 전용 자료로 운반한다."""

from collections.abc import Callable, Mapping

from src.shared.report_evidence.constants import SourceRequirement, SourceTier
from src.shared.report_evidence.date_normalization import normalize_official_source_date
from src.shared.report_evidence.industry_candidates import OfficialIndustryCandidateEvidence
from src.shared.report_evidence.models import CollectedEvidenceDocument, DocumentTextRange
from src.web import official_industry_evidence_constants as c


def industry_candidates_from_envelope(
    envelope: Mapping[str, object], *, company_id: str, validate: Callable,
) -> tuple[OfficialIndustryCandidateEvidence, ...]:
    """빈 슬롯·원문·sidecar를 기존 관측 검증기로 확인한 뒤 별도 DTO를 만든다.

    무분류 문서의 빈 근거 목록은 변경하지 않는다. 조사 전용 문서에는 검증한
    해당 원문들의 지문만 넣으며 회사 사실이나 필수 장의 충족으로 세지 않는다.
    """
    if validate(envelope, company_id=company_id) is None:
        return ()
    documents = {row["document_id"]: row for row in envelope["unclassified_documents"]}
    fragments = tuple(row for row in envelope["unclassified_fragments"]
                      if c.DISCOVERY_REASON in row["reason_codes"])
    typed_documents = {}
    for document_id in dict.fromkeys(row["document_id"] for row in fragments):
        raw = documents[document_id]
        typed_documents[document_id] = CollectedEvidenceDocument(
            company_id=raw["company_id"], document_id=document_id,
            canonical_url=raw["canonical_url"], source_tier=SourceTier(raw["source_tier"]),
            source_kind=raw["source_kind"], publisher=raw["publisher"], title=raw["title"],
            published_on=normalize_official_source_date(raw["published_on"]),
            collected_at=raw["collected_at"], content_sha256=raw["content_sha256"],
            exact_evidence_hashes=tuple(dict.fromkeys(row["text_sha256"] for row in fragments
                                                   if row["document_id"] == document_id)),
            identity_binding=raw["identity_binding"],
            usable_ranges=tuple(DocumentTextRange(start=row["start"], end=row["end"])
                                for row in raw["usable_ranges"]),
            collector_version=raw["collector_version"], parser_version=raw["parser_version"],
            requirement=SourceRequirement(raw["requirement"]),
            **{name: raw.get(name, "") for name in c.OPTIONAL_DOCUMENT_FIELDS},
        )
    return tuple(OfficialIndustryCandidateEvidence(
        company_id=row["company_id"], fragment_id=row["fragment_id"],
        document=typed_documents[row["document_id"]], location=row["location"],
        text_sha256=row["text_sha256"], text=row["text"],
        source_context_json=row.get("source_context_json", ""),
        section_context_json=row.get("section_context_json", ""),
    ) for row in fragments)
