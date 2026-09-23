"""같은 공시 문서의 관계법인 회계범위 각주를 «제약으로만» 운반하는 경계의 상수.

사용처: ``entity_scope_constraints``(context 구성)·``grounding``(진단 단계 기록).
"""

from __future__ import annotations

import re
from typing import Final

from src.shared.report_evidence.legacy_fragment_kinds import LEGACY_KIND_ENTITY_SCOPE_FOOTNOTE

#: DART 공시 접수번호 — 14자리 숫자. 공개 정본이 없어(pipeline 쪽은 모듈 내부 상수)
#: 같은 규칙을 이 feature 상수로 둔다.
DART_RECEIPT_NUMBER_RE: Final[re.Pattern[str]] = re.compile(r"[0-9]{14}")
#: 제약으로 운반하는 등록 조각 종류(pipeline real.py가 같은 공시에서 만드는 각주 조각).
ENTITY_SCOPE_CONSTRAINT_KINDS: Final[frozenset[str]] = frozenset({LEGACY_KIND_ENTITY_SCOPE_FOOTNOTE})
#: 같은 신원이어도 두 조각에 «알려진» 값이 서로 다르면 묶지 않는 칸 — 문서 전체 원문
#: 해시(발췌 원문 해시가 아니다)와 보고기간. 빈 값은 추정·생성하지 않고 «모름»으로 둔다.
ENTITY_SCOPE_CONFLICT_FIELDS: Final[tuple[str, ...]] = ("document_content_sha256", "reporting_period")
#: 이 제약으로 탈락했을 때 근거 세부 진단에 남기는 단계 이름. 공개 사유 코드는 기존
#: ``SCOPE_CONDITION_UNBOUND`` 그대로다. ``shared.report_quality.review_diagnostic_constants
#: .GROUNDING_DETAIL_STAGES``에 같은 글자가 있어야 진단에서 버려지지 않는다.
ENTITY_SCOPE_EXCLUSION_STAGE: Final[str] = "entity_scope_exclusion"
