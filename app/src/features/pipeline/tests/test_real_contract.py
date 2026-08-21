"""진짜 알맹이(`real.py`)가 1판 엔진을 «있는 이름으로» 부르는지 검사한다.

★ 엔진을 실행하지 않는다. 코드를 글자로 읽어서(구문 분석) 대조만 한다.
  실행하면 AI 호출로 돈이 나가고, 열쇠(.env)도 필요하다.

이 시험이 잡는 것 — **엔진 쪽 함수 이름이 바뀌면 즉시 빨간불.**
없으면 진짜로 돌리는 날 처음 알게 되고, 그때는 이미 돈이 나간 뒤다.
"""

from __future__ import annotations

import ast
import inspect
import subprocess
import sys

import pytest

from src.core import paths
from src.features.pipeline import real
from src.features.pipeline.demo import DemoPipeline
from src.features.pipeline.port import Pipeline

ENGINE_PATH = paths.PROJECT_ROOT / "analysis_engine" / "tools" / "run_pilot.py"
REAL_PATH = paths.APP_ROOT / "src" / "features" / "pipeline" / "real.py"


def _tree(path):
    return ast.parse(path.read_text(encoding="utf-8"))


def engine_names() -> set[str]:
    """엔진 파일이 밖에 내주는 이름 전부 (함수·상수·불러온 것)."""
    names: set[str] = set()
    for node in _tree(ENGINE_PATH).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            names.update(a.asname or a.name.split(".")[0] for a in node.names)
    return names


def used_names() -> set[str]:
    """`real.py`가 `engine.무엇` 꼴로 부르는 이름 전부."""
    used: set[str] = set()
    for node in ast.walk(_tree(REAL_PATH)):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name)
            and node.value.id == "engine"
        ):
            used.add(node.attr)
    return used


# ── 엔진 파일이 있는가 ──────────────────────────────────

def test_엔진_파일이_제자리에_있다():
    assert ENGINE_PATH.exists(), f"1판 엔진을 찾지 못했습니다: {ENGINE_PATH}"


def test_부르는_이름이_하나도_빠짐없이_엔진에_있다():
    missing = sorted(used_names() - engine_names())
    assert not missing, (
        f"엔진에 없는 이름을 부르고 있습니다: {missing}\n"
        f"엔진 파일: {ENGINE_PATH}"
    )


def test_실제로_뭔가를_부르고_있다():
    """검사가 «아무것도 안 세는» 상태로 조용히 통과하는 걸 막는다."""
    assert len(used_names()) >= 20


# ── 약속(Protocol)을 지키는가 ───────────────────────────

@pytest.mark.parametrize("method", ["find_company", "run"])
def test_데모와_진짜가_같은_약속을_지킨다(method):
    want = str(inspect.signature(getattr(Pipeline, method)))
    assert str(inspect.signature(getattr(DemoPipeline, method))) == want
    assert str(inspect.signature(getattr(real.RealPipeline, method))) == want


# ── 돈·안전 ─────────────────────────────────────────────

def test_불러오기만_해서는_무거운_프로그램을_안_건드린다():
    """새 프로세스에서 adapter import만으로 무거운 엔진을 읽지 않는다.

    전체 suite에서는 앞선 테스트가 의도적으로 ``anthropic``을 불러올 수 있다.
    그런 전역 ``sys.modules`` 상태에 기대면 실행 순서만으로 결과가 바뀌므로,
    실제 계약인 '깨끗한 프로세스의 import'를 독립 interpreter에서 확인한다.
    """
    subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; "
                "from src.features.pipeline import real; "
                "assert 'run_pilot' not in sys.modules; "
                "assert 'anthropic' not in sys.modules"
            ),
        ],
        cwd=paths.APP_ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )


def test_demo는_anthropic이_없어도_새_프로세스에서_부팅된다():
    """데모 선택은 선택 의존성인 provider SDK를 전혀 요구하지 않는다."""
    subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import builtins, os, sys; "
                "real_import = builtins.__import__; "
                "builtins.__import__ = lambda name, *args, **kwargs: "
                "(_ for _ in ()).throw(ImportError('anthropic blocked')) "
                "if name == 'anthropic' or name.startswith('anthropic.') "
                "else real_import(name, *args, **kwargs); "
                "os.environ['PIPELINE'] = 'demo'; "
                "from src.web import runtime; "
                "from src.features.pipeline.demo import DemoPipeline; "
                "assert isinstance(runtime._PIPELINE, DemoPipeline); "
                "assert 'anthropic' not in sys.modules"
            ),
        ],
        cwd=paths.APP_ROOT,
        check=True,
        capture_output=True,
        text=True,
        timeout=15,
    )


def test_엔진을_부르는_길은_한_곳뿐이다():
    """여러 곳에서 엔진을 불러오면 «어디서 돈이 나가는지» 추적할 수 없다."""
    calls = sum(
        1
        for node in ast.walk(_tree(REAL_PATH))
        if isinstance(node, ast.FunctionDef) and node.name == "_engine"
    )
    assert calls == 1


def test_진짜_알맹이는_아직_안_꽂혀_있다():
    """★ 이 시험이 깨지면 «돈이 나가기 시작했다»는 뜻이다. 의도한 것인지 확인할 것."""
    from src.web import runtime

    assert isinstance(runtime._PIPELINE, DemoPipeline), (
        "진짜 파이프라인이 꽂혔습니다. AI 호출 = 비용이 발생합니다."
    )
