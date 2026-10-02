from __future__ import annotations

from dataclasses import replace
import hashlib
import json
import re

import pytest

from src.features.composer.constants import CLAIM_SLOTS_BY_SECTION, SECTION_IDS
from src.features.composer.news_constants import NEWS_WRITER_GUIDE
from src.features.composer.evidence_pair_selection import (
    build_evidence_pair_map,
    writer_visible_fragments,
)
from src.features.composer.logic import (
    _FLOW_CELL_SUPPORTED_SLOTS_BY_SECTION,
    build_section_prompt,
    compose_sections,
    compose_selected_sections,
    parse_section_response,
)
from src.features.composer.port import (
    CollectedFragment,
    PerformanceTable,
    SectionEvidencePacket,
    SectionEvidencePacketSet,
)
from src.shared.report_evidence.policy import injected_slots_for
from src.shared.report_evidence.constants import SOURCE_KIND_NEWS
from src.shared.report_quality.source_identity import document_identity_from_parts


def _fragment(number: int, *slots: str) -> CollectedFragment:
    text = f"합성원문{number}: 회사가 검증 자료를 제공한다."
    url = f"https://example.test/document/{number}"
    return CollectedFragment(
        str(number),
        "공식자료",
        text,
        source_url=url,
        document_identity=document_identity_from_parts(url=url),
        document_content_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        supported_claim_slots=tuple(slots),
    )


def _response(**overrides: object) -> str:
    sentence = {"글": "회사는 검증 자료를 제공한다.", "등급": "확인"}
    sentence.update(overrides)
    return json.dumps({"문장들": [sentence]}, ensure_ascii=False)


def _business_pairs() -> tuple[tuple[CollectedFragment, ...], dict[str, tuple[str, str]]]:
    fragments = (
        _fragment(12, "business_model:revenue_model"),
        _fragment(27, "business_model:customer_type"),
        _fragment(31, "business_model:revenue_model"),
    )
    return fragments, build_evidence_pair_map("business_model", fragments)


def _pair_id(pairs: dict[str, tuple[str, str]], slot: str, number: str) -> str:
    return next(key for key, value in pairs.items() if value == (slot, number))


def test_FULL_지원쌍은_장과_프로그램전용칸에_닫힌다() -> None:
    fragments = (
        _fragment(5, "past_changes:completed_execution", "past_changes:historical_performance"),
        _fragment(8, "competitive_position:stated_differentiator", "competitive_position:limitation"),
    )
    past = build_evidence_pair_map("past_changes", fragments[:1])
    position = build_evidence_pair_map("competitive_position", fragments[1:])

    assert tuple(past.values()) == (("past_changes:completed_execution", "5"),)
    assert tuple(position.values()) == (("competitive_position:stated_differentiator", "8"),)
    assert set(past).isdisjoint(position)
    assert all(slot not in injected_slots_for("past_changes") for slot, _ in past.values())
    assert all(slot not in injected_slots_for("competitive_position") for slot, _ in position.values())


def test_full_writer_input_filters_unsupported_fragments_and_preserves_pair_ids() -> None:
    fragments = (
        _fragment(12, "business_model:revenue_model"),
        _fragment(13),
        _fragment(14, "future_strategy:stated_plan"),
        _fragment(27, "business_model:customer_type"),
    )
    original_pairs = build_evidence_pair_map("business_model", fragments)
    visible = writer_visible_fragments("business_model", fragments)
    full = build_section_prompt(
        "테스트", "business_model", fragments, None,
        show_supported_claim_slots=True,
    )
    shadow = build_section_prompt("테스트", "business_model", fragments, None)

    assert tuple(fragment.fragment_id for fragment in visible) == ("12", "27")
    assert build_evidence_pair_map("business_model", visible) == original_pairs
    assert "합성원문12" in full and "합성원문27" in full
    assert "합성원문13" not in full and "합성원문14" not in full
    assert all(fragment.text in shadow for fragment in fragments)
    for pair_id, (slot_id, fragment_id) in original_pairs.items():
        assert f"{slot_id}[{pair_id}]" in full
        assert full.count(f"{slot_id}[{pair_id}]") == 1
        assert f"[조각 {fragment_id}]" in full
    slot_label = re.search(r"\[조각 12\] \([^)]*지원 주장슬롯: ([^)]*)\)", full)
    assert slot_label is not None
    assert slot_label.group(1) == "business_model:revenue_model"
    assert "지원쌍이 없는 조각은 작성 입력에서 숨겼다" in full
    assert "지원쌍이 없는 조각은 작성 입력에서 숨겼다" not in shadow


