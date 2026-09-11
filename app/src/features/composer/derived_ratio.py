"""파생 비율 재계산 — 인용 조각 «안»의 두 원값으로 되짚어지는 비율만 인정한다.

★ 왜 필요한가 (2026-09-11 인텍에프에이 4차 실행 실측)
  3장 매출 구성 카드의 칸 「매출액 245.14억원(전체 90%)」이 수치 관문에서
  «인용 원문에 없는 수 90»으로 통째로 버려졌다. 그런데 인용 조각 안에는
  제품매출 24,514,287,835와 매출 합계 27,351,053,389이 «둘 다» 있었고,
  24,514,287,835 ÷ 27,351,053,389 × 100 = 89.6283%다. 즉 90%는 지어낸 수가
  아니라 그 조각 안에서 계산되는 참값인데, 관문이 «글자로 있는 수»만 인정해
  맞는 카드를 죽였다.

★ 회사·업종을 가리지 않는다 — 감사보고서는 «비중·점유율»을 인쇄하지 않는다.
  「어느 제품이 매출의 몇 퍼센트」는 언제나 파생값이므로, 이 갈래가 없으면
  어느 회사에서든 매출 구성 카드가 같은 이유로 계속 죽는다.

════════════════════════════════════════════════════════════
★ 왜 «맨 숫자»를 원값으로 쓰는가 — 결속기와 잣대가 갈리는 것이 아니다
════════════════════════════════════════════════════════════

  결속기(`grounding._amount_spans`)는 단위 머리말이 없는 맨 숫자를 원값으로
  쓰지 않는다. 「1,683」이 원인지 천원인지 모르는 채 「1,683억원」의 근거로
  삼으면 자릿수가 통째로 어긋나기 때문이다. 그 판단은 여기서도 그대로 옳다.

  비율은 다르다. **단위가 약분된다.** a와 b의 단위가 «서로 같기만 하면»
  a/b는 그 단위가 무엇이든 같은 값이다. 그래서 이 파일은 금액을 결속하지
  않고 비율만 되짚으며, 대신 **두 원값의 단위 표기가 완전히 같을 것**을
  요구한다(맨 숫자끼리 또는 같은 배율·꼬리끼리). 「245.14억원」과 맨 숫자
  「27,351,053,389」을 섞지 않는다 — 섞으면 단위가 약분되지 않는다.

★ 우연 일치를 막는 다섯 겹 (상수는 `derived_ratio_constants` 참조)
  ① **분모는 «총액 성격의 줄»이어야 한다**(`is_total_row_label`). 가장 크게
     듣는 겹이다. 구성비는 「부분 ÷ 전체」이므로 이 제약은 정의 그 자체다.
  ② **짝의 다른 한쪽은 «그 줄이 적어 낸 금액»이다**(`anchor_values`).
  ③ 원값 후보를 «금액·수량 표기»로 한정한다 — 연도(날짜 표기·맨 네 자리),
     주석 번호·페이지 수 같은 «쉼표 없는 맨 정수»는 후보가 아니다. 차원 판정은
     `grounding._dimension_at` 한 벌을 그대로 쓴다.
  ④ 조각 «하나» 안의 두 값만 짝짓는다. 조각을 가로질러 빌려오지 않는다.
  ⑤ 조각당 조합 수에 상한을 둔다. 넘으면 인정하지 않고 사유만 남긴다.

★ 종류는 «구성비» 하나뿐이다 — 증감률 갈래는 우연 통과율을 두 배로 만들면서
  지켜 줄 시험이 하나도 없어 걷어냈다(2026-09-11 독립 검토).

★ 남는 위험을 숨기지 않는다 (2026-09-11 독립 검토 코퍼스 DART 36건·조각
  10,576개로 생산 함수를 직접 돌려 재측정) — 앵커가 있는 상태에서 «지어낸»
  백분율이 우연히 되짚어지는 비율은 **정수 1.60% · 소수 한 자리 0.23% ·
  소수 두 자리 0.026%**다. 분모를 총액 줄로 가두기 전에는 정수 34.02%였다.
  그래도 0은 아니므로, 그 뒤에 도식 의미 검수(AI)가 한 번 더 본다. 이 수치는
  관문을 다시 만질 때 반드시 다시 재야 한다.

★ 알려진 적용 범위 한계 — 조각이 「(단위: 천원)」 «표 머리말»로만 단위를 주고
  칸에는 맨 숫자를 쓰면, 이 파일은 그 머리말을 읽지 않으므로 앵커가 0개가 되어
  갈래가 아예 돌지 않는다. fail-closed라 틀린 값을 만들지는 않지만, 그런 공시
  에서는 이 수정이 무효다. 표 조각화·머리말 단위 읽기는 별도 과제다.

★ fail-closed — 되짚어지지 않으면 지금까지처럼 «없는 수»로 남는다. 이 파일은
  기존 판정을 뒤집지 않고, 기존 판정이 «없는 수»라고 한 뒤에만 불린다.
"""

