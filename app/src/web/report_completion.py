"""FULL 완료 경계 전용 — 출고 권위(ReleaseAuthority) 발급 지문 대조.

FULL(``release_mode == "FULL"``) 생성물만 이 모듈을 거친다. demo·v1·SHADOW·
ENFORCE_NO_PARTIAL은 ``routers.reports.finalize_new_report_delivery``가
``release_mode``로 미리 갈라 이 모듈 근처에도 오지 않는다 —
「FULL 밖 demo/non-FULL 동작은 불변이다」.

★ 여기서 하지 않는 것 (설계 경계):
  - 원자 SQLite 거래를 새로 열지 않는다. ``reports.finalize_new_report_delivery``가
    이미 열어 둔 connection에 이 모듈의 순수 함수들을 끼워 쓴다.
  - ``cost_store.record_run_costs``(내부 AI 원가)를 건드리지 않는다 — 그건
    ``job_runtime._run_job``의 파이프라인 직후 독립 거래로 그대로 남는다.
  - non-FULL(demo/v1/SHADOW) 경로를 위한 대체 출고 권위를 발명하지 않는다.
"""

from __future__ import annotations

import datetime as dt

from src.features.pipeline.port import Report
from src.features.report_delivery import authority as authority_store
from src.features.report_delivery.cache_identity import CacheLookupKey
from src.features.report_delivery.models import ContentSnapshot, Delivery
from src.features.report_delivery.singleflight import LeaseKey
from src.features.report_delivery.source_identity import SourceSnapshot
from src.features.storage import reports as report_store
from src.shared import engine_build_identity as build_identity_contract
from src.shared.automatic_release_record import AutomaticReleaseRecord
from src.shared.report_generation.canonical import (
    assert_report_matches_generation_evidence,
)
from src.shared.report_generation.models import (
    GenerationProducerEvidence,
    assert_canonical_producer_evidence,
)


class ReleaseIdentityMismatch(RuntimeError):
    """FULL 출고의 회사·epoch 결속이 exact하지 않아 권위를 발급할 수 없다."""


def require_release_evidence(report: Report) -> GenerationProducerEvidence:
    """FULL Report의 실제 producer 객체를 실제 payload·manifest와 다시 결속한다.

    문자열 비교가 아니라 ``assert_report_matches_generation_evidence``의 실제
    판정 로직(manifest checksum·공개 content 지문·9장 packet 지문까지 전부
    재계산해 대조)을 그대로 통과해야 한다.
    """

    evidence = report.generation_evidence
    if type(evidence) is not GenerationProducerEvidence:
        raise ReleaseIdentityMismatch(
            "FULL 완료에는 실제 generation producer evidence가 필요합니다"
        )
    payload = report_store.report_to_dict(report)
    try:
        assert_report_matches_generation_evidence(
            payload,
            evidence,
            manifest_bytes=report.public_structure_manifest.encode("utf-8"),
        )
    except (TypeError, ValueError) as exc:
        raise ReleaseIdentityMismatch(
            "FULL Report와 generation producer evidence의 결속이 깨졌습니다"
        ) from exc
    return evidence


def assert_release_company_identity(
    *,
    corp_id: str,
    output_report: Report,
    evidence: GenerationProducerEvidence,
) -> str:
    """blob intent를 만들기 전에 회사 ID 3자를 exact 비교한다 (P1-2).

    normalized corp_id·``output_report.company_id``·``evidence.company_id``
    셋 중 하나라도 다르면 그 자리에서 거절한다 — 호출자가 잘못된 회사로
    출고를 요청해도 evidence 안의 값만 믿고 넘어가지 않는다.
    """

    normalized_corp_id = str(corp_id).strip()
    if (
        not normalized_corp_id
        or normalized_corp_id != output_report.company_id
        or normalized_corp_id != evidence.company_id
    ):
        raise ReleaseIdentityMismatch(
            "FULL 출고의 회사 ID가 corp_id·본문·생성 증거 사이에서 다릅니다"
        )
    return normalized_corp_id


