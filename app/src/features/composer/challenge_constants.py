"""5장 대응표에서 실제 대응을 대신할 수 없는 빈 값 표기."""

CHALLENGE_RESPONSE_MISSING = "challenge_response_missing"
#: 대응 칸이 «비어 있지는 않은데» 그 줄이 인용한 원문에 없는 말일 때.
#:
#: ★ 왜 빈 값과 다른 사유 코드인가 — 둘은 고칠 곳이 다르다. 빈 값은 회사가
#:   대응을 안 밝힌 것이고, 이쪽은 작가가 원문에 없는 대응을 적은 것이다.
#:   같은 코드로 묶으면 진단만 보고는 어느 쪽인지 알 수 없어 엉뚱한 데를
#:   고치게 된다(실측: 「대응 0건」을 추출 실패로 오진).
CHALLENGE_RESPONSE_NOT_IN_SOURCE = "challenge_response_not_in_source"
CHALLENGE_RESPONSE_CELL_COUNT = 2
CHALLENGE_RESPONSE_CELL_INDEX = 1
# 업종이나 행동을 분류하는 목록이 아니다. 대응 칸 전체가 빈 값일 때만 쓴다.
CHALLENGE_EMPTY_RESPONSES = frozenset({
    "", "없음", "미상", "미확인", "미정", "해당없음", "해당사항없음",
    "확인되지않음", "공시없음", "n/a", "-", "—", "–", ".",
})
