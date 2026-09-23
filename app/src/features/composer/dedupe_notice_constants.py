"""중복 이동·자료 잔존 안내문의 «대상 장 표시» 판 — dedupe 도메인 전용 상수.

★ 왜 constants.py에 두지 않나 — 큰 공용 상수 파일은 다른 소유 영역이라
  이 도메인이 함께 고치지 않는다. 이 판은 `dedupe.reconcile_section_notices`가
  «이동 근거를 확인했을 때만» 쓰는 문구라 dedupe 도메인에 둔다.

★ 문구 원칙 (2026-09-23 총괄 결정, P19)
  · 이동을 단언하려면 «그 내용이 지금 그 장에 실려 있다»는 근거가 있어야
    한다. 근거가 있으면 대상 장의 번호·제목을 함께 적는다.
  · 「아래」처럼 지면 위치를 보장하는 말, 「표」·「도식」을 뭉뚱그리는 말을
    쓰지 않는다 — 실제 남은 자료의 종류를 그대로 부른다.
  · 근거가 없으면 이동 단언을 지우고 자료 부족 계열로 내린다.

★ 접두어 상수는 «다시 알아보기» 열쇠다. reconcile은 멱등이어야 하므로,
  이미 이 판으로 바뀐 안내문을 두 번째 호출에서도 이동 계열로 알아봐야
  한다. 문구를 바꿀 때 접두어가 갈라지면 멱등이 깨진다 — 시험이
  (test_dedupe_moved_targets.py) 접두어와 템플릿의 일치를 대조한다.
"""

from __future__ import annotations

from typing import Final

#: 실제 남긴 소유 문장의 원문·인용 순서·슬롯·등급 지문 규칙.
MOVED_OWNER_FINGERPRINT_VERSION: Final[str] = "moved-owner-candidate-v1"

#: 이동 계열(대상 장 표시 판)의 고정 접두어 — 알아보기용.
NOTICE_MOVED_TARGETS_PREFIX: Final[str] = "이 장에 담겼던 내용은 "

#: 이동 계열(대상 장 표시 판) 본문. ``{targets}``에 「4장 «주요 변화와 실적»」
#: 꼴이 들어간다. 여러 장이면 «·»로 잇는다.
NOTICE_MOVED_TARGETS_TEMPLATE: Final[str] = (
    NOTICE_MOVED_TARGETS_PREFIX
    + "{targets}에서 다루고 있어, 같은 설명을 두 번 싣지 않았습니다. "
    + "자료가 없어서 비어 있는 것이 아닙니다."
)

#: 문장 없이 자료(표·도식·보도 목록)만 남은 장의 접두어 — 알아보기용.
NOTICE_ONLY_MATERIALS_PREFIX: Final[str] = "확인된 문장이 부족해 이 장에는 "

#: 자료 부족 + 자료 잔존 판. ``{materials}``에는 실제 남은 자료 종류를
#: «·»로 이어 넣는다(예: 「도식」·「표·보도 목록」). 지면 위치는 말하지 않는다.
NOTICE_ONLY_MATERIALS_TEMPLATE: Final[str] = (
    NOTICE_ONLY_MATERIALS_PREFIX + "{materials} 자료만 실었습니다."
)

#: 이동 계열 문장 뒤에 자료 잔존을 덧붙일 때 쓰는 꼬리.
NOTICE_MOVED_MATERIALS_SUFFIX_TEMPLATE: Final[str] = (
    " 이 장의 {materials} 자료는 함께 실려 있습니다."
)

#: 남은 자료 종류의 표시 이름 — 렌더가 그리는 실제 산출물 이름과 맞춘다.
MATERIAL_NAME_FLOW: Final[str] = "도식"
MATERIAL_NAME_NEWS: Final[str] = "보도 목록"
MATERIAL_NAME_TABLE: Final[str] = "표"

#: 종류 표시를 잇는 글자.
MATERIAL_JOIN: Final[str] = "·"
