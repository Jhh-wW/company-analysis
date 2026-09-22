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
SECTION_EXECUTION_STEP = "v2_작성_실행방식"
SECTION_EXECUTION_COUNT_FIELDS = ("동시상한", "장수", "소요_ms")
SUMMARY_STEP = "8_핵심요약_단계"
BODY_MACHINE_STEP = "8_본문검수_기계통과"
BODY_DISPOSITION_STEP = "8_본문검수_처분"
EMPTY_RECOVERY_STEP = "8_빈장_복구"
BODY_SECTION_IDS = frozenset((*STRICT_REQUIRED_QUALITY_SECTION_IDS, "summary"))
BODY_DISPOSITIONS = frozenset((
    "참", "거짓_재작성", "거짓_제거", "애매_강등", "근거결속실패_제거", "번호없음_제거",
))
#: 복구가 «시작조차» 못 한 두 경우. 예전에는 아무 기록 없이 넘어가서,
#: 실행 진단에 `8_빈장_복구` 단계 자체가 없는 실행이 「복구가 꺼져 있었다」인지
#: 「예산이 없었다」인지 「근거가 없었다」인지 되짚을 방법이 없었다
#: (2026-09-16 실측: 6·8장이 빈 채로 나간 실행의 단계 목록 53개에 이 step 없음).
#: 이름은 기존 `확인후보없음`(작가가 쓴 문장이 하나도 확인 등급을 못 받음)과
#: 다르다 — 이쪽은 «AI를 부르기 전»의 사유다.
EMPTY_RECOVERY_NO_BUDGET = "예산부족"
EMPTY_RECOVERY_NO_EVIDENCE = "근거후보없음"
EMPTY_RECOVERY_STATES = frozenset((
    "문장재작성대신예약", "작성형식실패", "확인후보없음", "작성완료", "검수완료",
    "호출중단", "복구형식실패", EMPTY_RECOVERY_NO_BUDGET, EMPTY_RECOVERY_NO_EVIDENCE,
))
EMPTY_RECOVERY_ERRORS = frozenset(("호출한도", "요청예산", "제공자오류"))
#: 빈 장 복구 작가 응답의 «꼴» — 내용이 아니라 구조만 닫힌 코드로 남긴다.
EMPTY_RECOVERY_SHAPE_CONTRACT = "계약"
EMPTY_RECOVERY_SHAPE_UNWRAPPED = "포장없음"
EMPTY_RECOVERY_SHAPE_FLAT_SINGLE = "단일장평면"
EMPTY_RECOVERY_SHAPE_NO_TARGET = "요청장없음"
EMPTY_RECOVERY_SHAPE_UNREADABLE = "읽기실패"
EMPTY_RECOVERY_RESPONSE_SHAPES = frozenset((
    EMPTY_RECOVERY_SHAPE_CONTRACT, EMPTY_RECOVERY_SHAPE_UNWRAPPED, EMPTY_RECOVERY_SHAPE_FLAT_SINGLE,
    EMPTY_RECOVERY_SHAPE_NO_TARGET, EMPTY_RECOVERY_SHAPE_UNREADABLE,
))
#: 복구 «검수완료» 기록의 관문별 문장 수 칸 — 검수 통과 → 수치·중복 검사 뒤 → 최종 반영.
EMPTY_RECOVERY_STAGE_COUNT_KEYS = ("검수통과", "안전검사후", "최종반영")

#: 근거 결속 검사에서 탈락한 «확인» 문장을 한 번 묶어 고쳐 쓰는 단계의 진단 이름.
#: 이 기록 하나가 「고쳐 쓰기를 안 했더라면 사라졌을 문장 수(대상)」와
#: 「고쳐 써서 «검수 직후 장부에 남은» 문장 수(최종반영)」를 함께 담아, 같은
#: 실행 안에서 기능 유무를 비교할 수 있게 한다.
#: ⚠️ «최종반영»은 보고서에 실제로 인쇄된 수가 아니다 — 이 단계 뒤에도 장 간
#:   중복 제거·수치 안전 검사 같은 관문이 남아 있어 거기서 더 빠질 수 있다.
#:   두 수를 같은 것으로 읽으면 빠진 문장을 이 단계의 실패로 오해하게 된다.
GROUNDING_REWRITE_STEP = "8_근거결속_재작성"
GROUNDING_REWRITE_STATE_DONE = "완료"
GROUNDING_REWRITE_STATE_CALL_ABORTED = "호출중단"
GROUNDING_REWRITE_STATE_FORMAT_FAILED = "작성형식실패"
GROUNDING_REWRITE_STATES = frozenset((
    GROUNDING_REWRITE_STATE_DONE, GROUNDING_REWRITE_STATE_CALL_ABORTED,
    GROUNDING_REWRITE_STATE_FORMAT_FAILED,
))
#: «완료» 기록의 닫힌 개수 칸. 대상 → 재작성수신(포기 제외) → 기계검사통과 → 재검수참 → 최종반영.
GROUNDING_REWRITE_COUNT_KEYS = ("대상", "재작성수신", "포기", "기계검사통과", "재검수참", "재검수애매", "최종반영")
#: «응답꼴» 칸의 모양이 상태마다 다르다 — 읽는 쪽이 둘을 같게 다루면 안 된다.
#:   · 완료      : 문자열 하나(«마지막» 시도의 꼴). 앞 시도가 형식 실패였어도
#:                 결국 읽힌 답이 판정의 근거이므로 마지막 하나만 남긴다.
#:   · 작성형식실패 : 시도별 꼴의 «목록». 어느 시도에서 어떻게 틀렸는지가 곧 사유다.
#:   · 호출중단   : 이 칸이 없다. 대신 «오류종류»가 있다.

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

