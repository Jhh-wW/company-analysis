"""6장 「회사가 밝힌 성장 계획」 표 행의 미래 근거 결속에 쓰는 닫힌 구문과 사유 코드.

여기 담는 것은 회사명·상품명·날짜·조각번호가 아니라 **문법 표지와 표 계약의 닫힌
어휘**뿐이다. 유사도 문턱이 없고, 목록에 없는 표현은 «증명되지 않음»으로 남는다
(6장 성장 계획 표는 fail-closed다 — 확인하지 못한 줄은 싣지 않는다).

⚠️ 이 파일의 어떤 상수도 특정 회사·업종에 묶이지 않는다. 고유명사를 목록에 넣지
   말 것 — 모든 회사·업종에 같은 잣대로 적용되어야 한다.
"""

from __future__ import annotations

import re
from typing import Final

# ══════════════════════════════════════════════════════════
# ① 검수 응답이 대는 «미래근거» 항목의 계약
# ══════════════════════════════════════════════════════════
#: 기존 «검증근거» 객체 안에 놓는 배열 이름. 「관계」와 같은 자리다.
FUTURE_KEY: Final[str] = "미래근거"
FUTURE_SOURCE_KEY: Final[str] = "근거"
FUTURE_TARGET_KEY: Final[str] = "대상"
FUTURE_ACTIVITY_KEY: Final[str] = "활동"
FUTURE_QUOTE_KEY: Final[str] = "원문"
FUTURE_MODE_KEY: Final[str] = "양태"
FUTURE_FIELD_KEYS: Final[tuple[str, ...]] = (
    FUTURE_SOURCE_KEY,
    FUTURE_TARGET_KEY,
    FUTURE_ACTIVITY_KEY,
    FUTURE_QUOTE_KEY,
    FUTURE_MODE_KEY,
)
#: 양태의 닫힌 목록. 이 밖의 값은 받지 않는다.
FUTURE_MODE_PLAN: Final[str] = "계획"
FUTURE_MODE_OUTLOOK: Final[str] = "전망"
FUTURE_MODES: Final[tuple[str, str]] = (FUTURE_MODE_PLAN, FUTURE_MODE_OUTLOOK)

# ══════════════════════════════════════════════════════════
# ② 사유 코드 — 실행 결과에서 식별 가능한 안정된 문자열
# ══════════════════════════════════════════════════════════
FUTURE_EVIDENCE_MISSING: Final[str] = "future_plan_evidence_missing"
FUTURE_FIELD_TYPE_INVALID: Final[str] = "future_plan_field_type_invalid"
FUTURE_SLOTS_MISSING: Final[str] = "future_plan_slots_missing"
FUTURE_SLOTS_DEGENERATE: Final[str] = "future_plan_slots_degenerate"
FUTURE_TARGET_TOO_SHORT: Final[str] = "future_plan_target_too_short"
FUTURE_TARGET_GENERIC: Final[str] = "future_plan_target_generic"
FUTURE_ACTIVITY_TOO_SHORT: Final[str] = "future_plan_activity_too_short"
FUTURE_TARGET_NOT_IN_CANDIDATE: Final[str] = "future_plan_target_not_in_candidate"
FUTURE_ACTIVITY_NOT_IN_CANDIDATE: Final[str] = "future_plan_activity_not_in_candidate"
FUTURE_SLOTS_SPLIT_ACROSS_CELLS: Final[str] = "future_plan_slots_split_across_cells"
FUTURE_SOURCE_ID_EMPTY: Final[str] = "future_plan_source_id_empty"
FUTURE_SOURCE_NOT_CITED: Final[str] = "future_plan_source_not_cited"
FUTURE_QUOTE_MISSING: Final[str] = "future_plan_quote_missing"
FUTURE_QUOTE_TOO_SHORT: Final[str] = "future_plan_quote_too_short"
FUTURE_QUOTE_NOT_IN_SOURCE: Final[str] = "future_plan_quote_not_in_source"
FUTURE_TARGET_NOT_IN_QUOTE: Final[str] = "future_plan_target_not_in_quote"
FUTURE_ACTIVITY_NOT_IN_QUOTE: Final[str] = "future_plan_activity_not_in_quote"
FUTURE_TARGET_NOT_BOUND: Final[str] = "future_plan_target_not_bound_to_activity"
FUTURE_MODALITY_NOT_BOUND: Final[str] = "future_plan_modality_not_bound"
FUTURE_SOURCE_STATES_CURRENT: Final[str] = "future_plan_source_states_current"
FUTURE_SUBJECT_MISMATCH: Final[str] = "future_plan_subject_mismatch"
FUTURE_MODE_INVALID: Final[str] = "future_plan_mode_invalid"
FUTURE_MODE_MISDECLARED: Final[str] = "future_plan_mode_misdeclared"
FUTURE_OUTLOOK_HARDENED: Final[str] = "future_plan_outlook_hardened"
FUTURE_POLARITY_FLIPPED: Final[str] = "future_plan_polarity_flipped"
FUTURE_CANDIDATE_STATES_CURRENT: Final[str] = "future_plan_candidate_states_current"
FUTURE_PLAN_DENIED_IN_SOURCE: Final[str] = "future_plan_denied_in_source"
FUTURE_SECOND_CLAIM_UNPROVEN: Final[str] = "future_plan_second_claim_unproven"
FUTURE_CLAIM_CELL_INCONSISTENT: Final[str] = "future_plan_claim_cell_inconsistent"

