"""composer 문장 단위 검증기 (엔진 v2 소단계 3-2).

★ 모든 처분은 «문장 단위»다 — 제거 또는 «해석» 강등뿐이다.
  보고서·장 단위 차단을 만들지 않는다 (기준문서 4절, 01_원칙과_금지.md).
★ 규칙 4개 (04장 3-2절):
  ① 출처 실존 — 인용 조각 id가 수집 목록에 없으면 그 문장 제거.
  ② 수치 검증 — «확인» 문장의 숫자는 인용 조각 원문·실적표에 있어야 한다.
     억원/원/%/배 환산은 ROUND_HALF_UP 재계산으로 허용 (publish.py의 철학 재사용,
     import는 하지 않음 — core/shared에 재사용 가능한 수치 헬퍼가 없음을 실측 확인).
     단위가 붙은 문장 숫자(억원 등)는 원시 토큰 그대로 존재 규칙을 쓰지 않는다
     — 근거 쪽 단위(조각 인접 단위 또는 실적표 unit 필드)와 정규화한 값이
     맞아야 «찾음»이다. 근거 전체에 단위 정보가 어디에도 없으면 확인도
     반증도 못 하므로 제거가 아니라 해석 강등이다.
  ③ 의미 검수 — 문장+인용 원문을 검수 AI에 나란히 보낸다 (writer/verify.py의
     근거 대조 철학 재사용, 단 «애매하면 거짓» → «애매하면 해석 강등»으로 변경).
  ④ 라벨 정합 — 인용 없는 «확인»은 자동 «해석» 강등. 장의 해석 비율>50%는
     로그 경고만 (차단 아님 — 06장 측정에서 드러나게 한다).
★ 어떤 입력에서도 예외로 전체가 죽지 않는다 — 검증기 내부 오류 시
  «확인» 전부 강등이라는 안전한 바닥으로 내려간다 (통과 위장 금지).
★ 닫힌 정규식 게이트 금지 — 여기의 정규식은 «숫자 토큰 추출» 전용이다.
  문장 내용을 어휘·마커·어미로 거르는 검사는 일절 없다.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from decimal import Decimal, DivisionByZero, InvalidOperation, Overflow, ROUND_HALF_UP
from typing import Any, Callable, Final, Optional

from src.features.composer.constants import (
    GRADE_CONFIRMED,
    GRADE_INTERPRETED,
    PARSE_RETRY_LIMIT,
    RETRY_REMINDER,
)
from src.features.composer.logic import (
    AskFn,
    FragmentsInput,
    _strip_inline_citation_markers,
)
from src.features.composer.port import (
    AskFatalError,
    CollectedFragment,
    ComposedReport,
    ComposedSection,
    ComposedSentence,
    PerformanceTable,
    fragments_from_raw,
)

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════
# 값 — 전부 이 파일(3-2 소유) 상수. constants.py는 3-1 소유라 수정하지 않는다.
# ══════════════════════════════════════════════════════════

#: 검수 AI 판정 어휘 — «애매»는 버리지 않고 해석으로 강등된다 (기준문서 4-2).
VERDICT_TRUE: Final[str] = "참"
VERDICT_FALSE: Final[str] = "거짓"
VERDICT_UNCLEAR: Final[str] = "애매"
VALID_VERDICTS: Final[frozenset[str]] = frozenset(
    {VERDICT_TRUE, VERDICT_FALSE, VERDICT_UNCLEAR}
)

#: 검수 응답 JSON 키 (매직 문자열 금지)
REVIEW_VERDICTS_KEY: Final[str] = "판정"
REVIEW_NUMBER_KEY: Final[str] = "번호"
REVIEW_RESULT_KEY: Final[str] = "결과"

#: 장의 해석 비율 경고 문턱 — 기준문서 4-2 「해석 비율이 50%를 넘는 장은 로그 경고만」
INTERPRETED_RATIO_WARN_LIMIT: Final[float] = 0.5

#: 검증이 장을 통째로 비웠을 때의 정직한 안내문.
#: ★ NOTICE_INSUFFICIENT_EVIDENCE(자료 부족)와 다른 사유다 — 자료 부재로 위장하지 않는다.
NOTICE_ALL_SENTENCES_REJECTED: Final[str] = (
    "이 장의 초안 문장들이 근거 대조 검증을 통과하지 못해 싣지 않았습니다. "
    "근거 없는 내용을 싣지 않기 위한 조치이며, 자료 보강 후 다시 실행하면 채워질 수 있습니다."
)

# ── 의미 검수 프롬프트 ──
# writer/verify.py:52-63의 판정 규칙을 재사용하되 두 곳을 바꿨다:
#   · 규칙 2: «반올림·환산도 거짓» → «값이 정확히 일치하는 단위 환산만 같은
#     것으로 본다»로 교체(②의 개선). 이 파일의 ② 수치 검증이 단위를 코드로
#     확인하지만(단위가 붙은 숫자의 원시 토큰 일치는 더 이상 통과시키지
#     않는다), 검수 AI에게도 «값이 실제로 같아야 한다»는 것을 명시해 둘째
#     방어선이 관대한 지시로 뚫리지 않게 한다.
#   · 규칙 5: «애매하면 거짓» → «애매하면 애매로 판정» (→ 해석 강등, 기준문서 4-2).
REVIEW_PROMPT_HEADER: Final[str] = (
    "아래는 기업분석 보고서 초안의 «확인» 등급 문장과, 각 문장이 인용한 근거 자료다.\n"
    "문장마다 판정하라: 이 문장의 내용이 인용한 근거 안에 있는가?\n"
)
REVIEW_PROMPT_RULES: Final[str] = (
    "\n■ 판정 규칙\n"
    "1. 근거에 없는 정보가 한 조각이라도 들어 있으면 «거짓»이다.\n"
    "2. 숫자·연도·고유명사가 근거와 다르면 «거짓»이다. "
    "단, 값이 정확히 일치하는 단위 환산(예: 569,500,000,000원 ↔ 5,695억원)"
    "만 같은 것으로 본다. 단위가 달라 값이 달라지면(예: 5,695억원을 "
    "5,695원·5,695만원으로 쓴 경우) «거짓»이다.\n"
    "3. 근거를 요약하거나 쉬운 말로 바꾼 것은 «참»이다. 뜻이 같으면 된다.\n"
    "4. 근거에 없는 원인·결과·전망을 덧붙였으면 «거짓»이다. "
    "(예: 근거는 「매출이 줄었다」인데 문장이 「경쟁 심화로 매출이 줄었다」면 거짓)\n"
    "5. ★ 애매하면 «애매»로 판정하라. 애매한 문장은 버려지지 않고 "
    "사실 서술이 아닌 «해석»으로 강등된다.\n"
    "6. 당신이 이 회사에 대해 따로 아는 것으로 판단하지 마라. "
    "오직 아래 근거만 보고 판단하라.\n"
)
REVIEW_JSON_GUIDE: Final[str] = (
    "\n출력 형식 — 설명 없이 아래 모양의 JSON만 출력한다:\n"
    '{"판정": [{"번호": <문장 번호>, "결과": "참" 또는 "거짓" 또는 "애매"}]}\n'
)
REVIEW_TABLE_HEAD: Final[str] = "\n■ 프로그램이 검증해 만든 실적표 (이것도 근거다)\n"
REVIEW_EVIDENCE_HEAD: Final[str] = "\n■ 근거 자료 (인용된 조각만)\n"
REVIEW_LIST_HEAD: Final[str] = "\n■ 대조할 문장\n"

# ── 재작성 프롬프트 (불합격 문장 1회 재작성) ──
REWRITE_PROMPT_HEADER: Final[str] = (
    "아래 문장은 근거 대조 검수에서 «근거에 없는 내용이 있다»고 판정되었다.\n"
    "인용한 근거 안에서 말할 수 있는 내용만 남겨 문장을 다시 써라.\n"
    "근거에 없는 정보·숫자·원인·결과·전망은 빼라.\n"
    "설명이나 머리말 없이 고친 문장 한 줄만 출력하라.\n"
)
REWRITE_EVIDENCE_HEAD: Final[str] = "\n근거 원문:\n"
REWRITE_SENTENCE_HEAD: Final[str] = "\n불합격 문장: "

# ── 수치 검증 ──
#: 숫자 토큰 + 바로 뒤 단위. «내용» 검사가 아니라 «숫자와 그 배율» 추출 전용이다.
_NUMBER_UNIT_RE: Final[re.Pattern[str]] = re.compile(
    r"(?P<num>\d+(?:,\d{3})*(?:\.\d+)?)"
    r"(?:\s*(?P<mag>[조억만]))?"
    r"(?:\s*(?P<tail>원|%|퍼센트|배))?"
)
_MAGNITUDE_SCALES: Final[dict[str, Decimal]] = {
    "조": Decimal(10) ** 12,
    "억": Decimal(10) ** 8,
    "만": Decimal(10) ** 4,
}
_PERCENT_SCALE: Final[Decimal] = Decimal("0.01")
_NO_SCALE: Final[Decimal] = Decimal(1)

#: 수치 검증 처분 (문장 단위)
NUMERIC_PASS: Final[str] = "통과"
NUMERIC_REMOVE: Final[str] = "제거"
NUMERIC_DEMOTE: Final[str] = "강등"

#: 재조립 시 장부에 없는 번호의 방어 기본값 표식 (제거 None과 구분)
_MISSING: Final[object] = object()


# ══════════════════════════════════════════════════════════
# 공용 작은 도구
# ══════════════════════════════════════════════════════════


def _normalize_fragments(fragments: FragmentsInput) -> tuple[CollectedFragment, ...]:
    """원시 dict든 어댑터 튜플이든 같은 모양으로 맞춘다 (logic.py와 같은 규칙)."""
    if isinstance(fragments, Mapping):
        return fragments_from_raw(fragments)
    return tuple(fragments)


def _demoted(sentence: ComposedSentence) -> ComposedSentence:
    """«확인» 문장을 «해석»으로 강등한다. 글과 인용은 그대로 둔다."""
    if sentence.grade == GRADE_INTERPRETED:
        return sentence
    return replace(sentence, grade=GRADE_INTERPRETED)


def _extract_payload(raw: str) -> Optional[Any]:
    """응답에서 JSON을 꺼낸다 — 코드 펜스·앞뒤 설명이 붙어도 살린다.

    logic.py의 파싱과 같은 관용 규칙이다. logic의 비공개 함수에 묶이지
    않으려고 작게 다시 두었다 (내용 검사가 아니라 «읽히는가»만 본다).
    """
    text = (raw or "").strip()
    if not text:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        pass
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        return json.loads(text[start : end + 1])
    except (json.JSONDecodeError, ValueError):
        return None


def _safe_ask(ask: AskFn, prompt: str) -> Optional[str]:
    """AI를 부른다. 호출이 죽어도 None으로 삼킨다 — 문장 검증 실패가
    보고서 전체를 멈추면 안 된다.

    ★ 예외다: AskFatalError(예산 소진·billing-uncertain 같은 «요청 전역»
      장애)는 삼키지 않고 재전파한다 — logic.py의 같은 예외와 짝이다.
    """
    try:
        return str(ask(prompt))
    except AskFatalError:
        raise
    except Exception:  # noqa: BLE001 - 검수 호출 실패는 «검수 불능»으로 처리한다
        logger.warning("검수 AI 호출이 실패했다 — 해당 판정은 «불능»으로 처리한다")
        return None


# ══════════════════════════════════════════════════════════
# ② 수치 검증 — 숫자 추출과 환산 대조
# ══════════════════════════════════════════════════════════


@dataclass(frozen=True)
class _SentenceNumber:
    """문장에서 추출한 숫자 하나 — 토큰 값·배율·단위 표기 여부."""

    token: Decimal
    scale: Decimal
    unit_marked: bool


def _extract_numbers(text: str) -> tuple[_SentenceNumber, ...]:
    """글에서 숫자 토큰과 바로 뒤 단위(조/억/만·원·%·배)를 뽑는다."""
    out: list[_SentenceNumber] = []
    for match in _NUMBER_UNIT_RE.finditer(text):
        try:
            token = Decimal(match.group("num").replace(",", ""))
        except InvalidOperation:
            continue
        magnitude = match.group("mag")
        tail = match.group("tail")
        scale = _MAGNITUDE_SCALES.get(magnitude or "", _NO_SCALE)
        if tail in ("%", "퍼센트"):
            scale = scale * _PERCENT_SCALE
        out.append(
            _SentenceNumber(
                token=token, scale=scale, unit_marked=bool(magnitude or tail)
            )
        )
    return tuple(out)


def _evidence_number_pools(
    evidence_texts: Sequence[str],
) -> tuple[frozenset[Decimal], frozenset[Decimal], bool]:
    """근거 글 묶음에서 (원시 토큰 값, 배율 적용 절대값, 단위 정보 존재 여부)를 만든다.

    ★ 세 번째 값은 근거 «어딘가»에 명시적 단위(조각 원문의 인접 단위 또는
      실적표 unit 필드로 채워 넣은 값 — 아래 _table_texts)가 하나라도
      있었는지다. 전부 맨 숫자뿐이면 단위 붙은 문장 숫자를 확인도 반증도
      못 한다(②의 개선 — 하단 _numeric_disposal 참고).
    """
    raw_values: set[Decimal] = set()
    absolute_values: set[Decimal] = set()
    has_unit_context = False
    for text in evidence_texts:
        for number in _extract_numbers(text):
            raw_values.add(number.token)
            if number.unit_marked:
                has_unit_context = True
            try:
                absolute_values.add(number.token * number.scale)
            except (InvalidOperation, Overflow):
                continue
    return frozenset(raw_values), frozenset(absolute_values), has_unit_context


def _number_found(
    number: _SentenceNumber,
    raw_values: frozenset[Decimal],
    absolute_values: frozenset[Decimal],
) -> bool:
    """단위 «없는» 문장 숫자(연도·개수 등) 전용 — 종전 규칙 그대로.

    허용 규칙 2가지:
      ⓐ 토큰 그대로 존재 (예: 실적표 셀 「456」 ↔ 문장 「456곳」)
      ⓑ 배율 적용 절대값이 정확히 존재 — 단위 없는 숫자는 scale이 항상
         1이므로 ⓐ와 같은 값이다.
    ★ 단위 붙은 문장 숫자는 이 함수를 쓰지 않는다 — _number_matches_by_math를
      쓴다(아래, ②의 개선). 단위 없는 숫자는 scale이 늘 1이라 ROUND_HALF_UP
      환산(옛 ⓒ)이 트리거될 일이 없어 여기서는 뺐다.
    """
    if number.token in raw_values:
        return True
    try:
        absolute = number.token * number.scale
    except (InvalidOperation, Overflow):
        return False
    return absolute in absolute_values


def _number_matches_by_math(
    number: _SentenceNumber,
    absolute_values: frozenset[Decimal],
) -> bool:
    """단위 «붙은» 문장 숫자 전용 — 셈이 맞는 경우만 «찾음»이다(②의 개선).

    허용 규칙 2가지:
      ⓑ 배율 적용 절대값이 정확히 존재 (예: 「1,683억원」 ↔ 「168,300,000,000원」)
      ⓒ 근거 절대값을 문장 단위로 환산해 ROUND_HALF_UP 반올림하면 같음
         (예: 원 단위 공시값 168,312,345,678원 ↔ 「1,683억원」).
    ★ 원시 토큰 그대로 존재(옛 ⓐ)는 여기 없다 — 단위가 다르면 숫자가 같아도
      다른 값이다. 실적표 셀 「5,695」(unit=억원)를 「5,695원」이 그대로
      가로채는 사고가 바로 이 규칙 때문이었다(실측 결함).
    """
    try:
        absolute = number.token * number.scale
    except (InvalidOperation, Overflow):
        return False
    if absolute in absolute_values:
        return True
    exponent = number.token.as_tuple().exponent
    if not isinstance(exponent, int):
        return False
    quantum = Decimal(1).scaleb(exponent)
    for candidate in absolute_values:
        try:
            converted = (candidate / number.scale).quantize(
                quantum, rounding=ROUND_HALF_UP
            )
        except (InvalidOperation, DivisionByZero, Overflow):
            continue
        if converted == number.token:
            return True
    return False


def _table_row_cell_texts(table: Optional[PerformanceTable]) -> tuple[str, ...]:
    """실적표 «행» 셀에 표의 unit을 이어 붙여 단위를 아는 근거로 만든다.

    ★ 표 셀은 「1,683」처럼 맨 숫자다 — 단위는 표 전체의 unit 필드
      (예: "억원")에 있다. 그 단위를 셀 숫자 뒤에 그대로 이어 붙이면
      _NUMBER_UNIT_RE가 사람이 쓴 "1,683억원"과 «똑같은 모양»으로 단위를
      읽는다. unit이 비었으면 표에도 단위 정보가 없다는 뜻이므로 맨 숫자
      그대로 둔다(«단위 불명»과 «단위 원» 구분).
    """
    if table is None:
        return ()
    unit = (table.unit or "").strip()
    cells = [cell.strip() for row in table.rows for cell in row if cell.strip()]
    if not unit:
        return tuple(cells)
    return tuple(f"{cell}{unit}" for cell in cells)


def _table_texts(table: Optional[PerformanceTable]) -> tuple[str, ...]:
    """실적표를 수치 대조용 글 조각들로 편다. 표가 없으면 빈 튜플.

    ★ 행 셀만 표의 unit을 붙인다(②의 개선) — 캡션·머리글(연도 등)은
      금액이 아니므로 단위를 붙이지 않는다.
    """
    if table is None:
        return ()
    cells: list[str] = [table.caption, table.unit]
    cells.extend(table.headers)
    cells.extend(_table_row_cell_texts(table))
    return tuple(cell for cell in cells if cell)


def _numeric_disposal(
    sentence: ComposedSentence,
    frag_by_id: Mapping[str, CollectedFragment],
    table_texts: Sequence[str],
) -> str:
    """«확인» 문장의 숫자를 인용 조각 원문·실적표와 대조해 처분을 정한다.

    ★ 「핵심 vs 부수」 판단 기준 (04장 3-2절 2번의 지시로 명시):
      · 단위(조/억/만·원·%·배)가 붙은 숫자는 금액·비율 주장 그 자체다 —
        _number_matches_by_math(셈이 맞는지만 본다, 옛 ⓐ 없음)로 확인하고,
        틀리면 원칙적으로 **문장 제거**다. 단, 근거 «전체»에 단위 정보가
        어디에도 없으면(맨 숫자뿐) 확인도 반증도 못 하므로 **해석 강등**에
        그친다(제거 아님 — ②의 개선).
      · 단위 없는 맨 숫자(연도·개수 등)는 서술의 부수 정보다 —
        실패해도 문장 뼈대는 남을 수 있으므로 **해석 강등**에 그친다.
    """
    numbers = _extract_numbers(sentence.text)
    if not numbers:
        return NUMERIC_PASS
    cited_texts = [
        frag_by_id[citation].text
        for citation in sentence.citations
        if citation in frag_by_id
    ]
    raw_values, absolute_values, has_unit_context = _evidence_number_pools(
        [*cited_texts, *table_texts]
    )
    remove = False
    demote = False
    for number in numbers:
        if number.unit_marked:
            if _number_matches_by_math(number, absolute_values):
                continue
            if has_unit_context:
                remove = True
            else:
                # 근거 전체가 단위 정보 없는 맨 숫자뿐 — 확인도 반증도 못 한다.
                demote = True
        elif not _number_found(number, raw_values, absolute_values):
            demote = True
    if remove:
        return NUMERIC_REMOVE
    if demote:
        return NUMERIC_DEMOTE
    return NUMERIC_PASS


# ══════════════════════════════════════════════════════════
# ①②④ 기계 검증 — 출처 실존 → 라벨 정합(인용 없는 확인) → 수치
# ══════════════════════════════════════════════════════════


def _machine_check(
    sentences: Sequence[ComposedSentence],
    frag_by_id: Mapping[str, CollectedFragment],
    table_texts: Sequence[str],
) -> list[ComposedSentence]:
    """AI 없이 코드로 확정할 수 있는 3가지 검증. 전부 문장 단위 처분이다."""
    kept: list[ComposedSentence] = []
    for sentence in sentences:
        # ① 출처 실존 — 깨진 인용이 «하나라도» 있는 문장은 제거한다.
        #   깨진 인용이 달린 문장은 지어낸 것과 구별할 방법이 없다.
        if any(citation not in frag_by_id for citation in sentence.citations):
            logger.info("인용 조각이 실존하지 않아 문장 제거: %.60s", sentence.text)
            continue
        # ④-a 라벨 정합 — 인용 없는 «확인»은 사실 주장을 뒷받침할 근거가 없다.
        #   제거가 아니라 «해석» 강등이다 (분석으로서의 가치는 남긴다).
        if sentence.grade == GRADE_CONFIRMED and not sentence.citations:
            sentence = _demoted(sentence)
        # ② 수치 검증 — «확인» 문장만. 해석은 사실 주장이 아니므로 대상이 아니다.
        if sentence.grade == GRADE_CONFIRMED:
            disposal = _numeric_disposal(sentence, frag_by_id, table_texts)
            if disposal == NUMERIC_REMOVE:
                logger.info(
                    "단위 붙은 수치가 근거에 없어 문장 제거: %.60s", sentence.text
                )
                continue
            if disposal == NUMERIC_DEMOTE:
                logger.info(
                    "부수 수치가 근거에 없어 해석 강등: %.60s", sentence.text
                )
                sentence = _demoted(sentence)
        kept.append(sentence)
    return kept


# ══════════════════════════════════════════════════════════
# ③ 의미 검수 — 검수 AI 대조 + 불합격 1회 재작성
# ══════════════════════════════════════════════════════════


@dataclass(frozen=True)
class _ReviewItem:
    """검수 대조 항목 하나 — 보고서 전체를 관통하는 고유 번호를 갖는다."""

    number: int
    sentence: ComposedSentence


def _render_table_evidence(table: Optional[PerformanceTable]) -> str:
    """검수 프롬프트에 싣는 실적표 — 표 수치를 근거로 쓴 문장을 살리기 위함."""
    if table is None or not table.rows:
        return ""
    caption = table.caption
    if table.unit:
        caption = f"{caption} (단위: {table.unit})"
    lines = [REVIEW_TABLE_HEAD, f"{caption}\n"]
    if table.headers:
        lines.append(" | ".join(table.headers) + "\n")
    for row in table.rows:
        lines.append(" | ".join(row) + "\n")
    return "".join(lines)


def _build_review_prompt(
    items: Sequence[_ReviewItem],
    frag_by_id: Mapping[str, CollectedFragment],
    table_evidence: str,
) -> str:
    """문장과 근거를 «나란히» 놓는 대조 지시문 (writer/verify.py의 핵심 철학).

    ★ 같은 조각을 여러 문장이 인용해도 원문은 한 번만 싣는다 — 그래서
      원문을 자르지 않는다 (writer의 500자 절단은 「근거에 있는데 없다」는
      오판을 낳는다고 스스로 경고했다).
    """
    cited_ids: list[str] = []
    for item in items:
        for citation in item.sentence.citations:
            if citation in frag_by_id and citation not in cited_ids:
                cited_ids.append(citation)
    parts = [REVIEW_PROMPT_HEADER, REVIEW_PROMPT_RULES, REVIEW_JSON_GUIDE]
    if table_evidence:
        parts.append(table_evidence)
    parts.append(REVIEW_EVIDENCE_HEAD)
    for fragment_id in cited_ids:
        parts.append(f"[조각 {fragment_id}] {frag_by_id[fragment_id].text}\n")
    parts.append(REVIEW_LIST_HEAD)
    for item in items:
        citation_label = (
            ", ".join(f"조각 {c}" for c in item.sentence.citations) or "(없음)"
        )
        parts.append(
            f"\n[{item.number}] (인용: {citation_label})\n"
            f"  문장: {item.sentence.text}\n"
        )
    return "".join(parts)


def _parse_verdicts(raw: Optional[str]) -> Optional[dict[int, str]]:
    """검수 응답을 {번호: 판정}으로 바꾼다. 통째로 못 읽으면 None(재요청 대상).

    개별 항목의 관용 규칙:
      · 계약 밖 판정값 → «애매» (강등으로 흐른다 — 통과 위장 금지)
      · 같은 번호의 모순 중복 → «애매»
      · bool 번호(True는 int의 하위 타입) → 버림
    """
    if raw is None:
        return None
    payload = _extract_payload(raw)
    if not isinstance(payload, Mapping):
        return None
    entries = payload.get(REVIEW_VERDICTS_KEY)
    if not isinstance(entries, list):
        return None
    out: dict[int, str] = {}
    for entry in entries:
        if not isinstance(entry, Mapping):
            continue
        number = entry.get(REVIEW_NUMBER_KEY)
        if isinstance(number, bool) or not isinstance(number, int):
            continue
        result = str(entry.get(REVIEW_RESULT_KEY) or "").strip()
        if result not in VALID_VERDICTS:
            result = VERDICT_UNCLEAR
        if number in out and out[number] != result:
            out[number] = VERDICT_UNCLEAR
        else:
            out[number] = result
    if not out:
        return None
    return out


def _ask_verdicts(
    ask: AskFn,
    items: Sequence[_ReviewItem],
    frag_by_id: Mapping[str, CollectedFragment],
    table_evidence: str,
) -> Optional[dict[int, str]]:
    """검수 AI 1회 호출(+파싱 실패 시 1회 재요청). 그래도 실패면 None."""
    prompt = _build_review_prompt(items, frag_by_id, table_evidence)
    verdicts = _parse_verdicts(_safe_ask(ask, prompt))
    retries = 0
    while verdicts is None and retries < PARSE_RETRY_LIMIT:
        retries += 1
        verdicts = _parse_verdicts(_safe_ask(ask, prompt + RETRY_REMINDER))
    return verdicts


def _ask_rewrite(
    ask: AskFn,
    sentence: ComposedSentence,
    frag_by_id: Mapping[str, CollectedFragment],
) -> str:
    """불합격 문장 1회 재작성. 실패하면 빈 문자열(→ 호출한 쪽이 제거)."""
    parts = [REWRITE_PROMPT_HEADER, REWRITE_EVIDENCE_HEAD]
    for citation in sentence.citations:
        fragment = frag_by_id.get(citation)
        if fragment is not None:
            parts.append(f"[조각 {citation}] {fragment.text}\n")
    parts.append(f"{REWRITE_SENTENCE_HEAD}{sentence.text}\n")
    raw = _safe_ask(ask, "".join(parts))
    if raw is None:
        return ""
    # 코드 펜스·빈 줄을 걷어내고 첫 실속 있는 한 줄만 쓴다 (한 줄만 요구했다)
    for line in raw.strip().splitlines():
        stripped = line.strip().strip("`").strip()
        if not stripped:
            continue
        # 재작성 응답도 작가 응답과 같은 흉내낸 인용 대괄호 위험이 있다
        # (logic._sentence_from_item과 같은 형식 정리, critical 결함 재발 방지).
        cleaned = _strip_inline_citation_markers(stripped)
        if cleaned:
            return cleaned
    return ""


def _rewrite_and_recheck(
    ask: AskFn,
    targets: Sequence[_ReviewItem],
    frag_by_id: Mapping[str, CollectedFragment],
    table_texts: Sequence[str],
    table_evidence: str,
    final: dict[int, Optional[ComposedSentence]],
) -> None:
    """«거짓» 판정 문장들: 재작성 1회 → 수치 재검증 → 재검수 → 최종 처분.

    최종 처분 규칙:
      · 재작성 실패(빈 응답·호출 실패) → 제거 — 이미 거짓으로 판정된 글이다.
      · 재작성문 수치 검증: 제거/강등 처분은 기계 검증과 같은 기준.
      · 재검수 «참» → 재작성문을 «확인»으로 유지.
      · 재검수 «애매» → 재작성문을 «해석» 강등.
      · 재검수 «거짓» 또는 재검수 자체 불능 → 제거.
        ★ 첫 검수 불능(전 문장 강등)과 달리 여기는 이미 한 번 «거짓»이었던
          문장이라, 확인 못 한 채 남기는 쪽이 더 위험하다.
    """
    recheck_items: list[_ReviewItem] = []
    for item in targets:
        rewritten_text = _ask_rewrite(ask, item.sentence, frag_by_id)
        if not rewritten_text:
            final[item.number] = None
            continue
        candidate = replace(item.sentence, text=rewritten_text)
        disposal = _numeric_disposal(candidate, frag_by_id, table_texts)
        if disposal == NUMERIC_REMOVE:
            final[item.number] = None
        elif disposal == NUMERIC_DEMOTE:
            final[item.number] = _demoted(candidate)
        else:
            recheck_items.append(replace(item, sentence=candidate))
    if not recheck_items:
        return
    verdicts = _ask_verdicts(ask, recheck_items, frag_by_id, table_evidence)
    for item in recheck_items:
        verdict = (
            VERDICT_FALSE
            if verdicts is None
            else verdicts.get(item.number, VERDICT_UNCLEAR)
        )
        if verdict == VERDICT_TRUE:
            final[item.number] = item.sentence
        elif verdict == VERDICT_UNCLEAR:
            final[item.number] = _demoted(item.sentence)
        else:
            final[item.number] = None


def _semantic_review(
    groups: Sequence[Sequence[ComposedSentence]],
    frag_by_id: Mapping[str, CollectedFragment],
    table_texts: Sequence[str],
    table: Optional[PerformanceTable],
    ask: AskFn,
) -> list[list[ComposedSentence]]:
    """남은 «확인» 문장 전부를 한 번의 검수 호출로 대조한다.

    ★ «해석» 문장은 대상이 아니다 — 해석은 분석·의미 부여 층이라
      「근거에 그대로 있는가」라는 잣대 자체가 맞지 않는다 (기준문서 3절).
    ★ 검수가 통째로 불능이면(재요청까지 실패) «확인» 전부를 해석으로
      강등한다 — 제거(차단)도, 검증 없는 통과 위장도 하지 않는다.
    """
    items: list[_ReviewItem] = []
    position_numbers: dict[tuple[int, int], int] = {}
    number = 0
    for group_index, group in enumerate(groups):
        for sentence_index, sentence in enumerate(group):
            if sentence.grade != GRADE_CONFIRMED:
                continue
            number += 1
            position_numbers[(group_index, sentence_index)] = number
            items.append(_ReviewItem(number=number, sentence=sentence))
    if not items:
        return [list(group) for group in groups]

    table_evidence = _render_table_evidence(table)
    final: dict[int, Optional[ComposedSentence]] = {}
    verdicts = _ask_verdicts(ask, items, frag_by_id, table_evidence)
    if verdicts is None:
        logger.warning(
            "의미 검수 응답을 받지 못해 «확인» 문장 %d개를 전부 해석으로 강등한다 "
            "(제거 아님)",
            len(items),
        )
        for item in items:
            final[item.number] = _demoted(item.sentence)
    else:
        rewrite_targets: list[_ReviewItem] = []
        for item in items:
            verdict = verdicts.get(item.number, VERDICT_UNCLEAR)
            if verdict == VERDICT_TRUE:
                final[item.number] = item.sentence
            elif verdict == VERDICT_FALSE:
                rewrite_targets.append(item)
            else:
                # «애매» 또는 판정 누락 — 버리지 않고 해석으로 강등한다
                final[item.number] = _demoted(item.sentence)
        if rewrite_targets:
            _rewrite_and_recheck(
                ask, rewrite_targets, frag_by_id, table_texts, table_evidence, final
            )

    rebuilt: list[list[ComposedSentence]] = []
    for group_index, group in enumerate(groups):
        out: list[ComposedSentence] = []
        for sentence_index, sentence in enumerate(group):
            item_number = position_numbers.get((group_index, sentence_index))
            if item_number is None:
                out.append(sentence)
                continue
            result = final.get(item_number, _MISSING)
            if result is _MISSING:
                # 장부에 없는 번호(내부 결함) — 지우지 말고 강등으로 방어한다
                out.append(_demoted(sentence))
            elif result is not None:
                out.append(result)
        rebuilt.append(out)
    return rebuilt


# ══════════════════════════════════════════════════════════
# ④-b 해석 비율 경고 + 진입 함수
# ══════════════════════════════════════════════════════════


def _warn_if_interpretation_heavy(
    section_id: str, sentences: Sequence[ComposedSentence]
) -> None:
    """장의 해석 비율이 50%를 넘으면 로그 경고만 남긴다 — 차단 아님."""
    total = len(sentences)
    if total == 0:
        return
    interpreted = sum(1 for s in sentences if s.grade == GRADE_INTERPRETED)
    if interpreted / total > INTERPRETED_RATIO_WARN_LIMIT:
        logger.warning(
            "장 %s: 해석 등급 %d/%d — 비율 50%%를 넘었다 (차단 아님, 측정용 경고)",
            section_id,
            interpreted,
            total,
        )


def _demote_all_confirmed_report(report: ComposedReport) -> ComposedReport:
    """비상 바닥: 검증기 내부 오류 시 «확인» 전부를 해석으로 강등해 돌려준다.

    검증 못 한 문장을 «확인»(검증된 사실)으로 내보내는 것이 유일하게
    금지된 결말이다. 제거(차단)도 하지 않는다.
    """
    sections = tuple(
        ComposedSection(
            section_id=section.section_id,
            sentences=tuple(_demoted(s) for s in section.sentences),
            notice=section.notice,
        )
        for section in report.sections
    )
    summary = tuple(_demoted(s) for s in report.summary)
    return ComposedReport(sections=sections, summary=summary)


def _verify_report_inner(
    report: ComposedReport,
    fragments: FragmentsInput,
    performance_table: Optional[PerformanceTable],
    ask: AskFn,
) -> ComposedReport:
    frag_by_id = {
        fragment.fragment_id: fragment
        for fragment in _normalize_fragments(fragments)
    }
    table_texts = _table_texts(performance_table)

    # 1) 기계 검증 (출처 실존 → 라벨 정합 → 수치) — 장·요약 전부 문장 단위
    checked_groups = [
        _machine_check(section.sentences, frag_by_id, table_texts)
        for section in report.sections
    ]
    checked_groups.append(_machine_check(report.summary, frag_by_id, table_texts))

    # 2) 의미 검수 — 남은 «확인» 문장을 보고서 전체 한 묶음으로 대조
    reviewed_groups = _semantic_review(
        checked_groups, frag_by_id, table_texts, performance_table, ask
    )
    reviewed_summary = reviewed_groups.pop()

    # 3) 재조립 + 해석 비율 경고 + 비워진 장의 정직한 안내문
    out_sections: list[ComposedSection] = []
    for section, kept in zip(report.sections, reviewed_groups):
        notice = section.notice
        if section.sentences and not kept and not notice:
            # 초안엔 문장이 있었는데 검증이 전부 걷어낸 장 — 자료 부재로 위장하지 않는다
            notice = NOTICE_ALL_SENTENCES_REJECTED
        _warn_if_interpretation_heavy(section.section_id, kept)
        out_sections.append(
            ComposedSection(
                section_id=section.section_id,
                sentences=tuple(kept),
                notice=notice,
            )
        )
    return ComposedReport(
        sections=tuple(out_sections), summary=tuple(reviewed_summary)
    )


def verify_report(
    report: ComposedReport,
    fragments: FragmentsInput,
    performance_table: Optional[PerformanceTable],
    ask: AskFn,
) -> ComposedReport:
    """진입 함수 — 규칙 ①~④를 보고서 전체에 문장 단위로 적용한다.

    Args:
        report: compose_sections가 만든 초안 (summary가 차 있으면 같이 검증).
        fragments: 수집 조각 — real.py 원시 dict 또는 CollectedFragment 시퀀스.
        performance_table: 프로그램이 검증해 만든 실적표. 없으면 None.
        ask: 검수·재작성용 AI 호출 주입 함수 (작가와 «다른 호출» —
            Generator/Evaluator 분리는 부르는 쪽이 별도 클로저로 보장한다).

    Returns:
        검증된 ComposedReport. 어떤 입력에서도 예외를 던지지 않으며,
        장 개수·순서는 입력 그대로다 (장 삭제 없음).
    """
    try:
        return _verify_report_inner(report, fragments, performance_table, ask)
    except AskFatalError:
        # 요청 전역 장애 — «검증기 내부 오류»로 위장하지 않고 그대로 재전파한다.
        raise
    except Exception:  # noqa: BLE001 - 검증기 결함이 보고서 생산을 멈추면 안 된다
        logger.exception(
            "검증기 내부 오류 — «확인» 전부를 해석으로 강등해 돌려준다 (차단 금지)"
        )
        try:
            return _demote_all_confirmed_report(report)
        except Exception:  # noqa: BLE001 - 마지막 방어: 입력이라도 돌려준다
            return report


def verify_sentences(
    sentences: Sequence[ComposedSentence],
    fragments: FragmentsInput,
    performance_table: Optional[PerformanceTable],
    ask: AskFn,
) -> tuple[ComposedSentence, ...]:
    """문장 묶음 하나에 같은 규칙 전부를 적용한다 — 3-3 요약 검증 재사용용."""
    try:
        frag_by_id = {
            fragment.fragment_id: fragment
            for fragment in _normalize_fragments(fragments)
        }
        table_texts = _table_texts(performance_table)
        checked = _machine_check(sentences, frag_by_id, table_texts)
        reviewed = _semantic_review(
            [checked], frag_by_id, table_texts, performance_table, ask
        )
        return tuple(reviewed[0])
    except AskFatalError:
        raise  # 요청 전역 장애 — 위 verify_report와 같은 이유로 재전파한다
    except Exception:  # noqa: BLE001 - 위 verify_report와 같은 비상 바닥
        logger.exception(
            "문장 검증 내부 오류 — «확인» 전부를 해석으로 강등해 돌려준다"
        )
        return tuple(_demoted(sentence) for sentence in sentences)
