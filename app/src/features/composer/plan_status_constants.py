"""같은 계획의 진행상태 산문에만 쓰는 닫힌 인용 계약."""
import re

PLAN_STATUS_SLOT = 'future_strategy:plan_status'
COMPLETED_EXECUTION_SLOT = 'past_changes:completed_execution'
COMPLETED_EXECUTION_STATE_MISMATCH = 'completed_execution_state_mismatch'
PLAN_STATUS_KEY = '계획진행근거'
PLAN_STATUS_FIELDS = frozenset({'근거', '대상', '활동', '진행원문', '계획원문'})
STATE_RE = re.compile(
    r'(?P<cancelled>취소(?:되었|됐|된|되어|하였다|했|한|상태|\s*상태))'
    r'|(?P<paused>(?:중단|보류)(?:되었|됐|된|되어|하였다|했|한|하고\s*있|\s*중|\s*상태))'
    r'|(?P<completed>(?:완료|마무리)(?:되었|됐|된|하였다|했|한|\s*상태))'
    r'|(?P<in_progress>(?:진행|추진|착수)(?:하고\s*있|되고\s*있|\s*중))'
)
STATUS_NEGATION_RE = re.compile(r'지\s*않|지\s*못|아니|않았|못했')
STATUS_PAST_CONTEXT_RE = re.compile(r'당시|과거|지난해|전기(?:\s|말|중|의|에)|이전(?:\s|에|의)')
STATUS_CURRENT_CONTEXT_RE = re.compile(r'현재|지금|당기|보고기간')
STATUS_CONDITIONAL_RE = re.compile(r'경우|가정|한다면|할\s*때|할\s*수\s*있|가능성')
STATUS_OWNER_MODIFIER_RE = re.compile(r'(?:기존|신규|새|다른|제\s*\d+\s*차)\s*$')
STATUS_CLAUSE_END_RE = re.compile(r'[.;!?。\n]')
STATUS_ACTIVITY_BOUNDARY_RE = re.compile(r'[,;]|(?:했고|하였고|했으며|하였으며|했지만|하였지만|이며|으나)\s')
STATUS_PLAN_DENIAL_RE = re.compile(r'계획(?:은|는|이|이란)?\s*(?:없|아니|아닙)')
SCOPE_FEEDBACK_BODY_KIND = '본문'
STATUS_COMPLETION_FACTS = ('완료했', '완료하였', '설립했', '설립하였', '착공했', '착공하였', '시행했', '시행하였')
# 현재 조직·서비스의 역할 설명은 설립·출시·실시 이력과 구별한다.
COMPLETED_ROLE_NOUN_RE = re.compile(r'(?:위원회|협의체|조직|체계|구조|제도|서비스|콘텐츠|기능|도구|솔루션|플랫폼|제품)')
COMPLETED_ROLE_CURRENT_RE = re.compile(
    r'(?:운영|수행|관리|유지|보유|보고|논의|제공|소개)하고\s*있(?:다|습니다|으며|고|는)'
    r'|(?:운영|수행|관리|유지|보고|제공)되고\s*있(?:다|습니다|으며|고|는)'
    r'|갖추고\s*있(?:다|습니다|으며|고|는)'
    r'|구성되어\s*있(?:다|습니다|으며|고|는)'
    r'|(?:운영|수행|관리|유지)\s*중(?:이다|입니다|인)'
)
# 기존 조직 역할의 거절 범위는 새 서비스 소개 제한과 구별해 그대로 보존한다.
COMPLETED_LEGACY_ROLE_NOUN_RE = re.compile(r'(?:위원회|협의체|조직|체계|구조|제도)')
COMPLETED_LEGACY_ROLE_CURRENT_RE = re.compile(
    r'(?:운영|수행|관리|유지|보유|보고|논의)하고\s*있(?:다|습니다|으며|고|는)'
    r'|(?:운영|수행|관리|유지|보고)되고\s*있(?:다|습니다|으며|고|는)'
    r'|갖추고\s*있(?:다|습니다|으며|고|는)'
    r'|구성되어\s*있(?:다|습니다|으며|고|는)'
    r'|(?:운영|수행|관리|유지)\s*중(?:이다|입니다|인)'
)
COMPLETED_ROLE_EVENT_RE = re.compile(
    r'(?:완료|준공|설립|구성|구축|도입|시행|실시|설치|출시|착공|개시|체결)'
    r'(?:했|하였|되었|됐|된|(?P<active_modifier>한)(?=\s|[,.;!?]))'
)
# 새 완료 관형형만 명시 자기 주어에 묶는다. 기존 미래 주어 검사는 바꾸지 않는다.
COMPLETED_MODIFIER_SUBJECT_RE = re.compile(
    r'(?:^|[\s,])(?P<subject>[가-힣A-Za-z0-9_]+?)(?:은|는|이|가)(?=\s)'
)
COMPLETED_MODIFIER_DENIAL_RE = re.compile(
    r'^\s*(?:것|바|사실)(?:은|는|이|가|도)?\s*(?:아니|아님|아닙|없)'
)
# ‘신청을 완료한 고객에게’의 완료는 회사의 출시·도입 사건이 아니다.
COMPLETED_MODIFIER_OTHER_ACTOR_RE = re.compile(
    r'^\s*(?:고객|소비자|직원|임직원|경쟁사|타사|협력사|거래처)'
    r'(?:에게|의|은|는|이|가|을|를|(?=\s))'
)
# 장소 부사 ‘온라인에서’와 달리 명시 타사에서 한 실행은 빌리지 않는다.
COMPLETED_MODIFIER_OTHER_ORIGIN_RE = re.compile(
    r'(?:^|[\s,])(?:경쟁사|경쟁업체|경쟁기업|타사|자회사|계열사|협력사|거래처|고객사)'
    r'에서(?:는)?\s'
)
COMPLETED_MODIFIER_OTHER_SUBJECTS = frozenset({
    '고객', '소비자', '직원', '임직원', '자회사', '계열사', '계열회사', '협력회사',
    '연결회사', '관계회사', '다른회사',
})
COMPLETED_ROLE_CLAUSE_RE = re.compile(r'[.;!?\n]|(?:으며|(?<!다)고|지만)\s+')
COMPLETED_ROLE_MEMBERSHIP_RE = re.compile(r'(?:으로|로)\s*$')
PLAN_STATUS_REVIEW_GUIDE = (
    '6장 plan_status의 현재 진행·중단·보류·취소·완료 상태를 참으로 판정하려면 '
    '그 후보에만 검증근거.계획진행근거 배열을 추가한다. 항목은 '
    '{"근거":"자기 인용 ID","대상":"같은 사업 대상","활동":"같은 활동",'
    '"진행원문":"그 상태의 연속 원문","계획원문":"같은 대상·활동의 명시 계획 연속 원문"}이다. '
    '두 원문은 같은 자기 인용 하나에 글자 그대로 있어야 한다. '
    '다른 사업·주체·기간의 구절이나 일반 현재 업무·환수조건을 계획 상태로 대체하지 않는다. '
    '원문 상태를 진행으로 바꾸지 않는다. 해당하지 않는 후보에는 이 배열을 요구하지 않는다.\n'
)
