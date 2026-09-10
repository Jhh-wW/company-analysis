"""사업 실행을 조직문화로 확대하는 좁은 경계의 표지."""

from typing import Final
import re


CULTURE_EVIDENCE_SCOPE_MISMATCH: Final[str] = "culture_evidence_scope_mismatch"
CULTURE_ACCOUNTING_POLICY_MISPLACED: Final[str] = "culture_accounting_policy_misplaced"
# ★ review_diagnostic_constants.REVIEW_SCOPE_ITEMS와 반드시 같은 값이어야
#   진단이 UI 표("장별 작성범위")에서 새지 않는다 — culture_accounting_policy_
#   misplaced와 같은 UI 범주를 쓴다(둘 다 "잘못된 장에 실린 내용").
CULTURE_FINANCIAL_RISK_SCOPE_MISPLACED: Final[str] = "culture_financial_risk_scope_misplaced"

# 공백을 정규화한 문장에만 적용한다. 회사·상품·출시 시점은 조건이 아니다.
ORGANIZATIONAL_CLAIM_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:조직의)?(?:(?:핵심|일상적)?운영원칙|조직문화|일하는방식|"
    r"의사결정(?:방식|과정|절차))"
)
CULTURE_ATTRIBUTION_RE: Final[re.Pattern[str]] = re.compile(
    r"보여(?:준다|주고|주는|주며)|드러(?:낸다|내고)|시사(?:한다|하는)|"
    r"반영(?:한다|하는)|원칙(?:으로|을)(?:삼|내재화)"
)

# 공식성 자체는 이 문자열 API로 판단하지 않는다. 아래 범주의 실제 내용이
# 있으면 일반 의미 검수에 남긴다; '조직/문화' 한 단어는 면제 근거가 아니다.
EXPLICIT_CULTURE_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:운영원칙|일하는방식|조직문화|인재상)(?:은|는|으로|로)|"
    r"(?:조직문화|인재상)(?:을|를)(?:지향|정의|명시)|"
    r"의사결정(?:방식|과정|절차|권한)|승인권한|결정권한|전결규정|"
    r"정기회의|담당자(?:에게)?(?:위임|자율)|조직개편"
)
SOURCE_UNAVAILABLE_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:공개|공시|확인)(?:하지않|되지않|할수없)|미공시|자료(?:가)?없"
)
SOURCE_CLAUSE_SPLIT_RE: Final[re.Pattern[str]] = re.compile(r"[.!?。\n]+")

# culture flow의 열 순서는 호출 계약이다. 회사명·상품·연도는 검사하지 않는다.
CULTURE_FLOW_CELL_COUNT: Final[int] = 3
SOURCE_GOAL_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:할|될|시킬)(?:계획|예정)|고자(?:하|한|합|했)|(?:사업|경영)목표"
)
FLOW_GOAL_QUALIFIER_RE: Final[re.Pattern[str]] = re.compile(r"계획|목표|예정|향후|앞으로")
GOAL_NEGATION_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:계획|목표|예정)(?:이|가|은|는|을|하지)?(?:아니|않|없)"
)
CURRENT_CULTURE_DENIAL_RE: Final[re.Pattern[str]] = re.compile(
    r"하지않|하지못|(?:이|가)아니|미시행|미도입"
)
OTHER_ORGANIZATION_RE: Final[re.Pattern[str]] = re.compile(r"타사|타회사|다른회사|경쟁사|고객사")

