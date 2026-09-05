"""typed DART와 공식 웹 수집을 한 번의 장 근거 생산 경계로 합성한다."""

from __future__ import annotations

import hashlib
import importlib
import json
import re
import sys
import threading
import urllib.parse
from collections.abc import Mapping
from pathlib import Path
from types import ModuleType
from typing import Final

from src.core import paths
from src.core.evidence_reclassify_switch import evidence_reclassify_enabled
from src.features.chapter_evidence.produce import produce_from_collection_envelopes
from src.features.homepage.wide_collect import collect_official_web_documents
from src.features.homepage.wide_evidence_mapping import to_evidence_mappings
from src.features.homepage.wide_fragments import build_fragments_for_collection
from src.features.pipeline.evidence_reclassify_step import attach_reclassify_source
from src.shared.report_evidence.runtime_port import (
    OfficialComparisonCandidateEvidence,
    OfficialEvidenceCollectionRequest,
    OfficialEvidenceCollectionResult,
    OfficialProvenanceDocument,
    UnclassifiedEvidenceObservation,
)
from src.shared.report_evidence.constants import SourceRequirement, SourceTier
from src.shared.report_evidence.constants import (
    SOURCE_KIND_DART_AUDIT_REPORT,
    SOURCE_KIND_DART_BUSINESS_REPORT,
    SOURCE_KIND_DART_QUARTERLY_REPORT,
    SOURCE_KIND_DART_SEMIANNUAL_REPORT,
    SOURCE_KIND_OFFICIAL_IDENTITY_VERIFIED_WEB_PAGE,
    SOURCE_KIND_OFFICIAL_IR_PDF,
    SOURCE_KIND_OFFICIAL_RECRUIT_PAGE,
    SOURCE_KIND_OFFICIAL_WEB_PAGE,
)
from src.shared.report_evidence.identity_verified_web import (
    build_dart_filing_url_provenance,
    is_canonical_dart_candidate_url as _is_canonical_dart_candidate_url,
)
from src.shared.comparison_candidate_basis import (
    comparison_evidence_sentences,
    comparison_source_sentence_has_marker,
)


_ENGINE_MODULE_NAMES: Final[tuple[str, ...]] = (
    "collect",
    "dart_fetcher",
    "serialize",
)
_ENGINE_IMPORT_LOCK = threading.Lock()
_SHA256_HEX_RE = re.compile(r"^[0-9a-f]{64}$")
_DART_RECEIPT_NUMBER_RE = re.compile(r"^[0-9]{14}$")
_DART_FRAGMENT_LOCATION_RE = re.compile(r"^([0-9]{1,10})-([0-9]{1,10})$")
_DART_DOCUMENT_LOCATION_MAX_CHARS: Final[int] = 8 * 1024 * 1024
_DART_DOCUMENT_SOURCE_KINDS: Final[frozenset[str]] = frozenset(
    {
        SOURCE_KIND_DART_AUDIT_REPORT,
        SOURCE_KIND_DART_BUSINESS_REPORT,
        SOURCE_KIND_DART_QUARTERLY_REPORT,
        SOURCE_KIND_DART_SEMIANNUAL_REPORT,
    }
)
_DART_DOCUMENT_REQUIREMENT_BY_SOURCE_KIND: Final[dict[str, str]] = {
    SOURCE_KIND_DART_BUSINESS_REPORT: SourceRequirement.REQUIRED.value,
    SOURCE_KIND_DART_AUDIT_REPORT: SourceRequirement.REQUIRED.value,
    SOURCE_KIND_DART_SEMIANNUAL_REPORT: SourceRequirement.OPTIONAL.value,
    SOURCE_KIND_DART_QUARTERLY_REPORT: SourceRequirement.OPTIONAL.value,
}
_DART_IDENTITY_CHECK_STATES: Final[frozenset[str]] = frozenset(
    {"verified_match", "unverifiable_no_fetcher_metadata"}
)
_OFFICIAL_WEB_DOCUMENT_SOURCE_KINDS: Final[frozenset[str]] = frozenset(
    {
        SOURCE_KIND_OFFICIAL_WEB_PAGE,
        SOURCE_KIND_OFFICIAL_RECRUIT_PAGE,
        SOURCE_KIND_OFFICIAL_IR_PDF,
        SOURCE_KIND_OFFICIAL_IDENTITY_VERIFIED_WEB_PAGE,
    }
)
_UNCLASSIFIED_OBSERVATION_VERSION: Final[str] = (
    "dart-unclassified-evidence-observation-v1"
)


def _paragraph_has_comparison_candidate(text: str) -> bool:
    """한 문단의 서로 다른 문장이 서로의 부정·인용 문맥을 오염시키지 않게 한다."""

    return any(
        comparison_source_sentence_has_marker(sentence)
        for sentence in comparison_evidence_sentences(text)
    )


