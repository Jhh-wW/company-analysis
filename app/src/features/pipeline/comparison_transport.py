"""typed 공식 수집물을 양사 비교 생산기의 Source 계약으로 옮긴다.

일반 ``build_citations``는 legacy 최신 공시 한 건을 모든 공시 조각에 붙이는
호환 경계다. typed 문서를 거기로 보내면 원래 접수번호·URL·내용 hash가 다른
최신 공시로 바뀔 수 있다. 이 어댑터는 수집기가 봉인한 문서와 조각을 직접
대조하고, 그 신원에서만 비교 후보 Source를 만든다.
"""

from __future__ import annotations

import hashlib
import re
import urllib.parse
from collections.abc import Mapping

from src.features.company_comparison.official_sources import (
    OfficialCandidateSentence,
    candidate_sentences_from_fragments,
    dart_profile_attestation_material,
)
from src.features.pipeline.evidence_transport import (
    RAW_EVIDENCE_ATTACHMENT_URL_KEY,
    RAW_EVIDENCE_COLLECTED_ON_KEY,
    RAW_EVIDENCE_COMPANY_ID_KEY,
    RAW_EVIDENCE_DOCUMENT_CONTENT_SHA256_KEY,
    RAW_EVIDENCE_DOCUMENT_IDENTITY_KEY,
    RAW_EVIDENCE_DOMAIN_ATTESTATION_EVIDENCE_KEY,
    RAW_EVIDENCE_DOMAIN_ATTESTATION_SOURCE_ID_KEY,
    RAW_EVIDENCE_DOMAIN_REDIRECT_FROM_HOST_KEY,
    RAW_EVIDENCE_DOMAIN_REDIRECT_TO_HOST_KEY,
    RAW_EVIDENCE_DOMAIN_REDIRECT_VERIFICATION_KEY,
    RAW_EVIDENCE_IDENTITY_BINDING_KEY,
    RAW_EVIDENCE_IR_METADATA_VERIFICATION_KEY,
    RAW_EVIDENCE_PUBLISHER_KEY,
    RAW_EVIDENCE_REPORTING_PERIOD_KEY,
)
from src.features.provenance.sources import (
    Source,
    SourceKind,
    build_dart_profile_attester_source,
    ensure_dart_profile_attesters,
    evidence_text_hash,
    exact_evidence_text_hash,
    full_typed_source_registry_problem,
    seal_collected_source,
)
from src.shared.report_evidence.constants import (
    FORMAL_DOCUMENT_SOURCE_KINDS,
    OFFICIAL_WEB_SOURCE_KINDS,
    SOURCE_KIND_DART_AUDIT_REPORT,
    SOURCE_KIND_DART_BUSINESS_REPORT,
    SOURCE_KIND_DART_QUARTERLY_REPORT,
    SOURCE_KIND_DART_SEMIANNUAL_REPORT,
    SOURCE_KIND_OFFICIAL_IR_PDF,
    SOURCE_KIND_OFFICIAL_WEB_PAGE,
)
from src.shared.report_evidence.date_normalization import (
    normalize_official_source_date,
)
from src.shared.report_evidence.runtime_port import OfficialEvidenceCollectionResult
from src.shared.report_evidence.models import CollectedEvidenceDocument
from src.shared.report_evidence.profile_domain_attestation import (
    parse_dart_profile_domain_attestation,
)
from src.shared.report_evidence.source_kind_policy import (
    formal_web_public_source_metadata,
)
from src.shared.report_quality.source_identity import collected_document_identity


_DART_SOURCE_KINDS = frozenset(
    {
        SOURCE_KIND_DART_BUSINESS_REPORT,
        SOURCE_KIND_DART_AUDIT_REPORT,
        SOURCE_KIND_DART_SEMIANNUAL_REPORT,
        SOURCE_KIND_DART_QUARTERLY_REPORT,
    }
)
_DART_RECEIPT = re.compile(r"[0-9]{14}")


