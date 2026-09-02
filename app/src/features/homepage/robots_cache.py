"""홈페이지·공식 IR PDF·광역 웹 세 수집기가 한 조사(scope) 안에서 host별
robots.txt 조회를 정확히 한 번만 하도록 공유하는 캐시(티켓 B2).

실측 결함: `real.py`가 홈페이지 수집(`logic.collect_homepage_fragments`)과
공식 IR 수집(`ir_pdf.collect_official_ir_fragments`)을 각각 호출하면 둘 다
같은 host의 robots.txt를 **따로** 확인했다(apex+www 별칭까지 겹치면 최대
4회). 광역 웹 수집(`wide_collect.py`)의 IR 위임도 이미 `_ensure_host_policy`가
확인한 host를 다시 확인했다 — 결속된 host마다 2회.

★ 캐시는 `safe_http.request_deadline_scope`가 여는 예산 객체
  (`_DeadlineBudget.robots_cache`)에 얹는다. 그 scope가 끝나면(각 수집기의
  `with request_deadline_scope(...)` 블록을 벗어나면) 캐시도 함께 사라진다
  — 프로세스 전역·모듈 수준 캐시가 아니다(다른 회사·다른 요청으로 새면
  안 된다는 요구사항, DNS cache와 같은 패턴).
★ scope 밖(단독 호출·`request_deadline_scope` 없이 부른 기존 단위시험)에서는
  캐시가 아예 동작하지 않는다 — 매번 `loader`를 그대로 실행해 기존 단독
  호출 동작을 그대로 유지한다.
★ 캐시 키는 host 문자열 하나다(scheme·port는 넣지 않는다) — 이미
  `wide_fetch.WideRobotsPolicy`가 쓰는 것과 같은 관례다. apex와 www는
  서로 다른 host 문자열이라 자연히 분리된다.
★ 서로 다른 robots 분류기가 같은 host를 두고 섞이면(예: `wide_fetch.
  robots_decision`의 RFC 9309 세분류 vs `logic.py`/`ir_pdf.py`의 4xx 일괄
  「이용 불가→빈 규칙」 처리), **먼저 그 host를 판정한 수집기가 이긴다.**
  이는 설계상 허용한 범위다:
    - 광역 웹 수집은 항상 IR 위임보다 먼저 그 host를 판정한다
      (`wide_collect.collect_official_web_documents`의 호출 순서가 고정
      이다) — IR은 광역의(더 엄격한) 판정을 그대로 물려받는다(요구사항
      3번, 의도한 동작).
    - 홈페이지·IR PDF 수집기끼리는 4xx 처리 규칙이 완전히 같아 호출
      순서와 무관하게 같은 결과다.
    - 홈페이지/IR PDF가 광역보다 먼저 호출되는 조합에서는 둘의(더 느슨한)
      4xx 일괄 판정이 광역에 물려갈 수 있다 — 두 분류기의 경계값(401·
      403·407·408·409·429)에서만 갈리는 좁은 경우다. 오늘 실제
      운영 배선(`real.py`)은 세 수집기를 같은 scope로 묶지 않으므로 이
      경우는 아직 발생하지 않는다 — scope를 공유하는 배선이 생기면
      최종 보고서의 설계 한계로 남긴다.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
from urllib import robotparser

from src.features.homepage.safe_http import active_deadline_budget

#: robots.txt 자체를 확인하지 못했거나(네트워크·서버 오류) 명시적으로
#: 거부돼(RFC 9309 세분류 기준) 이 host를 통째로 막을 때 쓰는 사유.
ROBOTS_REASON_UNREACHABLE = "robots_unreachable"

#: robots.txt를 확인했고(실제 규칙 또는 확인된 부재) 개별 경로 평가로
#: 넘어갈 수 있을 때 쓰는 사유.
ROBOTS_REASON_OK = "robots_ok"


@dataclass(frozen=True)
class RobotsDecision:
    """한 host의 robots.txt 조회 결과 — 세 수집기가 각자 타입으로 감싼다.

    ``blocked``는 host-level 게이트다: True면 robots.txt 자체를 확인하지
    못했거나 명시적으로 거부됐다는 뜻이라 개별 경로 Disallow 평가로
    내려가지 않는다(어느 수집기든 이 시점에서 그 host를 통째로 실패
    처리한다). False면 ``parser``가 실제(또는 확인된 빈) 규칙을 담고
    있으니 호출자가 ``parser.can_fetch(...)``로 경로별 판정을 계속한다.
    """

    host: str
    parser: robotparser.RobotFileParser
    blocked: bool
    reason_code: str


def cached_robots_decision(
    host: str,
    loader: Callable[[], RobotsDecision],
) -> RobotsDecision:
    """host의 robots 판정을 scope 예산에서 재사용하거나, 없으면 ``loader``로 만든다.

    scope 밖(``active_deadline_budget()``가 None) 또는 host가 빈 문자열이면
    캐시를 쓰지 않고 매번 ``loader``를 그대로 실행한다 — 기존 단독 호출
    동작을 바꾸지 않는다.
    """

    if not host:
        return loader()
    budget = active_deadline_budget()
    if budget is None:
        return loader()
    cache = budget.robots_cache
    cached = cache.get(host)
    if isinstance(cached, RobotsDecision):
        return cached
    decision = loader()
    cache[host] = decision
    return decision
