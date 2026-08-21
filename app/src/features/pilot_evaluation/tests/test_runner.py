from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs

import httpx
import pytest

from src.features.budget.constants import SPEND_PHASE_PIPELINE
from src.features.budget import spend_store
from src.features.cost_tracking import store as cost_store
from src.features.admin_dashboard import store as dashboard_store
from src.features.observability import lifecycle
from src.features.pilot_evaluation.checkpoint import (
    PRIOR_DAY_BILLING_UNCERTAIN_ERROR,
    PRIOR_DAY_BILLING_UNCERTAIN_STATE,
    CheckpointError,
    CheckpointStore,
)
from src.features.pilot_evaluation.manifest import (
    APPROVED_PAID_CASE_IDS,
    CANONICAL_PILOT_CASES,
    manifest_sha256,
    validate_manifest,
)
from src.features.pilot_evaluation.runner import (
    CanonicalPilotRunner,
    LEGAL_NAME_RECOVERY_READY_ERROR,
    LEGAL_NAME_RECOVERY_READY_STATE,
    PILOT_BINDING_TABLE,
    PilotBatchBlocked,
    PilotRunnerError,
    SERVICE_MAINTENANCE_BLOCKED_ERROR,
    SERVICE_MAINTENANCE_BLOCKED_STATE,
    SERVICE_MAINTENANCE_RECOVERY_READY_STATE,
    canonical_loopback_origin,
)
from src.features.pipeline.port import Outcome
from src.features.storage import db as storage_db


ORIGIN = "http://127.0.0.1:8020"
RUN_ID = "1" * 32
CSRF = "c" * 64
NEW_CSRF = "d" * 64
WORKFLOW = "a" * 32


def _final_lifecycle_record(cost_krw: float) -> str:
    return json.dumps({"run_id": RUN_ID, "cost_krw": cost_krw})


