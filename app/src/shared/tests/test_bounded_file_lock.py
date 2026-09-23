"""비차단 OS 잠금은 파일을 유지하고 프로세스 종료 시 자동 해제된다."""

import multiprocessing
from pathlib import Path

import pytest

from src.shared import bounded_file_lock as locks


def _hold_lock(path, ready, release):
    with locks.try_exclusive_file_lock(Path(path)) as acquired:
        if not acquired:
            raise RuntimeError("시험 잠금을 획득하지 못했습니다")
        ready.set()
        release.wait(timeout=15)


def test_비차단_잠금은_기다리지_않고_기존_잠금과_호환된다(tmp_path, monkeypatch):
    path = tmp_path / ".root.lock"

    def unexpected_sleep(_seconds):
        raise AssertionError("비차단 잠금은 기다리면 안 됩니다")

    monkeypatch.setattr(locks.time, "sleep", unexpected_sleep)
    with locks.exclusive_file_lock(path, timeout_seconds=1):
        with locks.try_exclusive_file_lock(path) as acquired:
            assert not acquired
    with locks.try_exclusive_file_lock(path) as acquired:
        assert acquired
    assert path.is_file() and path.read_bytes() == b"\0"


def test_프로세스_강제종료_후_남은_파일로_다시_잠글_수_있다(tmp_path):
    path = tmp_path / ".root.lock"
    context = multiprocessing.get_context("spawn")
    ready, release = context.Event(), context.Event()
    holder = context.Process(target=_hold_lock, args=(str(path), ready, release))
    holder.start()
    try:
        assert ready.wait(timeout=10)
        with locks.try_exclusive_file_lock(path) as acquired:
            assert not acquired
        holder.terminate()
        holder.join(timeout=5)
        assert not holder.is_alive()
        assert path.is_file()
        with locks.try_exclusive_file_lock(path) as acquired:
            assert acquired
    finally:
        # 강제 종료된 자식의 Event 내부 잠금에는 다시 접근하지 않는다.
        if holder.is_alive():
            holder.terminate()
        holder.join(timeout=5)


@pytest.mark.parametrize("unsafe_kind", ("directory", "hardlink", "symlink"))
def test_비차단_잠금도_일반파일_경계를_우회하지_않는다(tmp_path, unsafe_kind):
    path = tmp_path / ".root.lock"
    if unsafe_kind == "directory":
        path.mkdir()
    else:
        original = tmp_path / "other-file"
        original.write_bytes(b"\0")
        if unsafe_kind == "hardlink":
            path.hardlink_to(original)
        else:
            try:
                path.symlink_to(original)
            except OSError:
                pytest.skip("이 환경에서는 심볼릭 링크 생성 권한이 없습니다")
    with pytest.raises(locks.BoundedFileLockError):
        with locks.try_exclusive_file_lock(path):
            pytest.fail("안전하지 않은 잠금 경로를 열었습니다")