def assert_release_build_identity(
    *,
    evidence: GenerationProducerEvidence,
    frozen_build_identity: build_identity_contract.EngineBuildIdentity,
) -> None:
    """blob intent를 만들기 전에 evidence epoch와 완료 epoch를 exact 비교한다 (P1-1).

    세 번째 다리(``ContentSnapshot.engine_epoch_digest``)는 이 시점에는 아직
    존재하지 않는(owner 경로는 Content를 이 검사 뒤에 새로 만든다) 값이라
    여기서 비교할 수 없다 — ``ContentSnapshot.create``가 같은
    ``frozen_build_identity.epoch_digest``로만 Content를 만들므로 구성상 항상
    같다. 그 불변식은 :func:`assert_release_content_identity`가 저장 직후
    다시 확인한다.
    """

    if evidence.build_identity_sha256 != frozen_build_identity.epoch_digest:
        raise ReleaseIdentityMismatch(
            "FULL 출고의 생성 증거 epoch와 이번 완료의 engine epoch가 다릅니다"
        )


def assert_release_preflight_identity(
    *,
    evidence: GenerationProducerEvidence,
    preflight_identity_digest: str,
) -> str:
    """blob intent 전에 생성 근거와 cache/single-flight 출처 세대를 맞춘다.

    ``evidence_generation_sha256``는 작가가 실제로 받은 9장 근거 packet의
    세대이고, ``preflight_identity_digest``는 같은 packet을 만들기 전에
    cache 조회와 owner lease에 쓴 세대다. 둘이 다르면 다른 자료로 만든
    보고서를 이번 요청의 cache 원본처럼 출고하게 되므로 FULL은 빈 값도
    허용하지 않는다.
    """

    preflight_digest = str(preflight_identity_digest).strip()
    if (
        not preflight_digest
        or evidence.evidence_generation_sha256 != preflight_digest
    ):
        raise ReleaseIdentityMismatch(
            "FULL 출고의 생성 근거 세대와 생성 전 출처 지문이 다릅니다"
        )
    return preflight_digest


def assert_release_stored_source_identity(
    *,
    evidence: GenerationProducerEvidence,
    source: SourceSnapshot,
    cache_key: CacheLookupKey | None,
    reuse_singleflight_key: LeaseKey | None,
) -> None:
    """저장 source·보고서 evidence·cache/lease 열쇠를 exact 대조한다.

    사전 검사는 잘못 전달된 인자를 blob 전에 닫고, 이 검사는 adapter나 저장
    배선이 다른 SourceSnapshot을 붙이는 경우를 같은 출고 거래 안에서 다시
    닫는다. cache hit과 waiter는 둘 중 실제로 사용한 권위 열쇠도 같은 세대여야
    한다.
    """

    generation_digest = evidence.evidence_generation_sha256
    if (
        not isinstance(source, SourceSnapshot)
        or source.preflight_identity_digest != generation_digest
    ):
        raise ReleaseIdentityMismatch(
            "FULL 출고의 저장된 출처와 생성 근거 세대가 다릅니다"
        )
    if (
        cache_key is not None
        and cache_key.preflight_identity_digest != generation_digest
    ):
        raise ReleaseIdentityMismatch(
            "FULL 출고의 캐시 열쇠와 생성 근거 세대가 다릅니다"
        )
    if (
        reuse_singleflight_key is not None
        and reuse_singleflight_key.source_identity_digest != generation_digest
    ):
        raise ReleaseIdentityMismatch(
            "FULL 출고의 single-flight 완료 열쇠와 생성 근거 세대가 다릅니다"
        )