# ── 도식 수치 관문의 «파생 비율» 판정 ──────────────────────────────────────
#
# ★ 왜 실행 기록까지 올리나 — 이 판정은 「글자로 없는 수를 계산으로 인정했다」는
#   뜻이다. 어떤 근거 쌍으로 인정했는지가 남지 않으면, 나중에 그 카드가 틀렸을 때
#   무엇을 보고 통과시켰는지 되짚을 방법이 없다.
# ★ 원문 글자는 담지 않는다 — 장 이름·닫힌 사유 코드·수만 통과시킨다.
# ★ 이 기록은 «검수 제외» 장부와 다른 곳에 남는다. 인정 기록을 제외 장부에
#   넣으면 화면 안내문이 아무것도 빠지지 않았는데 「…개를 뺐습니다」라고 말한다.
DERIVED_RATIO_STEP = "8_도식_파생비율"
#: 인용 조각 안의 두 원값으로 되짚어 인정한 경우.
DERIVED_RATIO_RECOMPUTED = "derived_ratio_recomputed"
#: 조합 수가 상한을 넘어 «보지 않기로» 한 경우(인정 아님).
DERIVED_RATIO_PAIR_LIMIT = "derived_ratio_pair_limit"
DERIVED_RATIO_REASONS = frozenset((
    DERIVED_RATIO_RECOMPUTED, DERIVED_RATIO_PAIR_LIMIT,
))
#: 재계산 종류. 인정하지 않은 기록은 빈 문자열이다.
DERIVED_RATIO_SHARE_KIND = "구성비"
DERIVED_RATIO_KINDS = frozenset((DERIVED_RATIO_SHARE_KIND, ""))
#: 십진수 칸 — 후보가 «적어 낸» 백분율. 보고서에 이미 인쇄된 수다.
#: 부호·지수·쉼표 표기는 받지 않는다.
DERIVED_RATIO_DECIMAL_FIELDS = ("백분율",)
#: 근거 쌍은 «지문»으로만 남긴다 — 원문 금액을 기록 칸에 싣지 않는다.
#: 빈 문자열은 «근거 쌍 없음»(상한 초과 기록)이다.
DERIVED_RATIO_FINGERPRINT_FIELD = "근거지문"
DERIVED_RATIO_SECTION_IDS = frozenset(STRICT_REQUIRED_QUALITY_SECTION_IDS)

# ── 6장 → 5장 «장 이동» 기록 ──────────────────────────────────────────────
#
# ★ 왜 «제외» 장부가 아닌 여기인가 — 옮긴 문장은 빠진 문장이 아니다. 검수
#   제외 장부(8_근거검수_제외)에 넣으면 화면 안내문을 만드는 쪽이 그 장부의
#   모든 항목을 「…개를 뺐습니다」로 세기 때문에, 보고서에 그대로 실린 문장을
#   뺐다고 말하게 된다.
# ★ 원문 글자는 담지 않는다 — 장 이름·닫힌 사유 코드·수만 통과시킨다.
BODY_SECTION_MOVE_STEP = "8_본문검수_장이동"
#: 옮기기를 시도하게 만든 사유 코드. composer 의
#: `future_plan_constants.FUTURE_SECTION_NO_FORWARD_STATEMENT` 와 «반드시 같은
#: 값»이어야 한다 — 공유 계층이 feature 를 import 하지 않도록 인과·미래 근거
#: 코드와 같은 방식으로 글자를 적고, composer 쪽 시험이 두 값을 맞댄다.
SECTION_MOVE_REASONS = frozenset(("future_section_no_forward_statement",))
#: 못 옮긴 이유 — 닫힌 목록.
#:   · 허용근거밖   : 그 문장의 인용이 도착 장에 허용된 조각 밖이다.
#:   · 근거결속미해결 : 출발 장의 근거 결속 검사에도 걸렸다(이동이 아니라 제외).
#:   · 도착장규칙탈락 : 도착 장의 규칙으로 다시 보니 걸렸다.
#:   · 도착장중복   : 도착 장에 이미 같은 사실이 있다.
#:   · 도착장없음   : 이번 검수 묶음에 도착 장 자체가 없다.
SECTION_MOVE_BLOCKED_OUT_OF_EVIDENCE = "허용근거밖"
SECTION_MOVE_BLOCKED_SOURCE_BINDING = "근거결속미해결"
SECTION_MOVE_BLOCKED_TARGET_RULE = "도착장규칙탈락"
SECTION_MOVE_BLOCKED_DUPLICATE = "도착장중복"
SECTION_MOVE_BLOCKED_NO_TARGET = "도착장없음"
SECTION_MOVE_BLOCKERS = frozenset((
    SECTION_MOVE_BLOCKED_OUT_OF_EVIDENCE,
    SECTION_MOVE_BLOCKED_SOURCE_BINDING,
    SECTION_MOVE_BLOCKED_TARGET_RULE,
    SECTION_MOVE_BLOCKED_DUPLICATE,
    SECTION_MOVE_BLOCKED_NO_TARGET,
))

