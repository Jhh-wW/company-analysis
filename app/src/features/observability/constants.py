"""이력 저장·지표 계산에 쓰는 값을 한곳에 모은다.

★ 규칙 — 이 값들을 코드 여기저기에 문자열·숫자로 박지 않는다.
   기준이 바뀌면 여기도 같이 고친다.

다루는 범위:
  - 이력 1행 13종
  - 화면 6영역
  - 지표 계산식·산출 주체
  - 수집 완전성의 「존재 자료」 산정법
  - 통과선

★ feature 간 직접 import는 금지다. `core/constants.py`에
  같은 개념(COUNTED_CELLS 등)이 있어도 여기서 값만 맞춰 다시 정의한다.
"""

from __future__ import annotations

from typing import Final

# ══════════════════════════════════════════════════════════
# 저장 파일 — 상대 경로만 갖는다. 절대 경로는 부르는 쪽이 만든다.
# ══════════════════════════════════════════════════════════

#: app/ 기준 상대 경로. 절대 경로로 바꾸는 것은 `core/paths.py`를 아는 쪽(호출부)의 일이다.
#: 이 feature는 `core`를 import하지 않는다 (지시사항 — 저장 경로는 인자로 받는다).
DEFAULT_RECORDS_RELATIVE_PATH: Final[str] = "data/observability/runs.jsonl"

#: 이력 파일 위치를 바꾸는 환경변수. **시험에서 쓴다**.
#: ★ 왜 필요한가 — 이게 없어서 **시험을 돌릴 때마다 진짜 이력에 기록이 쌓였다.**
#:   실측: 이력 813건 중 대부분이 시험 찌꺼기였고, 관리 화면의 「전체 처리 건수」가
#:   사용자가 한 적 없는 조사를 세고 있었다. 저장소(`STORAGE_DB_PATH`)는 이미
#:   같은 방식으로 격리돼 있었는데 **이력만 빠져 있었다.**
ENV_RECORDS_PATH: Final[str] = "OBSERVABILITY_RECORDS_PATH"

# JSONL은 새 SQLite 관측 정본 도입 전 자료를 읽는 호환 사본이다. 1GB 운영
# 디스크에서 이 파일이 끝없이 자라 DB·불변 PDF까지 쓰지 못하게 해서는 안 된다.
# 정본 전환 뒤 신규 기록은 SQLite에만 남고, 옛 실행기에서도 이 물리 상한을 넘지 않는다.
MAX_RECORDS_FILE_BYTES: Final[int] = 64 * 1024 * 1024

# RunRecord의 길이 제한과 고정 필드 수보다 넉넉한 한 줄 상한. 직접 오염된 거대한
# 한 줄이 스트리밍 읽기 한 번으로 큰 메모리를 잡는 것도 막는다.
MAX_RECORD_LINE_BYTES: Final[int] = 16 * 1024

# ══════════════════════════════════════════════════════════
# 이력 1행 — 항목별 허용값 (13종)
# ══════════════════════════════════════════════════════════

# ── 2. 회사 유형 ─────────────────────────────────────────
CORP_TYPE_LISTED: Final[str] = "상장사"
CORP_TYPE_UNLISTED_AUDITED: Final[str] = "비상장 외감"
#: 02_판정 단계에 이르지 못하고 끝난 요청(예: 01_식별 실패)은 유형을 아직 모른다.
CORP_TYPE_UNKNOWN: Final[str] = ""
CORP_TYPE_VALUES: Final[tuple[str, ...]] = (
    CORP_TYPE_LISTED,
    CORP_TYPE_UNLISTED_AUDITED,
    CORP_TYPE_UNKNOWN,
)

# ── 3. 종료 단계 — 「표시 이름」표 ──────
#: 완주 = 보고서가 나갔다 (완성·부분 완성·미완성 전부 포함. 실패가 아니다).
END_STEP_COMPLETE: Final[str] = "완주"
END_STEP_IDENTIFY: Final[str] = "01_식별"
END_STEP_IDENTIFY_ERROR: Final[str] = "01_식별오류"
END_STEP_CONFIRM: Final[str] = "03_확인"
END_STEP_JUDGE: Final[str] = "02_판정"
END_STEP_POSTING: Final[str] = "05.5_공고"
END_STEP_IMAGE_INPUT: Final[str] = "05.5_이미지입력"
END_STEP_IMAGE_ERROR: Final[str] = "05.5_이미지오류"
END_STEP_GATE: Final[str] = "04_게이트"
END_STEP_GENERATE: Final[str] = "05_생성"
END_STEP_OUTPUT: Final[str] = "07_출력"

