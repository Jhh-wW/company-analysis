"""조사를 «통째로» 멈춰야 하는 사유가 장 하나의 실패로 삼켜지지 않는다.

초대 링크 중단·생성 lease 상실·대기 취소는 이 요청 전체가 더 갈 수 없는 상태다.
그런데 v2 작성 경로는 이 예외를 「이 장을 못 썼다」로 삼켜, 조사가 남은 장과 확인
단계를 끝까지 돌고 사유가 「품질 미달」로 뒤바뀌었다. 이 시험이 그 뒤바뀜을 막는다.

★ 실제 AI·네트워크는 한 번도 부르지 않는다. provider 자리에는 가짜 응답기를 두고,
  그 앞의 계량 경계·포트 래퍼·작성기 삼킴 지점은 «운영 코드 그대로» 지난다.
★ 마지막 시험은 실제 웹 실행기에서 링크를 진짜로 닫아, 이력 사유가 링크 중단으로
  남는지까지 본다 — 여기까지 닿아야 관리자가 원인을 알 수 있다.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import json
import re
import time
from collections.abc import Iterator
from types import SimpleNamespace
from typing import Any, Optional

import pytest
from fastapi.testclient import TestClient

from src.core.provider_gateway import attempt_context
from src.core.provider_gateway.attempt_context import ProviderAttemptCallbacks
from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.budget import provider_budget
from src.features.composer import logic as composer_logic
from src.features.composer import verify as composer_verify
from src.features.composer.constants import GRADE_CONFIRMED, SECTION_IDS
from src.features.composer.port import AskFatalError
from src.features.company_comparison.tests.test_logic import _v2_comparison_result
from src.features.pipeline import real
from src.features.pipeline.official_evidence_transport_adapter import (
    merge_official_evidence_fragments,
)
from src.features.pipeline.port import (
    CompanyCard,
    CompanyLookupResult,
    Outcome,
    RunResult,
    UserInput,
)
from src.features.pipeline.tests.test_real_cache import FakeEngine
from src.features.pipeline.tests.test_full_evidence_end_to_end import (
    _official_evidence,
)
from src.features.sharelink import store as share_store
from src.features.sharelink.constants import KEY_COOKIE_NAME
from src.features.storage import db as storage_db
from src.shared import engine_build_identity as build_identity_contract
from src.shared import generation_coordination
from src.shared.report_claim_policy import CLAIM_SLOTS_BY_SECTION
from src.shared.report_evidence.constants import ReleaseMode
from src.web import generation_singleflight, job_runtime, main, runtime

_LINK = "b7c8d9e0f1a23456b7c8d9e0f1a23456"
_CORP_ID = "00123456"
_FORM = {
    "company": "가나다전자",
    "job": "영업",
    "region": "서울",
    "posting_text": "채용 공고 원문",
}
_식별비용 = 10.0
_기준일 = dt.date(2026, 8, 24)
#: 가짜 본조사가 쓰려는 장 수. 첫 장 뒤에 링크가 닫히므로 1장에서 멈춰야 한다.
_장수 = 3


# ══════════════════════════════════════════════════════════
# 공용 — 가짜 provider와 유료 문맥
# ══════════════════════════════════════════════════════════


@pytest.fixture(autouse=True)
def _유료_문맥(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """직접 부르는 시험도 웹 worker와 같은 예산·시도 원장 문맥에서 돈다."""

    callbacks = ProviderAttemptCallbacks(
        lambda _provider, _operation, _reserved: object(),
        lambda _token: None,
        lambda _token: None,
        lambda _token, _observation: None,
    )
    with provider_budget.activate(100_000.0), attempt_context.activate(callbacks):
        yield


def _build_identity() -> build_identity_contract.EngineBuildIdentity:
    return build_identity_contract.process_engine_build_identity()


def _v2_모드() -> real.engine_mode.EngineMode:
    """직접 분기를 부르는 시험도 process 시작 계약을 명시적으로 연다."""

    return real.engine_mode.freeze_process_engine_mode(real.engine_mode.EngineMode.V2)


def _callbacks(ensure_paid_phase) -> generation_coordination.GenerationCallbacks:
    """웹 실행기가 유료 요청 하나에 설치하는 것과 같은 모양의 조정 callback."""

    return generation_coordination.GenerationCallbacks(
        coordinate=lambda *_args: None,
        ensure_paid_phase=ensure_paid_phase,
        engine_build_identity=_build_identity(),
    )


def _n번째_호출에서_멈춘다(멈출_호출: int, 중단):
    """유료 단계 진입 훅. 지정한 호출부터 중단 사유를 던진다."""

    센다 = {"횟수": 0}

    def 훅() -> None:
        센다["횟수"] += 1
        if 센다["횟수"] >= 멈출_호출:
            raise 중단()

    return 훅


def _링크중단() -> job_runtime.LinkAccessClosedDuringRun:
    return job_runtime.LinkAccessClosedDuringRun(
        job_runtime.LINK_STOP_REASON_REVOKED
    )


def _lease상실() -> generation_singleflight.GenerationSingleflightUnavailable:
    return generation_singleflight.GenerationSingleflightUnavailable(
        "보고서 생성 소유권을 잃어 provider를 호출하지 않습니다"
    )


def _계량_경계(엔진: FakeEngine) -> tuple[real._MeteredEngine, Any]:
    """운영과 같은 계량 client 경계를 만든다 (provider만 가짜)."""

    engine = real._MeteredEngine(엔진)
    return engine, real._metered_client(engine, 엔진._client())


# ══════════════════════════════════════════════════════════
# ① 포트 래퍼 — 요청 전역 중단은 문장 실패로 위장되지 않는다
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize(
    ("이름", "중단"),
    [("초대 링크 중단", _링크중단), ("생성 lease 상실", _lease상실)],
)
def test_요청_전역_중단은_작성기_삼킴_지점을_뚫고_나간다(이름: str, 중단) -> None:
    """작성·확인 단계의 「한 장 실패는 삼킨다」 규칙이 이 사유에는 적용되면 안 된다."""

    del 이름
    가짜엔진 = FakeEngine()
    engine, client = _계량_경계(가짜엔진)
    ask = real._v2_ask_via_provider(
        engine, client, stage="v2_compose", max_tokens=real.V2_WRITER_MAX_TOKENS
    )

    with generation_coordination.activate(
        _callbacks(_n번째_호출에서_멈춘다(1, 중단))
    ):
        # 포트 래퍼: 요청 전역 장애로 감싸 던진다 (호출 «횟수» 상한이 아니다).
        with pytest.raises(AskFatalError) as 잡힘:
            ask("프롬프트 본문")
        assert isinstance(잡힘.value.cause, 중단().__class__)
        assert 잡힘.value.call_limit is False

        # 작성·확인 두 삼킴 지점을 그대로 지난다 — 둘 다 재전파해야 한다.
        with pytest.raises(AskFatalError):
            composer_logic._ask_and_parse(ask, "프롬프트 본문", "identity")
        with pytest.raises(AskFatalError):
            composer_verify._safe_ask(ask, "프롬프트 본문")

    # 훅이 provider «앞»에서 막았으므로 실제로 나간 호출은 하나도 없다.
    assert 가짜엔진.client.messages.calls == 0


# ══════════════════════════════════════════════════════════
# ② 실제 v2 작성 경로 — 두 번째 호출에서 멈추고 남은 장을 더 쓰지 않는다
# ══════════════════════════════════════════════════════════


def _full_생산입력(
    engine: FakeEngine,
) -> tuple[dict[int, dict[str, object]], dict[str, Any], dict[str, Any]]:
    """중단 시험도 FULL의 공식문서·재무 생산 계약을 실제로 통과한다."""

    fragments, added = merge_official_evidence_fragments({}, _official_evidence())
    assert added == 9
    counter = object()
    financials, _years = engine.fetch_financials(
        _CORP_ID,
        counter,
        business_date=_기준일,
    )
    filing = engine.latest_report_rcept(
        _CORP_ID,
        "상장사",
        counter,
        business_date=_기준일,
    )
    produced = engine.make_fragments("", financials)
    financial_fragment = next(
        dict(fragment)
        for fragment in produced.values()
        if fragment.get("종류") == "재무"
        and str(fragment.get("원문") or "").startswith("주요계정(DART API):")
    )
    fragments[max(fragments) + 1] = financial_fragment
    return fragments, financials, filing


def _작가_응답(장번호: int) -> str:
    """한 장을 채우는 작가 응답 하나 — 내용보다 «형식»만 맞으면 된다."""

    표식 = "가나다라마바사아자"
    section_id = SECTION_IDS[장번호]
    slots = CLAIM_SLOTS_BY_SECTION[section_id]
    끝맺음 = ("첫째", "둘째", "셋째", "넷째", "다섯째")
    return json.dumps(
        {
            "문장들": [
                {
                    "글": (
                        f"{표식[장번호]} 회사 사업 고객 제품 전략 운영 문화 경쟁 "
                        f"과제 대응 협력 실적 {끝} 공식 자료에서 확인했다."
                    ),
                    "인용": [str(장번호 + 1)],
                    "등급": GRADE_CONFIRMED,
                    "주장슬롯": slots[순서 % len(slots)],
                }
                for 순서, 끝 in enumerate(끝맺음)
            ]
        },
        ensure_ascii=False,
    )


class _작가_응답기:
    """provider 자리에 앉는 가짜 응답기. 보낸 횟수를 센다."""

    def __init__(self) -> None:
        self.보낸_횟수 = 0

    def create(self, **kwargs: Any) -> SimpleNamespace:
        self.보낸_횟수 += 1
        return SimpleNamespace(
            model=kwargs.get("model", "가짜모델"),
            usage=SimpleNamespace(input_tokens=10, output_tokens=5),
            content=[SimpleNamespace(text=_작가_응답(self.보낸_횟수 - 1))],
        )


@pytest.mark.parametrize(
    ("이름", "중단", "사유이름"),
    [
        ("초대 링크 중단", _링크중단, "LinkAccessClosedDuringRun"),
        ("생성 lease 상실", _lease상실, "GenerationSingleflightUnavailable"),
    ],
)
def test_v2_작성은_두번째_호출의_중단에서_즉시_멈춘다(
    monkeypatch: pytest.MonkeyPatch, 이름: str, 중단, 사유이름: str
) -> None:
    """운영 v2 분기 전체를 가짜 provider로 합성해 「어디서 멈추는가」를 잰다."""

    del 이름
    monkeypatch.setenv(real.REPORT_RELEASE_MODE_ENV_NAME, ReleaseMode.FULL.value)
    monkeypatch.setattr(real, "_v2_cache_save", lambda **_kwargs: None)

    가짜엔진 = FakeEngine()
    응답기 = _작가_응답기()
    가짜엔진.client.messages = 응답기
    engine, client = _계량_경계(가짜엔진)
    단계: list[dict[str, Any]] = []
    fragments, financials, filing = _full_생산입력(가짜엔진)

    with generation_coordination.activate(
        _callbacks(_n번째_호출에서_멈춘다(2, 중단))
    ):
        with pytest.raises(Exception) as 잡힘:  # noqa: PT011 - 타입은 아래에서 본다
            real._run_v2_composer(
                engine=engine,
                client=client,
                company_name="가나다전자",
                corp_type="상장사",
                frags=fragments,
                financials=financials,
                filing=filing,
                revenue_tables=[],
                sources=[],
                business_date=_기준일,
                model="가짜모델",
                steps=단계,
                corp_id=_CORP_ID,
                current_fiscal_year=2025,
                source_identity_digest="a" * 64,
                build_identity=_build_identity(),
                generation_mode=_v2_모드(),
                comparison_result=_v2_comparison_result(),
            )

    # 중단 사유가 그대로 밖으로 나온다 — 「출고 검증 실패」로 바뀌지 않는다.
    assert type(잡힘.value).__name__ == 사유이름
    # 첫 장만 실제로 나갔고, 멈춘 뒤 새 provider 호출은 없다.
    assert 응답기.보낸_횟수 == 1
    assert [항목.get("step") for 항목 in 단계] == []


# ══════════════════════════════════════════════════════════
# ③ 본조사 바깥 경계 — 중단을 결과로 뭉개지 않고 쓴 값만 실어 보낸다
# ══════════════════════════════════════════════════════════


def _조사_사용자입력() -> UserInput:
    return UserInput(
        company="가나다전자", job="영업", region="서울", posting_text="채용 공고 원문"
    )


def _조사_카드() -> CompanyCard:
    return CompanyCard(
        legal_name="가나다전자",
        typed_name="가나다전자",
        address="서울특별시 강남구 테헤란로 1",
        ceo="홍길동",
        founded="20000101",
        ref=_CORP_ID,
    )


def test_본조사는_요청_전역_중단을_실패_결과로_뭉개지_않는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """여기서 결과로 바꿔 버리면 실행기의 전용 중단 분기에 영영 닿지 못한다."""

    가짜엔진 = FakeEngine()
    monkeypatch.setattr(real, "_engine", lambda: 가짜엔진)

    def 한_장_쓰고_중단한다(_self: Any, *_args: Any, **kwargs: Any) -> RunResult:
        # 실제 v2 경로가 그러듯 계량 경계를 지나 AI를 «이미 한 번» 썼다.
        engine = kwargs["engine"]
        ask = real._v2_ask_via_provider(
            engine,
            real._metered_client(engine, 가짜엔진._client()),
            stage="v2_compose",
            max_tokens=real.V2_WRITER_MAX_TOKENS,
        )
        ask("프롬프트 본문")
        raise _링크중단()

    monkeypatch.setattr(real.RealPipeline, "_run_metered", 한_장_쓰고_중단한다)

    with generation_coordination.activate(_callbacks(lambda: None)):
        with pytest.raises(job_runtime.LinkAccessClosedDuringRun) as 잡힘:
            real.RealPipeline().run(_조사_사용자입력(), _조사_카드())

    # 이미 나간 AI 사용 기록은 중단이라고 통째로 사라지면 안 된다.
    쓴값 = getattr(잡힘.value, real.STOPPED_RUN_USAGE_ATTR)
    assert 쓴값.model == "가짜모델"
    assert len(쓴값.ai_cost_events) == 1
    assert 쓴값.ai_cost_events[0].stage == "v2_compose"


# ══════════════════════════════════════════════════════════
# ④ 실제 웹 실행기 — 조사 도중 링크를 닫으면 이력 사유가 링크 중단이다
# ══════════════════════════════════════════════════════════


class 링크중단_v2조사(real.RealPipeline):
    """운영 v2 작성 경로의 «삼킴 지점»만 그대로 지나는 무과금 본조사.

    실제로 부르는 것: 계량 client 경계 → 포트 래퍼(`_v2_ask_via_provider`) →
    작성기 삼킴 지점(`composer.logic._ask_and_parse`). provider 자리에만 가짜
    응답기를 둔다. 수집·전자공시는 이 시험의 관심 밖이라 부르지 않는다.
    """

    def __init__(self) -> None:
        self.가짜엔진 = FakeEngine()
        self.응답기 = _작가_응답기()
        self.가짜엔진.client.messages = self.응답기
        #: 작성기가 «끝까지 돈» 장 수. 멈춰야 하므로 1이어야 한다.
        self.작성한_장 = 0

    def search_business_candidates(self, **_kwargs: Any) -> list[dict[str, object]]:
        return []

    def find_company_metered(self, user_input: UserInput) -> CompanyLookupResult:
        return CompanyLookupResult(
            card=CompanyCard(
                legal_name=user_input.company,
                typed_name=user_input.company,
                address="서울",
                ceo="대표",
                founded="20200101",
                ref=_CORP_ID,
            ),
            cost_krw=_식별비용,
            model="lookup-model",
        )

    def _run_metered(
        self,
        user_input: UserInput,
        card: CompanyCard,
        on_step: Optional[Any],
        *,
        engine: Any,
        build_identity: Any,
        generation_mode: Any,
    ) -> RunResult:
        del user_input, card, build_identity, generation_mode
        # 부분 지문이면 single-flight를 우회한다 — lease 없이 유료 단계만 연다.
        generation_coordination.coordinate(
            corp_id="", cache_namespace=None, preflight_identity_digest=""
        )
        client = real._metered_client(engine, self.가짜엔진._client())
        ask = real._v2_ask_via_provider(
            engine,
            client,
            stage="v2_compose",
            max_tokens=real.V2_WRITER_MAX_TOKENS,
        )
        try:
            for _ in range(_장수):
                composer_logic._ask_and_parse(ask, "프롬프트 본문", "identity")
                self.작성한_장 += 1
                if on_step is not None:
                    on_step("generate")
        except AskFatalError as error:
            # 운영 v2 분기(`real._run_v2_composer`)와 같은 자리에서 원인을 푼다.
            raise error.cause from error
        # 삼켜졌을 때 실제로 나오던 결과 — 이 문구가 남으면 사유가 뒤바뀐 것이다.
        return RunResult(
            outcome=Outcome.GATE_STOPPED,
            message="엔진 v2 출고 검증을 통과하지 못해 보고서를 내보내지 않았습니다.",
        )


def _csrf설치(client: TestClient) -> None:
    """분석 폼 CSRF 입구를 정상 폼처럼 지난다."""

    원본_post = client.post

    def csrf붙인_post(url, *args, **kwargs):
        if url in {"/confirm", "/run"}:
            data = dict(kwargs.pop("data", {}) or {})
            비밀 = client.cookies.get(KEY_COOKIE_NAME) or client.cookies.get(
                auth_constants.SESSION_COOKIE_NAME
            ) or ""
            if 비밀:
                data.setdefault("csrf_token", auth_logic.csrf_token_for_session(비밀))
            kwargs["data"] = data
        return 원본_post(url, *args, **kwargs)

    client.post = csrf붙인_post


@contextlib.contextmanager
def _링크손님() -> Iterator[TestClient]:
    """살아 있는 초대 링크로 들어온 손님 하나."""

    with TestClient(main.app) as client:
        with storage_db.connect() as conn:
            assert (
                share_store.insert_new(
                    conn,
                    key=_LINK,
                    company="가나다전자",
                    job="영업",
                    now_iso="2026-08-17T10:00:00",
                )
                is True
            )
        client.cookies.set(KEY_COOKIE_NAME, _LINK)
        _csrf설치(client)
        yield client


def _조사를_돌린다(client: TestClient) -> str:
    """회사 확인 → 본조사 시작 → 끝날 때까지 기다린 뒤 분석 ID를 준다."""

    확인 = client.post("/confirm", data=_FORM, follow_redirects=False)
    assert 확인.status_code == 200, 확인.text
    표 = re.search(r'name="paid_attempt_token" value="([^"]+)"', 확인.text)
    assert 표 is not None, "확인 화면에서 일회용 토큰을 찾지 못했습니다"
    시작 = client.post(
        "/run",
        data={**_FORM, "paid_attempt_token": 표.group(1), "posting_image_consent": "yes"},
        follow_redirects=False,
    )
    assert 시작.status_code == 303, 시작.text
    job_id = 시작.headers["location"].rsplit("/", 1)[-1]
    for _ in range(1000):
        job = job_runtime._JOBS.get(job_id)
        if job is not None and job.finished:
            return job_id
        time.sleep(0.01)
    raise AssertionError("가짜 본조사가 끝나지 않았습니다")


def test_조사_도중_링크를_닫으면_이력_사유가_링크_중단으로_남는다(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """관리자가 방금 닫은 링크가 원인인데 「품질 미달」로 찍히면 안 된다."""

    조사 = 링크중단_v2조사()
    monkeypatch.setattr(runtime, "_PIPELINE", 조사)

    원본_create = 조사.응답기.create

    def 첫_응답_뒤_링크를_닫는다(**kwargs: Any) -> SimpleNamespace:
        응답 = 원본_create(**kwargs)
        with storage_db.connect() as conn:
            assert share_store.delete(conn, _LINK) is True
        return 응답

    조사.응답기.create = 첫_응답_뒤_링크를_닫는다

    with _링크손님() as client:
        job_id = _조사를_돌린다(client)

    결과 = job_runtime._JOBS[job_id].result
    # ① 조사가 즉시 멈춘다 — 남은 장을 더 쓰지 않는다.
    assert 조사.작성한_장 == 1, "링크가 닫혔는데 남은 장을 계속 썼다"
    assert 조사.응답기.보낸_횟수 == 1, "멈춘 뒤에 새 AI 호출이 나갔다"
    # ② 사용자 문구가 링크 중단 안내다 — 「품질 미달」이 아니다.
    assert 결과.outcome is Outcome.FAILED
    assert 결과.message == job_runtime.LINK_REVOKED_RUN_STOPPED_MESSAGE
    # ③ 관리자 이력 사유가 링크 중단이다.
    with storage_db.connect() as conn:
        이력 = share_store.load_run(conn, job_id)
    assert 이력.stop_reason == job_runtime.LINK_STOP_REASON_REVOKED
    # ④ 사용자 차감은 0이지만, 이미 나간 AI 사용 기록은 남는다.
    assert 결과.charged is False
    # 식별 모델 뒤에 본조사 모델이 하나 더 붙어 있어야 한다 — 붙지 않았다면
    # 이미 나간 AI 호출 기록이 중단 때문에 통째로 사라졌다는 뜻이다.
    assert 결과.model.startswith("lookup-model")
    assert 결과.model != "lookup-model", "중단 때문에 이미 쓴 AI 기록이 사라졌다"
    assert len(결과.ai_cost_events) == 1
