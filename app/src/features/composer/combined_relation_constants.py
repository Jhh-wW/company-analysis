"""집단 수·전량 표현을 관계의 상대편 자리에 놓아 원문에 없는 새 관계를 만드는
문장을 잡는 일반 검사 상수.

★ 결합으로 새 관계가 생기는 꼴은 여럿(수량·인과·주체·시점)이지만, 지금 기계로 판정할
  수 있는 것은 «수량 한정이 관계의 상대편 자리에 온 문장» 한 축뿐이다
  (`tmp/lead/combined-facts-guard-design.md` §0). 인과는 이미 `direct_support`가,
  역할·과금은 `role_binding`이 담당한다. 이 파일은 그 밖의 술어(납품·공급·출자·계약·
  판매·운영…)로 넓힌 «집단 수 × 관계» 한 축만 본다 — 기존 `quantified_relation_guard`
  (배당 전용)를 일반화한 것이며, 두 검사는 흡수 전까지 함께 둔다.
★ 판정은 검수 응답의 «관계» 배열에 유형="결합" 항목을 받아 원문과 대조한다
  (`direct_support`의 인과 대조와 같은 방식). 문법 표면만으로 동의어·바꿔쓰기까지
  잡으려던 시도는 이미 실패로 기록되어 있다(`direct_support_constants.py` 참고) —
  «어느 구절이 그 관계를 뒷받침하는가»는 검수 응답이 직접 대게 하고, 코드는 그 구절이
  실제 인용 원문에 축자로 있는지만 대조한다.
★ 닫힌 목록만 쓴다. 회사명·업종·특정 숫자를 넣지 않는다.
"""

from typing import Final

import re

from src.features.composer.direct_support_constants import (
    RELATION_KEY,
    RELATION_QUOTE_KEY,
    RELATION_SOURCE_KEY,
    RELATION_TYPE_KEY,
)


# ══════════════════════════════════════════════════════════
# ① 발동 꼴 — 「범위 한정자 + (집단 이름) + 상대편 조사 + 40자 이내 관계 서술어」
# ══════════════════════════════════════════════════════════
#
# ★ 실측(2026-09-14, `tmp/lead/combined_facts_probe.py`·`_run_trigger.py`)으로 확인한
#   꼴이다 — 막아야 할 표본 12/12 발동, 살려야 할 표본 10/10 무발동, 실제 SK(주) 보고서
#   문장 23개(중복 포함) 중 발동 0개. 절 경계는 결속 검사와 같은 잣대를 그대로 쓴다
#   (아래 `role_binding_constants.CLAUSE_SPLIT_RE` 참고, 쉼표는 절을 나누지 않는다).
#: 범위 한정자 — 아라비아 숫자·한국어 수관형사 + 단위, 또는 전량 표현.
SCOPE_QUANTIFIER_RE_FRAGMENT: Final[str] = (
    r"(?:\d[\d,]*|한|두|세|네|다섯|여섯|일곱|여덟|아홉|열|수십|수백|수천)\s*"
    r"(?:개사|개|곳|사|명|군데)|전부|전량|모두|모든|전\s*계열"
)
#: 집단 이름(선택) — 관계의 «상대편»이 될 수 있는 닫힌 실체 부류만. 서비스·제품은 뺀다.
#:
#: ★ 선택으로 둔 이유 — 「연결대상 회사 577개로부터」처럼 수량이 뒤에 오면 조사가
#:   수량에 바로 붙는다. 집단 이름이 없으면서 조사도 바로 안 붙는 경우
#:   (「5개 사업이 성장했다」)는 실측으로 무발동을 확인했다.
GROUP_NOUN_RE_FRAGMENT: Final[str] = (
    r"회사|법인|기업|계열사|자회사|종속회사|관계사|연결대상\s*회사|고객사?|거래처|"
    r"협력사|파트너|기관|은행|지점|점포|공장|사업장|국가|지역|매장"
)
#: 상대편 자리를 표시하는 조사.
PARTY_PARTICLE_RE_FRAGMENT: Final[str] = (
    r"로부터|으로부터|에게서|에게|와|과|에|을\s*대상으로|이|가|는|은"
)
#: 관계 서술어 — 둘 사이에 실제로 무엇이 오가는 술어만. 튜플로 두어 정규식과 안내문이
#: «같은» 목록에서 나오게 한다 — 손으로 다시 적으면 코드가 찾는 낱말과 안내가
#: 어긋난다(role_binding_constants.py 의 실측 사고를 되풀이하지 않기 위해서다).
RELATION_VERBS: Final[tuple[str, ...]] = (
    "수취", "수령", "받", "지급", "납품", "공급", "판매", "제공", "출자", "투자",
    "보유", "체결", "계약", "인수", "매각", "운영", "담당", "위탁", "수주", "공모", "모집",
)
_RELATION_VERB_RE_FRAGMENT: Final[str] = "|".join(RELATION_VERBS)
#: 발동 꼴. 이름 붙은 두 조각(scope·relation)만 안내·진단에 쓰고 나머지는 익명 그룹이다.
COMBINED_RELATION_TRIGGER_RE: Final[re.Pattern[str]] = re.compile(
    rf"(?P<scope>{SCOPE_QUANTIFIER_RE_FRAGMENT})\s*(?:{GROUP_NOUN_RE_FRAGMENT})?\s*(?:들)?\s*"
    rf"(?:{PARTY_PARTICLE_RE_FRAGMENT})[^.;]{{0,40}}?(?P<relation>{_RELATION_VERB_RE_FRAGMENT})"
)