def _source_date(
    value: object,
    *,
    source_kind: str = "",
    source_url: str = "",
    collected_on: str = "",
) -> str:
    """종류별 날짜 계약을 지키며 Source 날짜를 canonical ISO로 만든다.

    DART 4종과 IR은 실제 발행일이 필수라 빈 값도 계약 오류다. 반면 회사
    홈페이지·채용 HTML은 발행일을 제공하지 않을 수 있으므로 수집일을
    확인일로 쓴다. 수집 단계가 문서형 과거 페이지의 현재성을 판정하며,
    이 transport는 그 판정을 다시 URL 문자열 추측으로 뒤집지 않는다.
    """

    raw = str(value or "").strip()
    if raw:
        return normalize_official_source_date(raw)
    if source_kind in OFFICIAL_WEB_SOURCE_KINDS and source_kind != SOURCE_KIND_OFFICIAL_IR_PDF:
        return normalize_official_source_date(collected_on)
    return normalize_official_source_date(raw)


def _evidence_payloads(fragment: Mapping[str, object]) -> tuple[str, ...]:
    raw = str(fragment.get("원문") or "").strip()
    structured = fragment.get("근거원문") or ()
    if isinstance(structured, str):
        structured = (structured,)
    if not isinstance(structured, (list, tuple)):
        raise ValueError("typed 비교 후보의 근거원문 배열이 올바르지 않습니다")
    return tuple(
        dict.fromkeys(
            value
            for value in (
                raw,
                *(str(item).strip() for item in structured),
            )
            if value
        )
    )


def _document_registry(
    result: OfficialEvidenceCollectionResult,
) -> dict[str, CollectedEvidenceDocument]:
    documents: dict[str, CollectedEvidenceDocument] = {}
    for candidate in result.candidates:
        for document in candidate.documents:
            existing = documents.setdefault(document.document_id, document)
            if existing != document:
                raise ValueError("typed 비교 후보 문서 ID가 서로 다른 문서를 가리킵니다")
    return documents


_RAW_DOCUMENT_FIELDS = (
    (RAW_EVIDENCE_IDENTITY_BINDING_KEY, "identity_binding"),
    (RAW_EVIDENCE_PUBLISHER_KEY, "publisher"),
    (RAW_EVIDENCE_COLLECTED_ON_KEY, "collected_at"),
    (RAW_EVIDENCE_DOMAIN_ATTESTATION_SOURCE_ID_KEY, "domain_attestation_source_id"),
    (RAW_EVIDENCE_DOMAIN_ATTESTATION_EVIDENCE_KEY, "domain_attestation_evidence"),
    (RAW_EVIDENCE_REPORTING_PERIOD_KEY, "reporting_period"),
    (RAW_EVIDENCE_ATTACHMENT_URL_KEY, "attachment_url"),
    (RAW_EVIDENCE_IR_METADATA_VERIFICATION_KEY, "ir_metadata_verification"),
    (RAW_EVIDENCE_DOMAIN_REDIRECT_VERIFICATION_KEY, "domain_redirect_verification"),
    (RAW_EVIDENCE_DOMAIN_REDIRECT_FROM_HOST_KEY, "domain_redirect_from_host"),
    (RAW_EVIDENCE_DOMAIN_REDIRECT_TO_HOST_KEY, "domain_redirect_to_host"),
)


def _require_raw_document_round_trip(
    raw: Mapping[str, object],
    document: CollectedEvidenceDocument,
) -> None:
    """typed→숫자 조각 변환이 formal 문서 메타를 한 칸도 바꾸지 않았는지 본다."""

    for key, attribute in _RAW_DOCUMENT_FIELDS:
        if str(raw.get(key) or "").strip() != str(
            getattr(document, attribute)
        ).strip():
            raise ValueError(f"typed 비교 후보가 문서 provenance 필드를 잃었습니다: {key}")
    for key, expected in (
        ("종류", document.source_kind),
        ("출처", document.canonical_url),
        ("문서ID", document.document_id),
        ("문서명", document.title),
        ("문서일", document.published_on),
    ):
        if str(raw.get(key) or "").strip() != str(expected).strip():
            raise ValueError(f"typed 비교 후보가 문서 표시 필드를 바꿨습니다: {key}")


