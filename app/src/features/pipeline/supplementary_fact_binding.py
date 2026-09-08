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
) -> tuple[SourceVerification, ...]:
    """manifest 산문과 원문형 사실을 구분하고 모든 직접 출처를 확인한다.

    공식 자료인지, 어느 장을 채우는지는 호출자가 판정한다. 이 함수는 사실의
    자기 선언을 믿지 않고 이미 수집·봉인된 출처와 지문을 대조하는 일만 한다.
    """

    if type(fact) is not FactRecord or type(registry) is not tuple:
        return ()
    try:
        if (
            fact.status != "verified"
            or fact.verification_status != "verified"
            or not fact.claim.strip()
            or fact.claim_slot not in CLAIM_SLOTS_BY_SECTION.get(fact.section_owner, ())
            or not fact.evidence_binding
            or fact.evidence_binding != fact_evidence_binding(fact)
        ):
            return ()
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
            return ()
        by_id = {getattr(source, "source_id", None): source for source in registry}
        if len(by_id) != len(registry):
            return ()
        primary = by_id.get(fact.source_id)
        if primary is None or fact_primary_source_metadata_mismatches(fact, primary):
            return ()

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
                return ()
            verified_sources.append(verified)

        if fact.claim_type in {VERIFIED_PROSE_CLAIM_TYPE, INTERPRETATION_CLAIM_TYPE}:
            manifest = json.loads(fact.state_evidence)
            if type(manifest) is not list or len(manifest) != len(ids):
                return ()
            fragment_ids: set[str] = set()
            for index, item in enumerate(manifest):
                if type(item) is not dict:
                    return ()
                fragment_id = item.get("fragment_id")
                if (
                    type(fragment_id) is not str
                    or not fragment_id.strip()
                    or fragment_id in fragment_ids
                    or item.get("source_id") != ids[index]
                    or item.get("document_identity") != identities[index]
                    or item.get("exact_sha256") != hashes[index]
                ):
                    return ()
                fragment_ids.add(fragment_id)
                if verified_sources[index].news:
                    news_verification = source_verifier(
                        by_id[ids[index]], registry,
                        reference_date=reference_date,
                        evidence_text=item.get("news_exact_text"),
                    )
                    if news_verification is None or not news_verification.evidence_bound:
                        return ()
        else:
            verified_primary = source_verifier(
                primary, registry,
                reference_date=reference_date,
                evidence_text=fact.state_evidence,
            )
            if verified_primary is None or not verified_primary.evidence_bound:
                return ()
        return tuple(verified_sources)
    except (AttributeError, KeyError, TypeError, ValueError):
        return ()