# ── 순수 회계 인식·측정 정책이 culture 장에 잘못 들어간 경우만 좁게 잡는다 ──
# 판정은 문장(절) 단위로 한다 — SOURCE_CLAUSE_SPLIT_RE로 나눈 같은 절 안에서만
# 아래 어휘를 대조한다. 글자수 상한(예: ".{0,10}")은 쓰지 않는다 — 그런
# 거리 제한은 근거 없는 매직 넘버다. 대신 "같은 절(문장)"이라는 문법적으로
# 뜻이 통하는 경계를 쓴다: 절 하나가 매출채권/계약자산 손실충당금의 인식
# 어휘와 측정 기준(신용위험 특성·연체일)을 모두 담고 있으면 순수 회계
# 서술이고, 같은 절 안에 실제 담당·검토·승인·감독 절차(부정되지 않은)까지
# 결속돼 있으면 그 절차 서술이 우선한다 — 차단하지 않는다.
CULTURE_ACCOUNTING_RECOGNITION_RE: Final[re.Pattern[str]] = re.compile(
    r"전체기간기대신용손실|손실충당금|간편법"
)
CULTURE_ACCOUNTING_CREDIT_CHARACTERISTIC_RE: Final[re.Pattern[str]] = re.compile(
    r"신용(?:위험)?특성"
)
CULTURE_ACCOUNTING_OVERDUE_BASIS_RE: Final[re.Pattern[str]] = re.compile(r"연체일")

# ── 순수 주식기준보상 "회계 인식·측정" 서술이 culture 장에 잘못 들어간 경우만
#    좁게 잡는다(culture_accounting_* 와 같은 절 단위 설계, 같은
#    CULTURE_ACCOUNTING_POLICY_MISPLACED 사유를 재사용 — 새 공유 영역 불필요) ──
# 단순히 "보상"·"주식"·"공정가치" 같은 낱말 하나만으로는 걸리지 않는다.
# 같은 절 안에 ① 현금결제(방식) ② 회계처리 ③ 부채의 공정가치 재측정
# ④ 보상원가 네 어휘가 모두 있어야 «순수 회계 인식·측정 절»로 본다.
#
# 면제(같은 절 결속 절차) 판단은 아래 CULTURE_ACCOUNTING_COMPENSATION_
# GOVERNANCE_VERB_RE/NEGATION_RE를 쓴다 — 손실충당금 블록의 버전은 재사용하지
# 않는다(이 절 전용으로 좁힌 것이라 손실충당금·재무위험·도식 판정 범위는
# 그대로다). "관리한다"는 이 동사 목록에 없으므로 그 수사만으로는 면제되지
# 않는다.
#
# ★ 조사(을/를)만으로는 면제하지 않는다 — "감독을 받는다"·"승인을 받는다"
#   처럼 뒤에 실제 "받다"가 이어질 때만 인정한다("외부 감독을 위한
#   참고자료"처럼 다른 말이 이어지면 조사만으로는 걸리지 않는다). "의"는
#   조사 결합만으로는 아예 인정하지 않는다("감독의무"가 조사+명사로 오검
#   출되던 자리).
# ★ "승인한"이 "승인한도"(명사)의 일부일 때는 걸리지 않는다 — 발견된
#   그 한 낱말만 좁혀 제외한다(기존 "감독당국" 제외와 같은 방식).
CULTURE_ACCOUNTING_COMPENSATION_SETTLEMENT_RE: Final[re.Pattern[str]] = re.compile(
    r"현금결제(?:방식)?"
)
CULTURE_ACCOUNTING_COMPENSATION_TREATMENT_RE: Final[re.Pattern[str]] = re.compile(
    r"회계처리"
)
CULTURE_ACCOUNTING_COMPENSATION_REMEASURE_RE: Final[re.Pattern[str]] = re.compile(
    r"부채의?공정가치(?:를|을)?재측정"
)
CULTURE_ACCOUNTING_COMPENSATION_COST_RE: Final[re.Pattern[str]] = re.compile(
    r"보상원가"
)
CULTURE_ACCOUNTING_COMPENSATION_GOVERNANCE_VERB_RE: Final[re.Pattern[str]] = re.compile(
    r"검토(?:하|한|함|받)|승인(?:하|한(?!도)|함|받)|감독(?!당국)(?:하|한|함|받)|"
    r"(?:검토|승인|감독)(?:을|를)(?=받)"
)
# «~한 바 없다»·«~한 적이 없다»도 부정으로 본다 — 손실충당금 블록의 부정
# 목록(하지않/하지못/되지않/되지못/받지않/받지못/미~)에는 이 두 표현이
# 없어서 "승인한 바 없다"·"검토한 적이 없다"가 오히려 면제를 만들던 자리다.
# 이 두 표현만 이 절 전용으로 덧붙이고, 손실충당금 블록의 부정 판정은
# 바꾸지 않는다.
CULTURE_ACCOUNTING_COMPENSATION_GOVERNANCE_NEGATION_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:검토|승인|감독)(?:하지않|하지못|되지않|되지못|한바없|한적이?없)|"
    r"받지않|받지못|미(?:검토|승인|감독)"
)