def _classified_evidence_location_bindings(
    envelope: Mapping[str, object],
    *,
    company_id: str,
) -> None:
    """일반 Writer 조각의 location↔hash↔usable_range 결속을 재검증한다.

    ``exact_evidence_hashes``만 검사하면 진짜 원문 hash를 그대로 둔 채 위치만
    같은 문서의 다른 구간이나 임의 문자열로 바꿀 수 있다. 생산기가 함께
    만든 ``exact_evidence_bindings``를 exact 비교하고, DART offset 또는 공식
    웹 ``URL#index``가 실제 usable range를 가리키며 길이도 같은지 확인한다.
    이 결속은 내부 배선 검증이지 원문 진위를 증명하는 전자서명은 아니다.
    """

    raw_documents = envelope.get("documents", ())
    raw_fragments = envelope.get("fragments", ())
    if not isinstance(raw_documents, (list, tuple)) or not isinstance(
        raw_fragments, (list, tuple)
    ):
        raise ValueError("typed 공식 근거 문서·조각 배열 형식이 올바르지 않습니다")

    documents: dict[str, dict[str, object]] = {}
    for raw_document in raw_documents:
        if not isinstance(raw_document, Mapping):
            raise ValueError("typed 공식 근거 문서가 Mapping이 아닙니다")
        document_id = str(raw_document.get("document_id") or "").strip()
        observed_company_id = str(raw_document.get("company_id") or "").strip()
        source_kind = str(raw_document.get("source_kind") or "").strip()
        canonical_url = str(raw_document.get("canonical_url") or "").strip()
        raw_ranges = raw_document.get("usable_ranges")
        raw_hashes = raw_document.get("exact_evidence_hashes")
        raw_bindings = raw_document.get("exact_evidence_bindings")
        if (
            observed_company_id != company_id
            or not document_id
            or document_id in documents
            or source_kind
            not in (_DART_DOCUMENT_SOURCE_KINDS | _OFFICIAL_WEB_DOCUMENT_SOURCE_KINDS)
            or not canonical_url
            or not isinstance(raw_ranges, (list, tuple))
            or not raw_ranges
            or not isinstance(raw_hashes, (list, tuple))
            or not raw_hashes
            or not isinstance(raw_bindings, (list, tuple))
            or not raw_bindings
        ):
            raise ValueError("typed 공식 근거 문서 위치 계약이 올바르지 않습니다")

        ranges: list[tuple[int, int]] = []
        for raw_range in raw_ranges:
            if not isinstance(raw_range, Mapping):
                raise ValueError("typed 공식 근거 usable range 형식이 올바르지 않습니다")
            start = raw_range.get("start")
            end = raw_range.get("end")
            if (
                isinstance(start, bool)
                or isinstance(end, bool)
                or not isinstance(start, int)
                or not isinstance(end, int)
                or start < 0
                or end <= start
                or (ranges and start < ranges[-1][1])
            ):
                raise ValueError("typed 공식 근거 usable range가 손상됐습니다")
            ranges.append((start, end))

        hashes = tuple(str(value).strip() for value in raw_hashes)
        if (
            len(hashes) != len(set(hashes))
            or any(_SHA256_HEX_RE.fullmatch(value) is None for value in hashes)
        ):
            raise ValueError("typed 공식 근거 hash 허용목록이 손상됐습니다")
        declared_bindings: set[tuple[str, str]] = set()
        for raw_binding in raw_bindings:
            if (
                not isinstance(raw_binding, Mapping)
                or set(raw_binding) != {"location", "text_sha256"}
                or type(raw_binding.get("location")) is not str
                or type(raw_binding.get("text_sha256")) is not str
            ):
                raise ValueError("typed 공식 근거 위치 결속 형식이 올바르지 않습니다")
            binding = (
                str(raw_binding["location"]),
                str(raw_binding["text_sha256"]),
            )
            if (
                not binding[0]
                or _SHA256_HEX_RE.fullmatch(binding[1]) is None
                or binding in declared_bindings
            ):
                raise ValueError("typed 공식 근거 위치 결속이 비었거나 중복됐습니다")
            declared_bindings.add(binding)
        documents[document_id] = {
            "source_kind": source_kind,
            "canonical_url": canonical_url,
            "ranges": tuple(ranges),
            "hashes": frozenset(hashes),
            "declared_bindings": frozenset(declared_bindings),
        }

    actual_by_document: dict[str, set[tuple[str, str]]] = {
        document_id: set() for document_id in documents
    }
    for raw_fragment in raw_fragments:
        if not isinstance(raw_fragment, Mapping):
            raise ValueError("typed 공식 근거 조각이 Mapping이 아닙니다")
        document_id = str(raw_fragment.get("document_id") or "").strip()
        document = documents.get(document_id)
        location = raw_fragment.get("location")
        text_sha256 = raw_fragment.get("text_sha256")
        text = raw_fragment.get("text")
        if (
            document is None
            or type(location) is not str
            or type(text_sha256) is not str
            or type(text) is not str
            or not location
            or _SHA256_HEX_RE.fullmatch(text_sha256) is None
            or hashlib.sha256(text.encode("utf-8")).hexdigest() != text_sha256
            or text_sha256 not in document["hashes"]
        ):
            raise ValueError("typed 공식 근거 조각의 원문 결속이 올바르지 않습니다")
        ranges = document["ranges"]
        assert isinstance(ranges, tuple)
        source_kind = str(document["source_kind"])
        if source_kind in _DART_DOCUMENT_SOURCE_KINDS:
            matched = _DART_FRAGMENT_LOCATION_RE.fullmatch(location)
            if matched is None:
                raise ValueError("typed DART 근거 위치가 offset 형식이 아닙니다")
            target_range = tuple(int(value) for value in matched.groups())
            if target_range not in ranges or target_range[1] - target_range[0] != len(text):
                raise ValueError("typed DART 근거 위치가 usable range와 다릅니다")
        else:
            prefix, separator, raw_index = location.rpartition("#")
            if (
                not separator
                or prefix != document["canonical_url"]
                or re.fullmatch(r"[0-9]{1,10}", raw_index) is None
            ):
                raise ValueError("typed 공식 웹 근거 위치가 URL#index 형식이 아닙니다")
            index = int(raw_index)
            if index >= len(ranges) or ranges[index][1] - ranges[index][0] != len(text):
                raise ValueError("typed 공식 웹 근거 위치가 usable range와 다릅니다")
        actual_by_document[document_id].add((location, text_sha256))

    for document_id, document in documents.items():
        if actual_by_document[document_id] != set(document["declared_bindings"]):
            raise ValueError("typed 공식 근거 location↔hash 결속 목록이 일치하지 않습니다")


