"""작가 프롬프트의 «공유 앞부분»을 못 박는다 — 프롬프트 캐시가 맞으려면.

★ 왜 이 시험이 있나 (2026-09-05 운영 실측)
  하이브 보고서에서 작가 호출 6번까지 685원을 쓰고 예산(900원)에 걸려 죽었다.
  호출당 입력이 70,367→75,494 토큰이었고 `cache_read_tokens`는 «전부 0»이었다.
  이유: 가장 큰 조각 블록이 프롬프트 «맨 뒤»에 있어서 장마다 앞부분이 달랐다.
  프롬프트 캐시는 앞부분이 바이트 단위로 완전히 같을 때만 맞는다.

★ 이 시험이 지키는 것
  ① 기본값(False)은 예전 문자열을 «바이트 그대로» 돌려준다 — 기존 호출자 보호.
  ② True면 아홉 장의 앞부분이 서로 바이트 동일하다 — 캐시가 맞는 유일한 조건.
  ③ 순서만 바뀌고 글자는 한 자도 바뀌지 않는다.
  ④ flat 모드에서만 켜진다 — packet 모드는 장마다 조각이 달라 공유분이 없다.
"""

from __future__ import annotations

import json
from collections import Counter

import pytest

from src.features.composer.constants import (
    CITATION_RULES_GUIDE,
    CLAIM_SLOTS_BY_SECTION,
    COMPETITIVE_POSITION_PARAGRAPH_PLAN,
    FLOW_PROMPT_BY_SECTION,
    FORBIDDEN_TOPICS_GUIDE,
    JSON_SCHEMA_GUIDE,
    MAX_INTERPRETED_SENTENCES_PER_SECTION,
    PROMPT_HEADER,
    RETRY_REMINDER,
    SECTION_GUIDES,
    SECTION_IDS,
    SECTION_SENTENCE_RANGES,
    SENTENCE_RANGE_GUIDE,
)
from src.features.composer.logic import (
    CacheablePrompt,
    _render_already_written,
    _render_fragments,
    _render_table,
    build_section_prompt,
    compose_sections,
)
from src.features.composer.port import (
    CollectedFragment,
    PerformanceTable,
    SectionEvidencePacket,
    SectionEvidencePacketSet,
)
from src.shared.report_generation.models import exact_text_sha256


_COMPANY = "가나다전자(주)"
_SECTION = "identity"
_ALREADY_WRITTEN = ("앞 장이 이미 쓴 문장이다.", "두 번째로 쓴 문장이다.")


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
        CollectedFragment(
            fragment_id="3",
            kind="공식IR",
            text="가나다전자는 신규 라인 증설 계획을 공식 발표했다.",
            document_title="사업보고서",
        ),
    )


def _table() -> PerformanceTable:
    return PerformanceTable(
        caption="최근 3개년 실적",
        headers=("연도", "매출"),
        rows=(("2023", "100"), ("2024", "120")),
        unit="억원",
    )


#: ★ 변경 «전» 조립 순서를 시험 안에서 직접 재현하기 위한 조각.
#:   build_section_prompt 안에서 그 자리에서 만들어지는 유일한 블록이라
#:   이름으로 참조할 수 없어 글자를 그대로 옮겨 적었다(리터럴 기준값).
_LEGACY_CLAIM_PLAN_HEAD = (
    "\n원자 주장 계획 — 각 문장은 가장 알맞은 id를 «주장슬롯»에 넣고, "
    "id는 고유 번호가 아니라 사실의 종류다. 같은 종류의 서로 다른 원자 "
    "사실에는 같은 id를 다시 써도 되지만, 같은 사실을 말만 바꿔 반복하지 "
    "않는다. 어느 자리에도 맞지 않으면 빈 문자열로 두며 새 id를 만들지 "
    "않는다:\n- "
)


def _legacy_claim_slot_guide(section_id: str) -> str:
    """flat 모드(show_supported_claim_slots=False)의 주장슬롯 안내 재현."""
    claim_slots = CLAIM_SLOTS_BY_SECTION.get(section_id, ())
    if not claim_slots:
        return ""
    return _LEGACY_CLAIM_PLAN_HEAD + "\n- ".join(claim_slots) + "\n"


