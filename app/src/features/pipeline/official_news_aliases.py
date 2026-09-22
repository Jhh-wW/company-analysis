"""이미 결속된 공식 원문의 명시 정의만 뉴스 전용 약칭으로 전달한다."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from typing import Any

from src.features.pipeline import official_news_alias_constants as c
from src.shared.company_identity import (
    exact_company_names_equivalent,
    normalize_korean_registration_number,
)
from src.shared.report_evidence.constants import OFFICIAL_WEB_SOURCE_KINDS
from src.shared.report_evidence.identity_verified_web import (
    parse_verified_dart_filing_official_web_binding,
    parse_verified_dart_filing_subdomain_binding,
)
from src.shared.report_evidence.models import (
    ChapterEvidenceCandidates,
    CollectedEvidenceDocument,
    DocumentTextRange,
    EvidenceFragment,
)
from src.shared.report_evidence.profile_domain_attestation import (
    parse_dart_profile_domain_attestation,
)
from src.shared.report_evidence.runtime_port import OfficialEvidenceCollectionResult
from src.shared.report_evidence.source_kind_policy import formal_web_public_source_metadata
from src.shared.report_quality.source_identity import collected_document_identity


@dataclass(frozen=True)
class OfficialNewsAliasEvidence:
    """약칭을 허용한 정확한 문구와 기존 수집 영수증의 위치."""

    alias: str
    company_id: str
    document_id: str
    canonical_url: str
    document_sha256: str
    fragment_id: str
    location: str
    fragment_sha256: str
    definition_start: int
    definition_end: int
    definition_text: str
    definition_sha256: str
    identity_binding: str
    source_snapshot_sha256: str

    def diagnostic(self) -> dict[str, Any]:
        """운영 진단에 원문을 다시 찾을 수 있는 공개 근거를 남긴다."""

        return asdict(self)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _validate_collection(result: OfficialEvidenceCollectionResult) -> None:
    # frozen DTO라도 조작된 테스트 객체·잘못된 어댑터 입력을 허용하지 않는다.
    for candidate in result.candidates:
        if not isinstance(candidate, ChapterEvidenceCandidates):
            raise ValueError("공식 후보 자료형 불일치")
        for document in candidate.documents:
            if not isinstance(document, CollectedEvidenceDocument):
                raise ValueError("공식 문서 자료형 불일치")
            for span in document.usable_ranges:
                if not isinstance(span, DocumentTextRange):
                    raise ValueError("공식 원문 구간 자료형 불일치")
                replace(span)
            replace(document)
        for fragment in candidate.fragments:
            if not isinstance(fragment, EvidenceFragment):
                raise ValueError("공식 조각 자료형 불일치")
            replace(fragment)
        replace(candidate)
    if replace(result).source_snapshot_sha256 != result.source_snapshot_sha256:
        raise ValueError("공식 원문 지문 불일치")


def _document_matches_profile(document: CollectedEvidenceDocument, profile: Mapping[str, Any]) -> bool:
    if not collected_document_identity(
        source_kind=document.source_kind,
        document_id=document.document_id,
        url=document.canonical_url,
    ):
        return False
    if document.source_kind not in OFFICIAL_WEB_SOURCE_KINDS:
        receipt = document.document_id.rpartition(":")[2]
        # fetcher 메타 없는 unverifiable 기록을 검증된 법인 약칭으로 승격하지 않는다.
        return document.identity_binding == (
            f"corp_code={profile['corp_code']};rcept_no={receipt};"
            f"source_kind={document.source_kind};identity_check={c.DART_VERIFIED_IDENTITY_CHECK}"
        )
    attestation = parse_dart_profile_domain_attestation(document.domain_attestation_evidence)
    if document.domain_attestation_evidence and (
        attestation is None
        or attestation.corp_code != profile["corp_code"]
        or attestation.hm_url != str(profile.get("hm_url") or "").strip()
    ):
        return False
    # 교차 도메인 이중 신원 영수증은 현재 법인과 등록번호에도 계속 묶는다.
    proof = parse_verified_dart_filing_official_web_binding(document.identity_binding)
    subdomain = parse_verified_dart_filing_subdomain_binding(document.identity_binding)
    if subdomain is not None:
        proof = parse_verified_dart_filing_official_web_binding(subdomain.root_binding)
    if proof is not None:
        number_hashes = {
            _sha(number)
            for field in ("bizr_no", "jurir_no")
            if (number := normalize_korean_registration_number(profile.get(field)))
        }
        if proof.provenance.company_id != profile["corp_code"] or proof.registration_number_sha256 not in number_hashes:
            return False
    return formal_web_public_source_metadata(
        source_kind=document.source_kind,
        source_url=document.canonical_url,
        company_name=profile["corp_name"],
        identity_binding=document.identity_binding,
        **{field: getattr(document, field) for field in (
            "domain_attestation_source_id", "domain_attestation_evidence",
            "reporting_period", "attachment_url", "ir_metadata_verification",
            "domain_redirect_verification", "domain_redirect_from_host", "domain_redirect_to_host",
        )},
    ) is not None


def _fragment_matches_location(document: CollectedEvidenceDocument, fragment: EvidenceFragment) -> bool:
    if document.source_kind in OFFICIAL_WEB_SOURCE_KINDS:
        prefix = f"{document.canonical_url}#"
        index = fragment.location.removeprefix(prefix)
        if not fragment.location.startswith(prefix) or re.fullmatch(c.WEB_FRAGMENT_INDEX_PATTERN, index) is None:
            return False
        if int(index) >= len(document.usable_ranges):
            return False
        span = document.usable_ranges[int(index)]
        return span.end - span.start == len(fragment.text)
    match = re.fullmatch(c.DART_FRAGMENT_LOCATION_PATTERN, fragment.location)
    if match is None:
        return False
    start, end = map(int, match.groups())
    return end - start == len(fragment.text) and any(
        (span.start, span.end) == (start, end) for span in document.usable_ranges
    )


def _definitions(text: str, legal_name: str):
    for match in re.finditer(c.ALIAS_DEFINITION_PATTERN, text):
        before = text[:match.start()]
        boundaries = list(re.finditer(c.LEGAL_NAME_BOUNDARY_PATTERN, before))
        start = boundaries[-1].end() if boundaries else 0
        raw_name = before[start:]
        start += len(raw_name) - len(raw_name.lstrip())
        if not exact_company_names_equivalent(raw_name.strip(), legal_name):
            continue
        alias = next(value for value in match.groupdict().values() if value is not None)
        if alias.casefold() in c.GENERIC_COMPANY_REFERENCES or exact_company_names_equivalent(alias, legal_name):
            continue
        sentence_before = list(re.finditer(c.SENTENCE_BOUNDARY_PATTERN, text[:start]))
        sentence_start = sentence_before[-1].end() if sentence_before else 0
        sentence_after = re.search(c.SENTENCE_BOUNDARY_PATTERN, text[match.end():])
        sentence_end = match.end() + sentence_after.start() if sentence_after else len(text)
        if re.search(c.UNSAFE_DEFINITION_CONTEXT_PATTERN, text[sentence_start:sentence_end]):
            continue
        yield alias, start, match.end()


def official_news_aliases(
    profile: Mapping[str, Any],
    official_evidence: object,
    *,
    existing_aliases: tuple[str, ...] = (),
) -> tuple[OfficialNewsAliasEvidence, ...]:
    """검증 실패·근거 부재·충돌은 빈 tuple, 명시 정의는 최대 한 개를 돌려준다.

    파일·네트워크·모델을 호출하지 않는다. 조각을 이어 붙이거나 홈페이지
    신원 검증용 별칭으로 되먹이지 않으며, 기존 DART 별칭이 항상 우선한다.
    """

    if (
        not isinstance(official_evidence, OfficialEvidenceCollectionResult)
        or not isinstance(profile.get("corp_name"), str)
        or not profile["corp_name"].strip()
        or not isinstance(profile.get("corp_code"), str)
        or profile["corp_code"] != official_evidence.company_id
        or profile.get("status", "000") != "000"
    ):
        return ()
    try:
        _validate_collection(official_evidence)
        found: dict[str, OfficialNewsAliasEvidence] = {}
        for candidate in official_evidence.candidates:
            documents = {item.document_id: item for item in candidate.documents}
            for fragment in candidate.fragments:
                document = documents[fragment.document_id]
                if not _document_matches_profile(document, profile) or not _fragment_matches_location(document, fragment):
                    continue
                for alias, start, end in _definitions(fragment.text, profile["corp_name"]):
                    definition = fragment.text[start:end]
                    found.setdefault(alias.casefold(), OfficialNewsAliasEvidence(
                        alias=alias, company_id=official_evidence.company_id,
                        document_id=document.document_id, canonical_url=document.canonical_url,
                        document_sha256=document.content_sha256, fragment_id=fragment.fragment_id,
                        location=fragment.location, fragment_sha256=fragment.text_sha256,
                        definition_start=start, definition_end=end, definition_text=definition,
                        definition_sha256=_sha(definition), identity_binding=document.identity_binding,
                        source_snapshot_sha256=official_evidence.source_snapshot_sha256,
                    ))
        if len(found) > c.MAX_OFFICIAL_NEWS_ALIAS_ADDITIONS:
            return ()
        return tuple(item for item in found.values() if not any(
            exact_company_names_equivalent(item.alias, existing) for existing in existing_aliases
        ))
    except (AttributeError, KeyError, TypeError, ValueError):
        return ()
