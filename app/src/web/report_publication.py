"""저장된 보고서를 «출고 완료 또는 진짜 legacy»로 판정하는 단일 경계."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping

from src.features.admin_dashboard import store as dashboard_store
from src.features.cost_tracking import store as cost_store
from src.features.export_pdf import release_store as pdf_release_store
from src.features.export_pdf.automatic_release import report_sha256
from src.features.report_delivery import artifact as artifact_store
from src.features.report_delivery import authority as authority_store
from src.features.report_delivery import store as delivery_store
from src.features.storage import reports as report_store
from src.shared.report_evidence.constants import ReleaseMode
from src.web import report_completion


def report_payload_is_true_legacy(report: object) -> bool:
    """출고 도입 전 payload만 legacy 호환 대상으로 인정한다.

    신규 FULL producer는 공개 봉인 지문을 생성 증거에 남긴다. lifecycle·intent·
    Delivery 행이 모두 유실됐다는 이유로 이 도장이 있는 raw를 옛 보고서로
    격하하면 안 된다. 반대로 이 필드가 없던 실제 과거 저장본은 그대로 허용한다.
    """

    evidence = getattr(report, "generation_evidence", None)
    return evidence is None or not str(
        getattr(evidence, "public_projection_sha256", "") or ""
    ).strip()


def raw_report_payload_is_true_legacy(payload_json: str) -> bool:
    """현재 정규화/renderer를 부르지 않고 저장 JSON의 시대 도장만 읽는다."""

    try:
        payload = json.loads(str(payload_json))
    except (TypeError, ValueError) as exc:
        raise ValueError("저장 보고서 JSON을 읽을 수 없습니다") from exc
    if not isinstance(payload, Mapping):
        raise ValueError("저장 보고서 JSON 최상위는 객체여야 합니다")
    evidence = payload.get("generation_evidence")
    if evidence is None:
        return True
    if not isinstance(evidence, Mapping):
        raise ValueError("생성 증거 JSON은 객체여야 합니다")
    return not str(evidence.get("public_projection_sha256") or "").strip()


def _delivery_is_durably_published(
    conn: sqlite3.Connection,
    report_id: str,
    *,
    require_full_authority: bool,
) -> bool:
    """Delivery·본문·PDF 승인과 새 FULL 권위를 한 DB snapshot에서 확인한다."""

    delivery = delivery_store.load_delivery_by_public_id(conn, report_id)
    if delivery is None:
        return False
    content = delivery_store.load_content_snapshot(
        conn, delivery.content_snapshot_id
    )
    if content is None:  # pragma: no cover - load_delivery가 먼저 검증한다
        return False
    try:
        report = report_store.report_from_json(content.payload.decode("utf-8"))
    except (UnicodeDecodeError, TypeError, ValueError):
        return False
    artifact = artifact_store.artifact_for_delivery(
        conn, delivery_id=delivery.delivery_id
    )
    pointer = artifact.blob_pointer if artifact is not None else None
    if artifact is None or pointer is None:
        return False
    released = pdf_release_store.load_automatic_release_record(
        conn,
        report_id=report_id,
        report_sha256=report_sha256(report),
        pdf_sha256=pointer.sha256,
        checker_version=artifact.version.checker_version,
    )
    if released is None:
        return False
    if report.release_mode != ReleaseMode.FULL.value:
        return True

    # 이 배포의 staging→published 생명주기를 가진 FULL은 권위 도입 뒤의
    # 신규 출고다. 그 이전에 이미 발급된 FULL 링크(생명주기 사건 없음)는
    # 소급 차단하지 않되, 권위 행이 있으면 언제나 정확히 검증한다.
    authority = authority_store.load_release_authority_by_public_id(
        conn, report_id
    )
    if authority is None:
        return not require_full_authority
    evidence = report_completion.require_release_evidence(report)
    report_completion.assert_release_authority_identity(
        authority=authority,
        expected_kind=(
            authority_store.ReleaseAuthorityKind.REUSE
            if delivery.cache_origin_content_id
            else authority_store.ReleaseAuthorityKind.OWNER
        ),
        evidence=evidence,
        delivery=delivery,
        content=content,
        artifact_id=artifact.artifact_id,
        automatic_release_sha256=released.record_sha256,
    )
    charge = cost_store.load_automatic_release_charge(
        conn,
        run_id=authority.charge_run_id,
        automatic_release_sha256=released.record_sha256,
    )
    if charge is None:
        return False
    return authority.charge_decision_sha256 == cost_store.charge_decision_sha256(
        run_id=authority.charge_run_id,
        automatic_release_sha256=released.record_sha256,
        decision=charge,
    )


def report_is_published_or_legacy(
    conn: sqlite3.Connection,
    report_id: str,
) -> bool:
    """완료 Delivery와 publish 생명주기 또는 진짜 옛 보고서만 허용한다.

    ``intent is None``만 legacy로 해석하면 raw staging 뒤 intent 행이 사라진
    보고서를 공개한다. 반대로 COMPLETE 열만 보고 staging 사건을 무시해도 부분
    손상된 raw가 열린다. 신규 저장의 ``report_staged``가 마지막 생명주기
    사건이면 intent 유무와 관계없이 출고 전 본문이다.
    """

    lifecycle = dashboard_store.report_publication_lifecycle(conn, report_id)
    if lifecycle == dashboard_store.REPORT_EVENT_STAGED:
        return False
    intent = delivery_store.load_delivery_intent(conn, report_id)
    if intent is None and not lifecycle:
        # Delivery 도입 전 저장본만 진짜 legacy다. 다만 intent 행만 유실된
        # 정상 Delivery가 있으면 아래 완전성 검사를 우선해야 같은 공개 ID의
        # 승인 원본을 계속 쓴다.
        if delivery_store.load_delivery_by_public_id(conn, report_id) is None:
            row = conn.execute(
                f"SELECT payload_json FROM {report_store.TABLE_REPORTS} WHERE report_id=?",
                (report_id,),
            ).fetchone()
            if row is None:
                return False
            return raw_report_payload_is_true_legacy(str(row[0]))
    elif intent is not None and intent.state != delivery_store.DELIVERY_INTENT_COMPLETE:
        return False
    return _delivery_is_durably_published(
        conn,
        report_id,
        require_full_authority=(
            lifecycle == dashboard_store.REPORT_EVENT_PUBLISHED
        ),
    )


__all__ = [
    "raw_report_payload_is_true_legacy",
    "report_is_published_or_legacy",
    "report_payload_is_true_legacy",
]
