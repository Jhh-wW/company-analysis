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
    REPLAY_PARSED_SCHEMA_VERSION, REPLAY_PARSED_CAPTURE_KIND, REPLAY_PARSED_CAPTURE_SCHEMA,
    REPLAY_THREAD_LOCK_TIMEOUT_SECONDS, NEWS_OBSERVATION_SCHEMA,
    NEWS_OBSERVATION_CAPTURE_KIND, NEWS_OBSERVATION_FILE_PATTERN, NEWS_OBSERVATION_EVENTS,
    REPLAY_FAILURE_SCHEMA_VERSION, REPLAY_FAILURE_CAPTURE_KIND,
    REPLAY_FAILURE_REQUEST_FINGERPRINT_SCOPE,
    REPLAY_FAILURE_FILE_PATTERN, REPLAY_FAILURE_MAX_RECORDS,
    REPLAY_FAILURE_ERROR_TYPE_MAX_CHARS, REPLAY_FAILURE_ERROR_MESSAGE_MAX_CHARS,
    REPLAY_FAILURE_ERROR_STATUS_MAX_CHARS,
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
    capture_kind: str = "",
) -> bool:
    """보관 실패는 이미 완료된 호출의 응답·정산을 바꾸지 않는다.

    인증 헤더·환경·클라이언트 객체는 받지 않는다. 원문은 이 파일에만 두고
    일반 진단/로그에는 전달하지 않는다. 기본 설정에서는 디렉토리도 만들지 않는다.
    """
    try:
        directory = _directory()
        if directory is None:
            return False
        if capture_kind not in {"", REPLAY_PARSED_CAPTURE_KIND}:
            return False
        if capture_kind and json.dumps(json.loads(response), ensure_ascii=False, sort_keys=True,
                                       separators=(",", ":"), allow_nan=False) != response:
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
        if capture_kind:
            record.update(schema_version=REPLAY_PARSED_SCHEMA_VERSION, capture_kind=capture_kind,
                          capture_schema=REPLAY_PARSED_CAPTURE_SCHEMA)
        return _store_local_record(record, prefix="call")
    except (OSError, ValueError, TypeError, BoundedFileLockError):
        return False


