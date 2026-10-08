"""명시된 매출 모집단과 투자 계획 부문을 비교하는 좁은 문법이다."""
import re

REVENUE_OWNER_RE = re.compile(r'(?P<owner>[가-힣A-Za-z0-9&]+(?:부문|사업))매출(?:액)?')
REVENUE_CONTRAST_RE = re.compile(
    r'매출(?:액)?(?:이|은|는)?[^.。;\n]{0,120}?감소[^.。;\n]{0,60}?(?:으나|지만|반면),?'
    r'(?P<owner>[가-힣A-Za-z0-9&]+)매출(?:액)?(?:의경우|은|는|이)?[^.。;\n]{0,180}?증가'
)
CLAIM_REVENUE_CONTRAST_RE = re.compile(
    r'매출(?:액)?[^.。;\n]{0,120}?감소[^.。;\n]{0,60}?(?:으나|지만|반면),?(?P<tail>[^.。;\n]+)'
)
ASSERTED_INCREASE_RE = re.compile(r'증가(?:하였|했다|하였다|했|하고|한다|한것|할것|하였다|했습니다)')
POPULATION_SOURCE_WINDOW = 350
INVESTMENT_PLAN_RE = re.compile(r'투자[^.。;\n]{0,80}?(?:예정|계획|추진할)')
SOURCE_INVESTMENT_PLAN_RE = re.compile(r'(?:계획[^.。;\n]{0,80}?투자|투자[^.。;\n]{0,80}?(?:예정|계획))')
WHOLE_COMPANY_RE = re.compile(r'(?:연결회사|회사|당사)(?:의)?(?:전체|전사)|전사(?:적|차원|투자)')
LOCAL_SECTION_RE = re.compile(r'^\[[^\]\n]*(?:부문|사업부)[^\]\n]*\]$')
SECTION_PART_RE = re.compile(r'\s*[-–—·/]\s*')
LOCAL_OWNER_SUFFIX_RE = re.compile(r'(?:사업부문|사업부|부문|사업|분야)$')
CLAUSE_BOUNDARY_RE = re.compile(r'(?<=[다요])\.|[。;\n]|(?<!\d)\.(?!\d)')
EXPLICIT_SUBJECT_RE = re.compile(r'(?:회사|당사|연결회사|전사)(?:전체)?(?:는|은|가|의)')
OTHER_SECTION_SUBJECT_RE = re.compile(r'[가-힣A-Za-z0-9]+(?:사업부문|사업부|부문|사업|분야)(?:은|는|이|가|의|에서)')
REVENUE_METRIC_RE = re.compile(r'매출(?:액)?')
QUANTITY_SUBJECT_RE = re.compile(r'수주량(?:은|는|이|가|도)')
INVESTMENT_WORD_RE = re.compile(r'투자')
PURPOSE_END_RE = re.compile(r'(?:을|를)?위(?:해|하여)')
PLAN_SUBJECT_END_RE = re.compile(r'(?:계획은|예정은|는|은)')
