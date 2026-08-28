"""보고서 delivery 캐시 조회 열쇠.

생성기 namespace 정본은 pipeline도 함께 쓰므로 ``shared``에 있고, 이
feature는 회사와 싸게 재검증한 preflight 출처 지문을 결합한다.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.shared.generation_cache_identity import (
    CacheIdentityUnavailable,
    GenerationCacheNamespace,
)


# 과거 import 이름은 유지하되 구현은 shared 정본 한 벌만 쓴다.
CacheNamespace = GenerationCacheNamespace


def _require_preflight_digest(value: str) -> str:
    clean = str(value).strip()
    if len(clean) != 64 or any(char not in "0123456789abcdef" for char in clean):
        raise CacheIdentityUnavailable("사전 출처 신원 SHA-256이 손상됐습니다")
    return clean


@dataclass(frozen=True)
class CacheLookupKey:
    """비용 통장·회사·생성기·사전 출처값이 같은 보고서 열쇠."""

    billing_bucket_id: str
    corp_id: str
    namespace_id: str
    preflight_identity_digest: str

    @classmethod
    def from_preflight(
        cls,
        *,
        billing_bucket_id: str,
        corp_id: str,
        namespace: CacheNamespace,
        preflight_identity_digest: str,
        preflight_cache_usable: bool,
    ) -> "CacheLookupKey":
        clean_bucket = str(billing_bucket_id).strip()
        clean_corp_id = str(corp_id).strip()
        if not clean_bucket or not clean_corp_id:
            raise CacheIdentityUnavailable(
                "비용 통장이나 회사 고유번호가 없어 캐시를 끕니다"
            )
        if not preflight_cache_usable:
            raise CacheIdentityUnavailable(
                "현재 사전 출처 신원을 확인할 수 없어 캐시를 끕니다"
            )
        return cls(
            clean_bucket,
            clean_corp_id,
            namespace.namespace_id,
            _require_preflight_digest(preflight_identity_digest),
        )
