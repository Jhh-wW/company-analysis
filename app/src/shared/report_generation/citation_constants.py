"""출력 채널 셋이 함께 쓰는 부록 «인용·출처» 안내 문구.

★ 왜 shared인가 — 「외부 언론 보도 출처가 없다」는 말은 PDF·Notion·웹
  세 채널이 모두 독자에게 보여 주는 문구다. 그런데 문구를 고르는 판정은
  출처 계층(`features.provenance`)이 한다. 문구를 한 채널(`export_pdf`)의
  상수로 두면 출처 계층이 출력 채널을 거꾸로 import하게 된다
  (feature-atomic §2-2 «feature 간 직접 import 금지», §2-4 «둘 이상이
  실제로 쓰면 shared»). 그래서 글자는 여기 한 벌만 두고, 채널 쪽 상수
  모듈은 같은 이름으로 재노출만 한다.
"""

from __future__ import annotations

from typing import Final

CITATIONS_NO_EXTERNAL_NEWS_NOTE: Final[str] = "이 보고서에는 외부 언론 보도 출처가 없습니다."

__all__ = ["CITATIONS_NO_EXTERNAL_NEWS_NOTE"]
