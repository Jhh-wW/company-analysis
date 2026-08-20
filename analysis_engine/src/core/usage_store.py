"""여러 프로세스가 함께 써도 증가분을 잃지 않는 일일 JSON 계수기.

별도 ``.lock`` 파일을 운영체제 잠금으로 잡은 뒤 읽고 증가시킨다. 새 JSON을 임시
파일에 완전히 기록하고 ``os.replace``로 교체하므로 쓰는 중 프로세스가 종료돼도
기존 정상 파일은 남는다.
"""

from __future__ import annotations

import json
import os
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

PRIVATE_FILE_MODE = 0o600


class UsageStoreError(RuntimeError):
    """사용량 기록이 손상되었거나 안전하게 저장되지 못했다."""


class UsageLimitReached(UsageStoreError):
    """증가시키기 전 이미 설정된 한도에 도달했다."""


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    lock_path = path.with_name(path.name + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with lock_path.open("a+b") as lock_file:
        if os.name == "nt":
            import msvcrt  # Windows에서만 존재한다.  # noqa: PLC0415

            lock_file.seek(0, os.SEEK_END)
            if lock_file.tell() == 0:
                lock_file.write(b"\0")
                lock_file.flush()
            lock_file.seek(0)
            msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                lock_file.seek(0)
                msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl  # POSIX에서만 존재한다.  # noqa: PLC0415

            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def _load_unlocked(path: Path) -> dict[str, int]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise UsageStoreError("API 사용량 기록을 안전하게 읽을 수 없습니다.") from exc
    if not isinstance(raw, dict) or any(
        not isinstance(day, str)
        or not isinstance(count, int)
        or isinstance(count, bool)
        or count < 0
        for day, count in raw.items()
    ):
        raise UsageStoreError("API 사용량 기록 형식이 올바르지 않습니다.")
    return raw


def _write_atomic(path: Path, data: dict[str, int]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    descriptor: int | None = None
    try:
        descriptor = os.open(
            temp_path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            PRIVATE_FILE_MODE,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            descriptor = None
            json.dump(data, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    except OSError as exc:
        raise UsageStoreError("API 사용량 기록을 안전하게 저장하지 못했습니다.") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        temp_path.unlink(missing_ok=True)


def today_count(path: Path, day_key: str) -> int:
    """잠금 안에서 해당 날짜의 횟수를 읽는다."""
    with _exclusive_lock(path):
        return _load_unlocked(path).get(day_key, 0)


def tick(path: Path, day_key: str, limit: int) -> int:
    """잠금 안에서 한도 확인과 1 증가를 한 덩어리로 수행한다."""
    if limit <= 0:
        raise ValueError("API 사용량 한도는 1 이상이어야 합니다.")
    with _exclusive_lock(path):
        count = _load_unlocked(path).get(day_key, 0)
        if count >= limit:
            raise UsageLimitReached
        new_count = count + 1
        _write_atomic(path, {day_key: new_count})
        return new_count