def _unclassified_evidence_observation(
    envelope: Mapping[str, object],
    *,
    company_id: str,
) -> UnclassifiedEvidenceObservation | None:
    """DART 무분류 원문을 검증한 뒤 개수와 지문만 앱 경계로 옮긴다.

    원문을 반환 자료형에 싣지 않으므로 writer나 로그가 이 차선을 근거로
    오인할 수 없다. 반면 hash로 결속된 원문이 실제로 있었다는 사실은
    preflight가 「자료 부족」과 「현재 분류기 범위 밖」을 구분할 수 있다.
    """

    raw_documents = envelope.get("unclassified_documents", ())
    raw_fragments = envelope.get("unclassified_fragments", ())
    if not isinstance(raw_documents, (list, tuple)) or not isinstance(
        raw_fragments, (list, tuple)
    ):
        raise ValueError("typed DART 무분류 관측 배열이 list/tuple이 아닙니다")
    if not raw_documents and not raw_fragments:
        return None
    if not raw_documents or not raw_fragments:
        raise ValueError("typed DART 무분류 문서와 조각은 함께 있어야 합니다")

    document_rows: list[dict[str, object]] = []
    document_ids: set[str] = set()
    ranges_by_document_id: dict[str, tuple[tuple[int, int], ...]] = {}
    for raw in raw_documents:
        if not isinstance(raw, Mapping):
            raise ValueError("typed DART 무분류 문서가 Mapping이 아닙니다")
        observed_company_id = str(raw.get("company_id") or "").strip()
        document_id = str(raw.get("document_id") or "").strip()
        content_sha256 = str(raw.get("content_sha256") or "").strip()
        canonical_url = str(raw.get("canonical_url") or "").strip()
        source_kind = str(raw.get("source_kind") or "").strip()
        source_tier = str(raw.get("source_tier") or "").strip()
        requirement = str(raw.get("requirement") or "").strip()
        identity_binding = str(raw.get("identity_binding") or "").strip()
        exact_hashes = raw.get("exact_evidence_hashes")
        raw_ranges = raw.get("usable_ranges")
        if observed_company_id != company_id:
            raise ValueError("typed DART 무분류 문서의 회사 식별자가 다릅니다")
        if (
            not document_id
            or document_id in document_ids
            or not canonical_url
            or not source_kind
        ):
            raise ValueError("typed DART 무분류 문서 식별자가 없거나 중복됩니다")
        if _SHA256_HEX_RE.fullmatch(content_sha256) is None:
            raise ValueError("typed DART 무분류 문서 hash가 올바르지 않습니다")
        receipt_number = document_id.rpartition(":")[2]
        try:
            parsed_url = urllib.parse.urlsplit(canonical_url)
            parsed_query = urllib.parse.parse_qs(
                parsed_url.query,
                keep_blank_values=True,
                strict_parsing=True,
            )
        except (TypeError, ValueError) as error:
            raise ValueError("typed DART 무분류 문서 URL이 올바르지 않습니다") from error
        if (
            source_kind not in _DART_DOCUMENT_SOURCE_KINDS
            or document_id != f"{source_kind}:{receipt_number}"
            or _DART_RECEIPT_NUMBER_RE.fullmatch(receipt_number) is None
            or source_tier != SourceTier.TIER_1_OFFICIAL.value
            or requirement
            != _DART_DOCUMENT_REQUIREMENT_BY_SOURCE_KIND.get(source_kind)
            or not _is_canonical_dart_candidate_url(canonical_url)
            or parsed_url.scheme != "https"
            or (parsed_url.hostname or "").casefold().rstrip(".")
            != "dart.fss.or.kr"
            or parsed_url.path != "/dsaf001/main.do"
            or parsed_query != {"rcpNo": [receipt_number]}
            or parsed_url.fragment
        ):
            raise ValueError("typed DART 무분류 문서 신원이 올바르지 않습니다")
        expected_identity_prefix = (
            f"corp_code={company_id};rcept_no={receipt_number};"
            f"source_kind={source_kind};identity_check="
        )
        if identity_binding not in {
            expected_identity_prefix + state
            for state in _DART_IDENTITY_CHECK_STATES
        }:
            raise ValueError("typed DART 무분류 문서의 회사 결속이 올바르지 않습니다")
        if not isinstance(raw_ranges, (list, tuple)) or not raw_ranges:
            raise ValueError("typed DART 무분류 문서의 원문 구간이 비었습니다")
        parsed_ranges: list[tuple[int, int]] = []
        for raw_range in raw_ranges:
            if not isinstance(raw_range, Mapping):
                raise ValueError("typed DART 무분류 문서 구간이 Mapping이 아닙니다")
            start = raw_range.get("start")
            end = raw_range.get("end")
            if (
                isinstance(start, bool)
                or isinstance(end, bool)
                or not isinstance(start, int)
                or not isinstance(end, int)
                or start < 0
                or end <= start
                or end > _DART_DOCUMENT_LOCATION_MAX_CHARS
                or (parsed_ranges and start < parsed_ranges[-1][1])
            ):
                raise ValueError("typed DART 무분류 문서 구간이 손상됐습니다")
            parsed_ranges.append((start, end))
        # 근거로 채택되지 않은 차선이므로 exact evidence 목록은 비어 있어야
        # 한다. 여기 값이 있으면 분류/무분류 생산 경계가 이미 섞인 것이다.
        if exact_hashes not in ([], ()):
            raise ValueError("typed DART 무분류 문서에 근거 hash를 넣을 수 없습니다")
        document_ids.add(document_id)
        ranges_by_document_id[document_id] = tuple(parsed_ranges)
        document_rows.append(
            {
                "document_id": document_id,
                "content_sha256": content_sha256,
                "canonical_url": canonical_url,
                "source_kind": source_kind,
            }
        )

    fragment_rows: list[dict[str, object]] = []
    fragment_ids: set[str] = set()
    used_ranges_by_document_id: dict[str, set[tuple[int, int]]] = {
        document_id: set() for document_id in document_ids
    }
    for raw in raw_fragments:
        if not isinstance(raw, Mapping):
            raise ValueError("typed DART 무분류 조각이 Mapping이 아닙니다")
        observed_company_id = str(raw.get("company_id") or "").strip()
        fragment_id = str(raw.get("fragment_id") or "").strip()
        document_id = str(raw.get("document_id") or "").strip()
        location = str(raw.get("location") or "").strip()
        text = raw.get("text")
        text_sha256 = str(raw.get("text_sha256") or "").strip()
        covered_slot_ids = raw.get("covered_slot_ids")
        score_millis = raw.get("score_millis")
        if observed_company_id != company_id:
            raise ValueError("typed DART 무분류 조각의 회사 식별자가 다릅니다")
        if not fragment_id or fragment_id in fragment_ids:
            raise ValueError("typed DART 무분류 조각 식별자가 없거나 중복됩니다")
        if document_id not in document_ids or not location:
            raise ValueError("typed DART 무분류 조각의 문서 결속이 올바르지 않습니다")
        if not isinstance(text, str) or not text:
            raise ValueError("typed DART 무분류 조각 원문이 비어 있습니다")
        if hashlib.sha256(text.encode("utf-8")).hexdigest() != text_sha256:
            raise ValueError("typed DART 무분류 조각 hash가 원문과 다릅니다")
        location_match = _DART_FRAGMENT_LOCATION_RE.fullmatch(location)
        if location_match is None:
            raise ValueError("typed DART 무분류 조각 위치가 올바르지 않습니다")
        location_range = tuple(int(value) for value in location_match.groups())
        if (
            location_range not in ranges_by_document_id[document_id]
            or location_range in used_ranges_by_document_id[document_id]
            or location_range[1] - location_range[0] != len(text)
        ):
            raise ValueError("typed DART 무분류 조각이 문서 원문 구간과 다릅니다")
        used_ranges_by_document_id[document_id].add(location_range)
        if (
            str(raw.get("section_id") or "").strip()
            or str(raw.get("slot_id") or "").strip()
            or covered_slot_ids not in ([], ())
            or isinstance(score_millis, bool)
            or score_millis != 0
        ):
            raise ValueError("typed DART 무분류 조각에 주장 의미를 붙일 수 없습니다")
        reason_codes = raw.get("reason_codes")
        if (
            not isinstance(reason_codes, (list, tuple))
            or not reason_codes
            or any(not isinstance(code, str) or not code.strip() for code in reason_codes)
        ):
            raise ValueError("typed DART 무분류 조각의 관측 사유가 없습니다")
        fragment_ids.add(fragment_id)
        fragment_rows.append(
            {
                "fragment_id": fragment_id,
                "document_id": document_id,
                "location": location,
                "text_sha256": text_sha256,
                "reason_codes": sorted(code.strip() for code in reason_codes),
            }
        )

    if any(
        used_ranges_by_document_id[document_id]
        != set(ranges_by_document_id[document_id])
        for document_id in document_ids
    ):
        raise ValueError("typed DART 무분류 문서 구간과 조각이 1:1이 아닙니다")

    payload = {
        "version": _UNCLASSIFIED_OBSERVATION_VERSION,
        "company_id": company_id,
        "documents": sorted(document_rows, key=lambda row: str(row["document_id"])),
        "fragments": sorted(fragment_rows, key=lambda row: str(row["fragment_id"])),
    }
    digest = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return UnclassifiedEvidenceObservation(
        company_id=company_id,
        document_count=len(document_rows),
        fragment_count=len(fragment_rows),
        observation_sha256=digest,
    )