def _store_local_record(record: dict, *, prefix: str) -> bool:
    """보관만 짧게 기다리며 응답·원장 계약과 별도로 실패를 반환한다."""
    try:
        patterns = {
            "call": REPLAY_FILE_PATTERN,
            "news-observation": NEWS_OBSERVATION_FILE_PATTERN,
            "failure": REPLAY_FAILURE_FILE_PATTERN,
        }
        if prefix not in patterns:
            return False
        directory = _directory()
        if directory is None:
            return False
        now = record["recorded_at_unix"]
        record_id = record["call_id"]
        payload = (json.dumps(record, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")
        if len(payload) > REPLAY_MAX_RECORD_BYTES:
            return False
        if not _WRITE_LOCK.acquire(timeout=REPLAY_THREAD_LOCK_TIMEOUT_SECONDS):
            return False
        try:
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            private_root = directory.parent
            # 같은 저장소의 여러 평가 프로세스도 정리와 생성을 한 번에 수행한다.
            # 외부 파일 잠금 경쟁은 보관만 건너뛰며 응답·정산을 바꾸지 않는다.
            with try_exclusive_file_lock(private_root / REPLAY_LOCK_FILENAME) as acquired:
                if not acquired:
                    return False
                retained = []
                # 각 표현의 파일만 센다. 실패 요청은 보존본을 삭제하지 않고 상한에서 거절한다.
                pattern = patterns[prefix]
                for path in private_root.glob(f"*/{pattern}"):
                    if (path.is_symlink() or path.parent.is_symlink()
                            or (hasattr(path.parent, "is_junction") and path.parent.is_junction())
                            or not path.is_file() or not path.resolve().is_relative_to(private_root)
                            or re.fullmatch(REPLAY_RUN_PATTERN, path.parent.name) is None):
                        continue
                    modified = path.stat().st_mtime
                    if prefix != "failure" and now - modified > REPLAY_RETENTION_SECONDS:
                        path.unlink()
                    else:
                        retained.append((modified, path))
                if prefix == "failure":
                    if len(retained) >= REPLAY_FAILURE_MAX_RECORDS:
                        return False
                else:
                    for _, path in sorted(retained)[:max(0, len(retained) - REPLAY_MAX_RECORDS + 1)]:
                        path.unlink()
                target = directory / f"{prefix}-{record_id}.json"
                descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "wb") as output:
                    output.write(payload)
        finally:
            _WRITE_LOCK.release()
        return True
    except (OSError, ValueError, TypeError, BoundedFileLockError):
        return False


def _bounded_failure_string(value: object, limit: int) -> str:
    return value[:limit] if type(value) is str else ""


def _failure_request_sha256(record: dict) -> str:
    """HTTP 전체 원바이트가 아니라 실제 전송 네 필드의 정규 JSON 지문을 만든다."""
    request = {key: record[key] for key in ("prompt", "response_schema", "model", "output_limit")}
    return _sha256(json.dumps(request, ensure_ascii=False, sort_keys=True,
                              separators=(",", ":"), allow_nan=False))


def record_local_provider_failure(
    *, prompt: str, response_schema: dict, model: str, output_limit: int,
    stage: str, error_type: object = "", error_message: object = "",
    status_code: object = None, elapsed_ms: int = 0,
    provider_request_id_sha256: str | None = None,
) -> bool:
    """실제 전송 네 필드 사본과 SDK 오류를 보관한다. HTTP 전체 원바이트 보관은 아니다.

    요청 지문은 prompt/schema/model/output_limit의 정규 JSON을 결속한다.
    응답·예외 객체·인증 정보는 받지 않는다.
    """
    try:
        if _directory() is None:
            return False
        if (type(prompt) is not str or type(model) is not str or type(stage) is not str
                or not isinstance(response_schema, dict) or type(output_limit) is not int
                or output_limit <= 0 or type(elapsed_ms) is not int or elapsed_ms < 0):
            return False
        if provider_request_id_sha256 is not None and (
                type(provider_request_id_sha256) is not str
                or re.fullmatch(r"[0-9a-f]{64}", provider_request_id_sha256) is None):
            return False
        schema_text = json.dumps(response_schema, ensure_ascii=False, sort_keys=True,
                                 separators=(",", ":"), allow_nan=False)
        record = {
            "schema_version": REPLAY_FAILURE_SCHEMA_VERSION,
            "fingerprint_version": REPLAY_FINGERPRINT_VERSION,
            "capture_kind": REPLAY_FAILURE_CAPTURE_KIND,
            "request_fingerprint_scope": REPLAY_FAILURE_REQUEST_FINGERPRINT_SCOPE,
            "provider_request_id_sha256": provider_request_id_sha256,
            "response_available": False,
            "call_id": uuid.uuid4().hex, "recorded_at_unix": time.time(),
            "stage": stage, "model": model, "output_limit": output_limit,
            "elapsed_ms": elapsed_ms, "prompt": prompt,
            "prompt_sha256": _sha256(prompt), "response_schema": response_schema,
            "response_schema_sha256": _sha256(schema_text),
            "sdk_error": {
                "type": _bounded_failure_string(error_type, REPLAY_FAILURE_ERROR_TYPE_MAX_CHARS),
                "message": _bounded_failure_string(error_message, REPLAY_FAILURE_ERROR_MESSAGE_MAX_CHARS),
                "status": str(status_code) if type(status_code) is int and 100 <= status_code <= 599 else "",
                "type_original_chars": len(error_type) if type(error_type) is str else 0,
                "message_original_chars": len(error_message) if type(error_message) is str else 0,
                "type_truncated": type(error_type) is str and len(error_type) > REPLAY_FAILURE_ERROR_TYPE_MAX_CHARS,
                "message_truncated": type(error_message) is str and len(error_message) > REPLAY_FAILURE_ERROR_MESSAGE_MAX_CHARS,
            },
        }
        record["request_sha256"] = _failure_request_sha256(record)
        canonical = json.dumps(record, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":"), allow_nan=False)
        record["record_sha256"] = _sha256(canonical)
        return _store_local_record(record, prefix="failure")
    except (OSError, ValueError, TypeError, BoundedFileLockError):
        return False


