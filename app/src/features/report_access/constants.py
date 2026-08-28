"""보고서 열람 grant의 한곳짜리 정책값."""

from __future__ import annotations

from typing import Final

from src.features.budget.sharing import REPORT_LINK_MAX_AGE_DAYS


PUBLIC_GRANT_COOKIE_NAME: Final[str] = "report_access"
PUBLIC_GRANT_TOKEN_BYTES: Final[int] = 32
# 보고서 Delivery와 같은 정본을 쓴다. 권한 쿠키만 먼저 끝나 2일째부터 자기
# 60일 보고서를 못 여는 서로 다른 수명 정책을 만들지 않는다.
PUBLIC_GRANT_MAX_AGE_SEC: Final[int] = (
    REPORT_LINK_MAX_AGE_DAYS * 24 * 60 * 60
)
PUBLIC_GRANT_ALLOCATION_ATTEMPTS: Final[int] = 8
REPORT_ID_HEX_CHARS: Final[int] = 32

# PUBLIC은 전역 5회/10분 입장 제한 때문에 이론상 하루 최대 720회다. 60일
# 수명 전체를 무제한으로 쌓지 않고, 정상 동시 사용의 두 배가 넘는 2,048개에서
# 새 grant를 fail-closed한다. 만료·철회 행은 같은 write transaction에서 먼저
# 지우므로 이 상한은 «살아 있는 grant»에만 적용된다.
PUBLIC_ACTIVE_GRANT_LIMIT: Final[int] = 2048
# 한 브라우저가 60일 동안 만들 수 있는 보고서 결속. 실제 제품 일일 입장 상한보다
# 충분히 크면서 탈취·자동화 한 개가 행을 무한히 늘리지는 못하게 한다.
PUBLIC_BINDINGS_PER_GRANT_LIMIT: Final[int] = 256

# ID 자체가 열쇠였던 구버전과 grant 버전의 영구 보안 경계. 배포 때의 wall-clock을
# 매번 새로 쓰면 access schema가 롤백·재생성된 날까지 새 보고서가 legacy로
# 승격된다. 그래서 코드에 박힌 한 시각을 DB에도 단 한 번 기록하고, 두 값이
# 다르면 bootstrap을 닫는다. 이 시각 뒤 생성 행은 어떤 재시작에서도 fallback이
# 될 수 없다.
LEGACY_COMPAT_CUTOVER_AT_ISO: Final[str] = "2026-08-28T20:00:00+09:00"
