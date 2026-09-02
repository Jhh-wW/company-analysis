"""호환 facade: 생산자와 함께 쓰는 회복 정책은 shared 정본을 재export한다.

기존 import 경로를 깨지 않되 새 기능 간 직접 import는 만들지 않는다.
"""

from src.shared.report_recovery import decide_post_validation

__all__ = ["decide_post_validation"]
