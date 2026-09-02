"""typed 수집을 켠 한 조사에서 DART·홈페이지·공식 IR 전송 횟수를 센다.

★ 무엇을 막는가 — 새 수집기를 얹으면 «같은 무료 자원을 또 부르는» 결함이
  늘어난다(실측: robots.txt 최대 4회, 홈페이지·IR 각 2회). typed
  수집기까지 더해지면 DART 일일 한도까지 같이 깎이고, 한도 소진은 다시
  오판정(P1-B)으로 번진다. 이 시험은 **스위치 on + FULL**로 `real._collect`를
  실제로 돌려 전송 횟수를 잰다.

★ 생산 경로를 통째로 가짜로 바꾸지 않는다 — `real._collect`·
  `collection_cache_scope`·typed 수집기·변환기는 전부 진짜 코드가 돌고,
  바깥 전송 경계(DART 조회기, HTTP fetcher)만 가짜다.

★ 실제 네트워크·DART·AI 호출 0건.
"""

from __future__ import annotations

import collections
import pathlib
from typing import Any

import pytest

from src.core import typed_collector_switch as switch
from src.features.homepage.logic import collect_homepage_fragments
from src.features.homepage.ir_pdf import collect_official_ir_fragments
from src.features.pipeline import engine_mode, real
from src.features.pipeline.port import UserInput
from src.shared import generation_coordination
from src.shared.report_evidence.release_mode import REPORT_RELEASE_MODE_ENV_NAME

# 세 수집기용 공유 가짜 전송과 robots 집계는 B2가 이미 만들어 뒀다. 두 벌
# 만들면 한쪽만 고쳐져 조용히 어긋난다.
from src.features.pipeline.tests.test_collect_transport_counts import (  # noqa: F401
    COMPANY_HOST,
    ROBOTS_ALLOW_ALL,
    ROBOTS_URL,
    ROOT,
    _body,
    _robots_hits_by_host,
    _SharedFakeSite,
)
from src.features.pipeline.tests.test_real_cache import CORP_ID, JOB, POSTING
from src.features.pipeline.tests.test_typed_dart_wiring import (
    TYPED_RCEPT_NO,
    _TypedDartFakeEngine,
    pytestmark,  # noqa: F401 - 엔진 트리가 없으면 이 파일도 통째로 건너뛴다
)


@pytest.fixture(autouse=True)
def _fresh_process_typed_collector_switch():
    switch._reset_process_typed_collector_switch_for_tests()  # noqa: SLF001
    yield
    switch._reset_process_typed_collector_switch_for_tests()  # noqa: SLF001


def _run_collect_with_typed_and_web(
    engine: _TypedDartFakeEngine,
    site: _SharedFakeSite,
    monkeypatch: pytest.MonkeyPatch,
) -> list[dict[str, Any]]:
    """typed 수집 + 홈페이지 + 공식 IR을 한 `_collect` 호출로 전부 돌린다."""

    monkeypatch.setattr(real.homepage_link, "workable_url", lambda raw: raw)

    def wrapped_collect_homepage(url: str, **kwargs: object):
        kwargs.pop("fetch", None)
        return collect_homepage_fragments(url, fetch=site.homepage_fetch, **kwargs)

    def wrapped_collect_ir(url: str, **kwargs: object):
        kwargs.pop("html_fetch", None)
        kwargs.pop("pdf_fetch", None)
        return collect_official_ir_fragments(
            url, html_fetch=site.ir_html_fetch, pdf_fetch=site.ir_pdf_fetch, **kwargs
        )

    # `_collect` 자체는 건드리지 않는다 — 그 안이 부르는 이름만 바꿔 끼운다.
    monkeypatch.setattr(real, "collect_homepage_fragments", wrapped_collect_homepage)
    monkeypatch.setattr(real, "collect_official_ir_fragments", wrapped_collect_ir)

    steps: list[dict[str, Any]] = []
    counter = engine.UsageCounter()
    financials, years = engine.fetch_financials(CORP_ID, counter)
    real._collect(  # noqa: SLF001 - 생산 수집 경로 그대로
        engine,
        engine._client(),  # noqa: SLF001
        {
            "status": "000",
            "corp_code": CORP_ID,
            "corp_name": "가나다전자",
            "corp_name_eng": "GANADA ELECTRONICS CO., LTD.",
            "hm_url": ROOT,
        },
        UserInput(
            company="가나다전자", job=JOB, region="서울 강남구", posting_text=POSTING
        ),
        counter,
        steps,
        financials=financials,
        fin_years=years,
        filing=None,
        generation_mode=engine_mode.EngineMode.V2,
        corp_code=CORP_ID,
    )
    return steps


