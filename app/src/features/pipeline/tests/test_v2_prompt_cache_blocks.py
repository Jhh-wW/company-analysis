# -*- coding: utf-8 -*-
"""작가 호출이 «공유 앞부분만» 캐시 블록으로 나눠 보내는지 못 박는다.

★ 왜 이 파일이 생겼나 (2026-09-05 실측, 회사 하이브)
  ─────────────────────────────────────────────────────────
  작가(v2_compose) 호출 6번까지 685원을 쓰고 요청 예약액이 떨어져 죽었다.
  호출당 입력이 70,367→75,494 토큰이었는데 `cache_read_input_tokens`는
  «전부 0»이었다 — 아홉 장이 같은 자료 조각을 통째로 다시 보내면서도 캐시가
  한 번도 맞지 않았다.

★ 이 시험이 지키는 것
  ① 표식이 실린 프롬프트는 두 블록으로 나뉘고 «앞부분에만» 캐시 표식이 붙는다
  ② 두 블록을 이으면 원래 프롬프트와 글자 하나까지 같다  ← 내용 변조 금지
  ③ 표식이 없으면(평범한 str·재시도 프롬프트) 예전처럼 통짜로 보낸다
  ④ 이미 나뉜 블록을 계량 경계가 «다시 한 덩어리로» 감싸지 않는다
     (감싸면 매 호출 cache write만 나고 read가 0이 된다 — 실측과 같은 결말)
  ⑤ 실제 아홉 장 호출에서 첫 블록이 «바이트 동일»하다  ← 캐시가 맞는 유일한 조건
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from src.core.provider_gateway import attempt_context
from src.core.provider_gateway.attempt_context import ProviderAttemptCallbacks
from src.features.budget import provider_budget
from src.features.composer.constants import RETRY_REMINDER, SECTION_IDS
from src.features.composer.logic import (
    CacheablePrompt,
    build_section_prompt,
    compose_sections,
)
from src.features.composer.port import CollectedFragment
from src.features.pipeline import real


_EPHEMERAL = {"type": "ephemeral"}
_COMPANY = "가나다전자(주)"
#: 빈 문장 목록은 «정상 파싱»이라 재요청이 일어나지 않는다 — 호출 수가 장 수와 같다.
_EMPTY_SECTION_RESPONSE = json.dumps({"문장들": []}, ensure_ascii=False)


@pytest.fixture(autouse=True)
def _유료_예약문맥():
    """직접 시험도 웹 worker와 같은 요청별 예약·시도 문맥에서 실행한다."""

    callbacks = ProviderAttemptCallbacks(
        lambda _provider, _operation, _reserved: object(),
        lambda _token: None,
        lambda _token: None,
        lambda _token, _observation: None,
    )
    with provider_budget.activate(100_000.0), attempt_context.activate(callbacks):
        yield


class _RecordingMessages:
    """네트워크 없이 provider 경계를 흉내 내고 보낸 요청·캐시 표식을 기록한다."""

    def __init__(self, *, response_text: str = "") -> None:
        self._metered: Any = None
        self._response_text = response_text
        self.requests: list[dict[str, Any]] = []
        #: 호출 «시점»의 계량 엔진 캐시 표식 — 나중에 읽으면 문맥이 이미 닫힌다.
        self.cache_flags: list[bool] = []
        self.counted_messages: list[Any] = []

    def attach(self, metered: Any) -> None:
        self._metered = metered

    def create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        self.cache_flags.append(bool(self._metered.prompt_cache_enabled))
        return SimpleNamespace(
            model=kwargs["model"],
            content=[SimpleNamespace(text=self._response_text)],
            usage=SimpleNamespace(
                input_tokens=1_000,
                output_tokens=100,
                cache_creation_input_tokens=0,
                cache_read_input_tokens=0,
            ),
        )

    def count_tokens(self, **kwargs: Any) -> Any:
        # 계수 경로가 블록 목록을 그대로 받는지도 함께 본다(SDK TextBlockParam은
        # cache_control을 허용한다 — 벗겨서 보낼 이유가 없다).
        self.counted_messages.append(kwargs.get("messages"))
        return SimpleNamespace(input_tokens=1_234)


class _FakeRawEngine:
    MODEL = "claude-haiku-4-5"

    def __init__(self, messages: _RecordingMessages) -> None:
        self.client = SimpleNamespace(messages=messages)

    def _client(self) -> Any:
        return self.client


def _ask_and_messages(
    *, response_text: str = ""
) -> tuple[Any, _RecordingMessages, Any]:
    """진짜 계량 client 경계를 지나는 v2 ask를 만든다(가짜 provider만 끼운다)."""

    messages = _RecordingMessages(response_text=response_text)
    raw = _FakeRawEngine(messages)
    metered = real._MeteredEngine(raw)
    messages.attach(metered)
    client = real._metered_client(metered, raw._client())
    ask = real._v2_ask_via_provider(
        metered,
        client,
        stage="v2_compose",
        max_tokens=real.V2_WRITER_MAX_TOKENS,
    )
    return ask, messages, metered


def _fragments() -> tuple[CollectedFragment, ...]:
    return (
        CollectedFragment(
            fragment_id="1",
            kind="홈페이지",
            text="가나다전자는 2003년에 설립된 부품 제조사다.",
            source_url="https://example.com/about",
        ),
        CollectedFragment(
            fragment_id="2",
            kind="공식IR",
            text="가나다전자는 2025년 반기보고서에서 매출 구성을 공시했다.",
            document_title="반기보고서",
        ),
    )


def _content(messages: _RecordingMessages, index: int = 0) -> Any:
    return messages.requests[index]["messages"][0]["content"]


# ══════════════════════════════════════════════════════════
# ① 표식이 실린 프롬프트 → 두 블록, 앞부분에만 캐시 표식
# ══════════════════════════════════════════════════════════


def test_공유앞부분이_있으면_두_블록으로_나눠_보낸다():
    prompt = CacheablePrompt("앞부분입니다.뒷부분입니다.", cache_prefix_chars=7)
    ask, messages, _ = _ask_and_messages()

    ask(prompt)

    content = _content(messages)
    assert content == [
        {"type": "text", "text": "앞부분입니다.", "cache_control": _EPHEMERAL},
        {"type": "text", "text": "뒷부분입니다."},
    ]
    # 두 블록을 이으면 원래 프롬프트와 같다 — 내용은 한 글자도 안 바뀐다.
    assert "".join(block["text"] for block in content) == str(prompt)
    assert messages.cache_flags == [True], "계량 단계가 캐시 모드로 열려야 한다"


def test_계수_경로도_블록_목록을_그대로_받는다():
    prompt = CacheablePrompt("앞부분입니다.뒷부분입니다.", cache_prefix_chars=7)
    ask, messages, _ = _ask_and_messages()

    ask(prompt)

    counted = messages.counted_messages[0]
    assert counted == messages.requests[0]["messages"]
    assert counted[0]["content"][0]["cache_control"] == _EPHEMERAL


# ══════════════════════════════════════════════════════════
# ② 표식이 없으면 예전 그대로 통짜 문자열
# ══════════════════════════════════════════════════════════


def test_평범한_문자열은_통짜로_보낸다():
    ask, messages, _ = _ask_and_messages()

    ask("프롬프트 본문")

    assert _content(messages) == "프롬프트 본문"
    assert messages.cache_flags == [False]


def test_재시도로_이어붙인_프롬프트는_표식을_잃고_통짜로_간다():
    prompt = CacheablePrompt("앞부분입니다.뒷부분입니다.", cache_prefix_chars=7)
    이어붙임 = prompt + RETRY_REMINDER
    ask, messages, _ = _ask_and_messages()

    ask(이어붙임)

    assert not isinstance(이어붙임, CacheablePrompt)
    assert _content(messages) == 이어붙임
    assert messages.cache_flags == [False]


@pytest.mark.parametrize(
    "prefix_chars",
    [0, len("앞부분만 있습니다.")],
    ids=["경계가_0", "뒷부분이_빈다"],
)
def test_나눌_수_없는_경계는_통짜로_되돌린다(prefix_chars: int):
    text = "앞부분만 있습니다."
    prompt = CacheablePrompt(text, cache_prefix_chars=prefix_chars)
    ask, messages, _ = _ask_and_messages()

    ask(prompt)

    assert _content(messages) == text
    assert messages.cache_flags == [False]


# ══════════════════════════════════════════════════════════
# ③ 계량 경계가 이미 나뉜 블록을 다시 감싸지 않는다
# ══════════════════════════════════════════════════════════


def test_이미_나뉜_블록은_그대로_통과한다():
    blocks = [
        {"type": "text", "text": "공유 앞부분", "cache_control": _EPHEMERAL},
        {"type": "text", "text": "장별 뒷부분"},
    ]
    원본 = [{"role": "user", "content": blocks}]

    결과 = real._prompt_cached_messages(원본)

    assert 결과 == 원본
    # 블록 목록 «객체 자체»를 다시 감싸지 않는다 — 감싸면 앞부분이 뒤에 섞인다.
    assert 결과[0]["content"] is blocks


def test_문자열_content는_예전처럼_한_블록으로_감싼다():
    결과 = real._prompt_cached_messages(
        [{"role": "user", "content": "같은 선택 프롬프트"}]
    )

    assert 결과 == [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": "같은 선택 프롬프트",
                    "cache_control": _EPHEMERAL,
                }
            ],
        }
    ]


def test_표식_없는_블록_목록은_예전처럼_막는다():
    with pytest.raises(provider_budget.ProviderBudgetUnavailable):
        real._prompt_cached_messages(
            [{"role": "user", "content": [{"type": "text", "text": "표식 없음"}]}]
        )


def test_모양이_어긋난_블록_목록도_막는다():
    블록 = {"type": "image", "cache_control": _EPHEMERAL}

    with pytest.raises(provider_budget.ProviderBudgetUnavailable):
        real._prompt_cached_messages([{"role": "user", "content": [블록]}])


# ══════════════════════════════════════════════════════════
# ④ 상시 검사 — 아홉 장 호출의 첫 블록이 바이트 동일한가
# ══════════════════════════════════════════════════════════


def test_아홉_장_호출의_첫_블록은_바이트_동일하다():
    """앞부분이 «실제로» 공유되지 않으면 캐시는 한 번도 맞지 않는다."""

    ask, messages, _ = _ask_and_messages(response_text=_EMPTY_SECTION_RESPONSE)

    compose_sections(_COMPANY, _fragments(), None, ask)

    assert len(messages.requests) == len(SECTION_IDS)
    보낸_블록들 = [_content(messages, index) for index in range(len(SECTION_IDS))]

    첫_블록들 = {블록[0]["text"] for 블록 in 보낸_블록들}
    assert len(첫_블록들) == 1, "장마다 앞부분이 다르면 캐시가 맞지 않는다"
    뒷_블록들 = {블록[1]["text"] for 블록 in 보낸_블록들}
    assert len(뒷_블록들) == len(SECTION_IDS), "장별 지시는 장마다 달라야 한다"
    assert all(블록[0]["cache_control"] == _EPHEMERAL for 블록 in 보낸_블록들)
    assert all("cache_control" not in 블록[1] for 블록 in 보낸_블록들)
    assert messages.cache_flags == [True] * len(SECTION_IDS)


def test_보낸_두_블록을_이으면_composer가_만든_프롬프트와_같다():
    ask, messages, _ = _ask_and_messages(response_text=_EMPTY_SECTION_RESPONSE)

    compose_sections(_COMPANY, _fragments(), None, ask)

    기대 = build_section_prompt(
        _COMPANY,
        SECTION_IDS[0],
        _fragments(),
        None,
        (),
        shared_evidence_prefix=True,
    )
    보낸_것 = "".join(block["text"] for block in _content(messages, 0))
    assert 보낸_것 == str(기대)