#: (내부 단계 키, 화면 표시 이름) — ③ 단계별 이탈이 그리는 순서 그대로.
#: ★ 03_수집·06_검증은 독립 종료 이유가 아니라 품질을 재는 단계라 표에서 빠진다.
#: 유료 앞단의 식별 오류·확인 종료·이미지 중단/오류는 거짓 분류를 막기 위해 따로 둔다.
FUNNEL_STAGES: Final[tuple[tuple[str, str], ...]] = (
    (END_STEP_IDENTIFY_ERROR, "회사 식별 오류"),
    (END_STEP_IDENTIFY, "회사 식별 실패"),
    (END_STEP_CONFIRM, "회사 확인에서 종료"),
    (END_STEP_JUDGE, "대상 제외"),
    (END_STEP_IMAGE_INPUT, "이미지 글자 추출 중단"),
    (END_STEP_IMAGE_ERROR, "이미지 처리 오류"),
    (END_STEP_POSTING, "공고 판별 폐기"),
    (END_STEP_GATE, "자료 부족 중단"),
    (END_STEP_GENERATE, "생성 실패"),
    (END_STEP_OUTPUT, "출력 실패"),
    (END_STEP_COMPLETE, "보고서 제공"),
)

#: 이력 1행의 `end_step`이 가질 수 있는 값 전체 (검증용).
END_STEP_VALUES: Final[tuple[str, ...]] = tuple(key for key, _ in FUNNEL_STAGES)

#: 「항상 결함. 0건이어야 함」인 종료 단계 — 3단계 신호 🔴.
#: ① 서비스 상태의 「오류 건수」는 이 두 단계로 끝난 요청 수다.
ERROR_END_STEPS: Final[tuple[str, ...]] = (
    END_STEP_IDENTIFY_ERROR,
    END_STEP_IMAGE_ERROR,
    END_STEP_GENERATE,
    END_STEP_OUTPUT,
)

# ── 4. 캐시 히트 ─────────────────────────────────────────
CACHE_HIT_L1: Final[str] = "1층"
CACHE_HIT_L2: Final[str] = "2층"
CACHE_HIT_NONE: Final[str] = "없음"
CACHE_HIT_VALUES: Final[tuple[str, ...]] = (CACHE_HIT_L1, CACHE_HIT_L2, CACHE_HIT_NONE)

# ── 9·10·11. canonical 1~9장 관측 계약 ──────────────────────────
#: 숫자 표시 번호가 아니라 저장·게이트에서 쓰는 의미 ID를 관측한다.
#: report_standard를 import하지 않는 feature 독립 규칙 때문에 값을 여기에도
#: 명시하되, canonical 순서와 정확히 같게 유지한다.
COUNTED_CELLS: Final[tuple[str, ...]] = (
    "identity",
    "business_model",
    "portfolio",
    "past_changes",
    "current_challenges",
    "future_strategy",
    "operations_partners",
    "culture",
    "competitive_position",
)
#: 신규 canonical 기록의 채움 수·미충족 수 분모.
TOTAL_CELLS: Final[int] = len(COUNTED_CELLS)

#: JSONL에 이미 쌓인 구형 취업보고서 6칸 계약. 신규 기록에는 쓰지
#: 않고, 읽기·집계 하위 호환을 위해서만 보존한다. 예전의 숨긴 `9`는
#: 공개 채움 분모에 포함되지 않았다.
LEGACY_COUNTED_CELLS: Final[tuple[str, ...]] = (
    "1",
    "2",
    "3",
    "4-1",
    "4-2",
    "4-3",
)
LEGACY_TOTAL_CELLS: Final[int] = len(LEGACY_COUNTED_CELLS)
LEGACY_HIDDEN_CELLS: Final[frozenset[str]] = frozenset({"9"})

# ── 12. 판정 등급 · 사람 검토 결과 ────────────────────────
GRADE_COMPLETE: Final[str] = "완성"
GRADE_PARTIAL: Final[str] = "부분 완성"
GRADE_INCOMPLETE: Final[str] = "미완성"
#: 완주하지 못한 요청은 등급 자체가 없다.
GRADE_NONE: Final[str] = ""
#: 화면에 낼 등급 순서 (완성 → 부분 완성 → 미완성). GRADE_NONE은 집계에서 뺀다.
GRADE_ORDER: Final[tuple[str, ...]] = (GRADE_COMPLETE, GRADE_PARTIAL, GRADE_INCOMPLETE)
GRADE_VALUES: Final[tuple[str, ...]] = GRADE_ORDER + (GRADE_NONE,)