#: 진단 전송 계약에 등록할 코드 → 사람이 읽는 항목 이름.
FUTURE_REASON_ITEM: Final[str] = "미래 계획 근거"
FUTURE_REASON_CODES: Final[tuple[str, ...]] = (
    FUTURE_EVIDENCE_MISSING,
    FUTURE_FIELD_TYPE_INVALID,
    FUTURE_SLOTS_MISSING,
    FUTURE_SLOTS_DEGENERATE,
    FUTURE_TARGET_TOO_SHORT,
    FUTURE_TARGET_GENERIC,
    FUTURE_ACTIVITY_TOO_SHORT,
    FUTURE_TARGET_NOT_IN_CANDIDATE,
    FUTURE_ACTIVITY_NOT_IN_CANDIDATE,
    FUTURE_SLOTS_SPLIT_ACROSS_CELLS,
    FUTURE_SOURCE_ID_EMPTY,
    FUTURE_SOURCE_NOT_CITED,
    FUTURE_QUOTE_MISSING,
    FUTURE_QUOTE_TOO_SHORT,
    FUTURE_QUOTE_NOT_IN_SOURCE,
    FUTURE_TARGET_NOT_IN_QUOTE,
    FUTURE_ACTIVITY_NOT_IN_QUOTE,
    FUTURE_TARGET_NOT_BOUND,
    FUTURE_MODALITY_NOT_BOUND,
    FUTURE_SOURCE_STATES_CURRENT,
    FUTURE_SUBJECT_MISMATCH,
    FUTURE_MODE_INVALID,
    FUTURE_MODE_MISDECLARED,
    FUTURE_OUTLOOK_HARDENED,
    FUTURE_POLARITY_FLIPPED,
    FUTURE_CANDIDATE_STATES_CURRENT,
    FUTURE_PLAN_DENIED_IN_SOURCE,
    FUTURE_SECOND_CLAIM_UNPROVEN,
    FUTURE_CLAIM_CELL_INCONSISTENT,
)

# ══════════════════════════════════════════════════════════
# ③ 표면 정규화 — «좁은» 규칙만 쓴다
# ══════════════════════════════════════════════════════════
#: 인용 표지는 지우되 자리에 공백을 남긴다. 부호를 «없애» 두 낱말이 붙어
#: 버리면 경계 검사가 무의미해진다.
CITATION_RE: Final[re.Pattern[str]] = re.compile(r"\[\d+(?:\s*,\s*\d+)*\]")
QUOTE_CHARACTERS: Final[str] = "\"'“”‘’「」『』〈〉《》"
WHITESPACE_RE: Final[re.Pattern[str]] = re.compile(r"\s+")

