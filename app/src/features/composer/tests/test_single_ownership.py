"""같은 사실이 여러 장에 반복되는 것을 막는 장치를 못 박는다.

★ 왜 이 시험이 있나 (실측 결함) — compose_sections가 9개 장을 «각각 독립»으로
  호출하면서 매번 조각 «전체»를 다시 줬다. 각 장은 다른 장이 무엇을 썼는지
  몰라 눈에 띄는 사실로 전부 몰렸다. 현대자동차 실측에서 「금융 커버리지
  15개국·80%」가 7개 장에, 「Boston Dynamics」가 6개 장에 나왔다.
★ 여기서 지키는 것:
  ① 장은 순서대로 쓰고, 앞 장이 쓴 문장이 뒤 장 프롬프트에 실린다.
  ② 첫 장에는 그 블록이 없다 (프롬프트를 괜히 늘리지 않는다).
  ③ 목록에 상한이 있어 프롬프트가 무한정 길어지지 않는다.
  ④ 이것은 «지침»이지 게이트가 아니다 — 문장을 사후에 지우지 않는다.
"""

from __future__ import annotations

import json

from src.features.composer.constants import (
    ALREADY_WRITTEN_GUIDE,
    ALREADY_WRITTEN_HEAD,
    ALREADY_WRITTEN_MAX_SENTENCES,
    SECTION_IDS,
)
from src.features.composer.logic import build_section_prompt, compose_sections
from src.features.composer.port import CollectedFragment


def _fragments() -> tuple[CollectedFragment, ...]:
    return (
        CollectedFragment(
            fragment_id="1",
            kind="사업내용",
            text="가나다전자는 반도체 검사 장비를 만든다.",
        ),
    )


class _RecordingAsk:
    """작가 호출을 가로채 프롬프트를 모으고, 장마다 다른 문장을 돌려준다."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        order = len(self.prompts)
        return json.dumps(
            {
                "문장들": [
                    {
                        "글": f"{order}번째 장에서만 말하는 사실이다.",
                        "인용": ["1"],
                        "등급": "확인",
                    }
                ]
            },
            ensure_ascii=False,
        )


# ══════════════════════════════════════════════════════════
# ① 앞 장이 쓴 문장이 뒤 장 프롬프트에 실린다
# ══════════════════════════════════════════════════════════


def test_뒤_장_프롬프트에_앞_장_문장이_실린다():
    ask = _RecordingAsk()

    compose_sections("가나다전자(주)", _fragments(), None, ask)

    assert len(ask.prompts) == len(SECTION_IDS)
    마지막_프롬프트 = ask.prompts[-1]
    assert ALREADY_WRITTEN_HEAD.strip() in 마지막_프롬프트
    # 앞 8개 장이 쓴 문장이 전부 보여야 한다.
    for order in range(1, len(SECTION_IDS)):
        assert f"{order}번째 장에서만 말하는 사실이다." in 마지막_프롬프트


def test_두번째_장부터_지침이_붙는다():
    ask = _RecordingAsk()

    compose_sections("가나다전자(주)", _fragments(), None, ask)

    assert ALREADY_WRITTEN_GUIDE.strip() in ask.prompts[1]


# ══════════════════════════════════════════════════════════
# ② 첫 장에는 블록이 없다
# ══════════════════════════════════════════════════════════


def test_첫_장에는_앞_장_블록이_없다():
    ask = _RecordingAsk()

    compose_sections("가나다전자(주)", _fragments(), None, ask)

    assert ALREADY_WRITTEN_HEAD.strip() not in ask.prompts[0]
    assert ALREADY_WRITTEN_GUIDE.strip() not in ask.prompts[0]


def test_빈_목록이면_블록을_넣지_않는다():
    prompt = build_section_prompt("가나다전자(주)", "identity", _fragments(), None, ())

    assert ALREADY_WRITTEN_HEAD.strip() not in prompt


def test_공백만_있는_문장은_목록에_넣지_않는다():
    prompt = build_section_prompt(
        "가나다전자(주)", "identity", _fragments(), None, ("", "   ", "\n")
    )

    assert ALREADY_WRITTEN_HEAD.strip() not in prompt


# ══════════════════════════════════════════════════════════
# ③ 목록 상한 — 프롬프트가 무한정 길어지지 않는다
# ══════════════════════════════════════════════════════════


def test_앞_장_문장_목록에_상한이_있다():
    넘치는_문장 = tuple(
        f"{index}번 문장이다." for index in range(ALREADY_WRITTEN_MAX_SENTENCES + 30)
    )

    prompt = build_section_prompt(
        "가나다전자(주)", "identity", _fragments(), None, 넘치는_문장
    )

    실린_개수 = sum(1 for 문장 in 넘치는_문장 if f"- {문장}" in prompt)
    assert 실린_개수 == ALREADY_WRITTEN_MAX_SENTENCES


# ══════════════════════════════════════════════════════════
# ④ 지침이지 게이트가 아니다 — 문장을 사후에 지우지 않는다
# ══════════════════════════════════════════════════════════


def test_같은_문장을_또_써도_지우지_않는다():
    """모델이 지침을 어겨도 파이프라인이 문장을 삭제하면 안 된다.

    삭제는 장 삭제·문장 대량 거절로 이어졌던 예전 실패 방식이다.
    여기서는 «알려 주기»까지만 하고 판단은 검증 단계(verify.py)에 맡긴다.
    """
    같은_문장 = "모든 장이 똑같이 쓰는 문장이다."

    def ask(_prompt: str) -> str:
        return json.dumps(
            {"문장들": [{"글": 같은_문장, "인용": ["1"], "등급": "확인"}]},
            ensure_ascii=False,
        )

    report = compose_sections("가나다전자(주)", _fragments(), None, ask)

    assert len(report.sections) == len(SECTION_IDS)
    for section in report.sections:
        assert [sentence.text for sentence in section.sentences] == [같은_문장]


# ══════════════════════════════════════════════════════════
# ⑤ 장 순서는 정본 목차 순서 그대로다
# ══════════════════════════════════════════════════════════


def test_장은_정본_목차_순서대로_작성된다():
    ask = _RecordingAsk()

    report = compose_sections("가나다전자(주)", _fragments(), None, ask)

    assert [section.section_id for section in report.sections] == list(SECTION_IDS)
