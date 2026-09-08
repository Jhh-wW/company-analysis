"""작성기와 실행기가 공유하는 원문 없는 검수 진단의 전송 계약."""

import re

from src.shared.report_quality.constants import STRICT_REQUIRED_QUALITY_SECTION_IDS

REVIEW_SECTION_IDS = frozenset((*STRICT_REQUIRED_QUALITY_SECTION_IDS, "summary"))
REVIEW_KINDS = ("본문", "요약", "도식")
REVIEW_REASONS = ("semantic_grounding_missing", "semantic_grounding_invalid")
REVIEW_ITEMS = ("수치", "추세", "시점")
CANDIDATE_FINGERPRINT_RE = re.compile(r"[0-9a-f]{64}")
