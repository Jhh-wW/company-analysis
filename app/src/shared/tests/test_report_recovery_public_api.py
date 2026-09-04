"""``shared.report_recovery`` 공개 함수가 죽은 채 남는 걸 막는 재발 방지 시험.

감사 Q-F3: ``decide_preflight``는 ``features.report_recovery`` facade를
거쳐 계속 재export되고 있었지만, production 어디서도 실제로 «호출»되지
않은 채 남아 있었다. 문서 독자는 재export만 보고 이 함수가 실제 게이트인
줄 오인했다 — 진짜 v2 사전 게이트는 ``pipeline/real.py``의
``GATE_STOPPED`` 발화점이 맡는다.

이 시험은 AST로 ``report_recovery.py``의 공개(언더스코어로 시작하지
않는) 최상위 함수 이름을 뽑고, ``app/src``·``analysis_engine/src``의
시험이 아닌 파일에 그 이름을 «호출 구문»(``이름(``)으로 참조하는 곳이
최소 하나는 있는지 확인한다.

단순 word 매칭이 아니라 호출 구문을 요구하는 이유: facade
(``features/report_recovery/logic.py``·``__init__.py``)는 함수를
import·재export만 해도 이름 자체는 파일 안에 등장한다. 그 재export만
보고 통과시키면 이번에 놓친 것과 똑같은 사각을 다시 만든다 — 실제로
그 함수를 «호출»하는 자리가 있어야만 살아 있는 공개 API로 인정한다.

예외 목록은 두지 않는다. 새 공개 함수를 추가했는데 이 시험이 실패하면,
production 호출자를 실제로 배선하거나 함수 자체를 정리 대상으로 본다.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

_MODULE_PATH = Path(__file__).resolve().parents[1] / "report_recovery.py"

# app/src/shared/tests/이 파일 위치 기준: parents[2] == app/src, parents[4] == 저장소 루트
_APP_SRC = Path(__file__).resolve().parents[2]
_REPO_ROOT = Path(__file__).resolve().parents[4]
_ENGINE_SRC = _REPO_ROOT / "analysis_engine" / "src"


def _public_top_level_function_names() -> tuple[str, ...]:
    tree = ast.parse(_MODULE_PATH.read_text(encoding="utf-8"))
    return tuple(
        node.name
        for node in ast.iter_child_nodes(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and not node.name.startswith("_")
    )


def _is_test_file(path: Path) -> bool:
    return (
        "tests" in path.parts
        or path.name.startswith("test_")
        or path.name == "conftest.py"
    )


def _has_production_call_site(name: str) -> bool:
    """``이름(`` 호출 구문이 시험이 아닌 파일에 있으면 참이다.

    import·``__all__`` 재export만으로는 참이 되지 않는다 —
    그 형태는 «참조»는 맞지만 «호출자»는 아니다.
    """

    pattern = re.compile(rf"\b{re.escape(name)}\s*\(")
    for root in (_APP_SRC, _ENGINE_SRC):
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            if path.resolve() == _MODULE_PATH or _is_test_file(path):
                continue
            text = path.read_text(encoding="utf-8")
            if pattern.search(text):
                return True
    return False


def test_report_recovery_공개_함수는_모두_production_호출자가_있다() -> None:
    public_names = _public_top_level_function_names()
    assert public_names, (
        "shared.report_recovery에 공개 함수가 하나도 없으면 "
        "이 시험은 아무것도 지키지 못한다 — 대상이 있는지부터 확인한다"
    )

    orphans = [name for name in public_names if not _has_production_call_site(name)]

    assert orphans == [], (
        "production 호출자가 없는 공개 함수: "
        f"{orphans}. facade에서 재export만 되고 실제로 호출되지 않으면 "
        "감사 Q-F3처럼 죽은 채 방치된다 — 실제 호출자를 배선하거나 함수를 지운다."
    )
