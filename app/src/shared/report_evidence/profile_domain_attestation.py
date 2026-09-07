"""DART 기업개황 홈페이지와 공식 자손 host의 공통 결속 영수증.

기업개황의 ``hm_url`` 원문만으로는 그 host 자체만 증명된다. 수집기가
``recruit.company.com`` 같은 실제 자손 host를 발견했을 때는 인증 root,
후보 host, 닫힌 등록 도메인 판정을 한 canonical 문자열로 묶는다. 수집,
typed transport, 공개 Source 등록부가 모두 이 모듈로 같은 결론을 낸다.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from dataclasses import dataclass
from typing import Final

from src.shared.company_identity import exact_company_name_key
from src.shared.official_ir import (
    dart_homepage_exact_host,
    dart_www_redirect_is_valid,
)
from src.shared.registered_domain import (
    is_actual_registered_subdomain,
    registrable_domain,
)
from src.shared.report_evidence.identity_verified_web import (
    canonical_identity_verified_web_url,
    is_disallowed_identity_host,
)


REGISTERED_SUBDOMAIN_ATTESTATION_PREFIX: Final[str] = (
    "dart-profile-registered-subdomain-v1:"
)
_PROFILE_KEYS = frozenset({"corp_code", "corp_name", "hm_url"})
_SUBDOMAIN_KEYS = frozenset(
    {"candidate_host", "profile_evidence", "root_host", "verification"}
)
_SUBDOMAIN_VERIFICATION = "actual_registered_subdomain"
_CORP_CODE_RE = re.compile(r"^[0-9]{8}$")


@dataclass(frozen=True)
class DartProfileAttestation:
    corp_code: str
    corp_name: str
    hm_url: str
    root_host: str
    base_evidence: str
    candidate_host: str = ""

    @property
    def is_registered_subdomain(self) -> bool:
        return bool(self.candidate_host)


def registered_subdomain_root_basis(root_host: object) -> str:
    """자손 host를 판정할 기준 host를 돌려준다.

    DART 기업개황의 ``hm_url``은 같은 회사를 ``company.example``로도
    ``www.company.example``로도 적는다. 두 값은 같은 등록 도메인의 두 이름일
    뿐인데, ``www`` 쪽을 글자 그대로 기준으로 삼으면 ``recruit.company.example``
    같은 «실제 자손»이 자손이 아니게 되어 영수증이 통째로 비었다.

    그래서 root가 등록 도메인(eTLD+1) 자체이거나 그 정확한 ``www`` 짝일 때만
    기준을 등록 도메인으로 모은다. 수집기
    ``features/homepage/wide_domain.py``의 ``bind_registered_subdomain``이 쓰는
    규칙과 같은 규칙이다(만드는 쪽과 되읽는 쪽이 다른 기준을 쓰면 안 된다).

    그 밖의 root(예: 공유 플랫폼 하위호스트 ``sites.example.com``)는 회사가 그
    등록 도메인을 소유한다고 볼 수 없으므로 **넓히지 않고** root host 글자
    그대로를 기준으로 남긴다 — 기존 동작 그대로다.

    Args:
        root_host: DART 기업개황 홈페이지의 실제 host.

    Returns:
        자손 판정 기준 host(소문자). 판정 불가면 ``""``(fail-closed).
    """

    if type(root_host) is not str:
        return ""
    normalized = root_host.casefold().rstrip(".")
    if not normalized:
        return ""
    root_core = registrable_domain(normalized)
    if root_core and normalized in (root_core, f"www.{root_core}"):
        return root_core
    return normalized


def _is_profile_registered_subdomain(root_host: str, candidate_host: str) -> bool:
    """영수증을 만들 때와 되읽을 때가 반드시 같은 답을 내는 단일 판정.

    ``candidate_host``가 root host 자신이면 자손이 아니다 — root 자체는 기본
    기업개황 영수증이 이미 증명하므로 파생 proof를 만들지 않는다.
    """

    basis = registered_subdomain_root_basis(root_host)
    return bool(
        basis
        and candidate_host != root_host
        and is_actual_registered_subdomain(basis, candidate_host)
    )


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _parse_base_evidence(value: object) -> DartProfileAttestation | None:
    if type(value) is not str or not value:
        return None
    try:
        payload = json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if (
        type(payload) is not dict
        or set(payload) != _PROFILE_KEYS
        or any(type(payload.get(key)) is not str for key in _PROFILE_KEYS)
        or _CORP_CODE_RE.fullmatch(payload["corp_code"].strip()) is None
        or not exact_company_name_key(payload["corp_name"])
        or _canonical_json(payload) != value
    ):
        return None
    root_host = dart_homepage_exact_host(payload["hm_url"])
    return DartProfileAttestation(
        corp_code=payload["corp_code"].strip(),
        corp_name=payload["corp_name"].strip(),
        hm_url=payload["hm_url"].strip(),
        root_host=root_host,
        base_evidence=value,
    )


def parse_dart_profile_domain_attestation(
    value: object,
) -> DartProfileAttestation | None:
    """기본 기업개황 또는 자손 host 영수증의 canonical 모양만 연다."""

    base = _parse_base_evidence(value)
    if base is not None:
        return base
    if type(value) is not str or not value.startswith(
        REGISTERED_SUBDOMAIN_ATTESTATION_PREFIX
    ):
        return None
    encoded = value[len(REGISTERED_SUBDOMAIN_ATTESTATION_PREFIX) :]
    try:
        payload = json.loads(encoded)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if (
        type(payload) is not dict
        or set(payload) != _SUBDOMAIN_KEYS
        or any(type(payload.get(key)) is not str for key in _SUBDOMAIN_KEYS)
        or payload["verification"] != _SUBDOMAIN_VERIFICATION
        or _canonical_json(payload) != encoded
    ):
        return None
    base = _parse_base_evidence(payload["profile_evidence"])
    root_host = payload["root_host"].casefold().rstrip(".")
    candidate_host = payload["candidate_host"].casefold().rstrip(".")
    if (
        base is None
        or not base.root_host
        or root_host != base.root_host
        or candidate_host != payload["candidate_host"]
        or root_host != payload["root_host"]
        or is_disallowed_identity_host(root_host)
        or is_disallowed_identity_host(candidate_host)
        or not _is_profile_registered_subdomain(root_host, candidate_host)
    ):
        return None
    return DartProfileAttestation(
        corp_code=base.corp_code,
        corp_name=base.corp_name,
        hm_url=base.hm_url,
        root_host=root_host,
        base_evidence=base.base_evidence,
        candidate_host=candidate_host,
    )


def build_registered_subdomain_profile_attestation(
    profile_evidence: object,
    *,
    source_url: object,
) -> str:
    """기업개황 root의 실제 자손 host만 별도 봉인 material로 만든다."""

    profile = _parse_base_evidence(profile_evidence)
    canonical_url = canonical_identity_verified_web_url(source_url)
    if profile is None or not profile.root_host or not canonical_url:
        return ""
    candidate_host = (
        urllib.parse.urlsplit(canonical_url).hostname or ""
    ).casefold().rstrip(".")
    if (
        is_disallowed_identity_host(profile.root_host)
        or is_disallowed_identity_host(candidate_host)
        or not _is_profile_registered_subdomain(profile.root_host, candidate_host)
    ):
        return ""
    payload = {
        "candidate_host": candidate_host,
        "profile_evidence": profile.base_evidence,
        "root_host": profile.root_host,
        "verification": _SUBDOMAIN_VERIFICATION,
    }
    return REGISTERED_SUBDOMAIN_ATTESTATION_PREFIX + _canonical_json(payload)


def dart_profile_attestation_matches_company(
    value: object,
    *,
    corp_code: object,
    company_name: object,
) -> bool:
    """영수증이 현재 DART 회사와 정확히 같은지 확인한다."""

    profile = parse_dart_profile_domain_attestation(value)
    return bool(
        profile is not None
        and type(corp_code) is str
        and profile.corp_code == corp_code.strip()
        and exact_company_name_key(profile.corp_name)
        == exact_company_name_key(company_name)
    )


def dart_profile_attestation_allows_source_url(
    value: object,
    *,
    source_url: object,
    redirect_verification: object = "",
    redirect_from_host: object = "",
    redirect_to_host: object = "",
) -> bool:
    """영수증과 명시적 redirect proof가 현재 Source URL을 허용하는가."""

    profile = parse_dart_profile_domain_attestation(value)
    canonical_url = canonical_identity_verified_web_url(source_url)
    redirect_values = (
        redirect_verification,
        redirect_from_host,
        redirect_to_host,
    )
    if (
        profile is None
        or not profile.root_host
        or not canonical_url
        or any(type(item) is not str for item in redirect_values)
    ):
        return False
    source_host = (
        urllib.parse.urlsplit(canonical_url).hostname or ""
    ).casefold().rstrip(".")
    if profile.is_registered_subdomain:
        return bool(
            not any(item.strip() for item in redirect_values)
            and source_host == profile.candidate_host
            and _is_profile_registered_subdomain(
                profile.root_host,
                profile.candidate_host,
            )
        )
    if source_host == profile.root_host:
        return not any(item.strip() for item in redirect_values)
    return dart_www_redirect_is_valid(
        verification=redirect_verification.strip(),
        from_host=redirect_from_host.strip(),
        to_host=redirect_to_host.strip(),
        dart_host=profile.root_host,
        source_host=source_host,
    )
