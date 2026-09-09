# -*- coding: utf-8 -*-
"""후보가 «누가 무엇을 하는가»·«무엇에 대가를 받는가»라고 적은 자리만 좁게 보는 상수.

여기 담는 것은 회사명·상품명·업종별 정답이 아니라 **한국어 문법 표지의 닫힌 목록**뿐이다.
목록 밖 표현은 이 검사가 아무 판정도 하지 않는다 — 통과가 아니라 «확인하지 못함»이다.
"""

from typing import Final

import re

# ══════════════════════════════════════════════════════════
# ① 후보가 «역할»을 단언한 자리 — 발동 표지
# ══════════════════════════════════════════════════════════
#
# ★ 실측 반례: 원문은 「내부 판매조직은 뮤직비즈니스 Center」라고만 적었는데 보고서는
#   도식 칸에 「기획·제작 및 유통」을, 본문에 「Center를 통해 기획·제작되며」를 적었다.
#   원문이 그 대상에 준 역할(판매)이 다른 역할(기획·제작)로 바뀐 자리다.
#: 이 검사가 보는 역할 낱말. 닫힌 목록이며 늘리면 정상 문장까지 결속을 요구한다.
ROLE_WORDS: Final[tuple[str, ...]] = ("기획", "제작", "개발", "제조", "생산")
_ROLE_ALT: Final[str] = "|".join(ROLE_WORDS)
#: 도식 칸은 «칸 하나가 하나의 주장»이라 낱말만으로 발동한다.
ROLE_CELL_MARKER_RE: Final[re.Pattern[str]] = re.compile(r"(" + _ROLE_ALT + r")")
#: 산문은 그 낱말이 «서술어로 쓰였을 때»만 발동한다.
#:
#: ★ 명사 나열(「공연 기획, 영상 콘텐츠 제작」·「자체 개발 소비자 조사 데이터」·
#:   「크리에이티브한 광고 제작 역량」)까지 결속을 요구하면 정상 문장이 대량으로
#:   지워진다. 그래서 역할 낱말 «바로 뒤»에 용언 어간이 붙은 꼴만 본다.
PROSE_ROLE_MARKER_RE: Final[re.Pattern[str]] = re.compile(
    r"(" + _ROLE_ALT + r")(?:되|돼|됐|된|하|해|했|한|할|함|합|하여|되어|"
    r"(?:을|를)\s*(?:담당|수행|영위))"
)

# ══════════════════════════════════════════════════════════
# ② 후보가 «대가·반복»을 단언한 자리 — 발동 표지
# ══════════════════════════════════════════════════════════
#
# ★ 실측 반례: 「기존 팬층 재구매」(원문은 「기확보된 기존 가수들의 넓은 구매층을
#   이용」), 「기존 고객사와의 지속적 협력」(원문에서 확인 안 됨), 「우대금리, 수수료
#   면제, 재예치」(우대금리·수수료 면제는 «다른 상품» 설명이고 재예치는 확인 안 됨).
#: 대가의 «방식»을 말하는 낱말. 매출·계약의 존재는 여기 들어가지 않는다.
FEE_WORDS: Final[tuple[str, ...]] = (
    "수수료", "로열티", "요금", "보수", "대가", "수취", "우대금리", "면제", "배분",
)
#: 거래가 «다시 일어난다»는 뜻의 낱말.
REPEAT_WORDS: Final[tuple[str, ...]] = (
    "재구매", "재예치", "재가입", "재계약", "재유치", "재이용",
    "반복", "지속", "후속", "갱신", "연장",
)
#: ★ 표면형은 띄어쓰기를 지운 뒤 찾으므로 낱말 경계를 걸쳐 우연히 맞는 자리가 생긴다.
#:   「유지보수」의 «보수»는 대가가 아니라 정비다 — 닫힌 예외로 뺀다.
_NOT_FEE_PREFIX: Final[str] = r"(?<!유지)"
FEE_CELL_MARKER_RE: Final[re.Pattern[str]] = re.compile(
    _NOT_FEE_PREFIX + r"(" + "|".join(FEE_WORDS) + r")")
