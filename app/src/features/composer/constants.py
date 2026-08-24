"""composer 상수 — 기준문서(docs/실행계획_엔진v2/03_단계2_기준문서.md) 3절을 코드로 옮긴 것.

★ 여기 문구는 전부 «작가 AI에게 주는 지침»이다. 검증 게이트가 아니다.
  문장 내용을 어휘·마커·어미 패턴으로 거르는 닫힌 목록 검사를
  이 feature에 만들지 않는다 (01_원칙과_금지.md).
★ 장 id·제목은 v3 정본 목차(report_standard/constants.py의 SECTION_SPECS)와
  같은 값을 «복사»했다. composer→report_standard import 금지 규칙 때문에
  import 대신 상수로 둔다. 정본 목차가 바뀌면 여기도 같이 바꾼다.
"""

from __future__ import annotations

from typing import Final

# ══════════════════════════════════════════════════════════
# 등급 라벨 (기준문서 3절)
# ══════════════════════════════════════════════════════════

#: 인용 원문에 직접 근거가 있는 사실 서술
GRADE_CONFIRMED: Final[str] = "확인"
#: 공식 자료에 기반한 분석·의미 부여 (골든 샘플의 「핵심 해석」 층)
GRADE_INTERPRETED: Final[str] = "해석"
VALID_GRADES: Final[frozenset[str]] = frozenset({GRADE_CONFIRMED, GRADE_INTERPRETED})

# ══════════════════════════════════════════════════════════
# 장 구성 (v3 정본 목차 재사용 — 변경 없음)
# ══════════════════════════════════════════════════════════