#: 낱말을 이루는 글자. 경계 판정의 유일한 기준이다.
WORD_CHAR: Final[str] = r"[0-9A-Za-z가-힣ㄱ-ㆎ]"
#: 오른쪽 경계에서 «하나만» 벗겨 주는 닫힌 조사 목록. 임의 접미사·어미를
#: 벗기지 않는다 — 넓히면 「마케팅」이 「마케팅화」에도 걸린다.
PARTICLE_TAIL: Final[str] = (
    r"(?:으로써|으로서|에서의|에게서|이라는|이라고|으로|로서|로써|에서|에게|한테|"
    r"까지|부터|보다|처럼|만큼|와의|과의|라는|이란|이라|에도|에는|의|을|를|은|는|"
    r"이|가|도|만|와|과|나|에|랑|께)"
)
#: 뒤에 낱말 글자가 곧바로 붙어도 «경계»로 인정하는 보조사.
#:
#: ★ 왜 필요한가 (실측) — SM 실행이 인용한 공시 원문에 「…새로운 시장 창출까지가능할
#:   것으로 기대하고 있습니다」처럼 «띄어쓰기가 빠진» 자리가 실제로 있다. 일반 조사는
#:   뒤에 낱말 글자가 오면 경계로 보지 않는데(「마케팅」이 「마케팅의사결정」에 걸리는
#:   것을 막기 위해서다), 그 규칙 때문에 이 정상 원문에서 활동을 찾지 못했다.
#: ⚠️ 여기에는 «한국어 명사의 첫머리로 쓰이지 않는» 보조사만 넣는다. 「의」·「은」처럼
#:    낱말 첫 글자로 흔한 조사는 절대 넣지 않는다 — 넣으면 경계 검사가 무너진다.
PARTICLE_DELIMITER: Final[str] = r"(?:까지|부터|조차|마저|밖에)"
#: 활동 명사 뒤에 붙는 «하다/되다» 계열의 첫 음절. 활동은 「확대」처럼 명사로도
#: 쓰이고 「확대하고」처럼 동사로도 쓰인다 — 조사만 벗기면 동사형을 못 찾는다.
#: ⚠️ 여기에 이어지는 «어미 첫 글자»까지 닫힌 목록으로 확인한다. 그러지 않으면
#:    「확대」가 「확대해석」에도 걸려 다른 낱말을 같은 활동으로 만든다.
VERBALIZER: Final[str] = r"(?:하|되|해|돼|시키|시켜|할|한|함|했|될|된|됨|됐)"
VERB_ENDING_HEAD: Final[str] = r"[고게지며면서는은을들여어야도록니다습라자기와으였았었겠셨려든]"
#: 대상과 활동 사이에 «허용되는» 다리. 내용어가 하나라도 끼면 결속으로 보지 않는다.
TARGET_ACTIVITY_BRIDGE_RE: Final[re.Pattern[str]] = re.compile(
    r"\A(?:" + PARTICLE_TAIL + r")?\s*(?:등|및|의|과|와)?\s*\Z"
)
#: 여러 낱말로 된 대상·활동을 맞출 때, 낱말 «사이»에 끼어드는 닫힌 조사.
#:
#: ★ 왜 필요한가 (실측) — 실제 저장 행의 대상은 「이종산업 제휴」인데 원문은
#:   「이종산업**과의** 제휴」다. 조사 하나가 낀 정상 바꿔쓰기인데 글자 그대로
#:   일치를 요구해 증명하지 못했다(독립 대조 F2, target_not_in_quote).
#: ⚠️ 낱말 사이에 «조사만» 허용한다. 내용어가 하나라도 끼면 다른 구다.
PHRASE_GAP: Final[str] = (
    r"(?:" + PARTICLE_TAIL + r"|" + PARTICLE_DELIMITER + r")?\s*"
)
#: 대상이 활동의 «수단»으로 붙은 꼴. 「A를 통해 B를 발굴」에서 A와 발굴을 잇는다.
#:
#: ★ 왜 별도인가 — 실측 정상 행 「이종산업 제휴를 통한 신수익원 발굴」이 이 꼴이다.
#:   목적어 다리(인접)만 인정하면 이 정상 행은 증명할 방법이 없다.
#: ⚠️ 수단 다리는 «후보와 원문의 다리 종류가 같을 때만» 쓴다(guard 쪽에서 확인).
#:   그러지 않으면 「A를 통해 B를 확대」 원문으로 「A 확대」 행을 승인하게 된다.
#: ⚠️ 사이에 낄 수 있는 것은 공백 없는 낱말 «하나»뿐이다. 절이 바뀌면 걸리지 않는다.
MEANS_BRIDGE_RE: Final[re.Pattern[str]] = re.compile(
    r"\A(?:을|를|의)?\s*(?:통해|통한|통하여|바탕으로|기반으로|활용해|활용하여)\s*"
    r"(?:\S{1,20}\s*)?\Z"
)
#: 문장 경계. 한국어 공시문은 「…습니다.」 뒤에 공백 없이 다음 문장이 붙는
#: 경우가 실제로 있다(실측). 숫자의 소수점은 «다/요» 뒤가 아니므로 안전하다.
SENTENCE_SPLIT_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<=[다요])\.|[!?。;]+|\n+"
)
#: 목적어가 표시된 명사구. 「양태가 이 활동이 아니라 뒤의 다른 목적어에 붙었나」를
#: 판정할 때만 쓴다.
OBJECT_TAIL_RE: Final[re.Pattern[str]] = re.compile(
    WORD_CHAR + r"{2,}\s*(?:을|를)\s*\Z"
)
#: 명시된 주제 주어. 「…은/는 」 꼴만 본다.
TOPIC_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:\A|[\s,·])(" + WORD_CHAR + r"{2,}?)(?:은|는)(?=\s)"
)
#: 주어 생략과 구별하기 위한 «일반 주어». 회사 이름 목록이 아니라 보고 대상을
#: 가리키는 대명사·일반명사만이다.
GENERIC_SUBJECTS: Final[frozenset[str]] = frozenset({
    "회사", "당사", "자사", "당행", "당그룹", "그룹", "기업", "저희", "우리", "본사",
})
#: «남의 주체»를 가리키는 관계 명사. 회사 이름 목록이 아니라, 어느 회사에나 같은
#: 뜻으로 쓰이는 «관계»를 나타내는 낱말만이다.
#:
#: ★ 왜 이 방식인가 (실측으로 배운 것) — 처음에는 「명시된 주어가 일반 주어도, 대상도,
#:   이 줄의 칸에 있는 말도 아니면 거절」로 넓게 잡았다. 그러자 SM 광고 원문의 정상
#:   문장(「…차별화된 마케팅 커뮤니케이션 서비스는 … 기대하고 있습니다」)까지 걸렸다 —
#:   그 주어는 «남»이 아니라 회사 «자신의 서비스»다. 보존해야 할 줄을 지우는 규칙이었다.
#: ⚠️ 낱말 «전체»가 일치할 때만 본다. 「경쟁력」이 「경쟁사」로 오인되지 않는다.
THIRD_PARTY_SUBJECTS: Final[frozenset[str]] = frozenset({
    "경쟁사", "경쟁업체", "경쟁기업", "타사", "타기업", "동종업계", "업계",
    "상대사", "거래처", "고객사", "협력사", "파트너사", "제3자", "타업체",
})
#: 주제가 «행위 주체»가 아니라 «사물»임을 보이는 머리명사.
#:
#: ★ 왜 필요한가 — 명시된 주어가 이 줄에 결속되지 않으면 남의 계획일 수 있어
#:   거절해야 한다(ROOT 반례: 원문 주어 「나래기업」, 행에는 「가람기업」). 그런데
#:   그 규칙만 두면 회사가 «자기 서비스»를 주어로 쓴 정상 문장까지 걸린다
#:   (실측 SM 광고 원문의 「…마케팅 커뮤니케이션 **서비스는** … 기대하고 있습니다」).
#: ⚠️ 회사·기업·은행·그룹·법인은 «넣지 않는다» — 넣으면 「나래기업」이 통과한다.
#:    여기 있는 것은 전부 «행위 주체가 될 수 없는 사물»의 이름이다.
THING_HEAD_NOUNS: Final[tuple[str, ...]] = (
    "서비스", "사업부문", "사업부", "사업", "부문", "부서", "상품", "제품",
    "솔루션", "플랫폼", "시스템", "브랜드", "채널", "매장", "지점", "콘텐츠",
    "프로그램", "기술", "설비", "라인", "조직",
)
#: 대상 칸이 이것뿐이면 «무엇의» 계획인지가 없다. 한 글자 대상도 함께 막는다.
GENERIC_TARGETS: Final[frozenset[str]] = frozenset({
    *GENERIC_SUBJECTS,
    "사업", "서비스", "제품", "상품", "시장", "고객", "영역", "부문", "분야",
    "매출", "수익", "실적", "전략", "계획", "목표", "성장", "사업부문", "역량",
})
MIN_TARGET_CHARS: Final[int] = 2
MIN_ACTIVITY_CHARS: Final[int] = 2
#: 구절이 짧으면 무엇이든 원문에 들어 있다. 결속 증거로 쓰기에 부족한 길이.
MIN_QUOTE_CHARS: Final[int] = 10