REPEAT_CELL_MARKER_RE: Final[re.Pattern[str]] = re.compile(
    r"(" + "|".join(REPEAT_WORDS) + r")")
#: 산문은 «되풀이된다는 뜻이 분명한» 낱말과 «대가를 주고받는 서술»만 본다.
#: ★ 「반복·지속·후속」을 산문 발동에 넣으면 「지속적인 채용」 같은 일반 문장이
#:   전부 걸린다. 도식 칸과 달리 산문은 그 낱말이 주장이 아닐 때가 많다.
PROSE_REPEAT_MARKER_RE: Final[re.Pattern[str]] = re.compile(
    r"(재구매|재예치|재가입|재계약|재유치|재이용|갱신|연장)")
#: ⚠️ 뒤따르는 동사는 «활용형까지» 적는다. 「부과」·「청구」를 명사 두 글자로 두면
#:    공백을 지운 표면형에서 「정**부과**제」처럼 낱말 경계를 걸쳐 우연히 맞는다
#:    (실측: 「개발 대가와 정부 과제 수입은 …」이 대가 결속을 요구했다).
#: ★ 「…수수료가 주요 수익원이다」도 대가 단언이다(우리은행 2장 실측). 수취 동사가
#:   붙지 않아 위 꼴로는 잡히지 않으므로 그 틀을 따로 더한다.
PROSE_FEE_MARKER_RE: Final[re.Pattern[str]] = re.compile(
    _NOT_FEE_PREFIX + r"(수수료|로열티|보수|대가|우대금리)(?=[^.]{0,12}?"
    r"(?:수취하|수취한|수취|수령하|수령한|수령|받|지급하|지급받|발생하|발생한|"
    r"부과하|부과되|부과한|부과된|청구하|청구되|청구한|청구된|면제)"
    r"|(?:은|는|이|가)?.{0,4}?주(?:요|된)?수익(?:원|기반))")

# ══════════════════════════════════════════════════════════
# ③ 후보가 스스로 부정한 자리 — 발동에서 뺀다
# ══════════════════════════════════════════════════════════
#
# ★ 「가람은 제작하지 않는다」는 역할 단언이 아니다. 표지만 세면 이런 정상 부정
#   진술이 결속을 대지 못해 통째로 삭제된다(인과 가드의 CLAIM_CAUSE_NEGATED_RE 와
#   같은 취급). 역할 낱말 «끝 자리»에서 이어 붙여 본다.
#: ⚠️ `\A` 를 넣지 마라 — `Pattern.match(text, pos)` 는 이미 그 자리에 고정되지만
#:    `\A` 는 «문자열 처음»만 가리켜서 두 번째 낱말부터 영영 맞지 않는다.
CLAIM_ROLE_NEGATED_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:하|되|한|된)?(?:지\s*(?:않|못)|\s*(?:않|못하|아니|없))")

# ══════════════════════════════════════════════════════════
# ④ 경계 — 절·주체·칸
# ══════════════════════════════════════════════════════════
#
#: 원문·산문을 «스스로 끊은» 자리에서만 나눈다. 쉼표는 절을 나누지 않는다.
CLAUSE_SPLIT_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<=[다요])[.!?]\s*|[;]\s*|\n+|\s{2,}")
#: 원문이 표를 평문으로 편 자리(「상품명 | 주요 내용」).
CELL_SPLIT_RE: Final[re.Pattern[str]] = re.compile(r"\s*\|\s*")
#: 대상과 역할 사이를 «다른 항목»이 가르는 자리. 쉼표는 절을 나누지는 않지만
#: 「A, B는 제작」처럼 나열의 다른 주체를 가리키므로 결속 인접성은 끊는다.
#: ★ 「및」·「·」은 넣지 않는다 — 「운용 및 관리 서비스에 대한 대가」처럼 한 대상의
#:   서술 안에서 쓰인다(우리은행 신탁 실측).
ADJACENCY_BREAK_RE: Final[re.Pattern[str]] = re.compile(r"[,、]")
#: 쉼표가 없어도 「A는 판매하고 B는 제작한다」처럼 «다른 주체의 술어»가 사이에서
#: 닫히면 그 역할은 이 대상의 것이 아니다. 연결어미만 닫힌 목록으로 본다.
PREDICATE_BREAK_RE: Final[re.Pattern[str]] = re.compile(
    r"(하고|하며|이며|이고|하지만|되며|되고|으로서|로서|한\s*뒤|한\s*후)")