from __future__ import annotations

import hashlib
import itertools
import re
from bisect import bisect_right
from dataclasses import dataclass
from decimal import Decimal, DivisionByZero, InvalidOperation, Overflow
from typing import Final, Optional, Sequence

from src.features.composer.derived_ratio_constants import (
    DERIVED_RATIO_KIND_SHARE,
    DERIVED_RATIO_LABEL_LOOKBACK_LINES,
    DERIVED_RATIO_MAX_PAIRS_PER_FRAGMENT,
    DERIVED_RATIO_PAIR_LIMIT_CODE,
    DERIVED_RATIO_RECOMPUTED_CODE,
    DERIVED_RATIO_SOURCE_DIMENSIONS,
    DERIVED_RATIO_TOLERANCE_QUANTA,
    DERIVED_RATIO_TOTAL_ROW_NAMES,
    DERIVED_RATIO_TOTAL_ROW_SUFFIXES,
    RATIO_FULL_SCALE,
)
from src.features.composer.grounding import _dimension_at, _period_at
from src.shared.report_quality.composition_diagnostic_constants import (
    DERIVED_RATIO_STEP,
)
from src.features.composer.grounding_constants import MAGNITUDE_SCALES

# 숫자 표기·연도·백분율 배율은 verify 한 곳에서만 읽는다. 여기서 정규식이나
# 배율을 새로 만들면 문장·도식·결속의 잣대가 넷으로 갈라진다.
from src.features.composer.verify import (
    _BARE_YEAR_RE,
    _DATE_EXPR_RE,
    _NUMBER_UNIT_RE,
    _PERCENT_SCALE,
    _SentenceNumber,
    _number_matches_by_math,
)

#: 맨 숫자를 원값으로 인정하는 최소 조건 — 천 단위 구분 쉼표.
#: ★ 왜 쉼표인가 — 공시 표의 금액은 예외 없이 「24,514,287,835」처럼 쉼표를
#:   찍는다. 반대로 주석 번호(「24. 수익」)·페이지 수·항목 번호는 쉼표가 없다.
#:   쉼표를 요구하면 그 셋이 후보에서 통째로 빠진다 — 목록을 만들지 않고도
#:   «금액 표기»만 남길 수 있는 유일한 표면 신호다.
_THOUSANDS_SEPARATOR: Final[str] = ","


@dataclass(frozen=True)
class RatioSource:
    """비율의 원값이 될 수 있는 «금액·수량» 하나."""

    #: 배율까지 적용한 절대값.
    value: Decimal
    #: 단위 표기(배율 + 꼬리). 맨 숫자는 빈 문자열이다. 같은 표기끼리만 짝짓는다.
    unit: str
    #: `grounding._dimension_at` 판정 — 금액·수량·외화만 원값이 된다.
    dimension: str
    #: `grounding._period_at` 판정. 명시 기간이 없으면 None.
    period: object
    #: 이 값이 «총액 성격의 줄»에 있는가. 구성비의 분모가 될 수 있는 조건이다.
    is_total_row: bool
    #: 조각 안 위치. 기록에는 넣지 않고 기간·행 이름 판정에만 쓴다.
    start: int


