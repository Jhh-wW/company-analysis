"""캐시를 «왜 못 썼는지»를 부르는 이름 한 벌.

★ 왜 따로 두나 — 조회한 쪽(`store.load_cache_lookup`)과 뒤처리를 하는 쪽
  (`web/generation_singleflight`)이 같은 낱말을 써야 한다. 미적중을 `None`
  하나로 뭉개면 「없어서 못 썼다」와 「오래돼서 못 썼다」가 구분되지 않고,
  뒤처리(오래된 캐시 열쇠 지우기)가 어디에도 붙지 못한다.
"""

from __future__ import annotations

from enum import Enum


class CacheMissReason(Enum):
    """캐시 조회가 본문을 돌려주지 못한 이유.

    값은 무효화 원장(`report_delivery_cache_invalidations.reason_code`)에 그대로
    적히는 닫힌 기계 코드다. 소문자·숫자·밑줄만 쓴다.
    """

    #: 그 사전 신원으로 저장된 캐시 열쇠 자체가 없다.
    NOT_FOUND = "not_found"
    #: 열쇠는 있지만 가리키는 본문이 재사용 한도 나이를 지났다.
    CONTENT_EXPIRED = "content_expired"
