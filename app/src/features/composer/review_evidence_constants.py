"""검수 화면의 근거 표시와 실제 출처 ID 사이의 닫힌 계약."""

import re

REVIEW_DISPLAY_ID_RE = re.compile(r"조각 ([0-9]+)")
REVIEW_COMPARISON_ID_LIST = r"[0-9]+(?:\s*(?:[,·]|및|와|과)\s*(?:조각\s*)?[0-9]+)*"
REVIEW_COMPARISON_MARKED_ID_RE = re.compile(
    r"(?<![\w])(?:조각|근거(?:\s*ID)?)\s*\[?(?P<ids>(?>" + REVIEW_COMPARISON_ID_LIST + r"))\]?"
    r"(?![0-9]|\s*(?:[.,][0-9]|[%％]|개|년|월|일|원|억|만|천|배|채|건|명))"
)
REVIEW_COMPARISON_LEADING_ID_RE = re.compile(
    r"(?:^|[;；\n|])\s*\[?(?P<ids>(?>" + REVIEW_COMPARISON_ID_LIST + r"))\]?\s*[:：](?!\s*[0-9])"
)
REVIEW_COMPARISON_NUMBER_RE = re.compile(r"[0-9]+")
REVIEW_COMPARISON_YEAR_RE = re.compile(r"[12][0-9]{3}")
TREND_OBSERVATIONS_KEY = "관측"
REVIEW_EVIDENCE_ID_STAGE = "review_evidence_id_binding"
REVIEW_EVIDENCE_ID_GUIDE = (
    "근거 ID 출력: 입력의 ‘조각 1’은 표시 이름이며 실제 ID는 ‘1’이다. "
    "상위 근거 배열과 검증근거 안의 근거 칸에는 실제 ID만 적는다. "
    "각 항목에 배정된 ID를 그대로 쓰고, 원문 인용 안의 글자는 바꾸지 않는다.\n"
)
