"""여러 feature가 같은 파일 경계를 쓸 때 쓰는 bounded 배타 잠금."""

from __future__ import annotations

import errno
import os
import stat
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Final, Iterator


DEFAULT_POLL_SECONDS: Final[float] = 0.05
_WINDOWS_BUSY_ERRORS: Final[frozenset[int]] = frozenset(
    {
        errno.EACCES,
        errno.EAGAIN,
        getattr(errno, "EDEADLK", 36),
    }
)
_WINDOWS_BUSY_WINERRORS: Final[frozenset[int]] = frozenset({33, 36, 158})


class BoundedFileLockError(RuntimeError):
    """잠금 파일 자체가 안전하지 않거나 OS 잠금 작업이 실패했다."""


class BoundedFileLockTimeout(BoundedFileLockError):
    """살아 있지만 멈춘 다른 process 때문에 유한 시간 안에 잠그지 못했다."""


def _is_linklike(path: Path, status: os.stat_result) -> bool:
    if stat.S_ISLNK(status.st_mode):
        return True
    reparse_flag = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
    attributes = getattr(status, "st_file_attributes", 0)
    return bool(reparse_flag and attributes & reparse_flag)


def _open_plain_lock_file(path: Path):
    before: os.stat_result | None
    try:
        before = path.lstat()
    except FileNotFoundError:
        before = None
    if before is not None and (
        _is_linklike(path, before)
        or not stat.S_ISREG(before.st_mode)
        or before.st_nlink != 1
    ):
        raise BoundedFileLockError("잠금 경로가 symlink 없는 일반 파일이 아닙니다")

    flags = (
        os.O_RDWR
        | os.O_CREAT
        | getattr(os, "O_BINARY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise BoundedFileLockError("잠금 파일을 안전하게 열 수 없습니다") from exc
    stream = os.fdopen(descriptor, "r+b", buffering=0)
    opened = os.fstat(stream.fileno())
    try:
        path_after = path.lstat()
    except OSError as exc:
        stream.close()
        raise BoundedFileLockError("잠금 파일을 연 직후 다시 확인하지 못했습니다") from exc
    if (
        _is_linklike(path, path_after)
        or not stat.S_ISREG(opened.st_mode)
        or not stat.S_ISREG(path_after.st_mode)
        or opened.st_nlink != 1
        or path_after.st_nlink != 1
        or (opened.st_dev, opened.st_ino) != (path_after.st_dev, path_after.st_ino)
        or (
            before is not None
            and (before.st_dev, before.st_ino)
            != (path_after.st_dev, path_after.st_ino)
        )
    ):
        stream.close()
        raise BoundedFileLockError("잠금 파일이 여는 동안 바뀌었습니다")
    return stream


def _ensure_lock_byte(stream) -> None:
    stream.seek(0, os.SEEK_END)
    if stream.tell() == 0:
        stream.write(b"\0")
        stream.flush()
        os.fsync(stream.fileno())
    stream.seek(0)


def _try_lock(stream) -> bool:
    stream.seek(0)
    if os.name == "nt":
        import msvcrt  # noqa: PLC0415

        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError as exc:
            if exc.errno in _WINDOWS_BUSY_ERRORS or getattr(
                exc, "winerror", None
            ) in _WINDOWS_BUSY_WINERRORS:
                return False
            raise BoundedFileLockError("Windows 파일 잠금에 실패했습니다") from exc
    else:  # pragma: no cover - Render/Linux 경계
        import fcntl  # noqa: PLC0415

        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            return False
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                return False
            raise BoundedFileLockError("POSIX 파일 잠금에 실패했습니다") from exc


def _unlock(stream) -> None:
    stream.seek(0)
    if os.name == "nt":
        import msvcrt  # noqa: PLC0415

        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
    else:  # pragma: no cover - Render/Linux 경계
        import fcntl  # noqa: PLC0415

        fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


@contextmanager
def exclusive_file_lock(
    path: Path,
    *,
    timeout_seconds: float,
    poll_seconds: float = DEFAULT_POLL_SECONDS,
) -> Iterator[None]:
    """정확한 한 파일의 byte lock을 유한 시간 안에 얻거나 실패한다."""

    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, (int, float))
        or timeout_seconds <= 0
    ):
        raise ValueError("파일 잠금 제한 시간은 0보다 커야 합니다")
    if (
        isinstance(poll_seconds, bool)
        or not isinstance(poll_seconds, (int, float))
        or poll_seconds <= 0
    ):
        raise ValueError("파일 잠금 확인 간격은 0보다 커야 합니다")
    lock_path = Path(path)
    stream = _open_plain_lock_file(lock_path)
    acquired = False
    try:
        _ensure_lock_byte(stream)
        deadline = time.monotonic() + float(timeout_seconds)
        while not (acquired := _try_lock(stream)):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise BoundedFileLockTimeout(
                    "다른 process가 파일 잠금을 제한 시간 안에 놓지 않았습니다"
                )
            time.sleep(min(float(poll_seconds), remaining))
        yield
    finally:
        if acquired:
            try:
                _unlock(stream)
            except OSError as exc:
                raise BoundedFileLockError("파일 잠금을 안전하게 해제하지 못했습니다") from exc
        stream.close()


__all__ = [
    "BoundedFileLockError",
    "BoundedFileLockTimeout",
    "exclusive_file_lock",
]
