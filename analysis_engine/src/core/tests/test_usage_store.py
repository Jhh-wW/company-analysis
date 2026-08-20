"""API 일일 계수기의 동시성·손상 방지 시험."""

from __future__ import annotations

import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core import usage_store

SRC_DIR = Path(__file__).resolve().parents[2]


def test_동시_요청도_증가분을_하나도_잃지_않는다(tmp_path: Path) -> None:
    path = tmp_path / "usage.json"
    day = "2026-08-17"

    with ThreadPoolExecutor(max_workers=8) as pool:
        counts = list(pool.map(lambda _: usage_store.tick(path, day, 100), range(40)))

    assert sorted(counts) == list(range(1, 41))
    assert usage_store.today_count(path, day) == 40


def test_여러_프로세스도_증가분을_하나도_잃지_않는다(tmp_path: Path) -> None:
    path = tmp_path / "usage.json"
    code = (
        "import sys; from pathlib import Path; from core import usage_store; "
        "[usage_store.tick(Path(sys.argv[1]), '2026-08-17', 100) for _ in range(10)]"
    )
    child_env = dict(os.environ)
    child_env["PYTHONPATH"] = str(SRC_DIR)

    def run_child(_: int) -> None:
        subprocess.run(
            [sys.executable, "-c", code, str(path)],
            check=True,
            capture_output=True,
            env=child_env,
            cwd=SRC_DIR,
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(run_child, range(4)))

    assert usage_store.today_count(path, "2026-08-17") == 40


def test_손상된_JSON은_0으로_초기화하지_않고_막는다(tmp_path: Path) -> None:
    path = tmp_path / "usage.json"
    path.write_text("{깨진 파일", encoding="utf-8")

    with pytest.raises(usage_store.UsageStoreError, match="안전하게 읽을 수 없습니다"):
        usage_store.tick(path, "2026-08-17", 100)

    assert path.read_text(encoding="utf-8") == "{깨진 파일"
