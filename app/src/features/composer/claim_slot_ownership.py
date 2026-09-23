"""조각이 «스스로 봉인해 온» 의미 칸에서 장 소유를 읽는 한 벌짜리 헬퍼.

★ 왜 따로 두나 — 「어느 의미 칸이 어느 장의 것인가」의 정본은 shared
  (`report_claim_policy`) 하나뿐인데, 그 표를 뒤집어 쓰는 자리가 보도표
  (`news_block`)와 중복 제거(`dedupe`) 두 곳이 됐다. 각자 뒤집어 들고 있으면
  정책이 칸을 옮길 때 한쪽만 옛 장에 붙는다. 그래서 뒤집기·조각 판독을
  이 모듈 하나로 모은다.

★ 칸 이름은 ``<장>:<칸>`` 모양이라 문자열을 잘라도 장이 나오지만, 자르지
  않는다 — 이름 규칙이 바뀌면 조용히 틀린 장에 붙는다. 정본 표에서 뒤집는다.

★ 모르는 칸 이름은 «무시»한다 — 소유를 «모른다»로 남길 뿐, 어느 장에도
  배정하지 않는다. 보조 판정 하나 때문에 보고서 전체를 예외로 막지 않는다.
"""

from __future__ import annotations

from typing import Final

from src.shared.report_claim_policy import CLAIM_SLOTS_BY_SECTION


#: 의미 칸(claim slot) → 그 칸이 속한 장. 정본 표(장 → 칸들)를 뒤집은 것이다.
SECTION_OF_SLOT: Final[dict[str, str]] = {
    slot_id: section_id
    for section_id, slot_ids in CLAIM_SLOTS_BY_SECTION.items()
    for slot_id in slot_ids
}


def sections_supported_by(fragment: object) -> frozenset[str]:
    """이 조각의 ``supported_claim_slots``가 가리키는 장 id 집합.

    legacy 조각(의미 칸이 빈 튜플)은 빈 집합을 돌려준다 — 소유를 «모른다»는
    뜻이지 «어느 장도 아니다»가 아니다. 부르는 쪽은 빈 집합을 근거로 아무
    판정도 바꾸면 안 된다.
    """

    supported: set[str] = set()
    for slot_id in tuple(getattr(fragment, "supported_claim_slots", ()) or ()):
        section_id = SECTION_OF_SLOT.get(str(slot_id).strip())
        if section_id is not None:
            supported.add(section_id)
    return frozenset(supported)
