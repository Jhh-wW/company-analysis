"""DART-local 후보 → 명시 Google fallback → 사용자 선택 → DART 재검증."""

from __future__ import annotations

import asyncio
import re
import threading
import time
import unicodedata

import httpx
import pytest
from fastapi.responses import PlainTextResponse
from fastapi.testclient import TestClient

from src.features.auth import constants as auth_constants
from src.features.auth import logic as auth_logic
from src.features.budget import logic as budget_logic
from src.features.budget import spend_store
from src.features.budget.constants import SPEND_PHASE_CANDIDATE, SPEND_PHASE_IDENTIFY
from src.features.business_candidate.constants import CANDIDATE_ATTEMPT_TTL_SEC
from src.features.business_candidate.logic import (
    CandidateResolution,
    ProviderRateLimited,
    ProviderTimedOut,
    RawBusinessCandidate,
    ResolutionStatus,
)
from src.features.admin_dashboard import store as dashboard_store
from src.features.pipeline.demo import DemoPipeline
from src.features.pipeline.port import (
    CompanyCard,
    CompanyLookupResult,
    Outcome,
    RunResult,
    UserInput,
)
from src.features.provider_health import constants as provider_health_constants
from src.features.provider_health import store as provider_health_store
from src.features.sharelink import store as share_store
from src.features.sharelink.constants import KEY_COOKIE_NAME
from src.features.storage import db as storage_db
from src.web import (
    evaluation_mode,
    job_runtime,
    main,
    paid_runtime,
    public_ids,
    request_helpers,
    runtime,
)
from src.web.routers import analysis as analysis_router


@pytest.fixture(autouse=True)
def _fresh_candidate_state(monkeypatch):
    from src.features.business_candidate import logic as candidate_logic

    monkeypatch.setattr(candidate_logic, "_RATE_HISTORY", budget_logic.RateHistory())
    job_runtime._CANDIDATE_ATTEMPTS.clear()
    job_runtime._CANDIDATE_SEARCH_GRANTS.clear()
    yield
    job_runtime._CANDIDATE_ATTEMPTS.clear()
    job_runtime._CANDIDATE_SEARCH_GRANTS.clear()


class CandidateAwareFakeRealPipeline:
    business_candidate_provider_costs_money = False

    def __init__(self, *, local_candidates: bool = True, candidate_refs: bool = True):
        self.local_candidates = local_candidates
        self.candidate_refs = candidate_refs
        self.search_calls = 0
        self.lookup_calls = 0
        self.lookup_inputs: list[str] = []
        self.lookup_refs: list[str] = []

    def search_business_candidates(self, **kwargs):
        self.search_calls += 1
        assert kwargs["company"] == "JYP"
        assert kwargs["address_hint"] == "서울 강동구"
        if not self.local_candidates:
            return []
        return [
            RawBusinessCandidate(
                candidate_name="(주)제이와이피엔터테인먼트",
                address="서울특별시 강동구 강동대로 205 (성내동, JYP Center)",
                homepage="https://www.jype.com/",
                source_label="전자공시(DART) 기업개황 fixture",
                source_url="https://opendart.fss.or.kr/",
                provider_name="DART",
                candidate_ref="00258689" if self.candidate_refs else "",
                stock_code="035900" if self.candidate_refs else "",
                modify_date="20221206" if self.candidate_refs else "",
            )
        ]

    def find_company_by_ref_metered(
        self, user_input: UserInput, candidate_ref: str
    ) -> CompanyLookupResult:
        self.lookup_calls += 1
        self.lookup_inputs.append(user_input.company)
        self.lookup_refs.append(candidate_ref)
        if candidate_ref != "00258689":
            return CompanyLookupResult(card=None, failed=True)
        return CompanyLookupResult(
            card=CompanyCard(
                legal_name="JYP Ent.",
                typed_name=user_input.company,
                address="서울특별시 강동구 강동대로 205",
                ceo="정욱",
                founded="19970425",
                homepage="jype.com",
                homepage_url="https://www.jype.com/",
                ref=candidate_ref,
            ),
            model="fake-dart-ref",
        )

    def find_company_metered(self, user_input: UserInput) -> CompanyLookupResult:
        self.lookup_calls += 1
        self.lookup_inputs.append(user_input.company)
        if user_input.company not in {
            "(주)제이와이피엔터테인먼트",
            "JYP 엔터테인먼트",
        }:
            return CompanyLookupResult(card=None, model="fake-dart")
        return CompanyLookupResult(
            card=CompanyCard(
                legal_name="(주)제이와이피엔터테인먼트",
                typed_name=user_input.company,
                address="서울특별시 강동구 강동대로 205",
                ceo="정욱",
                founded="19970425",
                homepage="jype.com",
                homepage_url="https://www.jype.com/",
                ref="fake-dart-jyp-001",
            ),
            cost_krw=7.0,
            model="fake-dart",
        )


class LinkMultiCompanyFakeRealPipeline:
    """실제 provider 없이 LINK의 후보 선택→생성 이력만 끝까지 재현한다."""

    business_candidate_provider_costs_money = False

    _COMPANIES = {
        "네이버": ("NAVER", "00266961", "035420"),
        "YG": ("와이지엔터테인먼트", "00613318", "122870"),
    }

    def __init__(self) -> None:
        self.search_inputs: list[str] = []
        self.exact_inputs: list[str] = []
        self.lookup_refs: list[str] = []
        self.run_inputs: list[tuple[str, str]] = []

    def search_business_candidates(self, **kwargs):
        input_name = kwargs["company"]
        self.search_inputs.append(input_name)
        # 이 통합 시험은 LINK 권한·이력만 본다. 로컬 후보는 정상 0건으로 두고
        # 아래 무과금 exact fixture가 공인 DART ID를 가진 확인 카드를 돌려준다.
        return []

    def find_company_by_ref_metered(
        self, user_input: UserInput, candidate_ref: str
    ) -> CompanyLookupResult:
        legal_name, expected_ref, _stock_code = next(
            value
            for value in self._COMPANIES.values()
            if value[1] == candidate_ref
        )
        self.lookup_refs.append(candidate_ref)
        assert candidate_ref == expected_ref
        return CompanyLookupResult(
            card=CompanyCard(
                legal_name=legal_name,
                typed_name=user_input.company,
                address="공식 DART fixture 주소",
                ceo="fixture",
                founded="20000101",
                ref=candidate_ref,
            ),
            model="free-local-dart-fixture",
        )

    def find_company_metered(self, user_input: UserInput) -> CompanyLookupResult:
        """실제 provider 없이 공식 DART 식별값을 돌려주는 exact fixture."""

        self.exact_inputs.append(user_input.company)
        legal_name, corp_code, _stock_code = self._COMPANIES[user_input.company]
        return CompanyLookupResult(
            card=CompanyCard(
                legal_name=legal_name,
                typed_name=user_input.company,
                address="공식 DART fixture 주소",
                ceo="fixture",
                founded="20000101",
                ref=corp_code,
            ),
            model="free-local-dart-fixture",
        )

    def run(
        self,
        user_input: UserInput,
        card: CompanyCard,
        on_step=None,
    ) -> RunResult:
        del on_step
        self.run_inputs.append((user_input.company, card.legal_name))
        return RunResult(
            outcome=Outcome.GATE_STOPPED,
            message="외부호출 없는 LINK 비용 원장 fixture 종료",
            cost_krw=13.0,
            model="offline-link-cost-fixture",
        )


class PaidGoogleFixture:
    costs_money = True
    accounting_cost_krw = 49.0
    provider_name = "Google Maps"

    def __init__(self):
        self.calls = 0

    def search(self, **_kwargs):
        self.calls += 1
        return [
            RawBusinessCandidate(
                candidate_name="JYP 엔터테인먼트",
                address="서울특별시 강동구 강동대로 205",
                homepage="https://www.jype.com/",
                provider_name="Google Maps",
                attributions=(("공공 주소 데이터", "https://example.org/source"),),
            )
        ]


