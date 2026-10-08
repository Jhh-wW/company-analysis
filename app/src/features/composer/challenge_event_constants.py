"""날짜가 붙은 실제 사고·제재 기록의 시점 제약."""
import re

TIME_BINDING_PROBLEM = "time_invalid"
RESPONSE_ACTIVITY_RE = re.compile(r"확보|보완|강화|개선|수립|설치|구축|개발|적용|획득|출원|납부|교육|이행")
# 동사 목록 밖의 명사형 활동도 명시 목적격 뒤 현재 진행을 붙인 경우에만 읽는다.
RESPONSE_NOMINAL_PROGRESS_RE = re.compile(
    r"(?P<activity>[가-힣A-Za-z][가-힣A-Za-z0-9_-]*?)(?:을|를|이|가)\s*"
    r"(?:(?:추진|진행)\s*하고\s*있(?!었|던|을)|(?:추진|진행)\s*(?:중|中)(?!\s*(?:이었|이던|일)))"
)
RESPONSE_ACTIVITY_UNIT_RE = re.compile(r"[;|\n,]|(?<=[다음함])[.!?。](?:\s+|$)")
RESPONSE_CURRENT_STATE_RE = re.compile(r"(?:진행|추진)\s*(?:중|中)(?!\s*(?:이었|이던|일))|(?:진행|추진)\s*하고\s*있(?!었|던|을)")
RESPONSE_UNREAL_PREFIX_RE = re.compile(r"당시|과거|향후|앞으로|가정|경우")
RESPONSE_OTHER_STATE_RE = re.compile(r"완료|예정|계획|가정|경우|가능|하지\s*않|미수행|하지\s*못|했|하였")
RESPONSE_ACTIVITY_WORD_RE = re.compile(r"[가-힣A-Za-z0-9_-]+")
RESPONSE_ACTIVITY_PARTICLE_RE = re.compile(r"(?:을|를|의|에)$")
RESPONSE_ACTIVITY_SUBJECT_WORD_RE = re.compile(r"(?:는|은)$")
RESPONSE_ACTIVITY_HEAD_WORDS = 2
RESPONSE_ACTIVITY_HEAD_CONNECTORS = frozenset({'대해', '위해', '통해', '하여', '위한', '관련한', '대한', '분야'})
EVENT_ACTOR_LEGAL_FORM_RE = re.compile(r"^(?:주식회사|\(주\))|(?:주식회사|\(주\))$")
RESPONSE_ACTIVITY_PROGRESS_TAIL_RE = re.compile(r"^\s*(?:[이가을를]\s*)?(?:중|中)(?!\s*(?:이었|이던|일))")
RESPONSE_ACTIVITY_HEAD_RE = re.compile(r"^(?:(?:현재|지금|올해)|(?:회사|당사|본사)(?:는|은))")
RESPONSE_FOREIGN_ACTOR_RE = re.compile(r"(?P<actor>고객사|거래처|협력사|자회사|종속기업|다른회사)\s*(?:는|은|이|가)")
RESPONSE_ACTION_JOIN_RE = re.compile(r"(?:^|\s)(?:및|그리고)(?=\s|$)|^\s*[이가을를]?(?:와|과)(?=\s|$)")
EVENT_CATEGORY_PATTERNS = {
    "sanction": r"제재|과태료|벌금|과징금|시정명령|(?:안전|보건|환경|규제|법규)[^.!?。;\n]{0,16}위반",
    "accident": r"중대재해|산업재해|사망사고|끼임사고|추락사고|사고|재해",
}
EVENT_CATEGORY_RES = {key: re.compile(value) for key, value in EVENT_CATEGORY_PATTERNS.items()}
EVENT_DATE_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?:\s*년|[./-]\d{1,2}[./-]\d{1,2})(?!\d)")
EVENT_YEAR_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)")
EVENT_DAY_RE = re.compile(
    r"(?<!\d)((?:19|20)\d{2})\s*(?:[./-]|년)\s*(\d{1,2})\s*(?:[./-]|월)\s*(\d{1,2})(?:일)?(?!\d)"
)
EVENT_TABLE_HEADER_RE = re.compile(r"제재(?:조치)?일|처벌또는조치내용|중대재해발생일자|재해발생일자")
EVENT_RECORD_SPLIT_RE = re.compile(r"[;\n]|(?<!\d)\.|\.(?!\d)")
ACTUAL_EVENT_RE = re.compile(
    r"(?:제재|과태료|벌금|과징금|시정명령)[^.!?。;\n]{0,48}(?:받았|받고있|부과됐|부과되었|부과받았)|"
    r"(?:사고|재해)[^.!?。;\n]{0,48}(?:발생했|발생하였|발생했다|발생하여|발생함)"
)
CURRENT_EVENT_RE = re.compile(r"현재|지금|여전히|미해결|겪고있|지속되고|계속되고|남아있|진행(?:중|中)")
CURRENT_COMPLETED_ACTION_RE = re.compile(
    r"(?:현재|지금)(?:(?:과태료|벌금|과징금)(?:의)?(?:를|을)?)?"
    r"(?:납부|설비개선|시설개선|안전설비개선|안전시설개선)"
    r"(?:(?:와|과|및)(?:납부|설비개선|시설개선|안전설비개선|안전시설개선))*"
    r"(?:를|을|이|가)?완료(?:했다|하였다|했으며|하였으며|했고|하였고|했습니다|하였습니다|된상태|한상태|함|됐|되었)"
)
ONGOING_RECORD_RE = re.compile(r"진행(?:중|中)|미해결|미완료|해결되지않|계속되고|지속되고")
GENERAL_EVENT_RISK_RE = re.compile(r"(?:제재|사고|재해|위반)[^.!?。;\n]{0,24}(?:위험|가능성|예방)")