def test_full_program_only_fragment_is_hidden_from_writer_but_table_remains() -> None:
    fragments = (
        _fragment(5, "past_changes:completed_execution"),
        _fragment(8, "past_changes:historical_performance"),
    )
    table = PerformanceTable(
        caption="합성 실적표", headers=("연도", "값"), rows=(("2025", "10"),),
    )
    full = build_section_prompt(
        "테스트", "past_changes", fragments, table,
        show_supported_claim_slots=True,
    )

    assert "합성원문5" in full
    assert "합성원문8" not in full
    assert "합성 실적표" in full and "2025 | 10" in full
    assert build_evidence_pair_map("past_changes", fragments) == (
        build_evidence_pair_map("past_changes", writer_visible_fragments("past_changes", fragments))
    )


def test_full_flow_fragment_with_pair_remains_visible() -> None:
    fragments = (
        _fragment(1, "operations_partners:value_chain"),
        _fragment(2, "operations_partners:operating_role"),
        _fragment(3),
    )
    full = build_section_prompt(
        "테스트", "operations_partners", fragments, None,
        show_supported_claim_slots=True,
    )

    assert "합성원문1" in full and "합성원문2" in full
    assert "합성원문3" not in full
    assert "«경로표» 행의 «인용»만" in full
    assert "operations_partners:value_chain[p7-001]" in full


def test_full_flow_cell_slots_always_have_writer_pair_eligibility() -> None:
    for section_id, cells in _FLOW_CELL_SUPPORTED_SLOTS_BY_SECTION.items():
        flow_slots = set().union(*cells)
        writer_slots = set(CLAIM_SLOTS_BY_SECTION[section_id]) - set(
            injected_slots_for(section_id)
        )
        assert flow_slots <= writer_slots, section_id


def test_full_without_pairs_shows_no_raw_fragment_and_explains_shortfall() -> None:
    fragments = (_fragment(1), _fragment(2, "future_strategy:stated_plan"))
    full = build_section_prompt(
        "테스트", "business_model", fragments, None,
        show_supported_claim_slots=True,
    )

    assert "[조각 1]" not in full and "[조각 2]" not in full
    assert "합성원문" not in full
    assert "아래에 필요한 사실이 없으면 그 사실을 만들지 않는다" in full


def test_full_mixed_program_and_writer_fragment_remains_visible() -> None:
    fragments = (
        _fragment(
            90,
            "competitive_position:stated_differentiator",
            "competitive_position:limitation",
        ),
        _fragment(91, "competitive_position:limitation"),
    )
    visible = writer_visible_fragments("competitive_position", fragments)
    full = build_section_prompt(
        "테스트", "competitive_position", fragments, None,
        show_supported_claim_slots=True,
    )

    assert tuple(fragment.fragment_id for fragment in visible) == ("90",)
    assert "합성원문90" in full and "합성원문91" not in full
    assert "competitive_position:stated_differentiator[p9-001]" in full
    assert build_evidence_pair_map("competitive_position", fragments) == (
        build_evidence_pair_map("competitive_position", visible)
    )