def _comparison_candidate_evidence(
    envelope: Mapping[str, object],
    *,
    company_id: str,
) -> tuple[OfficialComparisonCandidateEvidence, ...]:
    """무분류 원문 중 비교 표현만 별도 typed 후보 차선으로 보존한다.

    여기서는 경쟁사를 확정하거나 장 슬롯을 채우지 않는다. 기존 공용 비교
    문장 판별기가 찾은 정확 문장과 원본 문서 신원만 옮기며, 실제 DART 법인
    대상·양사 동일 지표·수치 판단은 comparison producer가 다시 검증한다.
    """

    # 문서/조각 회사 결속, 원문 hash, 빈 장·슬롯 계약은 관측 경계 한 곳을
    # 그대로 재사용한다. 서로 다른 검증기로 같은 원문을 해석하지 않는다.
    observation = _unclassified_evidence_observation(
        envelope,
        company_id=company_id,
    )
    if observation is None:
        return ()
    raw_documents = envelope.get("unclassified_documents", ())
    raw_fragments = envelope.get("unclassified_fragments", ())
    assert isinstance(raw_documents, (list, tuple))
    assert isinstance(raw_fragments, (list, tuple))
    documents = {
        str(raw.get("document_id") or "").strip(): raw
        for raw in raw_documents
        if isinstance(raw, Mapping)
    }
    candidates: list[OfficialComparisonCandidateEvidence] = []
    for raw_fragment in raw_fragments:
        if not isinstance(raw_fragment, Mapping):
            # 관측 검증이 이미 같은 값을 거절하므로 방어상 도달하지 않는다.
            raise ValueError("typed DART 비교 후보 조각이 Mapping이 아닙니다")
        document_id = str(raw_fragment.get("document_id") or "").strip()
        raw_document = documents.get(document_id)
        if raw_document is None:
            raise ValueError("typed DART 비교 후보의 원본 문서가 없습니다")
        fragment_text = str(raw_fragment.get("text") or "")
        marker_sentences = tuple(
            sentence
            for sentence in comparison_evidence_sentences(fragment_text)
            if comparison_source_sentence_has_marker(sentence)
        )
        for sentence_index, sentence in enumerate(marker_sentences):
            evidence_sha256 = hashlib.sha256(sentence.encode("utf-8")).hexdigest()
            candidates.append(
                OfficialComparisonCandidateEvidence(
                    company_id=company_id,
                    candidate_id=(
                        f"{str(raw_fragment.get('fragment_id') or '').strip()}"
                        f":comparison:{sentence_index}"
                    ),
                    document_id=document_id,
                    canonical_url=str(raw_document.get("canonical_url") or "").strip(),
                    source_tier=SourceTier(
                        str(raw_document.get("source_tier") or "").strip()
                    ),
                    source_kind=str(raw_document.get("source_kind") or "").strip(),
                    publisher=str(raw_document.get("publisher") or "").strip(),
                    title=str(raw_document.get("title") or "").strip(),
                    published_on=str(raw_document.get("published_on") or "").strip(),
                    collected_at=str(raw_document.get("collected_at") or "").strip(),
                    document_content_sha256=str(
                        raw_document.get("content_sha256") or ""
                    ).strip(),
                    identity_binding=str(
                        raw_document.get("identity_binding") or ""
                    ).strip(),
                    collector_version=str(
                        raw_document.get("collector_version") or ""
                    ).strip(),
                    parser_version=str(
                        raw_document.get("parser_version") or ""
                    ).strip(),
                    requirement=SourceRequirement(
                        str(raw_document.get("requirement") or "").strip()
                    ),
                    location=(
                        f"{str(raw_fragment.get('location') or '').strip()}"
                        f":sentence:{sentence_index}"
                    ),
                    evidence_text=sentence,
                    evidence_sha256=evidence_sha256,
                )
            )
    return tuple(candidates)