def test_transport_종류별_정확히_1회(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """typed·홈페이지·IR을 한꺼번에 켠 조사에서 같은 자원을 두 번 부르지 않는다.

    - DART 공시목록(list.json)은 pblntf_ty별 1회
    - DART 공시원문은 접수번호별 1회
    - robots.txt는 origin(host)당 1회 — 세 수집기가 캐시를 공유한다
    - 그 밖의 HTTP URL도 URL마다 1회
    """

    monkeypatch.setenv(switch.TYPED_DART_COLLECTOR_ENV_NAME, "1")
    monkeypatch.setenv(REPORT_RELEASE_MODE_ENV_NAME, "FULL")
    engine = _TypedDartFakeEngine(tmp_path / "raw")
    site = _SharedFakeSite(
        {
            ROBOTS_URL: ROBOTS_ALLOW_ALL,
            ROOT: _body("공식 홈페이지 소개 문단"),
            f"{ROOT}/": _body("공식 홈페이지 소개 문단"),
        }
    )

    steps = _run_collect_with_typed_and_web(engine, site, monkeypatch)

    typed_step = next(
        step for step in steps if step.get("step") == real.TYPED_DART_COLLECT_STEP
    )
    assert typed_step.get("조각수"), "typed 수집이 조각을 하나도 못 냈다(전제 붕괴)"

    # DART — 목록은 공시종류당 1회, 원문은 접수번호당 1회.
    assert collections.Counter(engine.list_calls) == collections.Counter({"A": 1})
    assert engine.document_calls == [TYPED_RCEPT_NO]

    # robots.txt — 세 수집기가 합쳐 host당 1회.
    hits = _robots_hits_by_host(site.calls)
    assert hits == {COMPANY_HOST: 1}, (
        f"robots.txt 요청이 host당 1회가 아님: "
        f"{[url for url in site.calls if url.endswith('robots.txt')]}"
    )

    # 그 밖의 HTTP도 URL마다 1회 — 홈페이지·IR이 같은 주소를 다시 부르지 않는다.
    duplicated = {
        url: count
        for url, count in collections.Counter(site.calls).items()
        if count > 1
    }
    assert duplicated == {}, f"같은 URL을 두 번 이상 불렀다: {duplicated}"


def test_수집_단계는_생성_공유나_캐시_적중을_거치지_않는다(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """이 횟수가 «캐시 덕분에 줄어든 값」이 아님을 밝힌다.

    `_collect`는 유료 생성 조정(owner/waiter)에도, 보고서 1층 캐시에도
    닿지 않는다 — 위 시험의 1회는 실제로 전송이 한 번 나갔다는 뜻이다.
    """

    monkeypatch.setenv(switch.TYPED_DART_COLLECTOR_ENV_NAME, "1")
    monkeypatch.setenv(REPORT_RELEASE_MODE_ENV_NAME, "FULL")
    engine = _TypedDartFakeEngine(tmp_path / "raw")
    site = _SharedFakeSite(
        {
            ROBOTS_URL: ROBOTS_ALLOW_ALL,
            ROOT: _body("공식 홈페이지 소개 문단"),
            f"{ROOT}/": _body("공식 홈페이지 소개 문단"),
        }
    )

    assert generation_coordination.is_active() is False
    steps = _run_collect_with_typed_and_web(engine, site, monkeypatch)

    assert generation_coordination.is_active() is False
    assert [step for step in steps if "캐시" in str(step.get("step") or "")] == []
    assert engine.generate_ai_calls == 0
    assert engine.posting_ai_calls == 0