#: 역할 낱말이 «서술어»로 쓰였는가(제작한다·제작한·제작되며). 역할 끝 자리에서 본다.
#:
#: ★ 이 구분이 주체 판정을 가른다. 서술어로 쓰였으면 앞에 주격·주제 조사를 단 다른
#:   이름이 그 일의 주체다 — 「가람은 나래가 제작한 제품을 판매한다」의 제작은 나래의
#:   것이다(root 실측 반례). 명사로 쓰였으면(「변동대가」·「제작 역량」) 앞의 「-는」은
#:   뒤 명사를 꾸미는 관형형이라 주체가 아니다.
#: ⚠️ `\A` 를 넣지 마라 — `Pattern.match(text, pos)` 가 이미 그 자리에 고정된다.
ROLE_AS_PREDICATE_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:되|돼|됐|된|하|해|했|한|할|함|합|시켜|시킨|시키)")
#: 주격·주제 조사를 달고 «띄어쓰기로 닫힌» 이름 조각. 다른 주체를 찾는 데 쓴다.
SUBJECT_TOKEN_RE: Final[re.Pattern[str]] = re.compile(
    r"([^\s,、;]+?)(?:은|는|이|가)(?=\s)")

# ══════════════════════════════════════════════════════════
# ⑤ 원문이 그 관계를 «물리는» 자리 — 구절을 잘라 숨기지 못하게 한다
# ══════════════════════════════════════════════════════════
#
# ★ 실측 우회: 원문 「가람은 제작하지 않는다」에서 구절을 「가람은 제작」까지만
#   잘라 내면 부정이 구절 밖으로 빠진다. 그래서 부정은 «구절»이 아니라 «구절이
#   속한 원문 절»에서, 역할이 끝난 자리부터 찾는다.
# ★ 다만 후보가 «같은 표지를 그대로 적었으면» 어긋남이 아니다. 「수수료 없는
#   비대면 서비스」는 원문도 후보도 「없」을 함께 적은 정상 서술이다(우리은행 실측).
#: 절 안에서 관계를 부정·종료하는 표지(공백을 지운 표면형 기준).
ROLE_DENIAL_MARKERS: Final[tuple[str, ...]] = (
    "하지않", "하지못", "되지않", "되지못", "지않", "이아니", "가아니", "아닙",
    "않으", "않는", "않았", "없", "불가", "중단", "중지", "폐지", "해지", "제외",
    "보기어렵", "보기는어렵",
)
#: 절 안에서 관계에 «조건»을 다는 표지. 후보가 조건을 빼고 옮기면 어긋남이다.
#: ★ 조건은 역할 «앞»에 오는 일이 많아(「…한 경우에만 제작합니다」) 절 전체에서
#:   찾는다. 그래서 목록을 아주 좁게 둔다 — 「한해」를 그냥 넣으면 「한 해 동안」이
#:   공백 제거 뒤 걸린다. 조사까지 붙은 꼴만 본다.
ROLE_CONDITION_MARKERS: Final[tuple[str, ...]] = (
    "경우에만", "에한하여", "에한해", "조건으로", "시에만", "전제로",
)
#: 역할 뒤 몇 글자까지 보는가(표면형 글자 수). 절 끝을 넘지 않으며, 이 창 밖의
#: 표현은 «확인하지 못함»으로 남는다.
DENIAL_WINDOW: Final[int] = 20

