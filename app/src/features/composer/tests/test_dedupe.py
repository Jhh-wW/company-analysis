"""장 간 중복 제거를 못 박는다 (사실 단일 소유의 «강제» 단계).

★ 왜 이 시험이 있나 (실측) — 프롬프트 지침(v2-11·v2-12)만으로는 숫자 없는
  사실이 6개 장에 거의 같은 문장으로 남았다. JYP 실측:
      [3장] 회사는 Sony Music, TME, Republic Records 등 글로벌 유통 전문사와의…
      [5장] 회사는 Sony Music, TME, Republic Records 등 글로벌 유통 전문사와의…
★ 여기서 지키는 것:
  ① 여러 장에 반복된 같은 사실은 «소유 장 하나»만 남는다.
  ② 소유 장은 «먼저 나온 장»이 아니라 «그 근거를 가장 깊이 쓴 장»이다.
     (실측에서 순서로 정했더니 파트너를 세 문장으로 다룬 7장이 스쳐 지나간
      1장에게 사실을 뺏겼다.)
  ③ 근거 조각이 다르면 글이 닮아도 지우지 않는다 — 다른 자료면 다른 사실이다.
  ④ 무리가 «번져나가지» 않는다 — 두 사실을 함께 언급한 문장이 다리가 되어
     무관한 사실까지 한 덩어리로 묶이면 안 된다.
  ⑤ 장이 비면 삭제하지 않고 «왜 비었는지» 안내문을 남긴다.
"""

from __future__ import annotations

from src.features.composer.constants import NOTICE_DUPLICATE_MOVED, SECTION_IDS
from src.features.composer.dedupe import drop_cross_section_duplicates
from src.features.composer.port import (
    ComposedReport,
    ComposedSection,
    ComposedSentence,
    FlowRow,
)

_파트너_문장 = (
    "회사는 Sony Music, TME, Republic Records 등 글로벌 유수의 음반·음원 "
    "유통 전문사와 파트너십을 체결하여 글로벌 유통 범위를 확대하고 있다."
)
_파트너_문장_변형 = (
    "회사는 Sony Music, TME, Republic Records 등 글로벌 유통 전문사와의 "
    "파트너십을 통해 음반·음원의 글로벌 유통 범위를 확대하고 있다."
)
_공연_문장 = (
    "공연 부문의 글로벌 확장을 위해 회사는 2023년 Live Nation과 전략적 "
    "파트너십을 체결하여 투어 협력 체계를 구축했다."
)
_다른_사실 = (
    "회사의 신인개발 부문은 캐스팅팀과 트레이닝팀으로 구성되어 연습생을 "
    "모집하고 체계적인 트레이닝을 제공한다."
)


def _sentence(text: str, citations: tuple[str, ...]) -> ComposedSentence:
    return ComposedSentence(text=text, citations=citations, grade="확인")


def _report(**by_section: tuple[ComposedSentence, ...]) -> ComposedReport:
    """장 id → 문장 튜플. 안 준 장은 빈 장으로 채운다(장 삭제 금지)."""
    return ComposedReport(
        sections=tuple(
            ComposedSection(
                section_id=section_id, sentences=by_section.get(section_id, ())
            )
            for section_id in SECTION_IDS
        )
    )


def _texts(report: ComposedReport, section_id: str) -> list[str]:
    for section in report.sections:
        if section.section_id == section_id:
            return [sentence.text for sentence in section.sentences]
    raise AssertionError(f"{section_id} 장이 없습니다")


# ══════════════════════════════════════════════════════════
# ① 반복된 사실은 한 장만 남는다
# ══════════════════════════════════════════════════════════


def test_여러_장에_반복된_사실은_한_장만_남는다():
    """7장이 같은 근거를 두 문장으로 다루므로 7장이 소유한다."""
    report = _report(
        identity=(_sentence(_파트너_문장, ("12",)),),
        portfolio=(_sentence(_파트너_문장_변형, ("12",)),),
        operations_partners=(
            _sentence(_파트너_문장, ("12",)),
            _sentence(_공연_문장, ("12", "13")),
        ),
    )

    새보고서, 뺀수 = drop_cross_section_duplicates(report)

    assert 뺀수 == 2
    assert _texts(새보고서, "identity") == []
    assert _texts(새보고서, "portfolio") == []
    assert len(_texts(새보고서, "operations_partners")) == 2


# ══════════════════════════════════════════════════════════
# ② 소유 장 = 그 근거를 가장 깊이 쓴 장 (순서가 아니다)
# ══════════════════════════════════════════════════════════