# ══════════════════════════════════════════════════════════
# ④ 양태 표지 — 한 교대 규칙에서 «가장 먼저 나오는 것»이 이긴다
# ══════════════════════════════════════════════════════════
#
# ★ 왜 «첫 표지»인가 — 「A를 운영하고 있으며 B를 확대할 계획입니다」에서 활동이
#   「운영」이면 뒤쪽의 「할 계획」을 끌어와 현재 운영을 계획으로 둔갑시킬 수 있다.
#   활동 바로 뒤에서 처음 만나는 표지가 그 활동의 양태다.
# ⚠️ 같은 자리에서 겹치면 교대 순서가 이긴다 — 「가능할 것으로 기대하고 있습니다」는
#   전망이지 진행이 아니므로 전망 교대를 앞에 둔다.
_OUTLOOK: Final[str] = (
    r"(?:을|를)?\s*것으로\s*(?:기대|전망|예상|관측|보|판단)"
    r"|것이라\s*(?:기대|전망|예상)"
    r"|기대(?:하고|되고|된다|됩니다|하며|한다|합니다|감)"
    r"|전망(?:이다|입니다|된다|됩니다|하고|이며)"
    r"|예상(?:된다|됩니다|하고|이다|입니다|되며)"
    r"|관측(?:된다|됩니다|이다)"
    r"|가능성(?:이|은|을|도)?\s*(?:있|높|크)"
)
_PLAN: Final[str] = (
    r"(?:할|하는|한다는|해\s*나갈|나갈|칠|될|낼)\s*(?:계획|예정|방침|목표)"
    r"|(?:을|를)\s*(?:계획|목표|방침)(?:으로|로)"
    r"|(?:이|가)\s*(?:계획|목표|방침)(?:이다|입니다)?"
    r"|계획(?:이다|입니다|이며|으로)|예정(?:이다|입니다|이며)"
    r"|방침(?:이다|입니다|이며)|목표(?:이다|입니다|이며)"
    r"|하고자|하려고|하려는|고자\s*(?:합니다|한다|함|하며)"
    r"|할\s*(?:것이다|것입니다)"
    r"|향후|앞으로|추후|중장기적으로|장기적으로|내년|차년도"
)
_ONGOING: Final[str] = (
    r"하고\s*있|되고\s*있|지고\s*있|이고\s*있|어\s*있"
    r"|중이다|중이며|중입니다|중인|진행\s*중|운영\s*중|추진\s*중"
    r"|현재|지금|이미|여전히"
)
_DONE: Final[str] = (
    r"완료|마쳤|마무리(?:했|하였|지었)"
    r"|하였|되었|했습니다|였습니다|됐|했"
    r"|해\s*왔|해왔|바\s*있"
)
MODALITY_RE: Final[re.Pattern[str]] = re.compile(
    r"(?P<outlook>" + _OUTLOOK + r")"
    r"|(?P<plan>" + _PLAN + r")"
    r"|(?P<ongoing>" + _ONGOING + r")"
    r"|(?P<done>" + _DONE + r")"
)
MODALITY_FUTURE_KINDS: Final[frozenset[str]] = frozenset({"outlook", "plan"})
#: 활동 «앞»에 놓인 미래 시점 부사. 뒤쪽에 표지가 하나도 없을 때만 쓰는 보조 경로다.
FUTURE_TEMPORAL_RE: Final[re.Pattern[str]] = re.compile(
    r"향후|앞으로|추후|중장기적으로|장기적으로|내년|차년도"
    r"|[0-9]{4}\s*년\s*(?:까지|부터)"
)
#: 원문 쪽 부정·축소·보류. 원문은 완결된 문장이므로 넓게 본다.
SOURCE_NEGATION_RE: Final[re.Pattern[str]] = re.compile(
    r"지\s*않|치\s*않|하지\s*못|없|아니|중단|철회|보류|연기|취소|철수|폐지|종료"
    r"|축소|감축|줄이"
)
#: 후보 칸 쪽 부정. 표의 칸은 명사구 조각이라 「수수료 없는 …」처럼 «수식어»의
#: 부정이 흔하다. 그것을 계획의 부정으로 읽으면 정상 줄이 극성 불일치로 빠진다.
#: 그래서 후보 쪽은 «계획 자체를 뒤집는» 낱말만 닫힌 목록으로 본다.
CANDIDATE_NEGATION_RE: Final[re.Pattern[str]] = re.compile(
    r"지\s*않|중단|철회|보류|연기|취소|철수|폐지|종료|축소|감축|줄이"
)
#: 양태 표지 «뒤»에서 그 계획 자체를 취소하는 말. 「…할 계획은 없습니다」처럼
#: 부정이 표지 뒤에 오면, 구절을 표지 직후에서 잘라 내 부정을 버리는 우회가 통한다.
#:
#: ★ 「하지 않을 계획」(부정 계획, 보존 대상)과 「계획은 없다」(계획 자체가 없음,
#:   거절 대상)를 가르는 자리가 바로 이것이다 — 부정이 표지 «앞»이면 극성이고,
#:   표지 «뒤»면 계획의 부재다.
SOURCE_PLAN_DENIAL_RE: Final[re.Pattern[str]] = re.compile(
    r"\A(?:은|는|이|가|을|를|도|만)?\s*(?:없|아니|아닙|않)"
)
#: 표지 뒤 부정을 «그 절 안에서만» 본다. 다음 절의 무관한 부정으로 지우지 않는다.
CLAUSE_BOUNDARY_RE: Final[re.Pattern[str]] = re.compile(r"[,，;·]|\s및\s|\s그리고\s")
#: 후보 칸을 «주장 조각»으로 가르는 닫힌 접속 표지. 여기 없는 연결은 한 조각이다.
#: ⚠️ 「-하고」·「-하며」 같은 연결어미는 넣지 않는다 — 한국어에서 그런 등위절은
#:    뒤의 양태가 분배되므로 별개 주장이 아니다(실측 보존 사례가 그 꼴이다).
CANDIDATE_SEGMENT_RE: Final[re.Pattern[str]] = re.compile(
    r"\s*(?:,|，|;|；|·|/|\+|및|그리고|또한)\s*"
)
#: 전망을 후보가 «유지했는지» 볼 때 찾는 한정 표현.
CANDIDATE_OUTLOOK_RE: Final[re.Pattern[str]] = re.compile(
    r"기대|전망|예상|관측|가능성|가능할|수\s*있을|것으로\s*보|추정"
)
#: 후보 칸이 스스로 «끝난 일»이라고 말하는 표지. 성장 계획 표에 들어올 수 없다.
CANDIDATE_DONE_RE: Final[re.Pattern[str]] = re.compile(
    r"완료|마무리|달성했|달성하였|출시했|출시하였|런칭했|런칭하였|체결했|종료"
)

