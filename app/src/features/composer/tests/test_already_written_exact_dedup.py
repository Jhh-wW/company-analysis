"""후보 C: 기존 80문장 범위 안의 완전 일치 반복만 프롬프트에서 줄인다.

변경 전 렌더러를 비교 기준으로 두고 실제 장별 입력·캐시 경계·작성 결과를
오프라인에서 확인한다. 글자 수 차이는 토큰 수나 운영 비용 절감률이 아니다.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import pytest

from src.features.composer import logic
from src.features.composer.constants import (
    ALREADY_WRITTEN_GUIDE,
    ALREADY_WRITTEN_HEAD,
    ALREADY_WRITTEN_MAX_SENTENCES,
    SECTION_GUIDES,
    SECTION_IDS,
)
from src.features.composer.port import CollectedFragment, PerformanceTable


_REPEATED = "회사는 검사 장비를 제조한다."
_FRAGMENTS = (
    CollectedFragment(fragment_id="1", kind="홈페이지", text=_REPEATED),
)
_TABLE = PerformanceTable(
    caption="최근 실적", headers=("연도", "매출"), rows=(("2025", "100"),),
)


def _legacy_render(already_written: Sequence[str]) -> str:
    """변경 전 함수 그대로: strip → 빈값 제거 → 상한, 중복은 남긴다."""
    kept = [text.strip() for text in already_written if text and text.strip()]
    if not kept:
        return ""
    kept = kept[:ALREADY_WRITTEN_MAX_SENTENCES]
    lines = "".join(f"- {text}\n" for text in kept)
    return f"{ALREADY_WRITTEN_HEAD}{lines}{ALREADY_WRITTEN_GUIDE}"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ([], []),
        (["", " \n\t", "\u3000"], []),
        ([" \n첫 문장.\t", "", "첫 문장.", "다음 문장."], ["첫 문장.", "다음 문장."]),
        (["나", "가", "나", "다", "가"], ["나", "가", "다"]),
        (["a\nb", "a\nb", "a b"], ["a\nb", "a b"]),
        (["매출 10% 증가.", "매출 10% 증가!", "매출이 10% 늘었다."],
         ["매출 10% 증가.", "매출 10% 증가!", "매출이 10% 늘었다."]),
        (["A B", "A  B", "a b", "Ａ B", "A\tB"],
         ["A B", "A  B", "a b", "Ａ B", "A\tB"]),
        (["가", "\u1100\u1161", "가"], ["가", "\u1100\u1161"]),
    ],
    ids=["empty", "blank", "strip-first", "first-order", "internal-newline",
         "similar-sentences", "exact-characters", "unicode-not-normalized"],
)
def test_exact_duplicates_keep_first_order_without_mutating_input(raw, expected):
    before = raw.copy()

    assert logic._render_already_written(raw) == _legacy_render(expected)
    assert raw == before


@pytest.mark.parametrize("unique_count", [1, 79, 80])
def test_dedup_after_existing_cap_does_not_admit_later_sentences(unique_count):
    assert ALREADY_WRITTEN_MAX_SENTENCES == 80
    expected = [f"기존 문장 {index:02d}." for index in range(unique_count)]
    capped = expected + [expected[0]] * (80 - unique_count)
    raw = ["", "\t"] * 50
    for text in capped:
        raw.extend([f"  {text}\n", " "])
    raw.extend(["범위 밖의 새 문장.", "또 다른 새 문장."])

    result = logic._render_already_written(raw)

    assert result == _legacy_render(expected)
    assert "새 문장" not in result
    assert result.count("- ") == unique_count


@pytest.mark.parametrize("count", [0, 1, 79, 80, 81, 120])
def test_unique_input_is_byte_identical_inside_and_outside_cap(count):
    raw = [f" \t고유 문장 {index}.\n" for index in range(count)]

    assert logic._render_already_written(raw).encode() == _legacy_render(raw).encode()


@pytest.mark.parametrize("section_id", SECTION_IDS)
@pytest.mark.parametrize("shared_prefix", [False, True])
@pytest.mark.parametrize("repeated", [False, True])
def test_section_prompts_preserve_surrounding_text_and_cache_prefix(
    monkeypatch, section_id, shared_prefix, repeated,
):
    unique = ["먼저 작성한 고유 문장.", "뒤이어 작성한 고유 문장."]
    raw = unique + ([unique[0], unique[1]] if repeated else [])
    kwargs = {"shared_evidence_prefix": shared_prefix}
    current = logic.build_section_prompt(
        "시험 회사", section_id, _FRAGMENTS, _TABLE, raw, **kwargs,
    )
    with monkeypatch.context() as patch:
        patch.setattr(logic, "_render_already_written", _legacy_render)
        legacy = logic.build_section_prompt(
            "시험 회사", section_id, _FRAGMENTS, _TABLE, raw, **kwargs,
        )

    legacy_block = _legacy_render(raw)
    expected_block = _legacy_render(unique)
    assert legacy.count(legacy_block) == 1
    assert current.encode() == legacy.replace(legacy_block, expected_block, 1).encode()
    assert type(current) is type(legacy)
    assert SECTION_GUIDES[section_id] in current
    if shared_prefix:
        assert isinstance(current, logic.CacheablePrompt)
        boundary = current.cache_prefix_chars
        assert boundary == legacy.cache_prefix_chars
        assert current[:boundary].encode() == legacy[:boundary].encode()
        assert ALREADY_WRITTEN_HEAD not in current[:boundary]
        assert current[boundary:].endswith(expected_block)
    else:
        assert type(current) is str
    if repeated:
        assert len(legacy) - len(current) == sum(len(text) + 3 for text in unique)
    else:
        assert current.encode() == legacy.encode()


@pytest.mark.parametrize("packet_mode", [False, True], ids=["flat", "packet"])
@pytest.mark.parametrize("repeated", [False, True], ids=["unique", "repeated"])
def test_composer_preserves_call_count_results_and_packet_boundaries(
    monkeypatch, packet_mode, repeated,
):
    packets = {
        section_id: (CollectedFragment(
            fragment_id=str(index), kind="홈페이지",
            text=f"{section_id} 장의 전용 근거 원문.",
        ),)
        for index, section_id in enumerate(SECTION_IDS, start=1)
    }

    def run():
        prompts = []

        def ask(prompt):
            index = len(prompts)
            prompts.append(prompt)
            sentence = {
                "글": _REPEATED if repeated else f"회사의 고유한 설명 {index}.",
                "인용": [str(index + 1) if packet_mode else "1"],
                "등급": "확인",
            }
            return json.dumps(
                {"문장들": [sentence, sentence] if repeated else [sentence]},
                ensure_ascii=False,
            )

        report = logic.compose_sections(
            "시험 회사", _FRAGMENTS, None, ask,
            section_evidence_packets=packets if packet_mode else None,
        )
        return prompts, report

    current_prompts, current_report = run()
    with monkeypatch.context() as patch:
        patch.setattr(logic, "_render_already_written", _legacy_render)
        legacy_prompts, legacy_report = run()

    assert current_report == legacy_report
    assert sum(len(section.sentences) for section in current_report.sections) == (
        18 if repeated else 9
    )
    assert len(current_prompts) == len(legacy_prompts) == len(SECTION_IDS) == 9
    for index, (current, legacy) in enumerate(zip(current_prompts, legacy_prompts)):
        if packet_mode:
            assert type(current) is str
            assert ALREADY_WRITTEN_HEAD not in current
            assert current.encode() == legacy.encode()
            for other_id, fragments in packets.items():
                assert (fragments[0].text in current) == (other_id == SECTION_IDS[index])
        else:
            assert isinstance(current, logic.CacheablePrompt)
            boundary = current.cache_prefix_chars
            assert boundary == legacy.cache_prefix_chars
            assert current[:boundary] == legacy[:boundary] == current_prompts[0][:boundary]
            if repeated and index:
                assert current[boundary:].count(f"- {_REPEATED}\n") == 1
                assert len(legacy) - len(current) == (2 * index - 1) * (len(_REPEATED) + 3)
            else:
                assert current.encode() == legacy.encode()

    before = sum(map(len, legacy_prompts))
    after = sum(map(len, current_prompts))
    expected_saved = 64 * (len(_REPEATED) + 3) if repeated and not packet_mode else 0
    assert before - after == expected_saved
    print(f"mode={'packet' if packet_mode else 'flat'} repeated={repeated}: "
          f"9 prompts {before} -> {after} chars; saved={before - after}")


def test_repeated_block_character_savings():
    raw = [_REPEATED] * 80
    before = _legacy_render(raw)
    after = logic._render_already_written(raw)

    assert after == _legacy_render([_REPEATED])
    assert len(before) - len(after) == 79 * (len(_REPEATED) + 3)
    print(f"80 repeated sentences: {len(before)} -> {len(after)} chars; "
          f"saved={len(before) - len(after)} ({(1 - len(after) / len(before)):.2%})")
