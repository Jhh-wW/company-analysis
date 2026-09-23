"""도식 검수 결과의 공개 결속 계약."""

from typing import Final

FLOW_REVIEW_RULE_VERSION: Final[str] = "flow-review-binding-v1"
FLOW_REVIEW_PATHS: Final[frozenset[str]] = frozenset({"legacy", "grouped"})
FLOW_REVIEW_BINDING_MISSING: Final[str] = "flow_review_binding_missing"
FLOW_REVIEW_BINDING_INVALID: Final[str] = "flow_review_binding_invalid"
