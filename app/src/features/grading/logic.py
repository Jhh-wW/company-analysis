"""보고서를 얼마나 채웠는지 등급을 매긴다.


★ 중요 — 여기서 「폐기」는 없다. 미달이어도 보고서는 나간다.
  아무것도 못 주는 종료는 04 게이트의 중단 하나뿐이다.
"""

from __future__ import annotations

import re

from src.core.constants import (
    COUNTED_CELLS,
    MIN_FILLED_CELLS,
    MONEY_NUMBER_PATTERN,
    PARTIAL_MIN_CELLS,
    REQUIRED_CELL,
    SITUATION_CELLS,
    TABLE_HEADER_MIN,
    TABLE_UNIT_RE,
    TABLE_HEADER_TOKENS,
    TABLE_LENGTH_MIN,
    TABLE_NUMBER_MIN,
)
from src.core.docshape import is_table_of_contents
from src.features.grading.constants import (
    ACCOUNTING_POLICY_PATTERNS,
    ACCOUNTING_STANDARD_TERMS,
)
from src.features.pipeline.port import Grade

_MONEY_NUMBER = re.compile(MONEY_NUMBER_PATTERN)
_POLICY_PATTERNS = tuple(re.compile(p) for p in ACCOUNTING_POLICY_PATTERNS)


def count_table_headers(text: str) -> int:
    """표에만 쓰이는 머리말이 몇 개 들어 있나 (「(단위:」·「구분」·「당기」 …).

    ★ 단위 표기는 **띄어쓰기를 허용**해 센다 (문제로그 P-101) —
      실제 공시는 「(단위 : 천원)」처럼 공백을 넣는데, 글자 그대로 대조하면
      그게 안 잡혀 머리말이 1개만 세어지고 문턱(2개)에 미달한다.
    ⚠️ 다른 머리말까지 공백 무시로 바꾸면 안 된다 — 「구 분」이 「구분하여」에 걸려
      멀쩡한 문장을 버린다. 실측으로 확인했다.
    """
    count = sum(1 for token in TABLE_HEADER_TOKENS if token in text)
    if TABLE_UNIT_RE.search(text) and "(단위:" not in text:
        count += 1          # 공백이 들어간 단위 표기 — 위 목록이 못 잡은 몫
    return count


def is_table_dump(text: str) -> bool:
    """공시 표가 문장이 못 된 채 통째로 들어온 줄인가.

    사람이 읽을 수 없는 숫자 나열은 **채워진 것으로 세지 않는다.**
    세면 자소서에 한 글자도 못 쓰는 칸이 성립 판정을 통과시킨다 (문제로그 P-29).

    표로 보는 길이 **두 갈래**다. 하나만으로는 다 못 잡는다.

    ① **긴 표** — 길이와 숫자 개수를 «둘 다» 넘긴다.
       한쪽만 보면 「매출 5,363억원, 영업이익 2,144억원을 달성했습니다」 같은
       정상 문장까지 버리게 된다.
    ② **짧은 표** — 표 머리말이 2개 이상 (문제로그 P-36).
       실측에서 **87자·숫자 2개**짜리 표가 ①을 그대로 통과해 9번 칸에 실렸다.
    """
    # ★ 목차 줄도 «표와 같은 부류»로 막는다 (문제로그 P-102).
    #   왜 여기에 거나 — 목차를 고치는 곳(`filingclean.repair`)은 **못 고칠 수 있다.**
    #   못 고치면 목차가 그대로 남는데, 목차 줄은
    #     · 한글이 있어서 `is_unusable_candidate`(한글 없으면 버림)에 «안» 걸리고
    #     · 쪽번호에 천 단위 쉼표가 없어서 아래 숫자 신호에도 «안» 걸린다.
    #   즉 **최종 방어선을 통째로 통과해 보고서에 실린다.** 실제로 뚫리는 것을 확인했다.
    #   정본이 못 박은 원칙 그대로다 — 「형태 검사를 «최종 판정»에도 반드시 건다」
    #   (P-22 사고 사례).
    if is_table_of_contents(text):
        return True
    if count_table_headers(text) >= TABLE_HEADER_MIN:
        return True
    if len(text) < TABLE_LENGTH_MIN:
        return False
    return len(_MONEY_NUMBER.findall(text)) >= TABLE_NUMBER_MIN


