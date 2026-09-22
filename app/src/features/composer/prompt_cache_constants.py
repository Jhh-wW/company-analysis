"""검수 고정 지침 캐시는 반복 수요를 확인한 환경에서만 켠다.

단회 요청은 첫 캐시쓰기 할증 때문에 비용이 늘 수 있어 기본값은 끈다.
문자 수를 모델별 최소 캐시 토큰 수로 간주하지 않는다. 활성화 이후에도
공급자의 실제 생성·읽기 토큰으로 효과를 확인하며 별도 준비 호출은 하지 않는다.
"""

from typing import Final


REVIEW_PROMPT_CACHE_ENV: Final = "COMPOSER_REVIEW_PROMPT_CACHE_ENABLED"
REVIEW_PROMPT_CACHE_DEFAULT_ENABLED: Final = False
REVIEW_PROMPT_CACHE_ENABLED_VALUES: Final = frozenset({"1", "true", "yes", "on"})
