"""표지에 크게 싣는 «최신 사업연도 실적» 값을 보고서에서 골라낸다.

★ 이 모듈은 «계산하지 않는다». 4장 실적표
  (``company_performance.logic.build_three_year_table``가 전자공시 API 원수치로
  fail-closed 하게 만든 표)의 최신 사업연도 행 셀을 «글자 그대로» 재사용한다.
  증감률·성장률·비율처럼 표에 인쇄되지 않은 값을 표지에서 새로 만들면, v2
  보고서에는 그것을 재검산할 사실 장부(fact_records)가 없어 검증 불가 주장이
  된다. 정본: ``docs/출력물 기준/90_공통_규칙/디자인과_PDF_QA.md`` 6-1절.

★ 값을 못 고르면 «빈 결과»를 돌려준다. 화면과 PDF는 빈 결과일 때 띠를 통째로
  그리지 않는다 — 빈 제목이나 ``—``로 채운 칸을 남기지 않는 것이 이 보고서의
  계약이다(실적표가 없는 회사가 실제로 있다).

★ 웹 틀과 PDF가 «같은 이 함수»를 쓴다. 두 곳에서 따로 고르면 화면과 인쇄물의
  숫자가 조용히 갈라진다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Final, Optional


#: 실적표를 알아보는 첫 열 이름. ``build_three_year_table``의 ``headers[0]``이다.
PERIOD_HEADER: Final[str] = "사업연도"

#: 표지 띠에 싣는 지표 — 닫힌 목록이다.
#:
#: ★ ``build_three_year_table``이 «반드시» 만들어 내는 두 필수 지표의 열 이름을
#:   그대로 쓴다(``company_performance/logic.py``의 ``_ACCOUNT_IDS`` ·
#:   ``_REQUIRED_METRICS``). 선택 지표(당기순이익)까지 실으면 회사마다 띠의
#:   칸 수가 달라져 표지 모양이 흔들린다.
#: ★ 라벨을 새로 짓지 않고 표의 «한국어 열 이름»을 옮기는 이유 — ``KPI`` 같은
#:   영문 약어는 출고 게이트(``publish.py``의 ``_FORBIDDEN_JOB_TOPIC``)가
#:   지원자 직무 소재로 보고 차단한다.
COVER_METRIC_LABELS: Final[tuple[str, ...]] = ("매출액", "영업이익")

#: 띠에 올릴 «후보» 지표 — 앞에서부터 표에 있는 것으로 COVER_METRIC_COUNT 개를 고른다.
#:
#: ★ 2026-08-29 — 은행 손익계산서에는 「매출액」에 해당하는 계정이 «아예 없다»
#:   (실측: 우리은행). 예전에는 매출액·영업이익이 «둘 다» 있어야 띠를 그렸기 때문에
#:   그런 회사는 표지 실적 박스가 통째로 사라졌다.
#:   후보를 하나 늘려 「매출액·영업이익」이 안 되면 「영업이익·당기순이익」으로 그린다.
#: ★ 보통 회사(세 지표가 다 있는 경우)는 앞 둘이 그대로 뽑혀 예전과 «똑같다».
#: ★ 목록은 여전히 «닫혀» 있다 — 표에 있는 아무 열이나 크게 띄우지 않는다.
COVER_METRIC_CANDIDATES: Final[tuple[str, ...]] = (
    "매출액",
    "영업이익",
    "당기순이익",
)

#: 띠에 올리는 칸 수. 회사마다 칸 수가 달라지면 표지 모양이 흔들린다.
COVER_METRIC_COUNT: Final[int] = len(COVER_METRIC_LABELS)


def cover_metric_labels(headers: list[str]) -> list[str]:
    """표의 열 이름에서 띠에 올릴 지표를 «후보 순서대로» 고른다."""

    present = [label for label in COVER_METRIC_CANDIDATES if label in headers]
    return present[:COVER_METRIC_COUNT]

#: 띠 제목의 꼬리말. 표 캡션의 「주요 실적」과 같은 뜻이며 새 주장이 아니다.
COVER_TITLE_SUFFIX: Final[str] = "실적"

#: 표지 띠가 쓰는 행 번호 — 실적표는 최신→과거 순서라 0번이 최신 사업연도다.
#: (``company_performance.logic._periods``가 그 순서를 강제한다.)
LATEST_ROW_INDEX: Final[int] = 0

#: 공개 표시값의 모양. 천 단위 콤마와 소수 자릿수(표의 ``scale_places``)를
#: 허용한다. 옛 저장본이나 손상된 payload가 표지에 이상한 글자를 크게 띄우지
#: 못하게 막는 «모양 검사»다 — 값을 바꾸거나 다시 계산하지 않는다.
_DISPLAY_NUMBER: Final[re.Pattern[str]] = re.compile(
    r"^[-+]?(?:0|[1-9]\d*|[1-9]\d{0,2}(?:,\d{3})+)(?:\.\d+)?$"
)


@dataclass(frozen=True)
class CoverMetric:
    """표지 띠의 칸 하나 — 표 셀 글자를 그대로 옮긴 것."""

    #: 표의 열 이름 그대로 (예: ``매출액``)
    label: str
    #: 표 셀 글자 그대로 (예: ``8,219``)
    value: str
    #: 표의 공개 단위 그대로 (예: ``억원``)
    unit: str


@dataclass(frozen=True)
class CoverMetrics:
    """표지 띠 하나. 값을 못 고르면 ``items``가 비고 거짓으로 평가된다."""

    #: 띠 제목 (예: ``2025 사업연도 실적``)
    title: str = ""
    items: tuple[CoverMetric, ...] = ()
    #: 4장 실적표와 «같은» 출처 표기. 표지에 새 출처를 만들지 않는다.
    cite: str = ""

    def __bool__(self) -> bool:
        """칸이 하나도 없으면 띠를 그리지 않는다 (빈 자리 금지)."""

        return bool(self.items)


#: 값을 못 고른 경우의 유일한 반환값. ``None`` 대신 총함수로 두어 화면·PDF가
#: 같은 모양으로 「없음」을 다룬다.
EMPTY_COVER_METRICS: Final[CoverMetrics] = CoverMetrics()


def _cells(row: Any) -> list[str]:
    return [str(cell).strip() for cell in (row or ())]


def _is_performance_table(table: Any) -> bool:
    """4장 실적표인지 «표 자신의 모양»으로만 판정한다.

    장 ID로 찾지 않는 이유 — v1(canonical)과 v2(composer)가 표를 넣는 경로가
    서로 다르고, 장 ID가 바뀌면 띠가 «조용히» 사라진다. 첫 열이 ``사업연도``이고
    필수 두 지표 열을 모두 가진 숫자표에 공개 단위까지 있는 표는 이 제품에서
    ``build_three_year_table``의 결과뿐이다.
    """

    headers = _cells(getattr(table, "headers", None))
    rows = [_cells(row) for row in (getattr(table, "rows", None) or ())]
    if not headers or not rows:
        return False
    if headers[0] != PERIOD_HEADER:
        return False
    if len(cover_metric_labels(headers)) < COVER_METRIC_COUNT:
        return False
    if not bool(getattr(table, "numeric", False)):
        return False
    if not str(getattr(table, "display_unit", "") or "").strip():
        return False
    return all(len(row) == len(headers) for row in rows)


def _performance_table(report: Any) -> Optional[Any]:
    """보고서 안에서 완료 사업연도 실적표를 찾는다. 없으면 ``None``."""

    for section in getattr(report, "sections", None) or ():
        for table in getattr(section, "tables", None) or ():
            if _is_performance_table(table):
                return table
    return None


def cover_metrics(report: Any) -> CoverMetrics:
    """표지 띠에 올릴 값을 고른다 — 고를 수 없으면 빈 결과.

    Args:
        report: 완성된 보고서(``pipeline.port.Report``). 옛 저장본도 안전하게
            읽으려고 속성 접근만 쓴다.

    Returns:
        최신 사업연도 행에서 «글자 그대로» 옮긴 ``CoverMetrics``. 실적표가
        없거나 칸 하나라도 모양이 어긋나면 ``EMPTY_COVER_METRICS``.
    """

    table = _performance_table(report)
    if table is None:
        return EMPTY_COVER_METRICS

    headers = _cells(getattr(table, "headers", None))
    rows = [_cells(row) for row in (getattr(table, "rows", None) or ())]
    if len(rows) <= LATEST_ROW_INDEX:
        return EMPTY_COVER_METRICS
    latest = rows[LATEST_ROW_INDEX]

    period = latest[0]
    if not period:
        return EMPTY_COVER_METRICS

    unit = str(getattr(table, "display_unit", "") or "").strip()
    items: list[CoverMetric] = []
    for label in cover_metric_labels(headers):
        value = latest[headers.index(label)]
        # 한 칸이라도 비거나 모양이 어긋나면 띠 «전체»를 그리지 않는다.
        # 반쪽짜리 띠는 없는 것보다 나쁘다.
        if not _DISPLAY_NUMBER.fullmatch(value):
            return EMPTY_COVER_METRICS
        items.append(CoverMetric(label=label, value=value, unit=unit))

    return CoverMetrics(
        title=f"{period} {PERIOD_HEADER} {COVER_TITLE_SUFFIX}",
        items=tuple(items),
        cite=str(getattr(table, "cite", "") or "").strip(),
    )
