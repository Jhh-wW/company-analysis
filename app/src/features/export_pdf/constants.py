"""PDF 보고서의 웹 브랜드 토큰과 다운로드 계약."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Final

CONTENT_TYPE_PDF: Final[str] = "application/pdf"
PDF_SUFFIX: Final[str] = ".pdf"
FILENAME_PATTERN: Final[str] = "{company_slug}-company-analysis" + PDF_SUFFIX
FILENAME_FALLBACK: Final[str] = "company"
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
FONT_FALLBACK_PATH: Final[Path] = FONT_DIR / "NotoSansKR-Regular.ttf"
FONT_REGULAR: Final[str] = "FreesentationPDF"
FONT_SEMIBOLD: Final[str] = "FreesentationPDF-SemiBold"
FONT_FALLBACK: Final[str] = "NotoSansKRPDF"
FONT_FALLBACK_MAX_BYTES: Final[int] = 6 * 1024 * 1024

# 디자인 토큰 v1의 무채색 팔레트. 웹도 같은 값을 사용해야 한다.
COLOR_INK: Final[str] = "#111111"
COLOR_MUTED: Final[str] = "#666666"
COLOR_WEAK: Final[str] = "#999999"
COLOR_LINE: Final[str] = "#CCCCCC"
COLOR_SURFACE: Final[str] = "#F5F5F5"
COLOR_HEADER: Final[str] = COLOR_INK
COLOR_CHART_DARK: Final[str] = "#222222"
COLOR_CHART_DEEP: Final[str] = "#444444"
COLOR_CHART_MID: Final[str] = "#666666"
COLOR_CHART_PALE: Final[str] = "#8C8C8C"
COLOR_CHART_LIGHT: Final[str] = "#B3B3B3"
CHART_PALETTE: Final[tuple[str, ...]] = (
    COLOR_CHART_LIGHT,
    COLOR_CHART_PALE,
    COLOR_CHART_MID,
    COLOR_CHART_DEEP,
    COLOR_CHART_DARK,
)

#: 구성 도식 계약이 최대 7개 항목을 받으므로 호환용으로 보간한 7단계 팔레트다.
#: 실제 그리기는 ``CHART_PALETTE``를 항목 수에 맞춰 밝은색→어두운색으로 고르게
#: 뽑는다. 이 상수의 길이는 report_standard의 기존 7항목 계약을 유지한다.
COMPOSITION_PALETTE: Final[tuple[str, ...]] = (
    "#B3B3B3",
    "#A0A0A0",
    "#8C8C8C",
    "#666666",
    "#555555",
    "#444444",
    "#222222",
)
COLOR_RISK: Final[str] = "#E7000B"
COLOR_PARTIAL: Final[str] = "#737373"
COLOR_PARTIAL_FILL: Final[str] = "#F5F5F5"
COLOR_INCOMPLETE: Final[str] = "#5F5F5F"
COLOR_INCOMPLETE_FILL: Final[str] = "#F5F5F5"

PAGE_MARGIN_PT: Final[float] = 17 * 72 / 25.4
PAGE_TOP_MARGIN_PT: Final[float] = 18 * 72 / 25.4
PAGE_BOTTOM_MARGIN_PT: Final[float] = 16 * 72 / 25.4
BODY_FONT_SIZE_PT: Final[float] = 9.4
SUBHEADING_FONT_SIZE_PT: Final[float] = 11.0
CARD_FONT_SIZE_PT: Final[float] = 8.4
SMALL_FONT_SIZE_PT: Final[float] = 7.2
TITLE_FONT_SIZE_PT: Final[float] = 34.0
HEADING_FONT_SIZE_PT: Final[float] = 20.0
TABLE_FONT_SIZE_PT: Final[float] = 7.7
BODY_LEADING_PT: Final[float] = 14.1
CARD_LEADING_PT: Final[float] = 11.8
TABLE_LEADING_PT: Final[float] = 10.0
META_FONT_SIZE_PT: Final[float] = 6.8
# 표지 다음 첫 본문 페이지 맨 위 마스트헤드 — 표지 제목(34pt)보다
# 한 단계 작고 장 제목(20pt)보다 커서, 표지와 겹치지 않으면서도 눈에 띄는
# 좌측 정렬 밴드로 보이게 한다.
MASTHEAD_TITLE_FONT_SIZE_PT: Final[float] = 24.0
MASTHEAD_TITLE_LEADING_PT: Final[float] = 28.0
PAGE_HEADER_HEIGHT_PT: Final[float] = 22.0
PAGE_HEADER_FONT_SIZE_PT: Final[float] = 8.0
SECTION_BADGE_SIZE_PT: Final[float] = 18.0
SECTION_APPENDIX_BADGE_WIDTH_PT: Final[float] = 26.0
SECTION_BADGE_FONT_SIZE_PT: Final[float] = 9.0
#: 위첨자 배지 글자는 네모 가운데보다 약 2.6pt 위에 찍힌다(픽셀 실측). 네모를 그만큼 올려
#: 글자가 네모 «가운데»에 오게 한다. 글자 자체는 움직이지 않아 추출 글자가 그대로다.
SECTION_BADGE_TEXT_LIFT_PT: Final[float] = 2.6
COVER_METRIC_VALUE_FONT_SIZE_PT: Final[float] = 22.0
PDF_AUTHOR: Final[str] = "기업분석2"

# PDFium은 한 프로세스에서 동시에 문서/page를 닫을 때 Windows 네이티브
# 중단점 예외를 낼 수 있다. 유료 조사 두 건이 겹쳐도 렌더 증거 확정은 한 번씩
# 실행하며, 고장 난 렌더 하나가 뒤 작업을 영원히 붙들지는 못하게 한다.
PDFIUM_RENDER_LOCK_TIMEOUT_SEC: Final[float] = 180.0

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
