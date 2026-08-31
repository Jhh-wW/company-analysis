"""복구 clone의 SQLite sidecar·payload·자원 상한 공격 회귀."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from ops import release_readiness as readiness


class _AcceptingManifestGate:
    def verify(self, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(
            backup_id="payload-adversarial",
            sequence=1,
            manifest_key_identity="spki-sha256:" + "a" * 64,
        )


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _storage_db_module():
    return readiness._load_storage_db_module()  # noqa: SLF001


def _create_runtime_database(path: Path) -> Path:
    with _storage_db_module().connect(path):
        pass
    return path


def _write_sidecar(database: Path) -> Path:
    checksum = database.with_name(database.name + ".sha256")
    checksum.write_text(f"{_digest(database)}  {database.name}\n", encoding="ascii")
    return checksum


def _directory_bytes(path: Path) -> dict[str, bytes]:
    return {
        item.name: item.read_bytes()
        for item in sorted(path.iterdir())
        if item.is_file() and not item.is_symlink()
    }


def _restore(database: Path, temp_parent: Path) -> dict[str, object]:
    checksum = _write_sidecar(database)
    return readiness.restore_dry_run(
        database,
        checksum,
        _digest(database),
        temp_parent=temp_parent,
        manifest_gate=_AcceptingManifestGate(),
        manifest_expectation=object(),
        manifest_data_root=database.parent,
    )


def _reject_without_mutation(database: Path, temp_parent: Path) -> None:
    _write_sidecar(database)
    before = _directory_bytes(database.parent)
    with pytest.raises(readiness.ReadinessError):
        _restore(database, temp_parent)
    assert _directory_bytes(database.parent) == before
    assert list(temp_parent.iterdir()) == []


def _legacy_report_payload(*, company: object = "복구시험") -> str:
    return json.dumps(
        {
            "company": company,
            "job": "",
            "corp_type": "상장사",
            "grade": "미완성",
            "sections": [],
        },
        ensure_ascii=False,
    )


def _insert_report(database: Path, payload: str, *, report_id: str = "report-1") -> None:
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO reports "
            "(report_id, corp_id, job, payload_json, generated_at, created_at) "
            "VALUES (?, 'corp-1', '', ?, '', '2026-08-23T00:00:00+00:00')",
            (report_id, payload),
        )


def _insert_legacy_layer2(
    database: Path,
    *,
    fragments_json: str = '[[1,{"종류":"공시","원문":"x","출처":"s"}]]',
    filing_json: str | None = '{"name":"filing"}',
    cell_judgments_json: str | None = '{"section":true}',
) -> None:
    """현재 제품 API가 금지한 corp-only 옛 행을 복구 공격 fixture로만 만든다."""

    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO layer2_cache "
            "(corp_id, fragments_json, filing_json, cell_judgments_json, "
            "fiscal_year, collected_at, updated_at) "
            "VALUES ('corp-1', ?, ?, ?, 2025, "
            "'2026-08-23T00:00:00+00:00', '2026-08-23T00:00:00+00:00')",
            (fragments_json, filing_json, cell_judgments_json),
        )


@pytest.mark.parametrize(
    "payload",
    (
        "{",
        json.dumps({"company": "필수필드누락"}, ensure_ascii=False),
        _legacy_report_payload(company=[]),
    ),
    ids=("malformed", "missing-required", "type-mismatch"),
)
def test_reports_payload_corruption_is_rejected_on_clone_only(
    tmp_path: Path,
    payload: str,
) -> None:
    database = _create_runtime_database(tmp_path / "source" / "storage.sqlite3")
    _insert_report(database, payload)
    temp_parent = tmp_path / "restore-temp"
    temp_parent.mkdir()

    _reject_without_mutation(database, temp_parent)


def test_canonical_report_missing_serializer_shape_is_rejected(
    tmp_path: Path,
) -> None:
    database = _create_runtime_database(tmp_path / "source" / "storage.sqlite3")
    reports = readiness._load_runtime_payload_consumers().reports  # noqa: SLF001
    payload = json.loads(_legacy_report_payload())
    payload["schema_version"] = reports.CANONICAL_SCHEMA_VERSION
    _insert_report(database, json.dumps(payload, ensure_ascii=False))
    temp_parent = tmp_path / "restore-temp"
    temp_parent.mkdir()

    _reject_without_mutation(database, temp_parent)


@pytest.mark.parametrize(
    ("field", "value"),
    (("requirements", [1]), ("cells", {"section": "truthy"})),
)
def test_canonical_report_nested_top_level_types_are_rejected(
    tmp_path: Path,
    field: str,
    value: object,
) -> None:
    database = _create_runtime_database(tmp_path / field / "source" / "storage.sqlite3")
    consumers = readiness._load_runtime_payload_consumers()  # noqa: SLF001
    from src.features.pipeline.port import Grade, Report  # noqa: PLC0415

    report = Report(
        company="타입시험",
        job="",
        corp_type="상장사",
        grade=Grade.INCOMPLETE,
        sections=[],
        schema_version=consumers.reports.CANONICAL_SCHEMA_VERSION,
    )
    payload = consumers.reports.report_to_dict(report)
    payload[field] = value
    _insert_report(database, json.dumps(payload, ensure_ascii=False))
    temp_parent = tmp_path / field / "restore-temp"
    temp_parent.mkdir(parents=True)

    _reject_without_mutation(database, temp_parent)


@pytest.mark.parametrize(
    ("path", "value"),
    (
        (("sections", 0, "tables", 0, "caption"), 123),
        (("sections", 0, "tables", 0, "headers", 0), 123),
        (("sections", 0, "tables", 0, "rows", 0, 0), 123),
        (("sources", 0, "name"), False),
        (("sources", 0, "state"), 1),
        (("sources", 0, "detail"), []),
        (("fact_records", 0, "fact_id"), 1),
        (("fact_records", 0, "numeric_checks"), "목록아님"),
        (("fact_records", 0, "numeric_checks"), [1]),
        (("fact_records", 0, "supports_causality"), 1),
        (("fact_records", 0, "fiscal_year"), True),
    ),
    ids=(
        "table-caption",
        "table-header",
        "table-row",
        "source-name",
        "source-status",
        "source-detail",
        "fact-string",
        "fact-list",
        "fact-list-item",
        "fact-bool",
        "fact-int",
    ),
)
def test_canonical_report_recursive_dataclass_types_are_rejected(
    tmp_path: Path,
    path: tuple[str | int, ...],
    value: object,
) -> None:
    database = _create_runtime_database(tmp_path / "source" / "storage.sqlite3")
    consumers = readiness._load_runtime_payload_consumers()  # noqa: SLF001
    from src.features.pipeline.port import (  # noqa: PLC0415
        FactRecord,
        Grade,
        Report,
        ReportSection,
        ReportTable,
        SourceStatus,
    )

    report = Report(
        company="재귀타입시험",
        job="",
        corp_type="상장사",
        grade=Grade.INCOMPLETE,
        sections=[
            ReportSection(
                cell="business_overview",
                title="개요",
                tables=[
                    ReportTable(
                        caption="시험표",
                        headers=["항목"],
                        rows=[["값"]],
                    )
                ],
            )
        ],
        sources=[SourceStatus(name="DART", state="ok")],
        fact_records=[FactRecord(fact_id="fact-1")],
        schema_version=consumers.reports.CANONICAL_SCHEMA_VERSION,
    )
    payload = consumers.reports.report_to_dict(report)
    target: object = payload
    for part in path[:-1]:
        target = target[part]  # type: ignore[index]
    target[path[-1]] = value  # type: ignore[index]
    _insert_report(database, json.dumps(payload, ensure_ascii=False))
    temp_parent = tmp_path / "restore-temp"
    temp_parent.mkdir()

    _reject_without_mutation(database, temp_parent)


def test_canonical_report_with_source_dataclass_passes_without_source_write(
    tmp_path: Path,
) -> None:
    database = _create_runtime_database(tmp_path / "source" / "storage.sqlite3")
    consumers = readiness._load_runtime_payload_consumers()  # noqa: SLF001
    from src.features.pipeline.port import Grade, Report  # noqa: PLC0415
    from src.features.provenance.sources import Source, SourceKind  # noqa: PLC0415

    report = Report(
        company="출처타입시험",
        job="",
        corp_type="상장사",
        grade=Grade.INCOMPLETE,
        sections=[],
        citations=[Source(number=1, kind=SourceKind.OTHER, label="시험 출처")],
        schema_version=consumers.reports.CANONICAL_SCHEMA_VERSION,
    )
    _insert_report(database, consumers.reports.report_to_json(report))
    temp_parent = tmp_path / "restore-temp"
    temp_parent.mkdir()
    _write_sidecar(database)
    before = _directory_bytes(database.parent)

    assert _restore(database, temp_parent)["status"] == "임시 복구 통과"
    assert _directory_bytes(database.parent) == before
    assert list(temp_parent.iterdir()) == []


def test_report_citations_reject_non_source_dataclass_without_value_disclosure() -> None:
    consumers = readiness._load_runtime_payload_consumers()  # noqa: SLF001
    from src.features.pipeline.port import Grade, Report  # noqa: PLC0415

    @dataclass(frozen=True)
    class WrongCitation:
        marker: str

    marker = "secret-citation-marker"
    report = Report(
        company="출처고정시험",
        job="",
        corp_type="상장사",
        grade=Grade.INCOMPLETE,
        sections=[],
        citations=[WrongCitation(marker)],
    )
    with pytest.raises(readiness.ReadinessError) as captured:
        readiness._assert_report_object(  # noqa: SLF001
            report,
            consumers=consumers,
            raw_payload="{}",
            budget=readiness.PayloadValidationBudget.start(),
            type_hints_cache={},
        )

    assert marker not in str(captured.value)


def test_large_fact_list_is_rejected_before_report_consumers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _create_runtime_database(tmp_path / "source" / "storage.sqlite3")
    consumers = readiness._load_runtime_payload_consumers()  # noqa: SLF001
    from src.features.pipeline.port import Grade, Report  # noqa: PLC0415

    report = Report(
        company="구조상한시험",
        job="",
        corp_type="상장사",
        grade=Grade.INCOMPLETE,
        sections=[],
        schema_version=consumers.reports.CANONICAL_SCHEMA_VERSION,
    )
    payload = consumers.reports.report_to_dict(report)
    payload["fact_records"] = [{} for _ in range(10_000)]
    _insert_report(database, json.dumps(payload, ensure_ascii=False))
    calls: list[str] = []

    def forbidden_load(*_args: object, **_kwargs: object) -> object:
        calls.append("load")
        raise AssertionError("구조 상한 뒤 reports.load를 호출하면 안 됩니다")

    def forbidden_decode(*_args: object, **_kwargs: object) -> object:
        calls.append("report_from_json")
        raise AssertionError("구조 상한 뒤 report_from_json을 호출하면 안 됩니다")

    monkeypatch.setattr(consumers.reports, "load", forbidden_load)
    monkeypatch.setattr(consumers.reports, "report_from_json", forbidden_decode)
    temp_parent = tmp_path / "restore-temp"
    temp_parent.mkdir()

    _reject_without_mutation(database, temp_parent)
    assert calls == []


def test_payload_deadline_blocks_before_report_consumers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _create_runtime_database(tmp_path / "source" / "storage.sqlite3")
    _insert_report(database, _legacy_report_payload())
    consumers = readiness._load_runtime_payload_consumers()  # noqa: SLF001
    calls: list[str] = []

    def forbidden_consumer(*_args: object, **_kwargs: object) -> object:
        calls.append("called")
        raise AssertionError("deadline 뒤 보고서 소비자를 호출하면 안 됩니다")

    monkeypatch.setattr(consumers.reports, "load", forbidden_consumer)
    monkeypatch.setattr(consumers.reports, "report_from_json", forbidden_consumer)
    monkeypatch.setattr(readiness, "PAYLOAD_VALIDATION_DEADLINE_SEC", -1.0)
    temp_parent = tmp_path / "restore-temp"
    temp_parent.mkdir()

    _reject_without_mutation(database, temp_parent)
    assert calls == []


def test_large_layer2_container_is_rejected_before_cache_consumer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _create_runtime_database(tmp_path / "source" / "storage.sqlite3")
    _insert_legacy_layer2(
        database,
        fragments_json=json.dumps(
            [{} for _ in range(readiness.MAX_JSON_CONTAINER_ITEMS + 1)]
        ),
    )
    calls: list[str] = []

    def forbidden_layer2_gate(*_args: object, **_kwargs: object) -> object:
        calls.append("layer2_gate")
        raise AssertionError("구조 상한 뒤 옛 2층 행 판정을 호출하면 안 됩니다")

    monkeypatch.setattr(readiness, "_assert_layer2_payloads", forbidden_layer2_gate)
    temp_parent = tmp_path / "restore-temp"
    temp_parent.mkdir()

    _reject_without_mutation(database, temp_parent)
    assert calls == []


def test_json_byte_cap_query_runs_before_json_valid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queries: list[str] = []

    class OneRowCursor:
        def __init__(self) -> None:
            self.sent = False

        def fetchmany(self, _size: int) -> list[tuple[object, ...]]:
            if self.sent:
                return []
            self.sent = True
            return [("text", 1)]

    class RecordingConnection:
        def execute(self, sql: str) -> OneRowCursor:
            queries.append(sql)
            if len(queries) > 1:
                raise AssertionError("바이트 상한 뒤 JSON 함수를 호출하면 안 됩니다")
            return OneRowCursor()

    monkeypatch.setattr(readiness, "MAX_JSON_FIELD_BYTES", 0)
    with pytest.raises(readiness.ReadinessError, match="바이트 상한"):
        readiness._assert_generic_json_columns(  # noqa: SLF001
            RecordingConnection(),  # type: ignore[arg-type]
            columns=(("reports", "payload_json"),),
            budget=readiness.PayloadValidationBudget.start(),
        )

    assert len(queries) == 1
    assert "json_valid" not in queries[0].lower()


def test_dashboard_uses_recursive_report_runtime_type_contract(tmp_path: Path) -> None:
    database = _create_runtime_database(tmp_path / "source" / "storage.sqlite3")
    consumers = readiness._load_runtime_payload_consumers()  # noqa: SLF001
    from src.features.pipeline.port import (  # noqa: PLC0415
        Grade,
        Report,
        ReportSection,
        ReportTable,
    )

    report = Report(
        company="대시보드타입시험",
        job="",
        corp_type="상장사",
        grade=Grade.INCOMPLETE,
        sections=[
            ReportSection(
                cell="business_overview",
                title="개요",
                tables=[ReportTable(caption="시험표", headers=["항목"], rows=[["값"]])],
            )
        ],
        schema_version=consumers.reports.CANONICAL_SCHEMA_VERSION,
    )
    payload_data = consumers.reports.report_to_dict(report)
    payload_data["sections"][0]["tables"][0]["caption"] = 123
    payload = json.dumps(payload_data, ensure_ascii=False)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO dashboard_report_versions "
            "(report_id, version, payload_json, payload_sha256, actor, created_at) "
            "VALUES ('nested-type', 1, ?, ?, 'tester', '2026-08-23T00:00:00+00:00')",
            (payload, digest),
        )
    temp_parent = tmp_path / "restore-temp"
    temp_parent.mkdir()

    _reject_without_mutation(database, temp_parent)


def test_loadable_legacy_report_passes_without_source_write(tmp_path: Path) -> None:
    database = _create_runtime_database(tmp_path / "source" / "storage.sqlite3")
    _insert_report(database, _legacy_report_payload())
    temp_parent = tmp_path / "restore-temp"
    temp_parent.mkdir()
    _write_sidecar(database)
    before = _directory_bytes(database.parent)

    result = _restore(database, temp_parent)

    assert result["status"] == "임시 복구 통과"
    assert _directory_bytes(database.parent) == before
    assert list(temp_parent.iterdir()) == []


def test_loadable_nonempty_legacy_version_is_not_treated_as_current_canonical(
    tmp_path: Path,
) -> None:
    database = _create_runtime_database(tmp_path / "source" / "storage.sqlite3")
    payload = json.loads(_legacy_report_payload())
    payload["schema_version"] = "company-report-v2"
    _insert_report(database, json.dumps(payload, ensure_ascii=False))
    temp_parent = tmp_path / "restore-temp"
    temp_parent.mkdir()

    assert _restore(database, temp_parent)["status"] == "임시 복구 통과"
    assert list(temp_parent.iterdir()) == []


@pytest.mark.parametrize(
    ("column", "payload"),
    (
        ("fragments_json", '[["1", {"원문": "x"}]]'),
        ("filing_json", "[]"),
        ("cell_judgments_json", '{"section": 1}'),
    ),
)
def test_deprecated_layer2_payload_variants_are_all_rejected(
    tmp_path: Path,
    column: str,
    payload: str,
) -> None:
    database = _create_runtime_database(tmp_path / column / "source" / "storage.sqlite3")
    _insert_legacy_layer2(database)
    with sqlite3.connect(database) as connection:
        connection.execute(f'UPDATE layer2_cache SET "{column}" = ?', (payload,))
    temp_parent = tmp_path / column / "restore-temp"
    temp_parent.mkdir(parents=True)

    _reject_without_mutation(database, temp_parent)


@pytest.mark.parametrize("variant", ("wrong-hash", "bad-report"))
def test_dashboard_snapshot_hash_and_report_contract_are_enforced(
    tmp_path: Path,
    variant: str,
) -> None:
    database = _create_runtime_database(tmp_path / variant / "source" / "storage.sqlite3")
    payload = _legacy_report_payload() if variant == "wrong-hash" else '{"not":"report"}'
    digest = "0" * 64 if variant == "wrong-hash" else hashlib.sha256(
        payload.encode("utf-8")
    ).hexdigest()
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO dashboard_report_versions "
            "(report_id, version, payload_json, payload_sha256, actor, created_at) "
            "VALUES ('report-1', 1, ?, ?, 'tester', '2026-08-23T00:00:00+00:00')",
            (payload, digest),
        )
    temp_parent = tmp_path / variant / "restore-temp"
    temp_parent.mkdir(parents=True)

    _reject_without_mutation(database, temp_parent)


@pytest.mark.parametrize("updated_at", ("2026-08-23T00:00:00+00:00", ""))
def test_dashboard_normal_state_missing_current_snapshot_is_rejected(
    tmp_path: Path,
    updated_at: str,
) -> None:
    database = _create_runtime_database(tmp_path / "source" / "storage.sqlite3")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO dashboard_report_states "
            "(report_id, status, blocked, company_type, version, updated_at, updated_by) "
            "VALUES ('missing-snapshot', 'normal', 0, 'listed', 3, ?, 'tester')",
            (updated_at,),
        )
    temp_parent = tmp_path / "restore-temp"
    temp_parent.mkdir()

    _reject_without_mutation(database, temp_parent)


def test_dashboard_current_snapshot_uses_actual_approved_consumer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _create_runtime_database(tmp_path / "source" / "storage.sqlite3")
    consumers = readiness._load_runtime_payload_consumers()  # noqa: SLF001
    from src.features.pipeline.port import Grade, Report  # noqa: PLC0415

    report = Report(
        company="승인소비자시험",
        job="",
        corp_type="상장사",
        grade=Grade.INCOMPLETE,
        sections=[],
        schema_version=consumers.reports.CANONICAL_SCHEMA_VERSION,
    )
    payload = consumers.reports.report_to_json(report)
    with _storage_db_module().connect(database) as connection:
        consumers.dashboard.register_report(
            connection,
            report_id="approved-current",
            corp_type="상장사",
            now_iso="2026-08-23T00:00:00+00:00",
            payload_json=payload,
        )
    calls: list[str] = []
    actual_consumer = consumers.dashboard.approved_report_payload

    def observed_consumer(connection: sqlite3.Connection, *, report_id: str) -> str:
        calls.append(report_id)
        return actual_consumer(connection, report_id=report_id)

    monkeypatch.setattr(consumers.dashboard, "approved_report_payload", observed_consumer)
    monkeypatch.setattr(consumers.publish, "validate_publishable", lambda _report: True)
    temp_parent = tmp_path / "restore-temp"
    temp_parent.mkdir()

    assert _restore(database, temp_parent)["status"] == "임시 복구 통과"
    assert calls and set(calls) == {"approved-current"}
    assert list(temp_parent.iterdir()) == []


def _run_record(run_id: str = "run-1"):
    from src.features.observability.constants import (  # noqa: PLC0415
        CACHE_HIT_NONE,
        CORP_TYPE_UNKNOWN,
        END_STEP_COMPLETE,
    )
    from src.features.observability.records import RunRecord  # noqa: PLC0415

    return RunRecord(
        run_id=run_id,
        at="2026-08-23T00:10:00+00:00",
        corp_type=CORP_TYPE_UNKNOWN,
        job="영업",
        end_step=END_STEP_COMPLETE,
        cache_hit=CACHE_HIT_NONE,
        fragments_collected=0,
        fragments_cited=0,
        sentences_made=0,
        sentences_passed=0,
        cells_filled=0,
        cells_missing=[],
        cells_suspect=[],
        grade="",
        human_check="",
        cost_krw=20.0,
        elapsed_sec=2.0,
        model="model",
    )


def _insert_final_lifecycle(database: Path) -> None:
    consumers = readiness._load_runtime_payload_consumers()  # noqa: SLF001
    with _storage_db_module().connect(database) as connection:
        consumers.lifecycle.finalize_once(connection, _run_record())


def test_lifecycle_actual_reader_and_audit_binding_pass(tmp_path: Path) -> None:
    database = _create_runtime_database(tmp_path / "source" / "storage.sqlite3")
    _insert_final_lifecycle(database)
    temp_parent = tmp_path / "restore-temp"
    temp_parent.mkdir()
    _write_sidecar(database)
    before = _directory_bytes(database.parent)

    assert _restore(database, temp_parent)["status"] == "임시 복구 통과"
    assert _directory_bytes(database.parent) == before
    assert list(temp_parent.iterdir()) == []


def test_lifecycle_valid_json_replacement_breaks_audit_binding(tmp_path: Path) -> None:
    database = _create_runtime_database(tmp_path / "source" / "storage.sqlite3")
    _insert_final_lifecycle(database)
    consumers = readiness._load_runtime_payload_consumers()  # noqa: SLF001
    replacement = consumers.lifecycle._encode_record(_run_record("run-other"))
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE observability_run_lifecycle SET final_record_json = ? "
            "WHERE run_id = 'run-1'",
            (replacement,),
        )
    temp_parent = tmp_path / "restore-temp"
    temp_parent.mkdir()

    _reject_without_mutation(database, temp_parent)


def test_other_canonical_json_text_column_is_scanned(tmp_path: Path) -> None:
    database = _create_runtime_database(tmp_path / "source" / "storage.sqlite3")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO pdf_release_records "
            "(report_id, pdf_sha256, approval_json, approval_created_at) "
            "VALUES ('r', ?, '{', '2026-08-23T00:00:00+00:00')",
            ("a" * 64,),
        )
    temp_parent = tmp_path / "restore-temp"
    temp_parent.mkdir()

    _reject_without_mutation(database, temp_parent)


def test_valid_legacy_layer2_row_is_rejected_without_dead_runtime_consumer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _create_runtime_database(tmp_path / "source" / "storage.sqlite3")
    _insert_legacy_layer2(database)
    consumers = readiness._load_runtime_payload_consumers()  # noqa: SLF001
    calls: list[str] = []

    def forbidden_cache(*_args: object, **_kwargs: object) -> object:
        calls.append("get_layer2")
        raise AssertionError("폐기된 get_layer2를 복구 검사가 호출하면 안 됩니다")

    monkeypatch.setattr(consumers.cache, "get_layer2", forbidden_cache)
    temp_parent = tmp_path / "restore-temp"
    temp_parent.mkdir()

    with pytest.raises(readiness.ReadinessError, match="신원 없는 옛 layer2_cache"):
        _restore(database, temp_parent)
    assert calls == []
    assert list(temp_parent.iterdir()) == []


@pytest.mark.parametrize(
    "record_kind",
    ("approval", "role", "automatic"),
)
def test_pdf_release_valid_json_with_invalid_domain_meaning_is_rejected(
    tmp_path: Path,
    record_kind: str,
) -> None:
    database = _create_runtime_database(
        tmp_path / record_kind / "source" / "storage.sqlite3"
    )
    with sqlite3.connect(database) as connection:
        if record_kind == "approval":
            connection.execute(
                "INSERT INTO pdf_release_records "
                "(report_id, pdf_sha256, approval_json, approval_created_at) "
                "VALUES ('report-1', ?, '{}', '2026-08-23T00:00:00+00:00')",
                ("a" * 64,),
            )
        elif record_kind == "role":
            connection.execute(
                """
                INSERT INTO pdf_release_role_decisions (
                    report_id, pdf_sha256, role, page_hashes_json,
                    reviewed_pages_json, expected_fact_ids_json,
                    reviewed_fact_ids_json, fact_failed_count,
                    reviewer, approved_at, visual_review_kind
                ) VALUES (
                    'report-1', ?, 'fact', '[]', '[]', '[]', '[]', 0,
                    ?, '2026-08-23T00:00:00+00:00', ''
                )
                """,
                ("a" * 64, "user:" + "1" * 20),
            )
        else:
            connection.execute(
                """
                INSERT INTO pdf_automatic_release_records (
                    report_id, report_sha256, pdf_sha256, checker_version,
                    release_json, release_sha256, released_at
                ) VALUES (
                    'report-1', ?, ?, 'automatic-release-v1', '{}', ?,
                    '2026-08-23T00:00:00+00:00'
                )
                """,
                ("a" * 64, "b" * 64, "c" * 64),
            )
    temp_parent = tmp_path / record_kind / "restore-temp"
    temp_parent.mkdir(parents=True)

    _reject_without_mutation(database, temp_parent)


def test_pdf_release_shadow_report_id_is_rejected_by_restore_gate(
    tmp_path: Path,
) -> None:
    from src.features.export_pdf.release import (  # noqa: PLC0415
        ApprovalDecision,
        PdfReleaseApproval,
    )
    from src.features.export_pdf.release_store import (  # noqa: PLC0415
        PdfRoleDecision,
        ensure_participant_ledger,
        save_approval,
        save_role_decision,
    )

    database = _create_runtime_database(tmp_path / "source" / "storage.sqlite3")
    pdf_sha256 = "a" * 64
    page_hashes = ("b" * 64,)
    fact_ids = ("fact-1",)
    approved_at = "2026-08-23T00:00:00+00:00"
    reviewers = {
        "fact": "user:" + "1" * 20,
        "editorial": "user:" + "2" * 20,
        "visual": "user:" + "3" * 20,
    }
    with _storage_db_module().connect(database) as connection:
        ensure_participant_ledger(
            connection,
            report_id="report-1",
            pdf_sha256=pdf_sha256,
            participants={
                "author": "user:" + "4" * 20,
                "producer": "user:" + "5" * 20,
                **reviewers,
            },
            assigned_at=approved_at,
        )
        decisions: dict[str, ApprovalDecision] = {}
        for role, reviewer in reviewers.items():
            decision = ApprovalDecision(True, reviewer, approved_at)
            decisions[role] = decision
            save_role_decision(
                connection,
                report_id="report-1",
                role_decision=PdfRoleDecision(
                    role=role,
                    pdf_sha256=pdf_sha256,
                    page_png_sha256s=page_hashes,
                    reviewed_pages=(1,),
                    expected_fact_ids=fact_ids,
                    reviewed_fact_ids=fact_ids if role == "fact" else (),
                    fact_failed_count=0,
                    decision=decision,
                    visual_review_kind="human" if role == "visual" else "",
                ),
            )
        save_approval(
            connection,
            report_id="report-1",
            approval=PdfReleaseApproval(
                pdf_sha256=pdf_sha256,
                page_png_sha256s=page_hashes,
                reviewed_pages=(1,),
                reviewed_fact_ids=fact_ids,
                fact_failed_count=0,
                fact=decisions["fact"],
                editorial=decisions["editorial"],
                visual=decisions["visual"],
                visual_review_kind="human",
            ),
            created_at=approved_at,
        )
        connection.execute(
            "INSERT INTO pdf_release_records "
            "(report_id, pdf_sha256, approval_json, approval_created_at) "
            "VALUES (' report-1 ', ?, '{}', ?)",
            (pdf_sha256, approved_at),
        )
    temp_parent = tmp_path / "restore-temp"
    temp_parent.mkdir()

    _reject_without_mutation(database, temp_parent)


@pytest.mark.parametrize(
    ("actor_id", "outcome"),
    (
        ("private@example.invalid", "success"),
        ("anonymous", "forged"),
    ),
)
def test_admin_audit_rows_bypassing_checks_are_rejected_by_restore_gate(
    tmp_path: Path,
    actor_id: str,
    outcome: str,
) -> None:
    database = _create_runtime_database(
        tmp_path / outcome / actor_id.replace("@", "-") / "source" / "storage.sqlite3"
    )
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA ignore_check_constraints = ON")
        connection.execute(
            """
            INSERT INTO admin_audit_events (
                event_time, request_id, actor_id, action,
                target_id, outcome, reason_code
            ) VALUES (
                '2026-08-23T10:00:00+09:00', 'request-1', ?,
                'admin.member.invite', 'member:fixed-target', ?, 'invited'
            )
            """,
            (actor_id, outcome),
        )
        connection.execute("PRAGMA ignore_check_constraints = OFF")
    temp_parent = tmp_path / outcome / actor_id.replace("@", "-") / "restore-temp"
    temp_parent.mkdir(parents=True)

    _reject_without_mutation(database, temp_parent)


def test_json_scan_reaches_invalid_row_after_first_fetchmany_batch(
    tmp_path: Path,
) -> None:
    database = _create_runtime_database(tmp_path / "source" / "storage.sqlite3")
    valid = _legacy_report_payload()
    rows = [
        (
            f"report-{index:03d}",
            "corp",
            "",
            valid if index < readiness.JSON_FETCH_BATCH_ROWS else "{",
            "",
            "2026-08-23T00:00:00+00:00",
        )
        for index in range(readiness.JSON_FETCH_BATCH_ROWS + 1)
    ]
    with sqlite3.connect(database) as connection:
        connection.executemany(
            "INSERT INTO reports "
            "(report_id, corp_id, job, payload_json, generated_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            rows,
        )
    temp_parent = tmp_path / "restore-temp"
    temp_parent.mkdir()

    _reject_without_mutation(database, temp_parent)


@pytest.mark.parametrize("suffix", readiness.SQLITE_COMPANION_SUFFIXES)
@pytest.mark.parametrize("artifact", ("empty-file", "directory"))
def test_any_sqlite_companion_artifact_blocks_before_restore(
    tmp_path: Path,
    suffix: str,
    artifact: str,
) -> None:
    database = _create_runtime_database(
        tmp_path / suffix.removeprefix("-") / artifact / "source" / "storage.sqlite3"
    )
    companion = Path(str(database) + suffix)
    if artifact == "empty-file":
        companion.touch()
    else:
        companion.mkdir()
    temp_parent = database.parent.parent / "restore-temp"
    temp_parent.mkdir()
    main_digest = _digest(database)

    with pytest.raises(readiness.ReadinessError, match="sidecar"):
        _restore(database, temp_parent)

    assert _digest(database) == main_digest
    assert list(temp_parent.iterdir()) == []


@pytest.mark.parametrize("suffix", readiness.SQLITE_COMPANION_SUFFIXES)
def test_broken_symlink_sqlite_companion_blocks_when_supported(
    tmp_path: Path,
    suffix: str,
) -> None:
    database = _create_runtime_database(tmp_path / "source" / "storage.sqlite3")
    companion = Path(str(database) + suffix)
    try:
        companion.symlink_to(database.parent / "missing-target")
    except OSError:
        pytest.skip("이 Windows 환경은 일반 사용자 symlink 생성을 허용하지 않습니다")
    temp_parent = tmp_path / "restore-temp"
    temp_parent.mkdir()

    with pytest.raises(readiness.ReadinessError, match="sidecar"):
        _restore(database, temp_parent)
    assert list(temp_parent.iterdir()) == []


@pytest.mark.parametrize("suffix", readiness.SQLITE_COMPANION_SUFFIXES)
@pytest.mark.parametrize("symlink_kind", ("normal", "broken"))
def test_lstat_symlink_presence_is_fail_closed_without_following_target(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    suffix: str,
    symlink_kind: str,
) -> None:
    database = tmp_path / "artifact.sqlite3"
    companion = Path(str(database) + suffix)
    real_lstat = os.lstat

    def fake_lstat(path: os.PathLike[str] | str):
        if Path(path) == companion:
            return SimpleNamespace(st_mode=0, kind=symlink_kind)
        return real_lstat(path)

    monkeypatch.setattr(readiness.os, "lstat", fake_lstat)
    with pytest.raises(readiness.ReadinessError, match="sidecar"):
        readiness._assert_no_sqlite_companions(database, label="시험 DB")  # noqa: SLF001


def test_wal_only_committed_row_cannot_bypass_manifest_main_hash(tmp_path: Path) -> None:
    database = _create_runtime_database(tmp_path / "source" / "storage.sqlite3")
    temp_parent = tmp_path / "restore-temp"
    temp_parent.mkdir()
    connection = sqlite3.connect(database)
    try:
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA wal_autocheckpoint=0")
        connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        main_before = database.read_bytes()
        size_before = database.stat().st_size
        connection.execute(
            "INSERT INTO sessions(token_hash, email, subject, is_admin, expires_at) "
            "VALUES ('wal-only', 'admin@example.invalid', 'sub', 1, 2000000000.0)"
        )
        connection.commit()
        assert database.read_bytes() == main_before
        assert database.stat().st_size == size_before
        assert Path(str(database) + "-wal").exists()

        with pytest.raises(readiness.ReadinessError, match="sidecar"):
            _restore(database, temp_parent)
        assert database.read_bytes() == main_before
        assert list(temp_parent.iterdir()) == []
    finally:
        connection.close()


@pytest.mark.parametrize(
    "limit_name",
    ("MAX_JSON_FIELD_BYTES", "MAX_JSON_TOTAL_BYTES", "MAX_JSON_TOTAL_ROWS"),
)
def test_json_resource_limits_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    limit_name: str,
) -> None:
    database = _create_runtime_database(tmp_path / limit_name / "source" / "storage.sqlite3")
    _insert_report(database, _legacy_report_payload(company="큰회사명"))
    monkeypatch.setattr(readiness, limit_name, 0)
    temp_parent = tmp_path / limit_name / "restore-temp"
    temp_parent.mkdir(parents=True)

    _reject_without_mutation(database, temp_parent)


def test_payload_deadline_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    database = _create_runtime_database(tmp_path / "source" / "storage.sqlite3")
    monkeypatch.setattr(readiness, "PAYLOAD_VALIDATION_DEADLINE_SEC", -1.0)
    temp_parent = tmp_path / "restore-temp"
    temp_parent.mkdir()

    _reject_without_mutation(database, temp_parent)


def test_clone_space_shortage_fails_before_copy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _create_runtime_database(tmp_path / "source" / "storage.sqlite3")
    monkeypatch.setattr(
        readiness.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=1, used=1, free=0),
    )
    temp_parent = tmp_path / "restore-temp"
    temp_parent.mkdir()

    _reject_without_mutation(database, temp_parent)


def test_restore_database_size_limit_is_not_preflight_only(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = _create_runtime_database(tmp_path / "source" / "storage.sqlite3")
    monkeypatch.setattr(readiness, "DEFAULT_MAX_DATABASE_BYTES", database.stat().st_size - 1)
    temp_parent = tmp_path / "restore-temp"
    temp_parent.mkdir()

    _reject_without_mutation(database, temp_parent)
