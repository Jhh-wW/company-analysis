"""로컬과 Render 영속 경로를 한곳에서 결정하는지 시험."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from core import runtime_paths

RUN_PILOT_PATH = runtime_paths.PROTOTYPE_ROOT / "tools" / "run_pilot.py"


def test_환경변수가_없으면_예전_로컬_경로를_그대로_쓴다(monkeypatch):
    monkeypatch.delenv(runtime_paths.ENV_DATA_ROOT, raising=False)

    assert runtime_paths.runtime_data_dir() == runtime_paths.LOCAL_DATA_DIR
    assert runtime_paths.runtime_log_dir() == runtime_paths.LOCAL_LOG_DIR


def test_Render에서는_영속디스크_아래로_모은다(tmp_path, monkeypatch):
    monkeypatch.setenv(runtime_paths.ENV_DATA_ROOT, str(tmp_path))

    assert runtime_paths.runtime_data_dir() == tmp_path / "prototype_v1"
    assert runtime_paths.runtime_log_dir() == tmp_path / "logs"


def test_run_pilot은_산출물과_읽기전용_입력을_나눈다():
    tree = ast.parse(RUN_PILOT_PATH.read_text(encoding="utf-8"))
    assignments = {
        target.id: node.value
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }

    data_value = assignments["DATA"]
    assert isinstance(data_value, ast.Call)
    assert isinstance(data_value.func, ast.Name)
    assert data_value.func.id == "runtime_data_dir"

    for fixture_name in ("RECOLLECT_DIR", "OCR_TEXT_DIR"):
        assert "LOCAL_DATA_DIR" in ast.unparse(assignments[fixture_name])