def _storage(path: Path, *, include_cost_summary: bool = True) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE observability_run_lifecycle (
                run_id TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                final_record_json TEXT
            );
            CREATE TABLE budget_spend_events (run_id TEXT, cost_krw REAL);
            CREATE TABLE budget_spend_inflight (run_id TEXT);
            CREATE TABLE reports (
                report_id TEXT PRIMARY KEY,
                corp_id TEXT
            );
            """
        )
        if include_cost_summary:
            conn.execute(
                """
                CREATE TABLE report_cost_summaries (
                    run_id TEXT PRIMARY KEY,
                    outcome TEXT,
                    internal_ai_cost_krw REAL,
                    automatic_release_sha256 TEXT
                )
                """
            )


def _html(body: str, status: int = 200, **headers: str) -> httpx.Response:
    return httpx.Response(
        status,
        text=body,
        headers={"Content-Type": "text/html; charset=utf-8", **headers},
    )


def _input_page(*, csrf: str = CSRF) -> str:
    return f"""
    <form method="post" action="/confirm">
      <input type="hidden" name="csrf_token" value="{csrf}">
      <input type="hidden" name="evaluation_workflow_id" value="{WORKFLOW}">
      <input type="checkbox" name="evaluation_paid_consent" value="yes" required>
    </form>
    """


def _candidate_page(case, *, corp_code: str | None = None) -> str:
    ref = corp_code or case.corp_code
    return f"""
    <form method="post" action="/confirm">
      <input type="hidden" name="csrf_token" value="{CSRF}">
      <input type="hidden" name="company" value="{case.input_name}">
      <input type="hidden" name="region" value="{case.address_hint}">
      <input type="hidden" name="retry" value="0">
      <input type="hidden" name="candidate_resolution_confirmed" value="yes">
      <input type="hidden" name="candidate_attempt_token" value="candidate-secret">
      <input type="hidden" name="candidate_selection_token" value="selection-secret">
      <input type="hidden" name="candidate_index" value="0">
      <input type="hidden" name="candidate_name" value="{case.expected_legal_name}">
      <input type="hidden" name="candidate_provider" value="DART">
      <input type="hidden" name="candidate_ref" value="{ref}">
      <input type="hidden" name="evaluation_consent_grant" value="grant-secret">
    </form>
    """


def _confirm_page(
    case, *, legal_name: str = "", confirmed_dart_ref: str = ""
) -> str:
    displayed_name = legal_name or case.expected_legal_name
    ref_attribute = (
        f' data-dart-corp-code="{confirmed_dart_ref}"'
        if confirmed_dart_ref
        else ""
    )
    return f"""
    <div class="company-card"{ref_attribute}>
      <div class="legal">{displayed_name}</div>
    </div>
    <form method="post" action="/run">
      <input type="hidden" name="csrf_token" value="{CSRF}">
      <input type="hidden" name="company" value="{case.input_name}">
      <input type="hidden" name="region" value="{case.address_hint}">
      <input type="hidden" name="paid_attempt_token" value="paid-secret">
      <input type="hidden" name="evaluation_consent_grant" value="grant-secret">
    </form>
    <form method="post" action="/reject">
      <input type="hidden" name="csrf_token" value="{CSRF}">
      <input type="hidden" name="company" value="{case.input_name}">
      <input type="hidden" name="region" value="{case.address_hint}">
      <input type="hidden" name="retry" value="0">
      <input type="hidden" name="paid_attempt_token" value="paid-secret">
      <input type="hidden" name="evaluation_consent_grant" value="grant-secret">
    </form>
    """


class SuccessfulFlow:
    def __init__(self, db: Path, case, *, legal_name: str = "") -> None:
        self.db = db
        self.case = case
        self.legal_name = legal_name
        self.posts: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "GET" and path == "/healthz":
            return httpx.Response(200, json={"status": "ok"})
        if request.method == "GET" and path == "/readyz":
            return httpx.Response(200, json={"status": "ready"})
        if request.method == "GET" and path == "/":
            return _html(_input_page())
        if request.method == "POST":
            self.posts.append(path)
            fields = {
                key: values[-1]
                for key, values in parse_qs(request.content.decode("utf-8")).items()
            }
            if path == "/confirm" and fields.get("candidate_resolution_confirmed") != "yes":
                assert fields["evaluation_paid_consent"] == "yes"
                return _html(_candidate_page(self.case))
            if path == "/confirm":
                assert fields["candidate_ref"] == self.case.corp_code
                with sqlite3.connect(self.db) as conn:
                    conn.execute(
                        "INSERT INTO observability_run_lifecycle"
                        "(run_id, state, final_record_json) "
                        "VALUES (?, 'pending', NULL)",
                        (RUN_ID,),
                    )
                    conn.execute(
                        "INSERT INTO budget_spend_events(run_id, cost_krw) VALUES (?, ?)",
                        (RUN_ID, 123.0),
                    )
                return _html(
                    _confirm_page(self.case, legal_name=self.legal_name)
                )
            if path == "/run":
                with sqlite3.connect(self.db) as conn:
                    conn.execute(
                        "UPDATE observability_run_lifecycle "
                        "SET state='final', final_record_json=? "
                        "WHERE run_id=?",
                        (_final_lifecycle_record(123.0), RUN_ID),
                    )
                    conn.execute(
                        "INSERT INTO report_cost_summaries VALUES (?, ?, ?, '')",
                        (RUN_ID, Outcome.REPORT.value, 123.0),
                    )
                    conn.execute(
                        "INSERT INTO reports VALUES (?, ?)",
                        (RUN_ID, self.case.corp_code),
                    )
                return httpx.Response(303, headers={"Location": f"/progress/{RUN_ID}"})
        if request.method == "GET" and path == f"/api/progress/{RUN_ID}":
            return httpx.Response(
                200,
                json={
                    "done": [],
                    "current": "",
                    "finished": True,
                    "next_url": f"/result/{RUN_ID}",
                },
            )
        if request.method == "GET" and path == f"/result/{RUN_ID}":
            with sqlite3.connect(self.db) as conn:
                conn.execute(
                    "UPDATE report_cost_summaries SET automatic_release_sha256=? WHERE run_id=?",
                    ("f" * 64, RUN_ID),
                )
            return _html("<h1>보고서</h1>")
        raise AssertionError(f"unexpected request {request.method} {path}")


def _terminal_resume_handler(request: httpx.Request) -> httpx.Response:
    if request.method == "POST":
        raise AssertionError("resume must not POST")
    if request.url.path == "/healthz":
        return httpx.Response(200, json={"status": "ok"})
    if request.url.path == "/readyz":
        return httpx.Response(200, json={"status": "ready"})
    if request.url.path == "/":
        return _html(_input_page())
    if request.url.path == f"/api/progress/{RUN_ID}":
        return httpx.Response(
            200,
            json={"finished": True, "next_url": f"/result/{RUN_ID}"},
        )
    if request.url.path == f"/result/{RUN_ID}":
        return _html("<h1>결과</h1>")
    raise AssertionError(request.url.path)


def _runner(
    tmp_path: Path,
    handler,
    *,
    cases=CANONICAL_PILOT_CASES,
    approved_paid_case_ids: frozenset[str] | None = None,
):
    db = tmp_path / "storage.db"
    if not db.exists():
        _storage(db)
    checkpoint = CheckpointStore(tmp_path / "canonical-pilot25-checkpoint.json")
    client = httpx.Client(
        transport=httpx.MockTransport(handler),
        base_url=ORIGIN,
        headers={"Origin": ORIGIN},
        follow_redirects=False,
    )
    return (
        CanonicalPilotRunner(
            origin=ORIGIN,
            storage_db_path=db,
            checkpoint=checkpoint,
            client=client,
            cases=cases,
            sleep=lambda _seconds: None,
            poll_interval_sec=0.01,
            poll_timeout_sec=0.1,
            ledger_settle_timeout_sec=0,
            approved_paid_case_ids=(
                frozenset(case.case_id for case in cases)
                if approved_paid_case_ids is None
                else approved_paid_case_ids
            ),
        ),
        client,
        db,
        checkpoint,
    )


RECOVERY_NOW = datetime(2026, 8, 21, 1, 0, tzinfo=timezone.utc)


def _recovery_storage(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        lifecycle.ensure_schema(conn)
        spend_store.ensure_schema(conn)
        conn.execute(
            "CREATE TABLE reports (report_id TEXT PRIMARY KEY, corp_id TEXT)"
        )


def _seed_legacy_legal_name_false_positive(
    tmp_path: Path,
    *,
    paid_at: datetime | None = None,
):
    case = CANONICAL_PILOT_CASES[0]
    db = tmp_path / "storage.db"
    _recovery_storage(db)
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        if request.method != "GET":
            raise AssertionError("recovery must not POST")
        if request.url.path == "/healthz":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/readyz":
            return httpx.Response(200, json={"status": "ready"})
        if request.url.path == "/":
            return _html(_input_page())
        raise AssertionError(request.url.path)

    runner, client, _db, checkpoint = _runner(tmp_path, handler)
    runner.now = lambda: RECOVERY_NOW
    runner.operate(execute=False)
    final_record = json.dumps(
        {
            "run_id": RUN_ID,
            "end_step": "03_확인",
            "cost_krw": 0.0,
            "model": "",
            "fragments_collected": 0,
            "fragments_cited": 0,
            "sentences_made": 0,
            "sentences_passed": 0,
            "cells_filled": 0,
        },
        ensure_ascii=False,
    )
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO observability_run_lifecycle "
            "(run_id, state, at, job, confirmed_cost_krw, elapsed_sec, model, "
            "expires_at, final_record_json) "
            "VALUES (?, 'final', ?, '회사분석', 0, 0, '', NULL, ?)",
            (RUN_ID, "2026-08-21T00:30:00+00:00", final_record),
        )
        conn.execute(
            "INSERT INTO observability_run_lifecycle_audit "
            "(run_id, from_state, to_state, event_at, record_sha256) "
            "VALUES (?, NULL, 'pending', ?, NULL)",
            (RUN_ID, "2026-08-21T00:30:00+00:00"),
        )
        conn.execute(
            "INSERT INTO observability_run_lifecycle_audit "
            "(run_id, from_state, to_state, event_at, record_sha256) "
            "VALUES (?, 'pending', 'final', ?, ?)",
            (RUN_ID, "2026-08-21T00:31:00+00:00", "f" * 64),
        )
    snapshot = checkpoint._load()
    with checkpoint.exclusive():
        runner._update_case(
            snapshot,
            case.case_id,
            state="identity_mismatch",
            run_id=RUN_ID,
            selected_corp_code=case.corp_code,
            legal_name="삼성전자(주)",
            internal_ai_cost_krw=0.0,
            billing_uncertain=False,
            paid_boundary_at=(
                paid_at or (RECOVERY_NOW - timedelta(minutes=20))
            ).isoformat(timespec="seconds"),
            error_code="legal_name_mismatch",
        )
    methods.clear()
    return runner, client, db, checkpoint, methods, case


P01_PAID_AT = datetime(2026, 8, 21, 10, 33, 52, tzinfo=timezone.utc)
P01_NEXT_KST_DAY = datetime(2026, 8, 21, 15, 1, tzinfo=timezone.utc)
P01_KNOWN_COST = 9.18


def _seed_prior_day_restart_unknown(
    tmp_path: Path,
    *,
    recovery_now: datetime = P01_NEXT_KST_DAY,
):
    case = CANONICAL_PILOT_CASES[0]
    db = tmp_path / "storage.db"
    _recovery_storage(db)
    with sqlite3.connect(db) as conn:
        cost_store.ensure_schema(conn)

    current_csrf = [CSRF]
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        if request.method != "GET":
            raise AssertionError("restart recovery must not POST")
        if request.url.path == "/healthz":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/readyz":
            return httpx.Response(200, json={"status": "ready"})
        if request.url.path == "/":
            return _html(_input_page(csrf=current_csrf[0]))
        raise AssertionError(request.url.path)

    runner, client, _db, checkpoint = _runner(tmp_path, handler)
    runner.now = lambda: P01_PAID_AT - timedelta(minutes=20)
    runner.operate(execute=False)

    final_record = json.dumps(
        {
            "run_id": RUN_ID,
            "end_step": "05_생성",
            "cost_krw": P01_KNOWN_COST,
            "model": "claude-haiku-test",
            "fragments_collected": 21,
            "fragments_cited": 0,
            "sentences_made": 0,
            "sentences_passed": 0,
            "cells_filled": 0,
        },
        ensure_ascii=False,
    )
    paid_day = P01_PAID_AT.astimezone(timezone(timedelta(hours=9))).date()
    bucket = "evaluation:loopback"
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO observability_run_lifecycle "
            "(run_id, state, at, job, confirmed_cost_krw, elapsed_sec, model, "
            "expires_at, final_record_json) "
            "VALUES (?, 'final', ?, '회사분석', 0, 11.8, ?, NULL, ?)",
            (
                RUN_ID,
                P01_PAID_AT.isoformat(timespec="seconds"),
                "claude-haiku-test",
                final_record,
            ),
        )
        spend_store.begin_inflight(
            conn,
            run_id=RUN_ID,
            phase=SPEND_PHASE_PIPELINE,
            day=paid_day,
            bucket=bucket,
            started_at=P01_PAID_AT.astimezone(
                timezone(timedelta(hours=9))
            ).isoformat(timespec="seconds"),
            requested_cost_krw=900.0,
            cap_krw=2_200.0,
            run_cap_krw=1_200.0,
        )
        spend_store.keep_inflight_with_known_spend(
            conn,
            run_id=RUN_ID,
            phase=SPEND_PHASE_PIPELINE,
            day=paid_day,
            bucket=bucket,
            cost_krw=P01_KNOWN_COST,
            created_at=(P01_PAID_AT + timedelta(seconds=12)).astimezone(
                timezone(timedelta(hours=9))
            ).isoformat(timespec="seconds"),
        )
        cost_store.record_run_costs(
            conn,
            run_id=RUN_ID,
            outcome=Outcome.FAILED,
            internal_ai_cost_krw=P01_KNOWN_COST,
            events=(
                cost_store.AiCostEvent(
                    stage="collect",
                    model_id="claude-haiku-test",
                    input_tokens=5_901,
                    output_tokens=130,
                    cost_krw=P01_KNOWN_COST,
                ),
            ),
        )

    runner.now = lambda: P01_PAID_AT + timedelta(seconds=12)
    snapshot = checkpoint._load()
    with checkpoint.exclusive():
        runner._update_case(
            snapshot,
            case.case_id,
            state="billing_uncertain",
            run_id=RUN_ID,
            report_id="",
            outcome=Outcome.FAILED.value,
            internal_ai_cost_krw=P01_KNOWN_COST,
            billing_uncertain=True,
            selected_corp_code=case.corp_code,
            legal_name="삼성전자(주)",
            paid_boundary_at=P01_PAID_AT.isoformat(timespec="seconds"),
            result_http_status=200,
            error_code="ledger_inflight_remains",
        )

    current_csrf[0] = NEW_CSRF
    runner.now = lambda: recovery_now
    methods.clear()
    return runner, client, db, checkpoint, methods, case


def test_manifest는_승인된_10_8_7과_정확한_경계법인을_고정한다():
    validate_manifest()
    assert len(CANONICAL_PILOT_CASES) == 25
    assert manifest_sha256() == (
        "1f01b8baad5f793fe0ee5ffe232a01fcd7c19cc504dc90946a13ef4f6ee69796"
    )
    by_id = {case.case_id: case for case in CANONICAL_PILOT_CASES}
    assert (by_id["P19"].input_name, by_id["P19"].expected_legal_name) == (
        "YG",
        "와이지엔터테인먼트",
    )
    assert by_id["P19"].corp_code == "00613318"
    assert by_id["P25"].corp_code == "00670766"
    assert all(not case.job and not case.posting_text for case in CANONICAL_PILOT_CASES)


@pytest.mark.parametrize(
    "origin",
    [
        "https://127.0.0.1:8020",
        "http://localhost:8020",
        "http://192.0.2.1:8020",
        "http://user:pass@127.0.0.1:8020",
        "http://127.0.0.1",
        "http://127.0.0.1:8020/path",
    ],
)
def test_origin은_숫자형_loopback_http_origin만_허용한다(origin):
    with pytest.raises(PilotRunnerError):
        canonical_loopback_origin(origin)
    assert canonical_loopback_origin(ORIGIN) == ORIGIN


def test_dry_run은_GET만_쓰고_token을_checkpoint나_event에_남기지않는다(tmp_path):
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        if request.url.path == "/healthz":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/readyz":
            return httpx.Response(200, json={"status": "ready"})
        return _html(_input_page())

    runner, client, _db, checkpoint = _runner(tmp_path, handler)
    try:
        summary = runner.operate(execute=False)
    finally:
        client.close()
    assert summary.reason == "dry_run"
    assert set(methods) == {"GET"}
    serialized = checkpoint.path.read_text(encoding="utf-8")
    events = checkpoint.events_path.read_text(encoding="utf-8")
    with sqlite3.connect(_db) as conn:
        binding = conn.execute(
            f"SELECT * FROM {PILOT_BINDING_TABLE}"
        ).fetchone()
    assert binding is not None
    assert len(str(binding[8])) == 64
    durable_text = serialized + events + "|".join(str(value) for value in binding)
    assert CSRF not in durable_text
    assert WORKFLOW not in durable_text
    assert "candidate-secret" not in durable_text


def test_새_launcher_DB는_비용표가_아직_없어도_dry_run하고_전용binding만_남긴다(
    tmp_path,
):
    db = tmp_path / "storage.db"
    # Match the schemas initialized by the real launcher/web startup.  The
    # cost-tracking feature owns its table and creates it only when a run ends.
    with storage_db.connect(db) as conn:
        spend_store.ensure_schema(conn)
        lifecycle.ensure_schema(conn)
        # 일반 보고서가 있어도 canonical pilot의 실행 이력은 아니다.
        conn.execute(
            "INSERT INTO reports "
            "(report_id, corp_id, job, payload_json, generated_at, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                "ordinary-report",
                "ordinary-corp",
                "",
                "{}",
                "2026-08-21T00:00:00+00:00",
                "2026-08-21T00:00:00+00:00",
            ),
        )

    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        if request.url.path == "/healthz":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/readyz":
            return httpx.Response(200, json={"status": "ready"})
        return _html(_input_page())

    runner, client, _db, checkpoint = _runner(tmp_path, handler)
    try:
        summary = runner.operate(execute=False)
    finally:
        client.close()

    assert summary.reason == "dry_run"
    assert set(methods) == {"GET"}
    with sqlite3.connect(db) as conn:
        tables = {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        binding = conn.execute(
            f"SELECT binding_id FROM {PILOT_BINDING_TABLE}"
        ).fetchone()
    snapshot = json.loads(checkpoint.path.read_text(encoding="utf-8"))
    assert "report_cost_summaries" not in tables
    assert binding is not None
    assert binding[0] == snapshot["binding_id"]


def test_비용표가_끝까지_없으면_running을_완료로_만들지않는다(tmp_path):
    case = CANONICAL_PILOT_CASES[18]
    db = tmp_path / "storage.db"
    _storage(db, include_cost_summary=False)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/healthz":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/readyz":
            return httpx.Response(200, json={"status": "ready"})
        if request.url.path == "/":
            return _html(_input_page())
        if request.url.path == f"/api/progress/{RUN_ID}":
            return httpx.Response(
                200,
                json={"finished": True, "next_url": f"/result/{RUN_ID}"},
            )
        if request.url.path == f"/result/{RUN_ID}":
            return _html("<h1>결과</h1>")
        raise AssertionError(request.url.path)

    runner, client, _db, checkpoint = _runner(tmp_path, handler)
    try:
        runner.operate(execute=False)
        with sqlite3.connect(db) as conn:
            conn.execute(
                "INSERT INTO observability_run_lifecycle VALUES (?, 'final', ?)",
                (RUN_ID, _final_lifecycle_record(0.0)),
            )
        snapshot = checkpoint._load()
        with checkpoint.exclusive():
            runner._update_case(
                snapshot,
                case.case_id,
                state="running",
                run_id=RUN_ID,
                selected_corp_code=case.corp_code,
            )
        with pytest.raises(PilotRunnerError, match="비용 원장"):
            runner.operate(execute=True, case_ids=(case.case_id,))
    finally:
        client.close()

    row = checkpoint._load()["cases"][case.case_id]
    assert row["state"] == "running"
    assert row["error_code"] == "ledger_pending"
    assert row["outcome"] == ""
    assert row["internal_ai_cost_krw"] is None


def test_비용표가_있다면_필수증거열이_없을때_POST전에_막는다(tmp_path):
    db = tmp_path / "storage.db"
    _storage(db, include_cost_summary=False)
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE report_cost_summaries (run_id TEXT PRIMARY KEY)")

    requests = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        raise AssertionError("invalid ledger schema must fail before HTTP")

    runner, client, _db, _checkpoint = _runner(tmp_path, handler)
    try:
        with pytest.raises(PilotRunnerError, match="비용 원장 표"):
            runner.operate(execute=True, case_ids=("P19",))
    finally:
        client.close()
    assert requests == 0


def test_DB_binding이_있으면_checkpoint_분실이나_다른이름으로_새batch를_막는다(
    tmp_path,
):
    posts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal posts
        if request.method == "POST":
            posts += 1
            raise AssertionError("POST must not happen")
        if request.url.path == "/healthz":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/readyz":
            return httpx.Response(200, json={"status": "ready"})
        return _html(_input_page())

    runner, client, db, checkpoint = _runner(tmp_path, handler)
    try:
        runner.operate(execute=False)

        replacement = CheckpointStore(tmp_path / "replacement-checkpoint.json")
        replacement_runner = CanonicalPilotRunner(
            origin=ORIGIN,
            storage_db_path=db,
            checkpoint=replacement,
            client=client,
            sleep=lambda _seconds: None,
            poll_interval_sec=0.01,
            poll_timeout_sec=0.1,
            ledger_settle_timeout_sec=0,
            approved_paid_case_ids=frozenset(
                case.case_id for case in CANONICAL_PILOT_CASES
            ),
        )
        with pytest.raises(CheckpointError, match="binding"):
            replacement_runner.operate(execute=True, case_ids=("P19",))
        assert not replacement.path.exists()

        checkpoint.path.unlink()
        with pytest.raises(CheckpointError, match="체크포인트가 없어"):
            runner.operate(execute=True, case_ids=("P19",))
    finally:
        client.close()

    assert posts == 0


def test_정확한_candidate_ref를_선택하고_동일run_id_원장과_보고서를_완료한다(tmp_path):
    case = CANONICAL_PILOT_CASES[18]
    flow = SuccessfulFlow(tmp_path / "storage.db", case)
    runner, client, _db, checkpoint = _runner(tmp_path, flow)
    try:
        summary = runner.operate(execute=True, case_ids=(case.case_id,))
        first_post_count = len(flow.posts)
        replay = runner.operate(execute=True, case_ids=(case.case_id,))
    finally:
        client.close()
    assert summary.executed_case_ids == ("P19",)
    assert replay.executed_case_ids == ()
    assert len(flow.posts) == first_post_count
    snapshot = json.loads(checkpoint.path.read_text(encoding="utf-8"))
    row = snapshot["cases"]["P19"]
    assert row["state"] == "completed"
    assert row["run_id"] == row["report_id"] == RUN_ID
    assert row["selected_corp_code"] == "00613318"
    assert row["outcome"] == Outcome.REPORT.value
    assert row["internal_ai_cost_krw"] == 123.0
    assert row["billing_uncertain"] is False
    assert flow.posts == ["/confirm", "/confirm", "/run"]


def test_결속뒤_checkpoint를_pending으로_되돌리면_같은case_재과금을_막는다(tmp_path):
    case = CANONICAL_PILOT_CASES[18]
    flow = SuccessfulFlow(tmp_path / "storage.db", case)
    runner, client, _db, checkpoint = _runner(tmp_path, flow)
    try:
        runner.operate(execute=True, case_ids=(case.case_id,))
        post_count = len(flow.posts)
        snapshot = json.loads(checkpoint.path.read_text(encoding="utf-8"))
        row = snapshot["cases"][case.case_id]
        row.update(
            {
                "state": "pending",
                "run_id": "",
                "report_id": "",
                "outcome": "",
                "internal_ai_cost_krw": None,
                "paid_boundary_at": "",
            }
        )
        checkpoint.path.write_text(
            json.dumps(snapshot, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )

        with pytest.raises(CheckpointError, match="체크포인트 내용"):
            runner.operate(execute=True, case_ids=(case.case_id,))
    finally:
        client.close()

    assert len(flow.posts) == post_count


def test_manifest_corp_code가_후보에_없으면_유료식별과_run을_호출하지않는다(tmp_path):
    case = CANONICAL_PILOT_CASES[18]
    posts: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/healthz":
            return httpx.Response(200, json={"status": "ok"})
        if request.method == "GET" and request.url.path == "/readyz":
            return httpx.Response(200, json={"status": "ready"})
        if request.method == "GET" and request.url.path == "/":
            return _html(_input_page())
        if request.method == "GET" and request.url.path == f"/api/progress/{RUN_ID}":
            return httpx.Response(
                200,
                json={
                    "done": [],
                    "current": "",
                    "finished": True,
                    "next_url": f"/result/{RUN_ID}",
                },
            )
        if request.method == "GET" and request.url.path == f"/result/{RUN_ID}":
            return _html("<h1>결과</h1>")
        posts.append(request.url.path)
        return _html(_candidate_page(case, corp_code="99999999"))

    runner, client, _db, checkpoint = _runner(tmp_path, handler)
    try:
        runner.operate(execute=True, case_ids=(case.case_id,))
    finally:
        client.close()
    assert posts == ["/confirm"]
    row = json.loads(checkpoint.path.read_text(encoding="utf-8"))["cases"]["P19"]
    assert row["state"] == "identity_mismatch"
    assert row["billing_uncertain"] is False


def test_직접확인카드의_관측된_DART번호가_manifest와같으면_후보폼없이도_안전하게실행한다(
    tmp_path,
):
    case = CANONICAL_PILOT_CASES[1]
    posts: list[str] = []
    db = tmp_path / "storage.db"
    _storage(db)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/healthz":
            return httpx.Response(200, json={"status": "ok"})
        if request.method == "GET" and request.url.path == "/readyz":
            return httpx.Response(200, json={"status": "ready"})
        if request.method == "GET" and request.url.path == "/":
            return _html(_input_page())
        if request.method == "GET" and request.url.path == f"/api/progress/{RUN_ID}":
            return httpx.Response(
                200,
                json={
                    "done": [],
                    "current": "",
                    "finished": True,
                    "next_url": f"/result/{RUN_ID}",
                },
            )
        if request.method == "GET" and request.url.path == f"/result/{RUN_ID}":
            return _html("<h1>결과</h1>")
        posts.append(request.url.path)
        if request.url.path == "/confirm":
            with sqlite3.connect(db) as conn:
                conn.execute(
                    "INSERT INTO observability_run_lifecycle"
                    "(run_id, state, final_record_json) VALUES (?, 'pending', NULL)",
                    (RUN_ID,),
                )
                conn.execute(
                    "INSERT INTO budget_spend_events(run_id, cost_krw) VALUES (?, ?)",
                    (RUN_ID, 0.0),
                )
            return _html(_confirm_page(case, confirmed_dart_ref=case.corp_code))
        if request.url.path == "/run":
            with sqlite3.connect(db) as conn:
                conn.execute(
                    "UPDATE observability_run_lifecycle "
                    "SET state='final', final_record_json=? WHERE run_id=?",
                    (_final_lifecycle_record(0.0), RUN_ID),
                )
                conn.execute(
                    "INSERT INTO report_cost_summaries VALUES (?, ?, ?, '')",
                    (RUN_ID, Outcome.FAILED.value, 0.0),
                )
            return httpx.Response(303, headers={"Location": f"/progress/{RUN_ID}"})
        raise AssertionError(request.url.path)

    runner, client, _db, checkpoint = _runner(tmp_path, handler)
    try:
        summary = runner.operate(execute=True, case_ids=(case.case_id,))
    finally:
        client.close()

    row = checkpoint._load()["cases"][case.case_id]
    assert summary.executed_case_ids == (case.case_id,)
    assert posts == ["/confirm", "/run"]
    assert row["state"] == "completed"
    assert row["selected_corp_code"] == case.corp_code


def test_P02_점검429가_provider_전임을_증명하면_재시도준비로만_복구한다(tmp_path):
    case = CANONICAL_PILOT_CASES[1]
    current_csrf = [CSRF]
    methods: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods.append(request.method)
        if request.url.path == "/healthz":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/readyz":
            return httpx.Response(200, json={"status": "ready"})
        if request.url.path == "/":
            return _html(_input_page(csrf=current_csrf[0]))
        raise AssertionError(request.url.path)

    runner, client, db, checkpoint = _runner(tmp_path, handler)
    paid_at = datetime(2026, 8, 21, 3, 16, 21, tzinfo=timezone.utc)
    try:
        runner.operate(execute=False)
        with storage_db.connect(db) as conn:
            dashboard_store.set_service_state(
                conn,
                status=dashboard_store.SERVICE_MAINTENANCE,
                cause="DART 후보 응답 해석 실패",
                impact="새 보고서 생성을 멈췄습니다.",
                next_action="수정과 재검사 뒤 관리자 재시작",
                actor_email="operator@example.com",
                now_iso="2026-08-21T12:00:00+09:00",
            )
        snapshot = checkpoint._load()
        with checkpoint.exclusive():
            runner._update_case(
                snapshot,
                case.case_id,
                state=SERVICE_MAINTENANCE_BLOCKED_STATE,
                run_id="",
                report_id="",
                outcome="",
                internal_ai_cost_krw=None,
                billing_uncertain=False,
                selected_corp_code="",
                legal_name="",
                paid_boundary_at=paid_at.isoformat(timespec="seconds"),
                result_http_status=429,
                error_code=SERVICE_MAINTENANCE_BLOCKED_ERROR,
            )
        with storage_db.connect(db) as conn:
            dashboard_store.set_service_state(
                conn,
                status=dashboard_store.SERVICE_NORMAL,
                cause="DART 후보 profile 보강 오류를 전역점검에서 분리",
                impact="후보 확인 실패는 재시도 화면으로만 제한",
                next_action="P02 재시도 전 유료 경계와 회귀 시험을 다시 확인",
                actor_email="operator@example.com",
                now_iso="2026-08-21T13:30:00+09:00",
            )
        current_csrf[0] = NEW_CSRF
        methods.clear()
        summary = runner.recover_service_maintenance_pre_provider(case.case_id)
    finally:
        client.close()

    row = checkpoint._load()["cases"][case.case_id]
    assert summary.reason == SERVICE_MAINTENANCE_RECOVERY_READY_STATE
    assert row["state"] == SERVICE_MAINTENANCE_RECOVERY_READY_STATE
    assert row["billing_uncertain"] is False
    assert row["service_maintenance_429_proven"] is True
    assert row["run_id"] == row["report_id"] == ""
    assert methods and set(methods) == {"GET"}


def test_서버가_명시한_점검429만_P02_재시도차단표식으로_기록한다(tmp_path):
    case = CANONICAL_PILOT_CASES[1]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/healthz":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/readyz":
            return httpx.Response(200, json={"status": "ready"})
        if request.url.path == "/":
            return _html(_input_page())
        if request.method == "POST" and request.url.path == "/confirm":
            return _html(
                "<h1>점검</h1>",
                status=429,
                **{"X-Company-Analysis-Block": "service-maintenance"},
            )
        raise AssertionError(request.url.path)

    runner, client, _db, checkpoint = _runner(tmp_path, handler)
    try:
        runner.operate(execute=False)
        summary = runner.operate(execute=True, case_ids=(case.case_id,))
    finally:
        client.close()

    row = checkpoint._load()["cases"][case.case_id]
    assert summary.executed_case_ids == (case.case_id,)
    assert row["state"] == SERVICE_MAINTENANCE_BLOCKED_STATE
    assert row["error_code"] == SERVICE_MAINTENANCE_BLOCKED_ERROR
    assert row["result_http_status"] == 429
    assert row["billing_uncertain"] is False


def test_서버429_증거가없는_옛_P02_재시도준비상태는_POST전에_차단한다(tmp_path):
    requests = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        raise AssertionError("증거 없는 복구는 HTTP 전에 막아야 합니다")

    runner, client, _db, checkpoint = _runner(tmp_path, handler)
    snapshot = {
        "cases": {
            case.case_id: {
                "case_id": case.case_id,
                "state": "pending",
                "billing_uncertain": False,
            }
            for case in CANONICAL_PILOT_CASES
        }
    }
    snapshot["cases"]["P02"].update(
        state=SERVICE_MAINTENANCE_RECOVERY_READY_STATE,
        paid_boundary_at="2026-08-21T03:16:21+00:00",
    )
    try:
        with pytest.raises(PilotBatchBlocked, match="429 증거"):
            runner.execute_pending(snapshot, case_ids=("P02",), max_cases=1)
    finally:
        client.close()
    assert requests == 0


def test_case직전_server_instance가_바뀌면_POST전에_차단한다(tmp_path):
    gets = 0
    posts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal gets, posts
        if request.method == "POST":
            posts += 1
            raise AssertionError("POST must not happen")
        if request.url.path == "/healthz":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/readyz":
            return httpx.Response(200, json={"status": "ready"})
        gets += 1
        return _html(_input_page(csrf=CSRF if gets == 1 else "d" * 64))

    runner, client, _db, _checkpoint = _runner(tmp_path, handler)
    try:
        with pytest.raises(CheckpointError, match="instance"):
            runner.operate(execute=True, case_ids=("P19",))
    finally:
        client.close()
    assert posts == 0


def test_run응답을_잃으면_미확정으로_고정하고_다음호출과_재시도를_막는다(tmp_path):
    case = CANONICAL_PILOT_CASES[18]
    flow = SuccessfulFlow(tmp_path / "storage.db", case)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "POST" and request.url.path == "/run":
            flow.posts.append("/run")
            raise httpx.ReadError("lost", request=request)
        return flow(request)

    runner, client, _db, checkpoint = _runner(tmp_path, handler)
    try:
        with pytest.raises(PilotBatchBlocked):
            runner.operate(execute=True, case_ids=("P19",))
        post_count = len(flow.posts)
        with pytest.raises(PilotBatchBlocked, match="미확정"):
            runner.operate(execute=True, case_ids=("P19",))
    finally:
        client.close()
    assert len(flow.posts) == post_count
    row = json.loads(checkpoint.path.read_text(encoding="utf-8"))["cases"]["P19"]
    assert row["state"] == "run_submission_started"
    assert row["billing_uncertain"] is True


def test_10분안의_다섯case가_있으면_sleep없이_재개시각만_돌려준다(tmp_path):
    now = datetime(2026, 8, 21, 0, 5, tzinfo=timezone.utc)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/healthz":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/readyz":
            return httpx.Response(200, json={"status": "ready"})
        return _html(_input_page())

    runner, client, _db, checkpoint = _runner(tmp_path, handler)
    runner.now = lambda: now
    try:
        runner.operate(execute=False)
        snapshot = checkpoint._load()
        with checkpoint.exclusive():
            for index in range(1, 6):
                runner._update_case(
                    snapshot,
                    f"P{index:02d}",
                    state="completed",
                    paid_boundary_at="2026-08-21T00:00:00+00:00",
                )
        summary = runner.operate(execute=True, case_ids=("P06",))
    finally:
        client.close()
    assert summary.executed_case_ids == ()
    assert summary.reason == "rate_window"
    assert summary.next_recommended_at == "2026-08-21T00:10:00+00:00"


def test_running_case는_confirm이나_run없이_같은서버와DB에서_progress만_재개한다(tmp_path):
    case = CANONICAL_PILOT_CASES[18]
    posts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal posts
        if request.method == "POST":
            posts += 1
            raise AssertionError("resume must not POST")
        if request.url.path == "/healthz":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/readyz":
            return httpx.Response(200, json={"status": "ready"})
        if request.url.path == "/":
            return _html(_input_page())
        if request.url.path == f"/api/progress/{RUN_ID}":
            return httpx.Response(
                200,
                json={"finished": True, "next_url": f"/result/{RUN_ID}"},
            )
        if request.url.path == f"/result/{RUN_ID}":
            return _html("<h1>보고서</h1>")
        raise AssertionError(request.url.path)

    runner, client, db, checkpoint = _runner(tmp_path, handler)
    try:
        runner.operate(execute=False)
        with sqlite3.connect(db) as conn:
            conn.execute(
                "INSERT INTO observability_run_lifecycle VALUES (?, 'final', ?)",
                (RUN_ID, _final_lifecycle_record(55.0)),
            )
            conn.execute(
                "INSERT INTO budget_spend_events VALUES (?, ?)",
                (RUN_ID, 55.0),
            )
            conn.execute(
                "INSERT INTO report_cost_summaries VALUES (?, ?, ?, ?)",
                (RUN_ID, Outcome.REPORT.value, 55.0, "f" * 64),
            )
            conn.execute("INSERT INTO reports VALUES (?, ?)", (RUN_ID, case.corp_code))
        snapshot = checkpoint._load()
        with checkpoint.exclusive():
            runner._update_case(
                snapshot,
                "P19",
                state="running",
                run_id=RUN_ID,
                selected_corp_code=case.corp_code,
            )
        summary = runner.operate(execute=True, case_ids=("P19",))
    finally:
        client.close()
    assert posts == 0
    assert summary.executed_case_ids == ("P19",)
    row = checkpoint._load()["cases"]["P19"]
    assert row["state"] == "completed"
    assert row["report_id"] == RUN_ID


def test_비용요약과_단계별합계가_다르면_완료하지않고_다음호출을_막는다(tmp_path):
    case = CANONICAL_PILOT_CASES[18]
    runner, client, db, checkpoint = _runner(tmp_path, _terminal_resume_handler)
    try:
        runner.operate(execute=False)
        with sqlite3.connect(db) as conn:
            conn.execute(
                "INSERT INTO observability_run_lifecycle VALUES (?, 'final', ?)",
                (RUN_ID, _final_lifecycle_record(55.0)),
            )
            conn.execute(
                "INSERT INTO budget_spend_events VALUES (?, ?)",
                (RUN_ID, 7.0),
            )
            conn.execute(
                "INSERT INTO report_cost_summaries VALUES (?, ?, ?, ?)",
                (RUN_ID, Outcome.REPORT.value, 55.0, "f" * 64),
            )
            conn.execute("INSERT INTO reports VALUES (?, ?)", (RUN_ID, case.corp_code))
        snapshot = checkpoint._load()
        with checkpoint.exclusive():
            runner._update_case(
                snapshot,
                case.case_id,
                state="running",
                run_id=RUN_ID,
                selected_corp_code=case.corp_code,
            )

        with pytest.raises(PilotBatchBlocked, match="비용 합계"):
            runner.operate(execute=True, case_ids=(case.case_id,))
    finally:
        client.close()

    row = checkpoint._load()["cases"][case.case_id]
    assert row["state"] == "billing_uncertain"
    assert row["billing_uncertain"] is True
    assert row["error_code"] == "ledger_cost_mismatch"


def test_lifecycle가_final아니면_비용표가_있어도_완료하지않는다(tmp_path):
    case = CANONICAL_PILOT_CASES[18]
    runner, client, db, checkpoint = _runner(tmp_path, _terminal_resume_handler)
    try:
        runner.operate(execute=False)
        with sqlite3.connect(db) as conn:
            conn.execute(
                "INSERT INTO observability_run_lifecycle VALUES (?, 'running', NULL)",
                (RUN_ID,),
            )
            conn.execute(
                "INSERT INTO budget_spend_events VALUES (?, ?)",
                (RUN_ID, 55.0),
            )
            conn.execute(
                "INSERT INTO report_cost_summaries VALUES (?, ?, ?, ?)",
                (RUN_ID, Outcome.REPORT.value, 55.0, "f" * 64),
            )
            conn.execute("INSERT INTO reports VALUES (?, ?)", (RUN_ID, case.corp_code))
        snapshot = checkpoint._load()
        with checkpoint.exclusive():
            runner._update_case(
                snapshot,
                case.case_id,
                state="running",
                run_id=RUN_ID,
                selected_corp_code=case.corp_code,
            )

        with pytest.raises(PilotRunnerError, match="비용 원장 마감"):
            runner.operate(execute=True, case_ids=(case.case_id,))
    finally:
        client.close()

    row = checkpoint._load()["cases"][case.case_id]
    assert row["state"] == "running"
    assert row["error_code"] == "ledger_pending"


def test_정상0원_실패원장은_보고서없이_완료한다(tmp_path):
    case = CANONICAL_PILOT_CASES[18]
    runner, client, db, checkpoint = _runner(tmp_path, _terminal_resume_handler)
    try:
        runner.operate(execute=False)
        with sqlite3.connect(db) as conn:
            conn.execute(
                "INSERT INTO observability_run_lifecycle VALUES (?, 'final', ?)",
                (RUN_ID, _final_lifecycle_record(0.0)),
            )
            conn.execute(
                "INSERT INTO report_cost_summaries VALUES (?, ?, ?, ?)",
                (RUN_ID, Outcome.FAILED.value, 0.0, ""),
            )
        snapshot = checkpoint._load()
        with checkpoint.exclusive():
            runner._update_case(
                snapshot,
                case.case_id,
                state="running",
                run_id=RUN_ID,
                selected_corp_code=case.corp_code,
            )

        summary = runner.operate(execute=True, case_ids=(case.case_id,))
    finally:
        client.close()

    row = checkpoint._load()["cases"][case.case_id]
    assert summary.completed_case_ids == (case.case_id,)
    assert row["state"] == "completed"
    assert row["outcome"] == Outcome.FAILED.value
    assert row["internal_ai_cost_krw"] == 0.0


def test_선택밖_global_running_case가_있으면_새pending을_시작하지않는다(tmp_path):
    running_case = CANONICAL_PILOT_CASES[18]
    posts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal posts
        if request.method == "POST":
            posts += 1
            raise AssertionError("another case must not POST")
        if request.url.path == "/healthz":
            return httpx.Response(200, json={"status": "ok"})
        if request.url.path == "/readyz":
            return httpx.Response(200, json={"status": "ready"})
        if request.url.path == "/":
            return _html(_input_page())
        raise AssertionError(request.url.path)

    runner, client, db, checkpoint = _runner(tmp_path, handler)
    try:
        runner.operate(execute=False)
        with sqlite3.connect(db) as conn:
            conn.execute(
                "INSERT INTO observability_run_lifecycle VALUES (?, 'running', NULL)",
                (RUN_ID,),
            )
        snapshot = checkpoint._load()
        with checkpoint.exclusive():
            runner._update_case(
                snapshot,
                running_case.case_id,
                state="running",
                run_id=RUN_ID,
                selected_corp_code=running_case.corp_code,
            )
        with pytest.raises(PilotBatchBlocked, match="선택 밖"):
            runner.operate(execute=True, case_ids=("P20",))
    finally:
        client.close()

    assert posts == 0
    rows = checkpoint._load()["cases"]
    assert rows["P19"]["state"] == "running"
    assert rows["P20"]["state"] == "pending"


def test_정확한_DART_corp_code뒤_주식회사_표기차이는_run을_막지않는다(tmp_path):
    case = CANONICAL_PILOT_CASES[0]
    flow = SuccessfulFlow(
        tmp_path / "storage.db",
        case,
        legal_name="삼성전자(주)",
    )
    runner, client, _db, checkpoint = _runner(tmp_path, flow)
    try:
        summary = runner.operate(execute=True, case_ids=(case.case_id,))
    finally:
        client.close()

    row = checkpoint._load()["cases"][case.case_id]
    assert summary.completed_case_ids == (case.case_id,)
    assert row["state"] == "completed"
    assert row["selected_corp_code"] == case.corp_code
    assert row["legal_name"] == "삼성전자(주)"
    assert flow.posts == ["/confirm", "/confirm", "/run"]


def test_봉인된_0원_회사확인오탐만_GET으로_복구준비하고_다른terminal은_보존한다(
    tmp_path,
):
    runner, client, _db, checkpoint, methods, case = (
        _seed_legacy_legal_name_false_positive(tmp_path)
    )
    snapshot = checkpoint._load()
    with checkpoint.exclusive():
        runner._update_case(
            snapshot,
            "P02",
            state="stopped_before_run",
            outcome="사용자 중단",
            error_code="operator_stop",
        )
    before_p02 = dict(checkpoint._load()["cases"]["P02"])

    try:
        summary = runner.recover_legal_name_mismatch(case.case_id)
        sealed_replay = runner.operate(execute=False)
        recognized = runner.operate(
            execute=True,
            case_ids=(case.case_id,),
            max_cases=0,
        )
    finally:
        client.close()

    rows = checkpoint._load()["cases"]
    assert summary.reason == LEGAL_NAME_RECOVERY_READY_STATE
    assert sealed_replay.reason == "dry_run"
    assert recognized.reason == "max_cases"
    assert rows[case.case_id]["state"] == LEGAL_NAME_RECOVERY_READY_STATE
    assert rows[case.case_id]["error_code"] == LEGAL_NAME_RECOVERY_READY_ERROR
    assert rows[case.case_id]["run_id"] == RUN_ID
    assert rows[case.case_id]["selected_corp_code"] == case.corp_code
    assert rows[case.case_id]["internal_ai_cost_krw"] == 0.0
    assert rows["P02"] == before_p02
    assert methods and set(methods) == {"GET"}


@pytest.mark.parametrize(
    ("state", "changes", "message"),
    [
        (
            "identity_mismatch",
            {"selected_corp_code": "99999999"},
            "DART 고유번호",
        ),
        (
            "identity_mismatch",
            {"error_code": "stored_report_identity_mismatch"},
            "오류 코드",
        ),
        (
            "identity_mismatch",
            {"legal_name": "삼성전기(주)"},
            "exact-equivalence",
        ),
        (
            "identity_mismatch",
            {"legal_name": "삼성전자Α(주)"},
            "exact-equivalence",
        ),
        (
            "identity_mismatch",
            {"internal_ai_cost_krw": 0.01},
            "0원",
        ),
        (
            "identity_mismatch",
            {"report_id": "2" * 32},
            "보고서 흔적",
        ),
        (
            "identity_mismatch",
            {"billing_uncertain": True},
            "비용 미확정",
        ),
        ("completed", {}, "identity_mismatch"),
    ],
)
def test_복구는_checkpoint의_특정_false_positive모양이_아니면_한건도_바꾸지않는다(
    tmp_path,
    state,
    changes,
    message,
):
    runner, client, _db, checkpoint, methods, case = (
        _seed_legacy_legal_name_false_positive(tmp_path)
    )
    snapshot = checkpoint._load()
    with checkpoint.exclusive():
        runner._update_case(
            snapshot,
            case.case_id,
            state=state,
            **changes,
        )
    before = dict(checkpoint._load()["cases"][case.case_id])

    try:
        with pytest.raises(PilotRunnerError, match=message):
            runner.recover_legal_name_mismatch(case.case_id)
    finally:
        client.close()

    assert checkpoint._load()["cases"][case.case_id] == before
    assert methods and set(methods) == {"GET"}


@pytest.mark.parametrize(
    "evidence",
    [
        "running_audit",
        "spend_event",
        "inflight",
        "report",
        "cost_summary",
        "nonzero_lifecycle",
    ],
)
def test_복구는_DB에_run비용보고서흔적이_하나라도_있으면_fail_closed한다(
    tmp_path,
    evidence,
):
    runner, client, db, checkpoint, methods, case = (
        _seed_legacy_legal_name_false_positive(tmp_path)
    )
    with sqlite3.connect(db) as conn:
        if evidence == "running_audit":
            conn.execute(
                "INSERT INTO observability_run_lifecycle_audit "
                "(run_id, from_state, to_state, event_at, record_sha256) "
                "VALUES (?, 'final', 'running', ?, NULL)",
                (RUN_ID, "2026-08-21T00:32:00+00:00"),
            )
        elif evidence == "spend_event":
            conn.execute(
                "INSERT INTO budget_spend_events "
                "(run_id, phase, day, bucket_id, cost_krw, created_at) "
                "VALUES (?, 'company_identity', '2026-08-21', ?, 0, ?)",
                (RUN_ID, "b" * 64, "2026-08-21T00:30:00+00:00"),
            )
        elif evidence == "inflight":
            conn.execute(
                "INSERT INTO budget_spend_inflight "
                "(run_id, phase, day, bucket_id, reserved_krw, started_at) "
                "VALUES (?, 'company_identity', '2026-08-21', ?, 0, ?)",
                (RUN_ID, "b" * 64, "2026-08-21T00:30:00+00:00"),
            )
        elif evidence == "report":
            conn.execute(
                "INSERT INTO reports VALUES (?, ?)",
                (RUN_ID, case.corp_code),
            )
        elif evidence == "cost_summary":
            conn.execute(
                "CREATE TABLE report_cost_summaries ("
                "run_id TEXT PRIMARY KEY, outcome TEXT, "
                "internal_ai_cost_krw REAL, automatic_release_sha256 TEXT)"
            )
            conn.execute(
                "INSERT INTO report_cost_summaries VALUES (?, ?, 0, '')",
                (RUN_ID, Outcome.FAILED.value),
            )
        elif evidence == "nonzero_lifecycle":
            conn.execute(
                "UPDATE observability_run_lifecycle "
                "SET confirmed_cost_krw=1 WHERE run_id=?",
                (RUN_ID,),
            )

    before = dict(checkpoint._load()["cases"][case.case_id])
    try:
        with pytest.raises(PilotRunnerError):
            runner.recover_legal_name_mismatch(case.case_id)
    finally:
        client.close()

    assert checkpoint._load()["cases"][case.case_id] == before
    assert methods and set(methods) == {"GET"}


def test_복구는_기존_paid_boundary가_10분창을_벗어난뒤에만_허용한다(tmp_path):
    runner, client, _db, checkpoint, methods, case = (
        _seed_legacy_legal_name_false_positive(
            tmp_path,
            paid_at=RECOVERY_NOW - timedelta(minutes=9, seconds=59),
        )
    )
    before = dict(checkpoint._load()["cases"][case.case_id])
    try:
        with pytest.raises(PilotRunnerError, match="다음 시각 이후"):
            runner.recover_legal_name_mismatch(case.case_id)
    finally:
        client.close()

    assert checkpoint._load()["cases"][case.case_id] == before
    assert methods and set(methods) == {"GET"}


def test_CLI는_복구준비와_유료execute를_한명령에서_함께_허용하지않는다():
    from tools.run_canonical_pilot25 import _parser

    parser = _parser()
    recovered = parser.parse_args(
        [
            "--origin",
            ORIGIN,
            "--storage-db",
            "storage.db",
            "--recover-legal-name-mismatch",
            "P01",
        ]
    )
    assert recovered.execute is False
    assert recovered.recover_legal_name_mismatch == "P01"

    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "--origin",
                ORIGIN,
                "--storage-db",
                "storage.db",
                "--recover-legal-name-mismatch",
                "P01",
                "--execute",
            ]
        )


def test_CLI는_case를_명시하지_않은_유료전체실행을_차단한다(capsys):
    from tools.run_canonical_pilot25 import main

    result = main(
        [
            "--origin",
            ORIGIN,
            "--storage-db",
            "storage.db",
            "--execute",
        ]
    )

    assert result == 2
    assert "--case-id" in capsys.readouterr().err


def test_runner도_유료승인범위를_HTTP와_checkpoint전에_차단한다(tmp_path):
    requests = 0

    def no_request_allowed(_request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        raise AssertionError("승인 범위 검사 전에 HTTP 요청을 보냈습니다")

    runner, client, _db, checkpoint = _runner(
        tmp_path,
        no_request_allowed,
        approved_paid_case_ids=APPROVED_PAID_CASE_IDS,
    )
    try:
        with pytest.raises(PilotRunnerError, match="case ID"):
            runner.operate(execute=True)
        with pytest.raises(PilotRunnerError, match="P01~P10"):
            runner.operate(execute=True, case_ids=("P11",))
        with pytest.raises(PilotRunnerError, match="P01~P10"):
            runner.execute_pending({}, case_ids=("P11",))
    finally:
        client.close()

    assert requests == 0
    assert not checkpoint.path.exists()


def test_CLI의_유료승인범위는_P10까지이고_P11부터는_연결전에_차단한다(
    monkeypatch, capsys
):
    from tools import run_canonical_pilot25 as pilot_cli

    parser = pilot_cli._parser()
    approved = parser.parse_args(
        [
            "--origin",
            ORIGIN,
            "--storage-db",
            "storage.db",
            "--execute",
            "--case-id",
            "P10",
        ]
    )
    pilot_cli._validate_paid_scope(approved)

    outside = parser.parse_args(
        [
            "--origin",
            ORIGIN,
            "--storage-db",
            "storage.db",
            "--execute",
            "--case-id",
            "P11",
        ]
    )
    with pytest.raises(PilotRunnerError, match="P01~P10"):
        pilot_cli._validate_paid_scope(outside)

    def provider_connection_must_not_exist(*_args, **_kwargs):
        raise AssertionError("승인 범위 검사 전에 HTTP client를 만들었습니다")

    monkeypatch.setattr(pilot_cli.httpx, "Client", provider_connection_must_not_exist)
    result = pilot_cli.main(
        [
            "--origin",
            ORIGIN,
            "--storage-db",
            "storage.db",
            "--execute",
            "--case-id",
            "P11",
        ]
    )
    assert result == 2
    assert "P01~P10" in capsys.readouterr().err


def test_전날_P01_미확정은_inflight를_보존하고_GET만으로_새서버에_재결속한다(
    tmp_path,
):
    runner, client, db, checkpoint, methods, case = _seed_prior_day_restart_unknown(
        tmp_path
    )
    try:
        with sqlite3.connect(db) as conn:
            inflight_before = conn.execute(
                "SELECT * FROM budget_spend_inflight WHERE run_id=?", (RUN_ID,)
            ).fetchall()

        summary = runner.recover_prior_day_restart(case.case_id)

        snapshot = checkpoint._load()
        recovered = snapshot["cases"][case.case_id]
        assert summary.reason == PRIOR_DAY_BILLING_UNCERTAIN_STATE
        assert recovered["state"] == PRIOR_DAY_BILLING_UNCERTAIN_STATE
        assert recovered["billing_uncertain"] is True
        assert recovered["error_code"] == PRIOR_DAY_BILLING_UNCERTAIN_ERROR
        assert snapshot["server_instance_sha256"] == hashlib.sha256(
            NEW_CSRF.encode("utf-8")
        ).hexdigest()
        checkpoint_digest = hashlib.sha256(checkpoint.path.read_bytes()).hexdigest()
        with sqlite3.connect(db) as conn:
            inflight_after = conn.execute(
                "SELECT * FROM budget_spend_inflight WHERE run_id=?", (RUN_ID,)
            ).fetchall()
            binding = conn.execute(
                f"SELECT server_instance_sha256, checkpoint_content_sha256 "
                f"FROM {PILOT_BINDING_TABLE}"
            ).fetchone()
        assert inflight_after == inflight_before
        assert binding == (
            snapshot["server_instance_sha256"],
            checkpoint_digest,
        )
        assert methods and set(methods) == {"GET"}

        methods.clear()
        dry_run = runner.operate(execute=False)
        assert dry_run.reason == "dry_run"
        assert methods and set(methods) == {"GET"}

        methods.clear()
        no_post = runner.operate(
            execute=True,
            case_ids=(CANONICAL_PILOT_CASES[1].case_id,),
            max_cases=0,
        )
        assert no_post.reason == "max_cases"
        assert methods and set(methods) == {"GET"}
    finally:
        client.close()


def test_전날_P01_복구는_같은_KST사업일에는_checkpoint를_바꾸지않는다(tmp_path):
    runner, client, db, checkpoint, methods, case = _seed_prior_day_restart_unknown(
        tmp_path,
        recovery_now=P01_PAID_AT + timedelta(hours=1),
    )
    before = checkpoint.path.read_bytes()
    with sqlite3.connect(db) as conn:
        binding_before = conn.execute(
            f"SELECT * FROM {PILOT_BINDING_TABLE}"
        ).fetchall()
        inflight_before = conn.execute(
            "SELECT * FROM budget_spend_inflight"
        ).fetchall()
    try:
        with pytest.raises(PilotRunnerError, match="다음 KST 사업일"):
            runner.recover_prior_day_restart(case.case_id)
    finally:
        client.close()
    assert checkpoint.path.read_bytes() == before
    with sqlite3.connect(db) as conn:
        assert conn.execute(f"SELECT * FROM {PILOT_BINDING_TABLE}").fetchall() == (
            binding_before
        )
        assert conn.execute("SELECT * FROM budget_spend_inflight").fetchall() == (
            inflight_before
        )
    assert methods and set(methods) == {"GET"}


@pytest.mark.parametrize(
    "mutation",
    ["missing_ai_event", "wrong_reservation", "running_lifecycle", "cost_mismatch"],
)
def test_전날_P01_복구는_원장증거가_하나라도_다르면_fail_closed한다(
    tmp_path,
    mutation,
):
    runner, client, db, checkpoint, methods, case = _seed_prior_day_restart_unknown(
        tmp_path
    )
    with sqlite3.connect(db) as conn:
        if mutation == "missing_ai_event":
            conn.execute("DELETE FROM ai_variable_cost_events WHERE run_id=?", (RUN_ID,))
        elif mutation == "wrong_reservation":
            conn.execute(
                "UPDATE budget_spend_inflight SET reserved_krw=1 WHERE run_id=?",
                (RUN_ID,),
            )
        elif mutation == "running_lifecycle":
            conn.execute(
                "UPDATE observability_run_lifecycle "
                "SET state='running', final_record_json=NULL, expires_at=? "
                "WHERE run_id=?",
                (
                    (P01_PAID_AT + timedelta(hours=1)).isoformat(timespec="seconds"),
                    RUN_ID,
                ),
            )
        else:
            conn.execute(
                "UPDATE report_cost_summaries SET internal_ai_cost_krw=99 "
                "WHERE run_id=?",
                (RUN_ID,),
            )
    before = checkpoint.path.read_bytes()
    try:
        with pytest.raises(PilotRunnerError):
            runner.recover_prior_day_restart(case.case_id)
    finally:
        client.close()
    assert checkpoint.path.read_bytes() == before
    assert methods and set(methods) == {"GET"}


@pytest.mark.parametrize(
    ("state", "billing_uncertain"),
    [("running", False), ("completed", True)],
)
def test_전날_P01_복구는_다른_active나_uncertain_case가_있으면_거부한다(
    tmp_path,
    state,
    billing_uncertain,
):
    runner, client, _db, checkpoint, methods, case = _seed_prior_day_restart_unknown(
        tmp_path
    )
    snapshot = checkpoint._load()
    with checkpoint.exclusive():
        runner._update_case(
            snapshot,
            "P02",
            state=state,
            billing_uncertain=billing_uncertain,
        )
    before = checkpoint.path.read_bytes()
    methods.clear()
    try:
        with pytest.raises(PilotRunnerError, match="다른 미확정·실행 중 case"):
            runner.recover_prior_day_restart(case.case_id)
    finally:
        client.close()
    assert checkpoint.path.read_bytes() == before
    assert methods and set(methods) == {"GET"}


def test_전날_P01_복구는_checkpoint_seal이_다르면_재결속하지않는다(tmp_path):
    runner, client, db, checkpoint, methods, case = _seed_prior_day_restart_unknown(
        tmp_path
    )
    tampered = checkpoint._load()
    tampered["cases"]["P02"]["error_code"] = "manual-tamper"
    checkpoint._write(tampered)
    with sqlite3.connect(db) as conn:
        binding_before = conn.execute(
            f"SELECT * FROM {PILOT_BINDING_TABLE}"
        ).fetchall()
        inflight_before = conn.execute(
            "SELECT * FROM budget_spend_inflight"
        ).fetchall()
    try:
        with pytest.raises(CheckpointError, match="seal"):
            runner.recover_prior_day_restart(case.case_id)
    finally:
        client.close()
    with sqlite3.connect(db) as conn:
        assert conn.execute(f"SELECT * FROM {PILOT_BINDING_TABLE}").fetchall() == (
            binding_before
        )
        assert conn.execute("SELECT * FROM budget_spend_inflight").fetchall() == (
            inflight_before
        )
    assert methods and set(methods) == {"GET"}


def test_전날_P01_복구뒤_inflight증거가_사라지면_ordinary_execute도_차단한다(
    tmp_path,
):
    runner, client, db, checkpoint, methods, case = _seed_prior_day_restart_unknown(
        tmp_path
    )
    try:
        runner.recover_prior_day_restart(case.case_id)
        before = checkpoint.path.read_bytes()
        with sqlite3.connect(db) as conn:
            conn.execute(
                "DELETE FROM budget_spend_inflight WHERE run_id=?", (RUN_ID,)
            )

        methods.clear()
        with pytest.raises(PilotRunnerError, match="각각 한 행"):
            runner.operate(
                execute=True,
                case_ids=(CANONICAL_PILOT_CASES[1].case_id,),
                max_cases=0,
            )
    finally:
        client.close()

    assert checkpoint.path.read_bytes() == before
    assert methods and set(methods) == {"GET"}


def test_전날미확정_보존terminal은_P01이_아니면_execute면제가_아니다(tmp_path):
    runner, client, _db, checkpoint, methods, case = _seed_prior_day_restart_unknown(
        tmp_path
    )
    try:
        runner.recover_prior_day_restart(case.case_id)
        snapshot = checkpoint._load()
        p01 = snapshot["cases"]["P01"]
        p02 = snapshot["cases"]["P02"]
        p02.update(dict(p01))
        p02["case_id"] = "P02"

        methods.clear()
        with pytest.raises(PilotBatchBlocked, match="P02"):
            runner.execute_pending(
                snapshot,
                case_ids=("P02",),
                max_cases=0,
            )
    finally:
        client.close()

    assert methods == []


def test_CLI의_전날재시작복구는_execute와_같은명령에_쓸수없다():
    from tools.run_canonical_pilot25 import _parser

    parser = _parser()
    recovered = parser.parse_args(
        [
            "--origin",
            ORIGIN,
            "--storage-db",
            "storage.db",
            "--recover-prior-day-restart",
            "P01",
        ]
    )
    assert recovered.execute is False
    assert recovered.recover_prior_day_restart == "P01"
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "--origin",
                ORIGIN,
                "--storage-db",
                "storage.db",
                "--recover-prior-day-restart",
                "P01",
                "--execute",
            ]
        )
