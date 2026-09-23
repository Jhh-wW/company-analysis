"""지울 문장을 소유 장이 «증명할 수 있게 담을 때만» 지운다 — 여러 문장에 나뉜 사실 포함.

★ 4차 통합 재현 — 골든 1장 문장 「공식 표어는 …이며 Leader's Code로 …를 제시한다」의
  두 사실이 8장에서는 두 문장에 하나씩 나뉘어 있었다.
★ 4차 엄밀 증명 (2026-09-23 총괄 확정) — 후보의 각 «완전 주장절»이 소유 문장 하나의
  완전 주장절과 어절 그대로 대응해야 지운다. 합집합은 모든 절이 «같은 명시 주제어»로
  시작할 때만이다. 뒤 절 주어가 생략된 후보는 주체를 증명할 수 없어 남긴다.
★ 지키는 것:
  ① 명시 주체를 반복한 두 완전절이 소유 장 두 문장에 각각 통째로 있으면 지운다.
  ② 원래 생략 주어 두 사실 후보(아래 _BOTH_FACTS)는 증명할 수 없어 남긴다.
  ③ 부가 절·조건(짧은 것 포함)·지울 문장에만 있는 수치가 있으면 남긴다.
  ④ 같은 조각을 인용했다는 이유만으로 짝이 아닌 문장의 어절을 합치지 않는다.
  ⑤ 나뉘어 남은 경우 이동 장부는 덮은 문장 «전부»에 결속되고, 하나라도
     사라지면 이동을 말하지 않는다.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from src.features.composer.constants import NOTICE_INSUFFICIENT_EVIDENCE, SECTION_IDS
from src.features.composer.dedupe import (
    MovedFactRecord,
    drop_cross_section_duplicates,
    moved_owner_fingerprint,
    reconcile_section_notices,
)
from src.features.composer.dedupe_notice_constants import NOTICE_MOVED_TARGETS_PREFIX
from src.features.composer.port import ComposedReport, ComposedSection, ComposedSentence

_CITATION = ("8",)
#: 원래 사례 — 뒤 절 「행동 원칙으로 …」의 주어가 생략됐다.
_BOTH_FACTS = (
    "가나다전자의 공식 표어는 '빠르게 시험하고 함께 고친다'이며 "
    "행동 원칙으로 정직·책임·존중을 제시한다."
)
_MOTTO = "가나다전자의 공식 표어는 '빠르게 시험하고 함께 고친다'다."
_PRINCIPLES = "가나다전자는 행동 원칙으로 정직·책임·존중을 제시한다."
#: 명시 주체 양성 — 두 절이 모두 같은 주제어 「가나다전자는」으로 시작한다.
_EXPLICIT_MOTTO = "가나다전자는 공식 표어로 '빠르게 시험하고 함께 고친다'를 사용한다."
_EXPLICIT_BOTH = (
    "가나다전자는 공식 표어로 '빠르게 시험하고 함께 고친다'를 사용하며, "
    "가나다전자는 행동 원칙으로 정직·책임·존중을 제시한다."
)
#: 같은 조각을 인용하고 「행동 원칙」 글자도 담았지만 1장 문장과는 짝이 아닌 문장
#: (짧은 쪽 겹침 0.45). 이 글자를 합치면 1장 문장을 덮은 것처럼 보인다.
_UNPAIRED_SAME_SOURCE = (
    "가나다전자는 사내 교육과 평가 제도를 운영하며 분기마다 조직 문화 설문을 "
    "실시하고, 행동 원칙으로 정직·책임·존중을 제시한다는 점을 신입 안내 자료에 담았다."
)


def _sentence(text: str) -> ComposedSentence:
    return ComposedSentence(text, _CITATION, "확인")


def _report(identity: str, culture: tuple[str, ...]) -> ComposedReport:
    by_section = {"identity": (_sentence(identity),),
                  "culture": tuple(_sentence(text) for text in culture)}
    return ComposedReport(tuple(
        ComposedSection(section_id, by_section.get(section_id, ()))
        for section_id in SECTION_IDS
    ))


def _texts(report: ComposedReport, section_id: str) -> list[str]:
    return [sentence.text for sentence in report.sections[SECTION_IDS.index(section_id)].sentences]


def _notice(report: ComposedReport, section_id: str) -> str:
    return report.sections[SECTION_IDS.index(section_id)].notice


def test_명시_주체_두_절이_소유_장_두_문장에_통째로_있으면_지운다() -> None:
    sink: list[MovedFactRecord] = []
    result, dropped = drop_cross_section_duplicates(
        _report(_EXPLICIT_BOTH, (_EXPLICIT_MOTTO, _PRINCIPLES)), moved_facts_sink=sink,
    )
    assert dropped == 1
    assert _texts(result, "identity") == []
    assert _texts(result, "culture") == [_EXPLICIT_MOTTO, _PRINCIPLES]
    # 장부는 내용을 실제로 남긴 두 문장 «모두»에 결속된다.
    assert len(sink) == 1 and sink[0].owner_fingerprints_all_required is True
    assert set(sink[0].owner_fingerprints) == {
        moved_owner_fingerprint(_sentence(_EXPLICIT_MOTTO)),
        moved_owner_fingerprint(_sentence(_PRINCIPLES)),
    }
    final = reconcile_section_notices(result, frozenset(), moved_facts=sink)
    assert _notice(final, "identity").startswith(NOTICE_MOVED_TARGETS_PREFIX)


@pytest.mark.parametrize("kept_index", (0, 1), ids=("표어만_남음", "원칙만_남음"))
def test_나뉘어_남은_문장_중_하나가_사라지면_이동을_말하지_않는다(kept_index: int) -> None:
    sink: list[MovedFactRecord] = []
    result, _dropped = drop_cross_section_duplicates(
        _report(_EXPLICIT_BOTH, (_EXPLICIT_MOTTO, _PRINCIPLES)), moved_facts_sink=sink,
    )
    culture_index = SECTION_IDS.index("culture")
    half_lost = replace(result, sections=tuple(
        replace(section, sentences=(section.sentences[kept_index],))
        if index == culture_index else section
        for index, section in enumerate(result.sections)
    ))
    final = reconcile_section_notices(half_lost, frozenset(), moved_facts=sink)
    assert _notice(final, "identity") == NOTICE_INSUFFICIENT_EVIDENCE


def test_원래_생략_주어_두_사실_후보는_주체를_증명할_수_없어_남긴다() -> None:
    """기대 변경 (2026-09-23 총괄 확정) — 종전에는 지웠다.

    뒤 절 「행동 원칙으로 …」의 주어가 생략돼, 소유 문장 「가나다전자는 …」의 주체와
    같다는 것을 표면으로 증명할 수 없다(첫 소유격 「가나다전자의」를 뒤 행위자로
    승계하면 「X의 파트너는 Y이며 …」 반례가 생긴다). 겹쳐 보이는 쪽을 택한다.
    """
    result, dropped = drop_cross_section_duplicates(_report(_BOTH_FACTS, (_MOTTO, _PRINCIPLES)))
    assert dropped == 0
    assert _texts(result, "identity") == [_BOTH_FACTS]
    assert _texts(result, "culture") == [_MOTTO, _PRINCIPLES]


@pytest.mark.parametrize("candidate", (
    _BOTH_FACTS[:-1] + "며, 신입 직원 교육과 분기별 인사 평가에서 이 원칙의 실천 여부를 매주 함께 점검한다.",
    _BOTH_FACTS[:-1] + "며, 이 원칙을 어긴 직원은 해당 연도 승진 심사 대상에서 제외된다는 조건을 둔다.",
    _BOTH_FACTS.replace("행동 원칙으로", "행동 원칙 5가지로"),
    # 독립 검증 원래 입력(짧은 조건·짧은 절) — 겹침이 높아도 남는다.
    _BOTH_FACTS.replace("제시한다.", "제시하되, 정규직에만 적용한다."),
    _BOTH_FACTS.replace("제시한다.", "제시하며 위반자는 해고한다."),
    _EXPLICIT_BOTH.replace("제시한다.", "제시하되, 정규직에만 적용한다."),
    _EXPLICIT_BOTH.replace("제시한다.", "제시하며 위반자는 해고한다."),
), ids=("부가_절", "조건", "지울_문장에만_있는_수치", "짧은_조건", "짧은_절",
        "명시주체_짧은_조건", "명시주체_짧은_절"))
def test_소유_장에_없는_절_조건_수치가_있으면_남긴다(candidate: str) -> None:
    owners = (
        (_EXPLICIT_MOTTO, _PRINCIPLES) if candidate.startswith("가나다전자는")
        else (_MOTTO, _PRINCIPLES)
    )
    result, dropped = drop_cross_section_duplicates(_report(candidate, owners))
    assert dropped == 0
    assert _texts(result, "identity") == [candidate]


def test_같은_근거라도_짝이_아닌_문장의_글자는_합치지_않는다() -> None:
    result, dropped = drop_cross_section_duplicates(
        _report(_EXPLICIT_BOTH, (_EXPLICIT_MOTTO, _UNPAIRED_SAME_SOURCE)),
    )
    assert dropped == 0
    assert _texts(result, "identity") == [_EXPLICIT_BOTH]


def test_한_문장이_혼자_덮으면_종전처럼_그_문장_하나로_충분하다() -> None:
    """후보 문장 전체가 소유 문장 하나 전체와 같으면 그 문장 하나로 충분하다."""
    sink: list[MovedFactRecord] = []
    result, dropped = drop_cross_section_duplicates(
        _report(_PRINCIPLES, (_EXPLICIT_MOTTO, _PRINCIPLES)), moved_facts_sink=sink,
    )
    assert dropped == 1
    assert _texts(result, "identity") == []
    assert sink[0].owner_fingerprints == (moved_owner_fingerprint(_sentence(_PRINCIPLES)),)
    assert sink[0].owner_fingerprints_all_required is False


def test_소유_문장의_앞_절만_같은_원래_사례는_뒤_절_범위를_증명할_수_없어_남긴다() -> None:
    """기대 변경 (2026-09-23 총괄 확정) — 종전에는 이 원래 입력을 지웠다.

    1장 「표어는 '…'다」는 8장 문장 「표어는 '…'이며 행동 원칙으로 …」의 앞 절과
    같다. 뒤 절이 앞 절을 한정하는지(목표·조건) 표면으로 증명할 수 없으므로 소유
    문장은 언제나 «전체»가 대응해야 한다 — 반복이 더 남을 수 있다.
    """
    result, dropped = drop_cross_section_duplicates(_report(_MOTTO, (_BOTH_FACTS, _PRINCIPLES)))
    assert dropped == 0
    assert _texts(result, "identity") == [_MOTTO]
