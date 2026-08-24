"""composer 데이터 계약 (엔진 v2 실행계획의 고정 계약).

★ 이 파일의 세 타입(ComposedSentence·ComposedSection·ComposedReport)은
  단계3 모든 소단계가 공유하는 «고정 계약»이다. 필드 변경·삭제 금지,
  꼭 필요하면 필드 «추가»만 허용한다.
★ composer는 pipeline·report_standard를 import 하지 않는다.
  파이프라인 쪽 실측 구조(조각 dict, ReportTable)는 아래 얇은 어댑터로 받는다.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Optional

from src.features.composer.constants import RCEPT_DT_LENGTH


class AskFatalError(Exception):
    """AI 호출이 «문장 내용 문제»가 아니라 «요청 전역 장애»로 죽었을 때만 쓴다.

    ★ 예산 소진(ProviderBudgetExceeded)·billing-uncertain 차단(요청 전체가
      더 못 부르는 상태)은 문장 하나의 실패가 아니라 이 요청 전체의 장애다.
      composer의 다른 모든 예외는 문장 단위로 삼켜 안내문·강등으로 바꾸지만,
      이 타입만은 어디서 잡히든 재전파해 pipeline.run_v2까지 뚫고 나가야
      한다 — 그래야 real.py가 v1과 같은 FAILED로 정직하게 끝낼 수 있다
      (전역 장애를 «검증 실패»로 오표기하지 않기 위함).
    """

    def __init__(self, cause: BaseException) -> None:
        self.cause = cause
        super().__init__(str(cause))


@dataclass(frozen=True)
class ComposedSentence:
    """작가 AI가 쓴 문장 하나."""

    #: 문장 본문 (한국어 산문)
    text: str
    #: 근거로 인용한 수집 조각 id들. 순수 «해석» 문장이면 빈 튜플 허용.
    citations: tuple[str, ...]
    #: "확인"(인용 원문에 직접 근거가 있는 사실) | "해석"(공식 자료 기반 분석·의미 부여)
    grade: str


@dataclass(frozen=True)
class ComposedSection:
    """장 하나. 장 삭제 금지 — 자료가 부족해도 안내문으로 남긴다."""

    #: 기존 v3 정본 장 id 재사용 (identity … competitive_position)
    section_id: str
    #: 이 장의 문장들. 생성 실패 시 빈 튜플.
    sentences: tuple[ComposedSentence, ...]
    #: 자료 부족·생성 실패의 정직한 안내문. 문제없으면 "".
    notice: str = ""


@dataclass(frozen=True)
class ComposedReport:
    """v2 보고서 전체. summary는 소단계 3-3이 채운다 (그전까지 빈 튜플)."""

    sections: tuple[ComposedSection, ...]
    summary: tuple[ComposedSentence, ...] = ()


# ══════════════════════════════════════════════════════════
# 입력 어댑터 — 파이프라인 실측 구조를 얇게 감싼다
# ══════════════════════════════════════════════════════════


@dataclass(frozen=True)
class CollectedFragment:
    """수집 조각 하나 — real.py의 `frags: dict[int, dict[str, str]]`를 감싼 것.

    실측 필드 대응: fragment_id ← dict 키(int), kind ← "종류", text ← "원문",
    source_url ← "출처"(홈페이지·공식 IR만), document_title ← "문서명"(공식 IR),
    location ← "원문위치"(공식 IR). 없는 필드는 빈 문자열.
    """

    fragment_id: str
    kind: str
    text: str
    source_url: str = ""
    document_title: str = ""
    location: str = ""


def fragments_from_raw(
    raw: Mapping[int, Mapping[str, Any]]
) -> tuple[CollectedFragment, ...]:
    """파이프라인 조각 dict를 CollectedFragment 튜플로 바꾼다.

    ★ 원문이 빈 조각은 뺀다 — 인용해도 대조할 원문이 없어 근거가 못 되기 때문이다.
      (내용을 보고 거르는 게 아니라 «비어 있는가»만 본다.)
    """
    out: list[CollectedFragment] = []
    for number in sorted(raw):
        item = raw[number]
        text = str(item.get("원문") or "").strip()
        if not text:
            continue
        out.append(
            CollectedFragment(
                fragment_id=str(number),
                kind=str(item.get("종류") or "").strip(),
                text=text,
                source_url=str(item.get("출처") or "").strip(),
                document_title=str(item.get("문서명") or "").strip(),
                location=str(item.get("원문위치") or "").strip(),
            )
        )
    return tuple(out)


@dataclass(frozen=True)
class PerformanceTable:
    """프로그램이 만든 3개년 실적표 — 작가 AI에게 근거로 주는 표.

    파이프라인 `ReportTable`(canonical_report.py의 table_facts 원천)을
    composer가 직접 import 하지 않으려고 얇게 복사한 모양이다.
    """

    caption: str
    headers: tuple[str, ...]
    rows: tuple[tuple[str, ...], ...]
    unit: str = ""
    cite: str = ""


def performance_table_from_report_table(table: Any) -> PerformanceTable:
    """파이프라인 ReportTable을 덕 타이핑으로 감싼다 (직접 import 회피)."""
    return PerformanceTable(
        caption=str(getattr(table, "caption", "") or ""),
        headers=tuple(str(h) for h in (getattr(table, "headers", None) or ())),
        rows=tuple(
            tuple(str(cell) for cell in row)
            for row in (getattr(table, "rows", None) or ())
        ),
        unit=str(getattr(table, "display_unit", "") or ""),
        cite=str(getattr(table, "cite", "") or ""),
    )


# ══════════════════════════════════════════════════════════
# 공시 신원 — 부록 출처에 «원문 주소»를 싣기 위한 어댑터
# ══════════════════════════════════════════════════════════


@dataclass(frozen=True)
class FilingMeta:
    """이번 조사가 실제로 내려받은 공시 1건의 신원.

    ★ 왜 이 타입이 필요한가 (실측 결함) — 전자공시 절 조각(사업내용·MD&A 등)에는
      조각 자체에 주소가 없다. 주소를 가진 것은 조각이 아니라 «그 조각을 떠 온
      문서»다. 그런데 v2는 그 문서 신원을 render까지 넘기지 않아, 현대자동차
      실측에서 부록 출처 12건 중 11건이 「주소 없음」으로 나갔다.
      독자가 원문을 열 수 없으면 근거 표기는 장식일 뿐이다.
    ★ v1은 같은 정보를 provenance/citations.py에서 이미 쓰고 있다. v1 경로는
      건드리지 않고(계획 01장 「v1 무변」), v2가 같은 재료를 받아 쓰게만 한다.
    """

    #: 공시 접수번호 (`rcept_no`). 이것이 있어야 원문 주소를 만들 수 있다.
    document_id: str = ""
    #: 보고서 이름 (`report_nm`). 예: "반기보고서 (2026.06)".
    title: str = ""
    #: 공시일 `YYYY-MM-DD`. 원래 모양이 아니면 비운다 (지어내지 않는다).
    disclosed_at: str = ""


def _format_disclosed_at(raw: str) -> str:
    """DART 공시일(`YYYYMMDD`) → `"YYYY-MM-DD"`. 모양이 안 맞으면 빈 문자열.

    ★ 날짜를 지어내지 않는다 — 틀린 공시일은 없는 공시일보다 나쁘다.
      v1 `provenance/citations.py._format_rcept_dt`와 같은 규칙이다.
    """
    digits = raw.strip()
    if len(digits) != RCEPT_DT_LENGTH or not digits.isdigit():
        return ""
    return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"


def filing_meta_from_raw(filing: Any) -> Optional[FilingMeta]:
    """real.py의 공시 dict(`rcept_no`·`report_nm`·`rcept_dt`)를 FilingMeta로.

    접수번호가 없으면 주소를 만들 수 없으므로 ``None``을 돌려준다 — 이때는
    부록이 예전처럼 주소 없이 나가며, 그 사실이 화면에 그대로 보인다.
    """
    if not isinstance(filing, Mapping):
        return None
    document_id = str(filing.get("rcept_no") or filing.get("rceptNo") or "").strip()
    if not document_id:
        return None
    return FilingMeta(
        document_id=document_id,
        title=str(filing.get("report_nm") or "").strip(),
        disclosed_at=_format_disclosed_at(str(filing.get("rcept_dt") or "")),
    )


def composition_table_from_raw(tables: Any) -> Optional[PerformanceTable]:
    """`revenuemix.build()`가 돌려준 표 목록의 «첫 표»를 구성표로 바꾼다.

    ★ 왜 필요한가 (실측 결함) — v1은 이 표를 만들어 2장에 붙이는데
      (`pipeline/real.py`의 tables_by_section["business_model"]),
      v2 호출부가 넘기지 않아 «표도 도식도» 통째로 빠져 있었다.
      9개 장 중 4장 하나만 표를 받는 상태였다.
    ★ 첫 표만 쓴다 — revenuemix는 제품별·지역별 두 표를 낼 수 있는데
      2장 한 자리에 둘 다 넣으면 같은 매출을 두 번 보여 주게 된다
      (정본 §5 「같은 수치를 문장과 표에 각각 씀」 중복 판정).
    """
    if not tables:
        return None
    first = tables[0] if isinstance(tables, (list, tuple)) else tables
    if not isinstance(first, Mapping):
        return None
    rows = tuple(
        tuple(str(cell) for cell in row) for row in (first.get("rows") or ())
    )
    if not rows:
        return None
    return PerformanceTable(
        caption=str(first.get("caption") or ""),
        headers=tuple(str(head) for head in (first.get("headers") or ())),
        rows=rows,
        unit=str(first.get("display_unit") or ""),
        cite=str(first.get("cite") or ""),
    )
