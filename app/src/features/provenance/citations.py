"""수집 조각 → 출처 목록 재료 — 「무엇을 근거로 이 문장을 썼나」의 마지막 연결.

★ 왜 필요한가
  `sources.py`는 이미 완성된 출처(Source) 목록을 마크다운으로 쓰고(render) 다시
  읽는(parse) 함수를 갖고 있다. 그런데 그 Source를 **어디서 만드나**가 없었다 —
  수집 단계가 넘겨주는 조각은 `{"종류": ..., "원문": ...}`뿐이라 날짜·언론사
  같은 것이 안 보인다고 여겨졌기 때문이다 (문제로그 P-24 · P-44).

  실제로는 재료가 이미 조각 «안에» 있다 (1판 엔진 `run_pilot.py` 실측):
    - 뉴스 조각 원문 맨 앞: `"(2025-03-12 보도 · mk.co.kr) 제목. 설명"`
      (`run_pilot.collect_news`가 붙인다)
    - 공시 계열 조각: 따로 넘어오는 `filing`(`latest_report_rcept()` 결과) 안에
      `report_nm`(보고서 이름) · `rcept_dt`(공시일, `YYYYMMDD`)가 있다
    - 홈페이지 조각: 수집기(`homepage.logic.collect_homepage_fragments`)가
      조각마다 `"출처"`(실제 읽은 URL)를 함께 준다

  이 파일은 그 재료를 **있는 그대로만** 뽑아 `Source`로 바꾼다. 날짜·이름을
  못 뽑으면 지어내지 않고 비워 둔다 — `Source.is_valid`가 걸러낸다.

정본: 확정/07_출력/2_규칙/01_배치와근거표기.md (결정 D14-1)
"""

from __future__ import annotations

import datetime as dt
import re
from typing import Any, Optional

from src.features.provenance.constants import (
    DART_ACCOUNT_FRAGMENT_LABEL,
    DART_ACCOUNT_FRAGMENT_PREFIX,
    FRAGMENT_KIND_HOMEPAGE,
    FRAGMENT_KIND_NEWS,
    HOMEPAGE_FALLBACK_LABEL,
    NEWS_UNKNOWN_DOMAIN,
)
from src.features.provenance.sources import Source, SourceKind

#: 뉴스 조각 원문 맨 앞 — `"(2025-03-12 보도 · mk.co.kr) 제목. 설명"`.
#: 정본은 `run_pilot.collect_news()`의 f-string이다. 여기를 고치면 그쪽도 맞춰야 한다.
_NEWS_PREFIX_RE = re.compile(
    r"^\((?P<date>\d{4}-\d{2}-\d{2}) 보도 · (?P<domain>[^)]*)\)\s*(?P<rest>.*)$",
    re.DOTALL,
)

#: DART 공시일(`rcept_dt`)의 원래 모양 — 8자리 숫자(`YYYYMMDD`) 하나뿐이어야 한다.
_RCEPT_DT_RE = re.compile(r"^\d{8}$")


def build_citations(
    fragments: dict[int, dict[str, str]],
    *,
    filing: Optional[dict[str, Any]],
    collected_on: dt.date,
) -> list[Source]:
    """수집 조각 목록 + 수집 정보 → 출처 목록.

    Args:
        fragments: `{조각번호: {"종류", "원문", ("출처")}}`. 05 생성이 문장 뒤
            `[번호]`로 가리키는 바로 그 조각이다. **번호를 새로 매기지 않는다**
            (정본 §근거 표기 — 번호) — 여기서도 그대로 옮긴다.
        filing: `latest_report_rcept()`가 돌려준 전자공시 최신 보고서 1건
            (`report_nm`·`rcept_dt`·`rcept_no` 등). 못 가져왔으면 `None` —
            그때는 공시 계열 조각의 보고서 이름·공시일을 비워 둔다.
        collected_on: 오늘(수집일). 공시 계열 조각의 「수집 …」 줄에 쓴다 —
            이건 우리 쪽이 언제 수집했는지이므로 `filing` 유무와 무관하게 안다.

    Returns:
        `Source` 목록(조각 번호 오름차순). 재료가 모자란 항목은 지어내지 않고
        `Source.is_valid`가 False가 되도록 둔다 — 걸러내는 일은 부르는 쪽이 한다.
    """
    collected_at = collected_on.isoformat()
    sources: list[Source] = []
    for number, frag in sorted(fragments.items()):
        kind = frag.get("종류", "")
        text = frag.get("원문", "")
        if kind == FRAGMENT_KIND_NEWS:
            sources.append(_news_source(number, text))
        elif kind == FRAGMENT_KIND_HOMEPAGE:
            sources.append(_homepage_source(number, frag, collected_at))
        else:
            sources.append(_filing_source(number, kind, text, filing, collected_at))
    return sources


