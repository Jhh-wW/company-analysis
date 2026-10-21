"""현재 자기 서비스 수익원 선언의 정확 구간만 고르는 문법."""
import re

SERVICE_REVENUE_ITEMS_MAX_CHARS = 160
SERVICE_REVENUE_NAME_MAX_CHARS = 70
SERVICE_REVENUE_UNIT_RE = re.compile(r"[^.!?;\n]+(?:[.!?]|(?=$|[;\n]))")
SERVICE_REVENUE_DECLARATION_RE = re.compile(
    r"\s*(?:(?:[①-⑳]|\(\d+\))\s*서비스의\s*제공\s*)?"
    r"(?P<claim>(?:회사|당사)는\s*"
    rf"(?P<items>[^.!?;\n]{{1,{SERVICE_REVENUE_ITEMS_MAX_CHARS}}}?)"
    r"(?:으로부터|로부터|에서)\s*(?:수익|매출)을\s*"
    r"(?:인식합니다|인식한다|인식하고\s*있습니다|인식하고\s*있다)[.!]?)\s*"
)
SERVICE_REVENUE_ITEM_SEPARATOR_RE = re.compile(r",|및|그리고")
SERVICE_REVENUE_ITEM_RE = re.compile(
    rf"(?P<name>[가-힣A-Za-z0-9()·/&-]{{1,{SERVICE_REVENUE_NAME_MAX_CHARS}}}?)(?:서비스|용역)"
)
SERVICE_REVENUE_GENERIC_NAMES = frozenset({
    "기타", "일반", "해당", "상기", "모든", "다양한", "여러", "신규",
})
SERVICE_REVENUE_PAST_RE = re.compile(r"과거|이전|종전|당시")