def test_후보_attempt와_검색grant는_공통_TTL_경계뒤에만_정리된다(monkeypatch):
    created_at = 1_000.0
    user_input = UserInput(company="JYP", job="", region="서울 강동구")
    attempt_token = "candidate-attempt"
    grant_token = "candidate-search-grant"
    run_id = "candidate-run-id"
    monkeypatch.setattr(job_runtime, "_JOBS", {})
    monkeypatch.setattr(job_runtime, "_PAID_ATTEMPTS", {})
    monkeypatch.setattr(
        job_runtime,
        "_CANDIDATE_ATTEMPTS",
        {
            attempt_token: job_runtime.CandidateAttempt(
                token=attempt_token,
                run_id=run_id,
                user_input=user_input,
                candidate_count=1,
                share_key="public",
                bucket_id="public",
                candidate_cost_krw=0.0,
                elapsed_sec=0.0,
                posting_image_consent=False,
                evaluation_paid_consent=False,
                created_at=created_at,
            )
        },
    )
    monkeypatch.setattr(
        job_runtime,
        "_CANDIDATE_SEARCH_GRANTS",
        {
            grant_token: job_runtime.CandidateSearchGrant(
                token=grant_token,
                user_input=user_input,
                share_key="public",
                bucket_id="public",
                posting_image_consent=False,
                evaluation_paid_consent=False,
                created_at=created_at,
            )
        },
    )
    monkeypatch.setattr(job_runtime, "_expire_observation_pending", lambda: None)
    released: list[str] = []
    monkeypatch.setattr(public_ids, "release", released.append)

    job_runtime._sweep_jobs(created_at + CANDIDATE_ATTEMPT_TTL_SEC)

    assert attempt_token in job_runtime._CANDIDATE_ATTEMPTS
    assert grant_token in job_runtime._CANDIDATE_SEARCH_GRANTS
    assert released == []

    job_runtime._sweep_jobs(created_at + CANDIDATE_ATTEMPT_TTL_SEC + 0.001)

    assert attempt_token not in job_runtime._CANDIDATE_ATTEMPTS
    assert grant_token not in job_runtime._CANDIDATE_SEARCH_GRANTS
    assert released == [run_id]


def test_paid_candidate_worker_slot부족은_provider0회_phase취소_0원이다(monkeypatch):
    from src.features.business_candidate import logic as candidate_logic
    from src.web.routers import analysis as analysis_router

    class FullWorkerSlots:
        def acquire(self, *, blocking):
            assert blocking is False
            return False

        def release(self):
            raise AssertionError("획득하지 않은 slot을 반환하면 안 됩니다")

    class RequestFixture:
        client = type("Client", (), {"host": "127.0.0.1"})()

    provider = PaidGoogleFixture()
    ticket = object()
    cancelled: list[object] = []
    released: list[str] = []
    monkeypatch.setattr(candidate_logic, "_PROVIDER_WORKER_SLOTS", FullWorkerSlots())
    monkeypatch.setattr(
        paid_runtime, "_reserve_run_slot", lambda _track, _bucket: "candidate-slot"
    )
    monkeypatch.setattr(
        paid_runtime, "_release_run_slot", lambda slot: released.append(slot)
    )
    monkeypatch.setattr(paid_runtime, "_begin_paid_phase", lambda **_kwargs: ticket)
    monkeypatch.setattr(
        paid_runtime,
        "_call_paid_provider",
        lambda _ticket, func, *args, **kwargs: func(*args, **kwargs),
    )
    monkeypatch.setattr(
        paid_runtime, "_cancel_paid_phase", lambda phase: cancelled.append(phase)
    )
    monkeypatch.setattr(
        paid_runtime,
        "_settle_paid_phase",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("provider 미호출 phase를 정산하면 안 됩니다")
        ),
    )

    outcome = asyncio.run(
        analysis_router._resolve_business_candidates(
            RequestFixture(),  # type: ignore[arg-type]
            provider=provider,
            user_input=UserInput(
                company="JYP",
                job="매니지먼트",
                region="서울 강동구",
                posting_text="채용 공고",
            ),
            resolved_track=(object(), "fixture-bucket", 100.0),  # type: ignore[arg-type]
            allow_paid_provider=True,
            analysis_run_id="fixture-run",
        )
    )

    assert isinstance(outcome, tuple)
    result, cost_krw = outcome
    assert result.status is candidate_logic.ResolutionStatus.RATE_LIMITED
    assert result.provider_called is False
    assert provider.calls == 0
    assert cost_krw == 0.0
    assert cancelled == [ticket]
    assert released == ["candidate-slot"]


def _admin_client() -> tuple[TestClient, str]:
    client = TestClient(
        main.app,
        base_url="http://127.0.0.1:8000",
        headers={"Origin": "http://127.0.0.1:8000"},
    )
    session = auth_logic.create_session("admin@example.com", True)
    client.cookies.set(auth_constants.SESSION_COOKIE_NAME, session.token)
    return client, auth_logic.csrf_token_for_session(session.token)


def _form(csrf: str, **changes: str) -> dict[str, str]:
    data = {
        "company": "JYP",
        "job": "매니지먼트",
        "region": "서울 강동구",
        "posting_text": "채용 공고",
        "posting_image_consent": "yes",
        "csrf_token": csrf,
    }
    data.update(changes)
    return data


def _hidden(body: str, name: str) -> str:
    found = re.search(
        rf'name="{re.escape(name)}"\s+value="([^"]*)"', body
    )
    assert found is not None, (name, body[:500])
    return found.group(1)


class _AnalysisClock:
    def __init__(self, now: float):
        self.now = now

    def monotonic(self) -> float:
        return self.now

    @staticmethod
    def perf_counter() -> float:
        return time.perf_counter()


def _analysis_clock() -> _AnalysisClock:
    """TTL 경계 시험에 물릴 가짜 시계를 만든다.

    시작값은 **진짜 시계를 정수로 반올림한 값**이다. 조건 두 개를 «동시에»
    만족해야 하기 때문이고, 2026-08-26에 둘 다 실측으로 확인했다.

    ① **진짜 시계와 가까울 것.** ``job_runtime._sweep_jobs``는 monkeypatch되지
       않아 «진짜» 시계를 본다. ``1000.0`` 같은 먼 값을 시드로 쓰면 기록을 만든
       즉시 「너무 오래됐다」로 쓸려 나가 grant 경계 시험이 **곧바로 403**이 된다.

    ② **300.0을 더하고 빼도 오차가 없을 것.** 경계 시험은 ``clock.now += 300.0``
       한 뒤 서비스 코드가 다시 빼서 ``(x + 300.0) - x``를 계산한다. 컴퓨터가
       소수를 아주 살짝 반올림해 저장하는 탓에 이 값이 «항상» 300.0이 되지는
       않는다 — 시드가 ``1000.123456789``이면 ``300.0000000000001``이 나오고,
       서비스 코드의 ``> CANDIDATE_ATTEMPT_TTL_SEC``에 걸려 「나이가 정확히
       TTL이면 아직 유효(200)」인 시험이 **403으로 뒤집힌다.**
       진짜 시계 값은 기계가 켜져 있던 시간마다 다르므로, 이 시험은 «돌리는
       순간에 따라» 빨간불이 됐다 — 원인 모를 흔들림의 정체가 이것이다.

    정수로 반올림한 값은 ①과 ②를 둘 다 만족한다(정수끼리의 덧셈·뺄셈에는
    반올림 오차가 없다).

    ★ 서비스 코드는 **고치지 않았다.** 진짜 운영에서는 두 시각이 각각 따로
      측정되어 「정확히 TTL」이 나올 일이 없다. 시험이 만든 인공적인 경계를
      맞추려고 서비스의 만료 규칙을 바꾸는 것은 앞뒤가 뒤바뀐 수정이다.
    """
    return _AnalysisClock(float(round(time.monotonic())))


def test_가짜_시계_시작값이_두_조건을_모두_지킨다():
    """★ 이 단언이 깨지면 아래 두 경계 시험이 «무작위로» 빨간불이 된다."""
    나이 = float(CANDIDATE_ATTEMPT_TTL_SEC)
    시드 = _analysis_clock().now

    # ② 300.0을 더하고 빼도 오차가 없다
    assert (시드 + 나이) - 시드 == 나이
    assert 시드 == float(round(시드)), "정수가 아니면 오차가 생긴다"

    # ① 진짜 시계와 가깝다 (_sweep_jobs 가 쓸어 가지 않도록)
    assert abs(시드 - time.monotonic()) < 나이

    # 반대 증거 — 「나쁜 자릿수」를 넣으면 정말로 어긋난다
    나쁜_시드 = 1000.123456789
    assert (나쁜_시드 + 나이) - 나쁜_시드 > 나이


@pytest.mark.parametrize(
    ("age_sec", "expected_status", "expected_lookups"),
    (
        (float(CANDIDATE_ATTEMPT_TTL_SEC), 200, 1),
        (CANDIDATE_ATTEMPT_TTL_SEC + 0.001, 403, 0),
    ),
)
def test_후보attempt는_TTL_정확경계까지만_선택에_재사용된다(
    monkeypatch, age_sec: float, expected_status: int, expected_lookups: int
):
    from src.web.routers import analysis as analysis_router

    clock = _analysis_clock()
    monkeypatch.setattr(analysis_router, "time", clock)
    pipeline = CandidateAwareFakeRealPipeline(local_candidates=True)
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)
    client, csrf = _admin_client()
    try:
        body = client.post("/confirm", data=_form(csrf)).text
        attempt_token = _hidden(body, "candidate_attempt_token")
        clock.now += age_sec

        confirmed = client.post(
            "/confirm",
            data=_form(
                csrf,
                candidate_resolution_confirmed="yes",
                candidate_attempt_token=attempt_token,
                candidate_selection_token=_hidden(
                    body, "candidate_selection_token"
                ),
                candidate_index=_hidden(body, "candidate_index"),
                candidate_name=_hidden(body, "candidate_name"),
                candidate_provider=_hidden(body, "candidate_provider"),
                candidate_ref=_hidden(body, "candidate_ref"),
            ),
        )
    finally:
        client.close()

    assert confirmed.status_code == expected_status
    assert pipeline.lookup_calls == expected_lookups
    assert attempt_token not in job_runtime._CANDIDATE_ATTEMPTS


