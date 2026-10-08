"""최초 검수만 긴 대기를 쓰며 전송·계량·불명 비용 계약을 유지한다."""
from __future__ import annotations

import ast
import inspect
import json
from types import SimpleNamespace

import anthropic
import httpx
import pytest

from src.core import constants
from src.core.provider_gateway import attempt_context
from src.core.provider_gateway.attempt_context import ProviderAttemptCallbacks
from src.features.composer import verify
from src.features.composer.port import AskFatalError
from src.features.pipeline import real
from src.features.pipeline.tests import test_native_review_retry_integration as retry_fixture
from src.features.pipeline.tests import test_v2_native_schema_metering as native


@pytest.fixture
def attempts():
    reservations, observations = [], []
    callbacks = ProviderAttemptCallbacks(
        lambda provider, stage, reserved: reservations.append((provider, stage, reserved)) or len(reservations),
        lambda _: None, lambda _: None,
        lambda _, observation: observations.append(observation),
    )
    with attempt_context.activate(callbacks):
        yield reservations, observations


def test_설치SDK에서_시간만바꾸며_계수_본문_비용_모델은동일하다(attempts):
    requests = []

    def handle(request):
        requests.append((request.url.path, json.loads(request.content), request.extensions['timeout']))
        if request.url.path.endswith('/count_tokens'):
            return httpx.Response(200, json={'input_tokens': native.COUNTED_INPUT})
        return httpx.Response(200, json=native.response_body())

    with anthropic.Anthropic(
        api_key='offline-test', max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handle)),
    ) as sdk, real.provider_budget.activate(2000):
        engine = real._MeteredEngine(SimpleNamespace(MODEL='claude-haiku-4-5'))
        client = real._metered_client(engine, sdk)
        assert client._client.max_retries == 0
        for timeout in (None, constants.INITIAL_REVIEW_TIMEOUT_SEC):
            ask = real._v2_ask_via_provider(
                engine, client, stage='v2_review', max_tokens=24000, timeout_sec=timeout,
            )
            assert ask('같은 검수 원문') == '{"판정": []}'
    counted = [row for row in requests if row[0].endswith('/count_tokens')]
    sent = [row for row in requests if not row[0].endswith('/count_tokens')]
    assert counted[0][1] == counted[1][1]
    assert sent[0][1] == sent[1][1]
    assert sent[0][2] == dict.fromkeys(('connect', 'read', 'write', 'pool'), 180.0)
    assert sent[1][2] == dict.fromkeys(('connect', 'read', 'write', 'pool'), 600.0)
    assert counted[1][2]['read'] == 180.0
    assert sent[1][1]['model'] == real.V2_REVIEW_MODEL
    assert sent[1][1]['max_tokens'] == 24000
    assert not sent[1][1].get('stream')
    reservations, observations = attempts
    assert reservations[0] == reservations[1]
    assert len(observations) == len(engine.usages) == engine._provider_dispatch_count == 2
    assert engine.usages[0]['cost_krw'] == engine.usages[1]['cost_krw']


def test_긴검수도_timeout뒤재전송없이_불명비용과원예외를보존한다(attempts):
    sends = []

    def handle(request):
        if request.url.path.endswith('/count_tokens'):
            return httpx.Response(200, json={'input_tokens': native.COUNTED_INPUT})
        sends.append(request)
        raise httpx.ReadTimeout('시험용 읽기 대기 만료', request=request)

    with anthropic.Anthropic(
        api_key='offline-test', max_retries=0,
        http_client=httpx.Client(transport=httpx.MockTransport(handle)),
    ) as sdk, real.provider_budget.activate(2000) as budget:
        engine = real._MeteredEngine(SimpleNamespace(MODEL=native.MODEL))
        ask = real._v2_ask_via_provider(
            engine, real._metered_client(engine, sdk), stage='v2_review', max_tokens=24000,
            timeout_sec=600.0,
        )
        with pytest.raises(AskFatalError) as caught:
            ask('검수 원문')
        sdk_error = caught.value.cause.__cause__
        assert isinstance(sdk_error, anthropic.APITimeoutError)
        assert isinstance(sdk_error.__cause__, httpx.ReadTimeout)
        assert engine.billing_uncertain
        assert budget.accounted_krw > 0
        with pytest.raises(AskFatalError):
            ask('재전송 금지')
    assert len(sends) == len(attempts[0]) == len(attempts[1]) == 1
    assert sends[0].extensions['timeout']['read'] == 600.0
    assert attempts[1][0].billing_disposition.value == 'CONSERVATIVE_LIABILITY'


