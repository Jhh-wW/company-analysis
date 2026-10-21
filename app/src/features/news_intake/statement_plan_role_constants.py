"""현재 운영 인용 안의 명시적 자사 실행 의지에 한정한 역할 분리 경계."""

import re

STATEMENT_PLAN_SPLIT = 'role_explicit_statement_plan_split'
STATEMENT_PLAN_SENTENCES = 2
STATEMENT_PLAN_SECTION = 'future_strategy'
STATEMENT_PLAN_SLOT = 'future_strategy:stated_plan'
STATEMENT_CURRENT_SLOTS = frozenset({
    ('operations_partners', 'operations_partners:operating_role'),
    ('portfolio', 'portfolio:product_role'),
})
# 지향점·행사 설명은 현재 제품 기능을 증명하지 않는다.
CURRENT_ACTIVITY_ONLY_RE = re.compile(
    r'(?:힘쓰고|주력하고|집중하고)\s*있다\.?$'
)
CURRENT_EVENT_ONLY_RE = re.compile(r'(?:세미나|행사|간담회|설명회).*(?:개최했다|진행했다|마쳤다)\.?$')
DIRECT_QUOTE = r'(?:“[^“”\n]+”|"[^"\n]+")'
REPRESENTATIVE_STATEMENT_SUFFIX = (
    r'\s+(?:관계자|대변인|대표)(?:는|은)\s+'
    rf'(?:{DIRECT_QUOTE}(?:이라며|라며|며)\s+)?'
    rf'(?P<quote>{DIRECT_QUOTE})고\s+(?:말했다|밝혔다|설명했다|전했다)\.?'
)
VOLITION_END_RE = re.compile(
    r'(?:이어가|확대하|추진하|제공하|개발하|개선하|운영하|강화하|지원하)겠(?:다|습니다)$'
)
CONDITIONAL_STATEMENT_RE = re.compile(r'라면|다면|하면|되면|으면|려면|지면|경우|가정|가상|예시')
EXPLICIT_SUBJECT_RE = re.compile(r'(?<!\S)([가-힣A-Za-z][가-힣A-Za-z0-9·&.-]*?)(?:은|는|이|가)\s')
SELF_SUBJECTS = frozenset({'우리', '당사', '저희'})
# '과제를 풀어가는 자리'의 관형 동사를 '풀어가 + 는'이라는 주어로 읽지 않는다.
ATTRIBUTIVE_ACTION_STEMS = frozenset({'풀어가'})