# ══════════════════════════════════════════════════════════
# ⑥ 대가의 «방향» — 좁은 닫힌 꼴 하나만 본다
# ══════════════════════════════════════════════════════════
#
# ★ 원문 「외국환취급수수료는 주로 기업금융 영업부문에서 발생합니다」는 «그 수수료가
#   어디서 생기나»를 말한다. 후보 「기업금융의 주요 수익원은 수수료」는 «그 부문의
#   수익이 무엇인가»를 말한다. 같은 낱말·같은 대상이지만 방향이 반대다.
# ⚠️ 이 두 꼴 밖의 방향 어긋남은 이 가드가 «확인하지 못한다».
FEE_CONCENTRATION_SOURCE_TEMPLATE: Final[str] = (
    r"{fee}(?:은|는|이|가)?주로.{{0,20}}?{scope}.{{0,10}}?(?:발생|생김|생긴)")
#: 후보 쪽 두 어순을 모두 본다 — 「A의 주요 수익원은 수수료」와 「…수수료가 주요 수익원」.
#: 뒤 꼴이 실제 저장된 우리은행 2장 문장의 어순이다.
FEE_MAIN_REVENUE_CLAIM_TEMPLATES: Final[tuple[str, ...]] = (
    r"{scope}(?:의|은|는)?.{{0,6}}?주(?:요|된)?수익(?:원|기반)(?:은|이|는)?.{{0,4}}?{fee}",
    r"{fee}(?:은|는|이|가)?.{{0,4}}?주(?:요|된)?수익(?:원|기반)",
)

# ══════════════════════════════════════════════════════════
# ⑦ 검수 응답의 결속 항목 — 기존 «관계» 배열의 유형 두 개를 쓴다
# ══════════════════════════════════════════════════════════
#
# ★ 새 최상위 필드를 만들지 않는다. direct_support_constants.RELATION_TYPES 가 이미
#   「역할」·「과금」을 선언해 두었고, 파서는 그 배열을 그대로 읽는다.
RELATION_KEY: Final[str] = "관계"
RELATION_TYPE_KEY: Final[str] = "유형"
RELATION_SOURCE_KEY: Final[str] = "근거"
RELATION_QUOTE_KEY: Final[str] = "원문"
RELATION_TARGET_KEY: Final[str] = "대상"
#: ⚠️ 유형 값 「역할」과 겹치지 않게 칸 이름은 「역할값」이다.
RELATION_ROLE_KEY: Final[str] = "역할값"
RELATION_ROLE: Final[str] = "역할"
RELATION_FEE: Final[str] = "과금"
ROLE_BINDING_TYPES: Final[frozenset[str]] = frozenset({RELATION_ROLE, RELATION_FEE})
ROLE_BINDING_FIELDS: Final[tuple[str, ...]] = (
    RELATION_TARGET_KEY, RELATION_ROLE_KEY, RELATION_SOURCE_KEY, RELATION_QUOTE_KEY,
)

# ══════════════════════════════════════════════════════════
# ⑧ 사유 코드 — 진단·allowlist 가 붙잡는 계약이라 영문으로 고정한다
# ══════════════════════════════════════════════════════════
ROLE_BINDING_MISSING: Final[str] = "role_binding_evidence_missing"
ROLE_BINDING_FIELD_TYPE_INVALID: Final[str] = "role_binding_field_type_invalid"
ROLE_BINDING_PAIR_MISSING: Final[str] = "role_binding_pair_missing"
ROLE_BINDING_PAIR_DEGENERATE: Final[str] = "role_binding_pair_degenerate"
ROLE_BINDING_KIND_MISMATCH: Final[str] = "role_binding_kind_does_not_answer_claim"
ROLE_BINDING_NOT_OWN_CITE: Final[str] = "role_binding_not_own_citation"
ROLE_BINDING_QUOTE_NOT_IN_SOURCE: Final[str] = "role_binding_quote_not_in_source"
ROLE_BINDING_TARGET_NOT_IN_CANDIDATE: Final[str] = "role_binding_target_not_in_candidate"
ROLE_BINDING_ROLE_NOT_IN_CANDIDATE: Final[str] = "role_binding_role_not_in_candidate"
ROLE_BINDING_ACTOR_BOUNDARY: Final[str] = "role_binding_actor_boundary_crossed"
ROLE_BINDING_ROLE_OUTSIDE_QUOTE: Final[str] = "role_binding_role_outside_quote"
ROLE_BINDING_UNBOUND_IN_SOURCE: Final[str] = "role_binding_unbound_in_source"
ROLE_BINDING_NEGATED_IN_SOURCE: Final[str] = "role_binding_negated_in_source"
ROLE_BINDING_CONDITION_DROPPED: Final[str] = "role_binding_condition_dropped_from_source"
ROLE_BINDING_DIRECTION_REVERSED: Final[str] = "role_binding_direction_reversed"
ROLE_BINDING_CLAIM_UNCOVERED: Final[str] = "role_binding_claim_not_covered"