def _dart_official_candidate_provenance(
    envelope: Mapping[str, object],
    *,
    company_id: str,
) -> tuple[tuple[str, str], ...]:
    """typed DART가 전문에서 찾은 URL의 닫힌 provenance만 통과시킨다."""

    raw_candidates = envelope.get("official_url_candidates", ())
    if not isinstance(raw_candidates, (list, tuple)):
        raise ValueError("typed DART 공식 URL 후보가 list/tuple이 아닙니다")
    result: list[tuple[str, str]] = []
    seen_urls: set[str] = set()
    for raw in raw_candidates:
        if not isinstance(raw, Mapping):
            raise ValueError("typed DART 공식 URL 후보가 Mapping이 아닙니다")
        candidate_company_id = str(raw.get("company_id") or "").strip()
        url = str(raw.get("url") or "").strip()
        document_id = str(raw.get("source_document_id") or "").strip()
        receipt_no = str(raw.get("source_receipt_no") or "").strip()
        member_name = str(raw.get("source_member_name") or "")
        location = str(raw.get("source_location") or "").strip()
        document_sha256 = str(raw.get("source_document_sha256") or "").strip()
        payload_sha256 = str(raw.get("source_payload_sha256") or "").strip()
        if candidate_company_id != company_id:
            raise ValueError("typed DART 공식 URL 후보의 회사 식별자가 다릅니다")
        try:
            provenance = build_dart_filing_url_provenance(
                company_id=candidate_company_id,
                url=url,
                source_document_id=document_id,
                source_receipt_no=receipt_no,
                source_member_name=member_name,
                source_location=location,
                source_document_sha256=document_sha256,
                source_payload_sha256=payload_sha256,
            )
        except ValueError as error:
            raise ValueError(
                "typed DART 공식 URL 후보 provenance가 올바르지 않습니다"
            ) from error
        if url in seen_urls:
            continue
        seen_urls.add(url)
        result.append((url, provenance))
    return tuple(result)