def test_소유_장은_먼저_나온_장이_아니라_깊이_다룬_장이다():
    """1장이 스쳐 지나가고 7장이 세 문장으로 다루면 7장이 소유한다."""
    report = _report(
        identity=(_sentence(_파트너_문장, ("12",)),),
        operations_partners=(
            _sentence(_파트너_문장_변형, ("12",)),
            _sentence(_공연_문장, ("13",)),
            _sentence("회사는 일본·홍콩·미국에 현지 법인을 두고 있다.", ("12",)),
        ),
    )

    새보고서, 뺀수 = drop_cross_section_duplicates(report)

    assert 뺀수 == 1
    assert _texts(새보고서, "identity") == []
    assert _파트너_문장_변형 in _texts(새보고서, "operations_partners")


def test_깊이가_같으면_정본_목차에서_앞선_장이_소유한다():
    report = _report(
        identity=(_sentence(_파트너_문장, ("12",)),),
        culture=(_sentence(_파트너_문장_변형, ("12",)),),
    )

    새보고서, 뺀수 = drop_cross_section_duplicates(report)

    assert 뺀수 == 1
    assert _texts(새보고서, "identity") == [_파트너_문장]
    assert _texts(새보고서, "culture") == []


# ══════════════════════════════════════════════════════════
# ③ 근거가 다르면 지우지 않는다
# ══════════════════════════════════════════════════════════


def test_근거_조각이_다르면_글이_닮아도_지우지_않는다():
    """다른 자료에서 온 말은 다른 사실이다 — 겹쳐 보여도 남긴다."""
    report = _report(
        identity=(_sentence(_파트너_문장, ("12",)),),
        portfolio=(_sentence(_파트너_문장_변형, ("99",)),),
    )

    새보고서, 뺀수 = drop_cross_section_duplicates(report)

    assert 뺀수 == 0
    assert _texts(새보고서, "identity") == [_파트너_문장]
    assert _texts(새보고서, "portfolio") == [_파트너_문장_변형]


def test_인용이_없는_문장은_건드리지_않는다():
    """순수 해석 문장은 결속할 근거가 없어 판단하지 않는다(오탐 방지)."""
    report = _report(
        identity=(_sentence(_파트너_문장, ()),),
        portfolio=(_sentence(_파트너_문장_변형, ()),),
    )

    _, 뺀수 = drop_cross_section_duplicates(report)

    assert 뺀수 == 0


def test_주제가_다르면_지우지_않는다():
    report = _report(
        identity=(_sentence(_파트너_문장, ("12",)),),
        culture=(_sentence(_다른_사실, ("12",)),),
    )

    _, 뺀수 = drop_cross_section_duplicates(report)

    assert 뺀수 == 0


# ══════════════════════════════════════════════════════════
# ④ 무리가 번져나가지 않는다
# ══════════════════════════════════════════════════════════


def test_두_사실을_함께_말한_문장이_무관한_사실을_끌어들이지_않는다():
    """실측 결함 — 이 번짐이 7장의 파트너 문장 4개를 통째로 날렸다."""
    합친_문장 = _파트너_문장 + " " + _공연_문장
    report = _report(
        identity=(_sentence(합친_문장, ("12", "13")),),
        operations_partners=(_sentence(_파트너_문장_변형, ("12",)),),
        culture=(_sentence(_다른_사실, ("13",)),),
    )

    새보고서, _ = drop_cross_section_duplicates(report)

    # 신인개발 문장은 파트너 사실과 아무 관계가 없다 — 살아 있어야 한다.
    assert _texts(새보고서, "culture") == [_다른_사실]


# ══════════════════════════════════════════════════════════
# ⑤ 장이 비면 왜 비었는지 남긴다
# ══════════════════════════════════════════════════════════


def test_장이_비면_자료부족이_아니라_이동했다고_알린다():
    report = _report(
        identity=(_sentence(_파트너_문장, ("12",)),),
        operations_partners=(
            _sentence(_파트너_문장_변형, ("12",)),
            _sentence(_공연_문장, ("12",)),
        ),
    )

    새보고서, _ = drop_cross_section_duplicates(report)

    비워진_장 = next(s for s in 새보고서.sections if s.section_id == "identity")
    assert 비워진_장.sentences == ()
    assert 비워진_장.notice == NOTICE_DUPLICATE_MOVED
    assert "자료가 없어서" in 비워진_장.notice


