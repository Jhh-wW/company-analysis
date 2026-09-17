"""근거 결속 재작성 기능을 운영에서 끄는 스위치.

``evidence_reclassify_switch.py`` 와 이름·주석 스타일은 같지만 의미는 반대다 —
저기는 «켜는» 스위치(선언하지 않으면 꺼짐)이고, 여기는 «끄는» 스위치다.

★ 왜 기본이 켜짐인가 — 근거 결속 검사에서 탈락한 «확인» 문장을 고쳐 써서
  살리는 이 기능은, 켰을 때와 껐을 때의 결과를 같은 실행 안에서 바로 비교
  실측해야 한다. 기본을 꺼짐으로 두면 그 비교를 하기 위해 매번 배포 설정을
  또 바꿔야 한다. 대신 기본을 켬으로 두고, 문제가 생기면 대시보드에서 값을
  정확히 ``"0"``으로 바꿔 즉시 끈다 — 코드 되감기나 재배포용 커밋이 필요 없다.

★ 왜 process-freeze 를 쓰지 않는가 — 이 스위치는 한 실행 안에서 값이 바뀌어도
  «이미 시작한 비교»를 무효화하지 않는다(재작성 여부는 각 시도 기록에 그대로
  남는다). 그래서 ``evidence_reclassify_switch.py`` 의 동결·Enum 장치를 그대로
  옮기지 않고, 호출마다 환경변수를 직접 읽는 가장 단순한 형태로 둔다.
"""

from __future__ import annotations

import os
from typing import Final

#: 근거 결속 재작성을 끄는 환경변수. 선언하지 않으면 켜진 것으로 본다.
GROUNDING_REWRITE_ENV_NAME: Final[str] = "GROUNDING_REWRITE"
#: 정확히 이 값일 때만 꺼진다. 오타나 관용 표기("off" 등)는 켜진 것으로 본다 —
#: 잘못 끄면 기능이 조용히 멈추지만, 잘못 켜져 있으면 실측으로 바로 드러난다.
GROUNDING_REWRITE_ENV_OFF: Final[str] = "0"


def grounding_rewrite_enabled() -> bool:
    """근거 결속 재작성 경로를 이 호출에서 써도 되는가.

    환경변수가 정확히 ``"0"`` 이면 False, 선언하지 않았거나 다른 값이면 True다.
    """

    return os.environ.get(GROUNDING_REWRITE_ENV_NAME) != GROUNDING_REWRITE_ENV_OFF