def provenance_documents_from_wide_envelope(
    envelope: Mapping[str, object],
    *,
    company_id: str,
) -> tuple[OfficialProvenanceDocument, ...]:
    """wide mapping의 Writer 비대상 문서를 typed 감사 차선으로 옮긴다."""

    raw_documents = envelope.get("provenance_documents", ())
    if not isinstance(raw_documents, (list, tuple)):
        raise ValueError("공식 웹 provenance-only 문서 배열 형식이 올바르지 않습니다")
    result: list[OfficialProvenanceDocument] = []
    for raw in raw_documents:
        if not isinstance(raw, Mapping):
            raise ValueError("공식 웹 provenance-only 문서가 Mapping이 아닙니다")
        observed_company_id = str(raw.get("company_id") or "").strip()
        if observed_company_id != company_id:
            raise ValueError("공식 웹 provenance-only 문서의 회사 식별자가 다릅니다")
        try:
            item = OfficialProvenanceDocument(
                company_id=observed_company_id,
                document_id=str(raw.get("document_id") or "").strip(),
                canonical_url=str(raw.get("canonical_url") or "").strip(),
                source_tier=SourceTier(str(raw.get("source_tier") or "").strip()),
                source_kind=str(raw.get("source_kind") or "").strip(),
                publisher=str(raw.get("publisher") or "").strip(),
                title=str(raw.get("title") or "").strip(),
                published_on=str(raw.get("published_on") or "").strip(),
                collected_at=str(raw.get("collected_at") or "").strip(),
                content_sha256=str(raw.get("content_sha256") or "").strip(),
                identity_binding=str(raw.get("identity_binding") or "").strip(),
                collector_version=str(raw.get("collector_version") or "").strip(),
                parser_version=str(raw.get("parser_version") or "").strip(),
                requirement=SourceRequirement(
                    str(raw.get("requirement") or "").strip()
                ),
                exclusion_reason=str(raw.get("exclusion_reason") or "").strip(),
                domain_attestation_source_id=str(
                    raw.get("domain_attestation_source_id") or ""
                ).strip(),
                domain_attestation_evidence=str(
                    raw.get("domain_attestation_evidence") or ""
                ).strip(),
                reporting_period=str(raw.get("reporting_period") or "").strip(),
                attachment_url=str(raw.get("attachment_url") or "").strip(),
                ir_metadata_verification=str(
                    raw.get("ir_metadata_verification") or ""
                ).strip(),
                domain_redirect_verification=str(
                    raw.get("domain_redirect_verification") or ""
                ).strip(),
                domain_redirect_from_host=str(
                    raw.get("domain_redirect_from_host") or ""
                ).strip(),
                domain_redirect_to_host=str(
                    raw.get("domain_redirect_to_host") or ""
                ).strip(),
            )
        except (TypeError, ValueError) as error:
            raise ValueError(
                "공식 웹 provenance-only 문서 계약이 올바르지 않습니다"
            ) from error
        result.append(item)
    return tuple(result)