def read_local_provider_failure(path: Path) -> dict:
    """응답이 없는 실패 기록의 요청·전체 지문을 검산한다. 성공 재현으로 읽지 않는다."""
    if path.stat().st_size > REPLAY_MAX_RECORD_BYTES:
        raise ValueError("실패 요청 파일이 보관 상한을 넘습니다")
    record = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(record, dict)
            or set(record) != {"schema_version", "fingerprint_version", "capture_kind",
                               "response_available", "call_id", "recorded_at_unix", "stage",
                               "model", "output_limit", "elapsed_ms", "prompt", "prompt_sha256",
                               "response_schema", "response_schema_sha256", "sdk_error",
                               "request_sha256", "record_sha256", "provider_request_id_sha256",
                               "request_fingerprint_scope"}
            or record.get("schema_version") != REPLAY_FAILURE_SCHEMA_VERSION
            or record.get("fingerprint_version") != REPLAY_FINGERPRINT_VERSION
            or record.get("capture_kind") != REPLAY_FAILURE_CAPTURE_KIND
            or record.get("request_fingerprint_scope") != REPLAY_FAILURE_REQUEST_FINGERPRINT_SCOPE
            or record.get("response_available") is not False
            or "response" in record or "response_sha256" in record
            or type(record.get("prompt")) is not str or type(record.get("model")) is not str
            or type(record.get("stage")) is not str
            or not isinstance(record.get("response_schema"), dict)
            or type(record.get("output_limit")) is not int or record["output_limit"] <= 0
            or type(record.get("elapsed_ms")) is not int or record["elapsed_ms"] < 0):
        raise ValueError("실패 요청 보관 계약이 맞지 않습니다")
    request_id_digest = record.get("provider_request_id_sha256")
    if request_id_digest is not None and (type(request_id_digest) is not str
                                         or re.fullmatch(r"[0-9a-f]{64}", request_id_digest) is None):
        raise ValueError("실패 요청의 공급자 요청 지문이 맞지 않습니다")
    error = record.get("sdk_error")
    if (not isinstance(error, dict) or set(error) != {
            "type", "message", "status", "type_original_chars", "message_original_chars",
            "type_truncated", "message_truncated",
        }
            or any(type(error[key]) is not str or len(error[key]) > limit for key, limit in (
                ("type", REPLAY_FAILURE_ERROR_TYPE_MAX_CHARS),
                ("message", REPLAY_FAILURE_ERROR_MESSAGE_MAX_CHARS),
                ("status", REPLAY_FAILURE_ERROR_STATUS_MAX_CHARS),
            )) or (error["status"] and re.fullmatch(r"[1-5][0-9]{2}", error["status"]) is None)):
        raise ValueError("실패 요청의 SDK 오류 필드가 맞지 않습니다")
    for key, limit in (("type", REPLAY_FAILURE_ERROR_TYPE_MAX_CHARS),
                       ("message", REPLAY_FAILURE_ERROR_MESSAGE_MAX_CHARS)):
        original_chars, truncated = error[f"{key}_original_chars"], error[f"{key}_truncated"]
        if (type(original_chars) is not int or original_chars < 0 or type(truncated) is not bool
                or len(error[key]) != min(original_chars, limit) or truncated != (original_chars > limit)):
            raise ValueError("실패 요청의 SDK 오류 발췌 표시가 맞지 않습니다")
    digest = record.pop("record_sha256", None)
    canonical = json.dumps(record, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"), allow_nan=False)
    schema_text = json.dumps(record["response_schema"], ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False)
    if (digest != _sha256(canonical) or record.get("prompt_sha256") != _sha256(record["prompt"])
            or record.get("response_schema_sha256") != _sha256(schema_text)
            or record.get("request_sha256") != _failure_request_sha256(record)):
        raise ValueError("실패 요청의 지문이 일치하지 않습니다")
    record["record_sha256"] = digest
    return record


def record_local_news_observation(*, event: str, payload: dict) -> bool:
    """로컬 평가의 검색 해석 행·선택 지문을 공급자 원응답과 구분해 보관한다."""
    if not local_provider_replay_enabled() or event not in NEWS_OBSERVATION_EVENTS:
        return False
    try:
        record = {
            "schema_version": NEWS_OBSERVATION_SCHEMA,
            "capture_kind": NEWS_OBSERVATION_CAPTURE_KIND,
            "call_id": uuid.uuid4().hex, "recorded_at_unix": time.time(),
            "event": event, "payload": payload,
        }
        canonical = json.dumps(record, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":"), allow_nan=False)
        record["record_sha256"] = _sha256(canonical)
        return _store_local_record(record, prefix="news-observation")
    except (OSError, ValueError, TypeError):
        return False


