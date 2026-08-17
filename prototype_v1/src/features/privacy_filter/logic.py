"""공고 판별 3층 — 개인정보 검출·삭제 (Presidio + 한국 패턴). 착수 순서 1-b.

정본: 확정/기획서/01_식별/04_공고판별규칙.md §3층
  · 직접 만들지 않는다 — Microsoft Presidio(MIT · 자체 서버 실행 · 데이터 외부 유출 없음)
  · 기본 상태로는 한글 이름·주민번호·전화를 못 잡는다(2026-08-13 실측)
    → 원인 실측(2026-08-14): 한국어 모델의 개체명 라벨(PS·LC·OG)이 Presidio 기본 매핑(PERSON…)과
      달라 전부 버려지고 있었다. PS→PERSON 매핑 등록으로 해소.
  · 한국 전용 패턴 4종을 정규식으로 등록한다 (주민등록번호·휴대전화·생년월일·라벨 이름)

검출이 3겹인 이유 — 한 겹씩은 구멍이 실측으로 확인됐다:
  ① 정규식(주민번호·전화·생년월일)  — 형식이 있는 값은 확정적으로. NFKC 정규화본에서 찾는다
  ② 라벨 패턴(이름:/성명:/[이름]/Name 뒤) — NER이 놓치는 표기(카나리아7742→QT 오인)를 잡는다
  ③ NER(PS→PERSON) + 모양 필터        — 라벨 없는 본문 속 이름(박영희 대리)을 잡되,
     한글 2~4자 모양만 이름으로 인정한다. 필터가 없으면 ko 모델이 영어 토큰(React·Backend)과
     지역(유성구)·공고 항목어(우대사항·정규직)까지 이름으로 오인해 공고를 파괴한다(2026-08-14 독립 검증)

주민등록번호 정규식에 생년월일 유효성 검사를 넣지 않는 이유:
  카나리아(999999-9997742)는 「세상에 없는 번호」로 설계됐다(01_식별/02_성공기준.md §재는 법).
  유효성 검사를 넣으면 시험 표식 자체를 못 잡는다. 지우개는 과검출이 안전한 방향이다
  (04_공고판별규칙.md §틀렸을 때 — 공고 정보를 더 지우는 실수는 되돌릴 수 있다).

회사명(OG)·지역(LC)은 지우지 않는다 — 공고의 회사명·근무지는 보고서 재료다.

알려진 한계 (2026-08-14 독립 검증 — 수정 대신 문서화·다층 방어로 상쇄):
  · 라벨 없는 단독 줄 이름(이력서 머리글의 「카나리아7742」 한 줄) — NER이 못 잡는다(실측).
    이런 문서는 1층 AI(공고 아님)·2층 값 조합이 문서째 폐기하는 것이 방어선이다.
    3층 단독 잔존율은 실캡처 시험(착수 2) 때 재실측한다.
  · 적대적 난독(원문자 ⓞ①·「앞자리/뒷자리」 분리 서술·이메일 @ 양옆 공백) — 1차 범위 밖.
  · 유선(02~)·안심번호(050)·국제 표기(+82) — 기획서 미결 그대로 1차 범위 밖.
  · 법인등록번호(6-7자리)는 주민번호와 동형이라 함께 지워진다 — 과검출 허용 방향.
  · 라벨 뒤 괄호 3연속(「이름(A)(B)(C):」)은 미검출 — 서식이 극히 드물어 수용(4차 검증).
  · 외자 이름이 어휘 필터와 겹치면(예: 이름 값이 「지원」) 놓친다 — 어휘 필터의 트레이드오프.
    성 띄어쓰기 뒤 글자가 어휘와 겹치는 경우(「성명(한글) 이 근무지」의 「이」)도 같은 계열 —
    자연 문장에서 우연히 나오기 어려운 조합. 실캡처 실측 때 빈도 관찰(5차 검증 권장).

⚠️ S1 주의: 이 모듈의 로그에는 검출된 「값」을 절대 넣지 않는다. 유형·건수만 남긴다.
⚠️ 검출·삭제는 NFKC 정규화본 기준이다 — erase()가 돌려주는 본문도 정규화본(전각→반각)이다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer, RecognizerRegistry
from presidio_analyzer.nlp_engine import NerModelConfiguration, SpacyNlpEngine
from presidio_analyzer.predefined_recognizers import EmailRecognizer, SpacyRecognizer
from presidio_anonymizer import AnonymizerEngine
from presidio_anonymizer.entities import OperatorConfig

from core.logging_util import log_step
from core.personal_patterns import BIRTH_VALUE_RE, PHONE_VALUE_RE, normalize_for_scan

LANGUAGE = "ko"
SPACY_MODEL = "ko_core_news_sm"

# 한국어 모델 라벨 → Presidio 표준 개체명 (이 매핑이 없으면 NER 결과가 전부 버려진다)
KO_ENTITY_MAPPING: dict[str, str] = {
    "PS": "PERSON",
    "LC": "LOCATION",
    "OG": "ORGANIZATION",
    "DT": "DATE_TIME",
    "TI": "DATE_TIME",
    "QT": "QUANTITY",
}

# 주민등록번호 — 6자리+7자리. 구분자(하이픈류·공백·점) {0,3}개 · 숫자 경계로 오인 방지
RRN_REGEX = r"(?<!\d)\d{6}[\s\-‐–—.]{0,3}\d{7}(?!\d)"

# 라벨 뒤 이름 — 「이름/성명/성함/작성자/Name」(괄호·글자 사이 공백 허용) 뒤 구분자(:·-·공백) 다음의
# 한글 덩어리(성 띄어쓰기 1회·카나리아 숫자 허용) 또는 영문 이름(2단어까지).
# 라벨 뒤 부가설명 괄호(「이름(한글):」·「성명(국문):」 — 공식 서식 관용구)는 건너뛴다(2차 검증 실측).
# 「성명(홍길동)」처럼 값이 괄호 안에 든 표기는 별도 분기로 잡는다.
# 「이름을 공개」처럼 라벨 뒤에 조사가 바로 붙으면 구분자가 없어 걸리지 않는다(공고 보호).
# 「지원자」는 「지원자격」(공고 항목)을 제외하고 뒤 한 덩어리만 잡는다.
# 부가설명 어휘 — 「이름(한글) 홍길동」처럼 콜론이 없어도 부가설명으로 인정하는 괄호 내용.
# 자유 내용 괄호는 뒤에 콜론/대시가 있을 때만 부가설명으로 본다 — 안 그러면 「성명(값) 다음단어」의
# 다음 단어를 이름으로 삼킨다(4차 독립 검증 실측: 자격요건·근무지·생년월일이 삭제됨)
_NAME_ANNOT_WORD = r"(?:한\s?글|영\s?문|국\s?문|영어|한자|한문|필수|선택|정자|자필|본명|실명)"
_NAME_ANNOT_PAREN = rf"\(\s*{_NAME_ANNOT_WORD}(?:\s*[/·,]\s*{_NAME_ANNOT_WORD}){{0,2}}\s*\)"
_NAME_LABEL = r"[\[(]?\s*(?:이\s?름|성\s?명|성\s?함|작\s?성\s?자|\b[Nn]ame)\s*[\])]?"
_NAME_VALUE = (r"(?:(?:[가-힣]\s)?[가-힣]{2,10}\d*"
               r"|[A-Za-z][A-Za-z.\-]{1,15}(?:\s[A-Za-z][A-Za-z.\-]{1,15})?)")
NAME_LABEL_REGEX = (
    "(?:"
    # A — 라벨 + 자유 괄호 0~2개 + 콜론/대시 확정 구분자 + 값 (「이름(한글)(영문): 값」)
    rf"{_NAME_LABEL}(?:\s*\([^)\n]{{1,20}}\)){{0,2}}\s*[:：\-‐–—]\s*{_NAME_VALUE}"
    # C — 라벨 + 부가설명 「어휘」 괄호 1~2개 + 공백 + 값 (「이름(한글) 홍길동」 — 콜론 없어도)
    rf"|{_NAME_LABEL}(?:\s*{_NAME_ANNOT_PAREN}){{1,2}}\s+{_NAME_VALUE}"
    # B — 라벨(괄호 없음) + 공백 + 값 (「성명 홍길동」)
    rf"|{_NAME_LABEL}\s+{_NAME_VALUE}"
    # D — 값이 괄호 안 (「성명(홍길동)」). 뒤에 괄호·콜론이 오면 부가설명이므로 A에 양보
    r"|(?:이\s?름|성\s?명|성\s?함)\s*\(\s*[가-힣]{2,10}\d*\s*\)(?!\s*[(:：])"
    r"|지원자(?!격)\s*[:：]?\s*[가-힣]{2,10}\d*"
    ")"
)

# NER 이름 인정 모양 — 한글 2~4자 단일 토큰만 (한국 이름 형태)
NER_NAME_SHAPE_RE = re.compile(r"[가-힣]{2,4}")

# NER 이름 오인 보호어 — 공고에 반드시 남아야 하는 어휘 (ko 모델이 이름으로 오인한 실측 사례 포함).
# 2층 판별 사전(posting_gate)과 목적이 다르다 — 저긴 「공고인가」 판별, 여긴 「지우면 안 되는 말」.
NER_PROTECTED_WORDS: frozenset[str] = frozenset({
    # 항목 헤더
    "자격요건", "지원자격", "필수요건", "자격조건", "필요역량", "우대사항", "우대조건", "우대요건",
    "전형절차", "채용절차", "서류전형", "면접전형", "채용과정", "접수기간", "모집기간", "마감일",
    "상시채용", "채용시", "접수방법", "지원방법", "입사지원", "제출서류", "모집분야", "모집부문",
    "채용분야", "모집직무", "모집인원", "채용인원", "복리후생", "담당업무", "채용공고",
    # 고용형태 값·문서어·직장 일반어
    "고용형태", "근무형태", "정규직", "계약직", "인턴", "파견직", "이력서", "자기소개서", "자소서",
    "지원서", "포트폴리오", "근무지", "근무지역", "연봉", "급여", "복지", "혜택", "직군", "담당자",
    "신입", "경력", "채용", "모집", "지원", "면접", "서류",
    # 2차 독립 검증 오탐 실측분 + 같은 계열 (구조 한계: 목록은 실측 축적 방식 —
    # 근본 개선 여부는 실캡처 20~30장 과삭제율 실측 후 재평가)
    "행사일", "박람회", "유의사항", "최종합격", "서류합격", "최종면접", "실무면접",
})
# 이름이 아닌 지역 접미 — 「유성구」를 이름으로 오인한 실측. 「동」은 실제 이름 끝에 흔해 제외
NON_NAME_SUFFIXES: tuple[str, ...] = ("시", "구", "군")

# 라벨 뒤 「값」 자리에 왔어도 이름이 아닌 어휘 — 서식 안내문(「성명(한글/영문) 생년월일 연락처를
# 기재…」)에서 다음 항목명이 이름으로 오인되는 것을 막는다(4차 독립 검증 실측).
# 조사(은/는/이/가…)가 붙은 형태도 걸러낸다. 한계: 외자 이름이 이 어휘와 겹치면(예: 「지원」) 놓친다.
KR_NAME_VALUE_STOPWORDS: frozenset[str] = NER_PROTECTED_WORDS | frozenset({
    "생년월일", "연락처", "전화번호", "휴대전화", "휴대폰", "이메일", "전자우편", "주소",
    "주민등록번호", "주민번호", "학력", "학력사항", "경력사항", "항목", "필드", "빈칸",
    "기재", "작성", "표기",
})
_TRAILING_PARTICLES = frozenset("은는이가을를의도로에")

# 검출 대상 개체 유형 — OG(회사명)·LC(근무지)는 여기 없다 = 지우지 않는다
TARGET_ENTITIES: tuple[str, ...] = (
    "KR_RRN", "KR_PHONE", "KR_BIRTHDATE", "KR_NAME", "PERSON", "EMAIL_ADDRESS",
)

# 개체 유형 → 삭제 표기 (한국어)
REPLACEMENT_BY_ENTITY: dict[str, str] = {
    "KR_RRN": "[삭제:주민등록번호]",
    "KR_PHONE": "[삭제:전화번호]",
    "KR_BIRTHDATE": "[삭제:생년월일]",
    "KR_NAME": "[삭제:이름]",
    "PERSON": "[삭제:이름]",
    "EMAIL_ADDRESS": "[삭제:이메일]",
}
DEFAULT_REPLACEMENT = "[삭제:개인정보]"

# 정규식은 형식이 확정적이라 높게, 라벨 이름은 문맥 추정이라 낮게
SCORE_REGEX = 0.95
SCORE_NAME_LABEL = 0.7
SCORE_THRESHOLD = 0.4


@dataclass(frozen=True)
class Detection:
    """검출 1건 — 값은 담지 않는다(S1). 위치(정규화본 기준)·유형·점수만."""
    entity_type: str
    start: int
    end: int
    score: float


@dataclass(frozen=True)
class EraseResult:
    """지우개 실행 결과. text는 삭제 완료본(정규화본)이라 저장·로그에 안전하다."""
    text: str
    counts: dict[str, int]  # 유형별 검출 건수


def _build_registry() -> RecognizerRegistry:
    """한국 패턴 4종 + 이메일 + NER 이름을 등록한 인식기 묶음."""
    registry = RecognizerRegistry(supported_languages=[LANGUAGE])
    registry.add_recognizer(PatternRecognizer(
        supported_entity="KR_RRN", name="kr_rrn", supported_language=LANGUAGE,
        patterns=[Pattern("주민등록번호", RRN_REGEX, SCORE_REGEX)]))
    registry.add_recognizer(PatternRecognizer(
        supported_entity="KR_PHONE", name="kr_phone", supported_language=LANGUAGE,
        patterns=[Pattern("휴대전화", PHONE_VALUE_RE.pattern, SCORE_REGEX)]))
    registry.add_recognizer(PatternRecognizer(
        supported_entity="KR_BIRTHDATE", name="kr_birthdate", supported_language=LANGUAGE,
        patterns=[Pattern("생년월일", BIRTH_VALUE_RE.pattern, SCORE_REGEX)]))
    registry.add_recognizer(PatternRecognizer(
        supported_entity="KR_NAME", name="kr_name_label", supported_language=LANGUAGE,
        patterns=[Pattern("라벨 이름", NAME_LABEL_REGEX, SCORE_NAME_LABEL)]))
    registry.add_recognizer(EmailRecognizer(supported_language=LANGUAGE))
    registry.add_recognizer(SpacyRecognizer(
        supported_language=LANGUAGE, supported_entities=["PERSON"]))
    return registry


@lru_cache(maxsize=1)
def get_analyzer() -> AnalyzerEngine:
    """분석 엔진 싱글턴 — 한국어 모델 로딩이 수 초라 1회만 만든다."""
    nlp_engine = SpacyNlpEngine(
        models=[{"lang_code": LANGUAGE, "model_name": SPACY_MODEL}],
        ner_model_configuration=NerModelConfiguration(
            model_to_presidio_entity_mapping=KO_ENTITY_MAPPING),
    )
    return AnalyzerEngine(
        nlp_engine=nlp_engine, registry=_build_registry(),
        supported_languages=[LANGUAGE], default_score_threshold=SCORE_THRESHOLD)


def _keep_person(span_text: str) -> bool:
    """NER PERSON 채택 여부 — 한국 이름 모양(한글 2~4자)이고 보호어·지역 접미가 아닐 때만."""
    t = span_text.strip()
    if not NER_NAME_SHAPE_RE.fullmatch(t):
        return False
    if t in NER_PROTECTED_WORDS:
        return False
    return not t.endswith(NON_NAME_SUFFIXES)


def _keep_labeled_name(span_text: str) -> bool:
    """KR_NAME 채택 여부 — 값 자리의 마지막 토큰이 항목명·공고 어휘면 이름이 아니다."""
    tokens = span_text.split()
    if not tokens:
        return True
    last = tokens[-1]
    if not re.fullmatch(r"[가-힣]{2,10}\d*", last):
        return True  # 영문 값 등은 어휘 판정 대상이 아니다
    if last in KR_NAME_VALUE_STOPWORDS:
        return False
    # 조사 1글자를 떼고 재판정 (「항목은」→「항목」). 2글자 이름은 조사를 떼지 않는다
    if len(last) >= 3 and last[-1] in _TRAILING_PARTICLES and last[:-1] in KR_NAME_VALUE_STOPWORDS:
        return False
    return True


def _analyze(normalized_text: str) -> list:
    """분석 + 오인 필터(NER 모양·값 자리 어휘). 입력은 반드시 normalize_for_scan을 거친 텍스트."""
    results = get_analyzer().analyze(
        text=normalized_text, language=LANGUAGE, entities=list(TARGET_ENTITIES))
    return [r for r in results
            if (r.entity_type != "PERSON" or _keep_person(normalized_text[r.start:r.end]))
            and (r.entity_type != "KR_NAME"
                 or _keep_labeled_name(normalized_text[r.start:r.end]))]


def detect(text: str) -> tuple[Detection, ...]:
    """개인정보 위치를 찾는다. 반환값에 원문 값은 없다 — 위치(정규화본 기준)·유형·점수만."""
    normalized = normalize_for_scan(text)
    return tuple(Detection(r.entity_type, r.start, r.end, round(r.score, 2))
                 for r in _analyze(normalized))


def erase(text: str, run_id: Optional[str] = None) -> EraseResult:
    """검출된 개인정보를 전부 [삭제:유형] 표기로 바꾼다. 반환 본문은 NFKC 정규화본.

    run_id를 주면 로그를 남긴다 — 로그에는 유형·건수·글자수만 (값 금지 · S1).
    """
    normalized = normalize_for_scan(text)
    analyzer_results = _analyze(normalized)
    operators = {"DEFAULT": OperatorConfig("replace", {"new_value": DEFAULT_REPLACEMENT})}
    operators.update({ent: OperatorConfig("replace", {"new_value": mark})
                      for ent, mark in REPLACEMENT_BY_ENTITY.items()})
    cleaned = AnonymizerEngine().anonymize(
        text=normalized, analyzer_results=analyzer_results, operators=operators).text

    counts: dict[str, int] = {}
    for r in analyzer_results:
        counts[r.entity_type] = counts.get(r.entity_type, 0) + 1
    if run_id is not None:
        log_step(run_id, "5.5-3층 개인정보지우개",
                 {"글자수": len(text)}, {"검출건수": counts, "삭제후_글자수": len(cleaned)})
    return EraseResult(text=cleaned, counts=counts)
