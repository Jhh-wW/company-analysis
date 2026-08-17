"""보고서에 실리는 출처 목록 — 문장 뒤 [번호], 맨 아래에 목록.

★ 왜 필요한가
  지금은 화면이 실행 기록(runs.jsonl)에서 출처를 «역산»한다. 기록 형식이
  바뀌면 조용히 틀어진다. 이 파일은 보고서 자체에 실을 수 있는 출처
  자료구조와, 그것을 마크다운으로 쓰고(직렬화) 다시 읽는(파싱) 함수를 담는다.

  **쓰기(render_sources)와 읽기(parse_sources)가 왕복해도 같아야 한다**
  — 시험(`tests/test_sources.py`)으로 증명한다.

정본: 확정/07_출력/2_규칙/01_배치와근거표기.md
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from src.features.provenance.constants import OTHER_DATE_PREFIX, SOURCES_HEADER


class SourceKind(str, Enum):
    """출처의 종류. 렌더링 형식이 종류마다 다르다."""

    #: 감사보고서·사업보고서 등 전자공시 원본 자료
    FILING = "공시"
    #: 언론 보도
    NEWS = "뉴스"
    #: 그 밖의 자료 (날짜 메타데이터 없이 이름만 있는 경우)
    OTHER = "기타"


@dataclass(frozen=True)
class Source:
    """출처 목록 한 줄 — 문장 뒤 `[번호]`가 가리키는 실제 출처.

    ★ `number`는 **AI가 고른 조각 번호를 그대로 쓴다.** 여기서 새로 매기지
      않는다 (정본 §근거 표기 — 번호).
    """

    number: int
    kind: SourceKind
    label: str
    #: 공시일 (예: "2024-03-15"). 공시 자료일 때만 쓴다.
    disclosed_at: str = ""
    #: 우리가 수집한 날짜 (예: "2026-08-13"). 공시 자료일 때만 쓴다.
    collected_at: str = ""
    #: 보도일. 뉴스일 때 반드시 있어야 한다.
    published_at: str = ""
    #: 언론사 도메인 (예: "mk.co.kr"). 뉴스일 때 반드시 있어야 한다.
    domain: str = ""

    @property
    def is_valid(self) -> bool:
        """뉴스는 보도일과 언론사 도메인이 반드시 있어야 한다 (정본 §근거 표기 — 뉴스)."""
        if self.number <= 0 or not self.label.strip():
            return False
        if self.kind is SourceKind.NEWS:
            return bool(self.published_at.strip()) and bool(self.domain.strip())
        return True


# ══════════════════════════════════════════════════════════
# 쓰기 — 구조 → 마크다운
# ══════════════════════════════════════════════════════════


def _filing_meta_line(source: Source) -> str:
    """공시 자료의 두 번째 줄 — 「공시일 공시 · 수집 수집일」.

    둘 중 하나만 있어도 그것만 적는다. 있는 것까지 지우면 사실이 사라진다.
    """
    if source.disclosed_at and source.collected_at:
        return f"{source.disclosed_at} 공시 · 수집 {source.collected_at}"
    if source.disclosed_at:
        return f"{source.disclosed_at} 공시"
    if source.collected_at:
        return f"수집 {source.collected_at}"
    return ""


def render_sources(sources: list[Source]) -> str:
    """출처 목록을 화면·워드·노션이 그대로 쓸 마크다운 블록으로 만든다.

    세 형태(화면·워드·노션)가 같은 문자열을 쓰게 하려는 것이다 (P3) — 형태마다
    따로 그리면 한쪽만 고쳤을 때 내용이 갈린다.

    Args:
        sources: 보고서 하나에 실릴 출처 목록.

    Returns:
        `[출처]` 머리말부터 시작하는 마크다운 블록.
    """
    lines = [SOURCES_HEADER]
    for source in sources:
        if source.kind is SourceKind.OTHER and source.collected_at:
            # ★ 홈페이지 같은 「기타」 자료는 «확인»으로 적는다.
            #   공시의 「수집 …」과 글자가 같으면 다시 읽을 때 공시로 잘못 분류된다.
            #   그리고 홈페이지는 언제든 바뀌므로 «언제 본 것인지»가 특히 중요하다.
            lines.append(f" [{source.number}] {source.label}")
            lines.append(f"     {OTHER_DATE_PREFIX}{source.collected_at}")
            continue
        if source.kind is SourceKind.NEWS:
            date = f" {source.published_at}" if source.published_at else ""
            domain = f"  ({source.domain})" if source.domain else ""
            lines.append(f" [{source.number}] {source.label}{date}{domain}")
            continue
        lines.append(f" [{source.number}] {source.label}")
        meta = _filing_meta_line(source)
        if meta:
            lines.append(f"     {meta}")
    return "\n".join(lines)


# ══════════════════════════════════════════════════════════
# 읽기 — 마크다운 → 구조
# ══════════════════════════════════════════════════════════

#: `[2] 감사보고서 제16장 수익인식 주석` 같은 항목 첫 줄.
_ENTRY = re.compile(r"^\s*\[(?P<num>\d+)\]\s*(?P<rest>.+?)\s*$")
#: 뉴스 줄 끝의 `2025-03-12  (mk.co.kr)` 꼴.
_NEWS_SUFFIX = re.compile(
    r"^(?P<label>.+?)\s+(?P<date>\d{4}-\d{2}-\d{2})\s*\((?P<domain>[^)]+)\)\s*$"
)
#: 공시 자료의 두 번째 줄 — 셋 중 하나로 갈린다 (둘 다 / 공시일만 / 수집일만).
_FILING_BOTH = re.compile(
    r"^\s*(?P<disclosed>\d{4}-\d{2}-\d{2})\s*공시\s*·\s*수집\s*"
    r"(?P<collected>\d{4}-\d{2}-\d{2})\s*$"
)
_FILING_DISCLOSED_ONLY = re.compile(r"^\s*(?P<disclosed>\d{4}-\d{2}-\d{2})\s*공시\s*$")
_FILING_COLLECTED_ONLY = re.compile(r"^\s*수집\s*(?P<collected>\d{4}-\d{2}-\d{2})\s*$")
#: 기타(홈페이지 등)의 두 번째 줄 — 「확인 날짜」. 공시의 「수집 날짜」와 구분한다.
_OTHER_CONFIRMED = re.compile(
    r"^\s*" + OTHER_DATE_PREFIX.strip() + r"\s*(?P<collected>\d{4}-\d{2}-\d{2})\s*$"
)


def parse_sources(text: str) -> list[Source]:
    """마크다운 `[출처]` 블록을 다시 구조로 읽는다.

    ★ `render_sources()`가 쓴 형식만 읽는다 — 사람이 손으로 다르게 쓴 문서를
      복원하는 범용 파서가 아니다. 목적은 «왕복 보장»이다.

    Args:
        text: `render_sources()`가 만들었거나 그와 같은 모양인 마크다운.

    Returns:
        출처 목록. 항목을 하나도 못 찾으면 빈 목록.
    """
    lines = text.splitlines()
    sources: list[Source] = []
    idx = 0
    total = len(lines)

    while idx < total:
        entry = _ENTRY.match(lines[idx])
        if entry is None:
            idx += 1
            continue

        number = int(entry.group("num"))
        rest = entry.group("rest").strip()

        news = _NEWS_SUFFIX.match(rest)
        if news is not None:
            sources.append(
                Source(
                    number=number,
                    kind=SourceKind.NEWS,
                    label=news.group("label").strip(),
                    published_at=news.group("date"),
                    domain=news.group("domain").strip(),
                )
            )
            idx += 1
            continue

        disclosed_at = collected_at = ""
        kind = SourceKind.OTHER
        if idx + 1 < total:
            nxt = lines[idx + 1]
            both = _FILING_BOTH.match(nxt)
            disclosed_only = _FILING_DISCLOSED_ONLY.match(nxt)
            collected_only = _FILING_COLLECTED_ONLY.match(nxt)
            if both is not None:
                disclosed_at = both.group("disclosed")
                collected_at = both.group("collected")
                kind = SourceKind.FILING
                idx += 1
            elif disclosed_only is not None:
                disclosed_at = disclosed_only.group("disclosed")
                kind = SourceKind.FILING
                idx += 1
            elif collected_only is not None:
                collected_at = collected_only.group("collected")
                kind = SourceKind.FILING
                idx += 1
            else:
                confirmed = _OTHER_CONFIRMED.match(nxt)
                if confirmed is not None:
                    # 「확인 날짜」 = 기타(홈페이지 등). 종류를 공시로 바꾸지 않는다.
                    collected_at = confirmed.group("collected")
                    idx += 1

        sources.append(
            Source(
                number=number,
                kind=kind,
                label=rest,
                disclosed_at=disclosed_at,
                collected_at=collected_at,
            )
        )
        idx += 1

    return sources


def count_missing_dates(sources: list[Source]) -> int:
    """출처일·수집일이 하나라도 빠진 공시 자료 개수 (C3).

    맨 아래 목록에 날짜가 한곳에 모이는 «딸려 오는 효과»를 여기서 쓴다
    (정본 §근거 표기 — 딸려 오는 효과).

    ⚠️ 알려진 한계 — 마크다운으로 한 번 왕복(render → parse)하면, 날짜가
      하나도 없던 공시 항목은 겉모양이 '기타'와 똑같아져 더 이상 공시로
      구분되지 않는다 (표시 형식 자체에 「원래 공시였다」는 표식이 없다).
      그래서 이 함수는 **렌더링하기 전, 원본 in-memory 목록**에 대고 불러야
      정확하다.
    """
    return sum(
        1
        for s in sources
        if s.kind is SourceKind.FILING and not (s.disclosed_at and s.collected_at)
    )
