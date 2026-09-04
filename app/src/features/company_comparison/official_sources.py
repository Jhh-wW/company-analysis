"""공식 웹·IR 경쟁 후보의 DART 법인·도메인 결속.

순수 경쟁 문장은 1~8장의 의미 사실로 억지 분류하지 않는다. 성공한 OpenDART
기업개황 응답의 최소 필드만 결정론적으로 봉인하고, 실제 HTTPS 최종 URL의 host가
그 ``hm_url`` host와 정확히 같은 HTML 원문만 별도 9장 후보 원장으로 넘긴다.
검색 결과·snippet 여부는 최종 후보 정책에서 한 번 더 닫는다.
"""

from __future__ import annotations

import ipaddress
import json
import re
import unicodedata
import urllib.parse
from dataclasses import dataclass
from typing import Any, Mapping

from src.features.provenance.sources import (
    Source,
    SourceKind,
    build_dart_profile_attester_source,
    evidence_text_hash,
    exact_evidence_text_hash,
    seal_collected_source,
)
from src.shared.comparison_candidate_basis import (
    comparison_evidence_sentences,
    comparison_source_sentence_has_marker,
)
from src.shared.official_ir import (
    IR_COLLECTED_ON_FIELD,
    IR_DART_WWW_REDIRECT_FIELD,
    IR_DART_WWW_REDIRECT_FROM_FIELD,
    IR_DART_WWW_REDIRECT_TO_FIELD,
    dart_www_redirect_is_valid,
)


DART_SUCCESS_STATUS = "000"
OFFICIAL_WEB_FRAGMENT_KINDS = frozenset({"홈페이지", "공식 IR"})
VERIFIED_FINAL_URL_FIELD = "후보출처검증"
VERIFIED_FINAL_URL_VALUE = "https_exact_dart_host"
_CORP_CODE = re.compile(r"[0-9]{8}")


@dataclass(frozen=True)
class OfficialCandidateSentence:
    """봉인된 공식 Source에서 그대로 읽은 한 문장."""

    source: Source
    evidence_text: str
    document_identity: str = ""
    document_content_sha256: str = ""


@dataclass(frozen=True)
class ProfileAttestationResult:
    """기업개황 attester와 그 ID가 결속된 provenance 조각."""

    fragments: dict[int, dict[str, Any]]
    attester: Source | None


def _exact_name(value: object) -> str:
    return " ".join(
        unicodedata.normalize("NFKC", str(value or "")).casefold().split()
    )


def _official_https_url(value: object) -> str:
    raw = str(value or "").strip()
    if not raw or "\\" in raw or any(ord(char) < 32 for char in raw):
        return ""
    if "://" not in raw:
        raw = f"https://{raw}"
    try:
        parsed = urllib.parse.urlsplit(raw)
        hostname = (parsed.hostname or "").rstrip(".").encode("idna").decode("ascii")
        port = parsed.port
    except (UnicodeError, TypeError, ValueError):
        return ""
    if (
        parsed.scheme.casefold() not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or "%" in parsed.netloc
        or hostname.casefold() == "localhost"
        or hostname.casefold().endswith(".localhost")
        or "." not in hostname
    ):
        return ""
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        address = None
    if address is not None and not address.is_global:
        return ""
    netloc = hostname.casefold()
    path = urllib.parse.quote(parsed.path or "", safe="/%:@!$&'()*+,;=-._~")
    query = urllib.parse.quote(parsed.query or "", safe="/%?:@!$&'()*+,;=-._~")
    # DART가 legacy http 주소를 주더라도 본문은 같은 host/path의 HTTPS로만
    # 실제 접속·검증한다. HTTP fallback 결과는 수집 표식이 없어 후보가 못 된다.
    return urllib.parse.urlunsplit(("https", netloc, path, query, ""))


def _host(value: object) -> str:
    try:
        return (urllib.parse.urlsplit(str(value or "")).hostname or "").casefold().rstrip(".")
    except ValueError:
        return ""


