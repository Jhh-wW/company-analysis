"""수집기 슬롯 «사본»들이 정본 정책과 갈라지지 않았는지 못 박는다.

같은 슬롯 목록이 세 곳에 있다.

- 정본: ``src/shared/report_evidence/policy.py``
- 사본 1: ``analysis_engine …/evidence_collection/constants.py``
  (엔진은 app을 import할 수 없어 값을 복사해 둔다)
- 사본 2: ``src/features/homepage/constants.py``의
  ``WIDE_REQUIRED_SLOT_IDS_BY_SECTION``

★ 이 파일이 메우는 구멍 — 사본 2는 지금까지 «정본과 같은지»를 아무도 보지
  않았다. ``homepage/tests/test_wide_collect.py``는 이 사본을 기대값으로 쓸
  뿐이라, 사본이 정본과 어긋나도 그 시험들은 그대로 초록이다.
  사본 1의 «값»은 ``test_vocabulary_equivalence.py``가 이미 지키지만, 그
  시험은 정본에 있는 9개 장만 훑으므로 «엔진에만 있는 여분의 장»은 못 본다.

★ 장 이름을 리터럴로 한 번 더 못 박는 이유 — 위 두 대조를 포함해 이 저장소의
  슬롯 시험은 전부 ``REQUIRED_EVIDENCE_SECTION_IDS``를 순회한다. 그 튜플이
  조용히 줄어들면 순회 횟수만 줄어든 채 전부 통과한다. 리터럴 단정이 그
  바닥을 받친다.
"""

from __future__ import annotations

import pytest

from src.features.chapter_evidence.tests.test_vocabulary_equivalence import (
    _engine_constants_path,
    _engine_module_dir,
    _extract_collector_slots_by_section,
)
from src.features.homepage.constants import WIDE_REQUIRED_SLOT_IDS_BY_SECTION
from src.shared.report_evidence.policy import (
    REQUIRED_EVIDENCE_SECTION_IDS,
    collector_slots_for,
    injected_slots_for,
)

#: 보고서 필수 아홉 장. 정본 상수에서 가져오지 않고 여기 적어 둔다 — 정본이
#: 줄어드는 회귀를 잡는 것이 목적이라 정본을 기준값으로 쓰면 의미가 없다.
_EXPECTED_SECTION_IDS: tuple[str, ...] = (
    "identity",
    "business_model",
    "portfolio",
    "past_changes",
    "current_challenges",
    "future_strategy",
    "operations_partners",
    "culture",
    "competitive_position",
)

#: 수집기가 «만들지 않는» 슬롯 — 구조화 검증기가 재무 API 수치와 동일조건
#: 비교 결과로 직접 주입한다. 수집기의 키워드 채점이 같은 칸을 채우면 권위가
#: 다른 두 값이 겹쳐 어느 쪽을 믿을지 모호해진다.
_INJECTED_ONLY_SLOT_IDS: tuple[str, ...] = (
    "past_changes:historical_performance",
    "competitive_position:comparison_target",
    "competitive_position:comparison_metric",
    "competitive_position:comparison_basis",
    "competitive_position:comparison_judgment",
    "competitive_position:limitation",
)


def test_필수_아홉장_이름이_리터럴로_고정돼_있다() -> None:
    """장이 조용히 사라지면 순회형 시험들이 전부 «덜 훑고» 통과한다."""

    assert REQUIRED_EVIDENCE_SECTION_IDS == _EXPECTED_SECTION_IDS
    assert len(REQUIRED_EVIDENCE_SECTION_IDS) == 9


def test_넓은_웹수집기_슬롯_사본의_장이_정본과_정확히_같다() -> None:
    """사본에 없는 장도, 정본에 없는 여분의 장도 허용하지 않는다."""

    assert set(WIDE_REQUIRED_SLOT_IDS_BY_SECTION) == set(_EXPECTED_SECTION_IDS)


@pytest.mark.parametrize("section_id", _EXPECTED_SECTION_IDS)
def test_넓은_웹수집기_슬롯_사본이_정본_수집기_슬롯과_같다(section_id: str) -> None:
    """사본 2가 정본에서 «수집기 몫»으로 정한 칸과 순서까지 같아야 한다."""

    assert tuple(WIDE_REQUIRED_SLOT_IDS_BY_SECTION[section_id]) == collector_slots_for(
        section_id
    ), f"{section_id} 의 수집기 슬롯이 정본과 사본 사이에서 갈라졌습니다"


def test_구조화_검증기_몫_슬롯은_수집기_사본에_없다() -> None:
    """정본과 사본이 «함께» 밀려도 잡히도록 주입 전용 칸을 리터럴로 못 박는다."""

    copied_slot_ids = {
        slot_id
        for slots in WIDE_REQUIRED_SLOT_IDS_BY_SECTION.values()
        for slot_id in slots
    }
    for slot_id in _INJECTED_ONLY_SLOT_IDS:
        section_id = slot_id.split(":", 1)[0]
        assert slot_id in injected_slots_for(section_id), (
            f"{slot_id} 가 정본의 주입 전용 칸에서 빠졌습니다"
        )
        assert slot_id not in copied_slot_ids, (
            f"{slot_id} 는 구조화 검증기 몫인데 수집기 사본이 만들려 하고 있습니다"
        )


def test_엔진_슬롯_사본에_정본에_없는_장이_없다() -> None:
    """값 대조는 test_vocabulary_equivalence 가 하고, 여기서는 여분의 장을 막는다."""

    engine_module_dir = _engine_module_dir()
    if not engine_module_dir.exists():
        pytest.skip("엔진 evidence_collection 모듈이 이 배치에 없음")

    engine_path = _engine_constants_path()
    assert engine_path.exists(), (
        "엔진 evidence_collection 모듈은 있는데 constants.py가 없습니다 — "
        "건너뛰지 않고 실패로 처리합니다"
    )

    engine_slots = _extract_collector_slots_by_section(engine_path)

    assert set(engine_slots) == set(_EXPECTED_SECTION_IDS)