@pytest.mark.parametrize(
    ("age_sec", "expected_status", "expected_calls"),
    (
        (float(CANDIDATE_ATTEMPT_TTL_SEC), 200, 1),
        (CANDIDATE_ATTEMPT_TTL_SEC + 0.001, 403, 0),
    ),
)
def test_후보검색_grant는_TTL_정확경계까지만_provider를_허용한다(
    monkeypatch, age_sec: float, expected_status: int, expected_calls: int
):
    from src.features.business_candidate import providers
    from src.web.routers import analysis as analysis_router

    clock = _analysis_clock()
    monkeypatch.setattr(analysis_router, "time", clock)
    pipeline = CandidateAwareFakeRealPipeline(local_candidates=False)
    google = PaidGoogleFixture()
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)
    monkeypatch.setenv(evaluation_mode.ENV_MODE, "1")
    monkeypatch.setenv(evaluation_mode.ENV_PAID_PROVIDERS, "1")
    monkeypatch.setattr(
        request_helpers, "_strict_loopback_http_request", lambda _request: True
    )
    monkeypatch.setattr(
        providers,
        "configured_provider",
        lambda _pipeline, **_kwargs: google,
    )
    client, csrf = _admin_client()
    try:
        workflow_id = _hidden(client.get("/").text, "evaluation_workflow_id")
        missed = client.post(
            "/confirm",
            data=_form(
                csrf,
                evaluation_paid_consent="yes",
                evaluation_workflow_id=workflow_id,
            ),
        )
        search_grant = _hidden(missed.text, "candidate_search_grant")
        consent_grant = _hidden(missed.text, "evaluation_consent_grant")
        stored_grant = job_runtime._CANDIDATE_SEARCH_GRANTS[search_grant]
        assert stored_grant.created_at == clock.now
        assert stored_grant.posting_image_consent is False
        assert stored_grant.evaluation_paid_consent is True
        assert stored_grant.user_input == request_helpers.company_analysis_input(
            company="JYP", region="서울 강동구"
        )
        clock.now += age_sec
        assert clock.now - stored_grant.created_at == pytest.approx(age_sec)

        searched = client.post(
            "/confirm",
            data=_form(
                csrf,
                candidate_search_requested="yes",
                candidate_search_grant=search_grant,
                evaluation_consent_grant=consent_grant,
            ),
        )
    finally:
        client.close()

    assert searched.status_code == expected_status, searched.text
    assert google.calls == expected_calls


def test_DART_local_후보는_사람이_선택해야만_DART를_다시_부르고_원입력을_보존한다(
    monkeypatch,
):
    pipeline = CandidateAwareFakeRealPipeline(local_candidates=True)
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)
    client, csrf = _admin_client()
    try:
        candidates = client.post("/confirm", data=_form(csrf))
        assert candidates.status_code == 200
        assert "확인할 회사 후보입니다" in candidates.text
        assert "(주)제이와이피엔터테인먼트" in candidates.text
        assert "원하는 기업이 없으신가요?" in candidates.text
        assert "검색어 수정하기" in candidates.text
        assert pipeline.search_calls == 1
        assert pipeline.lookup_calls == 0
        assert _hidden(candidates.text, "company") == "JYP"
        attempt_token = _hidden(candidates.text, "candidate_attempt_token")
        selection_token = _hidden(candidates.text, "candidate_selection_token")
        index = _hidden(candidates.text, "candidate_index")
        candidate_name = _hidden(candidates.text, "candidate_name")
        candidate_provider = _hidden(candidates.text, "candidate_provider")
        candidate_ref = _hidden(candidates.text, "candidate_ref")

        confirmed = client.post(
            "/confirm",
            data=_form(
                csrf,
                candidate_resolution_confirmed="yes",
                candidate_attempt_token=attempt_token,
                candidate_selection_token=selection_token,
                candidate_index=index,
                candidate_name=candidate_name,
                candidate_provider=candidate_provider,
                candidate_ref=candidate_ref,
            ),
        )
        assert confirmed.status_code == 200
        assert "이 회사가 맞나요?" in confirmed.text
        assert "서울특별시 강동구 강동대로 205" in confirmed.text
        confirmation_token = _hidden(confirmed.text, "paid_attempt_token")
        assert job_runtime._PAID_ATTEMPTS[confirmation_token].card.ref == "00258689"
        assert 'name="ref"' not in confirmed.text
        assert pipeline.search_calls == 1
        assert pipeline.lookup_inputs == ["JYP"]
        assert pipeline.lookup_refs == ["00258689"]
        assert _hidden(confirmed.text, "company") == "JYP"
    finally:
        client.close()


def test_DART_local_후보선택은_서명된_고유번호를_직접재조회하고_이름AI를_부르지않는다(
    monkeypatch,
):
    pipeline = CandidateAwareFakeRealPipeline(candidate_refs=True)
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)
    client, csrf = _admin_client()
    try:
        candidates = client.post("/confirm", data=_form(csrf))
        assert candidates.status_code == 200
        assert "035900" in candidates.text
        assert "2022-12-06" in candidates.text
        assert pipeline.lookup_calls == 0  # 점수가 높아도 자동 확정하지 않는다.

        confirmed = client.post(
            "/confirm",
            data=_form(
                csrf,
                candidate_resolution_confirmed="yes",
                candidate_attempt_token=_hidden(candidates.text, "candidate_attempt_token"),
                candidate_selection_token=_hidden(candidates.text, "candidate_selection_token"),
                candidate_index=_hidden(candidates.text, "candidate_index"),
                candidate_name=_hidden(candidates.text, "candidate_name"),
                candidate_provider=_hidden(candidates.text, "candidate_provider"),
                candidate_ref=_hidden(candidates.text, "candidate_ref"),
            ),
        )

        assert confirmed.status_code == 200
        assert "JYP Ent." in confirmed.text
        assert pipeline.lookup_refs == ["00258689"]
        assert pipeline.lookup_inputs == ["JYP"]
        confirmation_token = _hidden(confirmed.text, "paid_attempt_token")
        assert job_runtime._PAID_ATTEMPTS[confirmation_token].card.ref == "00258689"
        assert 'name="ref"' not in confirmed.text
    finally:
        client.close()


def test_YG는_실제_RealPipeline_DARTmatcher에서_선택전_후보카드로만_보인다(
    monkeypatch,
):
    from src.features.business_candidate import providers
    from src.features.pipeline import real

    class YgDartFixtureEngine:
        class UsageCounter:
            pass

        MODEL = ""

        def __init__(self):
            self.loaded = 0
            self.profile_calls: list[str] = []

        def load_env(self):
            self.loaded += 1

        def get_json(self, path, params, _counter):
            assert path == "company.json"
            corp_code = str(params["corp_code"])
            self.profile_calls.append(corp_code)
            assert corp_code == "00613318"
            return {
                "status": "000",
                "corp_code": corp_code,
                "corp_name": "와이지엔터테인먼트",
                "adres": "",
                "ceo_nm": "",
                "est_dt": "",
                "hm_url": "",
            }

    catalog = (
        (
            "00613318",
            "와이지엔터테인먼트",
            "YG Entertainment Inc.",
            "122870",
            "20240401",
        ),
    )
    engine = YgDartFixtureEngine()
    monkeypatch.setattr(real, "_company_catalog", lambda: catalog)
    monkeypatch.setattr(real, "_engine", lambda: engine)
    monkeypatch.setattr(runtime, "_PIPELINE", real.RealPipeline())
    monkeypatch.setattr(
        providers,
        "configured_provider",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("YG DART 후보 뒤 외부 provider를 열면 안 됩니다")
        ),
    )

    client, csrf = _admin_client()
    try:
        candidates = client.post(
            "/confirm",
            data=_form(csrf, company="YG", region=""),
        )
        assert candidates.status_code == 200
        assert "확인할 회사 후보입니다" in candidates.text
        assert "와이지엔터테인먼트" in candidates.text
        assert "YG Entertainment Inc." in candidates.text
        assert "122870" in candidates.text
        assert "2024-04-01" in candidates.text
        assert _hidden(candidates.text, "company") == "YG"
        # 이름 점수가 높아도 후보를 찾는 fixture 조회 한 번뿐이며 자동 확정하지 않는다.
        assert engine.profile_calls == ["00613318"]
        assert len(job_runtime._PAID_ATTEMPTS) == 0

        confirmed = client.post(
            "/confirm",
            data=_form(
                csrf,
                company="YG",
                region="",
                candidate_resolution_confirmed="yes",
                candidate_attempt_token=_hidden(
                    candidates.text, "candidate_attempt_token"
                ),
                candidate_selection_token=_hidden(
                    candidates.text, "candidate_selection_token"
                ),
                candidate_index=_hidden(candidates.text, "candidate_index"),
                candidate_name=_hidden(candidates.text, "candidate_name"),
                candidate_provider=_hidden(candidates.text, "candidate_provider"),
                candidate_ref=_hidden(candidates.text, "candidate_ref"),
            ),
        )

        assert confirmed.status_code == 200
        assert "이 회사가 맞나요?" in confirmed.text
        assert "와이지엔터테인먼트" in confirmed.text
        assert engine.profile_calls == ["00613318", "00613318"]
        confirmation_token = _hidden(confirmed.text, "paid_attempt_token")
        attempt = job_runtime._PAID_ATTEMPTS[confirmation_token]
        assert attempt.card.ref == "00613318"
        assert attempt.card.typed_name == "YG"
    finally:
        client.close()


