"""Durable, fail-closed checkpoint for sequential paid pilot execution."""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Final, Iterator, Mapping

from src.features.pilot_evaluation.manifest import CanonicalPilotCase


SCHEMA_VERSION: Final[int] = 2
PENDING_STATE: Final[str] = "pending"
RESUMABLE_STATE: Final[str] = "running"
PRIOR_DAY_BILLING_UNCERTAIN_STATE: Final[str] = (
    "billing_uncertain_previous_day"
)
PRIOR_DAY_BILLING_UNCERTAIN_ERROR: Final[str] = (
    "prior_day_unknown_charge_preserved"
)
TERMINAL_STATES: Final[frozenset[str]] = frozenset(
    {
        "completed",
        "identity_mismatch",
        "identity_ref_unverified",
        "stopped_before_run",
        PRIOR_DAY_BILLING_UNCERTAIN_STATE,
    }
)
UNRESOLVED_STATES: Final[frozenset[str]] = frozenset(
    {
        "identity_started",
        "identified",
        "run_submission_started",
        "billing_uncertain",
    }
)


class CheckpointError(RuntimeError):
    """The checkpoint cannot safely be created, loaded, or resumed."""


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class CheckpointStore:
    def __init__(self, path: Path, *, events_path: Path | None = None) -> None:
        self.path = path.resolve()
        self.events_path = (
            events_path.resolve()
            if events_path is not None
            else self.path.with_suffix(self.path.suffix + ".jsonl")
        )
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self._lock_fd: int | None = None

    @contextmanager
    def exclusive(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._lock_fd = os.open(
                self.lock_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
        except FileExistsError as exc:
            raise CheckpointError(
                "다른 파일럿 실행기 또는 확인이 필요한 이전 실행 lock이 있습니다"
            ) from exc
        try:
            os.write(self._lock_fd, str(os.getpid()).encode("ascii"))
            os.fsync(self._lock_fd)
            yield
        finally:
            if self._lock_fd is not None:
                os.close(self._lock_fd)
                self._lock_fd = None
            try:
                self.lock_path.unlink()
            except FileNotFoundError:
                pass

    def load_or_create(
        self,
        *,
        cases: tuple[CanonicalPilotCase, ...],
        binding_id: str,
        manifest_digest: str,
        origin: str,
        server_instance_digest: str,
        data_path_digest: str,
        now: str | None = None,
    ) -> dict[str, object]:
        timestamp = now or utc_now_iso()
        if (
            len(binding_id) != 32
            or any(character not in "0123456789abcdef" for character in binding_id)
        ):
            raise CheckpointError("체크포인트 저장소 binding ID가 올바르지 않습니다")
        if self.path.exists():
            snapshot = self._load()
            expected = {
                "binding_id": binding_id,
                "manifest_sha256": manifest_digest,
                "origin": origin,
                "server_instance_sha256": server_instance_digest,
                "data_path_sha256": data_path_digest,
            }
            mismatches = [
                key for key, value in expected.items() if snapshot.get(key) != value
            ]
            if mismatches:
                raise CheckpointError(
                    "체크포인트와 현재 manifest·서버·데이터가 달라 재개를 차단했습니다: "
                    + ", ".join(mismatches)
                )
            stored_cases = snapshot.get("cases")
            if not isinstance(stored_cases, dict) or tuple(stored_cases) != tuple(
                case.case_id for case in cases
            ):
                raise CheckpointError("체크포인트의 25개 case 구성이 manifest와 다릅니다")
            return snapshot

        snapshot = {
            "schema_version": SCHEMA_VERSION,
            "binding_id": binding_id,
            "manifest_sha256": manifest_digest,
            "origin": origin,
            "server_instance_sha256": server_instance_digest,
            "data_path_sha256": data_path_digest,
            "created_at": timestamp,
            "updated_at": timestamp,
            "cases": {
                case.case_id: {
                    "case_id": case.case_id,
                    "state": PENDING_STATE,
                    "run_id": "",
                    "report_id": "",
                    "outcome": "",
                    "internal_ai_cost_krw": None,
                    "billing_uncertain": False,
                    "selected_corp_code": "",
                    "legal_name": "",
                    "paid_boundary_at": "",
                    "result_http_status": None,
                    "error_code": "",
                    "updated_at": timestamp,
                }
                for case in cases
            },
        }
        self._write(snapshot)
        self._append_event("checkpoint_created", "", PENDING_STATE, timestamp)
        return snapshot

    def update_case(
        self,
        snapshot: dict[str, object],
        case_id: str,
        *,
        state: str,
        now: str | None = None,
        **changes: object,
    ) -> Mapping[str, object]:
        cases = snapshot.get("cases")
        if not isinstance(cases, dict) or case_id not in cases:
            raise CheckpointError("체크포인트에 없는 case ID입니다")
        row = cases[case_id]
        if not isinstance(row, dict):
            raise CheckpointError("체크포인트 case 행 모양이 올바르지 않습니다")
        allowed = {
            "run_id",
            "report_id",
            "outcome",
            "internal_ai_cost_krw",
            "billing_uncertain",
            "selected_corp_code",
            "legal_name",
            "paid_boundary_at",
            "result_http_status",
            "error_code",
        }
        unexpected = set(changes) - allowed
        if unexpected:
            raise CheckpointError(
                "체크포인트에 허용되지 않은 필드를 쓰려 했습니다: "
                + ", ".join(sorted(unexpected))
            )
        timestamp = now or utc_now_iso()
        row.update(changes)
        row["state"] = state
        row["updated_at"] = timestamp
        snapshot["updated_at"] = timestamp
        self._write(snapshot)
        self._append_event(
            "case_state",
            case_id,
            state,
            timestamp,
            run_id=str(row.get("run_id", "")),
            outcome=str(row.get("outcome", "")),
            cost_krw=row.get("internal_ai_cost_krw"),
            billing_uncertain=bool(row.get("billing_uncertain", False)),
            error_code=str(row.get("error_code", "")),
        )
        return row

    def _load(self) -> dict[str, object]:
        try:
            raw = self.path.read_text(encoding="utf-8")
            snapshot = json.loads(raw)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise CheckpointError("체크포인트를 안전하게 읽지 못했습니다") from exc
        if not isinstance(snapshot, dict) or snapshot.get("schema_version") != SCHEMA_VERSION:
            raise CheckpointError("지원하지 않는 체크포인트 형식입니다")
        return snapshot

    def _write(self, snapshot: Mapping[str, object]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent
        )
        temporary = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(snapshot, handle, ensure_ascii=False, sort_keys=True, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
        except BaseException:
            try:
                temporary.unlink()
            except FileNotFoundError:
                pass
            raise

    def _append_event(
        self,
        event: str,
        case_id: str,
        state: str,
        at: str,
        **fields: object,
    ) -> None:
        record = {
            "event": event,
            "case_id": case_id,
            "state": state,
            "at": at,
            **fields,
        }
        self.events_path.parent.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(
                json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                + "\n"
            )
            handle.flush()
            os.fsync(handle.fileno())
