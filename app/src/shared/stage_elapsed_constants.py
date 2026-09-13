"""실행 진단에 남기는 단계별 소요 시간 항목의 고정 열쇠.

파이프라인(생산자, `features/pipeline/real.py`)과 관리자 화면(소비자,
`web/routers/admin.py`)이 같은 문자열을 봐야 한다. 둘 다 feature이므로
어느 한쪽에 두면 다른 쪽이 그 feature를 직접 import해야 하는데, 같은 성격의
진단 열쇠인 `RUNTIME_FAILURE_STEP`(`shared/runtime_failure_constants.py`)이
이미 `shared`에 한 벌만 두는 방식을 쓰고 있어 그대로 따른다 — 값을 두 곳에
복제하고 대조 시험으로 지키는 대신, 한 곳만 두고 양쪽이 그것을 읽는다.
"""

from __future__ import annotations

from typing import Final

#: 화면 단계가 바뀔 때마다 직전 단계의 소요 시간을 담는 진단 항목의 "step" 값.
STAGE_ELAPSED_STEP: Final[str] = "단계소요"
#: 위 진단 항목에서 소요 시간을 담는 필드 이름. 값은 밀리초, 0 이상 정수다.
STAGE_ELAPSED_MS_KEY: Final[str] = "소요ms"
