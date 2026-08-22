"""Fail-closed browser-flow runner for the canonical G3.5 paid pilot.

This module talks only to the current loopback web workflow.  It never imports
or invokes the retired ``analysis_engine/tools/run_pilot.py`` entry point.
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import math
import re
import secrets
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, Final, Iterable, Mapping
from urllib.parse import urljoin, urlsplit

import httpx

from src.features.budget.constants import (
    PAID_PHASE_PROVIDER_BUDGET_KRW,
    SPEND_PHASE_PIPELINE,
)
from src.shared.company_identity import (
    exact_company_names_equivalent,
    verified_official_company_names_equivalent,
)
from src.features.observability.constants import END_STEP_CONFIRM, END_STEP_GENERATE
from src.features.pilot_evaluation.checkpoint import (
    PENDING_STATE,
    PRIOR_DAY_BILLING_UNCERTAIN_ERROR,
    PRIOR_DAY_BILLING_UNCERTAIN_STATE,
    RESUMABLE_STATE,
    SCHEMA_VERSION,
    TERMINAL_STATES,
    UNRESOLVED_STATES,
    CheckpointError,
    CheckpointStore,
)
from src.features.pilot_evaluation.final_gate_evidence import (
    FinalGateEvidenceError,
    read_bound_reason,
    validate_table_if_present as validate_final_gate_table_if_present,
)
from src.features.pilot_evaluation.manifest import (
    APPROVED_PAID_CASE_IDS,
    CANONICAL_PILOT_CASES,
    CanonicalPilotCase,
    manifest_sha256,
    validate_manifest,
)
from src.features.pilot_evaluation.schema import (
    CREATE_PILOT_BINDING_SQL as _CREATE_PILOT_BINDING_SQL,
    PILOT_BINDING_SCHEMA_VERSION,
    PILOT_BINDING_TABLE,
)
from src.features.pipeline.port import Outcome


MAX_RESPONSE_BYTES: Final[int] = 2 * 1024 * 1024
RATE_LIMIT_COUNT: Final[int] = 5
RATE_WINDOW_SEC: Final[int] = 10 * 60
LEGAL_NAME_RECOVERY_READY_STATE: Final[str] = "legal_name_recovery_ready"
LEGAL_NAME_MISMATCH_ERROR: Final[str] = "legal_name_mismatch"
LEGAL_NAME_RECOVERY_READY_ERROR: Final[str] = (
    "official_legal_name_equivalence_recovery_ready"
)
IDENTITY_REF_RECOVERY_READY_STATE: Final[str] = "identity_ref_retry_ready"
IDENTITY_REF_RECOVERY_READY_ERROR: Final[str] = (
    "observed_direct_dart_ref_retry_ready"
)
SERVICE_MAINTENANCE_RECOVERY_READY_STATE: Final[str] = (
    "service_maintenance_pre_provider_retry_ready"
)
SERVICE_MAINTENANCE_RECOVERY_READY_ERROR: Final[str] = (
    "service_maintenance_pre_provider_retry_ready"
)
SERVICE_MAINTENANCE_BLOCKED_STATE: Final[str] = (
    "identity_service_maintenance_blocked"
)
SERVICE_MAINTENANCE_BLOCKED_ERROR: Final[str] = (
    "identity_service_maintenance_pre_provider_429"
)
PILOT_BINDING_KEY: Final[str] = "g3.5-canonical-pilot25"
_RUN_LOCATION_RE: Final[re.Pattern[str]] = re.compile(
    r"^/progress/([0-9a-f]{32})$"
)
_RESULT_LOCATION_RE: Final[re.Pattern[str]] = re.compile(
    r"^/result/([0-9a-f]{32})$"
)
_HEX_32_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{32}$")
_SHA256_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{64}$")
_KNOWN_OUTCOMES: Final[frozenset[str]] = frozenset(item.value for item in Outcome)
_KST: Final[timezone] = timezone(timedelta(hours=9))


class PilotRunnerError(RuntimeError):
    """A sanitized failure that never contains a form token or response body."""


class PilotBatchBlocked(PilotRunnerError):
    """An unresolved or billing-uncertain case prevents another paid call."""


class _LedgerConsistencyError(PilotRunnerError):
    """Durable ledgers disagree, so no later case may be charged."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ParsedForm:
    action: str
    fields: Mapping[str, str]


@dataclass(frozen=True)
class ParsedPage:
    forms: tuple[ParsedForm, ...]
    legal_names: tuple[str, ...]
    confirmed_dart_refs: tuple[str, ...]
    has_paid_consent_checkbox: bool


@dataclass(frozen=True)
class WorkflowPage:
    csrf_token: str
    workflow_id: str
    server_instance_digest: str


@dataclass(frozen=True)
class LedgerResult:
    outcome: str
    cost_krw: float
    billing_uncertain: bool
    report_id: str
    corp_id: str
    automatic_release_sha256: str
    final_gate_reason: str


@dataclass(frozen=True)
class PilotRunSummary:
    executed_case_ids: tuple[str, ...]
    completed_case_ids: tuple[str, ...]
    terminal_case_ids: tuple[str, ...]
    next_recommended_at: str = ""
    reason: str = ""


class _PilotHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.forms: list[ParsedForm] = []
        self._form_action = ""
        self._form_fields: dict[str, str] | None = None
        self.legal_names: list[str] = []
        self.confirmed_dart_refs: list[str] = []
        self._legal_depth = 0
        self._legal_parts: list[str] = []
        self.has_paid_consent_checkbox = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        if self._legal_depth:
            self._legal_depth += 1
        elif tag == "div" and "legal" in values.get("class", "").split():
            self._legal_depth = 1
            self._legal_parts = []
        if tag == "div" and "company-card" in values.get("class", "").split():
            dart_ref = values.get("data-dart-corp-code", "")
            if dart_ref:
                self.confirmed_dart_refs.append(dart_ref)
        if tag == "form":
            if self._form_fields is not None:
                raise PilotRunnerError("중첩된 HTML form을 거부했습니다")
            self._form_action = values.get("action", "")
            self._form_fields = {}
        elif tag == "input":
            name = values.get("name", "")
            if (
                name == "evaluation_paid_consent"
                and values.get("type", "").lower() == "checkbox"
                and values.get("value", "") == "yes"
            ):
                self.has_paid_consent_checkbox = True
            if self._form_fields is not None and name:
                if name in self._form_fields:
                    raise PilotRunnerError("중복된 HTML form field를 거부했습니다")
                self._form_fields[name] = values.get("value", "")

    def handle_endtag(self, tag: str) -> None:
        if self._legal_depth:
            self._legal_depth -= 1
            if self._legal_depth == 0:
                name = " ".join("".join(self._legal_parts).split())
                if name:
                    self.legal_names.append(name)
                self._legal_parts = []
        if tag == "form" and self._form_fields is not None:
            self.forms.append(ParsedForm(self._form_action, dict(self._form_fields)))
            self._form_action = ""
            self._form_fields = None

    def handle_data(self, data: str) -> None:
        if self._legal_depth:
            self._legal_parts.append(data)


def parse_page(content: bytes) -> ParsedPage:
    if len(content) > MAX_RESPONSE_BYTES:
        raise PilotRunnerError("로컬 서버 HTML 응답이 허용 크기를 넘었습니다")
    try:
        text = content.decode("utf-8", errors="strict")
    except UnicodeDecodeError as exc:
        raise PilotRunnerError("로컬 서버 HTML 응답이 UTF-8이 아닙니다") from exc
    parser = _PilotHTMLParser()
    try:
        parser.feed(text)
        parser.close()
    except (AssertionError, ValueError) as exc:
        raise PilotRunnerError("로컬 서버 HTML 구조를 읽지 못했습니다") from exc
    return ParsedPage(
        forms=tuple(parser.forms),
        legal_names=tuple(parser.legal_names),
        confirmed_dart_refs=tuple(parser.confirmed_dart_refs),
        has_paid_consent_checkbox=parser.has_paid_consent_checkbox,
    )


def canonical_loopback_origin(value: str) -> str:
    try:
        parsed = urlsplit(value.strip())
        host = parsed.hostname or ""
        address = ipaddress.ip_address(host)
        port = parsed.port
    except (TypeError, ValueError) as exc:
        raise PilotRunnerError("origin은 숫자형 loopback HTTP 주소여야 합니다") from exc
    if (
        parsed.scheme != "http"
        or not address.is_loopback
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or port is None
    ):
        raise PilotRunnerError(
            "origin은 포트를 포함한 명시적 loopback HTTP origin이어야 합니다"
        )
    formatted_host = f"[{address.compressed}]" if address.version == 6 else address.compressed
    return f"http://{formatted_host}:{port}"


def _parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _is_exact_zero_cost(value: object) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return False
    return math.isfinite(number) and number == 0.0