@dataclass(frozen=True)
class DerivedRatioResult:
    """재계산 판정 하나 — 인정했는지, 어떤 사유인지, 근거 쌍이 무엇인지."""

    accepted: bool
    reason_code: str
    #: 후보가 적어 낸 백분율 값.
    percent: Decimal
    #: 재계산 종류(`DERIVED_RATIO_KIND_SHARE`). 인정하지 않은 경우 빈 문자열.
    kind: str = ""
    #: 근거 쌍 — a(작은 쪽)와 b(큰 쪽)의 절대값. 인정하지 않은 경우 None.
    numerator: Optional[Decimal] = None
    denominator: Optional[Decimal] = None

    def fingerprint(self) -> str:
        """근거 쌍의 지문 = ``sha256("분자/분모")``. 근거 쌍이 없으면 빈 문자열.

        ★ 왜 금액 자체를 안 남기나 — 진단은 «원문을 담지 않는다»가 이 저장소의
          규칙이다(`verify._machine_check` 머리말, 자매 함수
          `dedupe._log_chapter_sentence_counts`). 금액은 공시에 인쇄된 수이지만,
          기록 칸에 임의의 수를 싣기 시작하면 그 칸으로 원문이 새어 나간다.
        ★ 지문이면 «어느 쌍으로 인정했나»를 사후에 대조할 수 있다 — 그 조각에서
          두 값을 다시 뽑아 같은 지문이 나오는지 보면 된다.
        """

        if self.numerator is None or self.denominator is None:
            return ""
        pair = f"{self.numerator}/{self.denominator}"
        return hashlib.sha256(pair.encode("utf-8")).hexdigest()

    def as_diagnostic(self) -> dict:
        """운영 기록의 «수 칸». 없는 값은 빈 문자열이다(None 아님).

        ⚠️ 빈 문자열을 쓰는 이유 — 실행 기록 계약이 «문자열만» 통과시킨다.
          None을 담으면 항목 전체가 버려져 상한 초과 기록이 사라진다.
        """

        return {"백분율": str(self.percent), "근거지문": self.fingerprint()}


def append_derived_ratio_diagnostic(
    diagnostics: Optional[list[dict]],
    *,
    section_id: str,
    result: DerivedRatioResult,
) -> None:
    """파생 비율 판정을 운영 기록에 남긴다. 원문 글자는 담지 않는다.

    ★ 칸 이름과 통과 규칙은 실행 기록 계약 한 곳에서 온다
      (`composition_diagnostic_constants`). 여기서 이름을 새로 지으면 기록이
      «단계 이름을 못 알아보는 항목»으로 조용히 버려진다.
    """

    if diagnostics is None:
        return
    diagnostics.append(
        {
            "step": DERIVED_RATIO_STEP,
            "장": section_id,
            "사유코드": result.reason_code,
            "종류": result.kind,
            **result.as_diagnostic(),
        }
    )


def stated_percent(number: _SentenceNumber) -> Optional[Decimal]:
    """이 수가 「…%」로 적힌 백분율이면 그 표기값을, 아니면 None.

    ★ 배율이 붙은 백분율(「10만%」)은 여기 들지 않는다 — `scale`이 백분율
      배율과 정확히 같을 때만 «맨 백분율»이다. 배율 어휘에는 0.01이 없으므로
      (`grounding_constants.MAGNITUDE_SCALES`) 이 비교는 백분율만 고른다.
    """

    if number.is_year or not number.unit_marked:
        return None
    if number.scale != _PERCENT_SCALE:
        return None
    return number.token


def _tolerance(percent: Decimal) -> Optional[Decimal]:
    """후보가 «적어 낸 자릿수»로 정한 허용 오차. 자릿수를 못 읽으면 None."""

    exponent = percent.as_tuple().exponent
    if not isinstance(exponent, int) or exponent > 0:
        return None
    quantum = Decimal(1).scaleb(exponent)
    return quantum * DERIVED_RATIO_TOLERANCE_QUANTA


