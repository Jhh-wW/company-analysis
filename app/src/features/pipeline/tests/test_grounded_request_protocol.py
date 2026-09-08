"""뉴스 grounded 구조화 요청의 실제 schema 전송 경계를 검증한다."""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from typing import Any

import anthropic
import pytest

from src.core.provider_gateway import attempt_context
from src.features.budget import provider_budget
from src.features.news_intake.grounded import build_grounded_schema
from src.features.news_intake.models import NewsCandidate
from src.features.pipeline import real
from src.features.pipeline.tests.test_request_metering import (
    FakeMessages,
    _AttemptRecorder,
)


def _synthetic_candidates(batch_size: int) -> list[NewsCandidate]:
    return [
        NewsCandidate(
            id=f"candidate-{index}",
            title=f"Synthetic news title {index}",
            description="",
            originallink=f"https://example.test/original/{index}",
            link=f"https://example.test/search/{index}",
            published_on="2026-09-08",
            publisher="Synthetic Publisher",
            priority=1,
            source_url=f"https://example.test/article/{index}",
        )
        for index in range(batch_size)
    ]


def _key_paths(value: object, keys: set[str], path: str = "$") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key in keys:
                found.append(child_path)
            found.extend(_key_paths(child, keys, child_path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_key_paths(child, keys, f"{path}[{index}]"))
    return found


def _object_contracts(
    value: object,
    path: str = "$",
    found: dict[str, tuple[tuple[str, ...], object]] | None = None,
) -> dict[str, tuple[tuple[str, ...], object]]:
    if found is None:
        found = {}
    if isinstance(value, dict):
        if value.get("type") == "object":
            properties = value.get("properties")
            required = value.get("required")
            assert isinstance(properties, dict)
            assert isinstance(required, list)
            found[path] = (tuple(required), value.get("additionalProperties"))
            for key, child in properties.items():
                _object_contracts(child, f"{path}.properties.{key}", found)
        for key, child in value.items():
            if key != "properties":
                _object_contracts(child, f"{path}.{key}", found)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _object_contracts(child, f"{path}[{index}]", found)
    return found


class _CaptureMessages(FakeMessages):
    def create(self, **kwargs: Any) -> Any:
        response = super().create(**kwargs)
        # 오프라인에서는 응답만 종료 조건으로 제공하고 AI 품질은 검증하지 않는다.
        response.content = (SimpleNamespace(text='{"items": []}'),)
        response.stop_reason = "end_turn"
        return response


@pytest.fixture
def _provider_context():
    recorder = _AttemptRecorder()
    with provider_budget.activate(100_000.0), attempt_context.activate(
        recorder.callbacks()
    ):
        yield recorder


@pytest.mark.parametrize("batch_size", [1, 2, 4])
def test_grounded_schema_matches_sdk_normalization_without_mutating_source(
    batch_size: int,
) -> None:
    candidates = _synthetic_candidates(batch_size)
    source_schema = build_grounded_schema(
        [(candidate, "synthetic article body") for candidate in candidates]
    )
    original_schema = deepcopy(source_schema)

    normalized = real._provider_output_config(
        {"format": {"type": "json_schema", "schema": source_schema}}
    )
    sent_schema = normalized["format"]["schema"]

    assert source_schema == original_schema
    assert sent_schema == anthropic.transform_schema(deepcopy(source_schema))
    assert _key_paths(source_schema, {"minLength", "maxLength", "maxItems"})
    assert not _key_paths(sent_schema, {"minLength", "maxLength", "maxItems"})
    assert _object_contracts(sent_schema) == _object_contracts(source_schema)

    # minItems=1은 SDK가 지원하므로 batch 1에서 제거되지 않는 것을 허용한다.
    sent_items = sent_schema["properties"]["items"]
    assert sent_items.get("minItems") in (None, 0, 1)


def test_run_pilot_ask_captures_normalized_grounded_request(
    _provider_context,
) -> None:
    candidates = _synthetic_candidates(4)
    source_schema = build_grounded_schema(
        [(candidate, "synthetic article body") for candidate in candidates]
    )
    original_schema = deepcopy(source_schema)
    engine = real._engine()
    metered = real._MeteredEngine(engine)
    messages = _CaptureMessages()
    client = real._metered_client(metered, SimpleNamespace(messages=messages))

    # 실제 run_pilot._ask와 _MeteredMessages를 호출하되 네트워크는 사용하지 않는다.
    payload, _usage = engine._ask(
        client,
        "synthetic grounded protocol test",
        source_schema,
        max_tokens=5000,
    )

    assert payload == {"items": []}
    assert source_schema == original_schema
    assert len(messages.requests) == 1
    request = messages.requests[0]
    sent_schema = request["output_config"]["format"]["schema"]
    expected_schema = real._provider_output_config(
        {"format": {"type": "json_schema", "schema": source_schema}}
    )["format"]["schema"]
    assert sent_schema == expected_schema
    assert sent_schema == anthropic.transform_schema(deepcopy(source_schema))
    assert not _key_paths(sent_schema, {"minLength", "maxLength", "maxItems"})
    assert request["model"] == engine.MODEL
    assert request["max_tokens"] == 5000
    assert request["temperature"] == 0
    assert request["output_config"]["format"]["type"] == "json_schema"
