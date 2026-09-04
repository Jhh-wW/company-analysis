"""DART 공시 URL→공식 홈페이지 승격의 단일 proof 계약 회귀시험."""

from __future__ import annotations

import hashlib
import json

import pytest

from src.shared.report_evidence.constants import SourceRequirement, SourceTier
from src.shared.report_evidence.identity_verified_web import (
    build_dart_filing_url_provenance,
    build_verified_dart_filing_official_web_binding,
    build_verified_dart_filing_subdomain_binding,
    identity_verified_web_expected_trust,
    parse_verified_dart_filing_official_web_binding,
    parse_verified_dart_filing_subdomain_binding,
    verified_dart_filing_binding_allows_public_source,
)
from src.shared.company_identity import exact_company_name_key


_COMPANY_ID = "00126380"
_RECEIPT = "20250315000001"
_SHA_A = "a" * 64
_SHA_B = "b" * 64
_SHA_C = "c" * 64
_COMPANY_NAME = "가나다전자 주식회사"
_REGISTRATION_NUMBER = "1234567890"
_NAME_SHA = hashlib.sha256(
    exact_company_name_key(_COMPANY_NAME).encode("utf-8")
).hexdigest()
_REGISTRATION_SHA = hashlib.sha256(
    _REGISTRATION_NUMBER.encode("ascii")
).hexdigest()


def _provenance(url: str) -> str:
    return build_dart_filing_url_provenance(
        company_id=_COMPANY_ID,
        url=url,
        source_document_id=f"dart_audit_report:{_RECEIPT}",
        source_receipt_no=_RECEIPT,
        source_member_name="covers/company-homepage.xml",
        source_location="raw_xml_chars:20-80",
        source_document_sha256=_SHA_A,
        source_payload_sha256=_SHA_B,
    )


def _binding(*, provenance_url: str, candidate_url: str, effective_url: str) -> str:
    return build_verified_dart_filing_official_web_binding(
        provenance_value=_provenance(provenance_url),
        company_id=_COMPANY_ID,
        company_name=_COMPANY_NAME,
        company_registration_numbers=(_REGISTRATION_NUMBER,),
        candidate_url=candidate_url,
        effective_urls=(effective_url,),
        scope_sha256=_SHA_C,
        scope_allows=lambda value: value == effective_url,
        identity_evidence_sha256=_SHA_A,
        matched_name_sha256=_NAME_SHA,
        registration_number_sha256=_REGISTRATION_SHA,
    )


def test_exact_DARTproof와_HTTPS범위와_이중신원hash가_모두_있을때만_TIER1이다() -> None:
    candidate = "https://company.example/about?tenant=wise"
    binding = _binding(
        provenance_url=candidate,
        candidate_url=candidate,
        effective_url=candidate,
    )

    assert parse_verified_dart_filing_official_web_binding(binding) is not None
    assert identity_verified_web_expected_trust(binding) == (
        SourceTier.TIER_1_OFFICIAL,
        SourceRequirement.REQUIRED,
    )
    assert verified_dart_filing_binding_allows_public_source(
        binding,
        company_name=_COMPANY_NAME,
        source_url=candidate,
    )


def test_법인명hash나_등록번호hash가_DART신원과_다르면_binding을_못만든다() -> None:
    candidate = "https://company.example/"
    common = dict(
        provenance_value=_provenance(candidate),
        company_id=_COMPANY_ID,
        company_name=_COMPANY_NAME,
        company_registration_numbers=(_REGISTRATION_NUMBER,),
        candidate_url=candidate,
        effective_urls=(candidate,),
        scope_sha256=_SHA_C,
        scope_allows=lambda value: value == candidate,
        identity_evidence_sha256=_SHA_A,
    )

    assert build_verified_dart_filing_official_web_binding(
        **common,
        matched_name_sha256=_SHA_B,
        registration_number_sha256=_REGISTRATION_SHA,
    ) == ""
    assert build_verified_dart_filing_official_web_binding(
        **common,
        matched_name_sha256=_NAME_SHA,
        registration_number_sha256=_SHA_C,
    ) == ""


def test_legacy_HTTP는_같은_host_path_query의_기본HTTPS만_승격한다() -> None:
    http = "http://company.example/company?tenant=wise"
    https = "https://company.example/company?tenant=wise"

    assert _binding(
        provenance_url=http,
        candidate_url=https,
        effective_url=https,
    )


@pytest.mark.parametrize(
    "candidate",
    (
        "https://other.example/company?tenant=wise",
        "https://company.example/other?tenant=wise",
        "https://company.example/company?tenant=other",
    ),
)
def test_HTTP승격에서_host_path_query_하나라도_바뀌면_거절한다(candidate: str) -> None:
    binding = _binding(
        provenance_url="http://company.example/company?tenant=wise",
        candidate_url=candidate,
        effective_url=candidate,
    )

    assert binding == ""
    assert identity_verified_web_expected_trust(binding) == (
        SourceTier.TIER_3_TRUSTED,
        SourceRequirement.OPTIONAL,
    )