@pytest.mark.parametrize("typed", ["JYP", "jyp", "Jyp", "ＪＹＰ", "JYP Entertainment"])
def test_대소문자와_영문별칭은_유료AI나_Google전에_DART후보로_멈춘다(
    monkeypatch, typed
):
    pipeline = CandidateAwareFakeRealPipeline(candidate_refs=True)

    def local_search(**kwargs):
        pipeline.search_calls += 1
        assert kwargs["company"] == unicodedata.normalize("NFKC", typed)
        return [
            RawBusinessCandidate(
                candidate_name="JYP Ent.",
                english_name="JYP Entertainment Corporation",
                address="서울특별시 강동구 강동대로 205",
                provider_name="DART",
                candidate_ref="00258689",
                stock_code="035900",
                modify_date="20221206",
                name_match_kind="exact_name",
                name_similarity=1.0,
            )
        ]

    pipeline.search_business_candidates = local_search  # type: ignore[method-assign]
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)
    from src.features.business_candidate import providers

    monkeypatch.setattr(
        providers,
        "configured_provider",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("local alias hit 뒤 Google을 열면 안 됩니다")
        ),
    )
    client, csrf = _admin_client()
    try:
        response = client.post("/confirm", data=_form(csrf, company=typed))
    finally:
        client.close()

    assert response.status_code == 200
    assert "확인할 회사 후보입니다" in response.text
    assert "JYP Entertainment Corporation" in response.text
    assert pipeline.search_calls == 1
    assert pipeline.lookup_calls == 0


def test_회사분석_confirm은_옛_직무와_공고필드를_무시한다(monkeypatch):
    pipeline = CandidateAwareFakeRealPipeline(local_candidates=True)
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)
    client, csrf = _admin_client()
    try:
        response = client.post(
            "/confirm",
            data=_form(
                csrf,
                job="옛 직무",
                posting_text="옛 공고",
                posting_image_consent="yes",
            ),
        )
    finally:
        client.close()

    assert response.status_code == 200
    assert "확인할 회사 후보입니다" in response.text
    attempt = next(iter(job_runtime._CANDIDATE_ATTEMPTS.values()))
    assert attempt.user_input.job == ""
    assert attempt.user_input.posting_text == ""
    assert attempt.posting_image_consent is False


@pytest.mark.parametrize(
    ("posting_text", "posting_image_consent"),
    [("채용 공고 원문", ""), ("", "yes")],
)
def test_일반텍스트와_image_only신호는_confirm후보흐름을_유지한다(
    monkeypatch, posting_text, posting_image_consent
):
    pipeline = CandidateAwareFakeRealPipeline(local_candidates=True)
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)
    client, csrf = _admin_client()
    try:
        response = client.post(
            "/confirm",
            data=_form(
                csrf,
                posting_text=posting_text,
                posting_image_consent=posting_image_consent,
            ),
        )
    finally:
        client.close()

    assert response.status_code == 200
    assert "확인할 회사 후보입니다" in response.text
    assert pipeline.search_calls == 1
    assert pipeline.lookup_calls == 0


def test_cold_DART가_공통8초보다_길어도_30초안이면_warm과같은_무료후보를낸다(
    monkeypatch,
):
    from src.features.business_candidate import logic as candidate_logic
    from src.features.business_candidate import providers

    pipeline = CandidateAwareFakeRealPipeline(local_candidates=False)
    received_timeouts: list[float] = []

    def cold_local_search(**kwargs):
        pipeline.search_calls += 1
        received_timeouts.append(kwargs["timeout_sec"])
        if pipeline.search_calls == 1:
            time.sleep(0.03)
        return [
            RawBusinessCandidate(
                candidate_name="JYP Ent.",
                english_name="JYP Entertainment Corporation",
                address="서울특별시 강동구 강동대로 205",
                provider_name="DART",
                candidate_ref="00258689",
                stock_code="035900",
                modify_date="20221206",
                name_match_kind="acronym_token",
                name_similarity=1.0,
            ),
            RawBusinessCandidate(
                candidate_name="(주)제이와이피",
                english_name="JYP Corporation",
                address="서울특별시 강남구 청담동 123-50",
                provider_name="DART",
                candidate_ref="00535454",
                modify_date="20170630",
                name_match_kind="acronym_reading",
                name_similarity=1.0,
            ),
        ]

    pipeline.search_business_candidates = cold_local_search  # type: ignore[method-assign]
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)
    monkeypatch.setattr(candidate_logic, "PROVIDER_TIMEOUT_SEC", 0.005)
    monkeypatch.setattr(
        providers,
        "configured_provider",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("local cold 성공 뒤 Google을 열면 안 됩니다")
        ),
    )
    monkeypatch.setattr(
        paid_runtime,
        "_begin_paid_phase",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("local 후보 화면 전에 paid identify를 열면 안 됩니다")
        ),
    )

    client, csrf = _admin_client()
    try:
        cold_response = client.post("/confirm", data=_form(csrf))
        warm_response = client.post("/confirm", data=_form(csrf))
        attempts = [
            job_runtime._CANDIDATE_ATTEMPTS[
                _hidden(body.text, "candidate_attempt_token")
            ]
            for body in (cold_response, warm_response)
        ]
    finally:
        client.close()

    assert cold_response.status_code == warm_response.status_code == 200
    for response in (cold_response, warm_response):
        assert "확인할 회사 후보입니다" in response.text
        assert response.text.index("JYP Ent.") < response.text.index("(주)제이와이피")
    assert received_timeouts == [30.0, 30.0]
    assert pipeline.lookup_calls == 0
    assert pipeline.search_calls == 2
    assert [attempt.candidate_count for attempt in attempts] == [2, 2]
    assert [attempt.candidate_cost_krw for attempt in attempts] == [0.0, 0.0]


@pytest.mark.parametrize(
    "error_type",
    [ProviderTimedOut, RuntimeError, ProviderRateLimited],
)
def test_DART_local_기술실패는_paid_fallback과_보고서quota없이_재시도한다(
    monkeypatch, error_type
):
    from src.features.business_candidate import logic as candidate_logic
    from src.features.business_candidate import providers

    pipeline = CandidateAwareFakeRealPipeline(local_candidates=False)

    def broken_local_search(**_kwargs):
        pipeline.search_calls += 1
        raise error_type("offline fixture failure")

    pipeline.search_business_candidates = broken_local_search  # type: ignore[method-assign]
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)
    monkeypatch.setattr(paid_runtime, "_RATE_HISTORY", budget_logic.RateHistory())
    monkeypatch.setattr(
        paid_runtime,
        "_begin_paid_phase",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("local 기술실패 뒤 paid phase를 열면 안 됩니다")
        ),
    )
    monkeypatch.setattr(
        providers,
        "configured_provider",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("local 기술실패 뒤 Google을 열면 안 됩니다")
        ),
    )
    reserved_before = set(public_ids._RESERVED_IDS)

    client, csrf = _admin_client()
    try:
        response = client.post("/confirm", data=_form(csrf))
    finally:
        client.close()

    assert response.status_code == 200
    assert "회사 후보 조회가 잠시 지연됐습니다" in response.text
    assert "유료 회사 식별이나 외부 후보 검색으로 넘어가지 않았" in response.text
    assert "이름 재입력 횟수와 보고서 생성 횟수는 차감되지 않았" in response.text
    assert "Google Maps로 회사 후보 찾기" not in response.text
    assert _hidden(response.text, "retry") == "0"
    assert pipeline.search_calls == 1
    assert pipeline.lookup_calls == 0
    assert paid_runtime._RATE_HISTORY.starts == {}
    assert sum(
        len(starts) for starts in candidate_logic._RATE_HISTORY.starts.values()
    ) == 1
    assert set(public_ids._RESERVED_IDS) == reserved_before
    assert job_runtime._CANDIDATE_ATTEMPTS == {}


