"""프롬프트 원문을 바꾸지 않고 검수의 고정 접두부 경계를 운반한다."""

from __future__ import annotations

import os

from src.features.composer.prompt_cache_constants import (
    REVIEW_PROMPT_CACHE_DEFAULT_ENABLED,
    REVIEW_PROMPT_CACHE_ENABLED_VALUES,
    REVIEW_PROMPT_CACHE_ENV,
)


class PromptMetadata(str):
    """뒷문구 추가는 경계를 보존하고 앞문구 추가는 캐시를 해제한다.

    새 앞문구에는 개별 근거가 있을 수 있으므로 기존 고정 지침의 캐시
    범위를 임의로 늘리지 않는다. 자르기·형 변환은 기본 str 동작을 따른다.
    """

    cache_prefix_chars: int

    def __new__(
        cls, value: str, *, cache_prefix_chars: int | None = None,
    ) -> PromptMetadata:
        if cache_prefix_chars is None:
            cache_prefix_chars = getattr(value, "cache_prefix_chars", 0)
        if type(cache_prefix_chars) is not int:
            raise TypeError("캐시 접두부 길이는 정수여야 합니다")
        if not 0 <= cache_prefix_chars <= len(value):
            raise ValueError("캐시 접두부 길이가 프롬프트 범위를 벗어났습니다")
        prompt = super().__new__(cls, value)
        prompt.cache_prefix_chars = cache_prefix_chars
        return prompt

    def _with_text(self, value: str, *, cache_prefix_chars: int) -> PromptMetadata:
        return PromptMetadata(value, cache_prefix_chars=cache_prefix_chars)

    def __add__(self, suffix: str) -> PromptMetadata:
        return self._with_text(
            super().__add__(suffix), cache_prefix_chars=self.cache_prefix_chars,
        )

    def __radd__(self, prefix: str) -> PromptMetadata:
        return self._with_text(
            str.__add__(prefix, self),
            cache_prefix_chars=0 if prefix else self.cache_prefix_chars,
        )


def with_review_prompt_cache(value: str, *, fixed_prefix_chars: int) -> str:
    """설정이 켜졌을 때만 기존 문자열에 캐시 표식을 붙인다.

    경계는 builder가 개별 장·후보·근거를 붙이기 직전에 계산한다.
    지침의 모드가 달라지면 실제 접두부도 달라져 서로 다른 캐시가 된다.
    """
    configured = os.environ.get(REVIEW_PROMPT_CACHE_ENV)
    enabled = (
        REVIEW_PROMPT_CACHE_DEFAULT_ENABLED if configured is None
        else configured.strip().casefold() in REVIEW_PROMPT_CACHE_ENABLED_VALUES
    )
    if not enabled:
        return value
    return PromptMetadata(value, cache_prefix_chars=fixed_prefix_chars)
