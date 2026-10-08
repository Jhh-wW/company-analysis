"""공식 산업 보조 호출의 단계와 결정적 연결 필드."""

OFFICIAL_INDUSTRY_STAGE = "official_industry_context"
INDUSTRY_DISCOVERY_REASON = "official_industry_discovery"
OFFICIAL_INDUSTRY_DIAGNOSTIC_STEP = "공식_산업_보조"
SELECTED_DOCUMENT_FIELDS = (
    ("source_document_id", "document_id"),
    ("source_url", "canonical_url"),
    ("formal_source_kind", "source_kind"),
    ("source_publisher", "publisher"),
    ("document_title", "title"),
    ("document_date", "published_on"),
    ("document_content_sha256", "content_sha256"),
    ("identity_binding", "identity_binding"),
)
SELECTED_FRAGMENT_FIELDS = ("text", "location", "source_context_json", "section_context_json")
SELECTED_PROVENANCE_FIELDS = (
    ("source_collected_on", "collected_at"),
    ("domain_attestation_source_id", "domain_attestation_source_id"),
    ("domain_attestation_evidence", "domain_attestation_evidence"),
    ("reporting_period", "reporting_period"),
    ("attachment_url", "attachment_url"),
    ("ir_metadata_verification", "ir_metadata_verification"),
    ("domain_redirect_verification", "domain_redirect_verification"),
    ("domain_redirect_from_host", "domain_redirect_from_host"),
    ("domain_redirect_to_host", "domain_redirect_to_host"),
)
SUBITEM_FIELDS = ("item_title", "item_url", "item_published_on")
