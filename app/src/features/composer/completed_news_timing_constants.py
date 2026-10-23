"""완료된 보도 사건의 확인 시점을 운반하는 닫힌 표현."""

import re

ISO_NEWS_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
NEWS_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=\.)\s+")
REVIEWED_ACRONYM_RE = re.compile(r"\b[가-힣]+\(([A-Z][A-Z0-9-]*)\)")
COMPLETED_NEWS_EVENT_RE = re.compile(
    r"[^.!?;\n]+(?:체결|도입)(?:했다|하였다)\.\Z"
)
NEWS_EVENT_UNCERTAIN_OR_CURRENT_RE = re.compile(
    r"만약|가정|가설|경우|조건|예정|계획|전망|예상|가능성|검토|추진\s*중|"
    r"(?:다|라|으)면|(?:아니|않|못)|현재|유지|계속|지속|있(?:다|고|으며|어)|"
    r"\s중(?:\W|으로|이|인|에|$)|하는\s*한편|"
    r"(?:하지만|했지만|하면서|하며|하고)\s"
)
