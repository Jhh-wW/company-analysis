"""기업의 사업 기원·전환 관계를 자기 인용에 결속하는 닫힌 문법."""
import re

BUSINESS_ORIGIN_RULE_VERSION = 'business_origin_scope/1'
UNIT_RE = re.compile(r'[。;；\n]|(?<=[다요])\.(?:\s|$)')
BUSINESS_NOUN = r'(?:제조업|제조사|전문기업|전문회사|전문업체|기업|사업|부문|품목)'
ORIGIN_RE = re.compile(
    rf'(?P<prior>[^,，;；\n。]{{1,160}}?{BUSINESS_NOUN})\s*(?:에서|으로|로)\s*'
    r'(?P<verb>출발|시작|출범)'
)
TRANSITION_RE = re.compile(
    rf'(?P<prior>[^,，;；\n。]{{1,100}}?{BUSINESS_NOUN})\s*에서\s*'
    r'(?:(?:출발|시작|출범)(?:하여|해서|해|하였으며|했으며|하였고|했고)\s*)?'
    rf'(?P<next>[^,，;；\n。]{{1,100}}?{BUSINESS_NOUN})\s*(?:으로|로)\s*'
    r'(?P<verb>전환|확장|확대|진출)'
)
SPLIT_ORIGIN_RE = re.compile(
    rf'(?P<prior>[^,，;；\n。]{{1,120}}?(?:사업\s*부문|사업부|사업|부문))\s*(?:을|를)\s*'
    r'(?:인적|물적)?\s*분할(?:하여|해서|해)\s*설립(?:되었다|됐다|되었습니다|되었으며)'
)
ACTOR_RE = re.compile(r'(?:^|\s)(?P<actor>당사|회사|당회사|당기업|우리\s*회사|연결회사|본사|타사|경쟁사|고객사|협력사|관계사|종속기업|자회사|[^\s,，;；|]{1,40}(?:기업|회사|법인|㈜))(?:은|는|이|가)\s+')
SELF_ACTORS = frozenset(('당사', '회사', '당회사', '당기업', '우리회사', '연결회사', '본사'))
FOREIGN_RE = re.compile(r'(?:다른\s*(?:회사|기업)|타사|경쟁사|고객사|협력사|관계사|종속기업|자회사)')
INITIAL_RE = re.compile(r'창업\s*당시|설립\s*당시|초기에는|처음에는|최초\s*사업|사업\s*기원|(?:^|\|)\s*(?:창업|설립)\s*\|')
LATER_RE = re.compile(r'이후|그\s*후|후에|현재')
HISTORY_BUSINESS_RE = re.compile(rf'(?P<business>[^|,，;；\n]{{1,100}}?{BUSINESS_NOUN})(?:만|을|를|으로|로|\s|$)')
EXPANSION_RE = re.compile(rf'(?P<business>[^|,，;；\n]{{1,100}}?{BUSINESS_NOUN})\s*(?:으로|로|에|을|를)\s*(?:새로\s*)?(?:확장|확대|진출|전환|추가)')
FUTURE_TAIL_RE = re.compile(r'^\s*(?:할|하겠|하려|하기\s*위|예정|계획|방침)')
FUTURE_UNIT_RE = re.compile(r'할\s*(?:계획|예정)|하기로|추진할|진출할|확장할|전환할')
DENIAL_TAIL_RE = re.compile(r'^\s*(?:하지\s*않|한\s*것이\s*아니|했다고\s*볼\s*수\s*없)')
NON_BUSINESS_RE = re.compile(r'공정|공급\s*경로|물류|운송|배송|출발지|공정\s*단계')
EXCLUSIVE_RE = re.compile(r'단일\s*품목|단일\s*사업|오직|만\s*(?:영위|제조|생산|판매|취급)')
KEY_PREFIX_RE = re.compile(
    r'^(?:(?:이처럼|당초|과거|처음|초기|이후|현재|먼저|또한)\s*|'
    r'\d{4}\s*년(?:에|부터)?\s*|창업\s*당시\s*|설립\s*당시\s*|'
    r'초기에는\s*|처음에는\s*)+'
)
KEY_DESCRIPTOR_RE = re.compile(r'이라는|라는|단일\s*품목|단일\s*사업|전문|제조\s*사업|제조업|제조사|기업|회사|업체|사업|부문|품목')