# ══════════════════════════════════════════════════════════
# ④-2 성장 전략 «본문 문장»의 발동 조건 — 아주 좁게만 연다
# ══════════════════════════════════════════════════════════
#
# ★ 왜 필요한가 (실측 반례 future-industry-to-company-plan-prose) — 6장 본문이
#   「회사는 … 부가사업의 비중을 지속적으로 확대할 방침이다 [56]」라고 적었는데,
#   자기 인용 [56]에는 「산업 구조는 빠르게 진화하고 있으며 … 부가사업의 비중도
#   지속적으로 확대되고 있습니다」라는 «산업 전체의 현재 변화»만 있었다. 미래 근거
#   검사가 표에만 붙어 있어 본문 문장은 그대로 지나갔다.
# ★ 그래서 여는 문은 하나뿐이다 — «그 문장이 회사를 주어로 세우고, 그 문장 안에서
#   계획·전망 표지를 쓴 자리». 그 두 가지가 같은 문장에 함께 있을 때만 표와 같은
#   미래 근거를 요구한다.
# ⚠️ 아래 어느 것도 발동시키지 않는다 — 산업·시장의 현재 서술(주어가 회사가 아님),
#    회사의 진행·완료 서술(「하고 있다」·「하였다」), 배경 설명. 표 계약은 그대로다.
# ⚠️ 회사 이름·기사·업종 목록을 쓰지 않는다. 주어는 GENERIC_SUBJECTS 의 일반 주어일
#    때만 «회사»로 본다 — 고유명사를 알아보지 않으므로 모든 회사에 같은 잣대다.
#: 명시된 주어. 주제 조사(은/는)와 주격 조사(이/가)를 함께 본다 — 「회사가 …할
#: 계획이다」도 같은 주장이다. TOPIC_RE 는 원문 쪽 주체 판정에 계속 쓰이므로 건드리지
#: 않고, 본문 발동 전용으로 따로 둔다.
PROSE_SUBJECT_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:\A|[\s,·])(" + WORD_CHAR + r"{2,}?)(?:은|는|이|가)(?=\s)"
)

