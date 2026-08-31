"""보고서 열람 grant의 한곳짜리 정책값."""

from __future__ import annotations

from typing import Final

from src.core.constants import REPORT_GENERATION_EXECUTION_MAX_SEC
from src.features.budget.sharing import REPORT_LINK_MAX_AGE_DAYS


PUBLIC_GRANT_COOKIE_NAME: Final[str] = "report_access"
PUBLIC_GRANT_TOKEN_BYTES: Final[int] = 32
# SQLite 저장 거래를 마지막으로 확정할 때 남겨 두는 최소 시간.
PUBLIC_GRANT_COMMIT_MARGIN_SEC: Final[int] = 30
# grant는 조사 시작 때 생기지만 Delivery의 60일은 보고서가 완성된 뒤 시작한다.
# 따라서 정상 최대 실행시간과 마지막 DB commit 여유를 더해야 권한 쿠키가 먼저
# 끝나지 않는다. 실제 보고서는 Delivery가 60일에 먼저 닫으므로 열람기간을
# 늘리는 값이 아니다.
PUBLIC_GRANT_MAX_AGE_SEC: Final[int] = (
    REPORT_LINK_MAX_AGE_DAYS * 24 * 60 * 60
    + REPORT_GENERATION_EXECUTION_MAX_SEC
    + PUBLIC_GRANT_COMMIT_MARGIN_SEC
)
PUBLIC_GRANT_ALLOCATION_ATTEMPTS: Final[int] = 8
REPORT_ID_HEX_CHARS: Final[int] = 32

# PUBLIC은 전역 5회/10분 입장 제한 때문에 이론상 하루 최대 720회다. 전체
# grant 수명을 무제한으로 쌓지 않고, 정상 동시 사용의 두 배가 넘는 2,048개에서
# 새 grant를 fail-closed한다. 만료·철회 행은 같은 write transaction에서 먼저
# 지우므로 이 상한은 «살아 있는 grant»에만 적용된다.
PUBLIC_ACTIVE_GRANT_LIMIT: Final[int] = 2048
# 한 브라우저가 한 grant 수명 동안 만들 수 있는 보고서 결속. 일일 입장 상한보다
# 충분히 크면서 탈취·자동화 한 개가 행을 무한히 늘리지는 못하게 한다.
PUBLIC_BINDINGS_PER_GRANT_LIMIT: Final[int] = 256

# ID 자체가 열쇠였던 구버전과 grant 버전의 영구 보안 경계. 배포 때의 wall-clock을
# 매번 새로 쓰면 access schema가 롤백·재생성된 날까지 새 보고서가 legacy로
# 승격된다. 그래서 코드에 박힌 한 시각을 DB에도 단 한 번 기록하고, 두 값이
# 다르면 bootstrap을 닫는다. 이 시각 뒤 생성 행은 어떤 재시작에서도 fallback이
# 될 수 없다.
LEGACY_COMPAT_CUTOVER_AT_ISO: Final[str] = "2026-08-28T20:00:00+09:00"
