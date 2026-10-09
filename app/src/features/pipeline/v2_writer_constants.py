"""근거·의미칸을 포함하는 장별 작성 JSON의 출력 상한."""

from typing import Final

# 실제 작성 JSON이 4000토큰에서 잘려 읽히지 않은 경계를 보완한다.
# 응답 완료를 보장하지 않으며 호출 전 예약은 상한, 정산은 실제 사용량으로 한다.
V2_WRITER_MAX_TOKENS: Final[int] = 6000