def test_full_news_guide_tracks_only_visible_news_fragments() -> None:
    official = _fragment(1, "business_model:revenue_model")
    hidden_news = replace(_fragment(2), kind=SOURCE_KIND_NEWS)
    eligible_news = replace(
        _fragment(3, "business_model:customer_type"), kind=SOURCE_KIND_NEWS
    )
    hidden_full = build_section_prompt(
        "테스트", "business_model", (official, hidden_news), None,
        show_supported_claim_slots=True,
    )
    visible_full = build_section_prompt(
        "테스트", "business_model", (official, eligible_news), None,
        show_supported_claim_slots=True,
    )
    shadow = build_section_prompt("테스트", "business_model", (official, hidden_news), None)

    assert NEWS_WRITER_GUIDE not in hidden_full
    assert NEWS_WRITER_GUIDE in visible_full
    assert NEWS_WRITER_GUIDE in shadow


def test_FULL_선택한_지원쌍만_기존문장필드로_해석한다() -> None:
    _, pairs = _business_pairs()
    first = _pair_id(pairs, "business_model:revenue_model", "12")
    second = _pair_id(pairs, "business_model:revenue_model", "31")
    result = parse_section_response(
        _response(근거선택=[first, second]),
        "business_model",
        evidence_pairs=pairs,
    )

    assert result is not None and len(result) == 1
    assert result[0].planned_claim_slot == "business_model:revenue_model"
    assert result[0].citations == ("12", "31")
    assert result[0].verification_state == "unverified"


def test_FULL_구필드를_함께_쓴_경우는_지원쌍과_정확히_같을때만_받는다() -> None:
    _, pairs = _business_pairs()
    first = _pair_id(pairs, "business_model:revenue_model", "12")
    result = parse_section_response(
        _response(
            근거선택=[first],
            주장슬롯="business_model:revenue_model",
            인용=["12"],
        ),
        "business_model",
        evidence_pairs=pairs,
    )

    assert result is not None and len(result) == 1
    assert result[0].citations == ("12",)


@pytest.mark.parametrize(
    "overrides",
    (
        {"근거선택": []},
        {"근거선택": ["p9-001"]},
        {"근거선택": ["p2-999"]},
        {"근거선택": ["p2-"]},
        {"근거선택": "p2-001"},
        {"근거선택": ["p2-001", "p2-003"]},
        {"근거선택": ["p2-001"], "주장슬롯": "business_model:customer_type"},
        {"근거선택": ["p2-001"], "인용": ["27"]},
        {"근거선택": ["p2-001"], "인용": []},
    ),
)
def test_FULL_잘못된_지원쌍은_구필드로_되살리지_않는다(
    overrides: dict[str, object],
) -> None:
    _, pairs = _business_pairs()
    result = parse_section_response(
        _response(**overrides),
        "business_model",
        evidence_pairs=pairs,
    )

    assert result == ()


def test_FULL_구형응답은_지원쌍필드가_없을때만_동일하게_해석한다() -> None:
    _, pairs = _business_pairs()
    old = _response(
        주장슬롯="business_model:revenue_model",
        인용=["12"],
    )

    assert parse_section_response(old, "business_model", evidence_pairs=pairs) == (
        parse_section_response(old, "business_model")
    )
    assert parse_section_response(
        _response(
            근거선택=["p2-999"],
            주장슬롯="business_model:revenue_model",
            인용=["12"],
        ),
        "business_model",
        evidence_pairs=pairs,
    ) == ()


def test_FULL_잘못된_지원쌍_한문장만_버리고_다른문장은_보존한다() -> None:
    _, pairs = _business_pairs()
    first = _pair_id(pairs, "business_model:revenue_model", "12")
    response = json.dumps({"문장들": [
        {"글": "잘못된 합성 문장.", "등급": "확인", "근거선택": ["p9-001"],
         "주장슬롯": "business_model:revenue_model", "인용": ["12"]},
        {"글": "확인된 합성 문장.", "등급": "확인", "근거선택": [first]},
    ]}, ensure_ascii=False)

    result = parse_section_response(response, "business_model", evidence_pairs=pairs)

    assert result is not None and len(result) == 1
    assert result[0].citations == ("12",)