def dart_profile_attestation_material(
    *,
    profile: Mapping[str, Any],
    corp_code: str,
    company_name: str,
) -> tuple[str, str]:
    """검증된 OpenDART 기업개황의 안정 Source ID와 exact subset을 만든다.

    FULL typed 수집과 기존 비교 수집이 서로 JSON을 다시 만들면 회사 공식
    도메인 결속이 경계 사이에서 달라진다. status·법인번호·정확한 법인명을
    한 번 확인하고, 두 생산 경로가 같은 byte 문자열을 운반하게 한다.
    """

    code = str(corp_code or "").strip()
    response_code = str(profile.get("corp_code") or "").strip()
    profile_name = str(profile.get("corp_name") or "").strip()
    raw_profile_url = str(profile.get("hm_url") or "").strip()
    if (
        str(profile.get("status") or "").strip() != DART_SUCCESS_STATUS
        or not _CORP_CODE.fullmatch(code)
        or response_code != code
        or not profile_name
        or _exact_name(profile_name) != _exact_name(company_name)
    ):
        return "", ""
    evidence = json.dumps(
        {
            "corp_code": code,
            "corp_name": profile_name,
            "hm_url": raw_profile_url,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"dart-company-profile-{code}", evidence


def bind_dart_profile_attestation(
    fragments: Mapping[int, Mapping[str, Any]],
    *,
    profile: Mapping[str, Any],
    corp_code: str,
    company_name: str,
    collected_on: str,
) -> ProfileAttestationResult:
    """정확한 DART 기업개황과 HTTPS 최종 host가 맞는 웹 조각만 결속한다.

    시험용 문자열 fetch처럼 실제 최종 URL을 확인하지 못한 조각, HTTP fallback,
    인증서 대체 host, cross-host redirect는 일반 분석 재료로는 남아도 경쟁 후보
    근거로 승격되지 않는다.
    """

    copied = {number: dict(fragment) for number, fragment in fragments.items()}
    code = str(corp_code or "").strip()
    profile_name = str(profile.get("corp_name") or "").strip()
    raw_profile_url = str(profile.get("hm_url") or "").strip()
    official_url = _official_https_url(raw_profile_url)
    official_host = _host(official_url)
    source_id, evidence = dart_profile_attestation_material(
        profile=profile,
        corp_code=code,
        company_name=company_name,
    )
    if not source_id or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}", str(collected_on or "")
    ):
        return ProfileAttestationResult(copied, None)

    eligible_numbers: list[int] = []
    for number, fragment in copied.items():
        if str(fragment.get("종류") or "") not in OFFICIAL_WEB_FRAGMENT_KINDS:
            continue
        source_url = _official_https_url(fragment.get("출처"))
        source_host = _host(source_url)
        verified_www_redirect = dart_www_redirect_is_valid(
            verification=str(fragment.get(IR_DART_WWW_REDIRECT_FIELD) or ""),
            from_host=str(fragment.get(IR_DART_WWW_REDIRECT_FROM_FIELD) or ""),
            to_host=str(fragment.get(IR_DART_WWW_REDIRECT_TO_FIELD) or ""),
            dart_host=official_host,
            source_host=source_host,
        )
        if (
            official_host
            and fragment.get(VERIFIED_FINAL_URL_FIELD) == VERIFIED_FINAL_URL_VALUE
            and source_url
            and (source_host == official_host or verified_www_redirect)
        ):
            eligible_numbers.append(number)

    attester = build_dart_profile_attester_source(
        number=max(copied, default=0) + 1,
        source_id=source_id,
        evidence=evidence,
        company_name=company_name,
        collected_on=collected_on,
    )
    for number in eligible_numbers:
        copied[number].update(
            {
                "발행처": profile_name,
                "도메인근거SourceID": source_id,
                "도메인근거원문": evidence,
                **(
                    {IR_COLLECTED_ON_FIELD: collected_on}
                    if str(copied[number].get("종류") or "") == "공식 IR"
                    else {}
                ),
            }
        )
    return ProfileAttestationResult(copied, attester)


def candidate_sentences_from_fragments(
    fragments: Mapping[int, Mapping[str, Any]],
    sources: tuple[Source, ...] | list[Source],
) -> tuple[OfficialCandidateSentence, ...]:
    """실제 provenance 조각과 같은 번호의 Source에서 단일 문장들을 되살린다."""

    by_number = {source.number: source for source in sources}
    out: list[OfficialCandidateSentence] = []
    for number, fragment in sorted(fragments.items()):
        source = by_number.get(number)
        if source is None:
            continue
        raw = str(fragment.get("원문") or "").strip()
        for sentence in comparison_evidence_sentences(raw):
            if (
                evidence_text_hash(sentence) in source.evidence_hashes
                and exact_evidence_text_hash(sentence)
                in source.exact_evidence_hashes
            ):
                out.append(
                    OfficialCandidateSentence(
                        source,
                        sentence,
                        document_identity=str(
                            fragment.get("_evidence_document_identity") or ""
                        ).strip(),
                        document_content_sha256=str(
                            fragment.get("_evidence_document_content_sha256") or ""
                        ).strip(),
                    )
                )
    return tuple(out)


def register_candidate_sentence_evidence(
    fragments: Mapping[int, Mapping[str, Any]],
) -> dict[int, dict[str, Any]]:
    """실제 원문 안 경쟁 표지 문장만 Source 해시 등록 대상으로 표시한다.

    공개 문장을 새 원문처럼 주입하지 않는다. 각 값이 기존 ``원문``에서 그대로
    잘린 연속 문장인지는 ``build_citations``가 다시 확인한다.
    """

    copied = {number: dict(fragment) for number, fragment in fragments.items()}
    for fragment in copied.values():
        if str(fragment.get("종류") or "") == "뉴스":
            continue
        sentences = [
            sentence
            for sentence in comparison_evidence_sentences(
                str(fragment.get("원문") or "")
            )
            if comparison_source_sentence_has_marker(sentence)
        ]
        if not sentences:
            continue
        existing = fragment.get("근거원문") or []
        if isinstance(existing, str):
            existing = [existing]
        fragment["근거원문"] = list(
            dict.fromkeys(
                [
                    *(
                        str(item).strip()
                        for item in existing
                        if str(item).strip()
                    ),
                    *sentences,
                ]
            )
        )
    return copied