SECTION_IDS: Final[tuple[str, ...]] = (
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

SECTION_TITLES: Final[dict[str, str]] = {
    "identity": "기업 정체성",
    "business_model": "사업 구조와 수익 모델",
    "portfolio": "핵심 제품·서비스와 포트폴리오 역할",
    "past_changes": "3개년 주요 변화와 실행",
    "current_challenges": "당면 과제와 대응",
    "future_strategy": "성장 전략",
    "operations_partners": "사업 운영과 파트너 구조",
    "culture": "인재상과 일하는 방식",
    "competitive_position": "동종업계 비교 결과",
}

#: 장별 작성 지침 — 그 장이 «무엇을 말하는 자리»인지 작가에게 알려 준다.
SECTION_GUIDES: Final[dict[str, str]] = {
    "identity": (
        "1장 «기업 정체성» — 이 회사가 스스로를 어떻게 정의하는지 쓴다. "
        "법인의 기본 사실(업종·주력 사업·시장에서의 자기 규정)을 공식 자료 그대로 세우고, "
        "그것이 무엇을 뜻하는지 해석을 덧붙인다."
    ),
    "business_model": (
        "2장 «사업 구조와 수익 모델» — 무엇을 팔아 어떻게 돈을 버는지 쓴다. "
        "사업 부문 구성, 수익이 생기는 경로, 부문 간 비중을 근거 조각과 실적표에서 끌어온다."
    ),
    "portfolio": (
        "3장 «핵심 제품·서비스와 포트폴리오 역할» — 주력 제품·서비스가 무엇이고 "
        "전체 사업 포트폴리오 안에서 각각 어떤 역할을 맡는지 쓴다."
    ),
    "past_changes": (
        "4장 «3개년 주요 변화와 실행» — 지난 3개년의 실적 흐름과 회사가 실제로 한 "
        "주요 변화·실행(투자·출시·조직 개편 등)을 쓴다. 수치는 실적표와 조각 원문에 "
        "있는 값만 쓴다."
    ),
    "current_challenges": (
        "5장 «당면 과제와 대응» — 회사가 지금 마주한 과제(시장·경쟁·규제·재무)와 "
        "그에 대한 회사의 공식적 대응을 쓴다."
    ),
    "future_strategy": (
        "6장 «성장 전략» — 회사가 공식 자료에서 밝힌 미래 계획·신사업·투자 방향을 쓰고, "
        "그것이 어떤 성장 그림인지 해석을 덧붙인다."
    ),
    "operations_partners": (
        "7장 «사업 운영과 파트너 구조» — 사업이 실제로 돌아가는 방식(생산·유통·계약 구조)과 "
        "실명 파트너·협력 관계를 쓴다."
    ),
    "culture": (
        "8장 «인재상과 일하는 방식» — 회사가 공식적으로 밝힌 인재상·조직문화·일하는 방식을 쓴다."
    ),
    "competitive_position": (
        "9장 «동종업계 비교 결과» — 공식 자료에 동종업계·경쟁 관련 근거가 있을 때만 "
        "업계 안에서 이 회사가 서 있는 자리를 쓴다. 근거가 없으면 억지로 만들지 않는다."
    ),
}

#: 장별 목표 문장 수 (최소, 최대) — 골든 샘플 분량 기준 기본 6~12.
#: 지침일 뿐 검증 기준이 아니다. 근거가 부족하면 적게 써도 된다.
DEFAULT_SENTENCE_RANGE: Final[tuple[int, int]] = (6, 12)
SECTION_SENTENCE_RANGES: Final[dict[str, tuple[int, int]]] = {
    section_id: DEFAULT_SENTENCE_RANGE for section_id in SECTION_IDS
}

# ══════════════════════════════════════════════════════════
# 프롬프트 지침문 (기준문서 3절 + 5절)
# ══════════════════════════════════════════════════════════

PROMPT_HEADER: Final[str] = (
    "당신은 취업 준비생이 읽을 «공식 근거 기반 기업분석 보고서»의 한 장(章)을 "
    "산문으로 작성한다.\n"
    "회사: {company}\n"
    "지원 직무·채용공고·지원자 정보는 주어지지 않았다. 개인이나 직무에 맞춘 "
    "내용을 만들지 마라.\n"
)

#: 인용·라벨 규칙 — 기준문서 3절의 핵심. 모든 문장은 인용과 등급을 갖는다.
CITATION_RULES_GUIDE: Final[str] = (
    "작성 규칙:\n"
    "1. 모든 문장에 인용(조각 id 배열)과 등급을 붙인다.\n"
    "   - 등급 «확인»: 인용한 조각 원문에 직접 근거가 있는 사실 서술. "
    "반드시 근거 조각 id를 인용 배열에 넣는다.\n"
    "   - 등급 «해석»: 공식 자료에 기반한 분석·의미 부여. 바탕이 된 조각 id를 "
    "넣되, 종합적 해석이면 빈 배열도 허용된다.\n"
    "2. 조각·실적표에 없는 사실을 지어내지 않는다. 숫자는 조각 원문이나 "
    "실적표에 있는 값만 쓴다.\n"
    "3. 나열식 개조가 아니라 읽히는 하나의 산문으로 잇는다.\n"
    "4. 근거가 부족하면 억지로 채우지 말고 쓸 수 있는 만큼만 쓴다.\n"
    "5. «글» 문장 본문 안에 [숫자]·[인용: …] 같은 대괄호 표기를 직접 쓰지 "
    "않는다. 인용은 반드시 위 «인용» 배열로만 표시한다.\n"
)

#: 금지 주제 — 기준문서 5절 (v3와 동일).
FORBIDDEN_TOPICS_GUIDE: Final[str] = (
    "금지 주제 — 다음은 어떤 장에서도 다루지 않는다:\n"
    "직무별 KPI, 자소서(자기소개서) 작성, 면접 답변, 연봉 추정.\n"
)

#: 목표 문장 수 안내 (장별 값으로 format 한다)
SENTENCE_RANGE_GUIDE: Final[str] = (
    "목표 분량: {minimum}~{maximum}문장.\n"
)

#: AI 출력 JSON 스키마 설명 — 응답은 이 JSON «만» 허용한다.
JSON_SCHEMA_GUIDE: Final[str] = (
    "출력 형식 — 설명·머리말 없이 아래 모양의 JSON만 출력한다:\n"
    '{"문장들": [{"글": "<문장>", "인용": ["<조각id>", "..."], '
    '"등급": "확인" 또는 "해석"}]}\n'
    "\"인용\"의 조각id는 아래 자료 목록의 [조각 n] 번호를 그대로 쓴다.\n"
)

PROMPT_FRAGMENTS_HEAD: Final[str] = "\n수집된 공식 자료 조각 (전체):\n"
PROMPT_TABLE_HEAD: Final[str] = "\n프로그램이 검증해 만든 실적표:\n"

#: JSON 파싱 실패 후 재요청에 덧붙이는 안내
RETRY_REMINDER: Final[str] = (
    "\n(직전 응답을 JSON으로 읽을 수 없었다. 설명 없이 위 «출력 형식»의 "
    "JSON만 다시 출력하라.)\n"
)

#: 같은 장 재요청 횟수 상한 — 계획(04장): 파싱 실패 시 1회 재요청.
PARSE_RETRY_LIMIT: Final[int] = 1

# ══════════════════════════════════════════════════════════
# AI 응답 JSON 키 (매직 문자열 금지)
# ══════════════════════════════════════════════════════════

RESPONSE_SENTENCES_KEY: Final[str] = "문장들"
RESPONSE_TEXT_KEY: Final[str] = "글"
RESPONSE_CITATIONS_KEY: Final[str] = "인용"
RESPONSE_GRADE_KEY: Final[str] = "등급"

# ══════════════════════════════════════════════════════════
# 자료 부족·실패 안내문 (기준문서 3절 — 장 삭제 금지, 정직한 안내)
# ══════════════════════════════════════════════════════════

#: 생성(파싱) 실패 — «우리 쪽 실패»다. 자료 부재로 위장하지 않는다.
NOTICE_COMPOSE_FAILED: Final[str] = (
    "이 장의 본문 생성 결과를 정해진 형식으로 받지 못했습니다. "
    "회사 자료가 없어서가 아니라 생성 단계의 문제이므로, 다시 실행하면 채워질 수 있습니다."
)

#: 자료 부족 — 작가가 정상적으로 답했지만 쓸 문장이 없던 경우.
NOTICE_INSUFFICIENT_EVIDENCE: Final[str] = (
    "공식 자료에서 이 장의 근거를 충분히 찾지 못했습니다. "
    "찾은 범위 안에서만 서술하며, 근거 없는 내용을 채워 넣지 않았습니다."
)

# ══════════════════════════════════════════════════════════
# 공시 원문 주소 — 부록 출처를 «사용자가 직접 열 수 있게» 만든다
# ══════════════════════════════════════════════════════════

#: 전자공시 원문 뷰어 주소 틀. 접수번호(rcept_no)만 넣으면 원문이 열린다.
#: ★ v1 경로(provenance/citations.py)가 쓰는 주소 틀과 «같은 값»이다.
#:   두 벌로 나뉘면 같은 문서가 서로 다른 주소로 나가므로 값을 맞춰 둔다.
DART_DOCUMENT_URL_TEMPLATE: Final[str] = (
    "https://dart.fss.or.kr/dsaf001/main.do?rcpNo={document_id}"
)

#: DART 공시일(`rcept_dt`)의 원래 모양 — 8자리 숫자(`YYYYMMDD`) 하나뿐이다.
#: 모양이 안 맞으면 날짜를 «지어내지 않고» 비운다 (v1 _format_rcept_dt와 같은 규칙).
RCEPT_DT_LENGTH: Final[int] = 8

#: 전자공시 원문을 호스팅하는 기관 도메인. Source.host에 실어 「누가 보관한
#: 문서인가」를 발행 주체(회사)와 구분해 보여 준다.
DART_DOCUMENT_HOST: Final[str] = "dart.fss.or.kr"
