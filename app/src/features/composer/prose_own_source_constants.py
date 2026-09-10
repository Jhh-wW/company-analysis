# -*- coding: utf-8 -*-
"""«확인» 산문의 자기 원문 근거 검사에 쓰는 사유 코드와 표지.

★ 사유 코드 글자는 `shared/report_quality/review_diagnostic_constants.py` 의
  REVIEW_SCOPE_ITEMS 와 «반드시 같은 값»이어야 진단이 표에서 새지 않는다.
  두 곳이 어긋나면 co-located 동기화 시험이 빨간불이 된다.
"""

from typing import Final
import re

from src.features.composer.future_plan_constants import (
    GENERIC_SUBJECTS,
    PARTICLE_DELIMITER,
    PARTICLE_TAIL,
    THING_HEAD_NOUNS,
    VERBALIZER,
    VERB_ENDING_HEAD,
)

#: 감사 기록에 남길 근거어 수 상한. 판정 문턱은 기존 공유 계약을 따른다.
SUPPORT_TERM_LOG_LIMIT: Final[int] = 12

PROSE_OWN_SOURCE_UNSUPPORTED: Final[str] = "prose_own_source_unsupported"
PROSE_REVENUE_PRIMACY_FROM_RECOGNITION_ONLY: Final[str] = (
    "prose_revenue_primacy_from_recognition_only"
)
PROSE_OWN_SOURCE_REASON_CODES: Final[tuple[str, ...]] = (
    PROSE_OWN_SOURCE_UNSUPPORTED,
    PROSE_REVENUE_PRIMACY_FROM_RECOGNITION_ONLY,
)

#: 문장을 절로 나눈다. 마침표·줄바꿈만 쓰고 쉼표는 절을 나누지 않는다.
CLAUSE_SPLIT_RE: Final[re.Pattern[str]] = re.compile(r"[.!?。\n]+")

#: 근거어를 셀 때 쓰는 낱말. `prose_facts` 와 «같은» 규칙을 써야 한다 —
#: 결속과 공개가 다른 낱말을 세면 이번 결함이 다시 난다.
SUPPORT_WORD_RE: Final[re.Pattern[str]] = re.compile(r"[0-9A-Za-z가-힣]{2,}")

