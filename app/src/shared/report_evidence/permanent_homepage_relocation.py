"""DART 등록 root의 실제 영구 이동과 목적지 이중 신원을 결속한다."""

from __future__ import annotations

import hashlib
import datetime as dt
import json
import re
import string
import urllib.parse
from collections.abc import Mapping
from typing import Final

from src.shared.company_identity import exact_company_name_key
from src.shared.registered_domain import registrable_domain
from src.shared.report_evidence.identity_verified_web import canonical_identity_verified_web_url

PERMANENT_RELOCATION_PREFIX: Final[str] = "dart-profile-permanent-relocation-v1:"
PERMANENT_RELOCATION_STATUSES: Final[frozenset[int]] = frozenset({301, 308})
PERMANENT_RELOCATION_MAX_CHARS: Final[int] = 8_192
_HASH_RE = re.compile(r"[0-9a-f]{64}")
_CORP_RE = re.compile(r"[0-9]{8}")
_PROFILE_KEYS = frozenset({"corp_code", "corp_name", "hm_url"})
_REDIRECT_KEYS = frozenset({"source_url", "target_url", "status", "raw_location", "observed_at"})
_PROOF_KEYS = frozenset({
    "profile_sha256", "redirect", "redirect_sha256", "raw_location_sha256",
    "candidate_url", "scope_sha256", "identity_evidence_sha256",
    "matched_name_sha256", "registration_number_sha256",
})


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _dedicated_root(value: object, *, profile: bool = False) -> str:
    if type(value) is not str or not value or any(ord(c) < 32 or ord(c) == 127 for c in value):
        return ""
    try:
        if profile and "://" not in value:
            value = f"https://{value}"
        parsed = urllib.parse.urlsplit(value)
        if parsed.username is not None or parsed.password is not None:
            return ""
        if profile and parsed.scheme == "http" and parsed.port in (None, 80):
            parsed = parsed._replace(scheme="https", netloc=parsed.hostname or "")
        candidate = urllib.parse.urlunsplit(parsed._replace(path=parsed.path or "/"))
        canonical = canonical_identity_verified_web_url(candidate)
        host = parsed.hostname or ""
        core = registrable_domain(host)
        if not canonical or parsed.path not in ("", "/") or parsed.query or not core or host not in (core, f"www.{core}"):
            return ""
        return canonical
    except (TypeError, ValueError, UnicodeError):
        return ""


def _validated_payload(profile_evidence: object, payload: object) -> dict[str, object] | None:
    try:
        if type(profile_evidence) is not str or type(payload) is not dict or set(payload) != _PROOF_KEYS:
            return None
        profile = json.loads(profile_evidence)
        if (type(profile) is not dict or set(profile) != _PROFILE_KEYS
                or any(type(value) is not str for value in profile.values())
                or _CORP_RE.fullmatch(profile["corp_code"]) is None
                or not exact_company_name_key(profile["corp_name"])
                or _canonical(profile) != profile_evidence):
            return None
        redirect = payload["redirect"]
        if type(redirect) is not dict or set(redirect) != _REDIRECT_KEYS:
            return None
        if type(redirect["status"]) is not int or redirect["status"] not in PERMANENT_RELOCATION_STATUSES:
            return None
        if any(type(redirect[key]) is not str or not redirect[key]
               or any(ord(c) < 32 or ord(c) == 127 for c in redirect[key])
               for key in _REDIRECT_KEYS - {"status"}):
            return None
        original = _dedicated_root(profile["hm_url"], profile=True)
        candidate = _dedicated_root(payload["candidate_url"])
        target, _fragment = urllib.parse.urldefrag(redirect["target_url"])
        # 단일 절대 HTTPS Location의 root 이동만 연다. 상대 링크·query·tenant는 제외한다.
        location = urllib.parse.urlparse(redirect["raw_location"])
        if location.netloc and not location.path:
            location = location._replace(path="/")
        resolved = urllib.parse.urljoin(redirect["source_url"], urllib.parse.quote(
            urllib.parse.urlunparse(location), encoding="iso-8859-1", safe=string.punctuation,
        ))
        if (not original or redirect["source_url"] != original or not candidate
                or resolved != redirect["target_url"] or target != candidate
                or urllib.parse.urlsplit(redirect["raw_location"]).scheme != "https"
                or urllib.parse.urlsplit(original).hostname == urllib.parse.urlsplit(candidate).hostname):
            return None
        if dt.datetime.fromisoformat(redirect["observed_at"]).tzinfo is None:
            return None
        for key in _PROOF_KEYS - {"redirect", "candidate_url"}:
            if type(payload[key]) is not str or _HASH_RE.fullmatch(payload[key]) is None:
                return None
        if (payload["profile_sha256"] != _hash(profile_evidence)
                or payload["redirect_sha256"] != _hash(_canonical(redirect))
                or payload["raw_location_sha256"] != _hash(redirect["raw_location"])
                or payload["scope_sha256"] != _hash(candidate)
                or payload["matched_name_sha256"] != _hash(exact_company_name_key(profile["corp_name"]))):
            return None
        return payload
    except (TypeError, ValueError, KeyError, UnicodeError):
        return None


def build_permanent_homepage_relocation(
    *, profile_evidence: str, redirect: Mapping[str, object], candidate_url: str,
    scope_sha256: str, identity_evidence_sha256: str, matched_name_sha256: str,
    registration_number_sha256: str, expected_registration_hashes: tuple[str, ...],
) -> str:
    """수집기가 검증한 원 profile·응답·이중 신원 입력에서만 proof를 만든다."""
    if registration_number_sha256 not in expected_registration_hashes:
        return ""
    payload = {
        "profile_sha256": _hash(profile_evidence), "redirect": dict(redirect),
        "redirect_sha256": _hash(_canonical(dict(redirect))),
        "raw_location_sha256": _hash(str(redirect.get("raw_location", ""))),
        "candidate_url": candidate_url, "scope_sha256": scope_sha256,
        "identity_evidence_sha256": identity_evidence_sha256,
        "matched_name_sha256": matched_name_sha256,
        "registration_number_sha256": registration_number_sha256,
    }
    if _validated_payload(profile_evidence, payload) is None:
        return ""
    proof = PERMANENT_RELOCATION_PREFIX + _canonical(payload)
    return proof if len(proof) <= PERMANENT_RELOCATION_MAX_CHARS else ""


def permanent_homepage_relocation_allows_url(
    *, profile_evidence: object, verification: object, source_url: object,
    from_host: object, to_host: object,
) -> bool:
    """생성 입력·공개 Source가 동일 원 profile와 이동 범위에 결속되는가."""
    if (type(verification) is not str or not verification.startswith(PERMANENT_RELOCATION_PREFIX)
            or len(verification) > PERMANENT_RELOCATION_MAX_CHARS
            or type(from_host) is not str or type(to_host) is not str):
        return False
    encoded = verification[len(PERMANENT_RELOCATION_PREFIX):]
    try:
        payload = json.loads(encoded)
        if _canonical(payload) != encoded or _validated_payload(profile_evidence, payload) is None:
            return False
        canonical = canonical_identity_verified_web_url(source_url)
        parsed = urllib.parse.urlsplit(canonical)
        original = urllib.parse.urlsplit(payload["redirect"]["source_url"])
        target = urllib.parse.urlsplit(payload["candidate_url"])
        return bool(canonical and not parsed.query and parsed.hostname == target.hostname
                    and from_host == original.hostname and to_host == target.hostname)
    except (TypeError, ValueError, KeyError, UnicodeError):
        return False
