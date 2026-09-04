"""공식 근거 수집 구현과 보고서 파이프라인 사이의 실행 포트.

파이프라인은 DART·공식 웹 수집기의 내부 자료형을 알지 않는다. 조립 계층이
이 포트를 구현해 정책 순서의 아홉 장 후보와 원문 없는 출처 snapshot만
돌려준다.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, Protocol

from src.shared.company_identity import (
    exact_company_name_key,
    normalize_korean_registration_number,
)
from src.shared.report_evidence.constants import (
    FORMAL_DOCUMENT_SOURCE_KINDS,
    SourceRequirement,
    SourceTier,
)
from src.shared.report_evidence.models import ChapterEvidenceCandidates
from src.shared.report_evidence.policy import REQUIRED_EVIDENCE_SECTION_IDS
from src.shared.report_evidence.profile_domain_attestation import (
    parse_dart_profile_domain_attestation,
)
from src.shared.report_evidence.source_kind_policy import (
    formal_document_writer_ineligibility_reason,
    validate_formal_candidate_sources,
)


SOURCE_SNAPSHOT_VERSION: Final[str] = "official-evidence-source-snapshot-v4"
PROVENANCE_AUDIT_SNAPSHOT_VERSION: Final[str] = (
    "official-evidence-provenance-audit-v1"
)
_SHA256_HEX_CHARS: Final[frozenset[str]] = frozenset("0123456789abcdef")

GetJsonCallable = Callable[[str, dict[str, object], object], object]


class DownloadDocumentCallable(Protocol):
    """FULL DART artifact 생산 함수의 엄격 sidecar 호출 계약.

    단순 3인자 ``Callable``로 적으면 수집기가 실제로 요구하는 keyword가
    타입 계약에서 사라져, 조립부 wrapper가 실행 중에야 TypeError를 낸다.
    """

    def __call__(
        self,
        rcept_no: str,
        dest_dir: Path,
        counter: object,
        *,
        require_official_url_sidecar: bool = False,
    ) -> Path: ...


def _required_text(value: object, *, label: str) -> str:
    clean = str(value or "").strip()
    if not clean:
        raise ValueError(f"{label}은(는) 비워 둘 수 없습니다")
    return clean


def _canonical_json(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


@dataclass(frozen=True)
class OfficialEvidenceCollectionRequest:
    """기존 요청의 무료 DART 경계와 공식 회사 정보를 함께 나르는 입력."""

    company_id: str
    company_name: str
    company_aliases: tuple[str, ...]
    root_homepage_url: str
    company_registration_numbers: tuple[str, ...]
    official_candidate_urls: tuple[str, ...]
    as_of_date: dt.date
    dart_document_cache_dir: Path
    dart_counter: object
    dart_get_json: GetJsonCallable
    dart_download_document: DownloadDocumentCallable
    #: 기존 DART 기업개황 Source와 typed 웹 문서가 공유하는 exact attestation.
    #: hm_url이 비면 둘 다 빈 문자열이다. 수집기가 새로 꾸며 내지 않는다.
    domain_attestation_source_id: str = ""
    domain_attestation_evidence: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "company_id",
            _required_text(self.company_id, label="회사 식별자"),
        )
        object.__setattr__(
            self,
            "company_name",
            _required_text(self.company_name, label="회사명"),
        )
        aliases = tuple(
            dict.fromkeys(
                clean
                for value in self.company_aliases
                if (clean := str(value or "").strip())
            )
        )
        object.__setattr__(self, "company_aliases", aliases)
        object.__setattr__(
            self,
            "root_homepage_url",
            str(self.root_homepage_url or "").strip(),
        )
        if not isinstance(self.company_registration_numbers, tuple):
            raise ValueError("공식 등록번호 목록은 tuple이어야 합니다")
        registration_numbers: list[str] = []
        for value in self.company_registration_numbers:
            normalized = normalize_korean_registration_number(value)
            if not normalized:
                raise ValueError("공식 등록번호는 10자리 또는 13자리 숫자여야 합니다")
            if normalized not in registration_numbers:
                registration_numbers.append(normalized)
        object.__setattr__(
            self,
            "company_registration_numbers",
            tuple(registration_numbers),
        )
        if not isinstance(self.official_candidate_urls, tuple):
            raise ValueError("공식 URL 후보 목록은 tuple이어야 합니다")
        candidate_urls = tuple(
            dict.fromkeys(
                clean
                for value in self.official_candidate_urls
                if (clean := str(value or "").strip())
            )
        )
        object.__setattr__(self, "official_candidate_urls", candidate_urls)
        if type(self.as_of_date) is not dt.date:
            raise ValueError("공식 근거 기준일은 date여야 합니다")
        try:
            cache_dir = Path(self.dart_document_cache_dir)
        except TypeError as error:
            raise ValueError("DART 문서 cache 경로가 올바르지 않습니다") from error
        if cache_dir.exists() and not cache_dir.is_dir():
            raise ValueError("DART 문서 cache 경로는 폴더여야 합니다")
        object.__setattr__(self, "dart_document_cache_dir", cache_dir)
        if self.dart_counter is None:
            raise ValueError("기존 요청의 DART 사용량 counter가 필요합니다")
        if not callable(self.dart_get_json):
            raise ValueError("DART JSON 조회 함수가 올바르지 않습니다")
        if not callable(self.dart_download_document):
            raise ValueError("DART 문서 다운로드 함수가 올바르지 않습니다")
        attestation_id = str(self.domain_attestation_source_id or "").strip()
        attestation_evidence = str(self.domain_attestation_evidence or "").strip()
        if bool(attestation_id) != bool(attestation_evidence):
            raise ValueError("DART 기업개황 Source ID와 exact 원문은 함께 있어야 합니다")
        if attestation_id:
            payload = parse_dart_profile_domain_attestation(attestation_evidence)
            expected_id = f"dart-company-profile-{self.company_id}"
            if (
                payload is None
                or payload.is_registered_subdomain
                or attestation_id != expected_id
                or payload.corp_code != self.company_id
                or exact_company_name_key(payload.corp_name)
                != exact_company_name_key(self.company_name)
                or payload.hm_url != self.root_homepage_url
            ):
                raise ValueError("DART 기업개황 attestation이 현재 회사 요청과 다릅니다")
        object.__setattr__(self, "domain_attestation_source_id", attestation_id)
        object.__setattr__(self, "domain_attestation_evidence", attestation_evidence)

    @property
    def collected_at(self) -> str:
        """수집기 시계에 쓰는 결정론적 날짜 문자열."""

        return self.as_of_date.isoformat()


def _source_snapshot(
    company_id: str,
    candidates: tuple[ChapterEvidenceCandidates, ...],
    unclassified_evidence: UnclassifiedEvidenceObservation | None,
    comparison_candidates: tuple[OfficialComparisonCandidateEvidence, ...],
) -> tuple[str, int]:
    """선택·게이트·공개 출처에 영향을 주는 입력의 canonical 지문을 만든다.

    원문은 ``content_sha256``·``text_sha256``으로 결속하고 snapshot payload에
    다시 싣지 않는다. ``collected_at``과 attempt의 시간·바이트 계수는 같은
    자료를 언제/얼마 만에 받았는지만 나타내는 운영 관측치라 의도적으로 뺀다.
    이 값까지 넣으면 자료가 그대로여도 날짜가 바뀔 때마다 캐시가 무효화된다.
    반면 제목·발행일·출처종류·위치·선택점수·필수등급처럼 보고서 표시, 선택,
    게이트 중 하나라도 바꾸는 필드는 모두 넣는다.
    """

    documents_by_id: dict[str, dict[str, object]] = {}
    documents_by_url: dict[str, tuple[str, str]] = {}
    fragment_rows_by_id: dict[str, dict[str, object]] = {}
    attempt_rows_by_id: dict[str, dict[str, object]] = {}
    candidate_rows_by_section: dict[str, dict[str, object]] = {}

    for candidate in candidates:
        candidate_rows_by_section[candidate.section_id] = {
            "section_id": candidate.section_id,
            "candidate_readiness": candidate.candidate_readiness.value,
            "reason_codes": sorted(candidate.reason_codes),
            "estimated_tokens": candidate.estimated_tokens,
            "max_chars": candidate.max_chars,
            "max_estimated_tokens": candidate.max_estimated_tokens,
        }
        for document in candidate.documents:
            document_fingerprint: dict[str, object] = {
                "document_id": document.document_id,
                "canonical_url": document.canonical_url,
                "source_tier": document.source_tier.value,
                "source_kind": document.source_kind,
                "publisher": document.publisher,
                "title": document.title,
                "published_on": document.published_on,
                "content_sha256": document.content_sha256,
                "exact_evidence_hashes": sorted(document.exact_evidence_hashes),
                "identity_binding": document.identity_binding,
                "domain_attestation_source_id": (
                    document.domain_attestation_source_id
                ),
                "domain_attestation_evidence": (
                    document.domain_attestation_evidence
                ),
                "reporting_period": document.reporting_period,
                "attachment_url": document.attachment_url,
                "ir_metadata_verification": document.ir_metadata_verification,
                "domain_redirect_verification": (
                    document.domain_redirect_verification
                ),
                "domain_redirect_from_host": document.domain_redirect_from_host,
                "domain_redirect_to_host": document.domain_redirect_to_host,
                "usable_ranges": [
                    {"start": item.start, "end": item.end}
                    for item in document.usable_ranges
                ],
                "collector_version": document.collector_version,
                "parser_version": document.parser_version,
                "requirement": document.requirement.value,
            }
            previous = documents_by_id.setdefault(
                document.document_id,
                document_fingerprint,
            )
            if previous != document_fingerprint:
                raise ValueError("같은 문서 식별자가 서로 다른 공식 문서를 가리킵니다")
            url_fingerprint = (document.document_id, document.content_sha256)
            previous_url = documents_by_url.setdefault(
                document.canonical_url,
                url_fingerprint,
            )
            if previous_url != url_fingerprint:
                raise ValueError("같은 정규 URL이 서로 다른 공식 문서를 가리킵니다")

        for fragment in candidate.fragments:
            fragment_row: dict[str, object] = {
                "fragment_id": fragment.fragment_id,
                "document_id": fragment.document_id,
                "location": fragment.location,
                "text_sha256": fragment.text_sha256,
                "section_id": fragment.section_id,
                "slot_id": fragment.slot_id,
                "covered_slot_ids": sorted(fragment.covered_slot_ids),
                "score_millis": fragment.score_millis,
                "reason_codes": sorted(fragment.reason_codes),
                "period_start": fragment.period_start,
                "period_end": fragment.period_end,
                "unit": fragment.unit,
                "company_scope": fragment.company_scope,
            }
            previous_fragment = fragment_rows_by_id.setdefault(
                fragment.fragment_id,
                fragment_row,
            )
            if previous_fragment != fragment_row:
                raise ValueError("같은 근거 조각 식별자가 서로 다른 내용을 가리킵니다")
        for attempt in candidate.attempts:
            attempt_row: dict[str, object] = {
                "attempt_id": attempt.attempt_id,
                "source_kind": attempt.source_kind,
                "requirement": attempt.requirement.value,
                "state": attempt.state.value,
                "slot_ids": sorted(attempt.slot_ids),
                "reason_code": attempt.reason_code,
            }
            previous_attempt = attempt_rows_by_id.setdefault(
                attempt.attempt_id,
                attempt_row,
            )
            if previous_attempt != attempt_row:
                raise ValueError("같은 수집 시도 식별자가 서로 다른 결과를 가리킵니다")

    document_rows = [documents_by_id[key] for key in sorted(documents_by_id)]
    fragments = [fragment_rows_by_id[key] for key in sorted(fragment_rows_by_id)]
    attempts = [attempt_rows_by_id[key] for key in sorted(attempt_rows_by_id)]
    payload = {
        "version": SOURCE_SNAPSHOT_VERSION,
        "company_id": company_id,
        "candidates": [
            candidate_rows_by_section[section_id]
            for section_id in REQUIRED_EVIDENCE_SECTION_IDS
        ],
        "documents": document_rows,
        "fragments": fragments,
        "attempts": attempts,
        # 분류하지 못한 원문 자체는 앱 경계로 가져오지 않는다. 다만 그 원문이
        # 바뀌면 같은 생성 cache를 재사용하지 않도록, 수집 adapter가 검증해
        # 만든 개수와 지문만 snapshot 정본에 포함한다.
        "unclassified_evidence": (
            None
            if unclassified_evidence is None
            else {
                "document_count": unclassified_evidence.document_count,
                "fragment_count": unclassified_evidence.fragment_count,
                "observation_sha256": (
                    unclassified_evidence.observation_sha256
                ),
            }
        ),
        # 이 차선은 1~8장 writer 입력이 아니다. 공식 원문에서 비교 표현만
        # 뽑아 9장 비교 생산기에 넘기는 typed 후보이며, 실제 비교 대상·수치
        # 근거 확정은 뒤 생산기가 별도로 수행한다.
        "comparison_candidates": [
            {
                "candidate_id": item.candidate_id,
                "document_id": item.document_id,
                "canonical_url": item.canonical_url,
                "source_tier": item.source_tier.value,
                "source_kind": item.source_kind,
                "publisher": item.publisher,
                "title": item.title,
                "published_on": item.published_on,
                "document_content_sha256": item.document_content_sha256,
                "identity_binding": item.identity_binding,
                "collector_version": item.collector_version,
                "parser_version": item.parser_version,
                "requirement": item.requirement.value,
                "location": item.location,
                "evidence_sha256": item.evidence_sha256,
            }
            for item in comparison_candidates
        ],
    }
    # 독립 문서는 호출자가 붙인 document_id 개수가 아니라 실제 원문 바이트의
    # 고유 지문으로 센다. 같은 파일을 URL·ID만 바꿔 여덟 번 싣는다고 서로
    # 독립된 여덟 출처가 되지 않는다. 반대로 내용이 다른 문서는 같은 발행자의
    # 같은 문서군이어도 별개의 원문이므로 각각 센다.
    independent_content_hashes = {
        str(document["content_sha256"])
        for document in documents_by_id.values()
    }
    independent_content_hashes.update(
        item.document_content_sha256 for item in comparison_candidates
    )
    return (
        hashlib.sha256(_canonical_json(payload)).hexdigest(),
        len(independent_content_hashes),
    )


@dataclass(frozen=True)
class UnclassifiedEvidenceObservation:
    """분류 실패 원문을 노출하지 않고 존재와 변경만 증명하는 관측값.

    이 자료형에는 본문·URL·문서 제목이 들어갈 자리가 없다. 따라서 뒤 단계는
    무분류 문장을 주장 근거나 writer 입력으로 쓸 수 없고, 현재 분류기의 범위
    밖 원문이 실제로 있었다는 사실만 선결제 진단과 cache 지문에 반영한다.
    """

    company_id: str
    document_count: int
    fragment_count: int
    observation_sha256: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "company_id",
            _required_text(self.company_id, label="무분류 관측 회사 식별자"),
        )
        for value, label in (
            (self.document_count, "무분류 문서 수"),
            (self.fragment_count, "무분류 조각 수"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{label}는 1 이상의 정수여야 합니다")
        digest = str(self.observation_sha256 or "").strip()
        if len(digest) != 64 or not set(digest) <= _SHA256_HEX_CHARS:
            raise ValueError("무분류 관측 지문은 소문자 SHA-256이어야 합니다")
        object.__setattr__(self, "observation_sha256", digest)


@dataclass(frozen=True)
class OfficialComparisonCandidateEvidence:
    """글쓰기 슬롯과 분리해 보존한 공식 비교 후보 원문 한 문장.

    이 자료형에는 장·슬롯 필드가 없다. 따라서 후보 문장을 1~8장 근거로
    가장할 수 없고, 비교 생산기는 회사 카탈로그·양사 동일 지표를 다시
    확인한 뒤에만 9장 프로그램 사실로 승격한다.
    """

    company_id: str
    candidate_id: str
    document_id: str
    canonical_url: str
    source_tier: SourceTier
    source_kind: str
    publisher: str
    title: str
    published_on: str
    collected_at: str
    document_content_sha256: str
    identity_binding: str
    collector_version: str
    parser_version: str
    requirement: SourceRequirement
    location: str
    evidence_text: str
    evidence_sha256: str

    def __post_init__(self) -> None:
        for value, label in (
            (self.company_id, "비교 후보 회사 식별자"),
            (self.candidate_id, "비교 후보 식별자"),
            (self.document_id, "비교 후보 문서 식별자"),
            (self.canonical_url, "비교 후보 정규 URL"),
            (self.source_kind, "비교 후보 출처 종류"),
            (self.publisher, "비교 후보 발행자"),
            (self.title, "비교 후보 문서 제목"),
            (self.collected_at, "비교 후보 수집 시각"),
            (self.identity_binding, "비교 후보 회사 결속"),
            (self.collector_version, "비교 후보 수집기 버전"),
            (self.parser_version, "비교 후보 파서 버전"),
            (self.location, "비교 후보 원문 위치"),
            (self.evidence_text, "비교 후보 원문"),
        ):
            _required_text(value, label=label)
        if not isinstance(self.source_tier, SourceTier):
            raise ValueError("비교 후보 출처 등급이 올바르지 않습니다")
        if not isinstance(self.requirement, SourceRequirement):
            raise ValueError("비교 후보 출처 요구도가 올바르지 않습니다")
        document_digest = str(self.document_content_sha256 or "").strip()
        evidence_digest = str(self.evidence_sha256 or "").strip()
        for digest, label in (
            (document_digest, "비교 후보 문서"),
            (evidence_digest, "비교 후보 원문"),
        ):
            if len(digest) != 64 or not set(digest) <= _SHA256_HEX_CHARS:
                raise ValueError(f"{label} 지문은 소문자 SHA-256이어야 합니다")
        if hashlib.sha256(self.evidence_text.encode("utf-8")).hexdigest() != evidence_digest:
            raise ValueError("비교 후보 원문과 지문이 다릅니다")
        object.__setattr__(self, "document_content_sha256", document_digest)
        object.__setattr__(self, "evidence_sha256", evidence_digest)


@dataclass(frozen=True)
class OfficialProvenanceDocument:
    """수집했지만 Writer·게이트·독립문서 수에는 쓰지 않는 formal 문서.

    본문은 싣지 않고 exact 문서 지문과 provenance만 보존한다. 나중에 정책이
    바뀌어 Writer 자격이 생기면 새 수집/정본 버전으로 다시 판정해야 하며,
    이 객체를 후보 문서로 자동 승격하지 않는다.
    """

    company_id: str
    document_id: str
    canonical_url: str
    source_tier: SourceTier
    source_kind: str
    publisher: str
    title: str
    published_on: str
    collected_at: str
    content_sha256: str
    identity_binding: str
    collector_version: str
    parser_version: str
    requirement: SourceRequirement
    exclusion_reason: str
    domain_attestation_source_id: str = ""
    domain_attestation_evidence: str = ""
    reporting_period: str = ""
    attachment_url: str = ""
    ir_metadata_verification: str = ""
    domain_redirect_verification: str = ""
    domain_redirect_from_host: str = ""
    domain_redirect_to_host: str = ""

    def __post_init__(self) -> None:
        for value, label in (
            (self.company_id, "감사 문서 회사 식별자"),
            (self.document_id, "감사 문서 식별자"),
            (self.canonical_url, "감사 문서 정규 URL"),
            (self.source_kind, "감사 문서 출처 종류"),
            (self.publisher, "감사 문서 발행자"),
            (self.title, "감사 문서 제목"),
            (self.collected_at, "감사 문서 수집 시각"),
            (self.identity_binding, "감사 문서 회사 결속"),
            (self.collector_version, "감사 문서 수집기 버전"),
            (self.parser_version, "감사 문서 파서 버전"),
            (self.exclusion_reason, "감사 문서 Writer 제외 사유"),
        ):
            _required_text(value, label=label)
        if not isinstance(self.published_on, str):
            raise ValueError("감사 문서 발행일은 문자열이어야 합니다")
        if self.source_kind not in FORMAL_DOCUMENT_SOURCE_KINDS:
            raise ValueError("감사 문서 출처 종류가 formal 정본에 없습니다")
        if not isinstance(self.source_tier, SourceTier):
            raise ValueError("감사 문서 출처 등급이 올바르지 않습니다")
        if not isinstance(self.requirement, SourceRequirement):
            raise ValueError("감사 문서 필수 여부가 올바르지 않습니다")
        digest = str(self.content_sha256 or "").strip()
        if len(digest) != 64 or not set(digest) <= _SHA256_HEX_CHARS:
            raise ValueError("감사 문서 지문은 소문자 SHA-256이어야 합니다")
        object.__setattr__(self, "content_sha256", digest)
        optional_values = (
            self.domain_attestation_source_id,
            self.domain_attestation_evidence,
            self.reporting_period,
            self.attachment_url,
            self.ir_metadata_verification,
            self.domain_redirect_verification,
            self.domain_redirect_from_host,
            self.domain_redirect_to_host,
        )
        if any(not isinstance(value, str) for value in optional_values):
            raise ValueError("감사 문서 provenance 필드는 문자열이어야 합니다")
        if bool(self.domain_attestation_source_id.strip()) != bool(
            self.domain_attestation_evidence.strip()
        ):
            raise ValueError("감사 문서 attestation ID와 exact 원문은 함께 있어야 합니다")
        redirect_parts = (
            self.domain_redirect_verification.strip(),
            self.domain_redirect_from_host.strip(),
            self.domain_redirect_to_host.strip(),
        )
        if any(redirect_parts) and not all(redirect_parts):
            raise ValueError("감사 문서 redirect proof 세 필드는 함께 있어야 합니다")
        writer_reason = formal_document_writer_ineligibility_reason(self)
        expected_exclusion = (
            f"writer_ineligible:{writer_reason}"
            if writer_reason
            else "no_exact_evidence"
        )
        if self.exclusion_reason != expected_exclusion:
            raise ValueError("감사 문서 Writer 제외 사유가 정본 판정과 다릅니다")


def _provenance_audit_snapshot(
    company_id: str,
    documents: tuple[OfficialProvenanceDocument, ...],
) -> str:
    """generation cache와 분리된 원문 없는 감사 지문."""

    payload = {
        "version": PROVENANCE_AUDIT_SNAPSHOT_VERSION,
        "company_id": company_id,
        "documents": [
            {
                "document_id": item.document_id,
                "canonical_url": item.canonical_url,
                "source_tier": item.source_tier.value,
                "source_kind": item.source_kind,
                "publisher": item.publisher,
                "title": item.title,
                "published_on": item.published_on,
                "content_sha256": item.content_sha256,
                "identity_binding": item.identity_binding,
                "collector_version": item.collector_version,
                "parser_version": item.parser_version,
                "requirement": item.requirement.value,
                "exclusion_reason": item.exclusion_reason,
                "domain_attestation_source_id": item.domain_attestation_source_id,
                "domain_attestation_evidence": item.domain_attestation_evidence,
                "reporting_period": item.reporting_period,
                "attachment_url": item.attachment_url,
                "ir_metadata_verification": item.ir_metadata_verification,
                "domain_redirect_verification": item.domain_redirect_verification,
                "domain_redirect_from_host": item.domain_redirect_from_host,
                "domain_redirect_to_host": item.domain_redirect_to_host,
            }
            for item in sorted(
                documents, key=lambda value: (value.document_id, value.canonical_url)
            )
        ],
    }
    return hashlib.sha256(_canonical_json(payload)).hexdigest()


@dataclass(frozen=True)
class OfficialEvidenceCollectionResult:
    """회사에 결속된 아홉 장 후보와 재현 가능한 출처 snapshot."""

    company_id: str
    candidates: tuple[ChapterEvidenceCandidates, ...]
    unclassified_evidence: UnclassifiedEvidenceObservation | None = None
    comparison_candidates: tuple[OfficialComparisonCandidateEvidence, ...] = ()
    provenance_documents: tuple[OfficialProvenanceDocument, ...] = ()
    source_snapshot_sha256: str = field(init=False)
    provenance_snapshot_sha256: str = field(init=False)
    independent_document_count: int = field(init=False)

    def __post_init__(self) -> None:
        clean_company_id = _required_text(self.company_id, label="회사 식별자")
        object.__setattr__(self, "company_id", clean_company_id)
        candidates = tuple(self.candidates)
        if any(
            not isinstance(candidate, ChapterEvidenceCandidates)
            for candidate in candidates
        ):
            raise ValueError("공식 근거 결과에는 장별 후보만 넣을 수 있습니다")
        object.__setattr__(self, "candidates", candidates)
        section_ids = tuple(candidate.section_id for candidate in candidates)
        if section_ids != REQUIRED_EVIDENCE_SECTION_IDS:
            raise ValueError("공식 근거 결과는 정책 순서의 아홉 장 후보를 모두 담아야 합니다")
        if any(candidate.company_id != clean_company_id for candidate in candidates):
            raise ValueError("다른 회사의 장 후보를 공식 근거 결과에 섞을 수 없습니다")
        observation = self.unclassified_evidence
        if observation is not None:
            if not isinstance(observation, UnclassifiedEvidenceObservation):
                raise ValueError("무분류 근거 관측값의 자료형이 올바르지 않습니다")
            if observation.company_id != clean_company_id:
                raise ValueError("다른 회사의 무분류 근거 관측값을 섞을 수 없습니다")
        comparison_candidates = tuple(self.comparison_candidates)
        if any(
            not isinstance(item, OfficialComparisonCandidateEvidence)
            for item in comparison_candidates
        ):
            raise ValueError("공식 비교 후보의 자료형이 올바르지 않습니다")
        if any(item.company_id != clean_company_id for item in comparison_candidates):
            raise ValueError("다른 회사의 공식 비교 후보를 섞을 수 없습니다")
        candidate_ids = tuple(item.candidate_id for item in comparison_candidates)
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("공식 비교 후보 식별자가 중복됩니다")
        object.__setattr__(self, "comparison_candidates", comparison_candidates)
        provenance_documents = tuple(self.provenance_documents)
        if any(
            not isinstance(item, OfficialProvenanceDocument)
            for item in provenance_documents
        ):
            raise ValueError("provenance-only 문서 자료형이 올바르지 않습니다")
        if any(item.company_id != clean_company_id for item in provenance_documents):
            raise ValueError("다른 회사의 provenance-only 문서를 섞을 수 없습니다")
        provenance_ids = tuple(item.document_id for item in provenance_documents)
        provenance_urls = tuple(item.canonical_url for item in provenance_documents)
        if len(provenance_ids) != len(set(provenance_ids)):
            raise ValueError("provenance-only 문서 식별자가 중복됩니다")
        if len(provenance_urls) != len(set(provenance_urls)):
            raise ValueError("provenance-only 문서 URL이 중복됩니다")
        writer_document_ids = {
            document.document_id
            for candidate in candidates
            for document in candidate.documents
        }
        if writer_document_ids & set(provenance_ids):
            raise ValueError("같은 문서를 Writer와 provenance-only 차선에 함께 넣을 수 없습니다")
        object.__setattr__(self, "provenance_documents", provenance_documents)
        validate_formal_candidate_sources(candidates)

        snapshot_sha256, document_count = _source_snapshot(
            clean_company_id,
            candidates,
            observation,
            comparison_candidates,
        )
        object.__setattr__(self, "source_snapshot_sha256", snapshot_sha256)
        object.__setattr__(
            self,
            "provenance_snapshot_sha256",
            _provenance_audit_snapshot(clean_company_id, provenance_documents),
        )
        object.__setattr__(self, "independent_document_count", document_count)


class OfficialEvidenceCollector(Protocol):
    """무료 공식 근거를 타입화된 장 후보로 만드는 실서비스 포트."""

    def collect(
        self,
        request: OfficialEvidenceCollectionRequest,
    ) -> OfficialEvidenceCollectionResult:
        """외부 I/O 구현을 숨기고 검증된 후보와 snapshot만 반환한다."""