#: 근거어 하나에서 «끝에 붙은 조사·어미 한 벌»만 벗겨 주는 닫힌 목록.
#:
#: ★ 왜 필요한가 (실측) — 원문 「가람은 음반을 제작합니다」가 그대로 뒷받침하는
#:   정상 문장 「가람이 음반을 제작한다」가 근거어 1개로 세어져 공개에서 빠졌다.
#:   「가람이」는 「가람은」에, 「제작한다」는 「제작합니다」에 글자 그대로 들어
#:   있지 않기 때문이다. 조사·어미만 다른 같은 낱말을 다른 낱말로 세면 정상
#:   의역이 통째로 사라진다.
#: ★ 닫힌 목록만 쓴다 — 임의 접미사를 벗기지 않는다. 목록은 이 저장소가 이미
#:   쓰는 `future_plan_constants` 의 조사·용언 목록을 «그대로» 가져온다.
#:   두 벌로 갈라지면 한쪽만 고쳐지는 사고가 난다.
#: ⚠️ 벗긴 뒤에도 최소 길이를 지키지 못하면 벗기지 않는다. 「이나」처럼 조사만
#:   남는 조각을 근거어로 세지 않기 위해서다.
MIN_SUPPORT_STEM_CHARS: Final[int] = 2
#: 조사를 벗겨서 «새로» 얻은 낱말이 이 목록에 들면 근거어로 세지 않는다.
#:
#: ★ 왜 (실측) — 실제 SM F3 은 원래 근거어 1개로 막히던 자리인데, 조사를 벗기자
#:   「회사는」이 「회사」가 되어 근거어가 2개로 늘고 그대로 통과했다. 어느 회사
#:   보고서든 후보는 「회사는」으로 시작하고 공시 원문에는 「회사」가 늘 있으므로,
#:   이 낱말이 겹친다는 것은 아무것도 뒷받침하지 않는다.
#: ★ 목록은 새로 만들지 않고 이 저장소가 이미 쓰는 일반 주어 목록을 그대로 쓴다.
#: ⚠️ «벗겨서 새로 얻은» 낱말에만 적용한다. 원문에 글자 그대로 있던 낱말은 예전과
#:   똑같이 센다 — 이 변경으로 지금 통과하던 문장이 새로 막히지 않게 하기 위해서다.
GENERIC_SUPPORT_STEMS: Final[frozenset[str]] = GENERIC_SUBJECTS
#: 조사를 벗겨서 «새로» 얻은 낱말이 아래 조직단위 머리명사로 «끝나면»(수식어가
#: 붙어 있어도) 근거어로 세지 않는다.
#:
#: ★ 왜 필요한가 (실측) — 후보 「기업금융 **영업부문은**…」과 원문 「개인금융
#:   **영업부문에서** 발생합니다」가 조사만 벗기면 똑같이 「영업부문」이 되어
#:   근거어 2개(「영업부문」+「수익을」)로 최소 문턱(2개)을 넘었고, 실제로는
#:   다른 부문을 말하는 자기 인용 불일치 문장(자기 인용 [14][240])이 그대로
#:   통과했다. 「영업부문」은 기업금융에도 개인금융에도 똑같이 붙는 말이라, 이
#:   낱말이 겹친다는 것은 «어느 부문»인지 아무것도 뒷받침하지 않는다 — 수식어
#:   (「기업금융」/「개인금융」)는 공백으로 나뉜 별도 낱말이라 이 비교에 애초에
#:   들어오지 않기 때문이다.
#: ★ 회사·은행 이름을 나열하지 않는다(맞춤 차단목록 금지). 이 저장소가 이미
#:   «사물 머리명사»(행위 주체가 될 수 없는 사물의 이름)로 합의해 둔
#:   `THING_HEAD_NOUNS`(future_plan_constants) 중 «조직 단위»를 가리키는
#:   낱말만 골라 재사용한다. 새 어휘를 만들지 않는다.
#: ⚠️ 최소 지지어 문턱(2개, `MIN_PROSE_EVIDENCE_SUPPORT_TERMS`)을 올리지 않고,
#:   조사 벗김 자체도 없애지 않는다 — 다른 45개 문장(정상 의역 포함)에는 이
#:   회귀가 없었다. 이 조직단위 머리명사 한 갈래만 좁혀 막는다.
#: ⚠️ 「끝나면」이다 — 「영업부문」뿐 아니라 「리테일부문」·「글로벌사업부문」처럼
#:   다른 수식어가 붙은 어떤 조직단위 낱말에도 같은 원칙이 똑같이 적용된다.
#:   특정 회사·부문 이름을 나열하는 차단목록이 아니라 구조(머리명사) 판정이다.
#: ⚠️ «벗겨서 새로 얻은» 낱말에만 적용한다(GENERIC_SUPPORT_STEMS와 같은 자리).
#:   원문에 수식어까지 포함해 글자 그대로 있던 낱말은 예전과 똑같이 센다 —
#:   이 변경으로 지금 통과하던 정상 문장이 새로 막히지 않게 하기 위해서다.
GENERIC_ORG_UNIT_HEAD_SUFFIXES: Final[tuple[str, ...]] = tuple(
    sorted(
        {noun for noun in THING_HEAD_NOUNS
         if noun in {"부문", "사업부문", "사업부", "부서", "조직"}},
        key=len, reverse=True,
    )
)
#: 공시 문체에서 «같은 낱말»에 붙는 용언 꼬리. 닫힌 목록이며 새 낱말을 만들지
#: 않는다. 원문 「제작합니다」와 후보 「제작한다」가 같은 낱말임을 알아보려면
#: 양쪽에서 이 꼬리를 같은 규칙으로 떼어야 한다.
#: ⚠️ 공유 상수(`VERBALIZER`)를 넓히지 않는다 — 다른 가드의 판정이 함께 바뀐다.
_VERB_TAIL: Final[str] = (
    r"(?:하였습니다|되었습니다|하겠습니다|했습니다|합니다|습니다|됩니다|입니다|"
    r"하였다|되었다|하였고|하였으며|했다|한다|된다|됐다|이다|"
    r"하며|하고|하는|하여|해서|해도|되며|되고|되는|되어|"
    r"할|한|함|해|될|된|됨|돼)"
)
#: 근거어 하나에서 떼어 내는 «꼬리 한 벌». 조사·조사형 구분자·용언 꼬리를 모두
#: 한 목록으로 두고, 끝에 닿는 가장 «긴» 꼬리가 먼저 떨어진다(re 는 왼쪽부터
#: 훑으므로 더 앞에서 시작해 끝까지 닿는 후보가 먼저 잡힌다).
SUPPORT_TERM_TAIL_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:"
    + _VERB_TAIL
    + r"|" + VERBALIZER + r"(?:" + VERB_ENDING_HEAD + r")*"
    + r"|" + PARTICLE_DELIMITER
    + r"|" + PARTICLE_TAIL
    + r")\Z"
)

