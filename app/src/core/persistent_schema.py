"""운영 SQLite의 feature-owned 영속 스키마 단일 registry.

storage 연결과 복구 검증이 같은 목록을 실행해야, 실제 기능이 지연 생성한 표나
trigger가 백업에서 빠져도 정상으로 오판하지 않는다. Registry에는 운영 DB에
영속되는 feature bootstrap만 넣고 시험/임시 DB 전용 스키마는 넣지 않는다.
"""

from __future__ import annotations

import importlib
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Final


@dataclass(frozen=True)
class PersistentSchemaBootstrap:
    label: str
    module_name: str
    relative_path: str
    callable_name: str = "ensure_schema"


PERSISTENT_SCHEMA_BOOTSTRAPS: Final[tuple[PersistentSchemaBootstrap, ...]] = (
    PersistentSchemaBootstrap(
        "OAuth state 발급 원장", "src.features.auth.state_store",
        "src/features/auth/state_store.py",
    ),
    PersistentSchemaBootstrap(
        "비용 예약 원장", "src.features.budget.spend_store",
        "src/features/budget/spend_store.py",
    ),
    PersistentSchemaBootstrap(
        "provider 건강", "src.features.provider_health.store",
        "src/features/provider_health/store.py",
    ),
    PersistentSchemaBootstrap(
        "관측 수명주기", "src.features.observability.lifecycle",
        "src/features/observability/lifecycle.py",
    ),
    PersistentSchemaBootstrap(
        "관리자 변경 감사", "src.features.observability.admin_audit_store",
        "src/features/observability/admin_audit_store.py",
    ),
    PersistentSchemaBootstrap(
        "백업 실행 상태", "src.features.backup.status",
        "src/features/backup/status.py",
    ),
    PersistentSchemaBootstrap(
        "관리 KPI", "src.features.admin_dashboard.kpi",
        "src/features/admin_dashboard/kpi.py",
    ),
    PersistentSchemaBootstrap(
        "비용 추적", "src.features.cost_tracking.schema",
        "src/features/cost_tracking/schema.py",
    ),
    PersistentSchemaBootstrap(
        "PDF 출고 원장", "src.features.export_pdf.schema",
        "src/features/export_pdf/schema.py",
    ),
    PersistentSchemaBootstrap(
        "최종 게이트 진단", "src.features.final_gate_diagnostic.store",
        "src/features/final_gate_diagnostic/store.py",
    ),
    PersistentSchemaBootstrap(
        "span 선택 진단", "src.features.spanselect.diagnostic_store",
        "src/features/spanselect/diagnostic_store.py",
    ),
    PersistentSchemaBootstrap(
        "유료 파일럿 결속", "src.features.pilot_evaluation.schema",
        "src/features/pilot_evaluation/schema.py",
    ),
    PersistentSchemaBootstrap(
        "오류 신고", "src.features.feedback_report.store",
        "src/features/feedback_report/store.py",
    ),
    PersistentSchemaBootstrap(
        "보고서 내용·전달", "src.features.report_delivery.store",
        "src/features/report_delivery/store.py",
    ),
    PersistentSchemaBootstrap(
        "보고서 불변 산출물", "src.features.report_delivery.artifact",
        "src/features/report_delivery/artifact.py",
    ),
    PersistentSchemaBootstrap(
        "보고서 출고 권위", "src.features.report_delivery.authority",
        "src/features/report_delivery/authority.py",
    ),
    PersistentSchemaBootstrap(
        "보고서 휴지통 정리", "src.features.report_delivery.retention",
        "src/features/report_delivery/retention.py",
    ),
    PersistentSchemaBootstrap(
        "보고서 단일 실행 lease", "src.features.report_delivery.singleflight",
        "src/features/report_delivery/singleflight.py",
    ),
    PersistentSchemaBootstrap(
        "보고서 접근 grant", "src.features.report_access.store",
        "src/features/report_access/store.py",
    ),
)


def load_persistent_schema_bootstraps() -> tuple[
    tuple[str, Callable[[sqlite3.Connection], None]], ...
]:
    """고정 app 경계의 registry 항목만 callable로 해석한다."""

    app_root = Path(__file__).resolve().parents[2]
    identities = tuple(
        (item.label, item.module_name, item.relative_path, item.callable_name)
        for item in PERSISTENT_SCHEMA_BOOTSTRAPS
    )
    if (
        len({item[0] for item in identities}) != len(identities)
        or len({item[1] for item in identities}) != len(identities)
        or len({item[2] for item in identities}) != len(identities)
    ):
        raise RuntimeError("영속 schema registry에 중복 항목이 있습니다")
    loaded: list[tuple[str, Callable[[sqlite3.Connection], None]]] = []
    for specification in PERSISTENT_SCHEMA_BOOTSTRAPS:
        module = importlib.import_module(specification.module_name)
        expected = (app_root / specification.relative_path).resolve()
        actual = Path(str(getattr(module, "__file__", ""))).resolve()
        if actual != expected:
            raise RuntimeError(
                f"{specification.label} schema 모듈이 고정 app 경계와 다릅니다"
            )
        bootstrap = getattr(module, specification.callable_name, None)
        if not callable(bootstrap):
            raise RuntimeError(
                f"{specification.label} schema bootstrap 계약이 없습니다"
            )
        loaded.append((specification.label, bootstrap))
    return tuple(loaded)


def ensure_persistent_schema(conn: sqlite3.Connection) -> None:
    """등록된 운영 feature 스키마를 빠짐없이 멱등 bootstrap한다."""

    for _label, bootstrap in load_persistent_schema_bootstraps():
        bootstrap(conn)