def _legacy_prompt(
    company_name: str,
    section_id: str,
    fragments: tuple[CollectedFragment, ...],
    performance_table: PerformanceTable | None,
    already_written: tuple[str, ...],
) -> str:
    """이 변경 «전»의 블록 순서 — 조각 블록이 맨 뒤에 있던 모양."""
    minimum, maximum = SECTION_SENTENCE_RANGES[section_id]
    return "".join(
        [
            PROMPT_HEADER.format(company=company_name),
            "\n",
            SECTION_GUIDES[section_id],
            (
                COMPETITIVE_POSITION_PARAGRAPH_PLAN
                if section_id == "competitive_position"
                else ""
            ),
            "\n\n",
            CITATION_RULES_GUIDE,
            FORBIDDEN_TOPICS_GUIDE,
            SENTENCE_RANGE_GUIDE.format(
                minimum=minimum,
                maximum=maximum,
                interpretation_cap=MAX_INTERPRETED_SENTENCES_PER_SECTION,
            ),
            _legacy_claim_slot_guide(section_id),
            FLOW_PROMPT_BY_SECTION.get(section_id, JSON_SCHEMA_GUIDE),
            _render_table(performance_table),
            _render_already_written(already_written),
            _render_fragments(fragments),
        ]
    )


# ══════════════════════════════════════════════════════════
# ① 기본값은 예전과 바이트가 같다
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize("section_id", SECTION_IDS)
@pytest.mark.parametrize("with_table", [False, True])
def test_기본값은_기존_프롬프트와_바이트가_같다(section_id: str, with_table: bool):
    table = _table() if with_table else None

    prompt = build_section_prompt(
        _COMPANY,
        section_id,
        _fragments(),
        table,
        _ALREADY_WRITTEN,
    )

    assert prompt == _legacy_prompt(
        _COMPANY, section_id, _fragments(), table, _ALREADY_WRITTEN
    )
    # 기본 경로는 캐시 표식을 달지 않는다 — 켜지 않았는데 켜지면 안 된다.
    assert not isinstance(prompt, CacheablePrompt)
    assert getattr(prompt, "cache_prefix_chars", 0) == 0


def test_기본값은_앞_장_문장이_없어도_예전과_같다():
    prompt = build_section_prompt(_COMPANY, _SECTION, _fragments(), None, ())

    assert prompt == _legacy_prompt(_COMPANY, _SECTION, _fragments(), None, ())


# ══════════════════════════════════════════════════════════
# ② 공유 앞부분은 아홉 장에서 바이트 동일하다 (캐시가 맞는 유일한 조건)
# ══════════════════════════════════════════════════════════


def _shared_prompts() -> dict[str, CacheablePrompt]:
    """아홉 장을 실제 flat 호출과 같은 조건으로 만든다 — 장마다 다른 재료를 준다."""
    prompts: dict[str, CacheablePrompt] = {}
    for index, section_id in enumerate(SECTION_IDS):
        prompt = build_section_prompt(
            _COMPANY,
            section_id,
            _fragments(),
            # 실적표는 장마다 있고 없고가 갈린다 — 앞부분이 이에 흔들리면 안 된다.
            _table() if index % 2 == 0 else None,
            # 앞 장 문장은 뒤로 갈수록 늘어난다 — 이것도 앞부분에 닿으면 안 된다.
            tuple(f"{n}번째 장이 쓴 문장이다." for n in range(index)),
            shared_evidence_prefix=True,
        )
        assert isinstance(prompt, CacheablePrompt)
        prompts[section_id] = prompt
    return prompts


def test_공유앞부분은_장과_무관하게_바이트동일하다():
    prompts = _shared_prompts()

    prefixes = {
        prompt[: prompt.cache_prefix_chars] for prompt in prompts.values()
    }

    assert len(prefixes) == 1, "장마다 앞부분이 다르면 캐시는 한 번도 맞지 않는다"
    prefix = prefixes.pop()
    assert prefix.startswith(PROMPT_HEADER.format(company=_COMPANY))
    for fragment in _fragments():
        assert fragment.text in prefix
    assert len(prompts) == len(SECTION_IDS)


def test_공유앞부분은_장별_재료에_의존하지_않는다():
    prompts = _shared_prompts()

    # 장별 지시·앞 장 문장·실적표는 전부 앞부분 «밖»에 있어야 한다.
    for section_id, prompt in prompts.items():
        head = prompt[: prompt.cache_prefix_chars]
        assert SECTION_GUIDES[section_id] not in head
        assert CITATION_RULES_GUIDE not in head
        assert _table().caption not in head
        assert "번째 장이 쓴 문장이다." not in head


