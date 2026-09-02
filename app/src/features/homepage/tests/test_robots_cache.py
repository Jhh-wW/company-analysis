"""robots_cache 모듈 단위시험(티켓 B2) — scope 예산에 얹은 host별 robots
판정 캐시가 scope 수명과 정확히 일치하는지 확인한다.

★ 이 모듈(``homepage/robots_cache.py``)은 이번 티켓에서 새로 만들었으므로
  「원본 코드 대비 red」로 보일 이전 버전이 없다 — 결합 카운터 시험
  (``pipeline/tests/test_collect_transport_counts.py``)이 실제 3개 수집기
  경로에서의 red→green을 담당하고, 여기서는 이 모듈 자체의 계약(scope 안
  재사용·scope 밖 무동작·scope 간 격리·차단 판정 유지)만 고정한다.
"""

from __future__ import annotations

from urllib import robotparser

from src.features.homepage.robots_cache import RobotsDecision, cached_robots_decision
from src.features.homepage.safe_http import request_deadline_scope


def _decision(host: str, *, blocked: bool = False) -> RobotsDecision:
    parser = robotparser.RobotFileParser()
    parser.parse(["User-agent: *", "Allow: /"])
    return RobotsDecision(host=host, parser=parser, blocked=blocked, reason_code="robots_ok")


def test_scope_밖에서는_매번_loader를_실행한다() -> None:
    calls: list[int] = []

    def loader() -> RobotsDecision:
        calls.append(1)
        return _decision("example.com")

    cached_robots_decision("example.com", loader)
    cached_robots_decision("example.com", loader)

    assert len(calls) == 2  # scope가 없으면 캐시하지 않는다 — 기존 단독 호출 동작 유지


def test_같은_scope_안에서는_loader가_한_번만_실행된다() -> None:
    calls: list[int] = []

    def loader() -> RobotsDecision:
        calls.append(1)
        return _decision("example.com")

    with request_deadline_scope(5.0):
        first = cached_robots_decision("example.com", loader)
        second = cached_robots_decision("example.com", loader)

    assert len(calls) == 1
    assert first is second


def test_scope가_끝나면_robots_캐시도_사라진다() -> None:
    calls: list[int] = []

    def loader() -> RobotsDecision:
        calls.append(1)
        return _decision("example.com")

    with request_deadline_scope(5.0):
        cached_robots_decision("example.com", loader)

    with request_deadline_scope(5.0):
        cached_robots_decision("example.com", loader)

    # 두 번째 scope는 첫 번째 scope의 캐시를 물려받지 않는다 — 프로세스
    # 전역 캐시가 아니라는 요구사항(다른 회사·다른 요청으로 새면 안 된다).
    assert len(calls) == 2


def test_캐시된_차단_판정은_scope_안에서_계속_차단이다() -> None:
    calls: list[int] = []

    def loader() -> RobotsDecision:
        calls.append(1)
        return _decision("blocked.example", blocked=True)

    with request_deadline_scope(5.0):
        first = cached_robots_decision("blocked.example", loader)
        second = cached_robots_decision("blocked.example", loader)

    assert len(calls) == 1
    assert first.blocked is True
    assert second.blocked is True  # fail-closed 판정이 「허용」으로 새지 않는다


def test_같은_scope라도_다른_host는_각자_loader를_실행한다() -> None:
    calls: list[str] = []

    def make_loader(host: str):
        def loader() -> RobotsDecision:
            calls.append(host)
            return _decision(host)

        return loader

    with request_deadline_scope(5.0):
        cached_robots_decision("a.example", make_loader("a.example"))
        cached_robots_decision("b.example", make_loader("b.example"))
        cached_robots_decision("a.example", make_loader("a.example"))

    assert calls == ["a.example", "b.example"]
