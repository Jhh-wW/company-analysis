"""홈페이지·공식 IR PDF·광역 웹 세 수집기가 한 조사(scope) 안에서 host별
robots.txt 조회를 정확히 한 번만 하도록 공유하는 캐시.

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
★ 캐시 키는 RFC 9309 origin(scheme+host+port)이다 — **host 문자열만
  쓰면 안 된다.** (독립 검토 P0 실측): `logic.py`의 「HTTPS
  전면 실패 → HTTP로 재시도」 경로(`_http_variant`)가 같은
  `collect_homepage_fragments` scope 안에서 돌기 때문에, host만으로
  캐시하면 HTTPS robots.txt(허용)가 캐시된 뒤 HTTP 재시도가 실제 HTTP
  robots.txt(예: 전면 차단)를 다시 확인하지 않고 HTTPS 판정을 그대로
  물려받아 **차단된 사이트를 평문으로 읽어버렸다.** origin 키로 바꾸면
  scheme이 다른 재시도는 자연히 다른 캐시 항목이 되어 항상 다시 조회한다
  (`robots_cache_key` 참조 — 별도 우회 로직 없이 키 설계만으로 막는다).
★ 서로 다른 robots 분류기가 같은 origin을 두고 섞이면(예: `wide_fetch.
  robots_decision`의 RFC 9309 세분류 vs `logic.py`/`ir_pdf.py`의 4xx 일괄
  「이용 불가→빈 규칙」 처리), **먼저 그 origin을 판정한 수집기가 이긴다.**
  이는 설계상 허용한 범위다:
    - 광역 웹 수집은 항상 IR 위임보다 먼저 그 host를 판정한다
      (`wide_collect.collect_official_web_documents`의 호출 순서가 고정
      이다) — IR은 광역의(더 엄격한) 판정을 그대로 물려받는다(요구사항
      3번, 의도한 동작). 광역·IR 모두 HTTPS:443 고정이라 origin 키가 늘
      같다.
    - 홈페이지·IR PDF 수집기끼리는 4xx 처리 규칙이 완전히 같아 호출
      순서와 무관하게 같은 결과다.
    - 홈페이지/IR PDF가 광역보다 먼저 호출되는 조합에서는 둘의(더 느슨한)
      4xx 일괄 판정이 광역에 물려갈 수 있다 — 두 분류기의 경계값(401·
      403·407·408·409·429)에서만 갈리는 좁은 경우다. 최종 보고서의 설계
      한계로 남긴다.
"""

from __future__ import annotations

import urllib.parse
from dataclasses import dataclass
from typing import Callable, Final
from urllib import robotparser

from src.features.homepage.safe_http import active_deadline_budget

#: robots.txt 자체를 확인하지 못했거나(네트워크·서버 오류) 명시적으로
#: 거부돼(RFC 9309 세분류 기준) 이 host를 통째로 막을 때 쓰는 사유.
ROBOTS_REASON_UNREACHABLE = "robots_unreachable"

#: robots.txt를 확인했고(실제 규칙 또는 확인된 부재) 개별 경로 평가로
#: 넘어갈 수 있을 때 쓰는 사유.
ROBOTS_REASON_OK = "robots_ok"

#: scheme별 기본 포트 — 명시 포트가 기본값과 같으면 표기를 생략해
#: "https://x.com"과 "https://x.com:443"이 같은 origin 키로 모이게 한다.
_DEFAULT_PORT_BY_SCHEME: Final[dict[str, int]] = {"https": 443, "http": 80}


def robots_cache_key(robots_url: str) -> str:
    """robots.txt URL에서 RFC 9309 origin(scheme+host+port) 캐시 키를 만든다.

    host만 키로 쓰면 scheme이 다른 재요청(예: HTTPS 실패 뒤 HTTP 재시도)이
    다른 origin의 robots.txt를 확인하지 않고 이전 scheme의 판정을 그대로
    물려받는다 — 독립 검토에서 실측된 P0. 이 함수가 scheme을 키에 포함시켜
    그 사고를 키 설계 단계에서 막는다.
    """

    parsed = urllib.parse.urlsplit(robots_url)
    scheme = (parsed.scheme or "").casefold()
    host = (parsed.hostname or "").casefold()
    if not scheme or not host:
        return ""
    port = parsed.port
    default_port = _DEFAULT_PORT_BY_SCHEME.get(scheme)
    if port is not None and port != default_port:
        return f"{scheme}://{host}:{port}"
    return f"{scheme}://{host}"


@dataclass(frozen=True)
class RobotsDecision:
    """한 origin의 robots.txt 조회 결과 — 세 수집기가 각자 타입으로 감싼다.

    ``blocked``는 origin-level 게이트다: True면 robots.txt 자체를 확인하지
    못했거나 명시적으로 거부됐다는 뜻이라 개별 경로 Disallow 평가로
    내려가지 않는다(어느 수집기든 이 시점에서 그 origin을 통째로 실패
    처리한다). False면 ``parser``가 실제(또는 확인된 빈) 규칙을 담고
    있으니 호출자가 ``parser.can_fetch(...)``로 경로별 판정을 계속한다.
    """

    host: str
    parser: robotparser.RobotFileParser
    blocked: bool
    reason_code: str
    #: blocked=True일 때, 원래 fetch 실패의 사람이 읽는 원인(``str(exc)``).
    #: 독립 검토 P2: ``reason_code``만 남기면 "HTTP 503"·"TimeoutError: ..."
    #: 같은 구체적 원인이 일반 문구("robots_unreachable")로 뭉개져 진단이
    #: 어려워진다 — 호출자가 최종 예외 메시지에 이 값을 이어붙인다.
    detail: str = ""


def cached_robots_decision(
    cache_key: str,
    loader: Callable[[], RobotsDecision],
) -> RobotsDecision:
    """origin의 robots 판정을 scope 예산에서 재사용하거나, 없으면 ``loader``로 만든다.

    ``cache_key``는 ``robots_cache_key()``로 만든 RFC 9309 origin(scheme+
    host+port) 문자열이어야 한다 — host만 넘기면 scheme이 다른 재시도가
    잘못된 판정을 물려받는다(모듈 docstring의 P0 참조).

    scope 밖(``active_deadline_budget()``가 None) 또는 key가 빈 문자열이면
    캐시를 쓰지 않고 매번 ``loader``를 그대로 실행한다 — 기존 단독 호출
    동작을 바꾸지 않는다.
    """

    if not cache_key:
        return loader()
    budget = active_deadline_budget()
    if budget is None:
        return loader()
    cache = budget.robots_cache
    cached = cache.get(cache_key)
    if isinstance(cached, RobotsDecision):
        return cached
    decision = loader()
    cache[cache_key] = decision
    return decision
