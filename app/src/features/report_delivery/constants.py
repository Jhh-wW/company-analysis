"""report_delivery 기능 전체가 공유하는 닫힌 상수.

매직 넘버·사유 코드를 이 파일 하나로 모아, 스윕·adapter·web 계층이 서로
다른 값을 쓰다 어긋나는 사고를 막는다.
"""

from __future__ import annotations

from typing import Final

#: 서버 재시작 스윕이 ``required``로 정체된 delivery 의무를 실패로 넘기기까지
#: 기다리는 최소 시간(분). 진행 중인 정상 요청(생성 1건이 걸릴 수 있는 시간)을
#: 스윕이 실패로 오인하지 않도록 넉넉히 잡는다.
STALE_DELIVERY_INTENT_MINUTES: Final[int] = 30

#: 재시작 스윕이 정체된 delivery 의무를 닫을 때 남기는 기계 실패 코드.
#: ``report_delivery_adapter.fail_public_delivery``의 ``failure_code``
#: 정규식(``[a-z0-9_]{1,64}``)을 만족해야 한다.
STALE_DELIVERY_INTENT_FAILURE_CODE: Final[str] = "server_restart_incomplete"

#: 관리자가 ``/admin/delivery/settle``에서 수동으로 대사할 때 남기는 실패 코드.
#: 자동 스윕 코드와 다른 값을 써서 감사 로그에서 자동/수동을 구분한다.
MANUAL_SETTLEMENT_FAILURE_CODE: Final[str] = "admin_manual_settled"