# ══════════════════════════════════════════════════════════
# ⑤ 검수 프롬프트 — 파서가 요구하는 것과 «같은 말»을 쓴다
# ══════════════════════════════════════════════════════════
FUTURE_PLAN_REVIEW_GUIDE: Final[str] = (
    "\n■ 「회사가 밝힌 성장 계획」 표 줄과 이 장 본문 문장의 미래 근거\n"
    "이 표의 «값이 있는 모든 줄»은 기존 판정 행의 '검증근거'에 "
    f"'{FUTURE_KEY}' 배열을 «반드시» 더한다. 그 줄이 주장하는 «조각마다» 항목을 "
    "하나씩 두고, 항목의 다섯 칸을 모두 «문자열»로 채운다: "
    f'{{"{FUTURE_SOURCE_KEY}": "<이 줄이 인용한 근거 id 하나>", '
    f'"{FUTURE_TARGET_KEY}": "<계획의 대상, 이 줄의 한 칸에 있는 표현 그대로>", '
    f'"{FUTURE_ACTIVITY_KEY}": "<아직 하지 않은 행동·목표, 같은 칸에 있는 표현 그대로>", '
    f'"{FUTURE_QUOTE_KEY}": "<그 근거 원문에서 «한 군데»를 끊지 않고 그대로 옮긴 구절>", '
    f'"{FUTURE_MODE_KEY}": "{FUTURE_MODE_PLAN} 또는 {FUTURE_MODE_OUTLOOK}"}}\n'
    "대상과 활동은 «이 줄의 같은 칸»에 있어야 한다. 서로 다른 칸에서 하나씩 "
    "가져와 붙이지 마라. 대상은 한 글자이거나 「사업·서비스·시장·회사」처럼 "
    "무엇인지 알 수 없는 낱말이면 안 된다.\n"
    "항목의 원문 구절은 «그 항목이 댄 근거 하나»에서 «끊지 않고» 옮긴다. 한 구절에 "
    "여러 근거를 이어 붙이거나 중간을 생략하지 마라. 주장이 둘이고 각각 «다른» 인용이 "
    "뒷받침하면 항목을 둘로 나눠 각자의 근거를 대면 된다 — 그건 정상이다.\n"
    "그 구절 안에 대상과 활동이 «한 문장 안에서» 함께 있어야 하고, 그 문장에서 "
    "활동 바로 뒤에 오는 표지가 미래여야 한다. 대상이 활동의 목적어로 붙어 있든 "
    "「…을 통해 …한다」처럼 수단으로 붙어 있든 좋지만, 이 줄의 칸도 «같은 방식»으로 "
    "이어져 있어야 한다.\n"
    "원문이 그 활동을 「하고 있습니다」·「중입니다」·「하였습니다」·「완료」처럼 "
    "진행·완료로 적었다면 그 줄은 거짓이다. 같은 문장 뒤쪽에 있는 «다른» 목적어의 "
    "계획을 끌어와 이 활동에 붙이지 마라.\n"
    f'"{FUTURE_MODE_KEY}"는 원문이 실제로 쓴 말에 맞춘다. 원문이 「기대·전망·'
    f'예상·가능할 것」처럼 한정했으면 {FUTURE_MODE_OUTLOOK}이고, 이때 이 줄의 칸에도 '
    "그 한정을 그대로 남겨야 한다 — 기대를 확정된 계획으로 바꾸면 거짓이다.\n"
    "원문이 「하지 않을 계획」·「축소할 계획」처럼 부정·축소를 말했으면 이 줄도 "
    "같은 방향이어야 한다. 반대로 뒤집어 긍정 계획으로 적으면 거짓이다.\n"
    "\n"
    "이 장의 «본문 문장»도 회사를 주어로 세우고 「…할 계획·예정·방침·목표」나 "
    "「…할 것으로 기대·전망」처럼 회사의 계획·전망을 명시했다면, 같은 "
    f"'{FUTURE_KEY}' 배열을 «그 주장마다» 똑같은 다섯 칸으로 «반드시» 채워 단다. "
    "표가 아니라 본문 문장이라는 이유로 이 배열을 빼면 그 문장은 실리지 못한다. "
    "같은 대상·활동이 앞뒤 문장에 «되풀이»될 때도 계획 주장 자리마다 항목을 하나씩 "
    "따로 둔다 — 한 항목을 두 자리에 겹쳐 쓰지 마라. 이때 대상과 "
    "활동은 «그 문장 안»에 있는 표현이어야 하고, 위 표에 적용되는 규칙(자기 인용 하나의 "
    "연속 구절, 한 문장 안의 대상·활동 결속, 활동 바로 뒤 미래 표지, 주어 결속, "
    "전망의 한정 유지, 부정·축소 방향 일치)이 그대로 적용된다.\n"
    "산업·시장의 현재 변화, 회사가 «이미 하고 있는» 일, 배경 설명에는 이 배열을 "
    "넣지 않는다 — 그런 문장은 회사의 계획 주장이 아니다. 산업이 그렇게 변하고 "
    "있다는 원문을 회사가 그렇게 하겠다는 계획으로 바꿔 적지 마라.\n"
    "이 표와 이 장의 본문 문장이 아닌 다른 장·다른 문장에는 이 배열을 넣지 않는다.\n"
)
