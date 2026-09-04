"""칸 판정 코드 — 7번 게이트와 10번 검증이 같이 부른다 (문자열 연산, 0원).

성립 세기(O0)는 **7칸**(附 제외 — 표시 전용) 중 4칸 + 1번 O + 4번 중 1개.
"""
from __future__ import annotations

import datetime as dt
import re

# ── 기법 A: 「없음」 표현 사전 (실측으로 계속 늘린다) ──────────────
EMPTY_PATTERNS = (
    "해당사항 없음", "해당사항이 없습니다", "해당 없음",
    "기재를 생략", "작성하지 아니",
)
MIN_CONTENT_CHARS = 50  # 「없음」 문구를 빼고 이만큼도 안 남으면 빈 섹션으로 본다

# ── 기법 B: 형태 검사 ─────────────────────────────────────────────
AMOUNT_RE = re.compile(r"\d{1,3}(?:,\d{3})+")            # 천 단위 콤마 금액
CORP_NAME_RE = re.compile(r"\(주\)|㈜|주식회사|Co\.|Inc\.")
FACILITY_ACCOUNTS = ("기계장치", "시설장치", "공구와기구", "구축물", "건설중인자산")
MIN_FACILITY_AMOUNTS = 2      # 생산설비 계정 + 금액 2개 이상 (토지·건물만이면 X)
NEWS_MAX_YEARS = 3            # 뉴스 상한 — O9 정본과 동일
NEWS_MAX_OTHER_COMPANIES = 3  # 제목에 타사 3개 이상 = 종목 나열 기사
HOMONYM_THRESHOLD = 2         # 동명 법인 2곳 이상이면 본문 고유 단서 검사 (조건 5)

# ── 성립 세기 — 7칸 (附은 표시 전용이라 안 센다) ──────────────────
COUNTED_CELLS = ("1", "2", "3", "4-1", "4-2", "4-3", "9")
MIN_FILLED = 4
REQUIRED_CELL = "1"
ANY_OF_CELLS = ("4-1", "4-2", "4-3")


def section_is_empty(text: str) -> bool:
    """섹션이 있어도 「해당사항 없음」류면 빈 것으로 본다 (정원엔시스 실측)."""
    remaining = text
    for pattern in EMPTY_PATTERNS:
        remaining = remaining.replace(pattern, "")
    has_empty_marker = any(p in text for p in EMPTY_PATTERNS)
    return has_empty_marker and len(re.sub(r"[\s\-|·.0-9]", "", remaining)) < MIN_CONTENT_CHARS


def count_amounts(text: str) -> int:
    return len(AMOUNT_RE.findall(text))


def has_corp_name(text: str) -> bool:
    return CORP_NAME_RE.search(text) is not None


def facility_ok(text: str) -> bool:
    """2번 칸 — 생산설비 계정이 있고 금액이 2개 이상이어야 O (토지·건물만이면 X)."""
    return any(acc in text for acc in FACILITY_ACCOUNTS) and count_amounts(text) >= MIN_FACILITY_AMOUNTS


def news_ok(title: str, company: str, published: dt.date, today: dt.date,
            other_companies_in_title: int,
            homonym_count: int = 1, identity_hint_found: bool = False) -> bool:
    """뉴스 채택 조건 5개 (소스정책 정본).

    1~4: 제목에 회사명 · 3년 이내 · 타사 3개 미만.
    5 (나중에 더한 조건): **동명 법인이 2곳 이상이면** 기사 본문에 회사 고유 단서
    (대표자·지역·제품 중 1개)가 확인돼야 채택 — 동명 타사 기사 차단.
    """
    fresh = (today - published).days <= NEWS_MAX_YEARS * 365
    base = (company in title) and fresh and (other_companies_in_title < NEWS_MAX_OTHER_COMPANIES)
    if homonym_count >= HOMONYM_THRESHOLD:
        return base and identity_hint_found
    return base


def count_filled(cells: dict[str, bool]) -> int:
    """세는 칸 7개만 센다. 附이 dict에 있어도 무시한다."""
    return sum(1 for cell in COUNTED_CELLS if cells.get(cell, False))


def establishment(cells: dict[str, bool]) -> tuple[bool, list[str]]:
    """성립 3조건 (and) — 미달 사유 목록을 함께 돌려준다."""
    reasons: list[str] = []
    filled = count_filled(cells)
    if filled < MIN_FILLED:
        reasons.append(f"조건1 미달: 채워진 칸 {filled}/{len(COUNTED_CELLS)} (< {MIN_FILLED})")
    if not cells.get(REQUIRED_CELL, False):
        reasons.append("조건2 미달: 1번(뭘 팔아 돈 버나) 비어 있음")
    if not any(cells.get(c, False) for c in ANY_OF_CELLS):
        reasons.append("조건3 미달: 4-1·4-2·4-3 전부 비어 있음")
    return (not reasons), reasons
