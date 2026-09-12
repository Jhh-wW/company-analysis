"""단계 전환마다 남기는 소요 시간(ms) 진단이 실제 배선을 타는지 본다.

★ 이 시험이 지키는 것 — steps에 값을 넣는 계산이 아무리 옳아도, `tell()`이
  그것을 부르지 않거나 `run()`의 finally가 마지막 단계를 닫지 않으면
  관리자 화면에는 아무것도 안 남는다. 그래서 stub이 아니라 «진짜»
  `_run_metered`를 05 판정 단계까지 실제로 지나가며 검사한다 — 시험 안에서
  따로 만든 목록이 아니라 `run_diagnostics.capture()`로 받은 운영 배선의
  결과를 그대로 본다.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.core import deployment_identity
from src.features.observability import run_diagnostics
from src.features.pipeline import constants as pipeline_constants
from src.features.pipeline import real
from src.features.pipeline.port import CompanyCard, Outcome, UserInput
from src.shared import engine_build_identity as build_identity_contract
from src.shared.stage_elapsed_constants import STAGE_ELAPSED_MS_KEY, STAGE_ELAPSED_STEP


@pytest.fixture(autouse=True)
def _verified_process_build(monkeypatch: pytest.MonkeyPatch) -> None:
    """RealPipeline 직접 시험도 정상 process build 영수증을 명시적으로 연다."""

    for name in deployment_identity.COMMIT_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RENDER_GIT_COMMIT", "c" * 40)
    build_identity_contract.freeze_process_engine_build_identity()


def _card(ref: str = "00126380") -> CompanyCard:
    return CompanyCard(
        legal_name="회사",
        typed_name="회사",
        address="서울",
        ceo="대표",
        founded="20200101",
        ref=ref,
    )


def _user_input() -> UserInput:
    return UserInput(company="회사", job="직무", region="서울")


def _elapsed_entries(steps: list) -> list[dict]:
    return [
        item
        for item in steps
        if isinstance(item, dict) and item.get("step") == STAGE_ELAPSED_STEP
    ]


def test_같은_키로_연속_호출하면_전환이_아니라_기록하지_않는다():
    """같은 화면 단계로 다시 불리는 것은 전환이 아니다.

    ★ 왜 필요한가 — `tell("output")`은 재사용 종료·1층 캐시 종료·최종 종료
      갈래에서 각각 불릴 수 있다. 매번 구간을 닫으면 아주 짧은 구간과 진짜
      구간이 섞여 실제보다 훨씬 크게 보인다.
    """

    engine = real._MeteredEngine(SimpleNamespace(MODEL="claude-haiku-4-5"))
    steps: list[dict] = []

    engine.stage_elapsed_mark("judge", steps=steps)
    assert steps == []  # 첫 호출은 닫을 직전 구간이 없다
    engine.stage_elapsed_mark("judge", steps=steps)
    assert steps == []  # 같은 키라 전환이 아니다 — 여전히 아무것도 안 쌓인다

    engine.stage_elapsed_mark("collect", steps=steps)
    assert len(steps) == 1
    entry = steps[0]
    assert entry["step"] == STAGE_ELAPSED_STEP
    assert entry["단계"] == "judge"
    ms = entry[STAGE_ELAPSED_MS_KEY]
    assert type(ms) is int and ms >= 0


class _JudgeRejectEngine:
    """식별(01)까지는 성공하고 판정(05)에서 즉시 거부되는 최소 가짜 엔진.

    AI를 0회 부르는 판정 갈래만 지나므로 provider 응답을 흉내 낼 필요가 없다.
    """

    MODEL = "claude-haiku-4-5"
    PUBLIC_ORG_REGISTRY = "registry-path"

    class UsageCounter:
        pass

    def _client(self):
        return SimpleNamespace(messages=SimpleNamespace(create=lambda **_: None))

    def load_env(self) -> None:
        return None

    def get_json(self, name, _params, _counter):
        if name == "company.json":
            return {
                "status": "000",
                "corp_name": "회사",
                "corp_cls": "Y",
                "bizr_no": "123-45-67890",
                "hm_url": "",
            }
        if name == "list.json":
            # 013 = 조회 범위에 감사보고서 자료 없음(정상 빈 결과).
            return {"status": "013", "list": None}
        raise AssertionError(f"이 시험 경로에서는 예상하지 못한 DART 호출입니다: {name}")

    def load_public_org_registry(self, _path):
        return []

    def fetch_financials(self, _corp_code, _counter, *, business_date):
        del business_date
        return {}, []

    def decide(self, _corp_cls, _has_audit, _bizr_no, _match_fn, *, has_financial_statements):
        del has_financial_statements
        return SimpleNamespace(status="거부A", corp_type="비상장 외감")


def test_실제_본조사가_시동부터_두_단계를_지나면_단계소요를_순서대로_남긴다(
    monkeypatch: pytest.MonkeyPatch,
):
    """`run()` 진입부터 첫 `tell()`까지(1판 모듈 import)도 「시동」으로 잡힌다.

    ★ 왜 필요한가 — `_engine()` 호출은 냉시동에서 수 초가 걸리는데
      `engine = _MeteredEngine(_engine())` «뒤»에 시계를 시작하면 이 구간이
      어느 단계 진단에도 안 잡혔다(격리 실측). `run()`이 `engine` 생성 전에
      잡은 시각으로 시계를 미리 채워 두면 첫 `tell("identify")`가 그 구간을
      평범한 전환으로 닫는다.
    """

    monkeypatch.setattr(real, "_engine", lambda: _JudgeRejectEngine())

    with run_diagnostics.capture() as captured:
        result = real.RealPipeline().run(_user_input(), _card())

    assert result.outcome is Outcome.REJECT_PUBLIC
    elapsed = _elapsed_entries(captured.steps)
    assert [item["단계"] for item in elapsed] == [
        pipeline_constants.STAGE_BOOT,
        "identify",
        "judge",
    ]
    for item in elapsed:
        ms = item[STAGE_ELAPSED_MS_KEY]
        assert type(ms) is int and ms >= 0


class _BrokenProfileEngine:
    """식별 직후 DART 기업개황이 비정상 상태를 돌려줘 예외로 끝나는 가짜 엔진."""

    MODEL = "claude-haiku-4-5"

    class UsageCounter:
        pass

    def _client(self):
        return SimpleNamespace(messages=SimpleNamespace(create=lambda **_: None))

    def load_env(self) -> None:
        return None

    def get_json(self, _name, _params, _counter):
        return {"status": "999", "message": "DART 오류"}


def test_예외로_끝나도_마지막_단계_소요시간이_기록된다(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(real, "_engine", lambda: _BrokenProfileEngine())

    with run_diagnostics.capture() as captured:
        result = real.RealPipeline().run(_user_input(), _card())

    assert result.outcome is Outcome.FAILED
    elapsed = _elapsed_entries(captured.steps)
    # 예외는 tell("identify") 다음(tell("judge") 전)에서 나므로, 시동→식별
    # 두 구간만 닫히고 마지막(식별)은 run()의 finally가 닫는다.
    assert [item["단계"] for item in elapsed] == [pipeline_constants.STAGE_BOOT, "identify"]
    for item in elapsed:
        ms = item[STAGE_ELAPSED_MS_KEY]
        assert type(ms) is int and ms >= 0
