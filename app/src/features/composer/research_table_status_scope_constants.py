"""연구과제 표에서 실행 상태를 읽는 좁은 문법."""
import re

RESEARCH_TABLE_STATE_UNSUPPORTED = 'time_invalid'
RESEARCH_TABLE_HEADER_RE = re.compile(
    r'(?P<first>연\s*구\s*과\s*제|연\s*구\s*개\s*발\s*실\s*적)\s*\|\s*'
    r'(?P<second>연\s*구\s*기\s*간|기\s*대\s*효\s*과|상\s*태|연\s*구\s*결\s*과)'
    r'(?:\s*\|\s*(?P<third>연\s*구\s*결\s*과\s*(?:및|/)\s*기\s*대\s*효\s*과|'
    r'기\s*대\s*효\s*과|상\s*태|연\s*구\s*결\s*과))?'
)
RESEARCH_ROW_SEPARATOR_RE = re.compile(r'[;；\n]')
RESEARCH_PERIOD_RE = re.compile(r'^\s*\d{2,4}[./-]\d{1,2}(?:[./-]\d{1,2})?\s*[~～〜–-]\s*\d{2,4}[./-]\d{1,2}(?:[./-]\d{1,2})?\s*$')
RESEARCH_STATE_RE = re.compile(
    r'(?P<ongoing>(?:추진|진행|개발|연구|구축|양산|강화)(?:\s*하고\s*있(?:다|으며|는|습니다)|\s*중(?:이다|이며|입니다|인|에)?|한다|합니다))'
    r'|(?P<completed>(?:추진|진행|개발|연구|구축|양산|완료|강화)(?:했|하였|하였다|했다|했습니다|하였습니다)|(?:개발|구축|연구|양산)\s*완료(?:되었|됐|했|하였|하였다|했다|됨)?|양산에\s*적용(?:했|하였))'
)
RESEARCH_SOURCE_LIMIT_RE = re.compile(
    r'예정|계획|목표|기대|(?:완료|개발|추진|진행|구축|양산)(?:하지|하지는)\s*않|'
    r'미완료|미진행|중단|취소|철회|(?:가정|가상)\s*(?:회사|기업|사례)|'
    r'실습\s*예시|가정(?:합니다|한다|하면)|할\s*경우|한다면|하면|시에는'
)
RESEARCH_CLAUSE_BOUNDARY_RE = re.compile(r'[;；\n!?。]|\.(?!\d)|,(?=\s*(?:당사|회사|다른|별도|반면|한편))')
RESEARCH_DESCRIPTION_RE = re.compile(r'(?:기재|제시|소개|명시|수록|열거|표시)(?:했|하였|하였다|했다|되어|돼|하고|되|된)')
RESEARCH_TITLE_ACTIVITY_RE = re.compile(r'(?:개발|연구|강화|구축|양산)(?:\s*완료)?$')
RESEARCH_TITLE_MIN_TARGET_LENGTH = 6
RESEARCH_WORD_RE = re.compile(r'[가-힣A-Za-z][가-힣A-Za-z0-9_-]*')
RESEARCH_EFFECT_MIN_WORD_LENGTH = 2
RESEARCH_EFFECT_MIN_MATCH_WORDS = 2
RESEARCH_EFFECT_STOP_WORDS = frozenset({'연구결과', '기대효과', '기술개발', '기술', '개발', '구축', '강화', '연구', '적용', '확보', '완료', '양산'})
