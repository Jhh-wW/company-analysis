"""공시 당기·전기 비용 비교의 명시 결산기간 결속 규칙."""

import re

COMPARISON_PERIOD_UNBOUND = "disclosure_comparison_period_unbound"
RELATIVE_COMPARISON_RE = re.compile(r"전기\s*(?:대비|보다)|당기(?:에는|에)?")
PAST_COMPARISON_RE = re.compile(r"증가(?:하였|했)|감소(?:하였|했)|늘었|줄었|상승(?:하였|했)|하락(?:하였|했)")
FINANCIAL_METRIC_RE = re.compile(r"(?:판매|지급)수수료|급여|광고선전비|감가상각비|무형자산상각비|이자비용|외환차손")
CURRENT_OR_FUTURE_RE = re.compile(r"현재|지금|계속|있다|있으며|있습니다|예정|계획|전망|향후|앞으로")
CURRENT_HEADER_RE = re.compile(r"당\s*기")
PRIOR_HEADER_RE = re.compile(r"전\s*기")
PERIOD_RE = re.compile(r"(\d{4}-\d{2}-\d{2})/(\d{4}-\d{2}-\d{2})")
OWN_COMPANY_RE = re.compile(r"주식회사|\(주\)|㈜|\s+")
