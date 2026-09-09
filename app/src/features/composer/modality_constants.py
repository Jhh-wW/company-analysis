"""계획 한정 손실 가드의 닫힌 구문과 판정 코드."""
from __future__ import annotations

import re
from typing import Final

MODALITY_PLAN_ASSERTED: Final[str] = "planned_claim_asserted"
MIN_ACTIVITY_ANCHOR_CHARS: Final[int] = 2
MAX_ACTIVITY_STEM_CHARS: Final[int] = 24
QUOTE_CHARACTERS: Final[str] = "\"'“”‘’「」『』〈〉《》"
CLAUSE_BREAK_RE = re.compile(r"[.!?;。\n]+|[,，]")
CITATION_RE = re.compile(r"\[\d+(?:\s*,\s*\d+)*\]")
TOKEN_RE = re.compile(r"[가-힣A-Za-z0-9]+")
PARTICLE_RE = re.compile(r"(?:으로|에서|에게|까지|부터|을|를|은|는|이|가|의|에)$")
TOPIC_RE = re.compile(r"(?:^|\s)([가-힣A-Za-z0-9]+?)(?:은|는)\s")
# 관형형의 끝을 주제 조사로 오인해 '없', '지원하'를 주체로 쓰지 않는다.
RELATIVE_TOPIC_STEM_RE = re.compile(r"(?:[가-힣]*(?:하|되|있|없)|많|같|높|낮)")
GENERIC_SUBJECTS: Final[frozenset[str]] = frozenset({"회사", "기업", "당사", "당행", "자사", "이", "그", "저희"})
REPORT_COMPANY_SUBJECT: Final[str] = "__report_company__"
GENERIC_ACTIVITY_OBJECTS: Final[frozenset[str]] = frozenset({"서비스", "제품", "상품", "시장", "사업", "매장", "영역", "고객"})
ACTIVITY_MODIFIERS: Final[frozenset[str]] = frozenset({
    "현재", "이미", "앞으로", "향후", "계속", "지속", "지속적으로", "적극적으로",
    "본격적으로", "성공적으로", "직접", "추가로", "더욱", "함께", "새롭게", "점차",
})

# 하다 계열의 활동 명사만 다룬다. 임의 동의어나 복잡한 시제는 추측하지 않는다.
ACTIVITY_STEM_PATTERN: Final[str] = rf"(?P<stem>[가-힣]{{2,{MAX_ACTIVITY_STEM_CHARS}}}?)"
# 역할을 한다는 우회 표현도 같은 활동으로 대조하되 역할 자체의 목표는 보존한다.
ROLE_PLAN_TAIL_PATTERN: Final[str] = (
    r"하는\s*역할을\s*(?:수행)?(?:할\s*(?:계획|예정|방침)|하고자|하려고|"
    r"한다는\s*(?:계획|목표|방침))"
)
ROLE_ASSERTED_TAIL_PATTERN: Final[str] = (
    r"하는\s*역할을\s*(?:수행)?(?:하고\s*있|해\s*왔|하였|했|합니다|한다)"
)
PLAN_TAIL_PATTERN: Final[str] = (
    r"(?:해\s*나가고자|해\s*나갈\s*(?:계획|예정)|하고자|하려고|하려는\s*(?:계획|목표)|"
    r"할\s*(?:계획|예정|방침)|하기\s*위해|하기를\s*(?:목표|희망)|"
    r"하는\s*것을?\s*목표|하는\s*것이\s*목표|한다는\s*(?:계획|목표|방침)|"
    r"(?:을|를)\s*(?:목표|계획)(?:로|으로)|(?:이|가)\s*(?:목표|계획)|"
    + ROLE_PLAN_TAIL_PATTERN + r")"
)
ASSERTED_TAIL_PATTERN: Final[str] = (
    r"(?:해\s*나가고\s*있|하고\s*있|해\s*왔|하였|했|합니다|한다|한\s*바\s*있|"
    + ROLE_ASSERTED_TAIL_PATTERN + r")"
)
PLAN_RE = re.compile(r"(?<![가-힣])" + ACTIVITY_STEM_PATTERN + r"\s*" + PLAN_TAIL_PATTERN)
ASSERTED_RE = re.compile(r"(?<![가-힣])" + ACTIVITY_STEM_PATTERN + r"\s*" + ASSERTED_TAIL_PATTERN)
