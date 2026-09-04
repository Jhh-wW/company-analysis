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
FONT_REGULAR: Final[str] = "FreesentationPDF"
FONT_SEMIBOLD: Final[str] = "FreesentationPDF-SemiBold"

# 웹의 흑백 중심 토큰을 문서에서도 그대로 사용한다.
COLOR_INK: Final[str] = "#171717"
COLOR_MUTED: Final[str] = "#5F5F5F"
COLOR_WEAK: Final[str] = "#8A8A8A"
COLOR_LINE: Final[str] = "#E5E5E5"
COLOR_SURFACE: Final[str] = "#F5F5F5"
COLOR_HEADER: Final[str] = "#FAFAFA"
COLOR_CHART_DARK: Final[str] = "#0A0A0A"
COLOR_CHART_MID: Final[str] = "#5F5F5F"
COLOR_CHART_LIGHT: Final[str] = "#A3A3A3"
COLOR_CHART_PALE: Final[str] = "#D4D4D4"

#: 구성 도식(100% 누적 막대)의 무채색 계단. «칸 수만큼» 색이 있어야 한다.
#:
#: ★ 왜 넓혔나 (하이브 실측) — 옛 팔레트는 정확히 5색이었고 구성 도식도
#:   3~5행만 그렸다. 하이브 매출은 6개 부문이고 비중 합계가 정확히 100.00%인데,
#:   «행 수»만으로 도식이 안 그려지고 평범한 표로 나갔다. 6번째 색이 없어서
#:   PDF는 IndexError, 웹은 1번 색과 같은 색이 되기 때문이었다.
#:
#: ★ 마지막 칸은 흰색 + 테두리다 — 종이에서 「남은 몫」으로 읽히게 하기 위함.
#:   중간 단계는 균등 간격으로 두어 이웃한 두 칸이 항상 구별된다.
COMPOSITION_PALETTE: Final[tuple[str, ...]] = (
    "#0A0A0A",
    "#3D3D3D",
    "#6B6B6B",
    "#949494",
    "#B8B8B8",
    "#DCDCDC",
    "#FFFFFF",
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
TITLE_FONT_SIZE_PT: Final[float] = 31.0
HEADING_FONT_SIZE_PT: Final[float] = 17.0
TABLE_FONT_SIZE_PT: Final[float] = 7.7
BODY_LEADING_PT: Final[float] = 14.1
CARD_LEADING_PT: Final[float] = 11.8
TABLE_LEADING_PT: Final[float] = 10.0
META_FONT_SIZE_PT: Final[float] = 6.8
# 표지 다음 첫 본문 페이지 맨 위 마스트헤드 — 표지 제목(31pt)보다
# 한 단계 작고 장 제목(17pt)보다 커서, 표지와 겹치지 않으면서도 눈에 띄는
# 좌측 정렬 밴드로 보이게 한다.
MASTHEAD_TITLE_FONT_SIZE_PT: Final[float] = 20.0
MASTHEAD_TITLE_LEADING_PT: Final[float] = 24.0
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