def _extend_loaded_package_path(package_name: str, package_path: Path) -> None:
    """이미 적재된 동명 package가 엔진 하위 모듈을 가리지 않게 한다."""

    package = sys.modules.get(package_name)
    if package is None:
        return
    search_paths = getattr(package, "__path__", None)
    if search_paths is None:
        raise ImportError(f"{package_name}가 package가 아니어서 typed 수집기를 열 수 없습니다")
    expected = package_path.resolve()
    existing = {Path(value).resolve() for value in search_paths}
    if expected in existing:
        return
    if hasattr(search_paths, "insert"):
        search_paths.insert(0, str(expected))
    elif hasattr(search_paths, "append"):
        search_paths.append(str(expected))
    else:
        raise ImportError(f"{package_name} 검색 경로를 안전하게 확장할 수 없습니다")


def _typed_dart_collector_modules() -> tuple[ModuleType, ModuleType, ModuleType]:
    """요청 실행 시점에만 엔진 모듈을 열고 실제 파일 위치를 확인한다."""

    with _ENGINE_IMPORT_LOCK:
        engine_src = (paths.PROJECT_ROOT / "analysis_engine" / "src").resolve()
        expected_files = {
            name: (
                engine_src / "features" / "evidence_collection" / f"{name}.py"
            ).resolve()
            for name in _ENGINE_MODULE_NAMES
        }
        if not engine_src.is_dir() or any(
            not module_path.is_file() for module_path in expected_files.values()
        ):
            raise ImportError("typed DART 수집기 모듈이 저장소의 엔진 경계에 없습니다")

        if str(engine_src) not in sys.path:
            sys.path.insert(0, str(engine_src))
        _extend_loaded_package_path("features", engine_src / "features")
        _extend_loaded_package_path("core", engine_src / "core")
        importlib.invalidate_caches()

        modules = tuple(
            importlib.import_module(f"features.evidence_collection.{name}")
            for name in _ENGINE_MODULE_NAMES
        )
        for name, module in zip(_ENGINE_MODULE_NAMES, modules):
            raw_module_path = str(getattr(module, "__file__", "") or "")
            if not raw_module_path:
                raise ImportError("typed DART 수집기 모듈의 파일 신원을 확인할 수 없습니다")
            try:
                module_path = Path(raw_module_path).resolve(strict=True)
            except OSError as error:
                raise ImportError(
                    "typed DART 수집기 모듈의 파일 신원을 확인할 수 없습니다"
                ) from error
            if module_path != expected_files[name]:
                raise ImportError("typed DART 수집기가 엔진 경계 밖에서 적재됐습니다")

        collect_module, fetcher_module, serialize_module = modules
        if not callable(getattr(collect_module, "collect_dart_evidence", None)):
            raise ImportError("typed DART 수집 함수가 없습니다")
        if not callable(getattr(fetcher_module, "DartRuntimeFetcher", None)):
            raise ImportError("typed DART 실행 어댑터가 없습니다")
        if not callable(getattr(serialize_module, "harvest_to_mapping", None)):
            raise ImportError("typed DART mapping 함수가 없습니다")
        return collect_module, fetcher_module, serialize_module


