"""보고서 완료 거래의 정확한 재확인 경계.

SQLite ``commit``은 디스크 반영에는 성공했지만 호출자에게 성공 응답을 돌려주기
전에 연결이 끊길 수 있다. 그때 같은 보고서를 다시 만들거나 다시 차감하지 않고,
본문·PDF·자동승인·청구를 모두 묶은 :class:`ReleaseAuthority`를 읽어 정확히
같을 때만 이미 끝난 성공으로 인정한다.

이 모듈은 여러 feature를 조정하는 웹 경계다. 각 feature 안에 서로의 저장 규칙을
복제하지 않는다.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import TypeAlias

from src.features.cost_tracking import store as cost_store
from src.features.export_pdf import release_store as pdf_release_store
from src.features.report_delivery import artifact as artifact_store
from src.features.report_delivery import authority as authority_store
from src.features.report_delivery import store as delivery_store
from src.features.storage import db as storage_db
from src.shared.automatic_release_record import AutomaticReleaseRecord


class ReportCompletionError(RuntimeError):
    """보고서 완료 상태를 한 덩어리로 증명하지 못했다."""


class ReportCompletionCommitUncertain(ReportCompletionError):
    """commit 응답 유실 뒤 정확한 완료 영수증을 찾지 못했다."""


@dataclass(frozen=True)
class ReportCompletionReceipt:
    """공개 가능한 보고서 한 건의 최소 완결 영수증."""

    authority: authority_store.ReleaseAuthority
    automatic_release: AutomaticReleaseRecord
    charge: cost_store.CustomerChargeDecision

    def __post_init__(self) -> None:
        if type(self.authority) is not authority_store.ReleaseAuthority:
            raise TypeError("완료 영수증에는 정확한 ReleaseAuthority가 필요합니다")
        if type(self.automatic_release) is not AutomaticReleaseRecord:
            raise TypeError("완료 영수증에는 정확한 AutomaticReleaseRecord가 필요합니다")
        if type(self.charge) is not cost_store.CustomerChargeDecision:
            raise TypeError("완료 영수증에는 정확한 CustomerChargeDecision이 필요합니다")


CompletionStage: TypeAlias = Callable[
    [sqlite3.Connection], authority_store.ReleaseAuthority
]


def assert_exact_report_completion(
    conn: sqlite3.Connection,
    expected: authority_store.ReleaseAuthority,
) -> ReportCompletionReceipt:
    """한 연결에서 저장된 완료 구성요소가 기대한 권위와 모두 같은지 확인한다."""

    if type(expected) is not authority_store.ReleaseAuthority:
        raise TypeError("재확인에는 정확한 ReleaseAuthority가 필요합니다")
    stored = authority_store.load_release_authority(conn, expected.authority_id)
    if stored != expected:
        raise ReportCompletionError("저장된 출고 권위가 기대한 완료 영수증과 다릅니다")
    by_public_id = authority_store.load_release_authority_by_public_id(
        conn,
        expected.public_id,
    )
    if by_public_id != expected:
        raise ReportCompletionError("공개 ID가 다른 출고 권위를 가리킵니다")

    intent = delivery_store.load_delivery_intent(conn, expected.public_id)
    if (
        intent is None
        or intent.state != delivery_store.DELIVERY_INTENT_COMPLETE
        or intent.public_id != expected.public_id
    ):
        raise ReportCompletionError("보고서 전달 의무가 완료 상태가 아닙니다")

    artifact = artifact_store.load_artifact_metadata(conn, expected.artifact_id)
    if (
        artifact is None
        or artifact.original_state is not artifact_store.ArtifactOriginalState.STORED
        or artifact.content_snapshot_id != expected.content_snapshot_id
        or artifact.blob_pointer is None
    ):
        raise ReportCompletionError("최초 PDF 원본이 출고 권위와 결속되지 않았습니다")
    automatic_release = pdf_release_store.load_automatic_release_record(
        conn,
        report_id=expected.public_id,
        report_sha256=expected.report_payload_sha256,
        pdf_sha256=artifact.blob_pointer.sha256,
        checker_version=artifact.version.checker_version,
    )
    if (
        automatic_release is None
        or automatic_release.record_sha256 != expected.automatic_release_sha256
    ):
        raise ReportCompletionError("자동승인 기록이 출고 권위와 다릅니다")

    charge = cost_store.load_automatic_release_charge(
        conn,
        run_id=expected.charge_run_id,
        automatic_release_sha256=expected.automatic_release_sha256,
    )
    if charge is None:
        raise ReportCompletionError("출고 권위에 결속된 청구 결정을 찾지 못했습니다")
    charge_digest = cost_store.charge_decision_sha256(
        run_id=expected.charge_run_id,
        automatic_release_sha256=expected.automatic_release_sha256,
        decision=charge,
    )
    if charge_digest != expected.charge_decision_sha256:
        raise ReportCompletionError("청구 결정 지문이 출고 권위와 다릅니다")
    return ReportCompletionReceipt(
        authority=stored,
        automatic_release=automatic_release,
        charge=charge,
    )


def _load_exact_after_commit_error(
    expected: authority_store.ReleaseAuthority,
    *,
    db_path: Path | None,
) -> ReportCompletionReceipt:
    with storage_db.connect_readonly_existing(db_path) as conn:
        if conn is None:
            raise ReportCompletionError("완료 거래를 재확인할 DB가 없습니다")
        return assert_exact_report_completion(conn, expected)


def commit_report_completion(
    stage: CompletionStage,
    *,
    db_path: Path | None = None,
) -> ReportCompletionReceipt:
    """모든 완료 쓰기를 한 거래로 확정하고 응답 유실은 exact readback으로 복구한다.

    ``stage``는 전달 의무 완료까지 필요한 모든 행을 주어진 연결에 쓰고, 마지막에
    그 행들을 결속한 정확한 ``ReleaseAuthority``를 반환해야 한다. 이 함수는
    ``stage`` 중간 실패에는 재확인을 시도하지 않는다. 권위까지 만들어진 뒤 commit
    응답만 사라진 경우에만 새 읽기 연결로 같은 영수증을 찾는다.
    """

    if not callable(stage):
        raise TypeError("완료 거래에는 stage 함수가 필요합니다")
    expected: authority_store.ReleaseAuthority | None = None
    receipt: ReportCompletionReceipt | None = None
    commit_attempted = False
    try:
        with storage_db.connect(db_path) as conn:
            # schema bootstrap이 시작한 거래와 사용자 완료 거래를 섞지 않는다.
            conn.commit()
            conn.execute("BEGIN IMMEDIATE")
            candidate = stage(conn)
            if type(candidate) is not authority_store.ReleaseAuthority:
                raise TypeError("완료 stage는 정확한 ReleaseAuthority를 반환해야 합니다")
            expected = candidate
            receipt = assert_exact_report_completion(conn, expected)
            # context manager의 암묵 commit만 기다리면 어느 단계가 실패했는지와
            # commit 응답 유실인지 구분할 수 없다. 여기서 명시적으로 경계를 둔다.
            commit_attempted = True
            conn.commit()
        if receipt is None:  # pragma: no cover - 위 계약의 방어선
            raise ReportCompletionError("완료 영수증을 만들지 못했습니다")
        return receipt
    except Exception as exc:
        if expected is None or not commit_attempted:
            raise
        try:
            recovered = _load_exact_after_commit_error(expected, db_path=db_path)
        except Exception:
            raise ReportCompletionCommitUncertain(
                "commit 결과를 정확한 완료 영수증으로 재확인하지 못했습니다"
            ) from exc
        if recovered.authority != expected:  # pragma: no cover - loader 계약의 방어선
            raise ReportCompletionCommitUncertain(
                "commit 뒤 다른 완료 영수증이 저장됐습니다"
            ) from exc
        return recovered


__all__ = [
    "CompletionStage",
    "ReportCompletionCommitUncertain",
    "ReportCompletionError",
    "ReportCompletionReceipt",
    "assert_exact_report_completion",
    "commit_report_completion",
]
