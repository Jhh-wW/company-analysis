"""뭉개진 재무 줄을 «표»로 되돌린다.

정본: 확정/06_검증/2_규칙/01_칸판정코드.md §기법 D · 결정기록 **D13**

★ 왜 필요한가
  공시 원문은 원래 표(HTML)인데, 글자만 뽑으면서 **칸 구분이 통째로 사라져**
  한 줄로 뭉갰다. 그대로 두면 사람이 못 읽고, 버리면 3번 칸이 영영 빈다.
  **숫자는 문장으로 바꾸지 않고 표로 낸다.**

여기서 되살리는 것은 **전자공시 재무 API 조각** 하나다.
공시 원문 HTML 표 복원은 원본 파일을 다시 읽어야 해서 별도 작업이다.
"""

from __future__ import annotations

import re

from src.features.pipeline.port import ReportTable

#: 재무 API 조각임을 알리는 머리말. 시제품이 붙인 그대로다.
DART_FINANCIAL_PREFIX = "주요계정(DART API):"

#: 항목 구분자. 시제품이 `" · "`로 이어 붙였다.
_ITEM_SEP = " · "

#: `유동자산 76,330,498,769(2025.12.31 현재)` 를 세 조각으로 가른다.
_ITEM = re.compile(
    r"^(?P<name>.+?)\s+(?P<amount>-?[\d,]+)\s*(?:\((?P<when>[^)]*)\))?$"
)

#: 표에 붙는 설명과 열 이름 (화면 문구는 여기가 정본)
FINANCIAL_CAPTION = "전자공시에 신고된 주요 재무계정"
FINANCIAL_HEADERS = ["계정", "금액(원)", "기준"]
FINANCIAL_CITE = "전자공시 재무 API"

#: 이 개수보다 적으면 표로 만들 값어치가 없다 — 한두 줄은 문장이 낫다.
MIN_FINANCIAL_ROWS = 3


def is_financial_line(text: str) -> bool:
    """전자공시 재무 API에서 온 줄인가."""
    return text.strip().startswith(DART_FINANCIAL_PREFIX)


def _format_amount(raw: str) -> str:
    """금액을 사람이 읽기 쉽게 만든다.

    자릿수가 커서 그대로 보면 못 읽는다 — 「억」 단위를 괄호로 덧붙인다.
    ★ 원본 숫자는 «지우지 않는다». 덧붙이기만 한다 (원문 보존).
    """
    try:
        value = int(raw.replace(",", ""))
    except ValueError:
        return raw
    억 = 100_000_000
    if abs(value) < 억:
        return raw
    return f"{raw} ({value / 억:,.0f}억)"


def parse_financial_table(text: str) -> ReportTable | None:
    """뭉개진 재무 줄을 표로 되돌린다.

    Args:
        text: `주요계정(DART API): 유동자산 76,330,498,769(2025.12.31 현재) · …`

    Returns:
        표. 모양이 안 맞거나 항목이 너무 적으면 None (그때는 원래 규칙대로 처리).
    """
    if not is_financial_line(text):
        return None

    body = text.strip()[len(DART_FINANCIAL_PREFIX):].strip()
    rows: list[list[str]] = []
    for chunk in body.split(_ITEM_SEP):
        matched = _ITEM.match(chunk.strip())
        if matched is None:
            # 한 조각이라도 모양이 다르면 표로 만들지 않는다.
            # 억지로 맞추면 «없는 숫자»가 화면에 생긴다.
            return None
        rows.append(
            [
                matched.group("name").strip(),
                _format_amount(matched.group("amount")),
                (matched.group("when") or "").strip(),
            ]
        )

    # 전자공시가 같은 계정을 두 번 내려주는 경우가 있다 (연결·별도가 같은 값일 때).
    # 화면에 똑같은 줄이 두 번 보이면 잘못 만든 표처럼 읽힌다 → 완전히 같은 행만 지운다.
    # ★ 값이 «하나라도» 다르면 남긴다. 다른 숫자를 지우면 사실이 사라진다.
    본_행: list[list[str]] = []
    seen: set[tuple[str, ...]] = set()
    for row in rows:
        key = tuple(row)
        if key in seen:
            continue
        seen.add(key)
        본_행.append(row)
    rows = 본_행

    if len(rows) < MIN_FINANCIAL_ROWS:
        return None

    table = ReportTable(
        caption=FINANCIAL_CAPTION,
        headers=list(FINANCIAL_HEADERS),
        rows=rows,
        cite=FINANCIAL_CITE,
        numeric=True,
    )
    return table if table.is_valid else None
