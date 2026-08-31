"""수집 엔진(analysis_engine)의 슬롯 어휘와 정책 어휘가 갈라지지 않았는지.

엔진 모듈을 import하면 이 워크트리에 없는 무거운 의존성까지 끌려온다. import
대신 파일 경로를 ast로 파싱해 ``COLLECTOR_SLOTS_BY_SECTION`` 딕셔너리
리터럴만 꺼낸다. 이 생산부 워크트리에는 엔진 사본이 아직 없을 수 있으므로,
그 경우 소리 나게 건너뛴다(조용히 통과시키지 않는다).
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.shared.report_evidence.policy import (
    REQUIRED_EVIDENCE_SECTION_IDS,
    collector_slots_for,
)


def _engine_constants_path() -> Path:
    # 이 시험 파일 위치 기준: <워크트리 루트>/analysis_engine/src/features/
    #   evidence_collection/constants.py
    # app/src/features/chapter_evidence/tests/test_vocabulary_equivalence.py
    #   parents[0]=tests, [1]=chapter_evidence, [2]=features, [3]=src, [4]=app,
    #   [5]=워크트리 루트
    worktree_root = Path(__file__).resolve().parents[5]
    return (
        worktree_root
        / "analysis_engine"
        / "src"
        / "features"
        / "evidence_collection"
        / "constants.py"
    )


def _extract_collector_slots_by_section(path: Path) -> dict[str, tuple[str, ...]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        targets = [target.id for target in node.targets if isinstance(target, ast.Name)]
        if "COLLECTOR_SLOTS_BY_SECTION" not in targets:
            continue
        literal = ast.literal_eval(node.value)
        return {
            str(section_id): tuple(str(slot_id) for slot_id in slots)
            for section_id, slots in literal.items()
        }
    raise AssertionError(
        "엔진 상수 파일에 COLLECTOR_SLOTS_BY_SECTION 딕셔너리 리터럴이 없습니다"
    )


def test_엔진의_수집_슬롯_어휘가_정책과_같다() -> None:
    engine_path = _engine_constants_path()
    if not engine_path.exists():
        pytest.skip("엔진 사본 미존재 — 통합 트리에서 실행됨")

    engine_slots = _extract_collector_slots_by_section(engine_path)

    for section_id in REQUIRED_EVIDENCE_SECTION_IDS:
        assert section_id in engine_slots, f"엔진에 없는 장: {section_id}"
        assert tuple(engine_slots[section_id]) == collector_slots_for(section_id), (
            f"{section_id} 의 수집 슬롯 어휘가 엔진과 정책 사이에서 갈라졌습니다"
        )
