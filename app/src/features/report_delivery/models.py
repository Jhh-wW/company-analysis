"""보고서 내용과 사용자 전달을 분리하는 불변 자료형."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Final

from src.features.report_delivery.cache_identity import CacheNamespace
from src.features.report_delivery.canonical import (
    canonical_digest,
    require_aware,
    require_sha256_hex,
    sha256_hex,
    utc_text,
)
from src.features.report_delivery.source_identity import SourceSnapshot
from src.shared.engine_build_identity import epoch_digest_is_valid


class DeliveryPolicyError(ValueError):
    """오래되거나 시간 경계가 틀린 내용을 새 링크로 발급하려 했다."""


@dataclass(frozen=True)
class ContentSnapshot:
    """생성 당시 본문·출처·생성기 신원을 한 번만 저장하는 내용 원본."""

    content_id: str
    payload: bytes
    payload_sha256: str
    source_snapshot_id: str
    source_identity_digest: str
    cache_namespace_id: str
    content_generated_at: dt.datetime
    actual_models: tuple[str, ...]
    # 빈 값은 배포 전 역사적 content를 읽기 위한 비권위 호환값이다. 새 생성은
    # create()에서 반드시 정상 digest를 받아 ID 자체에 결속한다.
    engine_epoch_digest: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.payload, bytes) or not self.payload:
            raise ValueError("보고서 내용 원본은 비어 있지 않은 bytes여야 합니다")
        payload_digest = require_sha256_hex(self.payload_sha256, label="보고서 본문")
        if sha256_hex(self.payload) != payload_digest:
            raise ValueError("보고서 본문 checksum이 맞지 않습니다")
        generated = require_aware(self.content_generated_at, label="내용 생성")
        if any(not value.strip() for value in self.actual_models):
            raise ValueError("실제 모델 신원에 빈 값을 넣을 수 없습니다")
        if tuple(dict.fromkeys(self.actual_models)) != self.actual_models:
            raise ValueError("실제 모델 신원을 중복 저장할 수 없습니다")
        identity_payload = {
                "payload_sha256": payload_digest,
                "source_snapshot_id": self.source_snapshot_id,
                "cache_namespace_id": self.cache_namespace_id,
                "content_generated_at": utc_text(generated, label="내용 생성"),
                "actual_models": self.actual_models,
        }
        if self.engine_epoch_digest:
            if not epoch_digest_is_valid(self.engine_epoch_digest):
                raise ValueError("내용 원본의 engine epoch 영수증이 손상됐습니다")
            identity_payload["engine_epoch_digest"] = self.engine_epoch_digest
        expected = "content_" + canonical_digest(identity_payload)
        if self.content_id != expected:
            raise ValueError("저장된 내용 ID가 본문·출처·생성기 신원과 맞지 않습니다")

    @classmethod
    def create(
        cls,
        *,
        payload: bytes,
        source_snapshot: SourceSnapshot,
        cache_namespace: CacheNamespace,
        content_generated_at: dt.datetime,
        engine_epoch_digest: str,
        actual_models: tuple[str, ...] = (),
    ) -> "ContentSnapshot":
        if not isinstance(payload, bytes) or not payload:
            raise ValueError("보고서 내용 원본은 비어 있지 않은 bytes여야 합니다")
        generated = require_aware(content_generated_at, label="내용 생성")
        models = tuple(
            dict.fromkeys(str(model).strip() for model in actual_models if str(model).strip())
        )
        payload_digest = sha256_hex(payload)
        if not epoch_digest_is_valid(engine_epoch_digest):
            raise ValueError("새 내용 원본에는 정상 engine epoch 영수증이 필요합니다")
        content_id = "content_" + canonical_digest(
            {
                "payload_sha256": payload_digest,
                "source_snapshot_id": source_snapshot.snapshot_id,
                "cache_namespace_id": cache_namespace.namespace_id,
                "content_generated_at": utc_text(generated, label="내용 생성"),
                "actual_models": models,
                "engine_epoch_digest": engine_epoch_digest,
            }
        )
        return cls(
            content_id=content_id,
            payload=payload,
            payload_sha256=payload_digest,
            source_snapshot_id=source_snapshot.snapshot_id,
            source_identity_digest=source_snapshot.identity_digest,
            cache_namespace_id=cache_namespace.namespace_id,
            content_generated_at=generated,
            actual_models=models,
            engine_epoch_digest=engine_epoch_digest,
        )


@dataclass(frozen=True)
class DeliveryPolicy:
    """내용 신선도와 새 공개 링크 이용기간을 서로 다른 시계로 계산한다."""

    content_max_age: dt.timedelta
    public_link_lifetime: dt.timedelta

    def __post_init__(self) -> None:
        if self.content_max_age <= dt.timedelta(0):
            raise ValueError("내용 최대 나이는 0보다 커야 합니다")
        if self.public_link_lifetime <= dt.timedelta(0):
            raise ValueError("공개 링크 이용기간은 0보다 커야 합니다")

    def content_is_reusable(
        self, content: ContentSnapshot, *, delivered_at: dt.datetime
    ) -> bool:
        delivered = require_aware(delivered_at, label="보고서 전달")
        generated = require_aware(content.content_generated_at, label="내용 생성")
        age = delivered - generated
        if age < dt.timedelta(0):
            raise DeliveryPolicyError("보고서 전달 시각이 내용 생성 시각보다 빠릅니다")
        # 정확히 경계에 닿으면 오래된 내용이다. 날짜 반올림으로 하루를 더
        # 살리는 일을 피하고 timestamp 한 벌로 판정한다.
        return age < self.content_max_age


@dataclass(frozen=True)
class Delivery:
    """한 내용 원본을 누구의 공개 ID로 언제 전달했는지 적은 영수증."""

    delivery_id: str
    public_id: str
    content_snapshot_id: str
    billing_bucket_id: str
    delivered_at: dt.datetime
    expires_at: dt.datetime
    cache_origin_content_id: str

    def __post_init__(self) -> None:
        if not self.public_id.strip() or not self.billing_bucket_id.strip():
            raise DeliveryPolicyError("공개 ID와 비용 통장 지문이 필요합니다")
        if not self.content_snapshot_id.strip():
            raise DeliveryPolicyError("내용 원본 ID가 필요합니다")
        delivered = require_aware(self.delivered_at, label="보고서 전달")
        expires = require_aware(self.expires_at, label="보고서 만료")
        if expires <= delivered:
            raise DeliveryPolicyError("보고서 만료 시각은 전달 시각보다 늦어야 합니다")
        if self.cache_origin_content_id not in ("", self.content_snapshot_id):
            raise DeliveryPolicyError("캐시 출처가 전달한 내용 원본과 다릅니다")
        expected = "delivery_" + canonical_digest(
            {
                "public_id": self.public_id,
                "content_snapshot_id": self.content_snapshot_id,
                "billing_bucket_id": self.billing_bucket_id,
                "delivered_at": utc_text(delivered, label="보고서 전달"),
                "expires_at": utc_text(expires, label="보고서 만료"),
                "cache_origin_content_id": self.cache_origin_content_id,
            }
        )
        if self.delivery_id != expected:
            raise DeliveryPolicyError("저장된 delivery ID가 전달 기록과 맞지 않습니다")

    @classmethod
    def issue(
        cls,
        *,
        public_id: str,
        billing_bucket_id: str,
        content: ContentSnapshot,
        delivered_at: dt.datetime,
        policy: DeliveryPolicy,
        reused_from_cache: bool,
    ) -> "Delivery":
        clean_public_id = str(public_id).strip()
        clean_bucket = str(billing_bucket_id).strip()
        if not clean_public_id or not clean_bucket:
            raise DeliveryPolicyError("공개 ID와 비용 통장 지문이 필요합니다")
        delivered = require_aware(delivered_at, label="보고서 전달")
        if not policy.content_is_reusable(content, delivered_at=delivered):
            raise DeliveryPolicyError("내용 최대 나이를 지난 보고서는 새로 전달할 수 없습니다")
        expires = delivered + policy.public_link_lifetime
        origin = content.content_id if reused_from_cache else ""
        delivery_id = "delivery_" + canonical_digest(
            {
                "public_id": clean_public_id,
                "content_snapshot_id": content.content_id,
                "billing_bucket_id": clean_bucket,
                "delivered_at": utc_text(delivered, label="보고서 전달"),
                "expires_at": utc_text(expires, label="보고서 만료"),
                "cache_origin_content_id": origin,
            }
        )
        return cls(
            delivery_id=delivery_id,
            public_id=clean_public_id,
            content_snapshot_id=content.content_id,
            billing_bucket_id=clean_bucket,
            delivered_at=delivered,
            expires_at=expires,
            cache_origin_content_id=origin,
        )


PDF_CHANNEL: Final[str] = "pdf"