_HANGUL_RE: Final = re.compile(r"[가-힣]")
#: 표의 «뼈대»만 있는 줄 — 숫자·구두점·로마숫자·공백뿐인 줄. 값 줄이 여기 든다.
_TABLE_FILLER_RE: Final = re.compile(r"[\s\d,.\-()△▲%:;·ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]")
#: 행 이름 앞머리의 번호 표기 — 「Ⅰ.」·「1.」·「(2)」·「가.」.
_ROW_NUMBER_PREFIX_RE: Final = re.compile(
    r"^(?:[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+|\(?\d+\)?|[가-힣])[.)]"
)
#: 행 이름에 붙는 괄호 주석 — 「(주석16,19)」·「(단위: 원)」.
_ROW_BRACKET_RE: Final = re.compile(r"[(\[（][^)\]）]*[)\]）]?")


def _line_starts(text: str) -> tuple[list[str], list[int]]:
    """줄 목록과 각 줄의 시작 위치. 한 조각당 한 번만 만든다."""

    lines = text.split("\n")
    starts: list[int] = []
    position = 0
    for line in lines:
        starts.append(position)
        position += len(line) + 1
    return lines, starts


def _row_label(
    text: str, lines: Sequence[str], starts: Sequence[int], position: int
) -> str:
    """그 수가 놓인 «행의 이름». 없으면 빈 문자열.

    ★ 왜 앞 줄을 거슬러 읽나 (2026-09-11 독립 검토 실측) — 운영 평문화는 표
      태그를 개행으로 바꾼다. 그래서 행 이름과 값이 «다른 줄»에 있고, 같은 줄만
      보는 `grounding._cell_label`은 실제 평문에서 이름을 0.9%밖에 못 읽었다.
      실제 손익계산서 조각은 이렇게 생겼다:

          Ⅰ. 매출액
           27,351,053,389      ← 당기
           25,811,194,484      ← 전기

    ★ 같은 줄에 이름이 있으면 그것을 쓴다(한 줄 표기 공시도 있다).
    ★ 값만 있는 줄은 건너뛰고, 이름도 뼈대도 아닌 줄을 만나면 멈춘다 — 위 행의
      이름을 이 행에 붙이지 않기 위해서다.
    """

    index = bisect_right(starts, position) - 1
    if index < 0:
        return ""
    head = text[starts[index]:position]
    if _HANGUL_RE.search(head):
        return head
    floor = max(-1, index - DERIVED_RATIO_LABEL_LOOKBACK_LINES)
    for previous in range(index - 1, floor, -1):
        line = lines[previous]
        if _HANGUL_RE.search(line):
            return line
        if _TABLE_FILLER_RE.sub("", line):
            break
    return ""


def is_total_row_label(label: str) -> bool:
    """이 행 이름이 «총액 성격»인가 (`derived_ratio_constants`의 닫힌 어휘).

    ★ 공시 표는 총계 줄을 「합 계」·「자 본 총 계」처럼 띄어 쓴다. 공백을 지우고
      본다. 앞머리 번호(「Ⅰ.」)와 괄호 주석(「(주석16,19)」)도 이름이 아니다.
    """

    compact = "".join(_ROW_BRACKET_RE.sub(" ", str(label or "")).split())
    if not compact:
        return False
    if compact.endswith(DERIVED_RATIO_TOTAL_ROW_SUFFIXES):
        return True
    return _ROW_NUMBER_PREFIX_RE.sub("", compact) in DERIVED_RATIO_TOTAL_ROW_NAMES


