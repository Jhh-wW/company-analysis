"""DART 기업개황 root·자손 host 결속의 공통 정본 시험."""

from __future__ import annotations

import json

import pytest

from src.shared.official_ir import IR_DART_WWW_REDIRECT_VALUE
from src.shared.report_evidence.profile_domain_attestation import (
    REGISTERED_SUBDOMAIN_ATTESTATION_PREFIX,
    build_registered_subdomain_profile_attestation,
    dart_profile_attestation_allows_source_url,
    dart_profile_attestation_matches_company,
    parse_dart_profile_domain_attestation,
    registered_subdomain_root_basis,
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
        ("https://company.example/", "https://jobs.company.example.evil.com/"),
        ("https://company.example/", "https://other.example/"),
        ("https://co.kr/", "https://recruit.co.kr/"),
        ("https://company.unknown/", "https://recruit.company.unknown/"),
        # www root도 «등록 도메인이 다르면» 여전히 거절한다. 예전에는
        # (www root, 실제 자손) 짝도 이 목록에 있었지만 그건 결함이었다 -
        # 아래 test_www_root도_등록apex_기준으로_실제_자손을_결속한다 가
        # 그 짝을 반대 방향으로 지킨다.
        ("https://www.company.example/", "https://company.example.evil.com/"),
        ("https://www.ganada-example.co.kr/", "https://ganada-example.com/ir"),
        ("https://www.ganada-example.co.kr/", "https://other-example.co.kr/ir"),
        # www root 자신은 자손이 아니다 - root는 기본 기업개황 영수증이 증명한다.
        ("https://www.ganada-example.co.kr/", "https://www.ganada-example.co.kr/ir"),
        # www root에서 apex 짝도 자손이 아니다(같은 등록 도메인의 다른 이름).
        ("https://www.ganada-example.co.kr/", "https://ganada-example.co.kr/ir"),
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
# ══════════════════════════════════════════════════════════
# www root — DART가 준 홈페이지가 www 별칭일 때 (N18)
# ══════════════════════════════════════════════════════════

#: 옛 판(=apex hm_url)으로 만들어져 이미 저장된 영수증 글자 그대로.
#: 생산 함수로 다시 만들지 않고 리터럴로 박아, 형식이 조용히 바뀌면
#: 이 시험이 먼저 깨지게 한다.
_LEGACY_APEX_PROOF = (
    "dart-profile-registered-subdomain-v1:"
    '{"candidate_host":"recruit.ganada-example.co.kr",'
    '"profile_evidence":"{\\"corp_code\\":\\"00126380\\",'
    '\\"corp_name\\":\\"가나다전자\\",'
    '\\"hm_url\\":\\"https://ganada-example.co.kr/\\"}",'
    '"root_host":"ganada-example.co.kr",'
    '"verification":"actual_registered_subdomain"}'
)


def test_www_root도_등록apex_기준으로_실제_자손을_결속한다() -> None:
    """DART hm_url이 www 별칭이어도 recruit·ir 자손의 영수증이 비지 않는다."""

    www_base = _profile_evidence("https://www.ganada-example.co.kr/")
    apex_base = _profile_evidence("https://ganada-example.co.kr/")
    candidate = "https://recruit.ganada-example.co.kr/jobs"

    www_proof = build_registered_subdomain_profile_attestation(
        www_base,
        source_url=candidate,
    )
    apex_proof = build_registered_subdomain_profile_attestation(
        apex_base,
        source_url=candidate,
    )
    assert www_proof
    assert apex_proof

    parsed = parse_dart_profile_domain_attestation(www_proof)
    assert parsed is not None
    assert parsed.root_host == "www.ganada-example.co.kr"
    assert parsed.candidate_host == "recruit.ganada-example.co.kr"
    assert parsed.base_evidence == www_base

    www_payload = json.loads(www_proof[len(REGISTERED_SUBDOMAIN_ATTESTATION_PREFIX) :])
    apex_payload = json.loads(
        apex_proof[len(REGISTERED_SUBDOMAIN_ATTESTATION_PREFIX) :]
    )
    # 두 영수증의 키 집합과 «검증 표식·후보 host»는 같다. 다른 칸은 root_host와
    # profile_evidence 둘뿐이고, 그 둘은 각자의 hm_url을 그대로 담는다
    # (profile_evidence 안에 hm_url이 들어 있어 www/apex가 서로 다르다).
    assert set(www_payload) == set(apex_payload)
    assert {
        key for key in www_payload if www_payload[key] != apex_payload[key]
    } == {"root_host", "profile_evidence"}
    assert www_payload["candidate_host"] == apex_payload["candidate_host"]
    assert www_payload["verification"] == apex_payload["verification"]
    assert www_payload["root_host"] == "www.ganada-example.co.kr"
    assert apex_payload["root_host"] == "ganada-example.co.kr"
    assert www_payload["profile_evidence"] == www_base
    assert apex_payload["profile_evidence"] == apex_base


def test_www_root의_다른_실제자손도_같은_기준으로_결속된다() -> None:
    """recruit 말고 임의 자손(a.·ir.)도 같은 apex 기준으로 통과한다."""

    www_base = _profile_evidence("https://www.ganada-example.co.kr/")

    for candidate in (
        "https://a.ganada-example.co.kr/",
        "https://ir.ganada-example.co.kr/library",
    ):
        proof = build_registered_subdomain_profile_attestation(
            www_base,
            source_url=candidate,
        )
        assert proof, candidate
        assert dart_profile_attestation_allows_source_url(proof, source_url=candidate)


def test_www_root_영수증은_되읽어도_같은_URL만_허용한다() -> None:
    """만드는 쪽과 되읽는 쪽이 같은 기준을 쓴다(재검산)."""

    proof = build_registered_subdomain_profile_attestation(
        _profile_evidence("https://www.ganada-example.co.kr/"),
        source_url="https://recruit.ganada-example.co.kr/jobs",
    )

    assert dart_profile_attestation_allows_source_url(
        proof,
        source_url="https://recruit.ganada-example.co.kr/jobs",
    )
    assert dart_profile_attestation_matches_company(
        proof,
        corp_code="00126380",
        company_name="가나다전자",
    )
    assert not dart_profile_attestation_allows_source_url(
        proof,
        source_url="https://ir.ganada-example.co.kr/jobs",
    )
    assert not dart_profile_attestation_allows_source_url(
        proof,
        source_url="https://recruit.ganada-example.co.kr/jobs",
        redirect_verification=IR_DART_WWW_REDIRECT_VALUE,
        redirect_from_host="ganada-example.co.kr",
        redirect_to_host="www.ganada-example.co.kr",
    )


def test_옛_apex영수증은_글자_그대로_계속_열리고_허용된다() -> None:
    """이미 저장된 apex root 영수증의 호환을 리터럴로 지킨다."""

    parsed = parse_dart_profile_domain_attestation(_LEGACY_APEX_PROOF)

    assert parsed is not None
    assert parsed.root_host == "ganada-example.co.kr"
    assert parsed.candidate_host == "recruit.ganada-example.co.kr"
    assert dart_profile_attestation_allows_source_url(
        _LEGACY_APEX_PROOF,
        source_url="https://recruit.ganada-example.co.kr/jobs",
    )


@pytest.mark.parametrize(
    ("root_host", "expected_basis"),
    (
        ("www.ganada-example.co.kr", "ganada-example.co.kr"),
        ("ganada-example.co.kr", "ganada-example.co.kr"),
        ("WWW.Ganada-Example.co.kr.", "ganada-example.co.kr"),
        # 등록 도메인 자체도 www 짝도 아닌 root는 넓히지 않는다(fail-closed).
        ("sites.ganada-example.co.kr", "sites.ganada-example.co.kr"),
        ("www.sites.ganada-example.co.kr", "www.sites.ganada-example.co.kr"),
        # 공개 접미사 목록 밖이면 기준을 넓히지 않고 글자 그대로 둔다.
        ("company.unknown", "company.unknown"),
        ("", ""),
    ),
)
def test_자손판정_기준host는_apex와_www짝만_등록도메인으로_모은다(
    root_host: str,
    expected_basis: str,
) -> None:
    assert registered_subdomain_root_basis(root_host) == expected_basis


def test_자손판정_기준host는_문자열이_아니면_빈값이다() -> None:
    for value in (None, 123, b"www.ganada-example.co.kr", ["a"]):
        assert registered_subdomain_root_basis(value) == ""
