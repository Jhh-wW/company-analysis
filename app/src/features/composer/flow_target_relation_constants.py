"""운영 경로의 전달 대상과 행동을 자기 인용의 같은 절에 결속한다."""
import re
from typing import Final

FLOW_TARGET_RELATION_TYPE: Final[str] = "경로"
FLOW_TARGET_RELATION_UNBOUND: Final[str] = "scope_condition_unbound"
FLOW_TARGET_RELATION_SECTION: Final[str] = "operations_partners"
FLOW_TARGET_ACTION_INDEX: Final[int] = 1
FLOW_TARGET_RECIPIENT_INDEX: Final[int] = 2
FLOW_TARGET_CELL_COUNT: Final[int] = 3
FLOW_TARGET_ACTION_RE = re.compile(r"공급(?!망)|후원|납품|판매|제공|지원|유통|구매|협력")
FLOW_TARGET_LOCAL_ACTION_RE = re.compile(r"공급(?!망)|후원|납품|판매|제공|지원|유통|구매|협력|파트너십")
FLOW_TARGET_ROLE_TAIL_RE = re.compile(r"(?:에게|에는|에|와|과|을|를)?\s*(?:독점\s*)?(?:공급|후원|납품|판매|제공|지원|유통|구매|협력|파트너십).*$")
FLOW_TARGET_ROLE_PARTICLE_RE = re.compile(r"(?:에게|에는|에|와|과|을|를)$")
FLOW_TARGET_ROLE_PREFIX_RE = re.compile(r"^각\s+")
FLOW_TARGET_ROLE_GROUP_RE = re.compile(r"[,，;]")
FLOW_TARGET_PRODUCT_ACTIONS: Final[frozenset[str]] = frozenset({"공급", "납품", "판매", "제공"})
FLOW_TARGET_FOREIGN_SUBJECT_RE = re.compile(r"(?:타사|다른\s*회사|다른\s*기업|경쟁사|협력사|고객사)(?:은|는|이|가)\s*")
FLOW_TARGET_LIST_RE = re.compile(r"\s+및\s+|[·,，;()（）]")
FLOW_TARGET_QUOTED_NAME_RE = re.compile(r"['‘“\"]([^'’”\"\n]{2,80})['’”\"]")
FLOW_TARGET_LIST_TAIL_RE = re.compile(r"\s+등(?:\s|$)")
FLOW_TARGET_CLAUSE_RE = re.compile(
    r"[.!?;\n]+|(?:있으며|있고|있지만|했으며|하였으며|하며|했지만|하지만)\s*,?\s*"
)
FLOW_TARGET_ACTION_SWITCH_RE = re.compile(r"하고\s*,?\s*(?=[‘'\"“]?[가-힣A-Za-z][가-힣A-Za-z0-9_-]*[’'\"”]?(?:에게|에는|에|를|을|와|과|은|는)\s)")
FLOW_TARGET_NEGATION_RE = re.compile(r"^(?:을|를|이|가)?\s*(?:하지\s*않|하지\s*못|하지\s*아니|없|아니)")
FLOW_TARGET_SUBJECT_RE = re.compile(r"^(?:은|는|이|가)(?:\s|$)")
FLOW_TARGET_PASSIVE_RE = re.compile(r"(?:공급|제공|지원|후원)(?:을|를)?\s*받")
FLOW_TARGET_GENERIC_TERMS: Final[frozenset[str]] = frozenset({
    "고객", "고객사", "소비자", "사용자", "기업", "회사", "업체", "시장", "파트너",
})
FLOW_TARGET_MAX_PAIRS: Final[int] = 40
FLOW_TARGET_RELATION_GUIDE: Final[str] = (
    "  전달 대상별 행동 결속 필요: {pairs}\n"
    "  각 대상·행동 쌍마다 검증근거.관계에 유형 「경로」, 대상, 역할값(행동), 근거, 원문을 쓴다. "
    "대상과 행동은 도식의 해당 칸에도 있는 정확 구절을 고르고 원문은 자기 인용의 연속 구절이어야 한다. "
    "같은 문장 안의 다른 절에서 공급·후원과 파트너십 대상을 합치지 않는다. "
    "한 대상의 여러 행동은 각각 그 대상과 동일 절에서 확인한다. 대조근거의 낱말 나열은 증명을 대신하지 않는다.\n"
)