def ratio_source_values(text: str) -> tuple[RatioSource, ...]:
    """조각 원문에서 «비율의 원값이 될 수 있는 수»만 뽑는다.

    ★ 제외하는 것 — 날짜 표기 안의 수(연도·월·일), 맨 네 자리 연도,
      백분율·배수(그 자체가 비율이라 원값이 아니다), 그리고 쉼표 없는 맨 정수
      (주석 번호·페이지 수·항목 번호).
    """

    date_spans = [match.span() for match in _DATE_EXPR_RE.finditer(text)]
    lines, starts = _line_starts(text)
    sources: list[RatioSource] = []
    for match in _NUMBER_UNIT_RE.finditer(text):
        start, end = match.span("num")
        if any(start < other_end and other_start < end
               for other_start, other_end in date_spans):
            continue
        digits = match.group("num")
        magnitude = match.group("mag") or ""
        tail = match.group("tail") or ""
        if not magnitude and not tail:
            # 맨 숫자 — 연도이거나 쉼표가 없으면 금액 표기로 보지 않는다.
            if _BARE_YEAR_RE.fullmatch(digits) or _THOUSANDS_SEPARATOR not in digits:
                continue
        dimension = _dimension_at(text, match.start(), match.end())
        if dimension not in DERIVED_RATIO_SOURCE_DIMENSIONS:
            continue
        try:
            value = Decimal(digits.replace(_THOUSANDS_SEPARATOR, ""))
        except InvalidOperation:
            continue
        if magnitude:
            scale = MAGNITUDE_SCALES.get(magnitude)
            if scale is None:
                continue
            try:
                value = value * scale
            except (InvalidOperation, Overflow):
                continue
        if value <= 0:
            continue
        sources.append(
            RatioSource(
                value=value,
                unit=f"{magnitude}{tail}",
                dimension=dimension,
                period=_period_at(text, match.start()),
                is_total_row=is_total_row_label(
                    _row_label(text, lines, starts, match.start())
                ),
                start=match.start(),
            )
        )
    return tuple(sources)


def anchor_values(
    sources: Sequence[RatioSource], stated: Sequence[_SentenceNumber]
) -> tuple[RatioSource, ...]:
    """같은 줄이 «이미 적어 낸» 금액 표기가 가리키는 원값들.

    ★ 왜 필요한가 — 원값 두 개를 «아무렇게나» 짝지으면 조각 안 어떤 두 값이든
      후보가 되어, 지어낸 백분율이 우연히 맞을 자리가 크게 늘어난다. 지어낸
      수를 걸러 내라고 있는 관문이 지어낸 수를 통과시키면 안 된다.
    ⚠️ 이 겹만으로는 모자랐다 — 앵커만 걸고 분모를 자유롭게 두면 독립 검토
      코퍼스에서 지어낸 정수 백분율의 17.37%가 통과했다. 분모를 총액 줄로
      가두는 겹(`is_total_row_label`)이 함께 있어야 1%대가 된다.
    ★ 그래서 «이 줄이 말한 금액»을 한쪽 끝으로 못 박는다. 카드가 말하는
      파생 비율은 언제나 「내가 방금 말한 이 금액이 저 전체의 몇 퍼센트인가」
      이므로, 이 제약은 기능을 줄이지 않으면서 조합을 n²에서 n으로 줄인다.
    ★ «맞는지»는 문장·도식이 쓰는 환산 대조(`verify._number_matches_by_math`)
      그대로다. 여기서 자릿수 비교를 새로 만들지 않는다.
    """

    anchors: list[RatioSource] = []
    for source in sources:
        pool = frozenset({source.value})
        if any(
            number.unit_marked
            and not number.is_year
            and stated_percent(number) is None
            and _number_matches_by_math(number, pool)
            for number in stated
        ):
            anchors.append(source)
    return tuple(anchors)