# 같은 절 안에 실제 검토·승인·감독 절차가 결속돼 있는지 본다. "이사회"·
# "담당자" 같은 명사 하나만으로는 절차가 성립하지 않는다 — 반드시 아래
# 동사가 있어야 한다. 부정되면(검토하지 않는다, 승인을 받지 못한다 등)
# 실제로 수행된 절차가 아니므로 예외로 인정하지 않는다.
CULTURE_ACCOUNTING_GOVERNANCE_VERB_RE: Final[re.Pattern[str]] = re.compile(
    r"검토|승인|감독"
)
CULTURE_ACCOUNTING_GOVERNANCE_NEGATION_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:검토|승인|감독)(?:하지않|하지못|되지않|되지못)|받지않|받지못|"
    r"미(?:검토|승인|감독)"
)

# ── 재무 위험관리 "일반 목표/노출" 서술이 culture 장에 잘못 들어간 경우만
#    좁게 잡는다(culture_accounting_*와 같은 절 단위·같은 면제 설계) ──
# «금융시장 변동성에 초점을 맞춘 위험관리정책·재무성과에 미치는 부정적 영향
# 최소화» 같은 일반 목표 서술과, «환율 변동 위험 노출·파생상품 이용» 자체
# (절차 없이)는 업무 절차·인재상 설명이 아니고 current_challenges 장과
# 중복되므로 막는다. 같은 절에 검토/승인/감독/회의/보고/교육/주관 동사가
# 활용형으로 결속돼 있으면(예: 이사회 감독·재무 부서 주관) 일하는 방식
# 자료이므로 보존한다. 회사명·산업·인용 ID·글자수는 조건이 아니다 —
# "재무"라는 낱말 하나로 일괄 차단하지도 않는다.
CULTURE_FINANCIAL_RISK_POLICY_GOAL_RE: Final[re.Pattern[str]] = re.compile(
    r"위험관리정책"
)
CULTURE_FINANCIAL_RISK_GOAL_CONTEXT_RE: Final[re.Pattern[str]] = re.compile(
    r"금융시장의?변동성|재무성과에?미치는?부정적영향"
)
CULTURE_FINANCIAL_RISK_EXPOSURE_RE: Final[re.Pattern[str]] = re.compile(
    r"환율변동위험|통화의?환율변동"
)
CULTURE_FINANCIAL_RISK_DERIVATIVE_RE: Final[re.Pattern[str]] = re.compile(
    r"파생상품을?(?:이용|활용)"
)
# 검토·승인·감독뿐 아니라 회의·보고·교육·주관 동사도 활용형(하다/한다/받다
# 등)으로 쓰였으면 실제 업무 절차로 보고 보존한다. "사업보고서"(문서명)의
# "보고"나 "감독당국"(외부 기관 명사)의 "감독"처럼 활용되지 않은 채 다른
# 낱말 속에 들어 있는 경우는 동사가 아니므로 제외한다 — 명사 하나만
# 추가해서 순수 노출·목표 문장을 잘못 면제받지 않게 하는 좁힘이다.
CULTURE_FINANCIAL_RISK_GOVERNANCE_VERB_RE: Final[re.Pattern[str]] = re.compile(
    r"검토(?:하|한|함|받)|승인(?:하|한|함|받)|"
    r"감독(?!당국)(?:하|한|함|받|을|를|의)|"
    r"회의(?:에서|를|에)|"
    r"보고(?!서)(?:하|한|함|받)|"
    r"교육(?:하|한|함|받)|주관(?:하|한|함)"
)
CULTURE_FINANCIAL_RISK_GOVERNANCE_NEGATION_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:검토|승인|감독|회의|보고|교육|주관)(?:하지않|하지못|되지않|되지못)|"
    r"받지않|받지못|미(?:검토|승인|감독|보고|교육)"
)

