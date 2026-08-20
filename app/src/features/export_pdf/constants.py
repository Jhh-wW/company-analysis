"""PDF 보고서의 웹 브랜드 토큰과 다운로드 계약."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

CONTENT_TYPE_PDF: Final[str] = "application/pdf"
PDF_SUFFIX: Final[str] = ".pdf"
FILENAME_PATTERN: Final[str] = "{company}_분석_보고서" + PDF_SUFFIX
FILENAME_FALLBACK: Final[str] = "보고서"
# Windows 금지 문자뿐 아니라 HTTP 헤더 주입/모호성을 만드는 제어문자와 %도 뺀다.
FILENAME_FORBIDDEN_CHARS: Final[re.Pattern[str]] = re.compile(
    r'[\x00-\x1f\x7f\\/:*?"<>|%]+'
)
FILENAME_ASCII_ALLOWED: Final[re.Pattern[str]] = re.compile(r"[^A-Za-z0-9._-]+")
FILENAME_ASCII_FALLBACK: Final[str] = "analysis-report"
FILENAME_MAX_STEM: Final[int] = 120
WINDOWS_RESERVED_STEM: Final[re.Pattern[str]] = re.compile(
    r"^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?$", re.IGNORECASE
)

FONT_DIR: Final[Path] = Path(__file__).with_name("fonts")
FONT_REGULAR_PATH: Final[Path] = FONT_DIR / "Freesentation-Regular.ttf"
FONT_SEMIBOLD_PATH: Final[Path] = FONT_DIR / "Freesentation-SemiBold.ttf"
FONT_REGULAR: Final[str] = "FreesentationPDF"
FONT_SEMIBOLD: Final[str] = "FreesentationPDF-SemiBold"

# 웹의 흑백 중심 토큰을 문서에서도 그대로 사용한다.
COLOR_INK: Final[str] = "#171717"
COLOR_MUTED: Final[str] = "#5F6770"
COLOR_LINE: Final[str] = "#D8DCE1"
COLOR_SURFACE: Final[str] = "#F4F5F6"
COLOR_HEADER: Final[str] = "#ECEFF1"
COLOR_PARTIAL: Final[str] = "#737373"
COLOR_PARTIAL_FILL: Final[str] = "#F5F5F5"
COLOR_INCOMPLETE: Final[str] = "#5F5F5F"
COLOR_INCOMPLETE_FILL: Final[str] = "#F5F5F5"

PAGE_MARGIN_PT: Final[float] = 17 * 72 / 25.4
PAGE_TOP_MARGIN_PT: Final[float] = 18 * 72 / 25.4
PAGE_BOTTOM_MARGIN_PT: Final[float] = 16 * 72 / 25.4
BODY_FONT_SIZE_PT: Final[float] = 10.4
SMALL_FONT_SIZE_PT: Final[float] = 8.8
TITLE_FONT_SIZE_PT: Final[float] = 30.5
HEADING_FONT_SIZE_PT: Final[float] = 15.5
TABLE_FONT_SIZE_PT: Final[float] = 8.8
BODY_LEADING_PT: Final[float] = 16.0

REQUIREMENTS_CELL: Final[str] = "5"
REQUIREMENTS_NOTE: Final[str] = (
    "공고 원문 그대로입니다. 다듬지 않았습니다 — 자소서에 그대로 옮겨 쓰시라고요."
)
REQUIREMENTS_EMPTY_REASON: Final[str] = "올려주신 공고에서 요구 조건을 뽑지 못했습니다"
EMPTY_PREFIX: Final[str] = "비어 있습니다"
EMPTY_DEFAULT_REASON: Final[str] = "해당 자료를 찾지 못했습니다"
CITATIONS_NOTE: Final[str] = (
    "본문의 번호가 아래 원문을 가리킵니다."
)
SOURCE_STATE_LABEL: Final[dict[str, str]] = {
    "ok": "찾음",
    "none": "없음",
    "failed": "못 가져옴",
}
SOURCE_TABLE_HEADERS: Final[tuple[str, str, str]] = ("소스", "결과", "내용")