def test_후보선택_token은_직무주소공고_이미지동의_index를_bind하고_한번만_쓴다(
    monkeypatch,
):
    pipeline = CandidateAwareFakeRealPipeline(local_candidates=True)
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)
    client, csrf = _admin_client()
    try:
        body = client.post("/confirm", data=_form(csrf)).text
        attempt_token = _hidden(body, "candidate_attempt_token")
        selection_token = _hidden(body, "candidate_selection_token")
        index = _hidden(body, "candidate_index")
        candidate_name = _hidden(body, "candidate_name")
        candidate_provider = _hidden(body, "candidate_provider")
        tampered = client.post(
            "/confirm",
            data=_form(
                csrf,
                posting_image_consent="",
                candidate_resolution_confirmed="yes",
                candidate_attempt_token=attempt_token,
                candidate_selection_token=selection_token,
                candidate_index=index,
                candidate_name=candidate_name,
                candidate_provider=candidate_provider,
            ),
        )
        assert tampered.status_code == 403
        assert pipeline.lookup_calls == 0

        # 변조 시 one-time attempt도 폐기되어 같은 token을 정상값으로 되살릴 수 없다.
        replay = client.post(
            "/confirm",
            data=_form(
                csrf,
                candidate_resolution_confirmed="yes",
                candidate_attempt_token=attempt_token,
                candidate_selection_token=selection_token,
                candidate_index=index,
                candidate_name=candidate_name,
                candidate_provider=candidate_provider,
            ),
        )
        assert replay.status_code == 403
        assert pipeline.lookup_calls == 0
    finally:
        client.close()


@pytest.mark.parametrize("company", ["JYP", "YG"])
def test_오프라인데모는_외부후보를_부르지_않고_저장목록만_안내한다(
    monkeypatch, company
):
    monkeypatch.setattr(runtime, "_PIPELINE", DemoPipeline())

    def forbidden(_pipeline):
        raise AssertionError("오프라인 데모가 후보 공급자를 호출했습니다")

    from src.features.business_candidate import providers

    monkeypatch.setattr(providers, "configured_local_provider", forbidden)
    monkeypatch.setattr(providers, "configured_provider", forbidden)
    with TestClient(main.app) as client:
        response = client.post(
            "/confirm",
            data={
                "company": company,
                "job": "매니지먼트",
                "region": "서울 강동구",
                "posting_text": "채용 공고",
            },
        )
    assert response.status_code == 200
    assert "이 회사는 저장된 데모 목록에 없습니다" in response.text
    assert "미리 저장한 예시 보고서만 재생하는 오프라인 데모" in response.text
    assert "인터넷이나 DART에서 실시간으로 찾지 않습니다" in response.text
    assert "데모에서 되는 회사" in response.text
    assert "전자공시에 등록된 이름과 달라서" not in response.text


def test_DART_local후보0건이고_외부검색이꺼졌으면_Google을검색했다고_말하지않는다(
    monkeypatch,
):
    pipeline = CandidateAwareFakeRealPipeline(local_candidates=False)
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)
    from src.features.business_candidate import providers

    monkeypatch.setattr(
        providers,
        "configured_provider",
        lambda _pipeline, **_kwargs: None,
    )
    client, csrf = _admin_client()
    try:
        response = client.post("/confirm", data=_form(csrf))
    finally:
        client.close()

    assert response.status_code == 200
    assert "DART 법인목록의 약어·영문명 후보" in response.text
    assert "추가 외부 후보 검색은 현재 사용할 수 없" in response.text
    assert "Google Maps 장소 검색에서도" not in response.text
    assert "보고서 생성 횟수" in response.text
    assert "완료된 외부 조회 비용은 비용 원장에 기록될 수 있습니다" in response.text
    assert "할당량은" not in response.text
    assert pipeline.search_calls == 1
    assert pipeline.lookup_inputs == ["JYP"]


def test_Google은_DART0건뒤_명시버튼으로만_1회호출하고_같은_run_id로_DART재검증한다(
    monkeypatch,
):
    pipeline = CandidateAwareFakeRealPipeline(local_candidates=False)
    google = PaidGoogleFixture()
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)
    monkeypatch.setenv(evaluation_mode.ENV_MODE, "1")
    monkeypatch.setenv(evaluation_mode.ENV_PAID_PROVIDERS, "1")
    monkeypatch.setattr(request_helpers, "_strict_loopback_http_request", lambda _r: True)
    from src.features.business_candidate import providers

    monkeypatch.setattr(
        providers,
        "configured_provider",
        lambda _pipeline, **_kwargs: google,
    )
    client, csrf = _admin_client()
    try:
        workflow_id = _hidden(client.get("/").text, "evaluation_workflow_id")
        missed = client.post(
            "/confirm",
            data=_form(
                csrf,
                evaluation_paid_consent="yes",
                evaluation_workflow_id=workflow_id,
            ),
        )
        assert missed.status_code == 200, missed.text
        assert "Google Maps로 회사 후보 찾기" in missed.text
        assert "회사명·입력 주소가 Google에 전송" in missed.text
        assert google.calls == 0
        search_grant = _hidden(missed.text, "candidate_search_grant")
        consent_grant = _hidden(missed.text, "evaluation_consent_grant")

        # signed consent가 빠진 직접 POST는 provider 0회다.
        denied = client.post(
            "/confirm",
            data=_form(
                csrf,
                candidate_search_requested="yes",
                candidate_search_grant=search_grant,
                evaluation_paid_consent="yes",
            ),
        )
        assert denied.status_code == 422
        assert google.calls == 0

        candidates = client.post(
            "/confirm",
            data=_form(
                csrf,
                candidate_search_requested="yes",
                candidate_search_grant=search_grant,
                evaluation_consent_grant=consent_grant,
            ),
        )
        assert candidates.status_code == 200
        assert google.calls == 1
        assert "Google Maps" in candidates.text
        assert 'translate="no"' in candidates.text
        assert "공공 주소 데이터" in candidates.text
        candidate_attempt_token = _hidden(candidates.text, "candidate_attempt_token")
        candidate_token = _hidden(candidates.text, "candidate_selection_token")
        candidate_index = _hidden(candidates.text, "candidate_index")
        candidate_name = _hidden(candidates.text, "candidate_name")
        candidate_provider = _hidden(candidates.text, "candidate_provider")

        # 브라우저 refresh/back POST는 49원을 다시 쓰지 않고 같은 후보를 재표시한다.
        cached = client.post(
            "/confirm",
            data=_form(
                csrf,
                candidate_search_requested="yes",
                candidate_search_grant=search_grant,
                evaluation_consent_grant=consent_grant,
            ),
        )
        assert cached.status_code == 410
        assert google.calls == 1
        assert "검색 결과를 서버에 보관하지 않" in cached.text

        confirmed = client.post(
            "/confirm",
            data=_form(
                csrf,
                candidate_resolution_confirmed="yes",
                candidate_attempt_token=candidate_attempt_token,
                candidate_selection_token=candidate_token,
                candidate_index=candidate_index,
                candidate_name=candidate_name,
                candidate_provider=candidate_provider,
                evaluation_consent_grant=consent_grant,
            ),
        )
        assert confirmed.status_code == 200
        assert pipeline.lookup_inputs == ["JYP", "JYP 엔터테인먼트"]
        paid_attempt = job_runtime._PAID_ATTEMPTS[
            _hidden(confirmed.text, "paid_attempt_token")
        ]
        assert paid_attempt.user_input.company == "JYP"
        assert paid_attempt.lookup_cost_krw == 56.0
        with storage_db.connect() as conn:
            rows = [
                tuple(row)
                for row in conn.execute(
                    "SELECT run_id, phase, cost_krw FROM budget_spend_events "
                    "WHERE phase IN (?, ?) ORDER BY created_at",
                    (SPEND_PHASE_CANDIDATE, SPEND_PHASE_IDENTIFY),
                ).fetchall()
                if float(row[2]) > 0
            ]
        candidate_rows = [row for row in rows if row[1] == SPEND_PHASE_CANDIDATE]
        identify_rows = [row for row in rows if row[1] == SPEND_PHASE_IDENTIFY]
        assert candidate_rows[-1][2] == 49.0
        assert identify_rows[-1][2] == 7.0
        assert candidate_rows[-1][0] == identify_rows[-1][0] == paid_attempt.run_id
        assert paid_runtime._ACTIVE_PAID_PHASES == set()
    finally:
        client.close()


