"""진짜 조사 흐름에서만 쓰는 고정값."""

from typing import Final

#: DART OpenAPI가 요청을 정상 처리했음을 뜻하는 상태 코드.
#: 목록이 비어 있는 정상 응답과 한도·인증·서버 오류를 가르려면 반드시 확인한다.
DART_SUCCESS_STATUS: Final[str] = "000"

#: Anthropic 호출 한 번의 최대 대기 시간. SDK 기본 retry는 별도로
#: 끄므로, 이 값은 단일 호출이 서버 worker를 무한정 점유하지 못하게 한다.
ANTHROPIC_TIMEOUT_SEC: Final[float] = 180.0
