"""분할한 사업과 설립 법인의 직접 관계를 읽는 닫힌 문법."""
import re

UNIT_RE = re.compile(r"[.!?。;\n]+")
CORPORATE_NAME = r"(?:주식회사|\(주\))\s*(?P<entity>[가-힣A-Za-z0-9&·]+)"
DIVISION_OBJECT = r"(?P<object>[^.!?。;\n,]+?(?:사업\s*부문|사업부|부문|사업))\s*(?:을|를)\s*"
SPLIT_CONNECTOR = r"(?P<mode>인적|물적)?\s*분할(?:하여|해서|해)\s*"
ADNOMINAL_RELATION_RE = re.compile(
    DIVISION_OBJECT + SPLIT_CONNECTOR + r"설립(?:된|한)\s*" + CORPORATE_NAME
)
ACTIVE_RELATION_RE = re.compile(
    DIVISION_OBJECT + SPLIT_CONNECTOR + CORPORATE_NAME
    + r"(?:을|를)\s*설립(?:하였다|했다|하였으며|했으며|하였습니다|했습니다)"
)
SUBJECT_RELATION_RE = re.compile(
    CORPORATE_NAME + r"(?:은|는|이|가)\s*" + DIVISION_OBJECT + SPLIT_CONNECTOR
    + r"설립(?:되었다|됐다|되었으며|되었고|되었습니다|됐습니다)"
)
RELATION_PATTERNS = (ADNOMINAL_RELATION_RE, ACTIVE_RELATION_RE, SUBJECT_RELATION_RE)
ENTITY_PARTICLE_RE = re.compile(r"(?:와는|과는|와도|과도|에대한|으로는|으로|와|과|은|는|을|를|이|가)$")
OBJECT_SUBJECT_PREFIX_RE = re.compile(r"^.*?(?:은|는)")
LEADING_DATE_RE = re.compile(
    r"^(?:(?:이후|당시|지난)|[0-9]{4}년(?:[0-9]{1,2}월(?:[0-9]{1,2}일)?)?"
    r"(?:을기준일로(?:하여)?|기준|에|당시)?)+"
)
FOREIGN_OWNER_RE = re.compile(r"^(?:다른회사|타사|경쟁사|고객사|자회사|종속회사)(?:의|은|는)")
SELF_OWNER_RE = re.compile(r"^(?:당사|회사|본회사|우리회사)(?:의|은|는)")
RELATION_NONFACT_RE = re.compile(r"(?:설립되지|설립하지|설립할|설립될|가정|가설|예정|계획|검토|한다면|할경우)")
RELATION_DENIAL_TAIL_RE = re.compile(
    r"^\s*(?:는|라는)\s*(?:주장|사실|보도|설명|내용)\s*(?:을|를|은|는|이|가)?\s*"
    r"(?:부인|인정하지|사실이\s*아니|허위)"
)
