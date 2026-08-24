"""v2 출고 전 중복 «검출» — 아직 막지 않는다 (엔진 v2 인수 작업).

★ 왜 필요한가 — `docs/REPORT_STRUCTURE.md`「사실 단일 소유 원칙」·「출력 전
  중복 검사 게이트」는 "같은 사실이 두 번 발견되면 PDF 출력을 중단한다"고
  정한다. v1은 `report_standard/publish.py:validate_publishable`이 이 게이트를
  본다. v2는 `FactRecord`(구조화된 사실 원장)를 만들지 않으므로 v1 게이트를
  그대로 가져다 쓸 수 없고, 실측 확인상 v2 출고 경로(export_pdf/release.py·
  web/routers/reports.py)는 `composer.validate.validate_v2`의 3검사만 통과하면
  그대로 나간다 — 중복 검사가 «전혀» 없다.

★ ⛔ 이 모듈은 검출만 한다. 어디에서도 예외를 던지지 않고, `validate_v2`의
  게이트에도 배선하지 않는다. 정상 보고서까지 막을 오탐 위험을 사람이 실제
  보고서 실측으로 먼저 확인한 뒤 막을지 정하기 위해서다 — 잘못 막으면 중복이
  나가는 것보다 나쁘다(사용자에게 보고서가 «아무것도» 안 감).

★ v1 원리를 v2 자료로 옮긴 방법 — v1의 `_semantic_duplicate_key`(publish.py)는
  `FactRecord`의 구조화된 필드(법인명·claim_type·raw_value 등)로 사실의 «의미
  키»를 만든다. v2 렌더 결과(`pipeline.port.Report`)에는 그런 구조화 필드가
  없고 자유 산문(`prose_lines`)과 표(`ReportTable`)뿐이다. 그래서 이 모듈은
  정본이 정의한 사실 식별자 4요소(대상+지표/사건+기간·시점+값/상태) 중 v2
  산문·표에서 «신뢰 가능하게» 뽑을 수 있는 (값+단위+기간)만 키로 쓰고,
  지표 힌트(metric_hint)는 판정에 넣지 않고 사람이 볼 참고 정보로만 남긴다
  — 지표를 문장에서 정확히 뽑아낼 구조화 자료가 없어 오판정 위험이 크기
  때문이다(§5 참고).

★ 소유권 위반(섹션별 사실 소유권 표와 다른 장에 사실이 있는지)은 이 모듈이
  판정하지 않는다. v2 문장에는 "이 문장이 무슨 지표를 말하는가"를 구조화한
  필드가 없어 신뢰 가능한 판정을 만들 수 없다 — 「확인 못 함」으로 남긴다.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Final

from src.features.composer.constants import SECTION_TITLES
from src.features.composer.render import SECTION_DISPLAY_NUMBERS
from src.features.pipeline.port import Report, ReportSection, ReportTable

#: 확정도 두 단계 — 정본이 말하는 "발견되면 막는다"를 그대로 자동화하기 전에,
#: 사람이 먼저 오탐을 눈으로 골라낼 수 있도록 나눈다.
CONFIDENCE_CONFIRMED: Final[str] = "확정"
CONFIDENCE_SUSPECTED: Final[str] = "의심"

#: "문장"·"표" — 정본 §「같은 사실을 문장·표·그래프·카드 중 두 형식 이상으로
#: 반복하지 않는다」의 "형식"을 그대로 이름 붙인 것.
FORMAT_PROSE: Final[str] = "문장"
FORMAT_TABLE: Final[str] = "표"

#: 값 뒤에 붙어야 "측정된 수치"로 본다. 단위 없는 맨 숫자(문장 번호·순번 등)를
#: 걸러내기 위한 최소 조건이다. 재무·규모·비율계 단위만 넣는다 — 기업분석
#: 보고서가 실제로 쓰는 단위다(원 계열, 비율, 인원·건수·배수·순위).
_VALUE_UNIT_TOKENS: Final[tuple[str, ...]] = (
    "억원",
    "조원",
    "만원",
    "원",
    "%",
    "퍼센트",
    "명",
    "건",
    "개",
    "배",
    "위",
    "점",
)
_VALUE_UNIT_ALTERNATION: Final[str] = "|".join(
    sorted(_VALUE_UNIT_TOKENS, key=len, reverse=True)
)
#: ★ 단위 뒤 lookahead는 라틴/숫자만 막는다. 한국어는 조사가 명사 뒤에 «바로»
#:   붙는다("900명이", "1,200원을") — 한글까지 막으면 실제 문장의 절대다수를
#:   놓친다. "개년/개월/명당"처럼 단위가 다른 복합어의 일부가 되는 경우는
#:   정규식이 아니라 `_has_valid_unit_boundary`가 따로 거른다.
_VALUE_WITH_UNIT_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<![A-Za-z0-9가-힣])([-+]?\d[\d,]*(?:\.\d+)?)\s*"
    rf"({_VALUE_UNIT_ALTERNATION})(?![A-Za-z0-9])"
)

#: 단위 바로 뒤에 이 글자가 오면 "3개년"·"6개월"·"명당"처럼 단위가 다른 복합어의
#: 일부다 — 측정된 값이 아니므로 후보에서 뺀다.
_UNIT_FOLLOWED_BY_BLOCK: Final[frozenset[str]] = frozenset({"년", "월", "일", "당"})


def _has_valid_unit_boundary(text: str, unit_end: int) -> bool:
    if unit_end >= len(text):
        return True
    return text[unit_end] not in _UNIT_FOLLOWED_BY_BLOCK

#: 연도로 보이는 4자리 숫자. 기간 힌트를 찾는 데만 쓰고, 값으로는 세지 않는다
#: (v1 `publish.py`의 1900~2100 배제와 같은 이유 — 연도가 «값»으로 오인되면
#: 안 된다).
_YEAR_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"(?:19|20)\d{2}")
_YEAR_RANGE: Final[tuple[Decimal, Decimal]] = (Decimal("1900"), Decimal("2100"))

#: 연도 뒤 "N분기" 표기까지 기간에 포함할 탐색 폭(글자 수). "2026년 2분기"처럼
#: 붙어 나오는 모양만 잡고, 멀리 있는 분기 숫자는 다른 문장의 것일 수 있어
#: 잡지 않는다.
_QUARTER_LOOKAHEAD_CHARS: Final[int] = 8
_QUARTER_SUFFIX_RE: Final[re.Pattern[str]] = re.compile(r"^\s*년?\s*(\d)\s*분기")

#: 숫자와 기간 힌트(연도) 사이 최대 허용 거리(글자 수). 너무 멀면 다른 절의
#: 연도를 잘못 붙일 위험이 커 "기간 모름"으로 둔다.
_PERIOD_WINDOW_CHARS: Final[int] = 40

#: 지표 힌트를 뽑을 때 숫자 앞에서 살펴보는 글자 수.
_METRIC_HINT_WINDOW_CHARS: Final[int] = 20

#: 지표 힌트 끝의 조사. 뿌리 단어만 남기기 위해 뗀다. 긴 것부터 시도해야
#: "에서"가 "서"보다 먼저 잘리지 않는다.
_TRAILING_PARTICLES: Final[tuple[str, ...]] = (
    "에서",
    "까지",
    "보다",
    "으로",
    "로는",
    "은",
    "는",
    "이",
    "가",
    "을",
    "를",
    "의",
    "도",
    "로",
)

#: 지표 힌트 비교용 글자 2-그램 길이. dedupe.py의 문장 유사도 판단과 같은
#: 발상(형태소 분석 없이 표기 차이를 넘어서기)을 짧은 지표어에 맞춰 쓴다.
_METRIC_NGRAM_SIZE: Final[int] = 2

#: v1 port.py의 합계 행 판정과 같은 라벨. 합계 행은 각 항목의 값과 코드상
#: 다른 지표라 별도 취급할 필요는 없지만, 표 원문 그대로 스캔한다(값이
#: 같으면 여전히 같은 사실이 반복된 것일 수 있어 제외하지 않는다).


@dataclass(frozen=True)
class NumericOccurrence:
    """수치 하나가 나온 자리 한 건 — 중복 판정의 원재료.

    사람이 실측 결과를 눈으로 판단할 수 있도록 정본 4요소 근사값과 원문
    발췌를 함께 담는다.
    """

    section_id: str
    section_label: str
    format: str  # FORMAT_PROSE | FORMAT_TABLE
    value: str
    unit: str
    period: str  # 모르면 ""
    metric_hint: str  # 모르면 ""
    excerpt: str


@dataclass(frozen=True)
class DuplicateFinding:
    """같은 사실로 의심되는 발생 묶음 하나."""

    confidence: str  # CONFIDENCE_CONFIRMED | CONFIDENCE_SUSPECTED
    reason: str
    occurrences: tuple[NumericOccurrence, ...]


def _section_label(section: ReportSection) -> str:
    number = SECTION_DISPLAY_NUMBERS.get(section.cell, "") or section.cell
    title = SECTION_TITLES.get(section.cell, section.title)
    return f"{number}장 {title}" if number else title


def _numeric_value(raw: str) -> tuple[str, Decimal] | None:
    """콤마 뗀 값 문자열과 Decimal을 같이 돌려준다. 연도로 보이면 버린다."""
    cleaned = raw.replace(",", "").strip()
    try:
        parsed = Decimal(cleaned)
    except InvalidOperation:
        return None
    if _YEAR_RANGE[0] <= abs(parsed) <= _YEAR_RANGE[1]:
        return None
    return cleaned, parsed


def _nearest_period(text: str, position: int) -> str:
    """숫자 위치에서 가장 가까운 연도 토큰을 찾아 기간 힌트로 돌려준다."""
    candidates = list(_YEAR_TOKEN_RE.finditer(text))
    if not candidates:
        return ""
    nearest = min(
        candidates,
        key=lambda m: min(abs(m.start() - position), abs(m.end() - position)),
    )
    distance = min(abs(nearest.start() - position), abs(nearest.end() - position))
    if distance > _PERIOD_WINDOW_CHARS:
        return ""
    year = nearest.group(0)
    tail = text[nearest.end() : nearest.end() + _QUARTER_LOOKAHEAD_CHARS]
    quarter = _QUARTER_SUFFIX_RE.match(tail)
    return f"{year}-{quarter.group(1)}Q" if quarter else year


def _metric_hint(text: str, position: int) -> str:
    """숫자 앞 단어를 지표 힌트로 근사한다(형태소 분석 없는 근사치)."""
    window = text[max(0, position - _METRIC_HINT_WINDOW_CHARS) : position]
    match = re.search(r"([가-힣]{2,10})\s*$", window)
    if not match:
        return ""
    word = match.group(1)
    for particle in _TRAILING_PARTICLES:
        if word.endswith(particle) and len(word) > len(particle):
            return word[: -len(particle)]
    return word


def _metric_signature(hint: str) -> frozenset[str]:
    normalized = unicodedata.normalize("NFKC", hint).casefold()
    if len(normalized) < _METRIC_NGRAM_SIZE:
        return frozenset({normalized}) if normalized else frozenset()
    return frozenset(
        normalized[i : i + _METRIC_NGRAM_SIZE]
        for i in range(len(normalized) - _METRIC_NGRAM_SIZE + 1)
    )


def _metric_hints_compatible(hints: tuple[str, ...]) -> bool:
    """지표 힌트들이 "다른 지표"라고 확신할 수 있으면 False.

    ★ 애매하면 True(호환으로 봄) — 오탐을 두려워하되 감추지 않기 위해, 판정이
      불확실할 때는 "값+기간이 같다"는 더 강한 신호 쪽을 믿는다. 반대로 지표
      힌트가 둘 다 있고 글자가 하나도 안 겹치면 다른 사실일 가능성이 커
      확정에서 의심으로 낮춘다.
    """
    named = [hint for hint in hints if hint]
    if len(named) < 2:
        return True
    signatures = [_metric_signature(hint) for hint in named]
    first = signatures[0]
    return all(bool(first & other) for other in signatures[1:])


def _prose_occurrences(section: ReportSection) -> list[NumericOccurrence]:
    label = _section_label(section)
    out: list[NumericOccurrence] = []
    for text, _cite in section.prose_lines:
        for match in _VALUE_WITH_UNIT_RE.finditer(text):
            if not _has_valid_unit_boundary(text, match.end(2)):
                continue
            parsed = _numeric_value(match.group(1))
            if parsed is None:
                continue
            value, _decimal_value = parsed
            start = match.start(1)
            out.append(
                NumericOccurrence(
                    section_id=section.cell,
                    section_label=label,
                    format=FORMAT_PROSE,
                    value=value,
                    unit=match.group(2),
                    period=_nearest_period(text, start),
                    metric_hint=_metric_hint(text, start),
                    excerpt=text.strip(),
                )
            )
    return out


#: 표 열 머리글이 연도로 보이는지 판정. "2025"·"2025년" 둘 다 인정한다.
_TABLE_YEAR_HEADER_RE: Final[re.Pattern[str]] = re.compile(r"^(?:19|20)\d{2}(?:년)?$")


def _table_period_from_caption(caption: str) -> str:
    match = _YEAR_TOKEN_RE.search(caption or "")
    return match.group(0) if match else ""


def _table_occurrences(section: ReportSection, table: ReportTable) -> list[NumericOccurrence]:
    label = _section_label(section)
    caption_period = _table_period_from_caption(table.caption)
    unit_hint = str(table.display_unit or "").strip()
    out: list[NumericOccurrence] = []
    headers = list(table.headers or [])
    for row in table.rows or []:
        if not row:
            continue
        row_label = str(row[0]).strip()
        for column_index in range(1, min(len(headers), len(row))):
            header = str(headers[column_index]).strip()
            cell = str(row[column_index]).strip()
            parsed = _numeric_value(cell)
            if parsed is None:
                continue
            value, _decimal_value = parsed
            if _TABLE_YEAR_HEADER_RE.match(header):
                period = header.rstrip("년")
            else:
                period = caption_period
            unit = unit_hint or ("%" if "%" in header or "비중" in header else "")
            out.append(
                NumericOccurrence(
                    section_id=section.cell,
                    section_label=label,
                    format=FORMAT_TABLE,
                    value=value,
                    unit=unit,
                    period=period,
                    metric_hint=row_label,
                    excerpt=f"{table.caption} · {row_label} {header}={cell}".strip(" ·"),
                )
            )
    return out


def _collect_occurrences(report: Report) -> list[NumericOccurrence]:
    """본문 수치 발생 전부를 모은다.

    ★ `report.summary_items`는 스캔하지 않는다 — 정본이 "핵심 요약은 검증된
      본문 문장을 글자 변경 없이 재사용한다"고 명시적으로 허용한 반복이라
      중복이 아니다(REPORT_STRUCTURE.md「사실 단일 소유 원칙」 4번째 항목).
    """
    out: list[NumericOccurrence] = []
    for section in report.sections:
        out.extend(_prose_occurrences(section))
        for table in section.tables or []:
            out.extend(_table_occurrences(section, table))
    return out


def _reason(sections: set[str], formats: set[str]) -> str:
    parts: list[str] = []
    if len(sections) >= 2:
        parts.append("서로 다른 장에 같은 수치가 반복됨")
    if len(formats) >= 2:
        parts.append("문장·표 등 서로 다른 형식으로 반복됨")
    return " / ".join(parts) if parts else "같은 값이 반복됨"


def find_numeric_duplicates(report: Report) -> tuple[DuplicateFinding, ...]:
    """정본 §「사실 단일 소유 원칙」 위반 «후보»를 값+단위(+기간) 기준으로 찾는다.

    ⛔ 예외를 던지지 않는다. 출고를 막지 않는다. 호출부(`validate_v2` 등)에
    배선돼 있지 않다 — 사람이 실측으로 오탐률을 본 뒤 막을지 정하기 위한
    «검출 전용» 함수다.

    판정 규칙:
      ① 값+단위+기간이 모두 같고, 지표 힌트가 서로 다르다고 확신할 수 없으면
         → 확정(같은 사실 재서술일 가능성이 높다).
      ② 값+단위는 같지만 기간이 한쪽 이상 비었거나 다르면, 또는 지표 힌트가
         뚜렷이 달라 보이면 → 의심(판단은 사람 몫으로 남긴다).
      둘 다 "서로 다른 장" 또는 "서로 다른 형식(문장·표)"에 걸쳐 있을 때만
      묶는다 — 같은 문장·같은 표 셀 안의 반복은 이 함수의 대상이 아니다
      (`composer/dedupe.py`가 생성 단계에서 이미 다룬다).
    """
    occurrences = _collect_occurrences(report)
    if len(occurrences) < 2:
        return ()

    by_value_unit: dict[tuple[str, str], list[NumericOccurrence]] = {}
    for occ in occurrences:
        by_value_unit.setdefault((occ.value, occ.unit), []).append(occ)

    findings: list[DuplicateFinding] = []
    for group in by_value_unit.values():
        if len(group) < 2:
            continue
        sections = {occ.section_id for occ in group}
        formats = {occ.format for occ in group}
        if len(sections) < 2 and len(formats) < 2:
            continue  # 같은 장·같은 형식 안의 반복은 이 검출기의 대상이 아니다

        periods = {occ.period for occ in group}
        all_periods_known_and_equal = len(periods) == 1 and "" not in periods
        hints = tuple(occ.metric_hint for occ in group)
        confidence = (
            CONFIDENCE_CONFIRMED
            if all_periods_known_and_equal and _metric_hints_compatible(hints)
            else CONFIDENCE_SUSPECTED
        )
        findings.append(
            DuplicateFinding(
                confidence=confidence,
                reason=_reason(sections, formats),
                occurrences=tuple(group),
            )
        )
    return tuple(findings)