#: 사유 코드 → 한국어 설명. 공개 문구·문서·진단 표시는 이 표를 쓴다.
#: ⚠️ 문구는 모두 «확인하지 못함»이다. 「원문에 없다」고 단정하지 않는다.
ROLE_BINDING_REASON_TEXTS: Final[dict[str, str]] = {
    ROLE_BINDING_MISSING:
        "누가 무엇을 하는지·무엇에 대가를 받는지 적었는데 그 결속을 뒷받침하는 인용 구절을 제시하지 않았습니다",
    ROLE_BINDING_FIELD_TYPE_INVALID:
        "결속 항목의 칸이 문자열이 아닙니다",
    ROLE_BINDING_PAIR_MISSING:
        "결속 항목에 대상 또는 역할값을 적지 않았습니다",
    ROLE_BINDING_PAIR_DEGENERATE:
        "대상과 역할값이 같거나 한쪽이 다른 쪽의 일부라 결속이 성립하지 않습니다",
    ROLE_BINDING_KIND_MISMATCH:
        "결속 항목의 유형이 그 후보가 실제로 한 주장에 답하지 않습니다",
    ROLE_BINDING_NOT_OWN_CITE:
        "결속 근거로 그 후보가 인용하지 않은 조각을 댔거나 근거 id를 비워 두었습니다",
    ROLE_BINDING_QUOTE_NOT_IN_SOURCE:
        "결속 근거로 제시한 구절이 그 인용 원문에 이어진 그대로 있지 않습니다",
    ROLE_BINDING_TARGET_NOT_IN_CANDIDATE:
        "결속 항목이 적은 대상이 그 후보 안에 없습니다",
    ROLE_BINDING_ROLE_NOT_IN_CANDIDATE:
        "결속 항목이 적은 역할값이 그 후보 안에 없습니다",
    ROLE_BINDING_ACTOR_BOUNDARY:
        "후보 안에서 그 역할이 걸린 자리의 주체가 결속 항목이 적은 대상과 다릅니다",
    ROLE_BINDING_ROLE_OUTSIDE_QUOTE:
        "원문에서 그 역할이 걸린 자리가 제시한 구절 밖에 있습니다",
    ROLE_BINDING_UNBOUND_IN_SOURCE:
        "원문의 한 절 안에서 그 대상과 역할이 끊기지 않고 묶인 자리를 확인하지 못했습니다",
    ROLE_BINDING_NEGATED_IN_SOURCE:
        "원문 절이 그 관계를 부정하거나 끝났다고 말하는데 후보는 그 표현을 옮기지 않았습니다",
    ROLE_BINDING_CONDITION_DROPPED:
        "원문 절이 그 관계에 단 조건을 후보가 옮기지 않았습니다",
    ROLE_BINDING_DIRECTION_REVERSED:
        "원문은 그 대가가 어디서 발생하는지를 말하는데 후보는 그 대상의 주요 수익원이라고 방향을 뒤집었습니다",
    ROLE_BINDING_CLAIM_UNCOVERED:
        "후보가 역할·대가·반복이라고 적은 자리 가운데 결속 항목이 뒷받침하지 않은 자리가 있습니다",
}

# ══════════════════════════════════════════════════════════
# ⑨ 표면형 정규화
# ══════════════════════════════════════════════════════════
QUOTE_CHARS: Final[str] = "\"'`‘’“”「」『』«»‹›"
QUOTE_STRIP_RE: Final[re.Pattern[str]] = re.compile("[" + re.escape(QUOTE_CHARS) + "]")
WHITESPACE_RE: Final[re.Pattern[str]] = re.compile(r"\s")