def test_Google_attribution의_공급자문자열은_HTML로_실행되지_않는다(monkeypatch):
    candidate = RawBusinessCandidate(
        candidate_name="JYP 엔터테인먼트",
        address="서울 강동구",
        provider_name="Google Maps",
        attributions=(("<img src=x onerror=alert(1)>", "https://example.org/"),),
    )
    # resolver가 text-only로 바꾼 뒤 Jinja도 escape한다.
    from src.features.business_candidate import logic

    result = logic.resolve_candidates(
        type("P", (), {"costs_money": False, "search": lambda self, **_k: [candidate]})(),
        company="JYP",
        address_hint="서울",
        rate_key="escape-attribution",
        now=100.0,
    )
    assert result.candidates
    # 태그뿐인 attribution은 보이는 안전한 글자가 없으므로 통째로 버린다.
    assert result.candidates[0].attributions == ()


def test_후보를_pop한뒤_첫_guard가막아도_run_id예약을_즉시_반환한다(monkeypatch):
    pipeline = CandidateAwareFakeRealPipeline(local_candidates=True)
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)
    client, csrf = _admin_client()
    try:
        body = client.post("/confirm", data=_form(csrf)).text
        attempt_token = _hidden(body, "candidate_attempt_token")
        attempt = job_runtime._CANDIDATE_ATTEMPTS[attempt_token]
        assert attempt.run_id in public_ids._RESERVED_IDS

        monkeypatch.setattr(
            request_helpers,
            "_guard_run",
            lambda _request, **kwargs: (
                PlainTextResponse("시험용 사전 차단", status_code=429)
                if kwargs.get("count_start") is False
                else None
            ),
        )
        blocked = client.post(
            "/confirm",
            data=_form(
                csrf,
                candidate_resolution_confirmed="yes",
                candidate_attempt_token=attempt_token,
                candidate_selection_token=_hidden(body, "candidate_selection_token"),
                candidate_index=_hidden(body, "candidate_index"),
                candidate_name=_hidden(body, "candidate_name"),
                candidate_provider=_hidden(body, "candidate_provider"),
                candidate_ref=_hidden(body, "candidate_ref"),
            ),
        )
        assert blocked.status_code == 429
        assert attempt_token not in job_runtime._CANDIDATE_ATTEMPTS
        assert attempt.run_id not in public_ids._RESERVED_IDS
        assert pipeline.lookup_calls == 0
    finally:
        client.close()


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: f"<script>{value}</script>",
        lambda value: f"  {value}  ",
        lambda value: value.replace("(", "（", 1),
        lambda value: value[:1] + "\u202e" + value[1:],
    ],
)
def test_서명과_같은canonical값으로_줄어드는_raw후보변조도_DART전에_거부한다(
    monkeypatch, mutate
):
    pipeline = CandidateAwareFakeRealPipeline(local_candidates=True)
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)
    client, csrf = _admin_client()
    try:
        body = client.post("/confirm", data=_form(csrf)).text
        original_name = _hidden(body, "candidate_name")
        blocked = client.post(
            "/confirm",
            data=_form(
                csrf,
                candidate_resolution_confirmed="yes",
                candidate_attempt_token=_hidden(body, "candidate_attempt_token"),
                candidate_selection_token=_hidden(body, "candidate_selection_token"),
                candidate_index=_hidden(body, "candidate_index"),
                candidate_name=mutate(original_name),
                candidate_provider=_hidden(body, "candidate_provider"),
            ),
        )
        assert blocked.status_code == 403
        assert pipeline.lookup_calls == 0
    finally:
        client.close()


def test_카카오_LINK로_네이버와_YG를_차례로_검색확정생성해도_같은통장과_시작보고서를_지킨다(
    monkeypatch,
):
    raw_key = "a1b2c3d4e5f60718a1b2c3d4e5f60718"
    initial_report_id = "1" * 32
    pipeline = LinkMultiCompanyFakeRealPipeline()
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)
    with storage_db.connect() as conn:
        assert share_store.insert_new(
            conn,
            key=raw_key,
            company="카카오",
            job="마케팅",
            report_id=initial_report_id,
            now_iso="2026-08-21T10:00:00+09:00",
        )

    client = TestClient(
        main.app,
        base_url="https://testserver",
        headers={"Origin": "https://testserver"},
    )
    try:
        opened = client.get(f"/k/{raw_key}", follow_redirects=False)
        assert opened.status_code == 303
        assert client.cookies.get(KEY_COOKIE_NAME) == raw_key
        csrf = auth_logic.csrf_token_for_session(raw_key)

        run_ids: list[str] = []
        expected_refs = {"네이버": "00266961", "YG": "00613318"}
        for input_name in ("네이버", "YG"):
            form = _form(csrf, company=input_name)
            confirmed = client.post("/confirm", data=form)
            assert confirmed.status_code == 200
            assert (
                f'data-dart-corp-code="{expected_refs[input_name]}"'
                in confirmed.text
            )
            paid_attempt_token = _hidden(confirmed.text, "paid_attempt_token")

            started = client.post(
                "/run",
                data={
                    "company": input_name,
                    "region": "서울 강동구",
                    "paid_attempt_token": paid_attempt_token,
                    "csrf_token": csrf,
                },
                follow_redirects=False,
            )
            assert started.status_code == 303
            run_id = started.headers["location"].rsplit("/", 1)[-1]
            run_ids.append(run_id)
            for _ in range(200):
                if client.get(f"/api/progress/{run_id}").json()["finished"]:
                    break
                time.sleep(0.005)
            else:
                pytest.fail("무과금 LINK fixture 생성이 끝나지 않았습니다")
            assert job_runtime._JOBS[run_id].result is not None
            assert job_runtime._JOBS[run_id].result.outcome is Outcome.GATE_STOPPED

        with storage_db.connect() as conn:
            link = share_store.load(conn, raw_key)
            runs = share_store.list_runs_by_hash(conn, link.key_hash)
            spend_history = spend_store.load_run_history(conn, run_ids)

        assert link.report_id == initial_report_id
        assert link.company == "카카오"
        assert {run.run_id for run in runs} == set(run_ids)
        assert {run.input_company for run in runs} == {"네이버", "YG"}
        assert {run.confirmed_company for run in runs} == {
            "NAVER",
            "와이지엔터테인먼트",
        }
        assert {run.status for run in runs} == {share_store.RUN_STATUS_STOPPED}
        assert {run.link_key_hash for run in runs} == {link.key_hash}
        assert {run.internal_ai_cost_krw for run in runs} == {13.0}
        assert spend_history.run_ids == frozenset(run_ids)
        assert set(spend_history.by_run.values()) == {13.0}
        assert set(spend_history.bucket_by_run.values()) == {
            spend_store.bucket_id(raw_key)
        }
        assert pipeline.search_inputs == ["네이버", "YG"]
        assert pipeline.exact_inputs == ["네이버", "YG"]
        assert pipeline.lookup_refs == []
        assert pipeline.run_inputs == [
            ("네이버", "NAVER"),
            ("YG", "와이지엔터테인먼트"),
        ]
    finally:
        client.close()