# ── 재무 위험관리 «규정·원칙» 서술이 culture 장에 잘못 들어간 경우를 일반
#    규칙으로 잡는다(위 목표/노출 블록과 같은 절 단위 설계, 같은
#    CULTURE_FINANCIAL_RISK_SCOPE_MISPLACED 사유를 재사용 — 새 사유·새 UI
#    범주를 만들지 않는다) ──
#
# ★ 왜 위 블록만으로는 못 잡나 (실측) — 교육서비스 회사 실행의 culture 장
#   6문장 중 4문장이 재무 서술이었는데, 그중 셋이 위 블록의 어느 표지에도
#   걸리지 않았다:
#     · «신용검증절차… 연체관리… 손상여부를 검토하는 체계적 관리 원칙»
#     · «적정 유동성… 자금수지 예측… 유동성 위험을 최소화하는 재무관리 원칙»
#     · «환위험 관리 규정에 정의·측정주기·관리주체·관리절차를 포함»
#   위 블록은 «위험관리정책 + 금융시장 변동성»(목표)과 «환율변동위험·
#   파생상품»(노출)이라는 두 모양만 알고 있어서, 같은 재무위험 관리
#   서술이라도 신용·유동성·환위험 «규정» 쪽 어휘를 쓰면 그대로 통과했다.
#
# ★ 일반 규칙의 모양 — 한 절이 ① 재무·금융 위험의 «범주»와 ② 그 위험을
#   다루는 «규정·정책·원칙·절차·기준·체계»를 함께 말하면 재무위험 관리
#   규정 서술로 본다. 회사명·업종·연도·인용 id·글자수는 조건이 아니다.
#   «재무»라는 낱말 하나로 일괄 차단하지 않는다 — 범주와 규정 표현이 같은
#   절에 함께 있어야 한다.
CULTURE_FINANCIAL_RISK_CATEGORY_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:신용|유동성|시장|환|외환|이자율|금리|재무|가격)위험|"
    r"신용검증|신용거래|신용공여|연체관리|"
    r"유동성|자금수지|"
    r"외환거래|파생상품|헷지|헤지|"
    r"금융상품|금융자산|금융부채"
)
# «관리규정»·«위험정책»·«관리원칙»·«관리절차»·«관리기준»·«관리체계»처럼
# 앞말과 붙어 있을 때만 인정한다. «규정»·«원칙» 한 낱말만으로는 걸리지
# 않는다 — 복리후생 규정 설명 같은 정상 문장을 끌어들이지 않기 위한 좁힘이다.
CULTURE_FINANCIAL_RISK_RULE_RE: Final[re.Pattern[str]] = re.compile(
    r"(?:관리|위험|통제|운용)(?:규정|정책|원칙|절차|기준|체계)"
)
# 이 절을 «일하는 방식» 자료로 살려 두려면 그 절이 «누가»까지 말해야 한다.
# 위 목표/노출 블록은 동사만 요구했지만, 그러면 «손상여부를 검토하는»처럼
# 사람·조직이 없는 회계 동작만으로도 면제가 만들어진다(실측 3문장 중 하나가
# 그 자리였다). 그래서 이 블록은 조직 주체 + 부정되지 않은 절차 동사를
# «둘 다» 같은 절에서 요구한다. 「회사」·「당사」·「관리주체」처럼 조직을
# 가리키지 않는 일반 명사는 주체로 인정하지 않는다 — 그 말은 모든 문장에
# 붙어 있어서 인정하면 규칙이 통째로 꺼진다.
CULTURE_FINANCIAL_RISK_ORG_ACTOR_RE: Final[re.Pattern[str]] = re.compile(
    r"이사회|위원회|경영진|임원진|담당자|담당조직|"
    r"(?:담당)?부서|팀(?:에서|이|은|의|장|과)|사업부|본부|"
    r"(?:지원|기획|관리|경영|재무|인사|총무)실"
)
