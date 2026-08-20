"""워드(.docx) 내보내기에 쓰는 값 — 글꼴·색·여백·문구·파일명 규칙.

★ 규칙 — 이 값들을 `logic.py`에 숫자·문자열로 박지 않는다. 전부 여기서만 바꾼다
  (`~/.claude/rules/general.md` 매직 넘버 금지).

정본:
  - 확정/07_출력/1_흐름/01_세형태.md         §워드로 받기 (파일명 규칙)
  - 확정/07_출력/2_규칙/01_배치와근거표기.md  (배치 순서 · 빈칸 사유 문구)

★ 문구는 `src/web/templates/result.html`이 화면에 보여주는 문구와 **한 글자도
  다르지 않아야 한다** (07_출력 성공기준 P3 — 화면 ↔ 워드 ↔ 노션 내용이 다르면 안 됨).
  여기 상수는 그 화면 문구를 그대로 옮긴 것이다.
"""

from __future__ import annotations

import re
from typing import Final

# ── 문서 프리셋 ──────────────────────────────────────────
#: formal report에 맞는 Documents `standard_business_brief`를 기준으로 한다.
#: 기본 Calibri에는 한글 글리프가 없어, 글꼴만 한국어용 맑은 고딕으로 바꾼다.
DOCX_STYLE_PRESET: Final[str] = "standard_business_brief"

# ── 글꼴 ─────────────────────────────────────────────────
#: 한국어 글꼴 예외. 로마자(ascii)·동아시아(eastAsia) 둘 다 이 이름으로 맞춘다.
#: 워드는 둘을 따로 관리해서 한쪽만 지정하면 한글이 문서 기본 서체로 남는다.
FONT_NAME: Final[str] = "맑은 고딕"

FONT_SIZE_TITLE_PT: Final[float] = 30
FONT_SIZE_SUBTITLE_PT: Final[float] = 10.5
FONT_SIZE_HEADING_1_PT: Final[float] = 16
FONT_SIZE_HEADING_PT: Final[float] = 13
FONT_SIZE_HEADING_3_PT: Final[float] = 12
FONT_SIZE_BODY_PT: Final[float] = 11
#: 자료표가 본문보다 조밀해도 읽을 수 있게 유지하는 보고서 전용 예외.
FONT_SIZE_TABLE_PT: Final[float] = 9.5
FONT_SIZE_MUTED_PT: Final[float] = 9

BODY_SPACE_AFTER_PT: Final[float] = 6
BODY_LINE_SPACING: Final[float] = 1.10
HEADING_1_SPACE_BEFORE_PT: Final[float] = 16
HEADING_1_SPACE_AFTER_PT: Final[float] = 8
HEADING_2_SPACE_BEFORE_PT: Final[float] = 12
HEADING_2_SPACE_AFTER_PT: Final[float] = 6
HEADING_3_SPACE_BEFORE_PT: Final[float] = 8
HEADING_3_SPACE_AFTER_PT: Final[float] = 4
BULLET_SPACE_AFTER_PT: Final[float] = 8
BULLET_LINE_SPACING: Final[float] = 1.167
BULLET_MARKER_DXA: Final[int] = 360
BULLET_TEXT_DXA: Final[int] = 720
BULLET_HANGING_DXA: Final[int] = 360

# ── 색 ───────────────────────────────────────────────────
#: 사용자 화면과 같은 무채색만 쓴다. 정보 위계는 크기·굵기·간격으로 만든다.
COLOR_INK_RGB: Final[tuple[int, int, int]] = (23, 23, 23)
COLOR_MUTED_RGB: Final[tuple[int, int, int]] = (95, 95, 95)
COLOR_ACCENT_RGB: Final[tuple[int, int, int]] = COLOR_INK_RGB
COLOR_ACCENT_DARK_RGB: Final[tuple[int, int, int]] = (48, 48, 48)
COLOR_ACCENT_HEX: Final[str] = "171717"
#: 🟡 부분 완성 라벨 색.
COLOR_PARTIAL_RGB: Final[tuple[int, int, int]] = (138, 106, 32)
COLOR_PARTIAL_HEX: Final[str] = "8A6A20"
#: 🔴 미완성 라벨 색.
COLOR_INCOMPLETE_RGB: Final[tuple[int, int, int]] = (153, 65, 63)
COLOR_INCOMPLETE_HEX: Final[str] = "99413F"
#: 표 머리글 줄 배경(회색 음영). `w:shd/@w:fill`에 그대로 쓰는 6자리 헥스값.
COLOR_TABLE_HEADER_HEX: Final[str] = "F2F2F2"
COLOR_TABLE_BORDER_HEX: Final[str] = "E5E5E5"
COLOR_CALLOUT_FILL_HEX: Final[str] = "F4F6F9"
COLOR_PARTIAL_FILL_HEX: Final[str] = "F7F3E9"
COLOR_INCOMPLETE_FILL_HEX: Final[str] = "FFF2F3"

# ── 여백 ─────────────────────────────────────────────────
PAGE_WIDTH_IN: Final[float] = 8.5
PAGE_HEIGHT_IN: Final[float] = 11.0
PAGE_MARGIN_IN: Final[float] = 1.0
HEADER_FOOTER_DISTANCE_IN: Final[float] = 0.492

