"""robots_cache 모듈 단위시험 — scope 예산에 얹은 origin별 robots
판정 캐시가 scope 수명과 정확히 일치하고, scheme이 다르면 절대 섞이지
않는지 확인한다.

★ 이 모듈(``homepage/robots_cache.py``)은 이번 티켓에서 새로 만들었으므로
  「원본 코드 대비 red」로 보일 이전 버전이 없다 — 결합 카운터 시험
  (``pipeline/tests/test_collect_transport_counts.py``)이 실제 3개 수집기
  경로에서의 red→green을 담당하고, 여기서는 이 모듈 자체의 계약(scope 안
  재사용·scope 밖 무동작·scope 간 격리·차단 판정 유지·origin 키 분리)만
  고정한다.

★ P0(독립 검토): 최초 구현은 캐시 키로 host 문자열만 썼다.
  ``logic.py``의 HTTPS 전면 실패 → HTTP 재시도 경로가 같은
  ``collect_homepage_fragments`` scope 안에서 돌기 때문에, host만으로
  캐시하면 HTTPS robots(허용)가 캐시된 뒤 HTTP 재시도가 실제 HTTP
  robots.txt(예: 전면 차단)를 다시 확인하지 않고 HTTPS 판정을 그대로
  물려받아 **차단된 사이트를 평문으로 읽어버렸다.** 이 파일의
  ``test_scheme이_다르면_같은_host라도_다른_캐시_항목이다``가 그 사고를
  고정한다.
"""

from __future__ import annotations

from urllib import robotparser

from src.features.homepage.robots_cache import (
    RobotsDecision,
    cached_robots_decision,
    robots_cache_key,
)
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


# ── P0(독립 검토): scheme이 다르면 다른 origin이다 ─────────────


def test_robots_cache_key는_scheme이_다르면_다른_키를_돌려준다() -> None:
    https_key = robots_cache_key("https://company.example/robots.txt")
    http_key = robots_cache_key("http://company.example/robots.txt")

    assert https_key != http_key
    assert https_key == "https://company.example"
    assert http_key == "http://company.example"


def test_robots_cache_key는_기본_포트_표기_유무를_같은_키로_본다() -> None:
    assert robots_cache_key("https://company.example/robots.txt") == robots_cache_key(
        "https://company.example:443/robots.txt"
    )
    assert robots_cache_key("http://company.example/robots.txt") == robots_cache_key(
        "http://company.example:80/robots.txt"
    )


def test_robots_cache_key는_기본이_아닌_포트는_구분한다() -> None:
    assert robots_cache_key("https://company.example:8443/robots.txt") != robots_cache_key(
        "https://company.example/robots.txt"
    )


def test_scheme이_다르면_같은_host라도_다른_캐시_항목이다() -> None:
    """P0 재현: HTTPS robots(허용)를 캐시한 뒤 HTTP robots(전면 차단)를
    다시 물으면, host만 같다는 이유로 HTTPS의 「허용」 판정을 물려받지
    않고 HTTP robots.txt를 실제로 다시 조회해 차단을 지켜야 한다."""

    calls: list[str] = []

    def https_loader() -> RobotsDecision:
        calls.append("https")
        return _decision("company.example", blocked=False)

    def http_loader() -> RobotsDecision:
        calls.append("http")
        return _decision("company.example", blocked=True)

    with request_deadline_scope(5.0):
        https_decision = cached_robots_decision(
            robots_cache_key("https://company.example/robots.txt"), https_loader
        )
        http_decision = cached_robots_decision(
            robots_cache_key("http://company.example/robots.txt"), http_loader
        )

    assert calls == ["https", "http"]  # 둘 다 실제로 조회됐다 — 캐시가 섞이지 않았다
    assert https_decision.blocked is False
    assert http_decision.blocked is True  # HTTPS의 「허용」이 HTTP로 새지 않았다
