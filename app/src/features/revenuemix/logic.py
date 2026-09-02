"""매출 구성 비중을 공시에서 뜯어 표로 만든다.

★★ **베껴 오는 것뿐이다.** 사업보고서가 비중을 이미 계산해 놓았으므로
  우리는 더하지도 나누지도 않는다. 지어낼 자리가 아예 없다.
⚠️ 비중을 «우리가» 계산하면 반올림 규칙이 공시와 달라져 합이 100%가 안 맞는다.

★ 시계도 네트워크도 AI도 없다. 전부 순수 함수라 통째로 시험할 수 있다.
"""

from __future__ import annotations

import re
from typing import Optional

from src.features.revenuemix.constants import (
    HEADERS,
    MAX_ROWS,
    NAME_NOISE,
    PRODUCT_CAPTION,
    PRODUCT_HEADS,
    REGION_CAPTION,
    REGION_HEADS,
    ROW_RE,
    SCAN_CHARS,
    SUBTOTAL_WORDS,
    TOTAL_WORDS,
)

#: 연도 — 「2025년 제21기」에서 앞의 네 자리.
_YEAR_RE = re.compile(r"(20\d{2})\s*년")


def clean_name(raw: str) -> str:
    """행 이름에서 표 머리말·군더더기를 지운다.

    ★ 표가 «한 줄로 눌린» 글이라 머리말(「매 출 액」·「비 중」)이
      다음 행의 이름 앞에 그대로 붙어 온다. 그대로 두면 표가 못 읽는 글이 된다.
    """
    name = raw
    for 찌꺼기 in NAME_NOISE:
        name = name.replace(찌꺼기, " ")
    name = re.sub(r"\(주\s*\d+\)", " ", name)          # 각주 표시 (주2)
    name = re.sub(r"20\d{2}\s*년|제\s*\d+\s*기|\(\s*(?:당|전|전 전)\s*기\s*\)", " ", name)
    name = re.sub(r"[·\-–—]+\s*$", "", name)
    name = re.sub(r"\s+", " ", name).strip(" ,.()")
    # ★ 여기서 자르지 않는다 (사용자 결정) — 화면·PDF가 줄바꿈으로 흘려 받는다.
    #   ROW_RE 캡처 그룹이 이미 원본을 40자로 묶어 두므로 별도 상한이 없어도
    #   이름이 무한정 길어지지 않는다 (constants.MAX_NAME_CHARS 자리의 주석 참조).
    return name


def _is(name: str, words: tuple[str, ...]) -> bool:
    return any(w in name for w in words)


#: 표 «머리말»의 마지막 칸 — 여기까지 잘라 낸다.
#: ★ 왜 필요한가 — 표가 한 줄로 눌려 있어 머리말(「매 출 액 비 중 …」)이
#:   첫 행 이름 앞에 그대로 붙는다. 실측 — 첫 행이 「**액** 음반/음원 …」으로 나왔다.
_HEADER_TAIL_RE = re.compile(r"비\s*중")

#: 머리말을 찾을 범위(글자). 이보다 뒤의 「비중」은 머리말이 아니라 본문이다.
_HEADER_ZONE: int = 320


def find_block(filing_text: str, heads: tuple[str, ...]) -> tuple[str, str]:
    """표제를 찾아 (머리말, 행 부분)으로 갈라 돌려준다. 못 찾으면 ("", "").

    ★ **머리말을 버리지 않고 돌려준다** — 연도(「2025년 제21기 (당 기)」)가
      거기 있기 때문이다. 그냥 잘라 버렸더니 뒤에 남은 「2023년」을 주워
      **2025년 숫자에 2023년 딱지**가 붙었다 (실측으로 잡힘).
    """
    for head in heads:
        i = filing_text.find(head)
        if i < 0:
            continue
        block = filing_text[i: i + SCAN_CHARS]
        머리 = list(_HEADER_TAIL_RE.finditer(block[:_HEADER_ZONE]))
        if not 머리:
            return "", block
        끝 = 머리[-1].end()
        return block[:끝], block[끝:]
    return "", ""


def parse_rows(block: str) -> tuple[list[list[str]], Optional[str]]:
    """덩어리에서 «첫 해» 행들을 뽑는다.

    Args:
        block: 표제 뒤 덩어리.

    Returns:
        ([[구분, 매출액, 비중]…], 합계행 또는 None).

    ★ 한 행에 3개 연도가 나란히 있다. **맨 앞(당기)만** 쓴다 —
      정규식이 이름 바로 뒤의 첫 「숫자 + 비중」 짝만 물게 되어 있다.
    ★ 소계는 **버린다.** 안 버리면 비중을 다 더했을 때 200%가 된다.
    """
    rows: list[list[str]] = []
    total: Optional[list[str]] = None
    본이름: set[str] = set()
    for m in ROW_RE.finditer(block):
        name = clean_name(m.group(1))
        금액, 비중 = m.group(2), m.group(3) + "%"
        if not name or _is(name, SUBTOTAL_WORDS):
            continue
        if _is(name, TOTAL_WORDS):
            # ★ 합계에서 **멈춘다.** 표 하나가 끝난 자리다.
            #   안 멈추면 바로 뒤에 오는 «다음 표»(지역별)의 행까지 먹는다 —
            #   실측으로 잡혔다: 제품별 표에 「국내·아시아·북미」가 딸려 왔다.
            total = [name, 금액, 비중]
            break
        if name in 본이름:
            continue                      # 같은 이름이 두 번 — 다음 연도 열이다
        본이름.add(name)
        rows.append([name, 금액, 비중])
        if len(rows) >= MAX_ROWS:
            break
    return rows, total


def year_of(block: str) -> str:
    """이 표가 몇 년치인지. 못 찾으면 빈 문자열."""
    m = _YEAR_RE.search(block)
    return m.group(1) if m else ""


def build(filing_text: str, cite: str = "") -> list[dict]:
    """공시 원문에서 매출 구성 표를 만든다.

    Args:
        filing_text: 사업보고서 원문 전체.
        cite: 출처 표기.

    Returns:
        표 정의 목록 (`caption`·`headers`·`rows`·`cite`). 못 찾으면 빈 목록.

    ★ **못 찾으면 빈 목록이다.** 억지로 만들지 않는다 —
      비중을 우리가 계산해서 채우면 그 순간 공시와 어긋난다.
    """
    out: list[dict] = []
    for heads, caption in ((PRODUCT_HEADS, PRODUCT_CAPTION), (REGION_HEADS, REGION_CAPTION)):
        머리, block = find_block(filing_text, heads)
        if not block:
            continue
        rows, total = parse_rows(block)
        if len(rows) < 2:
            continue                      # 한 줄짜리는 「구성」이 아니다
        if total:
            rows = rows + [total]
        # ★ 연도는 «머리말»에서 읽는다 — 거기 첫 연도가 당기다.
        해 = year_of(머리)
        out.append({
            "caption": f"{caption}{f' ({해}년)' if 해 else ''}",
            "headers": list(HEADERS),
            "rows": rows,
            "cite": cite,
        })
    return out
