"""경쟁사 비교와 뉴스 검색을 동시에 돌리는 스위치의 운영 배선 계약.

단위 계약만 초록불이면 «동시에 돈다»를 증명하지 못한다. 이 파일은 가짜 DART
엔진과 메모리 수집기로 실제 ``RealPipeline.run``을 끝까지 돌려서 다음을 못 박는다.

- 스위치를 켜면 두 갈래가 **같은 약속 지점에서 서로를 만난다**(시계 없이 판정).
- 켜든 끄든 보고서 입력·캐시 열쇠·단계 기록·DART 호출·비용이 **같다**.
- 비교가 막히면 켜든 끄든 같은 사유로 멈추고 뉴스 단계는 남지 않는다.
- 비교 지문을 못 만들어도 예전처럼 「내부 근거 계약」 관문으로 멈춘다.
- 갈래 «안»에서 남긴 단계가 완료 순서가 아니라 정해진 차례로 놓인다.
- 다만 비교가 막히는 실행에서 **켜면 뉴스 검색 호출이 이미 나간다** — 이
  스위치가 만드는 유일한 외부 호출 증가 지점이라 여기서 함께 못 박는다.
- 갈래 스레드에 실행 문맥이 복사된다(`copy_context`를 빼면 실패해야 한다).

진짜 AI·네트워크는 한 번도 열지 않는다.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any

import pytest

from src.core import news_intake_switch, parallel_collect_switch
from src.features.budget import provider_budget
from src.features.company_comparison import ComparisonBlockedError
from src.features.observability import run_diagnostics
from src.features.pipeline import real
from src.features.pipeline.port import Outcome, RunResult
from src.features.pipeline.tests.test_news_intake_wiring import (
    GROUNDED_RUNTIME_BODY,
    _grounded_runtime_analysis,
    _grounded_runtime_item,
    _grounded_runtime_result,
)
from src.features.pipeline.tests.test_official_evidence_runtime import (
    FakeEngine,
    _Collector,
    _freeze_runtime,
    _official_result,
    _request,
    _RuntimeCalls,
    _wire_runtime,
)
from src.shared import engine_build_identity as build_identity_contract
from src.shared.final_gate_diagnostics import (
    FINAL_GATE_REASON_INTERNAL_EVIDENCE_CONTRACT,
    FINAL_GATE_REASON_OFFICIAL_EVIDENCE_INSUFFICIENT,
)
from src.shared.report_evidence.constants import ReleaseMode
from src.shared.stage_elapsed_constants import STAGE_ELAPSED_MS_KEY, STAGE_ELAPSED_STEP


#: 약속 지점에서 서로를 기다릴 갈래 수. **생산 상수를 쓰지 않는다** — worker 수를
#: 1로 낮추면 경쟁이 사라지는데 상수를 따라가면 그때도 초록불이 되기 때문이다.
#: 이 시험이 지키려는 것은 「정확히 두 갈래가 동시에 산다」이므로 리터럴 2다.
BRANCH_MEETING_PARTIES = 2

#: 차례로 도는 실행에서 첫 갈래가 혼자 기다리다 포기하기까지의 시간. 동시에 도는
#: 실행은 밀리초 안에 만나므로 이 값이 판정을 흔들지 않는다 — OFF 실행 한 번의
#: 비용일 뿐이다.
BRANCH_MEETING_TIMEOUT_SECONDS = 3.0

#: 비교 갈래가 「뉴스가 단계를 먼저 남겼다」는 신호를 기다리는 시간.
STEP_ORDER_WAIT_SECONDS = 3.0

#: 갈래 «안»에서 `current_steps()`로 남기는 탐침 단계. 갈래별 목록을 거쳐
#: 합류 지점이 정한 차례(비교 → 뉴스)로 공용 목록에 놓여야 한다.
COMPARISON_PROBE_STEP = "탐침_비교갈래_단계"
NEWS_PROBE_STEP = "탐침_뉴스갈래_단계"

#: 비교 갈래가 같은 접수번호로 원문을 요청하는 횟수. 요청 단위 정본 artifact가
#: 실제로 한 번만 내려받는지 보려면 2회 이상이어야 한다.
SAME_RECEIPT_REQUEST_COUNT = 2

#: 뉴스 검색 스냅샷이 남기는 단계 이름. 운영 상수가 아니라 리터럴이라 여기에도
#: 그대로 적는다 — 생산 코드에서 이름이 바뀌면 이 시험이 먼저 깨져야 한다.
NEWS_SEARCH_SNAPSHOT_STEP = "5b_뉴스_검색스냅샷"
NEWS_STEP_PREFIX = "5b_뉴스"
COMPARISON_BLOCKED_STEP = "v2_FULL_회사차별점사전검사_차단"
COMPARISON_TRANSPORT_BLOCKED_STEP = "v2_FULL_공식비교transport_차단"

#: 비교 갈래가 원문을 요청할 접수번호. FakeEngine의 최신 공시와 같은 값이다.
PROBE_RECEIPT_NUMBER = "20260315000123"

#: 가짜 비교 생산물. 실행마다 같은 값이어야 ON/OFF 산출물을 그대로 비교할 수 있다.
COMPARISON_SENTINEL = "가짜-비교-생산물"


class _UnserializableComparison:
    """캐시 지문을 만들 수 없는 비교 생산물.

    `canonical_sha256`은 이런 값에 `TypeError`를 낸다. 예전 코드는 지문 접기가
    비교 생산과 같은 `try` 안이라 그 오류도 「내부 근거 계약」 GATE_STOPPED로
    끝났다 — 갈래로 나누면서 그 덮개가 사라지지 않았는지 이 값으로 확인한다.
    """


#: 비교 결과를 캐시 열쇠에 접는 «생산» 함수. 공용 배선 helper가 이 자리를
#: 항등함수로 바꿔 두기 때문에, 그대로 두면 비교가 열쇠에 기여하는지 아닌지를
#: 이 파일이 전혀 보지 못한다(음성 대조에서 실제로 통과해 버렸다). import 시점의
#: 원본을 잡아 두고 각 실행에서 되돌려 끼운다.
PRODUCTION_COMPARISON_DIGEST = real._comparison_generation_digest

#: 유료 phase 예산. 갈래 스레드에서 이 문맥이 보이는지만 확인하므로 값은 넉넉히.
PROBE_BUDGET_KRW = 100_000.0

#: ON/OFF가 같아야 하는 composer 입력 열쇠. engine·client처럼 실행마다 객체가
#: 달라지는 자리는 빼고, 생산 코드가 «만든 값»만 고른다.
COMPOSER_IDENTITY_KEYS = (
    "company_name",
    "corp_type",
    "frags",
    "financials",
    "filing",
    "revenue_tables",
    "business_date",
    "model",
    "steps",
    "corp_id",
    "current_fiscal_year",
    "source_identity_digest",
    "comparison_result",
    "release_mode_override",
    "supplementary_research_required",
)


@pytest.fixture(autouse=True)
def _reset_switches(monkeypatch: pytest.MonkeyPatch):
    """두 동결 스위치를 시험마다 열어 두고, 끝나면 다시 비운다."""

    news_intake_switch._reset_process_news_intake_switch_for_tests()  # noqa: SLF001
    parallel_collect_switch._reset_process_parallel_collect_switch_for_tests()  # noqa: SLF001
    monkeypatch.delenv(news_intake_switch.NEWS_INTAKE_ENV_NAME, raising=False)
    monkeypatch.delenv(
        parallel_collect_switch.PARALLEL_COLLECT_ENV_NAME, raising=False
    )
    yield
    news_intake_switch._reset_process_news_intake_switch_for_tests()  # noqa: SLF001
    parallel_collect_switch._reset_process_parallel_collect_switch_for_tests()  # noqa: SLF001
    # 이 파일은 한 시험 안에서 본조사를 두 번 돌리며 process 신원을 두 번
    # 얼린다. 다음 파일에 V2/FULL이 얼어붙은 채 넘어가지 않게 여기서 푼다.
    real.engine_mode._reset_process_engine_mode_for_tests()  # noqa: SLF001
    build_identity_contract._reset_process_engine_build_identity_for_tests()  # noqa: SLF001


class _ProbeEngine(FakeEngine):
    """DART 호출을 «무엇을 몇 번» 수준으로 기록하는 가짜 엔진."""

    def __init__(self) -> None:
        super().__init__()
        self.get_json_calls: list[tuple[str, tuple[tuple[str, Any], ...]]] = []
        self.download_receipts: list[str] = []

    def get_json(
        self, endpoint: str, params: dict[str, Any], counter: Any
    ) -> dict[str, Any]:
        self.get_json_calls.append((endpoint, tuple(sorted(params.items()))))
        return super().get_json(endpoint, params, counter)

    def download_document(
        self,
        rcept_no: str,
        raw_dir: Any,
        counter: Any,
        *,
        require_official_url_sidecar: bool = False,
    ) -> str:
        self.download_receipts.append(str(rcept_no))
        return super().download_document(
            rcept_no,
            raw_dir,
            counter,
            require_official_url_sidecar=require_official_url_sidecar,
        )


def _provider_budget_visible() -> bool:
    """이 스레드에서 유료 예산 문맥이 보이는가.

    ``copy_context``로 문맥을 옮기지 않으면 새 스레드의 ContextVar는 기본값
    (설치 안 됨)이라 여기서 False가 된다.
    """

    try:
        provider_budget.current()
    except provider_budget.ProviderBudgetUnavailable:
        return False
    return True


@dataclass
class _BranchProbe:
    """두 갈래가 무엇을 봤는지 모으는 관측 기록.

    시계로 «겹쳤다»를 재지 않는다. 부하가 걸린 기계에서는 스레드를 띄우는 데만
    수백 밀리초가 끼어 거짓 실패가 난다. 대신 두 갈래가 **같은 약속 지점**
    (`threading.Barrier`)에서 서로를 기다리게 한다 — 동시에 돌면 반드시 만나고,
    차례로 돌면 첫 갈래가 혼자 기다리다 `BrokenBarrierError`로 깨진다.
    """

    comparison_calls: int = 0
    search_calls: int = 0
    comparison_budget_visible: bool | None = None
    news_budget_visible: bool | None = None
    same_receipt_requests: int = 0
    comparison_error: BaseException | None = None
    comparison_result: Any = COMPARISON_SENTINEL
    #: 두 갈래가 약속 지점에서 실제로 만났는가. None이면 그 갈래가 아예 안 왔다.
    meeting_point: "threading.Barrier | None" = None
    comparison_met: bool | None = None
    news_met: bool | None = None
    #: 뉴스 갈래가 단계를 먼저 남겼다는 신호. 비교 갈래가 이걸 기다렸다 남긴다.
    step_order_gate: "threading.Event | None" = None
    comparison_waited_for_news_step: bool | None = None


@dataclass
class _BranchRun:
    """한 번의 본조사 실행에서 나온 결과·배선 기록·관측."""

    result: RunResult
    calls: _RuntimeCalls
    probe: _BranchProbe
    engine: _ProbeEngine
    steps: list[dict[str, Any]] = field(default_factory=list)


def _install_branch_probes(
    monkeypatch: pytest.MonkeyPatch,
    probe: _BranchProbe,
) -> None:
    """비교 생산기를 약속 지점·문맥·원문 요청을 기록하는 대역으로 바꾼다."""

    def prepare_comparison(**kwargs: Any) -> Any:
        probe.comparison_calls += 1
        probe.comparison_budget_visible = _provider_budget_visible()
        download = kwargs["dart_download_document"]
        # 같은 접수번호를 여러 번 요청해도 실제 내려받기는 한 번이어야 한다.
        for _ in range(SAME_RECEIPT_REQUEST_COUNT):
            download(PROBE_RECEIPT_NUMBER, kwargs["engine"].RAW_DIR, kwargs["counter"])
            probe.same_receipt_requests += 1
        if probe.step_order_gate is not None:
            # 뉴스 갈래가 «먼저» 단계를 남기게 두고, 그 뒤에 이쪽이 남긴다.
            # 완료 순서와 기록 순서가 반대가 되는 상황을 일부러 만든다.
            probe.comparison_waited_for_news_step = probe.step_order_gate.wait(
                STEP_ORDER_WAIT_SECONDS
            )
            run_diagnostics.current_steps().append({"step": COMPARISON_PROBE_STEP})
        if probe.meeting_point is not None:
            probe.comparison_met = _meet(probe.meeting_point)
        if probe.comparison_error is not None:
            raise probe.comparison_error
        return probe.comparison_result

    monkeypatch.setattr(real, "_prepare_v2_comparison_result", prepare_comparison)
    monkeypatch.setattr(
        real, "_comparison_generation_digest", PRODUCTION_COMPARISON_DIGEST
    )


def _meet(meeting_point: threading.Barrier) -> bool:
    """약속 지점에서 다른 갈래를 기다린다. 혼자면 깨진 채로 돌아온다."""

    try:
        meeting_point.wait()
    except threading.BrokenBarrierError:
        return False
    return True


def _probe_search_news(probe: _BranchProbe):
    """첫 호출에서만 약속 지점·문맥·단계 기록을 남기는 가짜 뉴스 검색."""

    def search_news(_query: str, **_kwargs: object) -> Any:
        probe.search_calls += 1
        if probe.search_calls == 1:
            probe.news_budget_visible = _provider_budget_visible()
            if probe.step_order_gate is not None:
                # 갈래 안에서 «운영 방식»으로 단계를 남긴다. 갈래별 목록으로
                # 가지 않으면 이 줄이 공용 목록에 먼저 박혀 순서가 뒤집힌다.
                run_diagnostics.current_steps().append({"step": NEWS_PROBE_STEP})
                probe.step_order_gate.set()
            if probe.meeting_point is not None:
                probe.news_met = _meet(probe.meeting_point)
            return _grounded_runtime_result([_grounded_runtime_item()])
        return _grounded_runtime_result([])

    return search_news


def _run_research(
    *,
    parallel: bool,
    comparison_error: BaseException | None = None,
    comparison_result: Any = COMPARISON_SENTINEL,
    meet_at_barrier: bool = False,
    record_branch_steps: bool = False,
) -> _BranchRun:
    """운영 배선 그대로 본조사를 한 번 돌리고 관측을 모아 온다."""

    with pytest.MonkeyPatch.context() as monkeypatch:
        news_intake_switch._reset_process_news_intake_switch_for_tests()  # noqa: SLF001
        parallel_collect_switch._reset_process_parallel_collect_switch_for_tests()  # noqa: SLF001
        monkeypatch.setenv(news_intake_switch.NEWS_INTAKE_ENV_NAME, "1")
        if parallel:
            monkeypatch.setenv(
                parallel_collect_switch.PARALLEL_COLLECT_ENV_NAME,
                parallel_collect_switch.PARALLEL_COLLECT_ENV_ON,
            )
        else:
            monkeypatch.delenv(
                parallel_collect_switch.PARALLEL_COLLECT_ENV_NAME, raising=False
            )
        assert parallel_collect_switch.enabled() is parallel

        _freeze_runtime(
            monkeypatch,
            mode=real.engine_mode.EngineMode.V2,
            release_mode=ReleaseMode.FULL,
        )
        engine = _ProbeEngine()
        collector = _Collector([_official_result()])
        calls = _wire_runtime(monkeypatch, engine=engine)
        probe = _BranchProbe(
            comparison_error=comparison_error,
            comparison_result=comparison_result,
            meeting_point=(
                threading.Barrier(
                    BRANCH_MEETING_PARTIES,
                    timeout=BRANCH_MEETING_TIMEOUT_SECONDS,
                )
                if meet_at_barrier
                else None
            ),
            step_order_gate=threading.Event() if record_branch_steps else None,
        )
        _install_branch_probes(monkeypatch, probe)

        user_input, card = _request()
        # 단계 기록은 시험이 따로 모으지 않는다. 운영 실행이 자기 자리에 쌓고
        # 끝낼 때 넘겨주는 목록을 그대로 받는다 — 멈춘 실행에서도 채워진다.
        with run_diagnostics.capture() as sink:
            with provider_budget.activate(PROBE_BUDGET_KRW):
                result = real.RealPipeline(
                    official_evidence_collector=collector,
                    news_search=_probe_search_news(probe),
                    news_analyze=_grounded_runtime_analysis,
                    news_fetch_text=lambda _url: GROUNDED_RUNTIME_BODY,
                ).run(user_input, card)
        assert sink.filled, "운영 실행이 단계 기록을 넘겨주지 않았습니다"

    return _BranchRun(
        result=result, calls=calls, probe=probe, engine=engine, steps=sink.steps
    )


def _composer_identity(run: _BranchRun) -> dict[str, Any]:
    """생산 코드가 composer에 넘긴 값 중 실행마다 같아야 하는 것만 고른다."""

    assert len(run.calls.composers) == 1
    composer = run.calls.composers[0]
    identity = {key: composer[key] for key in COMPOSER_IDENTITY_KEYS}
    # composer 에 넘기는 진단 목록에는 단계별 소요 시간이 실려 있고 그 값은
    # 실행마다 다르다. 항목·순서는 그대로 비교하고 시간 값만 지운다.
    identity["steps"] = _without_elapsed_ms(identity["steps"])
    # SourceStatus는 값 비교가 되지만 목록 순서까지 함께 고정한다.
    identity["sources"] = [repr(source) for source in composer["sources"]]
    return identity


def _without_elapsed_ms(steps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """단계소요 항목의 소요ms 값만 지운 사본. 시간은 비결정이라 동일성 비교에서 뺀다."""

    stripped: list[dict[str, Any]] = []
    for item in steps:
        if item.get("step") == STAGE_ELAPSED_STEP:
            stripped.append({k: v for k, v in item.items() if k != STAGE_ELAPSED_MS_KEY})
        else:
            stripped.append(item)
    return stripped


def _step_names(steps: list[dict[str, Any]]) -> list[str]:
    return [str(step.get("step") or "") for step in steps]


def test_스위치를_켜면_두_갈래가_같은_약속_지점에서_만난다() -> None:
    """시계 없이 «동시»를 판정한다.

    두 갈래가 같은 약속 지점에서 서로를 기다린다. 동시에 살아 있어야만 둘 다
    통과하므로, 켜면 반드시 만나고 끄면 첫 갈래가 혼자 기다리다 깨진다. 부하가
    판정을 흔들 수 없다 — 기다리는 쪽은 상대가 올 때까지 기다리기 때문이다.
    """

    parallel = _run_research(parallel=True, meet_at_barrier=True)
    serial = _run_research(parallel=False, meet_at_barrier=True)

    assert parallel.probe.comparison_met is True, (
        "스위치를 켰는데 비교 갈래가 약속 지점에서 뉴스 갈래를 못 만났습니다"
    )
    assert parallel.probe.news_met is True, (
        "스위치를 켰는데 뉴스 갈래가 약속 지점에서 비교 갈래를 못 만났습니다"
    )

    assert serial.probe.comparison_met is False, (
        "스위치가 꺼졌는데 비교 갈래가 약속 지점에서 누군가를 만났습니다"
    )
    # 뒤늦게 온 뉴스 갈래는 이미 깨진 약속 지점을 본다 — 둘이 동시에 산 적이 없다.
    assert serial.probe.news_met is False
    # 약속이 깨져도 보고서 경로는 그대로다. 탐침이 실패를 만들지 않는다.
    assert parallel.result.outcome is Outcome.REPORT
    assert serial.result.outcome is Outcome.REPORT
    assert serial.probe.search_calls > 0


def test_켜고_꺼도_보고서입력과_캐시열쇠와_단계기록이_같다() -> None:
    """순서만 바뀌고 «만들어진 값»은 그대로여야 한다."""

    serial = _run_research(parallel=False)
    parallel = _run_research(parallel=True)

    assert serial.result.outcome is Outcome.REPORT
    assert parallel.result.outcome is Outcome.REPORT
    assert serial.result.report == parallel.result.report
    assert serial.result.message == parallel.result.message
    assert serial.result.final_gate_reason == parallel.result.final_gate_reason
    # 지금은 두 갈래 다 AI를 안 써서 0이지만, 어느 한쪽에 유료 호출이 생기면
    # 값이 갈리는 것을 여기서 본다.
    assert serial.result.cost_krw == parallel.result.cost_krw
    assert serial.result.charged == parallel.result.charged

    assert _composer_identity(serial) == _composer_identity(parallel)
    # 단계별 소요 시간(단계소요·소요ms)은 실행마다 당연히 달라서 값만 지우고
    # 비교한다. 항목의 존재·순서는 그대로 같아야 한다.
    assert _without_elapsed_ms(serial.steps) == _without_elapsed_ms(parallel.steps)
    assert NEWS_SEARCH_SNAPSHOT_STEP in _step_names(serial.steps)

    assert len(serial.calls.coordinates) == len(parallel.calls.coordinates) == 1
    assert (
        serial.calls.coordinates[0]["preflight_identity_digest"]
        == parallel.calls.coordinates[0]["preflight_identity_digest"]
    )
    # 옛 1층 캐시 조회는 이 배선에서 열리지 않는다. 「몇 번」을 미리 적지 않고
    # 두 실행이 같은 횟수·같은 인자로 불렀는지만 본다.
    assert serial.calls.cache_lookups == parallel.calls.cache_lookups


def test_켜고_꺼도_DART_호출과_원문_내려받기가_같다() -> None:
    """동시에 돌린다고 호출이 늘거나 같은 원문을 두 번 받으면 안 된다."""

    serial = _run_research(parallel=False)
    parallel = _run_research(parallel=True)

    assert serial.engine.get_json_calls == parallel.engine.get_json_calls
    assert serial.engine.download_receipts == parallel.engine.download_receipts
    assert serial.probe.search_calls == parallel.probe.search_calls
    assert serial.probe.comparison_calls == parallel.probe.comparison_calls == 1

    for run, label in ((serial, "차례"), (parallel, "동시")):
        assert run.probe.same_receipt_requests == SAME_RECEIPT_REQUEST_COUNT, label
        same = [
            receipt
            for receipt in run.engine.download_receipts
            if receipt == PROBE_RECEIPT_NUMBER
        ]
        assert len(same) == 1, (
            f"{label}: 같은 접수번호를 {len(same)}번 내려받았습니다"
        )


def test_갈래_스레드에_유료예산_문맥이_복사된다() -> None:
    """`copy_context`를 빼면 두 갈래 모두 문맥을 잃고 이 시험이 깨진다."""

    parallel = _run_research(parallel=True)

    assert parallel.probe.comparison_budget_visible is True
    assert parallel.probe.news_budget_visible is True
    # 뉴스 갈래는 문맥을 잃으면 예산 조회에서 막혀 검색 스냅샷 단계를 못 남긴다.
    assert NEWS_SEARCH_SNAPSHOT_STEP in _step_names(parallel.steps)


def test_비교가_막히면_켜든_꺼든_같은_사유로_멈추고_뉴스단계가_없다() -> None:
    """실패 의미는 스위치와 무관하다. 다만 켜면 뉴스 검색 호출이 이미 나간다."""

    blocked = ComparisonBlockedError("시험용 비교 차단")
    serial = _run_research(parallel=False, comparison_error=blocked)
    parallel = _run_research(parallel=True, comparison_error=blocked)

    for run, label in ((serial, "차례"), (parallel, "동시")):
        assert run.result.outcome is Outcome.GATE_STOPPED, label
        assert (
            run.result.final_gate_reason
            == FINAL_GATE_REASON_OFFICIAL_EVIDENCE_INSUFFICIENT
        ), label
        assert run.calls.composers == [], label

    assert serial.result.message == parallel.result.message

    for run, label in ((serial, "차례"), (parallel, "동시")):
        recorded = _step_names(run.steps)
        assert recorded.count(COMPARISON_BLOCKED_STEP) == 1, label
        assert not [
            name for name in recorded if name.startswith(NEWS_STEP_PREFIX)
        ], f"{label}: 비교가 막혔는데 뉴스 단계가 남았습니다"

    # 이 스위치가 만드는 유일한 외부 호출 증가 지점 — 켜면 되돌릴 수 없다.
    assert serial.probe.search_calls == 0
    assert parallel.probe.search_calls >= 1


def test_비교지문을_못만들면_켜든_꺼든_내부계약_사유로_멈춘다() -> None:
    """지문 접기가 터져도 예전처럼 fail-closed 관문으로 끝나야 한다.

    예전 코드는 비교 생산과 지문 접기가 같은 `try` 안이라 직렬화 오류도
    「내부 근거 계약」 GATE_STOPPED가 됐다. 갈래로 나누면서 접기를 합류 지점으로
    빼면 그 덮개가 사라져 처리되지 않은 실패로 바뀐다 — 그 회귀를 막는다.
    """

    serial = _run_research(
        parallel=False, comparison_result=_UnserializableComparison()
    )
    parallel = _run_research(
        parallel=True, comparison_result=_UnserializableComparison()
    )

    for run, label in ((serial, "차례"), (parallel, "동시")):
        assert run.result.outcome is Outcome.GATE_STOPPED, label
        assert (
            run.result.final_gate_reason
            == FINAL_GATE_REASON_INTERNAL_EVIDENCE_CONTRACT
        ), label
        assert run.calls.composers == [], label
        recorded = _step_names(run.steps)
        assert recorded.count(COMPARISON_TRANSPORT_BLOCKED_STEP) == 1, label

    assert serial.result.message == parallel.result.message
    # 비교 생산기는 정상적으로 끝났다 — 멈춘 것은 지문 접기다.
    assert serial.probe.comparison_calls == parallel.probe.comparison_calls == 1


def test_갈래_안에서_남긴_단계가_완료순서가_아니라_정해진_차례로_놓인다() -> None:
    """뉴스가 먼저 기록해도 공용 목록에는 비교가 먼저 와야 한다.

    탐침은 «운영 방식»으로 `current_steps()`에 남긴다. 갈래마다 자기 목록을 쓰지
    않으면 이 두 줄이 완료 순서 그대로 공용 목록에 박혀 순서가 뒤집힌다.

    ★ 이 시험이 결정적으로 지키는 것은 «뉴스 갈래»의 갈래별 목록이다. 비교 갈래
      쪽은 합류가 어차피 비교를 먼저 붙이므로 순서로는 구분되지 않는다(방어용).
    """

    run = _run_research(parallel=True, record_branch_steps=True)

    assert run.result.outcome is Outcome.REPORT
    # 완료 순서는 실제로 뒤집혀 있었다 — 비교가 뉴스 기록을 기다린 뒤 남겼다.
    assert run.probe.comparison_waited_for_news_step is True

    recorded = _step_names(run.steps)
    assert recorded.count(COMPARISON_PROBE_STEP) == 1
    assert recorded.count(NEWS_PROBE_STEP) == 1
    assert recorded.index(COMPARISON_PROBE_STEP) < recorded.index(NEWS_PROBE_STEP), (
        "갈래가 남긴 단계가 완료 순서대로 놓였습니다 — 갈래별 목록이 풀렸습니다"
    )
    # 뉴스 갈래 탐침은 검색 스냅샷 단계와 «같은 묶음»으로 옮겨져야 한다.
    assert recorded.index(NEWS_PROBE_STEP) < recorded.index(NEWS_SEARCH_SNAPSHOT_STEP)