# ── 표 스타일 ────────────────────────────────────────────
#: 워드 기본 템플릿에 내장된 표 스타일 이름. 테두리가 있어 표가 뭉개지지 않는다.
TABLE_STYLE: Final[str] = "Table Grid"
#: Letter 1인치 여백의 사용 가능 폭. Word 내부 단위(DXA)로 고정한다.
TABLE_WIDTH_DXA: Final[int] = 9360
TABLE_INDENT_DXA: Final[int] = 120
TABLE_CELL_TOP_DXA: Final[int] = 80
TABLE_CELL_BOTTOM_DXA: Final[int] = 80
TABLE_CELL_START_DXA: Final[int] = 120
TABLE_CELL_END_DXA: Final[int] = 120
SOURCES_TABLE_WIDTHS_DXA: Final[tuple[int, int, int, int, int]] = (
    560,
    3180,
    2220,
    1700,
    1700,
)
SUMMARY_TABLE_WIDTHS_DXA: Final[tuple[int, int, int]] = (720, 7440, 1200)
#: 문장 목록에 쓰는 워드 기본 글머리 스타일.
BULLET_STYLE: Final[str] = "List Bullet"

# ── 등급 라벨 — result.html의 grade 블록과 같은 문구·순서 ───
#: `Grade.value` → 라벨 아이콘. result.html의 🟡/🔴 배정과 같다.
GRADE_ICON: Final[dict[str, str]] = {"부분 완성": "🟡", "미완성": "🔴"}
GRADE_COLOR: Final[dict[str, tuple[int, int, int]]] = {
    "부분 완성": COLOR_PARTIAL_RGB,
    "미완성": COLOR_INCOMPLETE_RGB,
}

# ── 빈칸 문구 — result.html의 `<span class="why">비어 있습니다</span> — …` ──
EMPTY_PREFIX: Final[str] = "비어 있습니다"
EMPTY_DEFAULT_REASON: Final[str] = "해당 자료를 찾지 못했습니다"

# ── 요구역량(5번) 안내문 — result.html의 requirements_block()과 같은 문구 ───
REQUIREMENTS_CELL: Final[str] = "5"
REQUIREMENTS_NOTE: Final[str] = (
    "공고 원문 그대로입니다. 다듬지 않았습니다 — 자소서에 그대로 옮겨 쓰시라고요."
)
REQUIREMENTS_EMPTY_REASON: Final[str] = "올려주신 공고에서 요구 조건을 뽑지 못했습니다"

# ── 출처 — result.html의 출처 안내문과 같은 문구 ───────────
HEADING_SOURCES: Final[str] = "부록. 출처와 검증 상태"
CITATIONS_NOTE: Final[str] = "본문의 번호가 아래 원문을 가리킵니다."

# ── 수집 현황 — result.html의 소스별 표와 같은 문구 ─────────
HEADING_COLLECTION: Final[str] = "어디서 가져왔나"
SOURCES_TABLE_HEADERS: Final[tuple[str, str, str]] = ("소스", "결과", "내용")
#: 소스 상태(`SourceStatus.state`) → 화면과 같은 아이콘+문구.
#: 정본: 확정/07_출력/2_규칙/01_배치와근거표기.md §수집 현황
SOURCE_STATE_LABEL: Final[dict[str, str]] = {
    "ok": "⭕ 찾음",
    "none": "❌ 없음",
    "failed": "⚠️ 못 가져옴",
}

# ── 파일명 규칙 ───────────────────────────────────────────
# 정본: 확정/07_출력/1_흐름/01_세형태.md §워드로 받기
#: 워드 파일 확장자. ★ 이게 빠지면 더블클릭해도 워드가 안 열린다.
DOCX_SUFFIX: Final[str] = ".docx"
FILENAME_PATTERN: Final[str] = "{company}_{job}_{date}" + DOCX_SUFFIX
#: 파일명에 못 쓰는 문자 — 있으면 지운다(빈 문자열로 치환). `\ / : * ? " < > |`
FILENAME_FORBIDDEN_CHARS: Final[re.Pattern[str]] = re.compile(r'[\\/:*?"<>|]')
#: 회사명·직무를 전부 지워서 이름이 빌 때 쓰는 자리표시자.
FILENAME_FALLBACK: Final[str] = "보고서"

# ── ASCII 대체 파일명 (문제로그 P-77) ─────────────────────
# ★ HTTP 헤더 값에는 «한글을 못 넣는다». 그래서 한글 파일명은 `filename*`에
#   넣고, 옛 브라우저용으로 ASCII 이름을 `filename=`에 하나 더 넣는다.
#   ⚠️ 그런데 그 ASCII 이름이 지저분하면 «헤더 전체»가 무시되어 파일명이
#     통째로 사라진다. 실제로 크롬에서 이름 없는 파일로 받아진 사고가 났다.
#     (한글을 지우고 남은 「_  R&D _2026-08-15.docx」가 원인)

#: ASCII 대체 이름에 남길 글자 — 영문·숫자·점·밑줄·붙임표만.
#: `&`·따옴표·겹공백처럼 파서를 헷갈리게 하는 글자를 아예 안 남긴다.
FILENAME_ASCII_ALLOWED: Final[re.Pattern[str]] = re.compile(r"[^A-Za-z0-9._-]+")
#: 위 규칙으로 지우고 나서도 쓸 만한 글자가 없을 때 쓰는 «순수 ASCII» 이름.
#: ★ 한글을 쓰면 안 된다 — 헤더에 못 들어간다.
FILENAME_ASCII_FALLBACK: Final[str] = "report"
#: ASCII 대체 이름의 최소 길이(확장자 제외). 이보다 짧으면 뜻이 없어 자리표시자를 쓴다.
FILENAME_ASCII_MIN_STEM: Final[int] = 2

# ── 다운로드 응답 헤더 ────────────────────────────────────
CONTENT_TYPE_DOCX: Final[str] = (
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
)