@pytest.mark.parametrize(
    "url",
    (
        "http://company.example:8080/company",
        "https://user:secret@company.example/company",
        "https://127.0.0.1/company",
    ),
)
def test_비표준port_userinfo_IP후보는_proof생성전에_거절한다(url: str) -> None:
    with pytest.raises(ValueError):
        _provenance(url)


def test_landing이_다른host로_redirect되면_신원이_복사돼도_승격하지_않는다() -> None:
    candidate = "https://company.example/company"
    binding = _binding(
        provenance_url=candidate,
        candidate_url=candidate,
        effective_url="https://attacker.example/company",
    )

    assert binding == ""


@pytest.mark.parametrize(
    "candidate",
    (
        "https://blog.naver.com/company",
        "https://www.facebook.com/company",
        "https://dart.fss.or.kr/company",
    ),
)
def test_공유제3자와_DARTinfra_host는_완전한모양의_proof여도_승격하지_않는다(
    candidate: str,
) -> None:
    binding = _binding(
        provenance_url=candidate,
        candidate_url=candidate,
        effective_url=candidate,
    )

    assert binding == ""


def test_binding_JSON의_hash를_사후변조하면_canonical_parser가_거절한다() -> None:
    candidate = "https://company.example/"
    binding = _binding(
        provenance_url=candidate,
        candidate_url=candidate,
        effective_url=candidate,
    )
    prefix, encoded = binding.split(":", 1)
    payload = json.loads(encoded)
    payload["scope_sha256"] = "f" * 64
    # sort_keys/separator 정본을 일부러 지키지 않는다. 저장 뒤 문자열만 고친
    # 값은 source-kind 검증에서 TIER1 proof로 다시 열리지 않아야 한다.
    tampered = prefix + ":" + json.dumps(payload, ensure_ascii=False)

    assert parse_verified_dart_filing_official_web_binding(tampered) is None
    assert identity_verified_web_expected_trust(tampered) == (
        SourceTier.TIER_3_TRUSTED,
        SourceRequirement.OPTIONAL,
    )


@pytest.mark.parametrize(
    "root_url",
    ("https://company.example/", "https://www.company.example/"),
)
def test_strict_DART_root가_직접찾은_등록하위도메인은_원proof와_범위에_재결속된다(
    root_url: str,
) -> None:
    root_binding = _binding(
        provenance_url=root_url,
        candidate_url=root_url,
        effective_url=root_url,
    )
    child = "https://recruit.company.example/jobs"
    binding = build_verified_dart_filing_subdomain_binding(
        root_identity_binding=root_binding,
        source_url=child,
        scope_sha256=_SHA_B,
    )

    parsed = parse_verified_dart_filing_subdomain_binding(binding)
    assert parsed is not None
    assert parsed.root_binding == root_binding
    assert parsed.source_url == child
    assert identity_verified_web_expected_trust(binding) == (
        SourceTier.TIER_1_OFFICIAL,
        SourceRequirement.REQUIRED,
    )
    assert verified_dart_filing_binding_allows_public_source(
        binding,
        company_name=_COMPANY_NAME,
        source_url="https://recruit.company.example/jobs/opening-1",
    )


@pytest.mark.parametrize(
    "child",
    (
        "https://jobs.company.example/jobs",
        "https://other.example/jobs",
        "https://example/jobs",
        "https://recruit.company.example:8443/jobs",
        "https://user:secret@recruit.company.example/jobs",
    ),
)
def test_strict_DART_root_proof는_sibling_무관host_공개suffix_위험URL로_파생되지않는다(
    child: str,
) -> None:
    root = "https://recruit.company.example/"
    root_binding = _binding(
        provenance_url=root,
        candidate_url=root,
        effective_url=root,
    )

    assert build_verified_dart_filing_subdomain_binding(
        root_identity_binding=root_binding,
        source_url=child,
        scope_sha256=_SHA_B,
    ) == ""


def test_등록하위도메인_proof를_무관host로_바꾸면_parser가_거절한다() -> None:
    root = "https://company.example/"
    root_binding = _binding(
        provenance_url=root,
        candidate_url=root,
        effective_url=root,
    )
    binding = build_verified_dart_filing_subdomain_binding(
        root_identity_binding=root_binding,
        source_url="https://recruit.company.example/jobs",
        scope_sha256=_SHA_B,
    )

    tampered = binding.replace(
        "recruit.company.example",
        "recruit.other.example",
    )
    assert parse_verified_dart_filing_subdomain_binding(tampered) is None
    assert not verified_dart_filing_binding_allows_public_source(
        tampered,
        company_name=_COMPANY_NAME,
        source_url="https://recruit.other.example/jobs",
    )