# ══════════════════════════════════════════════════════════
# ② 검수 응답의 결속 항목 — 기존 «관계» 배열에 유형 하나를 더한다
# ══════════════════════════════════════════════════════════
#
# ★ 새 최상위 필드를 만들지 않는다. 이 유형은 `direct_support_constants.RELATION_TYPES`
#   에도 더해야 한다 — 안 더하면 `role_binding._unknown_type_entry_count`가 이 항목을
#   «계약 밖 유형»으로 세어, 역할·과금 요구가 없는 후보에서 결합 항목을 냈다는 이유만으로
#   역할 가드가 그 문장을 탈락시킨다.
RELATION_COMBINED: Final[str] = "결합"
#: 범위(수량·전량 표현) 칸.
#:
#: ⚠️ 이 칸 이름은 관계 배열 «항목 안»의 칸이며, 배열 자체의 이름(`RELATION_KEY`=「관계」)과
#:   이 칸 바로 아래 `COMBINED_RELATION_WORD_KEY` 도 글자가 같다. 배열은 목록이고 항목의
#:   칸은 문자열이라 자료형으로 구분되며, 설계안(§3.3)이 이 이름을 그대로 지정했다.
COMBINED_SCOPE_KEY: Final[str] = "범위"
#: 관계(서술어) 칸 — 항목 안에서 실제로 오간 것을 가리키는 서술어 낱말.
COMBINED_RELATION_WORD_KEY: Final[str] = "관계"

# ══════════════════════════════════════════════════════════
# ③ 사유 코드 — 기존 causal·role_binding 가드와 같은 «안정된 영문 코드»
# ══════════════════════════════════════════════════════════
COMBINED_SCOPE_EVIDENCE_MISSING: Final[str] = "combined_scope_evidence_missing"
COMBINED_SCOPE_FIELD_TYPE_INVALID: Final[str] = "combined_scope_field_type_invalid"
COMBINED_SCOPE_SOURCE_NOT_CITED: Final[str] = "combined_scope_source_not_cited"
COMBINED_SCOPE_QUOTE_NOT_IN_SOURCE: Final[str] = "combined_scope_quote_not_in_source"
COMBINED_SCOPE_RANGE_NOT_IN_QUOTE: Final[str] = "combined_scope_range_not_in_quote"
COMBINED_SCOPE_RELATION_NOT_IN_QUOTE: Final[str] = "combined_scope_relation_not_in_quote"
COMBINED_SCOPE_SPLIT_ACROSS_CLAUSES: Final[str] = "combined_scope_split_across_clauses"
COMBINED_SCOPE_NEGATED_IN_SOURCE: Final[str] = "combined_scope_negated_in_source"
COMBINED_SCOPE_CLAIM_NOT_COVERED: Final[str] = "combined_scope_claim_not_covered"

#: 사유 코드 → 한국어 설명. `review_diagnostic_constants.REVIEW_SCOPE_ITEMS`와 그
#: 전송 계약 시험이 이 표의 키를 그대로 가져다 쓴다(causal·role_binding과 같은 방식).
COMBINED_RELATION_REASON_TEXTS: Final[dict[str, str]] = {
    COMBINED_SCOPE_EVIDENCE_MISSING:
        "집단 수·전량 표현을 관계의 상대편으로 적었는데 그 관계를 뒷받침하는 결합 "
        "항목을 제시하지 않았습니다",
    COMBINED_SCOPE_FIELD_TYPE_INVALID:
        "결합 항목의 칸이 문자열이 아닙니다",
    COMBINED_SCOPE_SOURCE_NOT_CITED:
        "결합 근거로 그 후보가 인용하지 않은 조각을 댔거나 근거 id를 비워 두었습니다",
    COMBINED_SCOPE_QUOTE_NOT_IN_SOURCE:
        "결합 근거로 제시한 구절이 그 인용 원문에 그대로 있지 않습니다",
    COMBINED_SCOPE_RANGE_NOT_IN_QUOTE:
        "결합 근거가 적은 범위(수량·전량 표현)가 후보 또는 그 원문 구절에 없습니다",
    COMBINED_SCOPE_RELATION_NOT_IN_QUOTE:
        "결합 근거가 적은 관계(서술어)가 후보 또는 그 원문 구절에 없습니다",
    COMBINED_SCOPE_SPLIT_ACROSS_CLAUSES:
        "제시한 원문 구절 안에서 범위와 관계가 서로 다른 절에 떨어져 있습니다",
    COMBINED_SCOPE_NEGATED_IN_SOURCE:
        "원문 문장이 그 관계를 부정하거나 단정을 물립니다",
    COMBINED_SCOPE_CLAIM_NOT_COVERED:
        "후보가 수량 범위로 관계를 단언한 자리 가운데 결합 항목이 뒷받침하지 않은 "
        "자리가 있습니다",
}

