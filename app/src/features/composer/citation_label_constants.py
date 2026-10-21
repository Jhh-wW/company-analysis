"""검수 전 작성 응답에서 허용하는 명시적 조각 번호 표기."""
import re

DRAFT_CITATION_LABEL_PATTERN = re.compile(r"조각[ \t]+(?P<fragment_id>[1-9][0-9]*)")