def test_무료_DART_후보보강_응답오류는_외부상태만남기고_전역점검으로_번지지않는다(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("STORAGE_DB_PATH", str(tmp_path / "storage.db"))

    analysis_router._observe_candidate_resolution(
        CandidateResolution(
            ResolutionStatus.FAILED,
            provider_called=True,
            provider_name="DART",
            local_profile_enrichment_failed=True,
        )
    )

    with storage_db.connect() as conn:
        service = dashboard_store.get_service_state(conn)
        incidents = dashboard_store.list_incidents(conn)
        health = provider_health_store.get_state(
            conn, provider_health_constants.PROVIDER_DART
        )
        external = conn.execute(
            "SELECT status, error_summary FROM dashboard_external_status_events "
            "WHERE provider = ? ORDER BY id DESC LIMIT 1",
            ("DART",),
        ).fetchone()
    assert service.status == dashboard_store.SERVICE_NORMAL
    assert incidents == []
    assert health.state is provider_health_store.ProviderHealthState.DEGRADED
    assert external is not None
    assert tuple(external) == ("error", "응답을 안전하게 해석하지 못함")


def test_반복_DART_실패는_DART만_열고_전역점검으로_번지지않는다(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_DB_PATH", str(tmp_path / "storage.db"))

    for _ in range(2):
        analysis_router._observe_candidate_resolution(
            CandidateResolution(
                ResolutionStatus.FAILED,
                provider_called=True,
                provider_name="DART",
            )
        )

    with storage_db.connect() as conn:
        service = dashboard_store.get_service_state(conn)
        incidents = dashboard_store.list_incidents(conn)
        dart_health = provider_health_store.get_state(
            conn, provider_health_constants.PROVIDER_DART
        )
        anthropic_permission = provider_health_store.peek_permission(
            conn,
            provider_health_constants.PROVIDER_ANTHROPIC,
            now_iso="2026-08-28T10:00:00+09:00",
        )
    assert service.status == dashboard_store.SERVICE_NORMAL
    assert len(incidents) == 2
    assert incidents[0]["kind"] == dashboard_store.INCIDENT_PROVIDER_RESPONSE
    assert dart_health.state is provider_health_store.ProviderHealthState.OPEN
    assert anthropic_permission.allowed is True


def test_provider미호출_rate_limit은_provider장애로_세지않는다(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("STORAGE_DB_PATH", str(tmp_path / "storage.db"))

    analysis_router._observe_candidate_resolution(
        CandidateResolution(
            ResolutionStatus.RATE_LIMITED,
            provider_called=False,
            provider_name="DART",
        )
    )

    with storage_db.connect() as conn:
        service = dashboard_store.get_service_state(conn)
        incidents = dashboard_store.list_incidents(conn)
        health = provider_health_store.get_state(
            conn, provider_health_constants.PROVIDER_DART
        )
        event_count = conn.execute(
            f"SELECT COUNT(*) FROM {provider_health_store.TABLE_EVENTS}"
        ).fetchone()[0]

    assert service.status == dashboard_store.SERVICE_NORMAL
    assert incidents == []
    assert health.version == 0
    assert event_count == 0


def test_Google후보관측은_paid_callback소유라서_여기서_중복기록하지않는다(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("STORAGE_DB_PATH", str(tmp_path / "storage.db"))

    analysis_router._observe_candidate_resolution(
        CandidateResolution(
            ResolutionStatus.FAILED,
            provider_called=True,
            provider_name="Google Maps",
        )
    )

    with storage_db.connect() as conn:
        google_health = provider_health_store.get_state(
            conn, provider_health_constants.PROVIDER_GOOGLE_PLACES
        )
        dart_health = provider_health_store.get_state(
            conn, provider_health_constants.PROVIDER_DART
        )
        event_count = conn.execute(
            f"SELECT COUNT(*) FROM {provider_health_store.TABLE_EVENTS}"
        ).fetchone()[0]
    assert google_health.version == 0
    assert dart_health.version == 0
    assert event_count == 0


def test_DART_cooldown중에는_실제후보provider를_부르지않고_관측도_늘리지않는다(
    monkeypatch, tmp_path
):
    class FreeDartProvider:
        costs_money = False
        provider_name = "DART"

        def __init__(self):
            self.calls = 0

        def search(self, **_kwargs):
            self.calls += 1
            raise AssertionError("cooldown 중 DART provider를 호출하면 안 됩니다")

    class RequestFixture:
        client = type("Client", (), {"host": "127.0.0.1"})()

    now_iso = "2026-08-28T10:00:00+09:00"
    monkeypatch.setenv("STORAGE_DB_PATH", str(tmp_path / "storage.db"))
    monkeypatch.setattr(analysis_router.clock, "iso_now_kst", lambda: now_iso)
    monkeypatch.setattr(
        paid_runtime, "_reserve_run_slot", lambda _track, _bucket: "dart-slot"
    )
    released: list[str] = []
    monkeypatch.setattr(paid_runtime, "_release_run_slot", released.append)
    with storage_db.connect() as conn:
        for _ in range(provider_health_constants.FAILURES_TO_OPEN):
            provider_health_store.record_failure(
                conn,
                provider_health_constants.PROVIDER_DART,
                failure_kind=provider_health_store.ProviderFailureKind.TIMEOUT,
                now_iso=now_iso,
            )

    provider = FreeDartProvider()
    outcome = asyncio.run(
        analysis_router._resolve_business_candidates(
            RequestFixture(),  # type: ignore[arg-type]
            provider=provider,
            user_input=UserInput("JYP", "매니지먼트", "서울", "채용 공고"),
            resolved_track=(object(), "fixture-bucket", None),  # type: ignore[arg-type]
            allow_paid_provider=False,
            analysis_run_id="dart-cooldown-run",
        )
    )

    assert isinstance(outcome, tuple)
    result, cost_krw = outcome
    assert result.status is ResolutionStatus.RATE_LIMITED
    assert result.provider_called is False
    assert provider.calls == 0
    assert cost_krw == 0.0
    assert released == ["dart-slot"]
    # 실제 호출부가 하듯 관측 함수를 지나도 provider 미호출은 실패로 세지 않는다.
    analysis_router._observe_candidate_resolution(result)
    with storage_db.connect() as conn:
        state = provider_health_store.get_state(
            conn, provider_health_constants.PROVIDER_DART
        )
        event_count = conn.execute(
            f"SELECT COUNT(*) FROM {provider_health_store.TABLE_EVENTS}"
        ).fetchone()[0]
    assert state.state is provider_health_store.ProviderHealthState.OPEN
    assert state.version == provider_health_constants.FAILURES_TO_OPEN
    assert event_count == provider_health_constants.FAILURES_TO_OPEN


def test_DART_local_rate제한이면_만료된_probe권한도_미리잡지않는다(
    monkeypatch, tmp_path
):
    from src.features.business_candidate import logic as candidate_logic

    class FreeDartProvider:
        costs_money = False
        provider_name = "DART"

        def __init__(self):
            self.calls = 0

        def search(self, **_kwargs):
            self.calls += 1
            raise AssertionError("로컬 rate 제한 뒤 provider를 호출하면 안 됩니다")

    class RequestFixture:
        client = type("Client", (), {"host": "127.0.0.1"})()

    opened_at = "2026-08-28T10:00:00+09:00"
    after_cooldown = "2026-08-28T10:01:01+09:00"
    monkeypatch.setenv("STORAGE_DB_PATH", str(tmp_path / "storage.db"))
    monkeypatch.setattr(
        analysis_router.clock, "iso_now_kst", lambda: after_cooldown
    )
    monkeypatch.setattr(candidate_logic, "_claim_rate", lambda *_args: False)
    monkeypatch.setattr(
        paid_runtime, "_reserve_run_slot", lambda _track, _bucket: "dart-rate-slot"
    )
    monkeypatch.setattr(paid_runtime, "_release_run_slot", lambda _slot: None)
    with storage_db.connect() as conn:
        for _ in range(provider_health_constants.FAILURES_TO_OPEN):
            provider_health_store.record_failure(
                conn,
                provider_health_constants.PROVIDER_DART,
                failure_kind=provider_health_store.ProviderFailureKind.TIMEOUT,
                now_iso=opened_at,
            )

    provider = FreeDartProvider()
    outcome = asyncio.run(
        analysis_router._resolve_business_candidates(
            RequestFixture(),  # type: ignore[arg-type]
            provider=provider,
            user_input=UserInput("JYP", "매니지먼트", "서울", "채용 공고"),
            resolved_track=(object(), "fixture-bucket", None),  # type: ignore[arg-type]
            allow_paid_provider=False,
            analysis_run_id="dart-local-rate-run",
        )
    )

    assert isinstance(outcome, tuple)
    result, _cost_krw = outcome
    assert result.status is ResolutionStatus.RATE_LIMITED
    assert result.provider_called is False
    assert provider.calls == 0
    with storage_db.connect() as conn:
        state = provider_health_store.get_state(
            conn, provider_health_constants.PROVIDER_DART
        )
        event_count = conn.execute(
            f"SELECT COUNT(*) FROM {provider_health_store.TABLE_EVENTS}"
        ).fetchone()[0]
    assert state.state is provider_health_store.ProviderHealthState.OPEN
    assert event_count == provider_health_constants.FAILURES_TO_OPEN


def test_DART_cooldown뒤_탐색하나와_그결과관측하나만_기록한다(
    monkeypatch, tmp_path
):
    class FreeDartProvider:
        costs_money = False
        provider_name = "DART"

        def __init__(self):
            self.calls = 0

        def search(self, **_kwargs):
            self.calls += 1
            return []

    class RequestFixture:
        client = type("Client", (), {"host": "127.0.0.1"})()

    opened_at = "2026-08-28T10:00:00+09:00"
    after_cooldown = "2026-08-28T10:01:01+09:00"
    monkeypatch.setenv("STORAGE_DB_PATH", str(tmp_path / "storage.db"))
    monkeypatch.setattr(
        analysis_router.clock, "iso_now_kst", lambda: after_cooldown
    )
    monkeypatch.setattr(
        paid_runtime, "_reserve_run_slot", lambda _track, _bucket: "dart-probe-slot"
    )
    released: list[str] = []
    monkeypatch.setattr(paid_runtime, "_release_run_slot", released.append)
    with storage_db.connect() as conn:
        for _ in range(provider_health_constants.FAILURES_TO_OPEN):
            provider_health_store.record_failure(
                conn,
                provider_health_constants.PROVIDER_DART,
                failure_kind=provider_health_store.ProviderFailureKind.TIMEOUT,
                now_iso=opened_at,
            )

    provider = FreeDartProvider()
    outcome = asyncio.run(
        analysis_router._resolve_business_candidates(
            RequestFixture(),  # type: ignore[arg-type]
            provider=provider,
            user_input=UserInput("JYP", "매니지먼트", "서울", "채용 공고"),
            resolved_track=(object(), "fixture-bucket", None),  # type: ignore[arg-type]
            allow_paid_provider=False,
            analysis_run_id="dart-probe-run",
        )
    )

    assert isinstance(outcome, tuple)
    result, cost_krw = outcome
    assert result.status is ResolutionStatus.NO_MATCHES
    assert result.provider_called is True
    assert provider.calls == 1
    assert cost_krw == 0.0
    assert released == ["dart-probe-slot"]
    analysis_router._observe_candidate_resolution(result)
    with storage_db.connect() as conn:
        state = provider_health_store.get_state(
            conn, provider_health_constants.PROVIDER_DART
        )
        event_kinds = [
            str(row[0])
            for row in conn.execute(
                f"SELECT event_kind FROM {provider_health_store.TABLE_EVENTS} "
                "ORDER BY id"
            ).fetchall()
        ]
    assert state.state is provider_health_store.ProviderHealthState.HEALTHY
    assert event_kinds == ["failure", "failure", "probe_acquired", "success"]


def test_점검429는_실제_confirm에서_DART후보조회보다_먼저_막는다(monkeypatch, tmp_path):
    monkeypatch.setenv("STORAGE_DB_PATH", str(tmp_path / "storage.db"))
    monkeypatch.setenv(evaluation_mode.ENV_MODE, "1")
    monkeypatch.setenv(evaluation_mode.ENV_PAID_PROVIDERS, "1")
    monkeypatch.setattr(request_helpers, "_strict_loopback_http_request", lambda _r: True)
    pipeline = CandidateAwareFakeRealPipeline()
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)
    with storage_db.connect() as conn:
        dashboard_store.set_service_state(
            conn,
            status=dashboard_store.SERVICE_MAINTENANCE,
            cause="후보 공급자 점검",
            impact="새 분석을 멈췄습니다.",
            next_action="수정 뒤 관리자 재가동",
            actor_email="admin@example.com",
            now_iso="2026-08-22T10:00:00+09:00",
        )

    client, csrf = _admin_client()
    try:
        workflow_id = _hidden(client.get("/").text, "evaluation_workflow_id")
        response = client.post(
            "/confirm",
            data=_form(
                csrf,
                evaluation_paid_consent="yes",
                evaluation_workflow_id=workflow_id,
            ),
        )
    finally:
        client.close()

    assert response.status_code == 429
    assert response.headers["X-Company-Analysis-Block"] == "service-maintenance"
    assert pipeline.search_calls == 0
    assert pipeline.lookup_calls == 0


def test_살아있는_LINK에서는_별칭후보와_서명된후보선택을_허용한다(monkeypatch):
    raw_key = "b1b2c3d4e5f60718b1b2c3d4e5f60718"
    pipeline = CandidateAwareFakeRealPipeline(local_candidates=True)
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)
    with storage_db.connect() as conn:
        assert share_store.insert_new(
            conn,
            key=raw_key,
            company="카카오",
            job="마케팅",
            now_iso="2026-08-21T10:00:00+09:00",
        )
    client = TestClient(main.app, base_url="https://testserver")
    try:
        client.get(f"/k/{raw_key}")
        csrf = auth_logic.csrf_token_for_session(raw_key)
        body = client.post("/confirm", data=_form(csrf)).text
        selected = client.post(
            "/confirm",
            data=_form(
                csrf,
                candidate_resolution_confirmed="yes",
                candidate_attempt_token=_hidden(body, "candidate_attempt_token"),
                candidate_selection_token=_hidden(body, "candidate_selection_token"),
                candidate_index=_hidden(body, "candidate_index"),
                candidate_name=_hidden(body, "candidate_name"),
                candidate_provider=_hidden(body, "candidate_provider"),
                candidate_ref=_hidden(body, "candidate_ref"),
            ),
        )
        assert selected.status_code == 200
        assert pipeline.lookup_calls == 1
        assert pipeline.search_calls == 1
        assert pipeline.lookup_inputs == ["JYP"]
        assert pipeline.lookup_refs == ["00258689"]
    finally:
        client.close()


def test_회사이름을_바꾸면_옛평가동의를_재사용하지_않고_다시_받는다(monkeypatch):
    pipeline = CandidateAwareFakeRealPipeline(local_candidates=False)
    google = PaidGoogleFixture()
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)
    monkeypatch.setenv(evaluation_mode.ENV_MODE, "1")
    monkeypatch.setenv(evaluation_mode.ENV_PAID_PROVIDERS, "1")
    monkeypatch.setattr(request_helpers, "_strict_loopback_http_request", lambda _r: True)
    from src.features.business_candidate import providers

    monkeypatch.setattr(providers, "configured_provider", lambda _p, **_k: google)
    client, csrf = _admin_client()
    try:
        workflow_id = _hidden(client.get("/").text, "evaluation_workflow_id")
        first = client.post(
            "/confirm",
            data=_form(
                csrf,
                evaluation_paid_consent="yes",
                evaluation_workflow_id=workflow_id,
            ),
        )
        assert first.status_code == 200
        first_grant = _hidden(first.text, "evaluation_consent_grant")
        assert 'name="evaluation_paid_consent" value="yes" required' in first.text

        no_reconsent = client.post(
            "/confirm",
            data=_form(csrf, company="JYPe", retry="1"),
        )
        assert no_reconsent.status_code == 422
        assert google.calls == 0

        retried = client.post(
            "/confirm",
            data=_form(
                csrf,
                company="JYPe",
                retry="1",
                evaluation_paid_consent="yes",
                evaluation_workflow_id=_hidden(
                    first.text, "evaluation_workflow_id"
                ),
            ),
        )
        assert retried.status_code == 200
        second_grant = _hidden(retried.text, "evaluation_consent_grant")
        assert second_grant != first_grant
        assert google.calls == 0
    finally:
        client.close()


def test_Google검색중_요청취소뒤_같은_grant는_재호출과_재과금하지_않는다(monkeypatch):
    class BlockingGoogle(PaidGoogleFixture):
        def __init__(self):
            super().__init__()
            self.started = threading.Event()
            self.finish = threading.Event()

        def search(self, **_kwargs):
            self.calls += 1
            self.started.set()
            assert self.finish.wait(3), "시험 worker 종료 신호를 받지 못했습니다"
            return []

    pipeline = CandidateAwareFakeRealPipeline(local_candidates=False)
    google = BlockingGoogle()
    monkeypatch.setattr(runtime, "_PIPELINE", pipeline)
    monkeypatch.setenv(evaluation_mode.ENV_MODE, "1")
    monkeypatch.setenv(evaluation_mode.ENV_PAID_PROVIDERS, "1")
    monkeypatch.setattr(request_helpers, "_strict_loopback_http_request", lambda _r: True)
    from src.features.business_candidate import providers

    monkeypatch.setattr(providers, "configured_provider", lambda _p, **_k: google)
    client, csrf = _admin_client()
    workflow_id = _hidden(client.get("/").text, "evaluation_workflow_id")
    first = client.post(
        "/confirm",
        data=_form(
            csrf,
            evaluation_paid_consent="yes",
            evaluation_workflow_id=workflow_id,
        ),
    )
    assert first.status_code == 200
    search_grant = _hidden(first.text, "candidate_search_grant")
    consent_grant = _hidden(first.text, "evaluation_consent_grant")
    session_cookie = client.cookies.get(auth_constants.SESSION_COOKIE_NAME)
    client.close()
    search_form = _form(
        csrf,
        candidate_search_requested="yes",
        candidate_search_grant=search_grant,
        evaluation_consent_grant=consent_grant,
    )

    async def scenario() -> None:
        transport = httpx.ASGITransport(app=main.app, client=("127.0.0.1", 43123))
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://127.0.0.1:8000",
            headers={"Origin": "http://127.0.0.1:8000"},
            cookies={auth_constants.SESSION_COOKIE_NAME: session_cookie},
        ) as async_client:
            task = asyncio.create_task(async_client.post("/confirm", data=search_form))
            assert await asyncio.to_thread(google.started.wait, 1)
            task.cancel()
            google.finish.set()
            with pytest.raises(asyncio.CancelledError):
                await task

            grant = job_runtime._CANDIDATE_SEARCH_GRANTS[search_grant]
            assert grant.in_flight is False
            assert grant.resolution_status == "failed"
            assert not public_ids._RESERVED_IDS

            replay = await async_client.post("/confirm", data=search_form)
            assert replay.status_code == 200
            assert "이번에는 사용하지 못했습니다" in replay.text
            assert "외부 후보 검색으로 넘어가지 않았" not in replay.text
            assert "외부 후보 검색도 시작하지 않았" not in replay.text
            assert "완료된 외부 조회 비용은 비용 원장에 기록될 수" in replay.text
            assert _hidden(replay.text, "retry") == "1"
            assert google.calls == 1

    asyncio.run(scenario())