#: 규칙 버전 — 진단이 «어느 규칙으로 판정했는지»를 남긴다. 회사·날짜·사례가 아니라
#: 발동·대조 규칙의 판만 가리킨다.
COMBINED_RELATION_RULE_VERSION: Final[str] = "combined-relation-rules/1"

# ══════════════════════════════════════════════════════════
# ④ 진단 우선 모드 — 막지 않고 세기
# ══════════════════════════════════════════════════════════
#
#: 이 검사가 «막는가». False 면 계산은 하되 사유를 돌려주지 않고 로그만 남긴다.
#: 실제 보고서 몇 건에서 오탐률을 재고 나서 True 로 바꾼다 — 바꾸는 커밋은
#: `test_combined_relation_guard.py::test_차단_스위치의_현재값` 한 건만 건드린다.
COMBINED_RELATION_ENFORCED: Final[bool] = False

# ══════════════════════════════════════════════════════════
# ⑤ 검수 프롬프트에 더하는 요구 — 파서가 강제하는 것과 «같은» 것만 적는다
# ══════════════════════════════════════════════════════════
_JOIN: Final[str] = "·"
#: 후보 밑에 붙일 안내 줄의 머리말·항목 꼴(role_binding_hint_lines 와 같은 계약 —
#: 발동이 없으면 빈 문자열, 있으면 이 머리말 뒤에 항목을 붙인다).
COMBINED_RELATION_HINT_HEAD: Final[str] = "  수량 범위 결속 요구: "
COMBINED_RELATION_HINT_ITEM_TEMPLATE: Final[str] = "「{scope}…{relation}」"
COMBINED_RELATION_REVIEW_GUIDE: Final[str] = (
    "\n■ 집단 수·전량 표현을 관계의 상대편으로 적은 후보의 결합 근거\n"
    "후보가 «숫자나 전량 표현으로 한정한 집단»을 거래·수취·지급 같은 관계의 상대편 "
    "자리에 놓으면(예: 「577개 종속회사로부터 배당수익을 수취한다」), 그 집단 수와 "
    "관계가 원문 «한 곳»에서 함께 나온다는 근거가 필요하다. 각 후보의 「수량 범위 결속 "
    "요구」 줄이 그 자리를 알려준다.\n"
    f"관계 서술어 닫힌 목록: 「{_JOIN.join(RELATION_VERBS)}」. 이 목록 밖의 서술어에는 "
    "이 안내가 적용되지 않는다.\n"
    f"항목은 검증근거의 '{RELATION_KEY}' 배열에 넣고 다섯 칸을 모두 «문자열»로 채운다: "
    f'{{"{RELATION_SOURCE_KEY}": "<그 후보가 인용한 근거 id>", '
    f'"{COMBINED_SCOPE_KEY}": "<수량·전량 표현, 후보의 표현 그대로>", '
    f'"{COMBINED_RELATION_WORD_KEY}": "<관계 서술어, 후보의 표현 그대로 — 위 닫힌 '
    f'목록의 낱말을 포함한다>", '
    f'"{RELATION_QUOTE_KEY}": "<그 근거 원문에서 그대로 옮긴 구절>", '
    f'"{RELATION_TYPE_KEY}": "{RELATION_COMBINED}"}}\n'
    "범위와 관계는 «같은 원문 구절 하나» 안에 함께 있어야 한다. 집단 수를 말한 절과 "
    "관계를 말한 절이 서로 다른 문장이거나 마침표로 끊긴 다른 절이면 이 근거로 세울 "
    "수 없다 — 원문이 두 사실을 각각 말했을 뿐 «그 집단이 그 관계를 갖는다»고 한 곳에서 "
    "말하지 않았을 수 있기 때문이다. 원문이 그 관계를 부정하거나 단정을 물렸다면 "
    "(「받지 않습니다」·「받는다고 보기는 어렵다」) 이 근거로 세우지 말고 결과를 거짓으로 "
    "판정한다.\n"
    "후보가 이런 자리를 여럿 말했으면 자리마다 항목을 하나씩 댄다. 같은 항목 하나로 "
    "«다른» 자리까지 덮이지는 않는다.\n"
)
