from __future__ import annotations

import importlib
from pathlib import Path
from types import SimpleNamespace

from features.provider_diagnostics.logic import (
    build_usage_diagnostic,
    safe_stop_reason,
)


def test_알려진_종료코드만_보존한다() -> None:
    assert safe_stop_reason("max_tokens") == "max_tokens"
    assert safe_stop_reason("사용자 원문이 섞인 임의 문자열") == "unknown"


def test_출력상한과_파싱실패가_겹치면_절단의심으로_분류한다() -> None:
    diagnostic = build_usage_diagnostic(
        stop_reason="max_tokens",
        output_tokens=3000,
        requested_max_tokens=3000,
        parse_failed=True,
    )

    assert diagnostic == {
        "stop_reason": "max_tokens",
        "requested_max_tokens": 3000,
        "output_limit_reached": True,
        "truncation_suspected": True,
        "parse_failed": True,
    }


def test_정상종료는_출력상한_절단으로_분류하지_않는다() -> None:
    diagnostic = build_usage_diagnostic(
        stop_reason="end_turn",
        output_tokens=2999,
        requested_max_tokens=3000,
    )

    assert diagnostic["output_limit_reached"] is False
    assert diagnostic["truncation_suspected"] is False
    assert diagnostic["parse_failed"] is False


def test_run_pilot_ask는_깨진_상한응답의_진단만_남긴다(monkeypatch) -> None:
    project_root = Path(__file__).resolve().parents[4]
    monkeypatch.syspath_prepend(str(project_root))
    run_pilot = importlib.import_module("tools.run_pilot")
    monkeypatch.setattr(run_pilot, "_spent_usd", 0.0)
    response = SimpleNamespace(
        model="가짜모델",
        stop_reason="max_tokens",
        usage=SimpleNamespace(input_tokens=2, output_tokens=3000),
        content=[SimpleNamespace(text="{")],
    )
    client = SimpleNamespace(
        messages=SimpleNamespace(create=lambda **_kwargs: response)
    )

    payload, usage = run_pilot._ask(
        client,
        "시험 프롬프트",
        {},
        max_tokens=3000,
    )

    assert payload is None
    assert usage["stop_reason"] == "max_tokens"
    assert usage["output_limit_reached"] is True
    assert usage["truncation_suspected"] is True
    assert usage["parse_failed"] is True
    assert "시험 프롬프트" not in str(usage)
    assert "content" not in usage