class ProductionOfficialEvidenceCollector:
    """기존 DART 요청 자원과 공식 웹 수집을 typed 후보로 합친다."""

    def collect(
        self,
        request: OfficialEvidenceCollectionRequest,
    ) -> OfficialEvidenceCollectionResult:
        collect_module, fetcher_module, serialize_module = (
            _typed_dart_collector_modules()
        )
        dart_fetcher = fetcher_module.DartRuntimeFetcher(
            document_cache_dir=request.dart_document_cache_dir,
            counter=request.dart_counter,
            get_json_fn=request.dart_get_json,
            download_document_fn=request.dart_download_document,
            # FULL formal 경계만 구버전 대표 XML cache의 URL sidecar를
            # 강제 backfill한다. 실패를 후보 0건/자료 부족으로 숨기지 않는다.
            require_official_url_sidecar=True,
            today=lambda: request.as_of_date,
        )
        dart_harvest = collect_module.collect_dart_evidence(
            dart_fetcher,
            request.company_id,
            now=request.collected_at,
            # 비교 문법은 app의 정본을 callback으로 주입한다. engine은 앞쪽
            # 짧은 표 셀 N개에서 멈추지 않고 이 조건에 맞는 후보를 전문에서
            # bounded 탐색하며, 일치 후보가 cap을 넘으면 TRUNCATED로 남긴다.
            short_observation_filter=_paragraph_has_comparison_candidate,
        )
        dart_envelope = serialize_module.harvest_to_mapping(dart_harvest)
        if not isinstance(dart_envelope, Mapping):
            raise ValueError("typed DART 수집 결과가 Mapping이 아닙니다")
        _classified_evidence_location_bindings(
            dart_envelope,
            company_id=request.company_id,
        )
        dart_candidate_provenance = _dart_official_candidate_provenance(
            dart_envelope,
            company_id=request.company_id,
        )
        unclassified_evidence = _unclassified_evidence_observation(
            dart_envelope,
            company_id=request.company_id,
        )
        comparison_candidates = _comparison_candidate_evidence(
            dart_envelope,
            company_id=request.company_id,
        )

        wide_result = collect_official_web_documents(
            company_id=request.company_id,
            company_name=request.company_name,
            company_aliases=request.company_aliases,
            root_homepage_url=request.root_homepage_url,
            company_registration_numbers=request.company_registration_numbers,
            official_candidate_urls=request.official_candidate_urls,
            official_candidate_provenance=dart_candidate_provenance,
            domain_attestation_source_id=request.domain_attestation_source_id,
            domain_attestation_evidence=request.domain_attestation_evidence,
            # DART hm_url도 오래되어 다른 회사에 재할당될 수 있다. 정식 운영은
            # 법인명+등록번호 확인 전 root를 TIER1+REQUIRED로 쓰지 않는다.
            root_identity_verification_required=True,
            collected_at=request.collected_at,
        )
        wide_fragments = build_fragments_for_collection(wide_result)
        wide_envelope = to_evidence_mappings(
            result=wide_result,
            fragments=wide_fragments,
        )
        if not isinstance(wide_envelope, Mapping):
            raise ValueError("공식 웹 수집 결과가 Mapping이 아닙니다")
        _classified_evidence_location_bindings(
            wide_envelope,
            company_id=request.company_id,
        )
        provenance_documents = provenance_documents_from_wide_envelope(
            wide_envelope,
            company_id=request.company_id,
        )

        # analysis_engine serializer가 반드시 싣는 판정이다. 키가 빠졌는데
        # ``undecided``로 메우면 회사유형별 필수 source_kind 검사가 조용히
        # 생략되어, adapter 배선 손상이 실제 자료 부족처럼 보인다.
        try:
            company_type = dart_envelope["company_type"]
        except KeyError as error:
            raise ValueError(
                "DART 공식 근거 결과에 필수 company_type이 없습니다"
            ) from error
        if (
            type(company_type) is not str
            or not company_type
            or company_type != company_type.strip()
        ):
            raise ValueError("DART 공식 근거 company_type 형식이 올바르지 않습니다")

        # DART와 웹 배열을 여기서 직접 평탄화하지 않는다. source_kind·section·
        # slot·attempt 상태를 보존한 두 envelope가 아래 유일한 merge 경계를
        # 통과해야 회사 결속과 9장 선택 정책을 한 번에 적용할 수 있다.
        candidates = produce_from_collection_envelopes(
            company_id=request.company_id,
            company_type=company_type,
            collection_envelopes=(dart_envelope, wide_envelope),
        )
        result = OfficialEvidenceCollectionResult(
            company_id=request.company_id,
            candidates=candidates,
            unclassified_evidence=unclassified_evidence,
            comparison_candidates=comparison_candidates,
            provenance_documents=provenance_documents,
        )
        if not evidence_reclassify_enabled():
            return result
        return attach_reclassify_source(
            result,
            company_type=company_type,
            dart_envelope=dart_envelope,
            wide_envelope=wide_envelope,
        )