def _eligible_pairs(
    sources: Sequence[RatioSource], anchors: Sequence[RatioSource]
) -> tuple[tuple[Decimal, Decimal], ...]:
    """짝지을 수 있는 (작은 값, 큰 값) 쌍. 같은 값 쌍은 한 번만 센다.

    ★ 한쪽 끝은 반드시 «이 줄이 적어 낸» 값(anchor)이다. 나머지 한쪽만
      조각에서 찾는다.
    ★ **큰 쪽(분모)은 «총액 성격의 줄»이어야 한다.** 구성비는 「부분 ÷ 전체」이고,
      전체가 아닌 값을 분모로 쓰면 그것은 구성비가 아니다. 이 제약 없이는 지어낸
      정수 백분율의 34.02%가 통과했다(독립 검토 실측).
    ★ 같은 단위 표기·같은 차원·같은 기간만 짝짓는다. 단위가 다르면 약분되지
      않고, 기간이 다르면 구성비가 아니라 서로 다른 해의 수를 섞는 것이다.
    """

    pairs: set[tuple[Decimal, Decimal]] = set()
    for anchor, other in itertools.product(anchors, sources):
        if anchor.unit != other.unit or anchor.dimension != other.dimension:
            continue
        if anchor.period != other.period:
            continue
        if anchor.value == other.value:
            continue
        part, whole = (
            (anchor, other) if anchor.value < other.value else (other, anchor)
        )
        if not whole.is_total_row:
            continue
        pairs.add((part.value, whole.value))
    return tuple(sorted(pairs))


def _share_percent(low: Decimal, high: Decimal) -> Optional[Decimal]:
    """부분 ÷ 전체 × 100. 계산이 안 되면 None.

    ★ 종류는 구성비 하나뿐이다 — 증감률 갈래는 우연 통과율을 두 배로 만들면서
      지켜 줄 시험이 없어 걷어냈다(`derived_ratio_constants` 참조).
    """

    try:
        return low / high * RATIO_FULL_SCALE
    except (InvalidOperation, DivisionByZero, Overflow):
        return None


def recompute_percent(
    percent: Decimal,
    fragment_texts: Sequence[str],
    stated_numbers: Sequence[_SentenceNumber] = (),
    *,
    max_pairs: int = DERIVED_RATIO_MAX_PAIRS_PER_FRAGMENT,
) -> Optional[DerivedRatioResult]:
    """조각 «하나» 안의 두 원값으로 이 백분율이 되짚어지는지 본다.

    Args:
        percent: 후보가 적어 낸 백분율 값(「90%」이면 ``Decimal("90")``).
        fragment_texts: 그 줄이 «인용한» 조각 원문들. 조각을 이어 붙이지 않고
            하나씩 따로 본다 — 조각을 가로질러 값을 빌려오지 않기 위해서다.
        stated_numbers: 그 줄이 적어 낸 수 전부. 이 중 «금액 표기»가 가리키는
            원값만 짝의 한쪽 끝이 된다(`anchor_values`).
        max_pairs: 조각 하나에서 볼 조합 수 상한.

    Returns:
        · 되짚어졌으면 ``accepted=True`` 결과(근거 쌍 포함).
        · 조합 수가 상한을 넘어 «보지 않기로» 한 조각뿐이면 ``accepted=False``
          결과(사유 코드만). 상한을 넘긴 조각이 있어도 다른 조각에서
          되짚어지면 인정이 우선한다.
        · 그 밖에는 None — 지금까지처럼 «없는 수»로 남긴다.
    """

    tolerance = _tolerance(percent)
    if tolerance is None:
        return None
    limited = False
    for text in fragment_texts:
        if not (text or "").strip():
            continue
        sources = ratio_source_values(text)
        pairs = _eligible_pairs(sources, anchor_values(sources, stated_numbers))
        if len(pairs) > max_pairs:
            limited = True
            continue
        for low, high in pairs:
            candidate = _share_percent(low, high)
            if candidate is not None and abs(candidate - percent) <= tolerance:
                return DerivedRatioResult(
                    accepted=True,
                    reason_code=DERIVED_RATIO_RECOMPUTED_CODE,
                    percent=percent,
                    kind=DERIVED_RATIO_KIND_SHARE,
                    numerator=low,
                    denominator=high,
                )
    if limited:
        return DerivedRatioResult(
            accepted=False,
            reason_code=DERIVED_RATIO_PAIR_LIMIT_CODE,
            percent=percent,
        )
    return None