HUMAN_CHECK_MATCH: Final[str] = "일치"
HUMAN_CHECK_MISMATCH: Final[str] = "불일치"
#: 사람이 아직 안 봤으면 빈칸.
HUMAN_CHECK_NONE: Final[str] = ""
HUMAN_CHECK_VALUES: Final[tuple[str, ...]] = (
    HUMAN_CHECK_MATCH,
    HUMAN_CHECK_MISMATCH,
    HUMAN_CHECK_NONE,
)

# ══════════════════════════════════════════════════════════
# 지표 6종 — 이름·순서
# ══════════════════════════════════════════════════════════

METRIC_FAITHFULNESS: Final[str] = "원문 일치율"        # Faithfulness · 코드
METRIC_ANSWER_RELEVANCY: Final[str] = "내용 고유성"     # Answer Relevancy · AI → 집계는 코드
METRIC_CONTEXT_PRECISION: Final[str] = "수집 활용률"    # Context Precision · 코드
METRIC_CONTEXT_RECALL: Final[str] = "수집 완전성"       # Context Recall · 코드
METRIC_JUDGE_AGREEMENT: Final[str] = "AI 판정 정합률"   # 사람 → 코드
METRIC_JUDGE_STABILITY: Final[str] = "판정 재현성"      # 코드

#: 화면에 이 순서 그대로 낸다 (정해진 표 순서).
METRIC_ORDER: Final[tuple[str, ...]] = (
    METRIC_FAITHFULNESS,
    METRIC_ANSWER_RELEVANCY,
    METRIC_CONTEXT_PRECISION,
    METRIC_CONTEXT_RECALL,
    METRIC_JUDGE_AGREEMENT,
    METRIC_JUDGE_STABILITY,
)

# ══════════════════════════════════════════════════════════
# 표시 규칙 — % 소수점 자리 (「소수점이 아니라 %로」)
# ══════════════════════════════════════════════════════════

#: % 값을 몇째 자리까지 반올림할지. 표본이 작을 때(첫 20건) 소수점이 있어야
#: 1건 차이가 그래프에서 보인다.
PERCENT_DECIMALS: Final[int] = 1

# ══════════════════════════════════════════════════════════
# 개별 요청 조회 — 최근 목록 길이
# ══════════════════════════════════════════════════════════

#: ⑥ 개별 요청 조회에 내보내는 「최근」 건수 상한. 화면단이 더 필요하면 페이지네이션은
#: 화면단 몫이다 — 여기서는 최신순으로 이만큼만 잘라 준다.
RECENT_LIMIT: Final[int] = 50

# ══════════════════════════════════════════════════════════
# 실행 진단 요약 — 「어느 단계를 로그 한 줄에 담나」
# ══════════════════════════════════════════════════════════

#: 실행이 끝날 때 요약 로그 한 줄에 담을 단계 이름.
#: ★ 이 이름들은 파이프라인(`features/pipeline`)과 작성기(`features/composer`)가
#:   만든다. feature 간 직접 import는 금지라 값만 여기 다시 적는다 — 이름이 바뀌면
#:   요약이 조용히 비므로, 시험이 실제 생산 코드에 같은 이름이 있는지 대조한다.
#: ★ 여기 없는 단계는 요약에 절대 실리지 않는다. 원문 발췌·주소가 섞인 단계를
#:   실수로 로그에 흘리지 않기 위한 「허용 목록」이다.
RUN_SUMMARY_STEP_NAMES: Final[tuple[str, ...]] = (
    "5b_뉴스_수집",
    "6_수집_공식근거사전검사",
    "6_수집_DART부분보고서전환",
    "6_수집_공식근거생성입력",
    # 표가 0개일 때 축별 탈락 사유가 함께 실린다. 이 이름이 빠지면 「매출
    # 구성표를 왜 못 만들었나」가 요약에서 통째로 사라진다.
    "6_수집_매출구성",
    "7_이름후보",
    "3장_대표이름_미사용",
    "3장_대표이름_표",
    "3장_대표이름_표_불가",
    "뉴스_보도표",
    "뉴스_보도표_불가",
    "v2_조각_typed전달",
    "v2_조각_typed전달_불가",
    "v2_composer_완료",
    # 원문·URL·예외 메시지를 갖지 않는 닫힌 실패 경계만 요약에 허용한다.
    "runtime_failure",
)

