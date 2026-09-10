"""작성 기능과 실행 기능이 공유하는 원문 없는 단계 진단 계약."""

from src.shared.report_quality.constants import STRICT_REQUIRED_QUALITY_SECTION_IDS

PATH_FLAT = "flat"
PATH_PACKET = "packet"
EXTRACT_DIRECT = "direct"
EXTRACT_SLICED = "braces_sliced"
EXTRACT_FAILED = "failed"
READ_OK = "ok"
READ_EMPTY = "empty_response"
READ_JSON_SYNTAX = "json_syntax"
READ_NOT_OBJECT = "not_object"
READ_VERDICTS_KEY_MISSING = "verdicts_key_missing"
READ_VERDICTS_NOT_LIST = "verdicts_not_list"
READ_ALL_ROWS_INVALID = "all_rows_invalid"
ROW_NOT_MAPPING = "not_mapping"
ROW_NUMBER_NOT_INT = "number_not_int"
ROW_RESULT_INVALID = "result_not_allowed"
ROW_OWNER_MISMATCH = "section_owner_mismatch"
ROW_EVIDENCE_EMPTY = "evidence_ids_empty"
ROW_EVIDENCE_DUPLICATE = "evidence_ids_duplicated"
ROW_EVIDENCE_MISMATCH = "evidence_ids_mismatch"
ROW_NUMBER_CONFLICT = "number_conflicting_duplicate"

PROTOCOL_STEP = "8_본문검수_응답판독"
SUMMARY_STEP = "8_핵심요약_단계"

PROTOCOL_READ_CODES = frozenset((
    READ_OK, READ_EMPTY, READ_JSON_SYNTAX, READ_NOT_OBJECT,
    READ_VERDICTS_KEY_MISSING, READ_VERDICTS_NOT_LIST, READ_ALL_ROWS_INVALID,
))
PROTOCOL_ENUM_FIELDS = {
    "경로": frozenset((PATH_FLAT, PATH_PACKET)),
    "판독": PROTOCOL_READ_CODES,
    "추출방식": frozenset((EXTRACT_DIRECT, EXTRACT_SLICED, EXTRACT_FAILED)),
}
PROTOCOL_COUNT_FIELDS = (
    "시도", "입력문자", "응답문자", "요청번호수", "응답행수",
    "유효행수", "미응답번호수", "요청밖번호수",
)
PROTOCOL_OFFSET_FIELDS = ("json시작offset", "json끝offset")
PROTOCOL_ROW_REASONS = frozenset((
    ROW_NOT_MAPPING, ROW_NUMBER_NOT_INT, ROW_RESULT_INVALID,
    ROW_OWNER_MISMATCH, ROW_EVIDENCE_EMPTY, ROW_EVIDENCE_DUPLICATE,
    ROW_EVIDENCE_MISMATCH, ROW_NUMBER_CONFLICT,
))

SUMMARY_COUNT_FIELDS = (
    "본문후보수", "초안수", "검수후수", "첫보충후수", "수치검사후수", "최종수",
)
SUMMARY_BOOL_FIELDS = ("작성한도도달", "검수한도도달")
SUMMARY_STAGES = frozenset(("시작", "작성", "검수", "첫보충", "수치검사", "최종"))

# ── 장별 도식(경로표) 행 수 기록 ──────────────────────────────────────────
#
# ★ 왜 필요한가 (실측) — 어떤 장의 도식이 «0줄»로 나왔을 때, 작가가 애초에
#   빈 배열을 냈는지 우리가 걸렀는지를 되짚을 기록이 하나도 없었다. 도식
#   «검증» 제외 기록(8_도식_검증_제외)은 장 근거 정리 필터 «뒤»에 있어서,
#   그 필터에서 조용히 사라진 줄은 어느 기록에도 남지 않는다. 그래서 매
#   실행이 「왜 0줄인지 확인 못 함」으로 끝났다.
# ⚠️ 칸 내용·인용 id·회사 원문은 남기지 않는다 — 장 이름과 «개수»만 남긴다.
DIAGRAM_ROW_COUNT_STEP = "8_도식_생성수"
#: 작가 응답을 읽은 «직후» — parse_flow_rows가 만든 줄 수 그대로.
DIAGRAM_STAGE_PARSED = "작성"
#: 장 밖 인용·미지원 의미칸을 거르는 정리 «직후».
DIAGRAM_STAGE_SECTION_EVIDENCE = "장근거정리"
DIAGRAM_ROW_COUNT_STAGES = frozenset((
    DIAGRAM_STAGE_PARSED, DIAGRAM_STAGE_SECTION_EVIDENCE,
))
DIAGRAM_ROW_COUNT_SECTION_IDS = frozenset(STRICT_REQUIRED_QUALITY_SECTION_IDS)
