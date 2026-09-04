"""수집 엔진(analysis_engine)의 슬롯 어휘와 정책 어휘가 갈라지지 않았는지.

엔진 모듈을 import하면 무거운 의존성까지 함께 끌려온다. import 대신 파일
경로를 ast로 파싱해 ``COLLECTOR_SLOTS_BY_SECTION`` 딕셔너리 리터럴만
꺼낸다. 엔진 소스가 함께 있지 않은 배치(app만 떼어 낸 경우)도 있으므로 그
때만 소리 나게 건너뛴다(조용히 통과시키지 않는다). 모듈 폴더는 있는데
constants.py가 없거나, 있어도 이 딕셔너리를 못 찾거나 값이 다르면 — 그건
진짜 어휘가 갈라진 것이므로 건너뛰지 않고 반드시 실패해야 한다.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from src.shared.report_evidence.policy import (
    REQUIRED_EVIDENCE_SECTION_IDS,
    collector_slots_for,
)


def _engine_module_dir() -> Path:
    # 이 시험 파일 위치 기준: <저장소 루트>/analysis_engine/src/features/
    #   evidence_collection/
    # app/src/features/chapter_evidence/tests/test_vocabulary_equivalence.py
    #   parents[0]=tests, [1]=chapter_evidence, [2]=features, [3]=src, [4]=app,
    #   [5]=저장소 루트
    repo_root = Path(__file__).resolve().parents[5]
    return (
        repo_root
        / "analysis_engine"
        / "src"
        / "features"
        / "evidence_collection"
    )


def _engine_constants_path() -> Path:
    return _engine_module_dir() / "constants.py"


def _extract_collector_slots_by_section(path: Path) -> dict[str, tuple[str, ...]]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        # 일반 대입(``NAME = {...}``)과 주석 붙은 대입(``NAME: Final[...] = {...}``)
        # 둘 다 처리한다 — 엔진 사본은 후자(ast.AnnAssign) 형태를 쓴다.
        if isinstance(node, ast.Assign):
            targets = [target.id for target in node.targets if isinstance(target, ast.Name)]
            value_node = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target.id]
            value_node = node.value
        else:
            continue
        if "COLLECTOR_SLOTS_BY_SECTION" not in targets or value_node is None:
            continue
        literal = ast.literal_eval(value_node)
        return {
            str(section_id): tuple(str(slot_id) for slot_id in slots)
            for section_id, slots in literal.items()
        }
    raise AssertionError(
        "엔진 상수 파일에 COLLECTOR_SLOTS_BY_SECTION 딕셔너리 리터럴이 없습니다"
    )


def test_엔진의_수집_슬롯_어휘가_정책과_같다() -> None:
    engine_module_dir = _engine_module_dir()
    if not engine_module_dir.exists():
        pytest.skip(
            "엔진 evidence_collection 소스가 이 저장소에 없음 — "
            "app만 떼어 낸 배치"
        )

    engine_path = _engine_constants_path()
    assert engine_path.exists(), (
        "엔진 evidence_collection 모듈은 있는데 constants.py가 없습니다 — "
        "건너뛰지 않고 실패로 처리합니다"
    )

    engine_slots = _extract_collector_slots_by_section(engine_path)

    for section_id in REQUIRED_EVIDENCE_SECTION_IDS:
        assert section_id in engine_slots, f"엔진에 없는 장: {section_id}"
        assert tuple(engine_slots[section_id]) == collector_slots_for(section_id), (
            f"{section_id} 의 수집 슬롯 어휘가 엔진과 정책 사이에서 갈라졌습니다"
        )
