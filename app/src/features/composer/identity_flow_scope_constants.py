"""자기정의 표의 공식 칸에 허용하는 표현 분해 규칙."""
import re
from typing import Final

OFFICIAL_CELL_COUNT: Final[int] = 2
# 목록·괄호로 나눈 짧은 공식 표현은 각각 자기 원문에 있어야 한다.
EXPRESSION_SEPARATOR_RE: Final[re.Pattern[str]] = re.compile(
    r"[,;·/|&()\[\]{}]" r"|\s+(?:및|그리고|또는)\s+" r"|(?<=[가-힣])(?:와|과)\s+"
)
EXPRESSION_EDGE_RE: Final[re.Pattern[str]] = re.compile(r"^[\s:：]+|[\s.。:：]+$")
GRAMMATICAL_TAILS: Final[tuple[str, ...]] = (
    "입니다", "이다", "으로", "에게", "에서", "은", "는", "이", "가", "을", "를", "의", "로",
)
ASCII_EDGE_RE: Final[str] = r"[A-Za-z0-9]"
NOMINAL_BOUNDARY_PARTICLE_RE: Final[str] = r"(?:을|를|의)?"
GRAMMATICAL_TOKEN_EDGE_RE: Final[str] = r"\w"