# ── 규칙 ②: «주요 수익원» 단정을 순수 회계 인식 설명으로만 세운 경우 ──
#: 후보가 순위 낱말과 수익원 자리를 «붙여» 단정했는가. 「주요 통화」·
#: 「핵심 성공요소」처럼 순위가 아닌 관용 표현은 이 모양이 아니다.
REVENUE_PRIMACY_CLAIM_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:주요|주된|핵심)(?:한|적인)?(?:수익원|매출원|수입원)"
)

#: 원문 절이 «언제·어떻게 수익으로 인식하는가»만 말하는 회계 인식 설명인가.
RECOGNITION_CLAUSE_RE: Final[re.Pattern[str]] = re.compile(
    r"수익으로인식|수익인식|인식합니다|인식한다|인식하며|"
    r"기업회계기준서|수행의무|이행할때|진행기준|인도기준"
)

#: 원문 절이 «얼마나 큰가»를 조금이라도 말하는가. 이 표지가 하나라도 있으면
#: 그 원문은 순수 인식 설명이 아니므로 이 검사는 판정하지 않고 물러난다.
#: 1%인지 70%인지, 그 대상이 맞는지는 **이 검사가 판정하지 않는다**.
REVENUE_MAGNITUDE_MARKER_RE: Final[re.Pattern[str]] = re.compile(
    r"주요|주된|핵심|대부분|절대적|최대|가장|비중|차지|퍼센트|%"
)
REVENUE_WORD_RE: Final[re.Pattern[str]] = re.compile(r"수익|매출|수입")

__all__ = [
    "CLAUSE_SPLIT_RE",
    "GENERIC_ORG_UNIT_HEAD_SUFFIXES",
    "GENERIC_SUPPORT_STEMS",
    "MIN_SUPPORT_STEM_CHARS",
    "SUPPORT_TERM_TAIL_RE",
    "PROSE_OWN_SOURCE_REASON_CODES",
    "PROSE_OWN_SOURCE_UNSUPPORTED",
    "PROSE_REVENUE_PRIMACY_FROM_RECOGNITION_ONLY",
    "RECOGNITION_CLAUSE_RE",
    "REVENUE_MAGNITUDE_MARKER_RE",
    "REVENUE_PRIMACY_CLAIM_RE",
    "REVENUE_WORD_RE",
    "SUPPORT_WORD_RE",
    "SUPPORT_TERM_LOG_LIMIT",
]
