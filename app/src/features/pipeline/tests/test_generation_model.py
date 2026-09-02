"""8·9 생성만 «큰 모델»로 올린 것이 안전한지 못 박는다.

★ 왜 필요한가 — 작은 모델(haiku)은 후보 60~70줄 중 **2~3개만** 고르고 지시문의
  금지 조항도 어긴다. 실측(조건 고정·모델만 교체): 4축 문장 **0 → 16개**.

★ 이 시험이 지키는 것 두 가지 — 둘 다 «돈이 새는» 사고와 직결된다.
  ① **모델이 반드시 되돌아간다.** 안 되돌리면 뒤따르는 알맹이 검사까지 비싼
     모델로 돌아 조용히 돈이 샌다. 예외가 나도 되돌아가야 한다.
  ② 쓸 수 있다고 적은 모델은 공통 단가표에도 반드시 있어야 한다.

요청별 실제 비용·동시성은 `test_request_metering.py`가 client 응답 경계에서 지킨다.
"""

from __future__ import annotations

import pytest

from src.core.constants import (
    GENERATION_MODEL,
    MODEL_PRICES_USD_PER_MTOK,
    SAMPLING_OK_MODELS,
)
from src.features.spanselect.logic import select_spans

_기본모델 = "claude-haiku-4-5"
#: 1판이 모든 호출에 붙이는 값. 이걸 받는 세대여야 쓸 수 있다.
_TEMPERATURE_인자 = "temperature"


class _가짜엔진:
    """`select_spans`가 부르는 이름만 갖고 있다. AI는 안 부른다."""

    MODEL = _기본모델
    BLOCK_ORDER = ("1", "4-1", "5")
    GEN_MAX_TOKENS = 3000
    _spent_usd = 0.0

    def __init__(self, 터뜨릴까: bool = False) -> None:
        self.불렀을_때_모델 = ""
        self.터뜨릴까 = 터뜨릴까

    def split_sentences(self, text: str) -> list[str]:
        return [s.strip() for s in text.split(".") if s.strip()]

    def _ask(self, client, prompt, schema, max_tokens=0):
        # ★ 부르는 «그 순간»의 모델을 기록한다 — 교체가 실제로 걸렸는지 본다.
        self.불렀을_때_모델 = self.MODEL
        if self.터뜨릴까:
            raise RuntimeError("AI 호출이 터졌다")
        return {"items": []}, {"in": 1000, "out": 100, "usd": 0.0015}

    def check_draft(self, items, originals, requirements):
        from types import SimpleNamespace

        return SimpleNamespace(kept=list(items), deleted=[])

    class DraftItem:                                   # noqa: D106
        def __init__(self, sentence="", fragment_id=None, block="") -> None:
            self.sentence, self.fragment_id, self.block = sentence, fragment_id, block


_조각 = {1: {"종류": "사업내용", "원문": "회사는 장비를 만들어 판다."}}


def _돌린다(engine, model: str = ""):
    return select_spans(
        "가짜클라이언트", _조각, [], "직무", [], engine=engine, model=model
    )


# ══════════════════════════════════════════════════════════
# ① 모델 교체 — 걸리는가, 그리고 반드시 되돌아가는가
# ══════════════════════════════════════════════════════════


def test_생성만_큰_모델로_부른다():
    engine = _가짜엔진()

    _돌린다(engine, GENERATION_MODEL)

    assert engine.불렀을_때_모델 == GENERATION_MODEL


def test_부른_뒤에는_반드시_원래_모델로_되돌아간다():
    """★ 안 되돌리면 뒤따르는 알맹이 검사까지 비싼 모델로 돈다 — 돈이 샌다."""
    engine = _가짜엔진()

    _돌린다(engine, GENERATION_MODEL)

    assert engine.MODEL == _기본모델


def test_호출이_터져도_모델은_되돌아간다():
    """★ 예외 경로가 더 위험하다 — 아무도 안 보는 사이에 비싼 모델이 남는다."""
    engine = _가짜엔진(터뜨릴까=True)

    with pytest.raises(RuntimeError):
        _돌린다(engine, GENERATION_MODEL)

    assert engine.MODEL == _기본모델


def test_모델을_안_주면_엔진_기본값을_그대로_쓴다():
    engine = _가짜엔진()

    _돌린다(engine)

    assert engine.불렀을_때_모델 == _기본모델
    assert engine.MODEL == _기본모델


def test_생성_모델은_sampling_값을_받는_세대여야_한다():
    """★ 1판이 `temperature=0`을 보낸다 — 최신 세대는 그걸 400으로 거부한다.

    실측: `claude-sonnet-5`로 부르면 `BadRequestError`(0원 실패).
    1판은 수정 금지이므로 **sampling을 받는 세대만** 쓸 수 있다.
    """
    assert GENERATION_MODEL in SAMPLING_OK_MODELS, (
        f"{GENERATION_MODEL}은 `{_TEMPERATURE_인자}`를 거부할 수 있습니다 — "
        "1판 `_ask`를 감싸 그 인자를 빼기 전에는 쓸 수 없습니다"
    )


def test_쓸_수_있다고_적은_모델은_전부_단가가_있다():
    """★ 두 목록이 어긋나면 «조용히» 비용이 3배로 세어진다.

    「쓸 수 있는 세대」 목록과 단가표는 서로 다른 곳에 있다. 한쪽에만 모델을
    추가하면 아무 오류도 안 나고, 대신 «모르는 모델» 단가(15/75)가 적용돼
    비용 지표·대시보드·예산가드가 전부 부풀어 오른다.
    실제로 `claude-opus-4-6`이 「쓸 수 있다」 쪽에만 있고 단가표에 없었다.
    """
    빠진_것 = [m for m in SAMPLING_OK_MODELS if m not in MODEL_PRICES_USD_PER_MTOK]

    assert not 빠진_것, (
        f"쓸 수 있다고 적어 놓고 단가가 없는 모델: {빠진_것} — "
        "`MODEL_PRICES_USD_PER_MTOK`에 추가하세요"
    )