def read_local_news_observation(path: Path) -> dict:
    """별도 스키마와 전체 기록 지문이 맞는 뉴스 관측만 반환한다."""
    if path.stat().st_size > REPLAY_MAX_RECORD_BYTES:
        raise ValueError("뉴스 관측 파일이 보관 상한을 넘습니다")
    record = json.loads(path.read_text(encoding="utf-8"))
    if (not isinstance(record, dict) or record.get("schema_version") != NEWS_OBSERVATION_SCHEMA
            or record.get("capture_kind") != NEWS_OBSERVATION_CAPTURE_KIND
            or record.get("event") not in NEWS_OBSERVATION_EVENTS
            or not isinstance(record.get("payload"), dict)):
        raise ValueError("뉴스 관측 보관 계약이 맞지 않습니다")
    digest = record.pop("record_sha256", None)
    canonical = json.dumps(record, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"), allow_nan=False)
    if digest != _sha256(canonical):
        raise ValueError("뉴스 관측 기록 지문이 맞지 않습니다")
    record["record_sha256"] = digest
    return record


def read_local_provider_replay(path: Path) -> dict:
    """바이트 지문을 재검산한 로컬 보관본만 재현 도구에 돌려준다."""
    if path.stat().st_size > REPLAY_MAX_RECORD_BYTES:
        raise ValueError("재현 파일이 보관 상한을 넘습니다")
    record = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(record, dict) or record.get("schema_version") not in {
        REPLAY_SCHEMA_VERSION, REPLAY_PARSED_SCHEMA_VERSION,
    }:
        raise ValueError("재현 파일 계약 버전이 다릅니다")
    if record["schema_version"] == REPLAY_PARSED_SCHEMA_VERSION:
        if (record.get("capture_kind") != REPLAY_PARSED_CAPTURE_KIND
                or record.get("capture_schema") != REPLAY_PARSED_CAPTURE_SCHEMA):
            raise ValueError("재현 파일의 분석 응답 표현이 확인되지 않습니다")
    elif "capture_kind" in record or "capture_schema" in record:
        raise ValueError("기존 원응답 계약에 다른 분석 응답 표현이 섞였습니다")
    if record.get("fingerprint_version") != REPLAY_FINGERPRINT_VERSION:
        raise ValueError("재현 지문 방식이 다릅니다")
    for key in ("prompt", "response"):
        if not isinstance(record.get(key), str) or _sha256(record[key]) != record.get(f"{key}_sha256"):
            raise ValueError("재현 원문의 지문이 일치하지 않습니다")
    if record["schema_version"] == REPLAY_PARSED_SCHEMA_VERSION:
        if json.dumps(json.loads(record["response"]), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False) != record["response"]:
            raise ValueError("재현 분석 응답이 정해진 JSON 표현과 다릅니다")
    schema_text = json.dumps(record.get("response_schema"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if _sha256(schema_text) != record.get("response_schema_sha256"):
        raise ValueError("재현 응답 스키마의 지문이 일치하지 않습니다")
    return record
