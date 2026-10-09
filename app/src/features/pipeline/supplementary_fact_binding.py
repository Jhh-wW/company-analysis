"""보완조사의 최종 사실을 실제 출처 등록부에 다시 결속한다."""

from __future__ import annotations

import json

from src.features.pipeline.port import FactRecord
from src.shared.report_claim_policy import CLAIM_SLOTS_BY_SECTION
from src.shared.report_evidence.source_verification import SourceVerification, SourceVerifier
from src.shared.report_quality.constants import (
    INTERPRETATION_CLAIM_TYPE,
    VERIFIED_PROSE_CLAIM_TYPE,
)
from src.shared.report_quality.fact_binding import (
    fact_evidence_binding,
    fact_primary_source_metadata_mismatches,
)


def bound_supplementary_fact_sources(
    fact: FactRecord,
    *,
    registry: tuple[object, ...],
    source_verifier: SourceVerifier,
    reference_date: str,
    failure_detail: dict[str, object] | None = None,
) -> tuple[SourceVerification, ...]:
    """manifest 산문과 원문형 사실을 구분하고 모든 직접 출처를 확인한다.

    공식 자료인지, 어느 장을 채우는지는 호출자가 판정한다. 이 함수는 사실의
    자기 선언을 믿지 않고 이미 수집·봉인된 출처와 지문을 대조하는 일만 한다.
    """

    def reject(check: str, *, fields: tuple[str, ...] = ()) -> tuple:
        if failure_detail is not None:
            failure_detail.update(stage="fact_binding", reason_code="supplementary_fact_binding_invalid",
                                  check_items=[check], metadata_fields=list(fields))
        return ()

    if type(fact) is not FactRecord or type(registry) is not tuple:
        return reject("fact_or_registry_type")
    try:
        if (
            fact.status != "verified"
            or fact.verification_status != "verified"
            or not fact.claim.strip()
            or fact.claim_slot not in CLAIM_SLOTS_BY_SECTION.get(fact.section_owner, ())
            or not fact.evidence_binding
            or fact.evidence_binding != fact_evidence_binding(fact)
        ):
            check = ("fact_status" if fact.status != "verified"
                     else "verification_status" if fact.verification_status != "verified"
                     else "claim_empty" if not fact.claim.strip()
                     else "claim_slot" if fact.claim_slot not in CLAIM_SLOTS_BY_SECTION.get(fact.section_owner, ())
                     else "evidence_binding_missing" if not fact.evidence_binding else "evidence_binding_mismatch")
            return reject(check)
        ids = tuple(fact.supporting_source_ids)
        identities = tuple(fact.supporting_source_identities)
        hashes = tuple(fact.supporting_evidence_hashes)
        if (
            not ids
            or len(ids) != len(identities)
            or len(ids) != len(hashes)
            or len(ids) != len(set(ids))
            or ids[0] != fact.source_id
        ):
            check = ("supporting_source_ids_empty" if not ids
                     else "supporting_source_identities_length" if len(ids) != len(identities)
                     else "supporting_evidence_hashes_length" if len(ids) != len(hashes)
                     else "supporting_source_ids_unique" if len(ids) != len(set(ids))
                     else "supporting_primary_source_id")
            return reject(check)
        by_id = {getattr(source, "source_id", None): source for source in registry}
        if len(by_id) != len(registry):
            return reject("registry_source_id_unique")
        primary = by_id.get(fact.source_id)
        if primary is None:
            return reject("primary_source_missing")
        mismatches = fact_primary_source_metadata_mismatches(fact, primary)
        if mismatches:
            return reject("primary_source_metadata", fields=mismatches)

        verified_sources: list[SourceVerification] = []
        for source_id, identity, evidence_hash in zip(ids, identities, hashes):
            source = by_id.get(source_id)
            verified = source_verifier(
                source, registry, reference_date=reference_date, evidence_text="",
            )
            if (
                type(verified) is not SourceVerification
                or verified.source_id != source_id
                or verified.document_identity != identity
                or evidence_hash not in verified.exact_evidence_hashes
                or not (verified.official or verified.news)
            ):
                check = ("source_verification" if type(verified) is not SourceVerification
                         else "verified_source_id" if verified.source_id != source_id
                         else "document_identity" if verified.document_identity != identity
                         else "exact_evidence_hash" if evidence_hash not in verified.exact_evidence_hashes
                         else "official_or_news")
                return reject(check)
            verified_sources.append(verified)

        if fact.claim_type in {VERIFIED_PROSE_CLAIM_TYPE, INTERPRETATION_CLAIM_TYPE}:
            try:
                manifest = json.loads(fact.state_evidence)
            except (TypeError, ValueError):
                return reject("manifest_json")
            if type(manifest) is not list or len(manifest) != len(ids):
                return reject("manifest_shape")
            fragment_ids: set[str] = set()
            for index, item in enumerate(manifest):
                if type(item) is not dict:
                    return reject("manifest_item_type")
                fragment_id = item.get("fragment_id")
                if (
                    type(fragment_id) is not str
                    or not fragment_id.strip()
                    or fragment_id in fragment_ids
                    or item.get("source_id") != ids[index]
                    or item.get("document_identity") != identities[index]
                    or item.get("exact_sha256") != hashes[index]
                ):
                    check = ("manifest_fragment_id" if type(fragment_id) is not str or not fragment_id.strip()
                             or fragment_id in fragment_ids
                             else "manifest_source_id" if item.get("source_id") != ids[index]
                             else "manifest_document_identity" if item.get("document_identity") != identities[index]
                             else "manifest_exact_sha256")
                    return reject(check)
                fragment_ids.add(fragment_id)
                if verified_sources[index].news:
                    news_verification = source_verifier(
                        by_id[ids[index]], registry,
                        reference_date=reference_date,
                        evidence_text=item.get("news_exact_text"),
                    )
                    if news_verification is None or not news_verification.evidence_bound:
                        return reject("news_exact_evidence")
        else:
            verified_primary = source_verifier(
                primary, registry,
                reference_date=reference_date,
                evidence_text=fact.state_evidence,
            )
            if verified_primary is None or not verified_primary.evidence_bound:
                return reject("primary_exact_evidence")
        return tuple(verified_sources)
    except (AttributeError, KeyError, TypeError, ValueError):
        return reject("binding_exception")