# 실제 공시 표의 같은 행에 있는 관계만 제한한다. 머리말 없는 본문은 추정하지 않는다.
TABLE_UNIT_SPLIT_RE = re.compile(r"[;\n]")
EVENT_DATE_HEADERS = frozenset({"제재조치일", "제재일", "조치일", "중대재해발생일자", "재해발생일자", "사고발생일자"})
EVENT_ACTOR_HEADERS = frozenset({"조치대상자", "처벌또는조치대상자", "재해발생회사", "사고발생회사"})
EVENT_RESPONSE_HEADERS = frozenset({"이행및재발방지대책", "이행및대책", "조치및전망", "조치내용", "개선대책", "대책"})
EVENT_PRIMARY_RESPONSE_HEADERS = EVENT_RESPONSE_HEADERS - {'조치내용'}
EVENT_CONTENT_HEADERS = frozenset({"처벌또는조치내용", "중대재해내용", "재해내용", "사고내용"})
SELF_EVENT_ACTORS = frozenset({"당사", "회사", "본사", "당사사업장"})
UNKNOWN_EVENT_ACTORS = frozenset({"", "-", "해당없음", "미확인", "없음"})
EVENT_MONTH_RE = re.compile(r"(?<!\d)((?:19|20)\d{2})\s*(?:[./-]|년)\s*(\d{1,2})(?:월|[./-]\d{1,2})(?!\d)")
ACTUAL_ACCIDENT_CLAIM_RE = re.compile(r"(?:사고|재해)[^.!?。;\n]{0,48}(?:발생|사망|부상|추락|끼임)|(?:사망|부상)[^.!?。;\n]{0,16}(?:했다|했으며|하였|발생)")
PENALTY_RE = re.compile(r"제재|과태료|벌금|과징금|시정명령|처벌")
CONDITIONAL_PENALTY_RE = re.compile(r"위반할경우|위반시|받을수있|부과받을수있|부과될수있|가능")
CONTRACTOR_ACTOR_RE = re.compile(r"도급사|도급업체|협력업체|수급업체")
ACTION_RE = re.compile(r"변경신고|납부|교육|설치|개선|보완|수립|이행|조치|운영")
ACTION_PENDING_RE = re.compile(r"예정|계획|진행(?:중|中)|미완료|미이행|하지않|되지않")
ACTION_COMPLETED_RE = re.compile(r"완료|실시했|실시하였|설치했|설치하였|수립했|수립하였|이행했|이행하였|^(?:를|을)?(?:했다|하였다|했으며|하였으며|함)")
ACTION_STATE_MAX_CHARS = 24
PREVENTIVE_RESPONSE_RE = re.compile(r"(?:가능성|위험)[^.!?。;\n]{0,16}(?:줄이기위해|낮추기위해|방지하기위해|예방하기위해)")
CLAIM_SENTENCE_BOUNDARY_RE = re.compile(r"(?<=[다요음함])[.!?。](?:\s+|$)|[;\n]")
EVENT_SCOPE_PROBLEM = "scope_condition_unbound"
RESPONSE_SCOPE_PROBLEM = "challenge_response_not_in_source"

# 명시 소송표만 읽는다. 사고 날짜표의 기존 열 계약은 바꾸지 않는다.
LITIGATION_TABLE_HEADER_RE = re.compile(r"구분\|?계류법원\|?원고\|?피고\|?진행상황\|?소송금액")
LITIGATION_ROW_END_RE = re.compile(r"(?:\d+심|조정|중재)(?:진행중|진행中|계류중)\|?[\d,]+(?:천원)?")
LITIGATION_COURT_RE = re.compile(r"(?<![가-힣])[가-힣]{0,12}(?:지방|고등|가정|행정)법원(?:\s+[가-힣]{1,12}지원)?|(?<![가-힣])대법원")
LITIGATION_MARKER_RE = re.compile(r"\(\*\d+\)")
LITIGATION_FOOTNOTE_RE = re.compile(r"(?P<marker>\(\*\d+\))(?=(?:해당|관련|상기|동)소송)")
LITIGATION_FOOTNOTE_BODY_RE = re.compile(r"[^.!?。;\n]{1,800}")
LITIGATION_GLOBAL_CLAIM_RE = re.compile(r"(?:계류중인|진행중인|모든|전체|상기|이들)소송")
LITIGATION_EXPLICIT_NOTE_RE = re.compile(r"각주(?:\(?\*?|\[)?(?P<number>\d+)|\(\*(?P<marker_number>\d+)\)")
LITIGATION_ASSESSMENT_PROPERTIES = (
    re.compile(r"자원(?:의)?유출(?:금액|액)[^.!?。;\n]{0,48}불확실"),
    re.compile(r"재무상태[^.!?。;\n]{0,64}중요[^.!?。;\n]{0,32}영향[^.!?。;\n]{0,32}(?:미치지않|판단)"),
)