#: 아직 생산 코드에 없는 단계 이름. 붙는 즉시 요약에 실리도록 미리 넣어 두고,
#: 「이 이름이 실제로 만들어지는가」 시험에서만 뺀다.
RUN_SUMMARY_PLANNED_STEP_NAMES: Final[frozenset[str]] = frozenset()

#: 「부분 보고서로 내려갔다」를 뜻하는 단계. 요약의 등급 칸이 이걸 본다.
RUN_SUMMARY_PARTIAL_STEP: Final[str] = "6_수집_DART부분보고서전환"
#: 「본문 작성까지 끝냈다」를 뜻하는 단계.
RUN_SUMMARY_COMPOSED_STEP: Final[str] = "v2_composer_완료"

RUN_GRADE_FULL: Final[str] = "정식"
RUN_GRADE_PARTIAL: Final[str] = "부분"
#: 본문 작성 전에 멈춰 등급을 말할 수 없는 실행. 지어내지 않고 「미상」으로 둔다.
RUN_GRADE_UNKNOWN: Final[str] = "미상"

#: 요약 로그 한 줄(JSON)의 글자 상한. 넘으면 뒤쪽 단계부터 덜어낸다.
RUN_SUMMARY_MAX_CHARS: Final[int] = 2000
#: 요약에 실을 문자열 하나의 글자 상한. 원문 발췌가 통째로 실리는 것을 막는다.
RUN_SUMMARY_MAX_LABEL_CHARS: Final[int] = 60
#: 요약에 실을 목록·사전 한 개의 항목 수 상한.
RUN_SUMMARY_MAX_ITEMS: Final[int] = 12
#: 요약에서 따라 들어갈 중첩 깊이 상한. 더 깊으면 종류 이름만 남긴다.
RUN_SUMMARY_MAX_DEPTH: Final[int] = 2
#: 잘라낸 자리에 남기는 표시.
RUN_SUMMARY_TRUNCATED_MARK: Final[str] = "…"
#: 주소(URL)를 지운 자리에 남기는 표시.
RUN_SUMMARY_REDACTED_MARK: Final[str] = "[주소가려짐]"
#: 요약 로그 앞머리. 운영 로그에서 이 말로 찾는다.
RUN_SUMMARY_LOG_PREFIX: Final[str] = "실행 진단 요약"

#: 공식 근거 사전검사 단계에 남길 «막힌 사유 코드» 개수 상한.
#:
#: ★ 왜 상한이 필요한가 — 사유 코드는 필수 장(아홉 장)마다 여러 개가 붙을 수
#:   있어 전부 실으면 단계 하나가 진단 전체를 밀어낸다. 값 자체는 이미
#:   ``장:사유코드`` 모양의 닫힌 문자열이라 원문·주소를 담지 않는다
#:   (`shared.report_evidence.logic._scoped_reason_code`).
#: ★ 왜 12인가 — 요약 로그가 목록 하나에서 보여 주는 항목 수
#:   (`RUN_SUMMARY_MAX_ITEMS`)와 같게 둔다. 더 실어도 요약에서 잘려 나간다.
PREFLIGHT_REASON_CODE_LIMIT: Final[int] = RUN_SUMMARY_MAX_ITEMS

# ══════════════════════════════════════════════════════════
# 실행 진단 원본(steps) 보관 — 관리자 화면 전용
# ══════════════════════════════════════════════════════════

#: 실행 기록에 함께 저장하는 steps 원본의 표 형식 판.
RUN_STEPS_SCHEMA_VERSION: Final[int] = 1
#: steps 원본 JSON 한 건의 바이트 상한. 넘으면 뒤쪽 단계부터 덜어내고 표시를 남긴다.
#: 1GB 운영 디스크에서 진단 하나가 저장소를 밀어내지 않게 하는 값이다.
RUN_STEPS_MAX_BYTES: Final[int] = 256 * 1024
#: 관리자 화면이 steps를 보기 좋게 펼칠 때 쓰는 들여쓰기 칸 수.
RUN_STEPS_VIEW_INDENT: Final[int] = 2
#: 「오늘 상태」 화면이 보여 주는 최근 실행 진단 건수 상한.
#: 목록은 원본을 싣지 않으므로 건수만큼 링크 이력을 한 번씩 더 읽는다 — 첫 화면이
#: 느려지지 않게 최근 것만 본다.
RECENT_RUN_STEPS_LIMIT: Final[int] = 20
#: 화면에 실행 번호를 줄여 적을 때 남기는 앞자리 수. 실행끼리 구분하기에 충분하고
#: 표 한 칸을 넘지 않는 길이다.
RUN_ID_SHORT_CHARS: Final[int] = 12
