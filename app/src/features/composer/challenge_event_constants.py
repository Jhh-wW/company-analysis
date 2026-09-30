"""날짜가 붙은 실제 사고·제재 기록의 시점 제약."""
import re

TIME_BINDING_PROBLEM = "time_invalid"
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
