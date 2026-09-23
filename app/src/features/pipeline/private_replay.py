"""명시적으로 켠 로컬 평가에서만 요청·응답을 비공개 재현 파일로 보관한다."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
import uuid

from src.core import paths
from src.features.pipeline.private_replay_constants import (
    REPLAY_DEPLOYMENT_MARKERS, REPLAY_DIRECTORY, REPLAY_ENABLED_ENV,
    REPLAY_FILE_PATTERN, REPLAY_FINGERPRINT_VERSION, REPLAY_MAX_RECORD_BYTES,
    REPLAY_MAX_RECORDS, REPLAY_RETENTION_SECONDS, REPLAY_RUN_ENV, REPLAY_LOCK_FILENAME,
    REPLAY_RUN_PATTERN, REPLAY_SCHEMA_VERSION,
)
from src.shared.bounded_file_lock import BoundedFileLockError, try_exclusive_file_lock

_WRITE_LOCK = threading.Lock()


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def local_provider_replay_enabled() -> bool:
    """파일 접근 없이 명시적인 로컬 보관 설정만 확인한다."""
    if os.getenv(REPLAY_ENABLED_ENV) != "1" or any(os.getenv(key) for key in REPLAY_DEPLOYMENT_MARKERS):
        return False
    run_id = os.getenv(REPLAY_RUN_ENV, "")
    return re.fullmatch(REPLAY_RUN_PATTERN, run_id) is not None


def _directory() -> Path | None:
    if not local_provider_replay_enabled():
        return None
    run_id = os.getenv(REPLAY_RUN_ENV, "")
    if re.fullmatch(REPLAY_RUN_PATTERN, run_id) is None:
        return None
    app_root = paths.APP_ROOT.resolve()
    target = app_root.joinpath(*REPLAY_DIRECTORY, run_id)
    # 기존 링크·junction을 따라 저장소 밖에 쓰거나 다른 파일을 정리하지 않는다.
    for parent in (target, *target.parents):
        if parent == app_root:
            break
        if parent.is_symlink() or (hasattr(parent, "is_junction") and parent.is_junction()):
            return None
    resolved = target.resolve()
    if not resolved.is_relative_to(app_root.joinpath(*REPLAY_DIRECTORY)):
        return None
    return resolved


def record_local_provider_replay(
    *, prompt: str, response: str, stage: str, model: str,
    response_schema: object = None, output_limit: int, stop_reason: str = "",
    elapsed_ms: int = 0,
) -> bool:
    """보관 실패는 이미 완료된 호출의 응답·정산을 바꾸지 않는다.

    인증 헤더·환경·클라이언트 객체는 받지 않는다. 원문은 이 파일에만 두고
    일반 진단/로그에는 전달하지 않는다. 기본 설정에서는 디렉토리도 만들지 않는다.
    """
    try:
        directory = _directory()
        if directory is None:
            return False
        now = time.time()
        call_id = uuid.uuid4().hex
        schema_text = json.dumps(response_schema, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        record = {
            "schema_version": REPLAY_SCHEMA_VERSION,
            "fingerprint_version": REPLAY_FINGERPRINT_VERSION,
            "call_id": call_id, "recorded_at_unix": now,
            "stage": stage, "model": model, "output_limit": output_limit,
            "stop_reason": stop_reason, "elapsed_ms": elapsed_ms,
            "prompt_sha256": _sha256(prompt), "response_sha256": _sha256(response),
            "response_schema_sha256": _sha256(schema_text),
            "response_schema": response_schema, "prompt": prompt, "response": response,
        }
        payload = (json.dumps(record, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
        if len(payload) > REPLAY_MAX_RECORD_BYTES:
            return False
        if not _WRITE_LOCK.acquire(blocking=False):
            return False
        try:
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            private_root = directory.parent
            # 같은 저장소의 여러 평가 프로세스도 정리와 생성을 한 번에 수행한다.
            # 잠금 경쟁은 보관만 건너뛰며 공급자 응답을 기다리게 하지 않는다.
            with try_exclusive_file_lock(private_root / REPLAY_LOCK_FILENAME) as acquired:
                if not acquired:
                    return False
                retained = []
                for path in private_root.glob(f"*/{REPLAY_FILE_PATTERN}"):
                    if (path.is_symlink() or path.parent.is_symlink()
                            or (hasattr(path.parent, "is_junction") and path.parent.is_junction())
                            or not path.is_file() or not path.resolve().is_relative_to(private_root)
                            or re.fullmatch(REPLAY_RUN_PATTERN, path.parent.name) is None):
                        continue
                    modified = path.stat().st_mtime
                    if now - modified > REPLAY_RETENTION_SECONDS:
                        path.unlink()
                    else:
                        retained.append((modified, path))
                for _, path in sorted(retained)[:max(0, len(retained) - REPLAY_MAX_RECORDS + 1)]:
                    path.unlink()
                target = directory / f"call-{call_id}.json"
                descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "wb") as output:
                    output.write(payload)
        finally:
            _WRITE_LOCK.release()
        return True
    except (OSError, ValueError, TypeError, BoundedFileLockError):
        return False


def read_local_provider_replay(path: Path) -> dict:
    """바이트 지문을 재검산한 로컬 보관본만 재현 도구에 돌려준다."""
    if path.stat().st_size > REPLAY_MAX_RECORD_BYTES:
        raise ValueError("재현 파일이 보관 상한을 넘습니다")
    record = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(record, dict) or record.get("schema_version") != REPLAY_SCHEMA_VERSION:
        raise ValueError("재현 파일 계약 버전이 다릅니다")
    if record.get("fingerprint_version") != REPLAY_FINGERPRINT_VERSION:
        raise ValueError("재현 지문 방식이 다릅니다")
    for key in ("prompt", "response"):
        if not isinstance(record.get(key), str) or _sha256(record[key]) != record.get(f"{key}_sha256"):
            raise ValueError("재현 원문의 지문이 일치하지 않습니다")
    schema_text = json.dumps(record.get("response_schema"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if _sha256(schema_text) != record.get("response_schema_sha256"):
        raise ValueError("재현 응답 스키마의 지문이 일치하지 않습니다")
    return record