def test_공유앞부분_길이는_회사와_조각으로만_정해진다():
    적은_조각 = _fragments()[:1]

    많은_쪽 = build_section_prompt(
        _COMPANY, _SECTION, _fragments(), None, (), shared_evidence_prefix=True
    )
    적은_쪽 = build_section_prompt(
        _COMPANY, _SECTION, 적은_조각, None, (), shared_evidence_prefix=True
    )

    assert 많은_쪽.cache_prefix_chars > 적은_쪽.cache_prefix_chars


# ══════════════════════════════════════════════════════════
# ③ 뒷부분에는 장별 지시가 전부 살아 있다 (순서만 바뀐다)
# ══════════════════════════════════════════════════════════


@pytest.mark.parametrize("section_id", SECTION_IDS)
def test_뒷부분에는_장별_지시가_모두_있다(section_id: str):
    앞_장_문장 = "앞 장이 이미 쓴 문장이다."

    prompt = build_section_prompt(
        _COMPANY,
        section_id,
        _fragments(),
        _table(),
        (앞_장_문장,),
        shared_evidence_prefix=True,
    )

    head = prompt[: prompt.cache_prefix_chars]
    rest = prompt[prompt.cache_prefix_chars :]
    assert rest.startswith(SECTION_GUIDES[section_id])
    for 블록 in (
        SECTION_GUIDES[section_id],
        CITATION_RULES_GUIDE,
        FORBIDDEN_TOPICS_GUIDE,
        FLOW_PROMPT_BY_SECTION.get(section_id, JSON_SCHEMA_GUIDE),
        앞_장_문장,
        _table().caption,
    ):
        assert 블록 in rest
        assert 블록 not in head


@pytest.mark.parametrize("section_id", SECTION_IDS)
def test_공유앞부분은_글자를_바꾸지_않고_순서만_바꾼다(section_id: str):
    옛_모양 = build_section_prompt(
        _COMPANY, section_id, _fragments(), _table(), _ALREADY_WRITTEN
    )
    새_모양 = build_section_prompt(
        _COMPANY,
        section_id,
        _fragments(),
        _table(),
        _ALREADY_WRITTEN,
        shared_evidence_prefix=True,
    )

    # 늘어난 것은 앞부분과 장별 지시를 가르는 줄바꿈 «하나»뿐이다.
    assert len(새_모양) == len(옛_모양) + 1
    assert Counter(새_모양) == Counter(옛_모양 + "\n")


# ══════════════════════════════════════════════════════════
# ④ CacheablePrompt는 그냥 str이다 (기존 경로가 아무것도 몰라도 된다)
# ══════════════════════════════════════════════════════════


def test_CacheablePrompt는_str처럼_행동한다():
    prompt = build_section_prompt(
        _COMPANY, _SECTION, _fragments(), None, (), shared_evidence_prefix=True
    )

    assert isinstance(prompt, str)
    assert str(prompt) == prompt
    assert exact_text_sha256(prompt) == exact_text_sha256(str(prompt))
    # 이어 붙이면 표식이 사라진다 — 의도된 동작(재시도는 캐시를 포기한다).
    이어붙임 = prompt + "x"
    assert isinstance(이어붙임, str)
    assert not isinstance(이어붙임, CacheablePrompt)
    assert getattr(prompt + RETRY_REMINDER, "cache_prefix_chars", 0) == 0


def test_앞뒤로_잘라_이어_붙이면_원래_프롬프트다():
    """real.py 배선 계약 — 앞부분만 cache_control 블록으로 보내도 내용이 같다."""
    prompt = build_section_prompt(
        _COMPANY, _SECTION, _fragments(), None, (), shared_evidence_prefix=True
    )

    경계 = getattr(prompt, "cache_prefix_chars", 0)

    assert 경계 > 0
    head, rest = prompt[:경계], prompt[경계:]
    assert head + rest == str(prompt)
    # 잘라 낸 조각은 평범한 str이라 provider 호출부가 그대로 실어 보낼 수 있다.
    assert type(head) is str
    assert type(rest) is str


def test_CacheablePrompt는_경계값을_검증한다():
    with pytest.raises(ValueError):
        CacheablePrompt("짧은 글", cache_prefix_chars=100)
    with pytest.raises(ValueError):
        CacheablePrompt("짧은 글", cache_prefix_chars=-1)


# ══════════════════════════════════════════════════════════
# ⑤ compose_sections는 flat 모드에서만 켠다
# ══════════════════════════════════════════════════════════


