"""운영 영속 schema registry의 공개 계약과 inventory를 고정한다."""

from __future__ import annotations

import sqlite3
import subprocess
import sys
from pathlib import Path

from src.core import persistent_schema


EXPECTED_TABLES = {
    "src.features.auth.state_store": {"oauth_login_states"},
    "src.features.budget.spend_store": {
        "budget_spend_events",
        "budget_spend_inflight",
        "budget_spend_overruns",
        "budget_schema_migrations",
        "budget_phase_accounts",
        "budget_provider_attempts",
        "budget_provider_attempt_events",
    },
    "src.features.provider_health.store": {
        "provider_health_states",
        "provider_health_events",
    },
    "src.features.observability.lifecycle": {
        "observability_run_lifecycle",
        "observability_run_lifecycle_audit",
    },
    "src.features.observability.admin_audit_store": {"admin_audit_events"},
    "src.features.backup.status": {"backup_run_state", "backup_run_events"},
    "src.features.admin_dashboard.kpi": {
        "dashboard_report_kpi_attempts",
        "dashboard_report_kpi_events",
    },
    "src.features.cost_tracking.schema": {
        "ai_variable_cost_events",
        "report_cost_summaries",
        "monthly_server_fixed_costs",
    },
    "src.features.export_pdf.schema": {
        "pdf_release_records",
        "pdf_release_role_decisions",
        "pdf_release_participants",
        "pdf_automatic_release_records",
    },
    "src.features.final_gate_diagnostic.store": {"pipeline_final_gate_diagnostics"},
    "src.features.spanselect.diagnostic_store": {"pilot_span_selection_diagnostics"},
    "src.features.pilot_evaluation.schema": {"canonical_pilot25_bindings"},
    "src.features.feedback_report.store": {
        "feedback_reports",
        "feedback_report_events",
    },
    "src.features.report_delivery.store": {
        "report_delivery_source_snapshots",
        "report_delivery_cache_namespaces",
        "report_delivery_content_snapshots",
        "report_delivery_deliveries",
        "report_delivery_cache_entries",
        "report_delivery_cache_invalidations",
        "report_delivery_intents",
    },
    "src.features.report_delivery.artifact": {
        "artifact_blob_intents",
        "artifact_blob_intent_events",
        "report_delivery_artifacts",
        "report_delivery_delivery_artifacts",
    },
    "src.features.report_delivery.retention": {
        "report_delivery_retirement_intents",
        "report_delivery_retirement_events",
        "report_delivery_retired_public_ids",
    },
    "src.features.report_delivery.singleflight": {
        "report_delivery_singleflight_leases",
    },
    "src.features.report_access.store": {
        "report_access_public_grants",
        "report_access_public_bindings",
        "report_access_member_bindings",
        "report_access_cutover",
        "report_access_legacy_resources",
    },
}

BASE_SCHEMA_OWNERS = {
    "src/features/storage/db.py",
    "src/features/storage/job_interruptions.py",
    "src/features/sharelink/allowlist.py",
    "src/features/sharelink/store.py",
    "src/features/export_notion/store.py",
    "src/features/admin_dashboard/store.py",
}


def _tables(connection: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }


def test_registry_has_unique_public_bootstraps_and_exact_feature_inventory() -> None:
    entries = persistent_schema.PERSISTENT_SCHEMA_BOOTSTRAPS
    assert {entry.module_name for entry in entries} == set(EXPECTED_TABLES)
    assert len({entry.label for entry in entries}) == len(entries)
    assert len({entry.relative_path for entry in entries}) == len(entries)
    assert all(entry.callable_name == "ensure_schema" for entry in entries)

    loaded = dict(persistent_schema.load_persistent_schema_bootstraps())
    for entry in entries:
        with sqlite3.connect(":memory:") as connection:
            loaded[entry.label](connection)
            assert _tables(connection) == EXPECTED_TABLES[entry.module_name]


def test_all_production_create_table_modules_are_in_registry_or_base_bootstrap() -> None:
    app_root = Path(__file__).resolve().parents[3]
    discovered = {
        path.relative_to(app_root).as_posix()
        for path in (app_root / "src").rglob("*.py")
        if "tests" not in path.parts and "CREATE TABLE" in path.read_text(encoding="utf-8")
    }
    registered = {
        entry.relative_path
        for entry in persistent_schema.PERSISTENT_SCHEMA_BOOTSTRAPS
    }
    assert discovered == registered | BASE_SCHEMA_OWNERS


def test_admin_audit_registry_includes_both_append_only_triggers() -> None:
    loaded = dict(persistent_schema.load_persistent_schema_bootstraps())
    with sqlite3.connect(":memory:") as connection:
        loaded["관리자 변경 감사"](connection)
        triggers = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger'"
            )
        }
    assert triggers == {
        "admin_audit_events_no_update",
        "admin_audit_events_no_delete",
    }


def test_fresh_storage_connect_does_not_import_optional_pdf_render_dependencies(
    tmp_path: Path,
) -> None:
    app_root = Path(__file__).resolve().parents[3]
    script = r'''
import importlib.abc
import sys
from pathlib import Path

DENIED = {"pypdf", "reportlab", "weasyprint", "PIL"}
class DenyOptionalRender(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".", 1)[0] in DENIED:
            raise ImportError("optional renderer import blocked")
        return None

sys.meta_path.insert(0, DenyOptionalRender())
from src.features.storage import db
with db.connect(Path(sys.argv[1])):
    pass
assert not DENIED.intersection(sys.modules)
assert not any(name == "src.web" or name.startswith("src.web.") for name in sys.modules)
'''
    completed = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path / "minimal-runtime.sqlite3")],
        cwd=app_root,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
