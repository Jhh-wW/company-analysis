"""실제 SDK의 닫힌 오류 진단과 뉴스 단계의 원문 비노출을 검증한다."""
from __future__ import annotations

import json
from types import SimpleNamespace
import unittest

import anthropic
import httpx

from src.features.pipeline import constants
from src.features.pipeline.provider_error_diagnostics import safe_provider_error_metadata


def synthetic_error(message, api_type="invalid_request_error", status=400):
    request = httpx.Request("POST", "https://offline.invalid/v1/messages")
    body = {"type": "error", "error": {"type": api_type, "message": message}}
    response = httpx.Response(status, request=request, json=body)
    return anthropic.BadRequestError("합성 예외 문자열", response=response, body=body)


class ProviderErrorDiagnosticTests(unittest.TestCase):
    def test_sdk_type_and_exact_schema_message(self):
        meta = safe_provider_error_metadata(synthetic_error(constants.PROVIDER_SCHEMA_COMPLEXITY_MESSAGE))
        self.assertEqual(meta, {"provider_api_error_type": "invalid_request_error", "provider_error_category": "schema_too_complex"})

    def test_missing_body_stays_unknown(self):
        error = synthetic_error("합성 오류")
        error.body = None
        self.assertEqual(safe_provider_error_metadata(error)["provider_error_category"], "unknown")

    def test_embedded_phrase_is_not_classified(self):
        message = "합성 원문 " + constants.PROVIDER_SCHEMA_COMPLEXITY_MESSAGE
        self.assertEqual(safe_provider_error_metadata(synthetic_error(message))["provider_error_category"], "unknown")

    def test_unknown_type_never_leaks(self):
        marker = "SENSITIVE_FIXTURE_TYPE"
        result = safe_provider_error_metadata(synthetic_error(marker, marker))
        self.assertNotIn(marker, json.dumps(result))
        self.assertEqual(result["provider_api_error_type"], "unknown")

    def test_body_and_extra_fields_never_serialize(self):
        marker = "SENSITIVE_FIXTURE_BODY"
        error = synthetic_error(marker)
        error.body["extra"] = {"authorization": marker, "prompt": marker}
        result = safe_provider_error_metadata(error)
        self.assertNotIn(marker, json.dumps(result))
        self.assertEqual(set(result), {"provider_api_error_type", "provider_error_category"})

    def test_malformed_fields_stay_unknown(self):
        for value in (None, [], "합성 본문", 123):
            result = safe_provider_error_metadata(SimpleNamespace(type=[], body=value))
            self.assertEqual(result["provider_api_error_type"], "unknown")
            self.assertEqual(result["provider_error_category"], "unknown")

    def test_oversized_message_stays_unknown(self):
        error = synthetic_error("가" * (constants.PROVIDER_ERROR_MESSAGE_MAX_CHARS + 1))
        self.assertEqual(safe_provider_error_metadata(error)["provider_error_category"], "unknown")

    def test_mismatched_error_type_stays_unknown(self):
        result = safe_provider_error_metadata(synthetic_error(constants.PROVIDER_SCHEMA_COMPLEXITY_MESSAGE, "authentication_error"))
        self.assertEqual(result["provider_api_error_type"], "authentication_error")
        self.assertEqual(result["provider_error_category"], "unknown")

    def test_documented_spend_limits_through_sdk_http_and_gateway(self):
        from src.core.provider_gateway import gateway
        from src.core.provider_gateway.anthropic_adapter import AnthropicAdapter

        cases = [
            (400, "invalid_request_error", constants.PROVIDER_SCHEMA_COMPLEXITY_MESSAGE, None, "schema_too_complex"),
            (400, "invalid_request_error", constants.PROVIDER_SPEND_LIMIT_PREFIXES[0][0] + ". 합성 재개일", None, "workspace_spend_limit"),
            (400, "invalid_request_error", constants.PROVIDER_SPEND_LIMIT_PREFIXES[1][0] + ": 합성 재개일", None, "organization_spend_limit"),
            (429, "rate_limit_error", "합성 재개일", constants.PROVIDER_ENFORCED_SPEND_LIMIT_CODE, "organization_monthly_spend_cap"),
            (400, "invalid_request_error", "공식 근거가 없는 합성 오류", None, "unknown"),
        ]
        for flat in (False, True):
            for status, api_type, message, detail_code, category in cases:
                with self.subTest(flat=flat, category=category):
                    detail = {"type": api_type, "message": message}
                    if detail_code:
                        detail["details"] = {"error_code": detail_code}
                    wire_body = detail if flat else {"type": "error", "error": detail}
                    seen = []
                    observations = []

                    def handler(request):
                        seen.append(request.url.path)
                        return httpx.Response(status, json=wire_body, headers={"request-id": "req_fixture0000000001"})

                    with anthropic.Anthropic(api_key="offline-fixture-no-real-key", base_url="https://offline.invalid", max_retries=0,
                                             http_client=httpx.Client(transport=httpx.MockTransport(handler), trust_env=False)) as client:
                        with self.assertRaises(gateway.ProviderCallFailed) as caught:
                            gateway.call_once(adapter=AnthropicAdapter(lambda value: None), reserved_krw=94.41,
                                              before_dispatch=lambda: None, record_observation=observations.append,
                                              send=lambda: client.messages.create(model="claude-haiku-4-5", max_tokens=1,
                                                                                  messages=[{"role": "user", "content": "합성"}]))
                    cause = caught.exception.__cause__
                    self.assertEqual(cause.body, wire_body)
                    self.assertEqual(cause.type, None if flat else api_type)
                    self.assertIsInstance(cause, anthropic.BadRequestError if status == 400 else anthropic.RateLimitError)
                    self.assertEqual(safe_provider_error_metadata(cause), {"provider_api_error_type": api_type, "provider_error_category": category})
                    self.assertEqual(seen, ["/v1/messages"])
                    self.assertEqual(observations[0].liability_krw, 94.41)
                    self.assertIs(caught.exception.observation, observations[0])

    def test_unrecognized_prefix_and_status_do_not_classify(self):
        prefix = constants.PROVIDER_SPEND_LIMIT_PREFIXES[1][0]
        for message, status in (("합성 원문 " + prefix, 400), (prefix + "x", 400), (prefix, 401),
                                (prefix + " " + "가" * constants.PROVIDER_ERROR_MESSAGE_MAX_CHARS, 400)):
            self.assertEqual(safe_provider_error_metadata(synthetic_error(message, status=status))["provider_error_category"], "unknown")

    def test_unknown_detail_code_and_malformed_nested_body_do_not_classify(self):
        error = synthetic_error("합성", "rate_limit_error", status=429)
        error.body["error"]["details"] = {"error_code": "SENSITIVE_FIXTURE_DETAIL"}
        self.assertEqual(safe_provider_error_metadata(error)["provider_error_category"], "unknown")
        error.body = {"error": [], "type": "invalid_request_error", "message": constants.PROVIDER_SCHEMA_COMPLEXITY_MESSAGE}
        error.type = None
        self.assertEqual(safe_provider_error_metadata(error)["provider_api_error_type"], "unknown")

    def test_proposed_news_wiring_preserves_safe_json_and_cause(self):
        from src.features.pipeline import real
        from src.core.provider_gateway import gateway
        from src.core.provider_gateway.anthropic_adapter import AnthropicAdapter
        from src.features.observability.run_steps_store import _encode

        cause = synthetic_error(constants.PROVIDER_SCHEMA_COMPLEXITY_MESSAGE)
        cause.body["extra"] = "SENSITIVE_FIXTURE_BODY"
        observation = AnthropicAdapter(lambda value: None).failure(cause, reserved_krw=94.41)
        failure = gateway.ProviderCallFailed(observation)
        failure.__cause__ = cause

        class Session:
            snapshot = SimpleNamespace(query_attempts=())

            def collect(self, **kwargs):
                raise failure

        steps = []
        with self.assertRaises(gateway.ProviderCallFailed) as caught:
            real._collect_grounded_news(session=Session(), analyze=lambda *args: None,
                                               fetch_text=lambda url: "", corp_id="fixture", official_web_documents=1,
                                               collected_on="2026-09-08", steps=steps)
        self.assertIs(caught.exception, failure)
        encoded, count, omitted = _encode(steps)
        self.assertEqual((count, omitted), (1, 0))
        stored = json.loads(encoded)[0]
        self.assertEqual(stored["provider_api_error_type"], "invalid_request_error")
        self.assertEqual(stored["provider_error_category"], "schema_too_complex")
        self.assertEqual(stored["provider_liability_krw"], 94.41)
        self.assertEqual(stored["provider_billing"], "CONSERVATIVE_LIABILITY")
        self.assertNotIn("SENSITIVE_FIXTURE_BODY", encoded)
        self.assertNotIn(constants.PROVIDER_SCHEMA_COMPLEXITY_MESSAGE, encoded)