def assert_release_content_identity(
    *,
    evidence: GenerationProducerEvidence,
    frozen_build_identity: build_identity_contract.EngineBuildIdentity,
    content: ContentSnapshot,
) -> None:
    """저장된 Content까지 포함한 epoch 3자 exact 비교를 권위 발급 직전에 다시 확인한다."""

    if (
        evidence.build_identity_sha256 != frozen_build_identity.epoch_digest
        or content.engine_epoch_digest != frozen_build_identity.epoch_digest
    ):
        raise ReleaseIdentityMismatch(
            "FULL 출고의 저장된 Content epoch가 생성 증거·완료 engine epoch와 다릅니다"
        )


def issue_owner_release_authority(
    *,
    evidence: GenerationProducerEvidence,
    delivery: Delivery,
    content: ContentSnapshot,
    artifact_id: str,
    automatic_release: AutomaticReleaseRecord,
    charge_run_id: str,
    charge_decision_sha256: str,
    issued_at: dt.datetime,
) -> authority_store.ReleaseAuthority:
    """실제 저장 DTO만으로 OWNER 출고 권위를 순수 계산해 돌려준다.

    DB에 쓰지 않는다 — 호출자가 이미 연 거래에
    ``authority_store.save_release_authority(conn, authority)``로 직접 쓴다.
    """

    producer_sha256 = assert_canonical_producer_evidence(evidence)
    return authority_store.ReleaseAuthority.issue_owner(
        public_id=delivery.public_id,
        delivery_id=delivery.delivery_id,
        company_id=evidence.company_id,
        billing_bucket_id=delivery.billing_bucket_id,
        content_snapshot_id=content.content_id,
        artifact_id=artifact_id,
        report_payload_sha256=content.payload_sha256,
        producer_evidence_sha256=producer_sha256,
        assessment_sha256=evidence.assessment_sha256,
        # ★ 출고 권위가 가리키는 「공개본」은 공개 봉인 projection이다.
        #   필드 이름은 그대로 두고
        #   «의미»만 정한다 — 여기 실리던 지문 A(`public_content_sha256`)는 렌더
        #   이전 기대값이라 감사 장부 바꿔치기를 못 본다. 지문 A는 생산 증거
        #   안에 그대로 남아 renderer 위조 차단 몫을 계속 한다.
        public_content_sha256=evidence.public_projection_sha256,
        public_manifest_sha256=evidence.public_manifest_sha256,
        evidence_generation_sha256=evidence.evidence_generation_sha256,
        build_identity_sha256=evidence.build_identity_sha256,
        automatic_release_sha256=automatic_release.record_sha256,
        charge_run_id=charge_run_id,
        charge_decision_sha256=charge_decision_sha256,
        issued_at=issued_at,
    )


def _release_authority_source_identity(
    *,
    evidence: GenerationProducerEvidence,
    content: ContentSnapshot,
    artifact_id: str,
    automatic_release_sha256: str,
) -> dict[str, str]:
    return {
        "company_id": evidence.company_id,
        "content_snapshot_id": content.content_id,
        "artifact_id": str(artifact_id).strip(),
        "report_payload_sha256": content.payload_sha256,
        "producer_evidence_sha256": assert_canonical_producer_evidence(evidence),
        "assessment_sha256": evidence.assessment_sha256,
        "public_content_sha256": evidence.public_projection_sha256,
        "public_manifest_sha256": evidence.public_manifest_sha256,
        "evidence_generation_sha256": evidence.evidence_generation_sha256,
        "build_identity_sha256": evidence.build_identity_sha256,
        "automatic_release_sha256": str(automatic_release_sha256).strip(),
    }