class _RecordingAsk:
    """받은 prompt 객체를 «형까지» 그대로 보관한다 — str 변환하면 표식이 죽는다."""

    def __init__(self) -> None:
        self.prompts: list[str] = []

    def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        # 빈 문장 목록은 «정상 파싱»이라 재요청이 일어나지 않는다.
        return json.dumps({"문장들": []}, ensure_ascii=False)


def _packet_set() -> SectionEvidencePacketSet:
    """packet(FULL) 모드 재료 — 장마다 조각이 다르다(공유 앞부분이 없다)."""
    generation = "a" * 64
    packets = tuple(
        SectionEvidencePacket(
            company_id="00123456",
            evidence_generation_sha256=generation,
            section_id=section_id,
            fragments=(
                CollectedFragment(
                    fragment_id=str(index),
                    kind="typed-evidence-v1:test",
                    text=f"테스트 회사의 {section_id} 공식 원문이다.",
                    source_url=f"https://example.com/documents/{index}",
                    document_identity=f"document:example.com:doc-{index}",
                    document_content_sha256=f"{index:064x}",
                    supported_claim_slots=(CLAIM_SLOTS_BY_SECTION[section_id][0],),
                ),
            ),
        )
        for index, section_id in enumerate(SECTION_IDS, start=1)
    )
    return SectionEvidencePacketSet(
        company_id="00123456",
        evidence_generation_sha256=generation,
        packets=packets,
    )


def test_compose_sections는_flat모드에서만_공유앞부분을_켠다():
    flat_ask = _RecordingAsk()
    packet_ask = _RecordingAsk()

    compose_sections(_COMPANY, _fragments(), _table(), flat_ask)
    compose_sections(
        _COMPANY,
        (),
        None,
        packet_ask,
        section_evidence_packets=_packet_set(),
    )

    assert len(flat_ask.prompts) == len(SECTION_IDS)
    assert len(packet_ask.prompts) == len(SECTION_IDS)
    assert all(isinstance(prompt, CacheablePrompt) for prompt in flat_ask.prompts)
    assert not any(
        isinstance(prompt, CacheablePrompt) for prompt in packet_ask.prompts
    )
    assert all(isinstance(prompt, str) for prompt in packet_ask.prompts)


def test_프롬프트에_위치의존_방향어가_없다():
    """«아래 자료 목록»은 재배치하면 거짓말이 된다 — 위치 중립 문구여야 한다.

    ★ flat 모드는 조각이 «맨 앞»에, packet 모드는 «맨 뒤»에 온다. 한 문구가 두
      모드에서 다 맞으려면 방향을 말하지 않아야 한다. 문장을 통째로 지워도
      통과하는 시험은 소용없으므로, 위치 중립 문구가 «있는지»도 함께 본다.
    """
    금지어 = "아래 자료 목록"
    있어야_할_문구 = "자료 목록의 [조각 n] 번호를 그대로 쓴다"

    packet_ask = _RecordingAsk()
    compose_sections(
        _COMPANY, (), None, packet_ask, section_evidence_packets=_packet_set()
    )
    flat_prompts = [
        build_section_prompt(
            _COMPANY,
            section_id,
            _fragments(),
            None,
            (),
            shared_evidence_prefix=True,
        )
        for section_id in SECTION_IDS
    ]

    for prompt in [*flat_prompts, *packet_ask.prompts]:
        assert 금지어 not in prompt
        assert 있어야_할_문구 in prompt
    # 장을 SECTION_IDS에서 빼도 상수에 방향어가 남지 않게 원본도 함께 본다.
    for guide in (JSON_SCHEMA_GUIDE, *FLOW_PROMPT_BY_SECTION.values()):
        assert 금지어 not in guide
        assert 있어야_할_문구 in guide


def test_flat모드_아홉_호출은_같은_앞부분을_보낸다():
    ask = _RecordingAsk()

    compose_sections(_COMPANY, _fragments(), _table(), ask)

    앞부분들 = {
        prompt[: prompt.cache_prefix_chars]  # type: ignore[attr-defined]
        for prompt in ask.prompts
    }

    assert len(앞부분들) == 1
    앞부분 = 앞부분들.pop()
    assert 앞부분.startswith(PROMPT_HEADER.format(company=_COMPANY))
    # 앞 장 문장이 뒤 장으로 갈수록 붙어도 앞부분은 그대로여야 한다.
    assert all(
        prompt.cache_prefix_chars == len(앞부분)  # type: ignore[attr-defined]
        for prompt in ask.prompts
    )
