"""DART 기업개황 root·자손 host 결속의 공통 정본 시험."""

from __future__ import annotations

import json

import pytest

from src.shared.official_ir import IR_DART_WWW_REDIRECT_VALUE
from src.shared.report_evidence.profile_domain_attestation import (
    build_registered_subdomain_profile_attestation,
    dart_profile_attestation_allows_source_url,
    dart_profile_attestation_matches_company,
    parse_dart_profile_domain_attestation,
)


def _profile_evidence(hm_url: str = "https://company.example/") -> str:
    return json.dumps(
        {
            "corp_code": "00126380",
            "corp_name": "가나다전자",
            "hm_url": hm_url,
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def test_DART_root의_실제_등록하위도메인은_canonical_material로_결속된다() -> None:
    base = _profile_evidence()
    proof = build_registered_subdomain_profile_attestation(
        base,
        source_url="https://recruit.company.example/jobs",
    )

    parsed = parse_dart_profile_domain_attestation(proof)
    assert parsed is not None
    assert parsed.root_host == "company.example"
    assert parsed.candidate_host == "recruit.company.example"
    assert parsed.base_evidence == base
    assert dart_profile_attestation_matches_company(
        proof,
        corp_code="00126380",
        company_name="가나다전자",
    )
    assert dart_profile_attestation_allows_source_url(
        proof,
        source_url="https://recruit.company.example/jobs",
    )


@pytest.mark.parametrize(
    ("hm_url", "candidate_url"),
    (
        ("https://www.company.example/", "https://recruit.company.example/"),
        ("https://company.example/", "https://jobs.company.example.evil.com/"),
        ("https://company.example/", "https://other.example/"),
        ("https://co.kr/", "https://recruit.co.kr/"),
        ("https://company.unknown/", "https://recruit.company.unknown/"),
    ),
)
def test_형제_타등록도메인_공개접미사_미지원접미사는_하위도메인proof가_아니다(
    hm_url: str,
    candidate_url: str,
) -> None:
    assert not build_registered_subdomain_profile_attestation(
        _profile_evidence(hm_url),
        source_url=candidate_url,
    )


def test_하위도메인proof는_다른_host나_redirect표식으로_재사용할수없다() -> None:
    proof = build_registered_subdomain_profile_attestation(
        _profile_evidence(),
        source_url="https://recruit.company.example/jobs",
    )

    assert not dart_profile_attestation_allows_source_url(
        proof,
        source_url="https://ir.company.example/jobs",
    )
    assert not dart_profile_attestation_allows_source_url(
        proof,
        source_url="https://recruit.company.example/jobs",
        redirect_verification=IR_DART_WWW_REDIRECT_VALUE,
        redirect_from_host="company.example",
        redirect_to_host="www.company.example",
    )


def test_apex에서_www로의_실제검증표식은_일반웹도_공통계약으로_허용한다() -> None:
    evidence = _profile_evidence()

    assert not dart_profile_attestation_allows_source_url(
        evidence,
        source_url="https://www.company.example/about",
    )
    assert dart_profile_attestation_allows_source_url(
        evidence,
        source_url="https://www.company.example/about",
        redirect_verification=IR_DART_WWW_REDIRECT_VALUE,
        redirect_from_host="company.example",
        redirect_to_host="www.company.example",
    )
    assert not dart_profile_attestation_allows_source_url(
        evidence,
        source_url="https://recruit.company.example/about",
        redirect_verification=IR_DART_WWW_REDIRECT_VALUE,
        redirect_from_host="company.example",
        redirect_to_host="recruit.company.example",
    )