def test_장은_하나도_사라지지_않는다():
    report = _report(
        identity=(_sentence(_파트너_문장, ("12",)),),
        operations_partners=(
            _sentence(_파트너_문장_변형, ("12",)),
            _sentence(_공연_문장, ("12",)),
        ),
    )

    새보고서, _ = drop_cross_section_duplicates(report)

    assert [s.section_id for s in 새보고서.sections] == list(SECTION_IDS)


# ══════════════════════════════════════════════════════════
# 경계
# ══════════════════════════════════════════════════════════


def test_문장이_하나뿐이면_아무것도_하지_않는다():
    report = _report(identity=(_sentence(_파트너_문장, ("12",)),))

    새보고서, 뺀수 = drop_cross_section_duplicates(report)

    assert 뺀수 == 0
    assert 새보고서 is report


def test_같은_장_안의_반복은_이_단계가_다루지_않는다():
    report = _report(
        identity=(
            _sentence(_파트너_문장, ("12",)),
            _sentence(_파트너_문장_변형, ("12",)),
        )
    )

    _, 뺀수 = drop_cross_section_duplicates(report)

    assert 뺀수 == 0


def test_짧은_문장은_비교하지_않는다():
    """짧은 문장은 우연히 많이 겹친다 — 잘못 지우는 쪽이 더 나쁘다."""
    report = _report(
        identity=(_sentence("매출이 늘었다.", ("12",)),),
        portfolio=(_sentence("매출이 늘었다.", ("12",)),),
    )

    _, 뺀수 = drop_cross_section_duplicates(report)

    assert 뺀수 == 0


# ══════════════════════════════════════════════════════════
# ★ 도식 재료 보존 — 문장을 옮기는 단계가 그림을 지우면 안 된다
# ══════════════════════════════════════════════════════════


def test_문장을_빼도_경로표는_남는다():
    """★ 실측 결함 — 7장 흐름도가 두 번 연속 안 나온 진짜 원인.

    중복 제거가 ComposedSection을 다시 만들면서 flow_rows를 안 넘겨,
    7장에서 문장이 하나라도 빠지면 도식 재료가 통째로 사라졌다.
    그러면 뒤따르는 도식 검증도 볼 것이 없어 아무 일도 안 하고,
    화면에는 흐름도가 영영 안 나온다.
    """
    경로 = (
        FlowRow(cells=("수지", "가공", "가구사"), citations=("12",)),
        FlowRow(cells=("폐플라스틱", "열분해유", "폐기물 사업장"), citations=("13",)),
    )
    report = ComposedReport(
        sections=tuple(
            ComposedSection(
                section_id=section_id,
                sentences=(
                    (_sentence(_파트너_문장, ("12",)),)
                    if section_id == "identity"
                    else (
                        _sentence(_파트너_문장_변형, ("12",)),
                        _sentence(_공연_문장, ("12", "13")),
                    )
                    if section_id == "operations_partners"
                    else ()
                ),
                flow_rows=경로 if section_id == "operations_partners" else (),
            )
            for section_id in SECTION_IDS
        )
    )

    새보고서, 뺀수 = drop_cross_section_duplicates(report)

    assert 뺀수 >= 1  # 실제로 문장이 빠지는 상황이어야 의미가 있다
    운영 = next(
        s for s in 새보고서.sections if s.section_id == "operations_partners"
    )
    assert 운영.flow_rows == 경로, "문장을 옮기면서 도식 재료가 사라졌습니다"


def test_문장이_다_빠져도_경로표는_남는다():
    """장이 비어도 도식은 남는다 — 그림은 문장과 별개 재료다."""
    경로 = (FlowRow(cells=("수지", "가공", "가구사"), citations=("12",)),)
    report = ComposedReport(
        sections=tuple(
            ComposedSection(
                section_id=section_id,
                sentences=(
                    (_sentence(_파트너_문장, ("12",)),)
                    if section_id == "operations_partners"
                    else (
                        _sentence(_파트너_문장_변형, ("12",)),
                        _sentence(_공연_문장, ("12",)),
                    )
                    if section_id == "identity"
                    else ()
                ),
                flow_rows=경로 if section_id == "operations_partners" else (),
            )
            for section_id in SECTION_IDS
        )
    )

    새보고서, _ = drop_cross_section_duplicates(report)

    운영 = next(
        s for s in 새보고서.sections if s.section_id == "operations_partners"
    )
    assert 운영.flow_rows == 경로