# ── 최종 렌더 «문체·시점 표기» 기록 ──────────────────────────────────────
#
# ★ 왜 실행 기록까지 올리나 (2026-09-23 실측) — 최종 렌더의 문체 정규화기는
#   «지난 일정의 미래형» 절 수를 세어 두었지만, composer 는 그 수를 운영
#   로그(logger extra)에만 남기고 실행 기록 싱크에는 넣지 않았다. 로그는
#   실행별로 되짚기 어렵고 보존 기간도 다르므로, 「이 실행에서 시제 표기가
#   몇 번 붙었나」가 실행 진단(steps)에는 한 번도 남지 않았다.
# ★ 원문 글자는 담지 않는다 — 닫힌 사유 코드와 «개수»만 통과시킨다.
# ★ 이 기록은 «검수 제외» 장부(review_diagnostic_constants.REVIEW_SCOPE_ITEMS)
#   가 아니다. 시제 표기는 문장을 빼지 않고 표시만 고쳐 그대로 싣기 때문에,
#   제외 장부에 넣으면 화면 안내문이 안 뺀 문장을 「…개를 뺐습니다」로 센다
#   (도식 «파생 비율»·«장 이동» 기록과 같은 이유).
STYLE_STEP = "8_문체_표기"
#: 지난 일정(기준일 이전 날짜)의 미래형 절에 원문 기준 표기를 붙인 수의 사유 코드.
#: composer 의 `style_normalizer_constants.PAST_DATED_FUTURE_TENSE` 와 «반드시
#: 같은 값»이어야 한다 — 공유 계층이 feature 를 import 하지 않도록 장 이동
#: 사유 코드(SECTION_MOVE_REASONS)와 같은 방식으로 글자를 적고, composer 쪽
#: 시험이 두 값을 맞댄다.
STYLE_REASON_PAST_DATED_FUTURE_TENSE = "past_dated_future_tense"
STYLE_REASONS = frozenset((STYLE_REASON_PAST_DATED_FUTURE_TENSE,))
#: 사유별 개수를 담는 칸 이름. 값은 «1 이상의 정수»만 받는다 — 0건이면
#: composer 가 기록 자체를 만들지 않으므로(빈 이벤트 금지) 0은 계약 밖이다.
STYLE_COUNTS_FIELD = "사유별"
#: 어느 렌더의 관측인지 — 닫힌 값. 같은 실행에서 문체 기록이 둘 생길 수 있다:
#: 본 경로 «1차» 렌더를 기록한 뒤 보충(RUN_SUPPLEMENTS)이 돌면 «보충» 병합본
#: 렌더가 다시 기록되고, 그때 출고되는 것은 보충 쪽이다. 구별 칸이 없으면
#: 소비자가 출고본의 개수를 고를 수 없고, 보충 대상이 아닌 장의 같은 문장이
#: 두 기록에 거듭 세어진 것도 알 수 없다(2026-09-23 독립 검토). 확보 근거
#: (강등) 보고서의 렌더는 «확보근거»다.
STYLE_RENDER_FIELD = "렌더"
STYLE_RENDER_PRIMARY = "1차"
STYLE_RENDER_SUPPLEMENT = "보충"
STYLE_RENDER_EVIDENCE_AVAILABLE = "확보근거"
STYLE_RENDERS = frozenset((
    STYLE_RENDER_PRIMARY, STYLE_RENDER_SUPPLEMENT, STYLE_RENDER_EVIDENCE_AVAILABLE,
))
