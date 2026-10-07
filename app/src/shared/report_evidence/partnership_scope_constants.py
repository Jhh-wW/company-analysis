"""조사 수행자와 제휴 상대를 구분하는 좁은 원문 문법."""
import re

PARTNERSHIP_SLOT = "operations_partners:partnership"
OPERATING_ROLE_SLOT = "operations_partners:operating_role"
PARTNERSHIP_RESEARCH_PROBLEM = "scope_condition_unbound"
PARTNERSHIP_REROUTED = "research_partnership_rerouted"
PARTNERSHIP_EXCLUDED = "research_partnership_unverified"
SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[.!?。！？])\s+|[;；\r\n]+")
ACTIVITY_BOUNDARY_RE = re.compile(
    r"(?<=했으며)\s+|(?<=했고)\s+|(?<=하였으며)\s+|(?<=하였고)\s+|"
    r"(?<=했지만)\s+|(?<=했으나)\s+|(?<=하였지만)\s+|(?<=하였으나)\s+|"
    r"(?<=하고)\s+|(?<=하며)\s+"
)
# 조사·분석 결과를 만들거나 공개하는 수행만 대상으로 한다. 연구개발 일반은 제외한다.
RESEARCH_ACTION_RE = re.compile(
    r"(?:전수\s*조사|설문\s*조사|실태\s*조사|(?:공시|사업보고서|보고서).{0,80}?조사)"
    r"(?:를|을)?\s*(?:해|할|하|했|한|합|하여|실시|수행|진행)|"
    r"(?:조사|분석)\s*결과(?:를|을)?\s*(?:발표|공개)"
)
RELATION_RE = re.compile(r"제휴|협약|협력|공동|함께|파트너십|컨소시엄")
JOINT_RESEARCH_RE = re.compile(
    r"(?:와|과)\s*(?:함께|공동(?:으로)?)\s*"
    r"[^.!?;\r\n]{0,60}?(?:조사|분석)|"
    r"(?:와|과)\s+[^,;.!?\r\n]{1,40}?(?:은|는|이|가)\s*(?:함께|공동(?:으로)?)"
    r"[^.!?;\r\n]{0,60}?(?:조사|분석)|"
    r"(?:와|과)\s*(?:조사|분석)\s*(?:협약|협력|제휴)"
)
NEGATED_RELATION_RE = re.compile(r"공동\s*(?:조사|분석)?\s*(?:가|이)?\s*아(?:니|닌)|함께\s*하지\s*않|제휴.{0,8}없")
ACTOR_PREFIX_RE = re.compile(r"^\s*(?:또한\s*|그리고\s*)?(.+?)(?:은|는|이|가)\s+")
UNPERFORMED_RESEARCH_TAIL_RE = re.compile(
    r"^\s*(?:하지\s*않|하지\s*못|지\s*않|지\s*못|되지\s*않|되지\s*못|"
    r"할\s*(?:예정|계획)|할\s*것|(?:예정|계획)(?:이다|입니다)|"
    r"(?:경우|계획)(?:에|이)|하려|하겠|할\s*경우|한다면)"
)
