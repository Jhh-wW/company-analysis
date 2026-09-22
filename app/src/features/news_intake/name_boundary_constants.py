"""공개 기사로 재현한 전체 회사명 뒤 조사 경계의 제한된 보완."""

from typing import Final


# '뤼튼테크놀로지스도'의 보조사만 추가한다. 조사 다음 경계도 반드시 확인한다.
# 짧은 브랜드명이나 나머지 법인명 접미사를 추측하는 목록이 아니다.
ADDITIONAL_NAME_PARTICLE_PATTERN: Final[str] = r"(?:도)"