def _news_source(number: int, text: str) -> Source:
    """뉴스 조각 — 원문 앞머리에서 보도일·도메인을 뽑는다. 못 뽑으면 비운다.

    라벨은 기사 제목을 쓴다. 원문이 `"{제목}. {설명}"` 꼴로 이어붙었으므로
    (`run_pilot.collect_news`) 첫 `". "`까지를 제목으로 본다 — 못 가르면
    잘라내다 지어내는 셈이 되므로 원문 전체를 라벨로 남긴다.
    """
    match = _NEWS_PREFIX_RE.match(text)
    if match is None:
        return Source(number=number, kind=SourceKind.NEWS, label=text.strip())

    published_at = match.group("date")
    domain = match.group("domain").strip()
    if domain == NEWS_UNKNOWN_DOMAIN:
        domain = ""  # 자리표시자다 — 실제 도메인처럼 옮기면 지어낸 값이 된다

    rest = match.group("rest").strip()
    title, _sep, _description = rest.partition(". ")
    label = title.strip() or rest

    return Source(
        number=number,
        kind=SourceKind.NEWS,
        label=label,
        published_at=published_at,
        domain=domain,
    )


def _homepage_source(number: int, frag: dict[str, str], collected_at: str) -> Source:
    """홈페이지 조각 — 실제 읽은 URL을 라벨로 삼는다. URL이 없으면 지어내지 않는다.

    ★ 수집일을 반드시 넣는다. 홈페이지는 «언제든 바뀌는» 자료라,
      언제 본 것인지 없으면 사용자가 확인하러 갔을 때 다른 내용을 보게 된다.
    """
    url = frag.get("출처", "").strip()
    return Source(
        number=number,
        kind=SourceKind.OTHER,
        label=url or HOMEPAGE_FALLBACK_LABEL,
        collected_at=collected_at,
    )


def _filing_source(
    number: int,
    kind: str,
    text: str,
    filing: Optional[dict[str, Any]],
    collected_at: str,
) -> Source:
    """공시 계열 조각 — `filing`의 보고서 이름 + 공시일에 조각 종류를 붙인다.

    ★ 재무 API(`fnlttSinglAcnt.json`, 주요계정) 조각은 예외다. `filing`
      (사업·감사보고서 1건)과는 «다른» DART API 호출에서 온 값이라 그 보고서의
      공시일과 반드시 같다는 보장이 없다 — filing의 날짜를 갖다 붙이면 지어낸
      것과 같으므로 공시일은 비우고 출처만 밝힌다.
    """
    if text.startswith(DART_ACCOUNT_FRAGMENT_PREFIX):
        return Source(
            number=number,
            kind=SourceKind.FILING,
            label=DART_ACCOUNT_FRAGMENT_LABEL,
            collected_at=collected_at,
        )

    report_nm = str((filing or {}).get("report_nm") or "").strip()
    disclosed_at = _format_rcept_dt(str((filing or {}).get("rcept_dt") or "")) if filing else ""
    label = " · ".join(part for part in (report_nm, kind) if part)

    return Source(
        number=number,
        kind=SourceKind.FILING,
        label=label,
        disclosed_at=disclosed_at,
        collected_at=collected_at,
    )


def _format_rcept_dt(raw: str) -> str:
    """DART 공시일(`YYYYMMDD`) → `"YYYY-MM-DD"`. 모양이 안 맞으면 비운다(지어내지 않는다)."""
    if _RCEPT_DT_RE.match(raw):
        return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"
    return ""