def _require_profile_material_matches_request(
    document: CollectedEvidenceDocument,
    *,
    profile: Mapping[str, object],
    corp_code: str,
    company_name: str,
) -> None:
    """문서가 운반한 기업개황 proof를 현재 요청의 같은 응답과 대조한다."""

    source_id = document.domain_attestation_source_id.strip()
    evidence = document.domain_attestation_evidence.strip()
    if not source_id and not evidence:
        return
    expected_id, expected_evidence = dart_profile_attestation_material(
        profile=profile,
        corp_code=corp_code,
        company_name=company_name,
    )
    parsed = parse_dart_profile_domain_attestation(evidence)
    if (
        not expected_id
        or parsed is None
        or source_id != expected_id
        or parsed.base_evidence != expected_evidence
    ):
        raise ValueError("typed 비교 후보의 기업개황 proof가 현재 회사 요청과 다릅니다")


def _formal_source_from_document(
    *,
    number: int,
    raw: Mapping[str, object],
    document: CollectedEvidenceDocument,
    company_name: str,
    collected_on: str,
    evidence_hashes: list[str],
    exact_hashes: list[str],
) -> Source:
    """formal 문서 한 건을 종류별 공용 projection으로 Source에 옮긴다."""

    source_kind = document.source_kind
    source_url = document.canonical_url
    title = document.title.strip()
    location = str(raw.get("원문위치") or "").strip()
    collected_at = normalize_official_source_date(document.collected_at)
    if collected_at != normalize_official_source_date(collected_on):
        raise ValueError("typed 비교 후보의 수집일이 현재 요청 기준일과 다릅니다")
    published_on = _source_date(
        document.published_on,
        source_kind=source_kind,
        source_url=source_url,
        collected_on=collected_at,
    )

    if source_kind in _DART_SOURCE_KINDS:
        receipt = document.document_id.rpartition(":")[2]
        try:
            host = (
                urllib.parse.urlsplit(source_url).hostname or ""
            ).casefold().rstrip(".")
        except ValueError as error:
            raise ValueError("typed DART 비교 후보 URL이 올바르지 않습니다") from error
        if _DART_RECEIPT.fullmatch(receipt) is None:
            raise ValueError("typed DART 비교 후보 접수번호가 올바르지 않습니다")
        return seal_collected_source(
            Source(
                number=number,
                kind=SourceKind.FILING,
                label=title or source_kind,
                disclosed_at=published_on,
                collected_at=collected_at,
                source_id=f"typed-comparison-source-{number}",
                title=title or source_kind,
                # DART는 보관소이고 공시 내용의 발행 주체는 확인된 법인이다.
                publisher=company_name,
                host=host,
                url=source_url,
                document_id=receipt,
                location=location,
                source_type="공식 공시",
                fact_status="공시 실제값",
                evidence_hashes=evidence_hashes,
                exact_evidence_hashes=exact_hashes,
                document_content_sha256=document.content_sha256,
                formal_source_kind=source_kind,
                identity_binding=document.identity_binding,
            )
        )

    formal_web = formal_web_public_source_metadata(
        source_kind=source_kind,
        source_url=source_url,
        company_name=company_name,
        identity_binding=document.identity_binding,
        domain_attestation_source_id=document.domain_attestation_source_id,
        domain_attestation_evidence=document.domain_attestation_evidence,
        reporting_period=document.reporting_period,
        attachment_url=document.attachment_url,
        ir_metadata_verification=document.ir_metadata_verification,
        domain_redirect_verification=document.domain_redirect_verification,
        domain_redirect_from_host=document.domain_redirect_from_host,
        domain_redirect_to_host=document.domain_redirect_to_host,
    )
    if formal_web is None:
        raise ValueError("typed 공식 웹 비교 후보의 종류·URL·회사 proof가 다릅니다")
    return seal_collected_source(
        Source(
            number=number,
            kind=SourceKind.OTHER,
            label=title or source_url,
            collected_at=collected_at,
            published_at=published_on,
            source_id=f"typed-comparison-source-{number}",
            title=title or source_url,
            publisher=company_name,
            host=formal_web.host,
            url=source_url,
            document_id=document.document_id,
            location=location,
            source_type=formal_web.source_type,
            fact_status=(
                "공식 발행일·보고기간 확정"
                if source_kind == SOURCE_KIND_OFFICIAL_IR_PDF
                else "기준일 현재 확인"
            ),
            evidence_hashes=evidence_hashes,
            exact_evidence_hashes=exact_hashes,
            document_content_sha256=document.content_sha256,
            formal_source_kind=formal_web.formal_source_kind,
            identity_binding=formal_web.identity_binding,
            domain_attestation_source_id=formal_web.domain_attestation_source_id,
            domain_attestation_evidence=formal_web.domain_attestation_evidence,
            reporting_period=formal_web.reporting_period,
            attachment_url=formal_web.attachment_url,
            ir_metadata_verification=formal_web.ir_metadata_verification,
            domain_redirect_verification=formal_web.domain_redirect_verification,
            domain_redirect_from_host=formal_web.domain_redirect_from_host,
            domain_redirect_to_host=formal_web.domain_redirect_to_host,
        )
    )