@pytest.mark.parametrize('grouped', (False, True))
@pytest.mark.parametrize('first', (retry_fixture.BROKEN, '{"판정": []}'))
def test_flat과grouped는_형식또는누락후속을합쳐_긴검수두번만한다(attempts, grouped, first):
    messages = retry_fixture.ScriptedMessages()
    messages.replies = [(first, 7000), (retry_fixture.BROKEN, 7000)]
    engine = real._MeteredEngine(SimpleNamespace(MODEL=native.MODEL))
    client = real._metered_client(engine, SimpleNamespace(messages=messages))
    initial = real._v2_ask_via_provider(
        engine, client, stage='v2_review', max_tokens=24000, timeout_sec=600.0,
    )
    retry = real._v2_ask_via_provider(
        engine, client, stage='v2_review',
        max_tokens=lambda: real._initial_review_retry_max_tokens(engine), timeout_sec=600.0,
    )
    collector = real.run_diagnostics.begin_run()
    try:
        with real.provider_budget.activate(2000):
            if grouped:
                result = verify._ask_grouped_verdicts(
                    initial, retry_fixture.GROUPED_ITEMS, retry_fixture.FRAGMENTS, None,
                    initial_ask=initial, initial_retry_ask=retry,
                )
            else:
                result = verify._ask_verdicts(
                    initial, retry_fixture.FLAT_ITEMS, retry_fixture.FRAGMENTS, '',
                    initial_ask=initial, initial_retry_ask=retry,
                )
    finally:
        collector.finish()
    assert result in (None, {})
    assert [request['timeout'] for request in messages.requests] == [600.0, 600.0]
    assert [request['max_tokens'] for request in messages.requests] == [24000, 12000]


def test_생산배선은_최초두클로저에만_시간옵션을지정한다():
    function = ast.parse(inspect.getsource(real._run_v2_composer)).body[0]
    extended = []
    for node in ast.walk(function):
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Call):
            call = node.value
            if isinstance(call.func, ast.Name) and call.func.id == '_v2_ask_via_provider':
                options = {kw.arg: kw.value for kw in call.keywords}
                if 'timeout_sec' in options:
                    assert isinstance(options['timeout_sec'], ast.Name)
                    assert options['timeout_sec'].id == 'INITIAL_REVIEW_TIMEOUT_SEC'
                    extended.extend(target.id for target in node.targets if isinstance(target, ast.Name))
    assert sorted(extended) == ['initial_retry_reviewer_ask', 'initial_reviewer_ask']
    assert constants.INITIAL_REVIEW_MAX_CALLS == verify.PARSE_RETRY_LIMIT + 1 == 2


def test_유료lease_소유권_작업_공개접근권한은같은시간정본을쓴다():
    from src.features.budget.constants import PAID_PHASE_LEASE_SEC
    from src.web import generation_singleflight, job_runtime
    assert constants.REPORT_PROVIDER_WAIT_MAX_SEC == 4440
    assert PAID_PHASE_LEASE_SEC == constants.REPORT_GENERATION_EXECUTION_MAX_SEC == 5400
    assert generation_singleflight.PROVIDER_IN_FLIGHT_GRACE.total_seconds() == 660
    assert generation_singleflight.OWNER_PROVIDER_ADMISSION_AGE.total_seconds() == 4740
    assert job_runtime._JOB_EXECUTION_MAX_SEC == 5400