def assert_release_authority_identity(
    *,
    authority: authority_store.ReleaseAuthority,
    expected_kind: authority_store.ReleaseAuthorityKind,
    evidence: GenerationProducerEvidence,
    delivery: Delivery,
    content: ContentSnapshot,
    artifact_id: str,
    automatic_release_sha256: str,
) -> None:
    """FULL 권위가 실제 producer·본문·PDF·승인 기록과 exact한지 다시 본다.

    ``load_release_authority*``는 DB의 delivery/content/artifact 결속과 REUSE
    원본 계보를 검증한다. 이 함수는 그 위에 현재 FULL 보고서의 canonical
    producer evidence와 자동승인 기록까지 대조해, COMPLETE 재시도가 단지
    같은 공개 ID의 권위 행이 있다는 이유만으로 성공하지 못하게 한다.
    """

    if type(authority) is not authority_store.ReleaseAuthority:
        raise ReleaseIdentityMismatch("FULL 출고 권위 객체가 올바르지 않습니다")
    expected = {
        "kind": expected_kind,
        "public_id": delivery.public_id,
        "delivery_id": delivery.delivery_id,
        "billing_bucket_id": delivery.billing_bucket_id,
        **_release_authority_source_identity(
            evidence=evidence,
            content=content,
            artifact_id=artifact_id,
            automatic_release_sha256=automatic_release_sha256,
        ),
    }
    if any(getattr(authority, name) != value for name, value in expected.items()):
        raise ReleaseIdentityMismatch(
            "FULL 출고 권위가 producer·본문·PDF·자동승인 원본과 다릅니다"
        )


def assert_owner_release_authority_identity(
    *,
    authority: authority_store.ReleaseAuthority,
    evidence: GenerationProducerEvidence,
    billing_bucket_id: str,
    content: ContentSnapshot,
    artifact_id: str,
    automatic_release_sha256: str | None = None,
) -> None:
    """재사용 선택이 가리킨 OWNER 권위와 공유 원본 신원을 exact 대조한다."""

    if type(authority) is not authority_store.ReleaseAuthority:
        raise ReleaseIdentityMismatch("재사용할 OWNER 출고 권위 객체가 올바르지 않습니다")
    expected = {
        "kind": authority_store.ReleaseAuthorityKind.OWNER,
        "billing_bucket_id": str(billing_bucket_id).strip(),
        **_release_authority_source_identity(
            evidence=evidence,
            content=content,
            artifact_id=artifact_id,
            automatic_release_sha256=(
                authority.automatic_release_sha256
                if automatic_release_sha256 is None
                else automatic_release_sha256
            ),
        ),
    }
    if any(getattr(authority, name) != value for name, value in expected.items()):
        raise ReleaseIdentityMismatch(
            "재사용할 OWNER 권위가 producer·본문·PDF·자동승인 원본과 다릅니다"
        )


def issue_reuse_release_authority(
    *,
    origin: authority_store.ReleaseAuthority,
    evidence: GenerationProducerEvidence,
    delivery: Delivery,
    content: ContentSnapshot,
    artifact_id: str,
    automatic_release: AutomaticReleaseRecord,
    charge_run_id: str,
    charge_decision_sha256: str,
    issued_at: dt.datetime,
) -> authority_store.ReleaseAuthority:
    """검증된 OWNER의 producer·본문·최초 PDF를 새 Delivery가 상속한다."""

    assert_owner_release_authority_identity(
        authority=origin,
        evidence=evidence,
        billing_bucket_id=delivery.billing_bucket_id,
        content=content,
        artifact_id=artifact_id,
        automatic_release_sha256=automatic_release.record_sha256,
    )
    return authority_store.ReleaseAuthority.issue_reuse(
        origin=origin,
        public_id=delivery.public_id,
        delivery_id=delivery.delivery_id,
        billing_bucket_id=delivery.billing_bucket_id,
        automatic_release_sha256=automatic_release.record_sha256,
        charge_run_id=charge_run_id,
        charge_decision_sha256=charge_decision_sha256,
        issued_at=issued_at,
    )


__all__ = [
    "ReleaseIdentityMismatch",
    "assert_release_build_identity",
    "assert_release_authority_identity",
    "assert_owner_release_authority_identity",
    "assert_release_company_identity",
    "assert_release_content_identity",
    "assert_release_preflight_identity",
    "assert_release_stored_source_identity",
    "issue_owner_release_authority",
    "issue_reuse_release_authority",
    "require_release_evidence",
]