# ══════════════════════════════════════════════════════════
# ⑩ 검수 프롬프트에 더하는 요구 — 파서가 강제하는 것과 «같은» 것만 적는다
# ══════════════════════════════════════════════════════════
#
# ★ 파서가 요구하는 칸을 모델에게 요구하지 않으면 역할·대가를 말한 정상 후보가
#   전부 형식 미달로 떨어진다. 아래 문구와 위 사유 코드는 반드시 함께 바뀐다.
ROLE_BINDING_REVIEW_GUIDE: Final[str] = (
    "\n■ 역할·대가·반복을 적은 후보의 결속 근거\n"
    "후보가 «무엇을 기획·제작·개발·제조·생산한다»를 서술어로 적었거나, "
    "«수수료·로열티·보수·대가·우대금리·면제·배분» 또는 «재구매·재예치·재계약·갱신·"
    "반복·지속» 같은 대가·반복을 적었으면, 검증근거의 "
    f"'{RELATION_KEY}' 배열에 항목을 더한다. 다섯 칸을 모두 «문자열»로 채운다: "
    f'{{"{RELATION_SOURCE_KEY}": "<그 후보가 인용한 근거 id>", '
    f'"{RELATION_TARGET_KEY}": "<그 역할·대가가 걸린 대상, 후보의 표현 그대로>", '
    f'"{RELATION_ROLE_KEY}": "<후보가 적은 역할·대가·반복 표현 그대로>", '
    f'"{RELATION_QUOTE_KEY}": "<그 근거 원문에서 그대로 옮긴 구절>", '
    f'"{RELATION_TYPE_KEY}": "{RELATION_ROLE}" 또는 "{RELATION_FEE}"}}\n'
    "대상과 역할값은 서로 달라야 하고, 한쪽이 다른 쪽의 일부여서는 안 된다.\n"
    "같은 대상·같은 역할값의 반복은 각 자리의 주체·부정·조건을 모두 뒷받침하는 "
    "근거가 있을 때 항목 하나로 증명할 수 있다. 서로 «다른» 주장은 자리마다 항목이 "
    "필요하다: 「수수료, 로열티」는 항목 두 개가 필요하고 「수수료」 근거 하나로 "
    "로열티까지 승인되지 않는다. 대상이나 주체가 다른 자리도 각자의 항목이 필요하다. "
    "「기획·제작」처럼 한 표현이 두 낱말을 묶고 있으면 역할값도 「기획·제작」으로 "
    "통째로 적는다.\n"
    "제시하는 구절은 그 근거 원문의 «한 절 안»에서 대상과 역할이 끊기지 않고 묶인 "
    "부분이어야 한다. 쉼표로 갈린 다른 주체, 「…하고 …한다」로 닫힌 다른 술어, 다른 "
    "문장의 같은 낱말을 끌어와 붙이지 마라. 원문이 그 대상에 준 역할이 «판매»뿐이면 "
    "«기획·제작»으로 바꾸지 말고 거짓으로 판정한다.\n"
    "원문 구절은 «글자 그대로» 이어진 그대로 옮긴다. 요약·이어 붙이기·중간 생략을 "
    "하지 마라. 부정이나 조건을 피하려고 그 절의 앞부분만 잘라 내지 마라 — 구절이 "
    "걸린 절 전체를 본다. 원문이 「…하지 않는다」·「중단」·「…한 경우에만」이라고 "
    "적었으면 후보도 그 표현을 함께 옮겨야 한다.\n"
    "원문이 「그 수수료는 주로 A 부문에서 발생한다」라고 적은 것을 「A의 주요 수익원은 "
    "수수료」로 뒤집지 마라. 방향이 다른 주장이다.\n"
    "역할·대가·반복을 적지 않은 후보에는 이 항목을 넣지 않는다. 「제작하지 않는다」처럼 "
    "«부정»한 문장도 단언이 아니므로 필요 없다. 기존 수치·추세·시점·인과 배열과 판정 "
    "규칙은 그대로다.\n"
)