def build_typed_comparison_candidate_inputs(
    fragments: Mapping[int, Mapping[str, object]],
    *,
    result: OfficialEvidenceCollectionResult,
    profile: Mapping[str, object],
    corp_code: str,
    company_name: str,
    collected_on: str,
) -> tuple[tuple[Source, ...], tuple[OfficialCandidateSentence, ...]]:
    """formal 문서 신원을 보존한 후보 Source·원문 문장을 만든다."""

    if result.company_id != corp_code:
        raise ValueError("typed 비교 후보 묶음의 회사 식별자가 다릅니다")
    documents = _document_registry(result)
    copied = {int(number): dict(raw) for number, raw in fragments.items()}

    sources: list[Source] = []
    for number, raw in sorted(copied.items()):
        source_kind = str(raw.get("종류") or "").strip()
        if source_kind not in FORMAL_DOCUMENT_SOURCE_KINDS:
            raise ValueError("typed 비교 후보에 등록되지 않은 공식 자료종류가 있습니다")
        document_id = str(raw.get("문서ID") or "").strip()
        document = documents.get(document_id)
        if document is None:
            raise ValueError("typed 비교 후보 조각의 원본 문서가 없습니다")
        _require_raw_document_round_trip(raw, document)
        _require_profile_material_matches_request(
            document,
            profile=profile,
            corp_code=corp_code,
            company_name=company_name,
        )
        source_url = str(raw.get("출처") or "").strip()
        declared_identity = str(
            raw.get(RAW_EVIDENCE_DOCUMENT_IDENTITY_KEY) or ""
        ).strip()
        declared_content_hash = str(
            raw.get(RAW_EVIDENCE_DOCUMENT_CONTENT_SHA256_KEY) or ""
        ).strip()
        expected_identity = collected_document_identity(
            source_kind=source_kind,
            document_id=document_id,
            url=source_url,
        )
        raw_text = str(raw.get("원문") or "")
        if (
            str(raw.get(RAW_EVIDENCE_COMPANY_ID_KEY) or "").strip() != corp_code
            or document.company_id != corp_code
            or document.source_kind != source_kind
            or document.canonical_url != source_url
            or document.content_sha256 != declared_content_hash
            or not expected_identity
            or expected_identity != declared_identity
            or hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
            not in document.exact_evidence_hashes
        ):
            raise ValueError("typed 비교 후보의 회사·문서·원문 결속이 깨졌습니다")

        payloads = _evidence_payloads(raw)
        evidence_hashes = sorted(
            {digest for text in payloads if (digest := evidence_text_hash(text))}
        )
        exact_hashes = sorted(
            {digest for text in payloads if (digest := exact_evidence_text_hash(text))}
        )
        sources.append(
            _formal_source_from_document(
                number=number,
                raw=raw,
                document=document,
                company_name=company_name,
                collected_on=collected_on,
                evidence_hashes=evidence_hashes,
                exact_hashes=exact_hashes,
            )
        )

    # 1~8장 의미 분류에 실패했지만 공용 비교 문장 판별기를 통과한 DART
    # 원문은 별도 후보 차선으로만 들어온다. 장 슬롯을 새로 붙이지 않고 원본
    # 문서·위치·content hash에 결속된 Source와 한 문장만 만든다.
    comparison_rows: list[OfficialCandidateSentence] = []
    next_candidate_number = max(copied, default=0) + 1
    initial_rows = candidate_sentences_from_fragments(copied, sources)
    existing_candidate_keys = {
        (
            row.document_identity,
            exact_evidence_text_hash(row.evidence_text),
        )
        for row in initial_rows
    }
    for item in result.comparison_candidates:
        if item.source_kind not in _DART_SOURCE_KINDS:
            raise ValueError("typed 비교 후보 전용 차선에는 DART 원문만 허용됩니다")
        document_identity = collected_document_identity(
            source_kind=item.source_kind,
            document_id=item.document_id,
            url=item.canonical_url,
        )
        exact_hash = exact_evidence_text_hash(item.evidence_text)
        if (
            not document_identity
            or exact_hash != item.evidence_sha256
            or (document_identity, exact_hash) in existing_candidate_keys
        ):
            if (document_identity, exact_hash) in existing_candidate_keys:
                continue
            raise ValueError("typed 비교 후보 전용 원문의 문서·hash 결속이 깨졌습니다")
        receipt = item.document_id.rpartition(":")[2]
        try:
            host = (
                urllib.parse.urlsplit(item.canonical_url).hostname or ""
            ).casefold().rstrip(".")
        except ValueError as error:
            raise ValueError("typed DART 비교 후보 URL이 올바르지 않습니다") from error
        if _DART_RECEIPT.fullmatch(receipt) is None or host != "dart.fss.or.kr":
            raise ValueError("typed DART 비교 후보의 문서 신원이 올바르지 않습니다")
        source = seal_collected_source(
            Source(
                number=next_candidate_number,
                kind=SourceKind.FILING,
                label=item.title,
                disclosed_at=_source_date(
                    item.published_on,
                    source_kind=item.source_kind,
                    source_url=item.canonical_url,
                    collected_on=item.collected_at,
                ),
                collected_at=normalize_official_source_date(item.collected_at),
                source_id=f"typed-comparison-candidate-{next_candidate_number}",
                title=item.title,
                publisher=company_name,
                host=host,
                url=item.canonical_url,
                document_id=receipt,
                location=item.location,
                source_type="공식 공시",
                fact_status="공시 실제값",
                evidence_hashes=[evidence_text_hash(item.evidence_text)],
                exact_evidence_hashes=[exact_hash],
                document_content_sha256=item.document_content_sha256,
                formal_source_kind=item.source_kind,
                identity_binding=item.identity_binding,
            )
        )
        sources.append(source)
        comparison_rows.append(
            OfficialCandidateSentence(
                source=source,
                evidence_text=item.evidence_text,
                document_identity=document_identity,
                document_content_sha256=item.document_content_sha256,
            )
        )
        existing_candidate_keys.add((document_identity, exact_hash))
        next_candidate_number += 1

    reference_date = normalize_official_source_date(collected_on)
    registry = ensure_dart_profile_attesters(sources, company_name=company_name)
    # 공식 웹 조각이 하나도 없는 DART-only 수집에서도 비교 후보 판별은
    # 현재 요청의 OpenDART 기업개황 신원을 필요로 한다. 예전 시험은 이
    # attester를 손으로 주입해 생산 배선 누락을 숨겼다. 이미 한 번 읽어
    # 검증한 같은 profile snapshot에서만 자동 생성하고, 자식 Source가
    # 운반한 동일 ID가 있으면 중복하지 않는다.
    attestation_id, attestation_evidence = dart_profile_attestation_material(
        profile=profile,
        corp_code=corp_code,
        company_name=company_name,
    )
    if attestation_id and not any(
        source.source_id.strip() == attestation_id for source in registry
    ):
        registry = (
            *registry,
            build_dart_profile_attester_source(
                number=max((source.number for source in registry), default=0) + 1,
                source_id=attestation_id,
                evidence=attestation_evidence,
                company_name=company_name,
                collected_on=reference_date,
            ),
        )
    for source in registry:
        if source.formal_source_kind:
            problem = full_typed_source_registry_problem(
                source,
                registry,
                reference_date=reference_date,
            )
            if problem:
                raise ValueError(f"typed 비교 후보 Source 계약이 손상됐습니다: {problem}")
    rows = (*initial_rows, *comparison_rows)
    return registry, rows


__all__ = ["build_typed_comparison_candidate_inputs"]