def test_요약모양_공격은_본문지원쌍을_우회하지_못한다() -> None:
    _, pairs = _business_pairs()
    response = json.dumps({
        "문장들": [{"글": "잘못된 합성 문장.", "등급": "확인", "근거선택": ["p9-001"]}],
        "요약": [{"글": "공개를 시도한 문장.", "근거선택": ["p2-001"]}],
    }, ensure_ascii=False)

    assert parse_section_response(response, "business_model", evidence_pairs=pairs) == ()


def test_기본응답과_SHADOW프롬프트는_새선택지의_영향을_받지않는다() -> None:
    fragments, pairs = _business_pairs()
    old = _response(
        주장슬롯="business_model:revenue_model",
        인용=["12"],
    )
    shadow = build_section_prompt("테스트", "business_model", fragments, None)
    full = build_section_prompt(
        "테스트", "business_model", fragments, None,
        show_supported_claim_slots=True,
    )

    assert parse_section_response(old, "business_model") == parse_section_response(
        old, "business_model", evidence_pairs=None
    )
    assert "근거선택" not in shadow
    assert "근거선택" in full
    assert all(pair_id in full for pair_id in pairs)
    for fragment in fragments:
        assert full.count(fragment.text) == 1
        assert shadow.count(fragment.text) == 1


def test_FULL_아홉장_본문지침은_옛_독립인용필드와_상충하지_않는다() -> None:
    banned = (
        "각 문장은 가장 알맞은 id를 «주장슬롯»에 넣고",
        "모든 문장에 인용(조각 id 배열)",
        "종합적 해석이면 빈 배열도 허용된다",
        "인용은 반드시 «인용» 배열로만 표시한다",
        "숫자는 조각 원문이나 실적표에 있는 값만 쓴다.",
        "«인용»에 넣은 조각",
        '"인용"의 조각id는 자료 목록의',
    )
    for section_id in SECTION_IDS:
        full = build_section_prompt(
            "테스트", section_id, (), None, show_supported_claim_slots=True
        )
        assert all(old not in full for old in banned), section_id
        assert '"근거선택": ["<지원쌍 ID>"]' in full
        assert '"주장슬롯": "<허용된 id 또는 빈 문자열>"' not in full
        assert "본문에 별도의 «인용»·«주장슬롯»은 쓰지 않는다" in full or (
            "«경로표» 행의 «인용»만" in full
        )


def _packets() -> SectionEvidencePacketSet:
    generation = "a" * 64
    packets = []
    for index, section_id in enumerate(SECTION_IDS, start=1):
        fragment = _fragment(index, CLAIM_SLOTS_BY_SECTION[section_id][0])
        packets.append(
            SectionEvidencePacket(
                company_id="00123456",
                evidence_generation_sha256=generation,
                section_id=section_id,
                fragments=(fragment,),
            )
        )
    return SectionEvidencePacketSet(
        company_id="00123456",
        evidence_generation_sha256=generation,
        packets=tuple(packets),
    )


class _PairWriter:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, prompt: str) -> str:
        return self.for_section("business_model")(prompt)

    def for_section(self, section_id: str):
        def ask(prompt: str) -> str:
            self.calls += 1
            if section_id != "business_model":
                return json.dumps({"문장들": []}, ensure_ascii=False)
            assert "p2-001" in prompt
            return _response(근거선택=["p2-001"])

        return ask


def test_FULL_1차와_보충은_같은_봉인쌍을_한호출로_쓴다() -> None:
    packets = _packets()
    primary_writer = _PairWriter()
    primary = compose_sections(
        "테스트", (), None, primary_writer, section_evidence_packets=packets,
    )
    supplement_writer = _PairWriter()
    supplement = compose_selected_sections(
        "테스트", None, supplement_writer,
        section_evidence_packets=packets,
        section_ids=("business_model",),
    )

    assert primary_writer.calls == len(SECTION_IDS)
    assert supplement_writer.calls == 1
    primary_sentence = primary.sections[1].sentences[0]
    supplement_sentence = supplement.sections[0].sentences[0]
    assert primary_sentence == supplement_sentence
    assert primary_sentence.citations == ("2",)
    assert primary_sentence.planned_claim_slot == CLAIM_SLOTS_BY_SECTION["business_model"][0]
