"""보고서 생성 결과를 바꾸는 배포·모델·설정 namespace 정본.

pipeline은 유료 호출 전에 이 신원을 알아야 중복 생성을 합칠 수 있고,
report_delivery는 같은 신원을 ContentSnapshot과 캐시에 영속화해야 한다.
두 feature가 각자 해시를 만들면 같은 실행을 서로 다른 생성기로 보거나,
반대로 다른 설정의 결과를 같은 캐시로 섞게 되므로 이 모듈 한 벌만 쓴다.
"""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


class CacheIdentityUnavailable(ValueError):
    """생성기 신원을 확정할 수 없어 캐시를 읽거나 쓸 수 없다."""


_UNKNOWN_RELEASE_IDS = frozenset({"", "unknown", "none", "unavailable", "null"})


def _known_release_id(value: str) -> bool:
    return str(value).strip().lower() not in _UNKNOWN_RELEASE_IDS


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CacheIdentityUnavailable(
            "생성기 지문에는 유한한 JSON 값만 쓸 수 있습니다"
        ) from exc


def _digest(value: Any) -> str:
    return hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _normalized(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise CacheIdentityUnavailable(
                "생성기 지문에 NaN이나 무한대를 넣을 수 없습니다"
            )
        return value
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, Mapping):
        return {
            str(key).strip(): _normalized(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    ):
        return [_normalized(item) for item in value]
    raise CacheIdentityUnavailable(
        f"생성기 지문에 넣을 수 없는 값입니다: {type(value).__name__}"
    )


def _require_digest(value: str, *, label: str) -> str:
    clean = str(value).strip()
    if len(clean) != 64 or any(char not in "0123456789abcdef" for char in clean):
        raise CacheIdentityUnavailable(f"{label} SHA-256이 손상됐습니다")
    return clean


@dataclass(frozen=True)
class GenerationCacheNamespace:
    """provider 호출 전에 확정 가능한 생성기 release의 불변 namespace."""

    namespace_id: str
    product: str
    schema_version: str
    deployment_revision: str
    image_digest: str
    model_identity_sha256: str
    settings_sha256: str

    def __post_init__(self) -> None:
        if not self.product.strip() or not self.schema_version.strip():
            raise CacheIdentityUnavailable("제품과 보고서 schema 신원이 필요합니다")
        if not (
            _known_release_id(self.deployment_revision)
            or _known_release_id(self.image_digest)
        ):
            raise CacheIdentityUnavailable(
                "배포 revision이나 image digest를 확인할 수 없어 캐시를 끕니다"
            )
        model_digest = _require_digest(
            self.model_identity_sha256,
            label="요청 모델 신원",
        )
        settings_digest = _require_digest(self.settings_sha256, label="출력 설정")
        expected = "cache_" + _digest(
            {
                "product": self.product,
                "schema_version": self.schema_version,
                "deployment_revision": self.deployment_revision,
                "image_digest": self.image_digest,
                "model_identity_sha256": model_digest,
                "settings_sha256": settings_digest,
            }
        )
        if self.namespace_id != expected:
            raise CacheIdentityUnavailable(
                "저장된 캐시 namespace ID가 생성기 신원과 맞지 않습니다"
            )

    @classmethod
    def create(
        cls,
        *,
        product: str,
        schema_version: str,
        deployment_revision: str = "",
        image_digest: str = "",
        requested_models: Mapping[str, str],
        output_settings: Mapping[str, Any] | None = None,
    ) -> "GenerationCacheNamespace":
        clean_product = str(product).strip()
        clean_schema = str(schema_version).strip()
        revision = str(deployment_revision).strip()
        image = str(image_digest).strip()
        if not clean_product or not clean_schema:
            raise CacheIdentityUnavailable("제품과 보고서 schema 신원이 필요합니다")
        if not (_known_release_id(revision) or _known_release_id(image)):
            raise CacheIdentityUnavailable(
                "배포 revision이나 image digest를 확인할 수 없어 캐시를 끕니다"
            )
        models = {
            str(role).strip(): str(model).strip()
            for role, model in requested_models.items()
            if str(role).strip() and str(model).strip()
        }
        if not models:
            raise CacheIdentityUnavailable(
                "요청 모델 신원을 확인할 수 없어 캐시를 끕니다"
            )
        settings = _normalized(output_settings or {})
        model_digest = _digest(models)
        settings_digest = _digest(settings)
        namespace_id = "cache_" + _digest(
            {
                "product": clean_product,
                "schema_version": clean_schema,
                "deployment_revision": revision,
                "image_digest": image,
                "model_identity_sha256": model_digest,
                "settings_sha256": settings_digest,
            }
        )
        return cls(
            namespace_id=namespace_id,
            product=clean_product,
            schema_version=clean_schema,
            deployment_revision=revision,
            image_digest=image,
            model_identity_sha256=model_digest,
            settings_sha256=settings_digest,
        )