class CanonicalPilotRunner:
    def __init__(
        self,
        *,
        origin: str,
        storage_db_path: Path,
        checkpoint: CheckpointStore,
        client: httpx.Client,
        cases: tuple[CanonicalPilotCase, ...] = CANONICAL_PILOT_CASES,
        now: Callable[[], datetime] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        poll_interval_sec: float = 2.0,
        poll_timeout_sec: float = 35 * 60,
        ledger_settle_timeout_sec: float = 10.0,
        approved_paid_case_ids: frozenset[str] = APPROVED_PAID_CASE_IDS,
    ) -> None:
        self.origin = canonical_loopback_origin(origin)
        self.storage_db_path = storage_db_path.resolve()
        self.checkpoint = checkpoint
        if self.checkpoint.path.parent != self.storage_db_path.parent:
            raise PilotRunnerError(
                "체크포인트는 지정한 실시간 평가 SQLite와 같은 격리 폴더에 있어야 합니다"
            )
        self.client = client
        self.cases = cases
        self.now = now or (lambda: datetime.now(timezone.utc))
        self.sleep = sleep
        self.poll_interval_sec = max(0.01, float(poll_interval_sec))
        self.poll_timeout_sec = max(self.poll_interval_sec, float(poll_timeout_sec))
        self.ledger_settle_timeout_sec = max(0.0, float(ledger_settle_timeout_sec))
        self.approved_paid_case_ids = frozenset(approved_paid_case_ids)
        self._binding_id = ""
        self._sealed_checkpoint_sha256 = ""

    def operate(
        self,
        *,
        execute: bool,
        case_ids: Iterable[str] = (),
        max_cases: int | None = None,
    ) -> PilotRunSummary:
        selected_case_ids = tuple(case_ids)
        if execute:
            self._validate_paid_selection(selected_case_ids)
        with self.checkpoint.exclusive():
            snapshot = self.preflight()
            if not execute:
                return self._summary(snapshot, (), reason="dry_run")
            return self.execute_pending(
                snapshot, case_ids=selected_case_ids, max_cases=max_cases
            )

    def recover_legal_name_mismatch(self, case_id: str) -> PilotRunSummary:
        """식별번호에 결속된 공식명 오탐 한 건을 재시도 준비 상태로 바꾼다.

        이 복구는 의도적으로 ``--execute``와 분리된다. 일반 사전 점검 GET만
        수행하고 봉인된 체크포인트와 SQLite 증거를 확인한 뒤 한 건만 제한된
        복구 준비 상태로 바꾼다. 실제 POST에는 별도의 명시적 실행이 필요하다.
        """

        with self.checkpoint.exclusive():
            snapshot = self.preflight()
            selected = self._select_cases((case_id,))
            if len(selected) != 1:
                raise PilotRunnerError("복구할 case ID 하나가 필요합니다")
            case = selected[0]
            row = self._case_rows(snapshot)[case.case_id]
            self._validate_legal_name_recovery_checkpoint(row, case)
            self._validate_legal_name_recovery_storage(row)
            self._update_case(
                snapshot,
                case.case_id,
                state=LEGAL_NAME_RECOVERY_READY_STATE,
                billing_uncertain=False,
                error_code=LEGAL_NAME_RECOVERY_READY_ERROR,
            )
            return self._summary(
                snapshot,
                (),
                reason=LEGAL_NAME_RECOVERY_READY_STATE,
            )

    def recover_prior_day_restart(self, case_id: str) -> PilotRunSummary:
        """Rebind one restarted local evaluator without erasing unknown billing.

        This recovery is intentionally narrower than ordinary preflight.  It is
        available only for the proven P01 shape after the KST ledger day has
        rolled over.  The old inflight row remains untouched as forensic
        evidence; only the sealed checkpoint and its DB binding move to the new
        GET-observed process digest.  A separate later ``--execute`` invocation
        is still required before any paid POST can occur.
        """

        with self.checkpoint.exclusive():
            if case_id != "P01":
                raise PilotRunnerError("재시작 복구는 현재 P01 한 건에만 허용됩니다")
            validate_manifest(self.cases)
            self._validate_storage()
            health = self._get_json("/healthz", expected_status=200)
            if health.get("status") != "ok":
                raise PilotRunnerError("healthz가 ok가 아닙니다")
            ready = self._get_json("/readyz", expected_status=200)
            if ready.get("status") != "ready":
                raise PilotRunnerError("readyz가 유료 실행 준비 상태가 아닙니다")
            workflow = self._workflow_page()
            snapshot = self.checkpoint._load()
            case = self._select_cases((case_id,))[0]
            old_server_digest, old_checkpoint_digest = (
                self._validate_restart_recovery_binding(
                    snapshot, current_server_digest=workflow.server_instance_digest
                )
            )
            self._validate_prior_day_restart_case(snapshot, case)

            timestamp = self._now_iso()
            row = self._case_rows(snapshot)[case.case_id]
            row["state"] = PRIOR_DAY_BILLING_UNCERTAIN_STATE
            row["billing_uncertain"] = True
            row["error_code"] = PRIOR_DAY_BILLING_UNCERTAIN_ERROR
            row["updated_at"] = timestamp
            snapshot["server_instance_sha256"] = workflow.server_instance_digest
            snapshot["updated_at"] = timestamp

            # Write the file first.  A crash before the DB CAS leaves a hash
            # mismatch and therefore fails closed before any later POST.
            self.checkpoint._write(snapshot)
            new_checkpoint_digest = self._checkpoint_sha256()
            self._cas_restart_recovery_binding(
                old_server_digest=old_server_digest,
                new_server_digest=workflow.server_instance_digest,
                old_checkpoint_digest=old_checkpoint_digest,
                new_checkpoint_digest=new_checkpoint_digest,
            )
            self.checkpoint._append_event(
                "case_state",
                case.case_id,
                PRIOR_DAY_BILLING_UNCERTAIN_STATE,
                timestamp,
                run_id=str(row.get("run_id", "")),
                outcome=str(row.get("outcome", "")),
                cost_krw=row.get("internal_ai_cost_krw"),
                billing_uncertain=True,
                error_code=PRIOR_DAY_BILLING_UNCERTAIN_ERROR,
                server_instance_rebound=True,
            )
            return self._summary(
                snapshot,
                (),
                reason=PRIOR_DAY_BILLING_UNCERTAIN_STATE,
            )

    def recover_identity_ref_unverified(self, case_id: str) -> PilotRunSummary:
        """Rebind and prepare one proven zero-cost identity observation retry.

        This is deliberately a separate, explicit operation.  It accepts only
        the terminal shape produced before any run ID, report, or AI ledger
        event exists; the old checkpoint journal entry remains the evidence of
        the failed observation.  A later ``--execute --case-id`` is still
        needed before the new DART confirmation POST.
        """

        with self.checkpoint.exclusive():
            if case_id != "P02":
                raise PilotRunnerError("DART 번호 미관측 복구는 현재 P02 한 건에만 허용됩니다")
            validate_manifest(self.cases)
            self._validate_storage()
            health = self._get_json("/healthz", expected_status=200)
            if health.get("status") != "ok":
                raise PilotRunnerError("healthz가 ok가 아닙니다")
            ready = self._get_json("/readyz", expected_status=200)
            if ready.get("status") != "ready":
                raise PilotRunnerError("readyz가 유료 실행 준비 상태가 아닙니다")
            workflow = self._workflow_page()
            snapshot = self.checkpoint._load()
            case = self._select_cases((case_id,))[0]
            old_server_digest, old_checkpoint_digest = (
                self._validate_restart_recovery_binding(
                    snapshot, current_server_digest=workflow.server_instance_digest
                )
            )
            row = self._case_rows(snapshot)[case.case_id]
            if (
                str(row.get("state", "")) != "identity_ref_unverified"
                or str(row.get("error_code", "")) != "candidate_ref_not_observed"
                or bool(row.get("billing_uncertain"))
                or any(str(row.get(field, "")) for field in ("run_id", "report_id", "selected_corp_code", "legal_name"))
                or str(row.get("outcome", "")) != "IDENTITY_REF_UNVERIFIED"
                or row.get("internal_ai_cost_krw") is not None
                or row.get("result_http_status") is not None
                or _parse_timestamp(row.get("paid_boundary_at")) is None
            ):
                raise PilotRunnerError("P02의 0원 DART 번호 미관측 종료 모양이 다릅니다")

            timestamp = self._now_iso()
            row["state"] = IDENTITY_REF_RECOVERY_READY_STATE
            row["error_code"] = IDENTITY_REF_RECOVERY_READY_ERROR
            row["updated_at"] = timestamp
            snapshot["server_instance_sha256"] = workflow.server_instance_digest
            snapshot["updated_at"] = timestamp
            self.checkpoint._write(snapshot)
            new_checkpoint_digest = self._checkpoint_sha256()
            self._cas_restart_recovery_binding(
                old_server_digest=old_server_digest,
                new_server_digest=workflow.server_instance_digest,
                old_checkpoint_digest=old_checkpoint_digest,
                new_checkpoint_digest=new_checkpoint_digest,
            )
            self.checkpoint._append_event(
                "case_state",
                case.case_id,
                IDENTITY_REF_RECOVERY_READY_STATE,
                timestamp,
                run_id="",
                outcome=str(row.get("outcome", "")),
                cost_krw=None,
                billing_uncertain=False,
                error_code=IDENTITY_REF_RECOVERY_READY_ERROR,
                server_instance_rebound=True,
            )
            return self._summary(
                snapshot, (), reason=IDENTITY_REF_RECOVERY_READY_STATE
            )

    def recover_service_maintenance_pre_provider(self, case_id: str) -> PilotRunSummary:
        """Rebind one P02 response durably marked as a maintenance 429.

        It never clears the service state. An authenticated administrator must
        record the correction and restart through the normal route first.
        """

        with self.checkpoint.exclusive():
            if case_id != "P02":
                raise PilotRunnerError("점검 전단계 복구는 현재 P02 한 건에만 허용됩니다")
            validate_manifest(self.cases)
            self._validate_storage()
            health = self._get_json("/healthz", expected_status=200)
            if health.get("status") != "ok":
                raise PilotRunnerError("healthz가 ok가 아닙니다")
            ready = self._get_json("/readyz", expected_status=200)
            if ready.get("status") != "ready":
                raise PilotRunnerError("readyz가 유료 실행 준비 상태가 아닙니다")
            workflow = self._workflow_page()
            snapshot = self.checkpoint._load()
            case = self._select_cases((case_id,))[0]
            old_server_digest, old_checkpoint_digest = (
                self._validate_restart_recovery_binding(
                    snapshot, current_server_digest=workflow.server_instance_digest
                )
            )
            row = self._case_rows(snapshot)[case.case_id]
            paid_at = _parse_timestamp(row.get("paid_boundary_at"))
            if (
                str(row.get("case_id", "")) != case.case_id
                or str(row.get("state", "")) != SERVICE_MAINTENANCE_BLOCKED_STATE
                or str(row.get("error_code", "")) != SERVICE_MAINTENANCE_BLOCKED_ERROR
                or bool(row.get("billing_uncertain"))
                or any(
                    str(row.get(field, ""))
                    for field in ("run_id", "report_id", "selected_corp_code", "legal_name", "outcome")
                )
                or row.get("internal_ai_cost_krw") is not None
                or row.get("result_http_status") != 429
                or paid_at is None
            ):
                raise PilotRunnerError("P02의 점검 전단계 429 종료 모양이 다릅니다")
            self._validate_service_restart_after_block(paid_at)

            timestamp = self._now_iso()
            row["state"] = SERVICE_MAINTENANCE_RECOVERY_READY_STATE
            row["error_code"] = SERVICE_MAINTENANCE_RECOVERY_READY_ERROR
            row["billing_uncertain"] = False
            row["service_maintenance_429_proven"] = True
            row["updated_at"] = timestamp
            snapshot["server_instance_sha256"] = workflow.server_instance_digest
            snapshot["updated_at"] = timestamp
            self.checkpoint._write(snapshot)
            new_checkpoint_digest = self._checkpoint_sha256()
            self._cas_restart_recovery_binding(
                old_server_digest=old_server_digest,
                new_server_digest=workflow.server_instance_digest,
                old_checkpoint_digest=old_checkpoint_digest,
                new_checkpoint_digest=new_checkpoint_digest,
            )
            self.checkpoint._append_event(
                "case_state",
                case.case_id,
                SERVICE_MAINTENANCE_RECOVERY_READY_STATE,
                timestamp,
                run_id="",
                outcome="",
                cost_krw=None,
                billing_uncertain=False,
                error_code=SERVICE_MAINTENANCE_RECOVERY_READY_ERROR,
                server_instance_rebound=True,
            )
            return self._summary(
                snapshot, (), reason=SERVICE_MAINTENANCE_RECOVERY_READY_STATE
            )

    def retire_unproven_p02_retry_and_rebind(self, case_id: str) -> PilotRunSummary:
        """Retire an old P02 retry that lacks a durable maintenance-429 proof.

        The original zero-cost identity observation remains in the append-only
        journal. This operation never retries P02; it restores that terminal
        result and moves only the sealed checkpoint binding to a new server.
        """

        with self.checkpoint.exclusive():
            if case_id != "P02":
                raise PilotRunnerError("증거 없는 재시도 은퇴는 현재 P02 한 건에만 허용됩니다")
            validate_manifest(self.cases)
            self._validate_storage()
            health = self._get_json("/healthz", expected_status=200)
            ready = self._get_json("/readyz", expected_status=200)
            if health.get("status") != "ok" or ready.get("status") != "ready":
                raise PilotRunnerError("재결속 서버가 유료 실행 준비 상태가 아닙니다")
            workflow = self._workflow_page()
            snapshot = self.checkpoint._load()
            case = self._select_cases((case_id,))[0]
            old_server_digest, old_checkpoint_digest = self._validate_restart_recovery_binding(
                snapshot, current_server_digest=workflow.server_instance_digest
            )
            row = self._case_rows(snapshot)[case.case_id]
            paid_at = _parse_timestamp(row.get("paid_boundary_at"))
            if (
                str(row.get("case_id", "")) != case.case_id
                or str(row.get("state", "")) != SERVICE_MAINTENANCE_RECOVERY_READY_STATE
                or str(row.get("error_code", "")) != SERVICE_MAINTENANCE_RECOVERY_READY_ERROR
                or bool(row.get("billing_uncertain"))
                or row.get("service_maintenance_429_proven") is True
                or any(str(row.get(field, "")) for field in ("run_id", "report_id", "selected_corp_code", "legal_name", "outcome"))
                or row.get("internal_ai_cost_krw") is not None
                or row.get("result_http_status") is not None
                or paid_at is None
            ):
                raise PilotRunnerError("P02의 증거 없는 재시도 준비 모양이 다릅니다")
            self._validate_service_restart_after_block(paid_at)
            try:
                events = [json.loads(line) for line in self.checkpoint.events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                raise CheckpointError("P02 은퇴용 journal을 안전하게 읽지 못했습니다") from exc
            original = [
                event for event in events
                if event.get("case_id") == case.case_id
                and event.get("state") == "identity_ref_unverified"
                and event.get("error_code") == "candidate_ref_not_observed"
                and event.get("outcome") == "IDENTITY_REF_UNVERIFIED"
                and not event.get("run_id")
                and event.get("cost_krw") is None
                and event.get("billing_uncertain") is False
            ]
            if len(original) != 1:
                raise PilotRunnerError("P02 원래 DART 번호 미관측 journal 증거가 다릅니다")

            timestamp = self._now_iso()
            row.update(
                state="identity_ref_unverified",
                error_code="candidate_ref_not_observed",
                outcome="IDENTITY_REF_UNVERIFIED",
                billing_uncertain=False,
                run_id="",
                report_id="",
                selected_corp_code="",
                legal_name="",
                internal_ai_cost_krw=None,
                result_http_status=None,
                updated_at=timestamp,
            )
            snapshot["server_instance_sha256"] = workflow.server_instance_digest
            snapshot["updated_at"] = timestamp
            self.checkpoint._write(snapshot)
            new_checkpoint_digest = self._checkpoint_sha256()
            self._cas_restart_recovery_binding(
                old_server_digest=old_server_digest,
                new_server_digest=workflow.server_instance_digest,
                old_checkpoint_digest=old_checkpoint_digest,
                new_checkpoint_digest=new_checkpoint_digest,
            )
            self.checkpoint._append_event(
                "case_retired", case.case_id, "identity_ref_unverified", timestamp,
                run_id="", outcome="IDENTITY_REF_UNVERIFIED", cost_krw=None,
                billing_uncertain=False, error_code="candidate_ref_not_observed",
                unproven_retry_retired=True, server_instance_rebound=True,
            )
            return self._summary(snapshot, (), reason="unproven_p02_retry_retired")

    def preflight(self) -> dict[str, object]:
        validate_manifest(self.cases)
        self._validate_storage()
        health = self._get_json("/healthz", expected_status=200)
        if health.get("status") != "ok":
            raise PilotRunnerError("healthz가 ok가 아닙니다")
        ready = self._get_json("/readyz", expected_status=200)
        if ready.get("status") != "ready":
            raise PilotRunnerError("readyz가 유료 실행 준비 상태가 아닙니다")
        workflow = self._workflow_page()
        data_path_digest = hashlib.sha256(
            str(self.storage_db_path).casefold().encode("utf-8")
        ).hexdigest()
        checkpoint_path_digest = hashlib.sha256(
            str(self.checkpoint.path).casefold().encode("utf-8")
        ).hexdigest()
        binding_id = self._bind_checkpoint_to_storage(
            manifest_digest=manifest_sha256(self.cases),
            server_instance_digest=workflow.server_instance_digest,
            data_path_digest=data_path_digest,
            checkpoint_path_digest=checkpoint_path_digest,
        )
        snapshot = self.checkpoint.load_or_create(
            cases=self.cases,
            binding_id=binding_id,
            manifest_digest=manifest_sha256(self.cases),
            origin=self.origin,
            server_instance_digest=workflow.server_instance_digest,
            data_path_digest=data_path_digest,
            now=self._now_iso(),
        )
        self._seal_checkpoint()
        self._verify_known_runs_use_storage(snapshot)
        return snapshot

    def execute_pending(
        self,
        snapshot: dict[str, object],
        *,
        case_ids: tuple[str, ...] = (),
        max_cases: int | None = None,
    ) -> PilotRunSummary:
        self._validate_paid_selection(case_ids)
        selected = self._select_cases(case_ids)
        rows = self._case_rows(snapshot)
        preserved = {
            case_id: self._is_preserved_prior_day_uncertain(case_id, row)
            for case_id, row in rows.items()
        }
        unsafe = [
            case_id
            for case_id, row in rows.items()
            if (
                str(row.get("state", ""))
                == PRIOR_DAY_BILLING_UNCERTAIN_STATE
                and not preserved[case_id]
            )
            or (
                not preserved[case_id]
                and (
                    bool(row.get("billing_uncertain"))
                    or str(row.get("state", "")) in UNRESOLVED_STATES
                )
            )
        ]
        if unsafe:
            raise PilotBatchBlocked(
                "재호출할 수 없는 미확정 case가 있어 새 유료 호출을 차단했습니다: "
                + ", ".join(unsafe)
            )
        for case_id, row in rows.items():
            if not preserved[case_id]:
                continue
            checkpoint_cost = float(row["internal_ai_cost_krw"])
            paid_at = _parse_timestamp(row.get("paid_boundary_at"))
            if paid_at is None:  # Kept explicit even though the exact-shape guard checked it.
                raise PilotBatchBlocked("보존된 P01 유료 경계 시각이 올바르지 않습니다")
            now = self.now()
            if now.tzinfo is None:
                now = now.replace(tzinfo=timezone.utc)
            self._validate_prior_day_restart_storage(
                row,
                checkpoint_cost=checkpoint_cost,
                paid_at=paid_at,
                paid_day=paid_at.astimezone(_KST).date().isoformat(),
                current_day=now.astimezone(_KST).date().isoformat(),
            )
        all_running = [
            case
            for case in self.cases
            if str(rows[case.case_id].get("state")) == RESUMABLE_STATE
        ]
        if len(all_running) > 1:
            raise PilotBatchBlocked("동시에 둘 이상의 running case가 있어 재개를 차단했습니다")
        selected_ids = {case.case_id for case in selected}
        if all_running and all_running[0].case_id not in selected_ids:
            raise PilotBatchBlocked(
                "선택 밖에 아직 running인 case가 있어 새 실행을 차단했습니다: "
                + all_running[0].case_id
            )
        running = all_running

        executed: list[str] = []
        newly_started = 0
        if running:
            self._resume_case(snapshot, running[0])
            executed.append(running[0].case_id)
            rows = self._case_rows(snapshot)

        pending = [
            case
            for case in selected
            if str(rows[case.case_id].get("state"))
            in {
                PENDING_STATE,
                LEGAL_NAME_RECOVERY_READY_STATE,
                IDENTITY_REF_RECOVERY_READY_STATE,
                SERVICE_MAINTENANCE_RECOVERY_READY_STATE,
            }
        ]
        for case in pending:
            if max_cases is not None and newly_started >= max(0, max_cases):
                return self._summary(snapshot, executed, reason="max_cases")
            wait_until = self._rate_recommendation(snapshot)
            if wait_until is not None:
                return self._summary(
                    snapshot,
                    executed,
                    next_recommended_at=wait_until.isoformat(timespec="seconds"),
                    reason="rate_window",
                )
            if (
                str(rows[case.case_id].get("state", ""))
                == SERVICE_MAINTENANCE_RECOVERY_READY_STATE
            ):
                if rows[case.case_id].get("service_maintenance_429_proven") is not True:
                    raise PilotBatchBlocked("P02 점검 복구의 서버 429 증거 표식이 없습니다")
                previous_paid_at = _parse_timestamp(
                    rows[case.case_id].get("paid_boundary_at")
                )
                if previous_paid_at is None:
                    raise PilotBatchBlocked("P02 점검 복구의 이전 유료 경계 시각이 없습니다")
                self._validate_service_restart_after_block(previous_paid_at)
            self._start_case(snapshot, case)
            executed.append(case.case_id)
            newly_started += 1
        return self._summary(snapshot, executed, reason="selection_complete")

    def _validate_paid_selection(self, case_ids: tuple[str, ...]) -> None:
        """Reject implicit or unapproved paid work at every public boundary."""

        if not case_ids:
            raise PilotRunnerError(
                "유료 실행에는 승인된 case ID를 하나 이상 명시해야 합니다"
            )
        outside = sorted(set(case_ids) - self.approved_paid_case_ids)
        if outside:
            raise PilotRunnerError(
                "현재 유료 승인은 P01~P10뿐입니다. 승인 밖 case: "
                + ", ".join(outside)
            )

    def _is_preserved_prior_day_uncertain(
        self,
        case_id: str,
        row: Mapping[str, object],
    ) -> bool:
        """Only the explicit sealed terminal may remain honestly uncertain."""

        case = next((item for item in self.cases if item.case_id == case_id), None)
        try:
            cost = float(row.get("internal_ai_cost_krw"))
        except (TypeError, ValueError, OverflowError):
            return False
        return (
            case_id == "P01"
            and case is not None
            and str(row.get("case_id", "")) == case_id
            and str(row.get("state", "")) == PRIOR_DAY_BILLING_UNCERTAIN_STATE
            and row.get("billing_uncertain") is True
            and str(row.get("error_code", ""))
            == PRIOR_DAY_BILLING_UNCERTAIN_ERROR
            and _HEX_32_RE.fullmatch(str(row.get("run_id", ""))) is not None
            and str(row.get("outcome", "")) == Outcome.FAILED.value
            and not str(row.get("report_id", ""))
            and type(row.get("result_http_status")) is int
            and row.get("result_http_status") == 200
            and str(row.get("selected_corp_code", "")) == case.corp_code
            and exact_company_names_equivalent(
                str(row.get("legal_name", "")), case.expected_legal_name
            )
            and math.isfinite(cost)
            and cost > 0
            and _parse_timestamp(row.get("paid_boundary_at")) is not None
        )

    def _start_case(
        self, snapshot: dict[str, object], case: CanonicalPilotCase
    ) -> None:
        workflow = self._workflow_page()
        expected_digest = str(snapshot.get("server_instance_sha256", ""))
        if workflow.server_instance_digest != expected_digest:
            raise CheckpointError("case 시작 직전 서버 instance가 바뀌어 호출을 차단했습니다")

        paid_at = self._now_iso()
        lifecycle_before = self._lifecycle_ids()
        self._update_case(
            snapshot,
            case.case_id,
            state="identity_started",
            now=paid_at,
            run_id="",
            report_id="",
            outcome="",
            internal_ai_cost_krw=None,
            billing_uncertain=True,
            selected_corp_code="",
            legal_name="",
            paid_boundary_at=paid_at,
            result_http_status=None,
            error_code="identity_response_pending",
            final_gate_reason="",
        )
        initial_data = {
            "csrf_token": workflow.csrf_token,
            "evaluation_workflow_id": workflow.workflow_id,
            "evaluation_paid_consent": "yes",
            "company": case.input_name,
            "region": case.address_hint,
        }
        response = self._post_paid_boundary("/confirm", initial_data, case.case_id)
        if (
            response.status_code == 429
            and response.headers.get("X-Company-Analysis-Block", "")
            == "service-maintenance"
        ):
            self._update_case(
                snapshot,
                case.case_id,
                state=SERVICE_MAINTENANCE_BLOCKED_STATE,
                billing_uncertain=False,
                result_http_status=429,
                error_code=SERVICE_MAINTENANCE_BLOCKED_ERROR,
            )
            return
        page = self._expect_html(response, 200, "confirm_initial")
        candidate_forms = tuple(
            form
            for form in page.forms
            if form.action == "/confirm" and form.fields.get("candidate_ref", "")
        )
        selected_ref = ""
        if candidate_forms:
            matching = [
                form
                for form in candidate_forms
                if form.fields.get("candidate_ref") == case.corp_code
                and form.fields.get("candidate_provider") == "DART"
            ]
            if len(matching) != 1:
                self._update_case(
                    snapshot,
                    case.case_id,
                    state="identity_mismatch",
                    billing_uncertain=False,
                    error_code="expected_corp_code_not_unique",
                )
                return
            selected = matching[0]
            self._validate_candidate_form(selected, case)
            selected_ref = case.corp_code
            response = self._post_paid_boundary(
                "/confirm", dict(selected.fields), case.case_id
            )
            page = self._expect_html(response, 200, "confirm_candidate")
            if page.confirmed_dart_refs != (selected_ref,):
                run_id = self._single_new_lifecycle_id(
                    lifecycle_before, required=True
                )
                cleanup_ok = self._reject_if_possible(page)
                cost = self._known_spend_cost(run_id)
                self._update_case(
                    snapshot,
                    case.case_id,
                    state=("identity_mismatch" if cleanup_ok else "identified"),
                    run_id=run_id,
                    selected_corp_code=selected_ref,
                    internal_ai_cost_krw=cost,
                    billing_uncertain=not cleanup_ok,
                    error_code="confirmed_corp_code_not_observed",
                )
                if not cleanup_ok:
                    raise PilotBatchBlocked(
                        "최종 확인 카드의 DART 번호와 token 정리를 확정하지 못했습니다"
                    )
                return
        elif page.confirmed_dart_refs:
            # 로컬 후보 화면이 일시적으로 비어도, 서버가 실제 확인 카드에 DART
            # 고유번호를 명시했다면 이름 추측 없이 그 번호만 manifest와 대조한다.
            # 카드가 하나가 아니거나 예상 번호와 다르면 절대 /run으로 넘어가지 않는다.
            if page.confirmed_dart_refs != (case.corp_code,):
                self._update_case(
                    snapshot,
                    case.case_id,
                    state="identity_mismatch",
                    billing_uncertain=False,
                    error_code="expected_direct_corp_code_not_unique",
                )
                return
            selected_ref = case.corp_code

        run_forms = tuple(form for form in page.forms if form.action == "/run")
        if not selected_ref:
            run_id = self._single_new_lifecycle_id(lifecycle_before, required=False)
            self._reject_if_possible(page)
            cost = self._known_spend_cost(run_id) if run_id else None
            self._update_case(
                snapshot,
                case.case_id,
                state="identity_ref_unverified",
                run_id=run_id,
                outcome="IDENTITY_REF_UNVERIFIED",
                internal_ai_cost_krw=cost,
                billing_uncertain=False,
                error_code="candidate_ref_not_observed",
            )
            return
        if len(run_forms) != 1 or len(page.legal_names) != 1:
            self._update_case(
                snapshot,
                case.case_id,
                state="identity_started",
                billing_uncertain=True,
                error_code="confirm_contract_missing",
            )
            raise PilotBatchBlocked("회사 확인 응답 계약을 검증하지 못해 재호출을 차단했습니다")

        legal_name = page.legal_names[0]
        run_id = self._single_new_lifecycle_id(lifecycle_before, required=True)
        if not verified_official_company_names_equivalent(
            legal_name,
            case.expected_legal_name,
            observed_corp_code=selected_ref,
            expected_corp_code=case.corp_code,
        ):
            cleanup_ok = self._reject_if_possible(page)
            cost = self._known_spend_cost(run_id)
            state = "identity_mismatch" if cleanup_ok else "identified"
            self._update_case(
                snapshot,
                case.case_id,
                state=state,
                run_id=run_id,
                legal_name=legal_name,
                selected_corp_code=selected_ref,
                internal_ai_cost_krw=cost,
                billing_uncertain=not cleanup_ok,
                error_code=LEGAL_NAME_MISMATCH_ERROR,
            )
            if not cleanup_ok:
                raise PilotBatchBlocked("회사 확인 token 정리 여부가 불명확해 재호출을 차단했습니다")
            return

        run_form = run_forms[0]
        self._validate_run_form(run_form, case)
        self._update_case(
            snapshot,
            case.case_id,
            state="identified",
            run_id=run_id,
            legal_name=legal_name,
            selected_corp_code=selected_ref,
            billing_uncertain=False,
            error_code="",
        )
        self._update_case(
            snapshot,
            case.case_id,
            state="run_submission_started",
            billing_uncertain=True,
            error_code="run_response_pending",
        )
        response = self._post_paid_boundary("/run", dict(run_form.fields), case.case_id)
        accepted_id = self._accepted_run_id(response)
        if accepted_id != run_id:
            self._update_case(
                snapshot,
                case.case_id,
                state="run_submission_started",
                billing_uncertain=True,
                error_code="run_id_mismatch",
            )
            raise PilotBatchBlocked("서버와 데이터 원장의 run ID가 달라 재개를 차단했습니다")
        self._update_case(
            snapshot,
            case.case_id,
            state=RESUMABLE_STATE,
            run_id=run_id,
            billing_uncertain=False,
            error_code="",
        )
        self._poll_and_finalize(snapshot, case, run_id)

    def _resume_case(
        self, snapshot: dict[str, object], case: CanonicalPilotCase
    ) -> None:
        row = self._case_rows(snapshot)[case.case_id]
        run_id = str(row.get("run_id", ""))
        if not _HEX_32_RE.fullmatch(run_id):
            raise PilotBatchBlocked("running case의 run ID가 올바르지 않습니다")
        self._poll_and_finalize(snapshot, case, run_id)

    def _validate_legal_name_recovery_checkpoint(
        self,
        row: Mapping[str, object],
        case: CanonicalPilotCase,
    ) -> None:
        """Require the exact legacy false-positive shape before any mutation."""

        if str(row.get("state", "")) != "identity_mismatch":
            raise PilotRunnerError(
                "법인명 복구는 identity_mismatch case 하나에만 허용됩니다"
            )
        if str(row.get("error_code", "")) != LEGAL_NAME_MISMATCH_ERROR:
            raise PilotRunnerError(
                "법인명 복구 대상의 오류 코드가 legal_name_mismatch가 아닙니다"
            )
        if str(row.get("selected_corp_code", "")) != case.corp_code:
            raise PilotRunnerError(
                "법인명 복구 대상의 DART 고유번호가 manifest와 다릅니다"
            )
        legal_name = str(row.get("legal_name", ""))
        if (
            not legal_name
            or legal_name == case.expected_legal_name
            or not verified_official_company_names_equivalent(
                legal_name,
                case.expected_legal_name,
                observed_corp_code=str(row.get("selected_corp_code", "")),
                expected_corp_code=case.corp_code,
            )
        ):
            raise PilotRunnerError(
                "법인명 차이가 식별번호로 결속된 공식 표기 등가가 아닙니다"
            )
        run_id = str(row.get("run_id", ""))
        if not _HEX_32_RE.fullmatch(run_id):
            raise PilotRunnerError("법인명 복구 대상의 확인 run ID가 올바르지 않습니다")
        if (
            str(row.get("report_id", ""))
            or str(row.get("outcome", ""))
            or row.get("result_http_status") is not None
        ):
            raise PilotRunnerError(
                "이미 run 결과 또는 보고서 흔적이 있는 case는 복구할 수 없습니다"
            )
        if row.get("billing_uncertain") is not False:
            raise PilotRunnerError("비용 미확정 case는 법인명 복구할 수 없습니다")
        if not _is_exact_zero_cost(row.get("internal_ai_cost_krw")):
            raise PilotRunnerError("내부 AI 원가가 정확히 0원이 아니어서 복구를 차단했습니다")

        paid_at = _parse_timestamp(row.get("paid_boundary_at"))
        if paid_at is None:
            raise PilotRunnerError("법인명 복구 대상의 유료 경계 시각이 올바르지 않습니다")
        now = self.now()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        now = now.astimezone(timezone.utc)
        retry_not_before = paid_at + timedelta(seconds=RATE_WINDOW_SEC)
        if now < retry_not_before:
            raise PilotRunnerError(
                "이전 시도를 5건/10분 창에서 잃지 않도록 다음 시각 이후에 복구하세요: "
                + retry_not_before.isoformat(timespec="seconds")
            )

    def _validate_legal_name_recovery_storage(
        self, row: Mapping[str, object]
    ) -> None:
        """Prove in SQLite that confirmation ended before a provider run."""

        run_id = str(row.get("run_id", ""))
        try:
            with self._connect_storage() as conn:
                tables = {
                    str(item[0])
                    for item in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
                if "observability_run_lifecycle_audit" not in tables:
                    raise PilotRunnerError(
                        "lifecycle 감사 표가 없어 no-run 증거를 확인할 수 없습니다"
                    )
                lifecycle_row = conn.execute(
                    "SELECT state, confirmed_cost_krw, final_record_json "
                    "FROM observability_run_lifecycle WHERE run_id=?",
                    (run_id,),
                ).fetchone()
                audit_rows = conn.execute(
                    "SELECT from_state, to_state "
                    "FROM observability_run_lifecycle_audit WHERE run_id=? "
                    "ORDER BY event_id",
                    (run_id,),
                ).fetchall()
                spend_count = int(
                    conn.execute(
                        "SELECT COUNT(*) FROM budget_spend_events WHERE run_id=?",
                        (run_id,),
                    ).fetchone()[0]
                )
                inflight_count = int(
                    conn.execute(
                        "SELECT COUNT(*) FROM budget_spend_inflight WHERE run_id=?",
                        (run_id,),
                    ).fetchone()[0]
                )
                report_count = int(
                    conn.execute(
                        "SELECT COUNT(*) FROM reports WHERE report_id=?",
                        (run_id,),
                    ).fetchone()[0]
                )
                cost_summary_count = (
                    int(
                        conn.execute(
                            "SELECT COUNT(*) FROM report_cost_summaries WHERE run_id=?",
                            (run_id,),
                        ).fetchone()[0]
                    )
                    if "report_cost_summaries" in tables
                    else 0
                )
                ai_event_count = (
                    int(
                        conn.execute(
                            "SELECT COUNT(*) FROM ai_variable_cost_events WHERE run_id=?",
                            (run_id,),
                        ).fetchone()[0]
                    )
                    if "ai_variable_cost_events" in tables
                    else 0
                )
                overrun_count = (
                    int(
                        conn.execute(
                            "SELECT COUNT(*) FROM budget_spend_overruns WHERE run_id=?",
                            (run_id,),
                        ).fetchone()[0]
                    )
                    if "budget_spend_overruns" in tables
                    else 0
                )
        except PilotRunnerError:
            raise
        except sqlite3.Error as exc:
            raise PilotRunnerError(
                "법인명 복구용 lifecycle·비용·보고서 증거를 읽지 못했습니다"
            ) from exc

        if lifecycle_row is None or str(lifecycle_row[0]) != "final":
            raise PilotRunnerError("확인 lifecycle이 final이 아니어서 복구를 차단했습니다")
        if not _is_exact_zero_cost(lifecycle_row[1]):
            raise PilotRunnerError("확인 lifecycle 확정 비용이 0원이 아닙니다")
        if [(item[0], str(item[1])) for item in audit_rows] != [
            (None, "pending"),
            ("pending", "final"),
        ]:
            raise PilotRunnerError(
                "lifecycle에 pending→final 외 전이가 있어 no-run 복구를 차단했습니다"
            )
        if any(
            count != 0
            for count in (
                spend_count,
                inflight_count,
                report_count,
                cost_summary_count,
                ai_event_count,
                overrun_count,
            )
        ):
            raise PilotRunnerError(
                "비용·inflight·보고서·run 요약 흔적이 있어 복구를 차단했습니다"
            )

        try:
            final_record = json.loads(str(lifecycle_row[2]))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise PilotRunnerError("확인 lifecycle 최종 기록을 읽지 못했습니다") from exc
        zero_count_fields = (
            "fragments_collected",
            "fragments_cited",
            "sentences_made",
            "sentences_passed",
            "cells_filled",
        )
        if (
            not isinstance(final_record, dict)
            or str(final_record.get("run_id", "")) != run_id
            or str(final_record.get("end_step", "")) != END_STEP_CONFIRM
            or not _is_exact_zero_cost(final_record.get("cost_krw"))
            or str(final_record.get("model", ""))
            or any(
                type(final_record.get(field)) is not int
                or int(final_record.get(field)) != 0
                for field in zero_count_fields
            )
        ):
            raise PilotRunnerError(
                "최종 관측값이 0원 회사확인 종료 모양이 아니어서 복구를 차단했습니다"
            )

    def _validate_prior_day_restart_case(
        self,
        snapshot: Mapping[str, object],
        case: CanonicalPilotCase,
    ) -> None:
        """Require the exact sealed P01 unknown-charge shape and no other risk."""

        rows = self._case_rows(snapshot)
        for other_case_id, other in rows.items():
            if other_case_id == case.case_id:
                continue
            other_state = str(other.get("state", ""))
            if (
                other.get("billing_uncertain") is not False
                or other_state in UNRESOLVED_STATES
                or other_state == RESUMABLE_STATE
            ):
                raise PilotRunnerError(
                    "다른 미확정·실행 중 case가 있어 재시작 복구를 차단했습니다: "
                    + other_case_id
                )

        row = rows[case.case_id]
        if (
            str(row.get("state", "")) != "billing_uncertain"
            or str(row.get("error_code", "")) != "ledger_inflight_remains"
            or row.get("billing_uncertain") is not True
        ):
            raise PilotRunnerError(
                "재시작 복구 대상이 정확한 ledger_inflight_remains 상태가 아닙니다"
            )
        run_id = str(row.get("run_id", ""))
        if not _HEX_32_RE.fullmatch(run_id):
            raise PilotRunnerError("재시작 복구 대상 run ID가 올바르지 않습니다")
        if (
            str(row.get("selected_corp_code", "")) != case.corp_code
            or not exact_company_names_equivalent(
                str(row.get("legal_name", "")), case.expected_legal_name
            )
        ):
            raise PilotRunnerError("재시작 복구 대상의 DART 회사 identity가 다릅니다")
        if (
            str(row.get("outcome", "")) != Outcome.FAILED.value
            or str(row.get("report_id", ""))
            or row.get("result_http_status") != 200
        ):
            raise PilotRunnerError("재시작 복구 대상의 실패·보고서 상태가 다릅니다")
        try:
            checkpoint_cost = float(row.get("internal_ai_cost_krw"))
        except (TypeError, ValueError, OverflowError) as exc:
            raise PilotRunnerError("재시작 복구 대상의 알려진 비용이 올바르지 않습니다") from exc
        if not math.isfinite(checkpoint_cost) or checkpoint_cost <= 0:
            raise PilotRunnerError("재시작 복구 대상의 알려진 비용이 올바르지 않습니다")

        paid_at = _parse_timestamp(row.get("paid_boundary_at"))
        if paid_at is None:
            raise PilotRunnerError("재시작 복구 대상의 유료 경계 시각이 올바르지 않습니다")
        now = self.now()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        current_day = now.astimezone(_KST).date()
        paid_day = paid_at.astimezone(_KST).date()
        if current_day <= paid_day:
            raise PilotRunnerError(
                "미확정 비용을 같은 KST 사업일에 우회할 수 없습니다. "
                "다음 KST 사업일에 다시 복구하세요"
            )
        self._validate_prior_day_restart_storage(
            row,
            checkpoint_cost=checkpoint_cost,
            paid_at=paid_at,
            paid_day=paid_day.isoformat(),
            current_day=current_day.isoformat(),
        )

    def _validate_prior_day_restart_storage(
        self,
        row: Mapping[str, object],
        *,
        checkpoint_cost: float,
        paid_at: datetime,
        paid_day: str,
        current_day: str,
    ) -> None:
        """Cross-check every durable P01 ledger while preserving old inflight."""

        if current_day <= paid_day:
            raise PilotRunnerError("보존된 P01은 다음 KST 사업일에만 사용할 수 있습니다")
        run_id = str(row.get("run_id", ""))
        required_columns = {
            "budget_spend_events": {
                "run_id", "phase", "day", "bucket_id", "cost_krw", "created_at"
            },
            "budget_spend_inflight": {
                "run_id", "phase", "day", "bucket_id", "reserved_krw", "started_at"
            },
            "report_cost_summaries": {
                "run_id", "outcome", "internal_ai_cost_krw",
                "customer_charge_krw", "charge_eligible",
                "automatic_release_sha256", "charge_reason",
            },
            "ai_variable_cost_events": {
                "run_id", "sequence", "stage", "model_id", "input_tokens",
                "output_tokens", "cache_creation_tokens", "cache_read_tokens",
                "cost_krw", "failed_call",
            },
        }
        try:
            with self._connect_storage() as conn:
                tables = {
                    str(item[0])
                    for item in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'"
                    ).fetchall()
                }
                missing_tables = set(required_columns) - tables
                if missing_tables:
                    raise PilotRunnerError(
                        "재시작 복구 증거 표가 없습니다: "
                        + ", ".join(sorted(missing_tables))
                    )
                malformed = [
                    table
                    for table, required in required_columns.items()
                    if not required.issubset(
                        {
                            str(column[1])
                            for column in conn.execute(
                                f"PRAGMA table_info({table})"
                            ).fetchall()
                        }
                    )
                ]
                if malformed:
                    raise PilotRunnerError(
                        "재시작 복구 증거 표 모양이 올바르지 않습니다: "
                        + ", ".join(malformed)
                    )
                lifecycle_row = conn.execute(
                    "SELECT state, final_record_json "
                    "FROM observability_run_lifecycle WHERE run_id=?",
                    (run_id,),
                ).fetchone()
                cost_rows = conn.execute(
                    "SELECT outcome, internal_ai_cost_krw, customer_charge_krw, "
                    "charge_eligible, automatic_release_sha256, charge_reason "
                    "FROM report_cost_summaries WHERE run_id=?",
                    (run_id,),
                ).fetchall()
                spend_rows = conn.execute(
                    "SELECT phase, day, bucket_id, cost_krw, created_at "
                    "FROM budget_spend_events WHERE run_id=? ORDER BY phase",
                    (run_id,),
                ).fetchall()
                inflight_rows = conn.execute(
                    "SELECT phase, day, bucket_id, reserved_krw, started_at "
                    "FROM budget_spend_inflight WHERE run_id=? ORDER BY phase",
                    (run_id,),
                ).fetchall()
                ai_rows = conn.execute(
                    "SELECT sequence, stage, model_id, input_tokens, output_tokens, "
                    "cache_creation_tokens, cache_read_tokens, cost_krw, failed_call "
                    "FROM ai_variable_cost_events WHERE run_id=? ORDER BY sequence",
                    (run_id,),
                ).fetchall()
                report_count = int(
                    conn.execute(
                        "SELECT COUNT(*) FROM reports WHERE report_id=?", (run_id,)
                    ).fetchone()[0]
                )
        except PilotRunnerError:
            raise
        except sqlite3.Error as exc:
            raise PilotRunnerError("재시작 복구용 원장 증거를 읽지 못했습니다") from exc

        if lifecycle_row is None or str(lifecycle_row[0]) != "final":
            raise PilotRunnerError("재시작 복구 대상 lifecycle이 final이 아닙니다")
        try:
            final_record = json.loads(str(lifecycle_row[1]))
            lifecycle_cost = float(final_record.get("cost_krw"))
        except (AttributeError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise PilotRunnerError("재시작 복구 lifecycle 최종 기록이 올바르지 않습니다") from exc
        zero_fields = (
            "fragments_cited",
            "sentences_made",
            "sentences_passed",
            "cells_filled",
        )
        if (
            not isinstance(final_record, dict)
            or str(final_record.get("run_id", "")) != run_id
            or str(final_record.get("end_step", "")) != END_STEP_GENERATE
            or type(final_record.get("fragments_collected")) is not int
            or int(final_record.get("fragments_collected")) <= 0
            or any(
                type(final_record.get(field)) is not int
                or int(final_record.get(field)) != 0
                for field in zero_fields
            )
            or not math.isfinite(lifecycle_cost)
            or not math.isclose(
                lifecycle_cost, checkpoint_cost, rel_tol=1e-9, abs_tol=1e-6
            )
        ):
            raise PilotRunnerError("재시작 복구 lifecycle 최종 기록이 P01과 다릅니다")

        if len(cost_rows) != 1:
            raise PilotRunnerError("재시작 복구 비용 요약은 정확히 한 행이어야 합니다")
        cost_row = cost_rows[0]
        try:
            summary_cost = float(cost_row[1])
            customer_charge = float(cost_row[2])
            charge_eligible = int(cost_row[3])
        except (TypeError, ValueError, OverflowError) as exc:
            raise PilotRunnerError("재시작 복구 비용 요약 금액이 올바르지 않습니다") from exc
        if (
            str(cost_row[0]) != Outcome.FAILED.value
            or not math.isclose(
                summary_cost, checkpoint_cost, rel_tol=1e-9, abs_tol=1e-6
            )
            or customer_charge != 0.0
            or charge_eligible != 0
            or str(cost_row[4])
            or str(cost_row[5]) != "not_automatically_released"
            or report_count != 0
        ):
            raise PilotRunnerError("재시작 복구 비용·보고서 증거가 P01과 다릅니다")

        if len(spend_rows) != 1 or len(inflight_rows) != 1:
            raise PilotRunnerError("P01의 확정 비용과 미확정 표식이 각각 한 행이 아닙니다")
        spend = spend_rows[0]
        inflight = inflight_rows[0]
        try:
            spend_cost = float(spend[3])
            reserved_cost = float(inflight[3])
        except (TypeError, ValueError, OverflowError) as exc:
            raise PilotRunnerError("P01 비용 표식 금액이 올바르지 않습니다") from exc
        pipeline_reservation = PAID_PHASE_PROVIDER_BUDGET_KRW[SPEND_PHASE_PIPELINE]
        if (
            str(spend[0]) != SPEND_PHASE_PIPELINE
            or str(inflight[0]) != SPEND_PHASE_PIPELINE
            or str(spend[1]) != paid_day
            or str(inflight[1]) != paid_day
            or str(inflight[1]) == current_day
            or str(spend[2]) != str(inflight[2])
            or not _SHA256_RE.fullmatch(str(spend[2]))
            or not math.isfinite(spend_cost)
            or not math.isfinite(reserved_cost)
            or reserved_cost <= 0
            or not math.isclose(
                spend_cost, checkpoint_cost, rel_tol=1e-9, abs_tol=1e-6
            )
            or not math.isclose(
                spend_cost + reserved_cost,
                pipeline_reservation,
                rel_tol=1e-9,
                abs_tol=1e-6,
            )
        ):
            raise PilotRunnerError("P01 확정 비용·미확정 예약 증거가 다릅니다")
        started_at = _parse_timestamp(inflight[4])
        spend_at = _parse_timestamp(spend[4])
        if (
            started_at is None
            or spend_at is None
            or started_at < paid_at - timedelta(seconds=10)
            or spend_at < started_at
        ):
            raise PilotRunnerError("P01 비용 원장의 시각 순서가 올바르지 않습니다")

        if len(ai_rows) != 1:
            raise PilotRunnerError("P01의 알려진 AI 비용 이벤트가 정확히 한 행이 아닙니다")
        ai = ai_rows[0]
        token_counts = ai[3:7]
        try:
            sequence = int(ai[0])
            ai_cost = float(ai[7])
            failed_call = int(ai[8])
        except (TypeError, ValueError, OverflowError) as exc:
            raise PilotRunnerError("P01 AI 비용 이벤트 금액이 올바르지 않습니다") from exc
        if (
            sequence != 1
            or str(ai[1]) != "collect"
            or not str(ai[2]).strip()
            or any(type(value) is not int or value < 0 for value in token_counts)
            or failed_call != 0
            or not math.isfinite(ai_cost)
            or not math.isclose(ai_cost, checkpoint_cost, rel_tol=1e-9, abs_tol=1e-6)
            or str(final_record.get("model", "")) != str(ai[2])
        ):
            raise PilotRunnerError("P01의 알려진 AI 비용 이벤트가 최종 기록과 다릅니다")

    def _poll_and_finalize(
        self,
        snapshot: dict[str, object],
        case: CanonicalPilotCase,
        run_id: str,
    ) -> None:
        deadline = time.monotonic() + self.poll_timeout_sec
        next_url = ""
        while True:
            try:
                response = self.client.get(f"/api/progress/{run_id}")
            except httpx.RequestError as exc:
                self._update_case(
                    snapshot,
                    case.case_id,
                    state=RESUMABLE_STATE,
                    billing_uncertain=False,
                    error_code="progress_network_error",
                )
                raise PilotRunnerError("진행 조회 연결이 끊겼습니다. 같은 서버로 재개하세요") from exc
            if len(response.content) > 64 * 1024:
                raise PilotRunnerError("진행 JSON 응답이 허용 크기를 넘었습니다")
            if response.status_code == 200:
                payload = self._decode_json(response, "progress")
                if payload.get("finished") is True:
                    next_url = str(payload.get("next_url", ""))
                    if next_url != f"/result/{run_id}":
                        raise PilotRunnerError("진행 완료의 result 경로가 run ID와 다릅니다")
                    break
            elif response.status_code in {409, 410}:
                # A restarted server may have finalized costs even though it cannot
                # reconstruct a stopped result page.  The ledger remains decisive.
                next_url = ""
                break
            elif response.status_code != 503:
                raise PilotRunnerError(
                    f"진행 조회가 허용되지 않은 HTTP {response.status_code}를 반환했습니다"
                )
            if time.monotonic() >= deadline:
                self._update_case(
                    snapshot,
                    case.case_id,
                    state=RESUMABLE_STATE,
                    billing_uncertain=False,
                    error_code="progress_timeout",
                )
                raise PilotRunnerError("진행 조회 시간이 끝났습니다. 같은 서버로 재개하세요")
            self.sleep(self.poll_interval_sec)

        result_status: int | None = None
        if next_url:
            try:
                result_response = self.client.get(next_url)
            except httpx.RequestError as exc:
                self._update_case(
                    snapshot,
                    case.case_id,
                    state=RESUMABLE_STATE,
                    billing_uncertain=False,
                    error_code="result_network_error",
                )
                raise PilotRunnerError("결과 조회 연결이 끊겼습니다. 같은 서버로 재개하세요") from exc
            result_status = result_response.status_code
            if result_status not in {200, 409}:
                self._update_case(
                    snapshot,
                    case.case_id,
                    state=RESUMABLE_STATE,
                    billing_uncertain=False,
                    result_http_status=result_status,
                    error_code="result_not_terminal",
                )
                raise PilotRunnerError(
                    f"결과 화면이 확정 상태가 아닌 HTTP {result_status}를 반환했습니다"
                )

        try:
            ledger = self._wait_for_ledger(run_id)
        except _LedgerConsistencyError as exc:
            self._update_case(
                snapshot,
                case.case_id,
                state="billing_uncertain",
                billing_uncertain=True,
                result_http_status=result_status,
                error_code=exc.code,
            )
            raise PilotBatchBlocked(str(exc)) from exc
        if ledger is None:
            self._update_case(
                snapshot,
                case.case_id,
                state=RESUMABLE_STATE,
                billing_uncertain=False,
                result_http_status=result_status,
                error_code="ledger_pending",
            )
            raise PilotRunnerError("비용 원장 마감이 아직 보이지 않습니다. 같은 데이터로 재개하세요")
        if ledger.billing_uncertain:
            self._update_case(
                snapshot,
                case.case_id,
                state="billing_uncertain",
                outcome=ledger.outcome,
                internal_ai_cost_krw=ledger.cost_krw,
                billing_uncertain=True,
                result_http_status=result_status,
                error_code="ledger_inflight_remains",
                final_gate_reason=ledger.final_gate_reason,
            )
            raise PilotBatchBlocked("미확정 비용 표식이 남아 다음 유료 호출을 차단했습니다")
        if ledger.outcome == Outcome.REPORT.value:
            if ledger.report_id != run_id or ledger.corp_id != case.corp_code:
                self._update_case(
                    snapshot,
                    case.case_id,
                    state="identity_mismatch",
                    outcome=ledger.outcome,
                    internal_ai_cost_krw=ledger.cost_krw,
                    billing_uncertain=False,
                    result_http_status=result_status,
                    error_code="stored_report_identity_mismatch",
                    final_gate_reason=ledger.final_gate_reason,
                )
                return
            if result_status != 200 or not _SHA256_RE.fullmatch(
                ledger.automatic_release_sha256
            ):
                # Generation finished, but output release is not a complete report
                # for pilot scoring.  It is still terminal and must not be rerun.
                error_code = "automatic_release_blocked"
            else:
                error_code = ""
        else:
            error_code = ""
        self._update_case(
            snapshot,
            case.case_id,
            state="completed",
            report_id=ledger.report_id,
            outcome=ledger.outcome,
            internal_ai_cost_krw=ledger.cost_krw,
            billing_uncertain=False,
            result_http_status=result_status,
            error_code=error_code,
            final_gate_reason=ledger.final_gate_reason,
        )

    def _update_case(
        self,
        snapshot: dict[str, object],
        case_id: str,
        *,
        state: str,
        now: str | None = None,
        **changes: object,
    ) -> Mapping[str, object]:
        row = self.checkpoint.update_case(
            snapshot,
            case_id,
            state=state,
            now=now,
            **changes,
        )
        self._seal_checkpoint()
        return row

    def _workflow_page(self) -> WorkflowPage:
        try:
            response = self.client.get("/")
        except httpx.RequestError as exc:
            raise PilotRunnerError("로컬 평가 첫 화면에 연결하지 못했습니다") from exc
        page = self._expect_html(response, 200, "input")
        forms = tuple(form for form in page.forms if form.action == "/confirm")
        if len(forms) != 1 or not page.has_paid_consent_checkbox:
            raise PilotRunnerError("유료 실시간 평가 동의 form이 활성화되지 않았습니다")
        csrf = forms[0].fields.get("csrf_token", "")
        workflow_id = forms[0].fields.get("evaluation_workflow_id", "")
        if not _SHA256_RE.fullmatch(csrf) or not _HEX_32_RE.fullmatch(workflow_id):
            raise PilotRunnerError("평가 화면의 CSRF/workflow 계약이 올바르지 않습니다")
        return WorkflowPage(
            csrf_token=csrf,
            workflow_id=workflow_id,
            server_instance_digest=hashlib.sha256(csrf.encode("utf-8")).hexdigest(),
        )

    def _post_paid_boundary(
        self, path: str, data: Mapping[str, str], case_id: str
    ) -> httpx.Response:
        # Do not include data, tokens, or response bodies in any raised message.
        try:
            return self.client.post(path, data=data)
        except httpx.RequestError as exc:
            raise PilotBatchBlocked(
                f"{case_id} 유료 경계 응답을 받지 못해 자동 재호출을 차단했습니다"
            ) from exc

    def _validate_candidate_form(
        self, form: ParsedForm, case: CanonicalPilotCase
    ) -> None:
        required = {
            "csrf_token",
            "company",
            "region",
            "retry",
            "candidate_resolution_confirmed",
            "candidate_attempt_token",
            "candidate_selection_token",
            "candidate_index",
            "candidate_name",
            "candidate_provider",
            "candidate_ref",
            "evaluation_consent_grant",
        }
        if (
            set(form.fields) != required
            or form.fields.get("company") != case.input_name
            or form.fields.get("region") != case.address_hint
            or form.fields.get("candidate_ref") != case.corp_code
            or form.fields.get("candidate_provider") != "DART"
            or form.fields.get("candidate_resolution_confirmed") != "yes"
            or not form.fields.get("candidate_attempt_token")
            or not form.fields.get("candidate_selection_token")
            or not form.fields.get("evaluation_consent_grant")
        ):
            raise PilotRunnerError("선택한 DART 후보 form 계약이 manifest와 다릅니다")

    def _validate_run_form(self, form: ParsedForm, case: CanonicalPilotCase) -> None:
        required = {
            "csrf_token",
            "company",
            "region",
            "paid_attempt_token",
            "evaluation_consent_grant",
        }
        if (
            set(form.fields) != required
            or form.fields.get("company") != case.input_name
            or form.fields.get("region") != case.address_hint
            or not form.fields.get("paid_attempt_token")
            or not form.fields.get("evaluation_consent_grant")
        ):
            raise PilotRunnerError("조사 시작 form 계약이 manifest와 다릅니다")

    def _reject_if_possible(self, page: ParsedPage) -> bool:
        forms = tuple(form for form in page.forms if form.action == "/reject")
        if len(forms) != 1:
            return False
        required = {
            "csrf_token",
            "company",
            "region",
            "retry",
            "paid_attempt_token",
            "evaluation_consent_grant",
        }
        if set(forms[0].fields) != required or not forms[0].fields.get("paid_attempt_token"):
            return False
        try:
            response = self.client.post("/reject", data=dict(forms[0].fields))
        except httpx.RequestError:
            return False
        return response.status_code == 200

    def _accepted_run_id(self, response: httpx.Response) -> str:
        if response.status_code != 303:
            raise PilotBatchBlocked(
                f"조사 시작 응답 HTTP {response.status_code}를 확정할 수 없어 재호출을 차단했습니다"
            )
        location = response.headers.get("location", "")
        relative = self._same_origin_relative_location(location)
        match = _RUN_LOCATION_RE.fullmatch(relative)
        if match is None:
            raise PilotBatchBlocked("조사 시작 redirect가 안전한 progress 경로가 아닙니다")
        return match.group(1)

    def _same_origin_relative_location(self, location: str) -> str:
        try:
            absolute = urljoin(self.origin + "/", location)
            parsed = urlsplit(absolute)
            expected = urlsplit(self.origin)
        except ValueError as exc:
            raise PilotRunnerError("redirect 경로 형식이 올바르지 않습니다") from exc
        if (
            parsed.scheme != expected.scheme
            or parsed.hostname != expected.hostname
            or parsed.port != expected.port
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise PilotRunnerError("loopback origin 밖의 redirect를 거부했습니다")
        return parsed.path

    def _expect_html(
        self, response: httpx.Response, expected_status: int, stage: str
    ) -> ParsedPage:
        if response.status_code != expected_status:
            raise PilotRunnerError(
                f"{stage} 응답이 HTTP {response.status_code}여서 중단했습니다"
            )
        content_type = response.headers.get("content-type", "").lower()
        if "text/html" not in content_type:
            raise PilotRunnerError(f"{stage} 응답이 HTML이 아닙니다")
        return parse_page(response.content)

    def _get_json(self, path: str, *, expected_status: int) -> Mapping[str, object]:
        try:
            response = self.client.get(path)
        except httpx.RequestError as exc:
            raise PilotRunnerError(f"{path}에 연결하지 못했습니다") from exc
        if response.status_code != expected_status:
            raise PilotRunnerError(f"{path}가 HTTP {response.status_code}를 반환했습니다")
        return self._decode_json(response, path)

    @staticmethod
    def _decode_json(response: httpx.Response, stage: str) -> Mapping[str, object]:
        if len(response.content) > 64 * 1024:
            raise PilotRunnerError(f"{stage} JSON 응답이 허용 크기를 넘었습니다")
        try:
            payload = json.loads(response.content)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise PilotRunnerError(f"{stage} JSON 응답을 읽지 못했습니다") from exc
        if not isinstance(payload, dict):
            raise PilotRunnerError(f"{stage} JSON 응답 모양이 올바르지 않습니다")
        return payload

    def _validate_storage(self) -> None:
        if not self.storage_db_path.is_file() or self.storage_db_path.is_symlink():
            raise PilotRunnerError("실시간 평가의 일반 SQLite 파일을 지정해야 합니다")
        required = {
            "observability_run_lifecycle",
            "budget_spend_events",
            "budget_spend_inflight",
            "reports",
        }
        with self._connect_storage() as conn:
            present = {
                str(row[0])
                for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            cost_columns = (
                {
                    str(row[1])
                    for row in conn.execute(
                        "PRAGMA table_info(report_cost_summaries)"
                    ).fetchall()
                }
                if "report_cost_summaries" in present
                else set()
            )
            lifecycle_columns = {
                str(row[1])
                for row in conn.execute(
                    "PRAGMA table_info(observability_run_lifecycle)"
                ).fetchall()
            }
            spend_columns = {
                str(row[1])
                for row in conn.execute(
                    "PRAGMA table_info(budget_spend_events)"
                ).fetchall()
            }
            inflight_columns = {
                str(row[1])
                for row in conn.execute(
                    "PRAGMA table_info(budget_spend_inflight)"
                ).fetchall()
            }
            report_columns = {
                str(row[1])
                for row in conn.execute("PRAGMA table_info(reports)").fetchall()
            }
            try:
                validate_final_gate_table_if_present(conn)
            except FinalGateEvidenceError as exc:
                raise PilotRunnerError(str(exc)) from exc
        missing = required - present
        if missing:
            raise PilotRunnerError(
                "실시간 평가 SQLite에 필요한 표가 없습니다: " + ", ".join(sorted(missing))
            )
        required_cost_columns = {
            "run_id",
            "outcome",
            "internal_ai_cost_krw",
            "automatic_release_sha256",
        }
        if cost_columns and not required_cost_columns.issubset(cost_columns):
            raise PilotRunnerError("실시간 평가 SQLite의 비용 원장 표 모양이 올바르지 않습니다")
        required_evidence_columns = {
            "observability_run_lifecycle": (
                lifecycle_columns,
                {"run_id", "state", "final_record_json"},
            ),
            "budget_spend_events": (spend_columns, {"run_id", "cost_krw"}),
            "budget_spend_inflight": (inflight_columns, {"run_id"}),
            "reports": (report_columns, {"report_id", "corp_id"}),
        }
        malformed = [
            table
            for table, (columns, expected_columns) in required_evidence_columns.items()
            if not expected_columns.issubset(columns)
        ]
        if malformed:
            raise PilotRunnerError(
                "실시간 평가 SQLite의 실행·비용 증거 표 모양이 올바르지 않습니다: "
                + ", ".join(malformed)
            )

    def _validate_restart_recovery_binding(
        self,
        snapshot: Mapping[str, object],
        *,
        current_server_digest: str,
    ) -> tuple[str, str]:
        """Verify the old two-file seal before a one-time process rebind."""

        manifest_digest = manifest_sha256(self.cases)
        data_path_digest = hashlib.sha256(
            str(self.storage_db_path).casefold().encode("utf-8")
        ).hexdigest()
        checkpoint_path_digest = hashlib.sha256(
            str(self.checkpoint.path).casefold().encode("utf-8")
        ).hexdigest()
        old_server_digest = str(snapshot.get("server_instance_sha256", ""))
        binding_id = str(snapshot.get("binding_id", ""))
        if (
            snapshot.get("schema_version") != SCHEMA_VERSION
            or str(snapshot.get("manifest_sha256", "")) != manifest_digest
            or str(snapshot.get("origin", "")) != self.origin
            or str(snapshot.get("data_path_sha256", "")) != data_path_digest
            or not _SHA256_RE.fullmatch(old_server_digest)
            or not _SHA256_RE.fullmatch(current_server_digest)
            or not _HEX_32_RE.fullmatch(binding_id)
        ):
            raise CheckpointError(
                "재시작 복구 checkpoint의 manifest·origin·경로 결속이 다릅니다"
            )
        stored_cases = snapshot.get("cases")
        if not isinstance(stored_cases, dict) or tuple(stored_cases) != tuple(
            case.case_id for case in self.cases
        ):
            raise CheckpointError("재시작 복구 checkpoint의 case 구성이 다릅니다")
        if current_server_digest == old_server_digest:
            raise PilotRunnerError("서버 instance가 바뀌지 않아 재시작 복구를 거부했습니다")

        try:
            with self._connect_storage() as conn:
                row = conn.execute(
                    f"SELECT schema_version, binding_id, manifest_sha256, origin, "
                    "server_instance_sha256, data_path_sha256, checkpoint_path_sha256, "
                    "checkpoint_content_sha256 "
                    f"FROM {PILOT_BINDING_TABLE} WHERE pilot_key=?",
                    (PILOT_BINDING_KEY,),
                ).fetchone()
        except sqlite3.Error as exc:
            raise CheckpointError("재시작 복구용 DB binding을 읽지 못했습니다") from exc
        if row is None:
            raise CheckpointError("재시작 복구할 DB binding이 없습니다")
        sealed_digest = str(row[7])
        try:
            stored = (
                int(row[0]),
                str(row[1]),
                str(row[2]),
                str(row[3]),
                str(row[4]),
                str(row[5]),
                str(row[6]),
            )
        except (TypeError, ValueError, OverflowError) as exc:
            raise CheckpointError(
                "재시작 복구의 DB와 checkpoint 결속값이 올바르지 않습니다"
            ) from exc
        expected = (
            PILOT_BINDING_SCHEMA_VERSION,
            binding_id,
            manifest_digest,
            self.origin,
            old_server_digest,
            data_path_digest,
            checkpoint_path_digest,
        )
        if stored != expected or not _SHA256_RE.fullmatch(sealed_digest):
            raise CheckpointError("재시작 복구의 DB와 checkpoint 결속값이 다릅니다")
        if self._checkpoint_sha256() != sealed_digest:
            raise CheckpointError("재시작 복구 전 checkpoint 내용 seal이 다릅니다")
        self._binding_id = binding_id
        self._sealed_checkpoint_sha256 = sealed_digest
        return old_server_digest, sealed_digest

    def _cas_restart_recovery_binding(
        self,
        *,
        old_server_digest: str,
        new_server_digest: str,
        old_checkpoint_digest: str,
        new_checkpoint_digest: str,
    ) -> None:
        """CAS both mutable binding fields after the checkpoint atomic write."""

        manifest_digest = manifest_sha256(self.cases)
        data_path_digest = hashlib.sha256(
            str(self.storage_db_path).casefold().encode("utf-8")
        ).hexdigest()
        checkpoint_path_digest = hashlib.sha256(
            str(self.checkpoint.path).casefold().encode("utf-8")
        ).hexdigest()
        try:
            with sqlite3.connect(str(self.storage_db_path), timeout=2.0) as conn:
                conn.execute("BEGIN IMMEDIATE")
                cursor = conn.execute(
                    f"UPDATE {PILOT_BINDING_TABLE} "
                    "SET server_instance_sha256=?, checkpoint_content_sha256=? "
                    "WHERE pilot_key=? AND schema_version=? AND binding_id=? "
                    "AND manifest_sha256=? AND origin=? "
                    "AND server_instance_sha256=? AND data_path_sha256=? "
                    "AND checkpoint_path_sha256=? AND checkpoint_content_sha256=?",
                    (
                        new_server_digest,
                        new_checkpoint_digest,
                        PILOT_BINDING_KEY,
                        PILOT_BINDING_SCHEMA_VERSION,
                        self._binding_id,
                        manifest_digest,
                        self.origin,
                        old_server_digest,
                        data_path_digest,
                        checkpoint_path_digest,
                        old_checkpoint_digest,
                    ),
                )
                if cursor.rowcount != 1:
                    raise CheckpointError(
                        "재시작 복구 중 DB binding이 바뀌어 실행을 차단했습니다"
                    )
        except CheckpointError:
            raise
        except sqlite3.Error as exc:
            raise CheckpointError("재시작 복구 DB binding CAS에 실패했습니다") from exc
        self._sealed_checkpoint_sha256 = new_checkpoint_digest

    def _bind_checkpoint_to_storage(
        self,
        *,
        manifest_digest: str,
        server_instance_digest: str,
        data_path_digest: str,
        checkpoint_path_digest: str,
    ) -> str:
        """Bind one checkpoint path to this DB before any pilot POST.

        Ordinary reports and unrelated runs may already exist in the database;
        only this dedicated marker identifies a canonical pilot.  Losing or
        renaming the checkpoint therefore cannot silently create a second paid
        batch or reset the runner's 5-per-10-minute window.
        """

        expected = {
            "schema_version": PILOT_BINDING_SCHEMA_VERSION,
            "manifest_sha256": manifest_digest,
            "origin": self.origin,
            "server_instance_sha256": server_instance_digest,
            "data_path_sha256": data_path_digest,
            "checkpoint_path_sha256": checkpoint_path_digest,
        }
        try:
            with sqlite3.connect(str(self.storage_db_path), timeout=2.0) as conn:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(_CREATE_PILOT_BINDING_SQL)
                binding_columns = {
                    str(column[1])
                    for column in conn.execute(
                        f"PRAGMA table_info({PILOT_BINDING_TABLE})"
                    ).fetchall()
                }
                required_binding_columns = {
                    "pilot_key",
                    "schema_version",
                    "binding_id",
                    "manifest_sha256",
                    "origin",
                    "server_instance_sha256",
                    "data_path_sha256",
                    "checkpoint_path_sha256",
                    "checkpoint_content_sha256",
                    "created_at",
                }
                if not required_binding_columns.issubset(binding_columns):
                    raise CheckpointError(
                        "이전 형식의 파일럿 binding은 체크포인트 내용 무결성을 "
                        "증명하지 못하므로 새 격리 DB가 필요합니다"
                    )
                row = conn.execute(
                    f"SELECT schema_version, binding_id, manifest_sha256, origin, "
                    "server_instance_sha256, data_path_sha256, checkpoint_path_sha256, "
                    "checkpoint_content_sha256 "
                    f"FROM {PILOT_BINDING_TABLE} WHERE pilot_key=?",
                    (PILOT_BINDING_KEY,),
                ).fetchone()
                if row is not None:
                    stored = {
                        "schema_version": int(row[0]),
                        "manifest_sha256": str(row[2]),
                        "origin": str(row[3]),
                        "server_instance_sha256": str(row[4]),
                        "data_path_sha256": str(row[5]),
                        "checkpoint_path_sha256": str(row[6]),
                    }
                    mismatches = [
                        key for key, value in expected.items() if stored.get(key) != value
                    ]
                    if mismatches:
                        raise CheckpointError(
                            "평가 SQLite의 파일럿 binding이 현재 실행과 다릅니다: "
                            + ", ".join(mismatches)
                        )
                    if not self.checkpoint.path.is_file():
                        raise CheckpointError(
                            "평가 SQLite에 파일럿 binding은 있지만 체크포인트가 없어 "
                            "중복 실행을 차단했습니다"
                        )
                    binding_id = str(row[1])
                    if not _HEX_32_RE.fullmatch(binding_id):
                        raise CheckpointError(
                            "평가 SQLite의 파일럿 binding ID가 올바르지 않습니다"
                        )
                    sealed_digest = str(row[7])
                    if not _SHA256_RE.fullmatch(sealed_digest):
                        raise CheckpointError(
                            "평가 SQLite의 체크포인트 내용 지문이 올바르지 않습니다"
                        )
                    current_digest = self._checkpoint_sha256()
                    if current_digest != sealed_digest:
                        raise CheckpointError(
                            "체크포인트 내용이 평가 SQLite에 결속된 상태와 달라 "
                            "중복 실행을 차단했습니다"
                        )
                    self._binding_id = binding_id
                    self._sealed_checkpoint_sha256 = sealed_digest
                    return binding_id

                if self.checkpoint.path.exists():
                    raise CheckpointError(
                        "체크포인트는 있지만 평가 SQLite의 파일럿 binding이 없어 "
                        "자동 재개를 차단했습니다"
                    )
                binding_id = secrets.token_hex(16)
                conn.execute(
                    f"INSERT INTO {PILOT_BINDING_TABLE} ("
                    "pilot_key, schema_version, binding_id, manifest_sha256, origin, "
                    "server_instance_sha256, data_path_sha256, checkpoint_path_sha256, "
                    "checkpoint_content_sha256, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, '', ?)",
                    (
                        PILOT_BINDING_KEY,
                        PILOT_BINDING_SCHEMA_VERSION,
                        binding_id,
                        manifest_digest,
                        self.origin,
                        server_instance_digest,
                        data_path_digest,
                        checkpoint_path_digest,
                        self._now_iso(),
                    ),
                )
                self._binding_id = binding_id
                self._sealed_checkpoint_sha256 = ""
                return binding_id
        except CheckpointError:
            raise
        except sqlite3.Error as exc:
            raise CheckpointError(
                "평가 SQLite에 파일럿 체크포인트 binding을 안전하게 기록하지 못했습니다"
            ) from exc

    def _checkpoint_sha256(self) -> str:
        try:
            content = self.checkpoint.path.read_bytes()
        except OSError as exc:
            raise CheckpointError("체크포인트 내용 지문을 읽지 못했습니다") from exc
        return hashlib.sha256(content).hexdigest()

    def _seal_checkpoint(self) -> None:
        if not _HEX_32_RE.fullmatch(self._binding_id):
            raise CheckpointError("체크포인트를 결속할 평가 SQLite binding이 없습니다")
        digest = self._checkpoint_sha256()
        if digest == self._sealed_checkpoint_sha256:
            return
        try:
            with sqlite3.connect(str(self.storage_db_path), timeout=2.0) as conn:
                conn.execute("BEGIN IMMEDIATE")
                cursor = conn.execute(
                    f"UPDATE {PILOT_BINDING_TABLE} "
                    "SET checkpoint_content_sha256=? "
                    "WHERE pilot_key=? AND binding_id=? "
                    "AND checkpoint_content_sha256=?",
                    (
                        digest,
                        PILOT_BINDING_KEY,
                        self._binding_id,
                        self._sealed_checkpoint_sha256,
                    ),
                )
                if cursor.rowcount != 1:
                    raise CheckpointError(
                        "평가 SQLite의 체크포인트 결속 상태가 바뀌어 실행을 차단했습니다"
                    )
        except CheckpointError:
            raise
        except sqlite3.Error as exc:
            raise CheckpointError(
                "평가 SQLite에 체크포인트 내용 지문을 결속하지 못했습니다"
            ) from exc
        self._sealed_checkpoint_sha256 = digest

    def _connect_storage(self) -> sqlite3.Connection:
        uri = self.storage_db_path.as_uri() + "?mode=ro"
        return sqlite3.connect(uri, uri=True, timeout=2.0)

    def _validate_service_restart_after_block(self, paid_at: datetime) -> None:
        """Require an audited normal restart after a known maintenance 429."""

        try:
            with self._connect_storage() as conn:
                state = conn.execute(
                    "SELECT status, updated_at FROM dashboard_service_state "
                    "WHERE singleton = 1"
                ).fetchone()
                maintenance = conn.execute(
                    "SELECT created_at FROM dashboard_service_events "
                    "WHERE status = 'maintenance' ORDER BY id DESC LIMIT 500"
                ).fetchall()
                normal = conn.execute(
                    "SELECT cause, impact, next_action, created_at "
                    "FROM dashboard_service_events WHERE status = 'normal' "
                    "ORDER BY id DESC LIMIT 1"
                ).fetchone()
        except sqlite3.Error as exc:
            raise PilotRunnerError("점검 복구용 운영 상태 증거를 읽지 못했습니다") from exc
        if state is None or str(state[0]) != "normal":
            raise PilotRunnerError("관리자 재가동 뒤의 정상 운영 상태 증거가 없습니다")
        state_at = _parse_timestamp(state[1])
        maintenance_at = next(
            (
                parsed
                for event in maintenance
                if (parsed := _parse_timestamp(event[0])) is not None and parsed <= paid_at
            ),
            None,
        )
        normal_at = _parse_timestamp(normal[3]) if normal is not None else None
        if (
            state_at is None
            or maintenance_at is None
            or normal_at is None
            or normal_at <= paid_at
            or state_at < normal_at
            or not all(str(normal[index]).strip() for index in range(3))
        ):
            raise PilotRunnerError("P02 점검 뒤의 append-only 재가동 증거가 다릅니다")

    def _lifecycle_ids(self) -> frozenset[str]:
        with self._connect_storage() as conn:
            rows = conn.execute(
                "SELECT run_id FROM observability_run_lifecycle"
            ).fetchall()
        return frozenset(str(row[0]) for row in rows)

    def _single_new_lifecycle_id(
        self, before: frozenset[str], *, required: bool
    ) -> str:
        new_ids = self._lifecycle_ids() - before
        if len(new_ids) == 1:
            run_id = next(iter(new_ids))
            if _HEX_32_RE.fullmatch(run_id):
                return run_id
        if not required and not new_ids:
            return ""
        raise PilotBatchBlocked(
            "회사 확인 응답과 SQLite lifecycle의 새 run ID를 하나로 결속하지 못했습니다"
        )

    def _known_spend_cost(self, run_id: str) -> float | None:
        if not run_id:
            return None
        with self._connect_storage() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(cost_krw), 0) FROM budget_spend_events WHERE run_id=?",
                (run_id,),
            ).fetchone()
        return float(row[0]) if row is not None else None

    def _read_ledger(self, run_id: str) -> LedgerResult | None:
        with self._connect_storage() as conn:
            # Python sqlite3는 SELECT만으로 읽기 transaction을 자동 시작하지
            # 않는다. lifecycle·비용·보고서·최종 게이트가 한 WAL snapshot을
            # 가리키도록 첫 SELECT 전에 명시적으로 연다.
            conn.execute("BEGIN")
            # A brand-new launcher DB has not recorded a run yet, so the
            # feature-owned cost table legitimately does not exist at dry-run
            # time.  Once a run finishes, absence is still not evidence: keep
            # polling/fail closed instead of manufacturing a zero-cost row.
            cost_table = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                ("report_cost_summaries",),
            ).fetchone()
            if cost_table is None:
                return None
            row = conn.execute(
                "SELECT outcome, internal_ai_cost_krw, automatic_release_sha256 "
                "FROM report_cost_summaries WHERE run_id=?",
                (run_id,),
            ).fetchone()
            lifecycle_row = conn.execute(
                "SELECT state, final_record_json "
                "FROM observability_run_lifecycle WHERE run_id=?",
                (run_id,),
            ).fetchone()
            spend_row = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(cost_krw), 0) "
                "FROM budget_spend_events WHERE run_id=?",
                (run_id,),
            ).fetchone()
            inflight = int(
                conn.execute(
                    "SELECT COUNT(*) FROM budget_spend_inflight WHERE run_id=?",
                    (run_id,),
                ).fetchone()[0]
            )
            report = conn.execute(
                "SELECT report_id, corp_id FROM reports WHERE report_id=?",
                (run_id,),
            ).fetchone()
            if row is None:
                return None
            if lifecycle_row is None:
                return None
            lifecycle_state = str(lifecycle_row[0])
            if lifecycle_state in {"pending", "running"}:
                return None
            if lifecycle_state != "final":
                raise _LedgerConsistencyError(
                    "ledger_lifecycle_invalid",
                    "실행 lifecycle 상태가 비용 원장 마감과 일치하지 않아 다음 호출을 차단했습니다",
                )
            try:
                lifecycle_record = json.loads(str(lifecycle_row[1]))
                lifecycle_run_id = str(lifecycle_record.get("run_id", ""))
                lifecycle_cost = float(lifecycle_record.get("cost_krw"))
            except (AttributeError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise _LedgerConsistencyError(
                    "ledger_lifecycle_record_invalid",
                    "실행 lifecycle 최종 기록이 올바르지 않아 다음 호출을 차단했습니다",
                ) from exc
            if (
                lifecycle_run_id != run_id
                or not math.isfinite(lifecycle_cost)
                or lifecycle_cost < 0
            ):
                raise _LedgerConsistencyError(
                    "ledger_lifecycle_record_invalid",
                    "실행 lifecycle 최종 기록이 올바르지 않아 다음 호출을 차단했습니다",
                )
            outcome = str(row[0])
            try:
                cost = float(row[1])
                spend_cost = float(spend_row[1]) if spend_row is not None else 0.0
            except (TypeError, ValueError, OverflowError) as exc:
                raise _LedgerConsistencyError(
                    "ledger_cost_invalid",
                    "비용 원장의 내부 AI 원가가 올바르지 않아 다음 호출을 차단했습니다",
                ) from exc
            if outcome not in _KNOWN_OUTCOMES:
                raise _LedgerConsistencyError(
                    "ledger_outcome_invalid",
                    "비용 원장의 종료값이 올바르지 않아 다음 호출을 차단했습니다",
                )
            if (
                not math.isfinite(cost)
                or cost < 0
                or not math.isfinite(spend_cost)
                or spend_cost < 0
            ):
                raise _LedgerConsistencyError(
                    "ledger_cost_invalid",
                    "비용 원장의 내부 AI 원가가 올바르지 않아 다음 호출을 차단했습니다",
                )
            if not math.isclose(cost, spend_cost, rel_tol=1e-9, abs_tol=1e-6):
                raise _LedgerConsistencyError(
                    "ledger_cost_mismatch",
                    "요청별 비용 합계와 내부 AI 원가가 달라 다음 호출을 차단했습니다",
                )
            if not math.isclose(cost, lifecycle_cost, rel_tol=1e-9, abs_tol=1e-6):
                raise _LedgerConsistencyError(
                    "ledger_lifecycle_cost_mismatch",
                    "실행 lifecycle 원가와 내부 AI 원가가 달라 다음 호출을 차단했습니다",
                )
            if outcome == Outcome.REPORT.value and report is None:
                return None
            if outcome != Outcome.REPORT.value and report is not None:
                raise _LedgerConsistencyError(
                    "ledger_outcome_report_mismatch",
                    "종료값과 저장 보고서 존재 여부가 달라 다음 호출을 차단했습니다",
                )
            try:
                final_gate_reason = read_bound_reason(
                    conn,
                    run_id=run_id,
                    outcome=outcome,
                    gate_stopped_outcome=Outcome.GATE_STOPPED.value,
                    lifecycle_record=lifecycle_record,
                )
            except (FinalGateEvidenceError, sqlite3.Error) as exc:
                raise _LedgerConsistencyError(
                    "final_gate_evidence_invalid",
                    "최종 게이트 진단이 lifecycle·종료값과 달라 다음 호출을 차단했습니다",
                ) from exc
            return LedgerResult(
                outcome=outcome,
                cost_krw=cost,
                billing_uncertain=inflight > 0,
                report_id=str(report[0]) if report is not None else "",
                corp_id=str(report[1]) if report is not None else "",
                automatic_release_sha256=str(row[2] or ""),
                final_gate_reason=final_gate_reason,
            )

    def _wait_for_ledger(self, run_id: str) -> LedgerResult | None:
        deadline = time.monotonic() + self.ledger_settle_timeout_sec
        while True:
            result = self._read_ledger(run_id)
            if result is not None:
                return result
            if time.monotonic() >= deadline:
                return None
            self.sleep(min(self.poll_interval_sec, 0.25))

    def _verify_known_runs_use_storage(self, snapshot: Mapping[str, object]) -> None:
        run_ids = {
            str(row.get("run_id", ""))
            for row in self._case_rows(snapshot).values()
            if str(row.get("run_id", ""))
        }
        if not run_ids:
            return
        placeholders = ",".join("?" for _ in run_ids)
        values = tuple(sorted(run_ids))
        with self._connect_storage() as conn:
            found = {
                str(row[0])
                for row in conn.execute(
                    "SELECT run_id FROM observability_run_lifecycle "
                    f"WHERE run_id IN ({placeholders})",
                    values,
                ).fetchall()
            }
        if found != run_ids:
            raise CheckpointError("체크포인트 run ID가 지정한 평가 SQLite와 다릅니다")

    def _rate_recommendation(
        self, snapshot: Mapping[str, object]
    ) -> datetime | None:
        now = self.now().astimezone(timezone.utc)
        recent = sorted(
            timestamp
            for row in self._case_rows(snapshot).values()
            if (timestamp := _parse_timestamp(row.get("paid_boundary_at"))) is not None
            and now - timestamp < timedelta(seconds=RATE_WINDOW_SEC)
            and timestamp <= now + timedelta(seconds=10)
        )
        if len(recent) < RATE_LIMIT_COUNT:
            return None
        return recent[-RATE_LIMIT_COUNT] + timedelta(seconds=RATE_WINDOW_SEC)

    def _select_cases(
        self, requested: tuple[str, ...]
    ) -> tuple[CanonicalPilotCase, ...]:
        if not requested:
            return self.cases
        wanted = tuple(dict.fromkeys(requested))
        known = {case.case_id: case for case in self.cases}
        unknown = [case_id for case_id in wanted if case_id not in known]
        if unknown:
            raise PilotRunnerError("manifest에 없는 case ID입니다: " + ", ".join(unknown))
        return tuple(known[case_id] for case_id in wanted)

    @staticmethod
    def _case_rows(snapshot: Mapping[str, object]) -> dict[str, dict[str, object]]:
        rows = snapshot.get("cases")
        if not isinstance(rows, dict) or not all(
            isinstance(case_id, str) and isinstance(row, dict)
            for case_id, row in rows.items()
        ):
            raise CheckpointError("체크포인트 case mapping이 올바르지 않습니다")
        return rows

    def _summary(
        self,
        snapshot: Mapping[str, object],
        executed: Iterable[str],
        *,
        next_recommended_at: str = "",
        reason: str,
    ) -> PilotRunSummary:
        rows = self._case_rows(snapshot)
        completed = tuple(
            case_id for case_id, row in rows.items() if row.get("state") == "completed"
        )
        terminal = tuple(
            case_id
            for case_id, row in rows.items()
            if str(row.get("state", "")) in TERMINAL_STATES
        )
        return PilotRunSummary(
            executed_case_ids=tuple(executed),
            completed_case_ids=completed,
            terminal_case_ids=terminal,
            next_recommended_at=next_recommended_at,
            reason=reason,
        )

    def _now_iso(self) -> str:
        current = self.now()
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        return current.astimezone(timezone.utc).isoformat(timespec="seconds")