def is_accounting_policy(text: str) -> bool:
    """회계기준을 «어떻게 적용하는지» 설명하는 문구인가 (문제로그 P-40).

    「수익인식모형 연결회사의 고객과의 계약에서 생기는 수익은 …」처럼
    **회사 이름을 바꿔도 그대로 말이 되는** 문장이 1번 칸에 실렸다.
    자소서에 한 글자도 못 쓰는 문장이라 화면에 내보내기 전에 거른다.

    보는 길이 **두 갈래**다.

    ① **결정적 낱말** — 「수행의무」·「공정가치」처럼 회계 주석에만 나오는 말.
       사업 설명·실적 문장에는 절대 안 나와서 하나만 있어도 회계 문구다.
    ② **적용 설명 문형** — 「수익으로 인식하고 있습니다」.
       회계 대상어 + 조사 + 인식/측정 동사의 **종결형**이 붙는 모양이다.

    ★ 낱말을 세지 않는 이유 — 「매출」·「수익」이 들었다고 거르면
      「매출액 5,363억원 … 실적을 달성하였습니다」 같은 **가장 좋은 문장**이 죽는다.
      「수익을 **창출**」은 살리고 「수익으로 **인식**」만 거른다.

    ★ 방침은 「느슨하게, 애매하면 통과」다.
      **오거부가 최대 실패 유형**이라 확신이 없으면 통과시킨다.

    Args:
        text: 보고서에 실릴 문장 한 줄.

    Returns:
        회계기준 설명 문구면 True. 애매하면 False(통과).
    """
    if not text:
        return False
    if any(term in text for term in ACCOUNTING_STANDARD_TERMS):
        return True
    return any(pattern.search(text) for pattern in _POLICY_PATTERNS)


def count_filled(cells: dict[str, bool]) -> int:
    """세는 칸 6개 중 채워진 개수.

    공고 블록(5·8)과 화면에서 숨긴 6·7·9·附는 세지 않는다.
    화면·등급·관측의 분모를 같은 여섯 칸으로 유지한다(P-119).
    """
    return sum(1 for cell in COUNTED_CELLS if cells.get(cell, False))


def grade_of(cells: dict[str, bool]) -> tuple[Grade, list[str]]:
    """칸 채움 상태로 등급과 미달 사유를 낸다.

    Args:
        cells: 칸 번호 → 채워졌는지.

    Returns:
        (등급, 미달 사유 목록). 완성이면 사유는 빈 목록.
    """
    filled = count_filled(cells)
    reasons: list[str] = []

    # 성립 3조건 — 셋을 다 넘겨야 완성이다.
    if filled < MIN_FILLED_CELLS:
        reasons.append(f"채워진 항목이 {filled}개입니다 ({MIN_FILLED_CELLS}개 이상 필요)")
    if not cells.get(REQUIRED_CELL, False):
        reasons.append("「뭘 팔아서 돈 버나」가 비었습니다")
    if not any(cells.get(c, False) for c in SITUATION_CELLS):
        reasons.append("「지금 이 회사 상황」이 통째로 비었습니다")

    if not reasons:
        return Grade.COMPLETE, []
    if filled >= PARTIAL_MIN_CELLS:
        return Grade.PARTIAL, reasons
    return Grade.INCOMPLETE, reasons


def grade_message(grade: Grade, filled: int, *, total: int | None = None) -> str:
    """등급에 맞는 안내 문구.

    사용자에게 보여줄 말이라 내부 표기(O0 등)를 쓰지 않는다.
    """
    total = len(COUNTED_CELLS) if total is None else max(0, int(total))
    if grade is Grade.COMPLETE:
        return ""
    if grade is Grade.PARTIAL:
        return (
            f"자료가 부족해 일부만 채웠습니다 ({total}개 중 {filled}개). "
            "비어 있는 항목에는 왜 비었는지를 적어두었습니다."
        )
    return (
        f"이 회사는 공개된 자료가 거의 없습니다 ({total}개 중 {filled}개). "
        "채워진 부분만 참고하시고, 나머지는 회사 홈페이지나 면접에서 직접 확인하세요."
    )
