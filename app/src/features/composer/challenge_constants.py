"""5장 대응표에서 실제 대응을 대신할 수 없는 빈 값 표기."""

CHALLENGE_RESPONSE_MISSING = "challenge_response_missing"
CHALLENGE_RESPONSE_CELL_COUNT = 2
CHALLENGE_RESPONSE_CELL_INDEX = 1
# 업종이나 행동을 분류하는 목록이 아니다. 대응 칸 전체가 빈 값일 때만 쓴다.
CHALLENGE_EMPTY_RESPONSES = frozenset({
    "", "없음", "미상", "미확인", "미정", "해당없음", "해당사항없음",
    "확인되지않음", "공시없음", "n/a", "-", "—", "–", ".",
})
