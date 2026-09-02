"""품질 게이트 워크플로의 트리거와 검사 범위를 고정하는 시험.

릴리스 절차가 「release 브랜치 push → CI 초록 확인 → main 병합」이라서
release 브랜치를 push하는 시점에 자동 시험이 돌지 않으면 초록을 확인할 방법이 없다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
WORKFLOW_PATH = REPOSITORY_ROOT / ".github" / "workflows" / "quality-gate.yml"

# YAML 1.1은 따옴표 없는 on 을 «참»으로 읽는다(실측: PyYAML 6.0.3도 같다).
# 그래서 워크플로의 on: 블록은 문자열 키가 아니라 불리언 True 키로 들어온다.
YAML_TRUE_KEY = True

# release 브랜치는 push 시점에, main 은 병합 뒤에 각각 게이트가 필요하다.
REQUIRED_PUSH_BRANCHES = ("main", "release/**")

# 게이트가 실제로 훑어야 하는 5개 영역. 하나라도 빠지면 그만큼 사각지대가 된다.
REQUIRED_STAGE_COMMANDS = (
    "python -m pytest app/src app/tools/tests -q",
    "python -m pytest analysis_engine/src -q",
    "python -m pytest deploy/tests -q",
    "python -m pytest ops -q",
    "docker build --file app/Dockerfile",
    "verify_container_contract.py",
)


def _read_workflow_text() -> str:
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def _load_trigger_section() -> dict[str, Any]:
    """워크플로의 on: 블록을 돌려준다."""
    document = yaml.safe_load(_read_workflow_text())
    assert isinstance(document, dict), "품질 게이트 워크플로를 매핑으로 읽지 못했습니다."

    trigger = document.get("on", document.get(YAML_TRUE_KEY))
    assert isinstance(trigger, dict), "워크플로에 트리거(on:) 블록이 없습니다."
    return trigger


def test_push_trigger_covers_main_and_release_branches() -> None:
    push = _load_trigger_section().get("push")
    assert isinstance(push, dict), "push 트리거가 없으면 브랜치 조건을 걸 수 없습니다."

    branches = push.get("branches")
    assert isinstance(branches, list), "push 트리거에 branches 목록이 없습니다."

    for branch in REQUIRED_PUSH_BRANCHES:
        assert branch in branches, (
            f"품질 게이트가 «{branch}» push에서 돌지 않습니다. 지금 값: {branches}"
        )


def test_pull_request_trigger_is_kept() -> None:
    # 값이 비어 있어(None) 참/거짓으로 보면 안 되므로 키 존재만 확인한다.
    trigger = _load_trigger_section()
    assert "pull_request" in trigger, (
        "PR 트리거가 사라지면 main 병합 전에 검사할 기회가 없어집니다."
    )


def test_gate_still_runs_all_five_stages() -> None:
    workflow = _read_workflow_text()

    for command in REQUIRED_STAGE_COMMANDS:
        assert command in workflow, f"품질 게이트 검사 단계가 사라졌습니다: {command}"
