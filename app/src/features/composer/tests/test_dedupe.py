"""장 간 중복 제거를 못 박는다 (사실 단일 소유의 «강제» 단계).

★ 왜 이 시험이 있나 (실측) — 프롬프트 지침(순차 작성·장별 소유 경계)만으로는 숫자 없는
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

import logging

from src.features.composer.constants import (
    NOTICE_DUPLICATE_MOVED,
    NOTICE_DUPLICATE_MOVED_TABLE_KEPT,
    SECTION_IDS,
)
from src.features.composer.dedupe import (
    drop_cross_section_duplicates,
    sections_with_program_tables,
)
from src.features.composer.port import (
    CollectedFragment,
    ComposedReport,
    ComposedSection,
    ComposedSentence,
    FlowRow,
    NewsRow,
    PerformanceTable,
)

_LOGGER_NAME = "src.features.composer.dedupe"

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


# ══════════════════════════════════════════════════════════
# ⑥ 장별 문장 수를 로그로 남긴다 (무과금 진단용)
# ══════════════════════════════════════════════════════════


def test_장별_문장_수가_로그로_남는다(caplog):
    """문장이 빠진 장·그대로인 장 모두 «정리 전→후» 개수가 한 줄로 남는다."""
    report = _report(
        identity=(_sentence(_파트너_문장, ("12",)),),
        portfolio=(_sentence(_파트너_문장_변형, ("12",)),),
        operations_partners=(
            _sentence(_파트너_문장, ("12",)),
            _sentence(_공연_문장, ("12", "13")),
        ),
    )

    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        새보고서, 뺀수 = drop_cross_section_duplicates(report)

    # 반환값은 이 로그와 무관하게 그대로다 (다른 시험과 같은 재료로 재확인).
    assert 뺀수 == 2
    assert _texts(새보고서, "identity") == []
    assert _texts(새보고서, "operations_partners") == [_파트너_문장, _공연_문장]

    assert "장별 문장 수(정리 전→후)" in caplog.text
    assert "identity:1→0" in caplog.text
    assert "portfolio:1→0" in caplog.text
    assert "operations_partners:2→2" in caplog.text
    # 문장 본문이 로그에 그대로 새면 안 된다 (회사 원문 보호).
    assert _파트너_문장 not in caplog.text


def test_지울_것이_없어도_장별_문장_수를_남긴다(caplog):
    """중복이 없어 아무것도 안 지워도(뺀수 0) 개수는 그대로 로그에 남는다."""
    report = _report(
        identity=(_sentence(_파트너_문장, ("12",)),),
        culture=(_sentence(_다른_사실, ("12",)),),
    )

    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        _, 뺀수 = drop_cross_section_duplicates(report)

    assert 뺀수 == 0
    assert "identity:1→1" in caplog.text
    assert "culture:1→1" in caplog.text


def test_문장이_하나뿐이어도_장별_문장_수를_남긴다(caplog):
    """flat < 2로 즉시 끝나는 경로(문장 총 1개 이하)도 로그가 남는다."""
    report = _report(identity=(_sentence(_다른_사실, ("12",)),))

    with caplog.at_level(logging.INFO, logger=_LOGGER_NAME):
        _, 뺀수 = drop_cross_section_duplicates(report)

    assert 뺀수 == 0
    assert "identity:1→1" in caplog.text


# ══════════════════════════════════════════════════════════
# ⑦ 5장↔6장 동점은 «정본 순서»가 아니라 «시제»로 가른다
# ══════════════════════════════════════════════════════════
#
# ★ 왜 필요한가 (실측 — 4차 유료 실행 멀티캠퍼스, 보고서 run e193846f) —
#   회사가 사업보고서에 「…확보해 나가겠습니다」라고 적은 한 사실을 작가가 두 장에
#   옮겼다. 6장에는 계획 어투로, 5장에는 「…확보하려 하고 있다」는 진행 어투로.
#   근거가 같으니 이 단계가 하나를 뺐는데, 두 장이 각각 한 문장씩이라 «깊이»가
#   동점이었고 동점이면 정본 목차에서 앞선 5장이 이겼다. 6장은 통째로 비었다.
#   미래 계획은 현재 과제와 같은 근거를 쓰는 일이 잦아 6장이 구조적으로 진다.
# ★ 아래 5장 문장은 그 실행의 보고서에 실제로 실린 글자 그대로다.
#   6장 문장은 «옮겨져 사라졌기 때문에» 산출물에 남아 있지 않다 — 그래서 원문
#   공시의 어투(「…확보해 나가겠습니다」)로 되살린 재구성이다. 재구성인 것을
#   여기 밝혀 둔다. 남아 있는 것은 그 장의 안내문이 NOTICE_DUPLICATE_MOVED
#   였다는 사실뿐이고, 그 문자열은 이 단계에서만 붙는다.
#
# ⚠️ 이 규칙이 지키는 것은 «미래 표지가 있는 중복 문장의 소유를 6장으로 준다»
#    까지다. «6장이 비지 않게 한다»가 아니다 — 실측 r4는 5장에도 그 문장
#    하나뿐이어서, 소유가 6장으로 가면 이번에는 5장이 빈다(독립 검토가 같은
#    입력에 base/new를 나란히 돌려 5장 1→0 · 6장 0→1 확인). 빈 장을 없애려면
#    같은 근거가 5·6장 후보로 함께 뽑히는 상류와 6장 관문을 고쳐야 한다.
#    아래 시험들도 «어느 장이 소유하는가»만 단정한다.

_실측_5장_진행어투 = (
    "회사는 이러한 교육서비스 부문의 매출 감소에 대응하여 AI 교육 체계를 "
    "고도화하고 진단 기반 리더십 교육을 강화함으로써 안정적인 매출 기반과 "
    "차별화된 수익 모델을 확보하려 하고 있다."
)
_재구성_6장_계획어투 = (
    "회사는 진단 기반 리더십 교육을 강화함으로써 안정적인 매출 기반과 "
    "차별화된 수익 모델을 확보해 나가겠다고 밝혔다."
)
_5장_계획어투 = (
    "회사는 매출 감소에 대응해 진단 기반 리더십 교육을 강화함으로써 안정적인 "
    "매출 기반과 차별화된 수익 모델을 확보할 계획이다."
)
_6장_진행어투 = (
    "회사는 진단 기반 리더십 교육을 강화함으로써 안정적인 매출 기반과 "
    "차별화된 수익 모델을 확보하고 있다."
)


def test_5장6장_동점이면_미래를_말한_6장이_소유한다():
    """실측 회귀 — 같은 근거를 5장은 진행형, 6장은 계획형으로 썼다."""
    report = _report(
        current_challenges=(_sentence(_실측_5장_진행어투, ("153",)),),
        future_strategy=(_sentence(_재구성_6장_계획어투, ("153",)),),
    )

    새보고서, 뺀수 = drop_cross_section_duplicates(report)

    assert 뺀수 == 1
    assert _texts(새보고서, "future_strategy") == [_재구성_6장_계획어투]
    assert _texts(새보고서, "current_challenges") == []


def test_6장이_진행형이면_기존대로_앞선_5장이_소유한다():
    """미래 표지가 6장 쪽에 없으면 가를 근거가 없다 — 순서 규칙 그대로."""
    report = _report(
        current_challenges=(_sentence(_실측_5장_진행어투, ("153",)),),
        future_strategy=(_sentence(_6장_진행어투, ("153",)),),
    )

    새보고서, 뺀수 = drop_cross_section_duplicates(report)

    assert 뺀수 == 1
    assert _texts(새보고서, "current_challenges") == [_실측_5장_진행어투]
    assert _texts(새보고서, "future_strategy") == []


def test_두_장_모두_미래를_말하면_한쪽으로_몰지_않는다():
    """둘 다 계획형이면 시제로는 못 가른다 — 기존 순서 규칙에 맡긴다."""
    report = _report(
        current_challenges=(_sentence(_5장_계획어투, ("153",)),),
        future_strategy=(_sentence(_재구성_6장_계획어투, ("153",)),),
    )

    새보고서, 뺀수 = drop_cross_section_duplicates(report)

    assert 뺀수 == 1
    assert _texts(새보고서, "current_challenges") == [_5장_계획어투]


def test_시제_규칙은_5장6장_밖의_쌍에는_걸리지_않는다():
    """1장↔6장은 시제로 갈리는 쌍이 아니다 — 정본 순서 그대로 1장이 이긴다."""
    report = _report(
        identity=(_sentence(_실측_5장_진행어투, ("153",)),),
        future_strategy=(_sentence(_재구성_6장_계획어투, ("153",)),),
    )

    새보고서, 뺀수 = drop_cross_section_duplicates(report)

    assert 뺀수 == 1
    assert _texts(새보고서, "identity") == [_실측_5장_진행어투]
    assert _texts(새보고서, "future_strategy") == []


def test_깊이가_더_깊은_장은_시제보다_먼저_이긴다():
    """시제는 «동점»을 가르는 규칙이다 — 깊이 규칙을 밀어내지 않는다."""
    다른_문장 = (
        "회사는 국내 경기위축에 따른 공공기관 및 교육예산 축소로 교육서비스 "
        "부문의 매출이 감소했다고 밝혔다."
    )
    report = _report(
        current_challenges=(
            _sentence(_실측_5장_진행어투, ("153",)),
            _sentence(다른_문장, ("153",)),
        ),
        future_strategy=(_sentence(_재구성_6장_계획어투, ("153",)),),
    )

    새보고서, 뺀수 = drop_cross_section_duplicates(report)

    assert 뺀수 == 1
    assert _실측_5장_진행어투 in _texts(새보고서, "current_challenges")
    assert _texts(새보고서, "future_strategy") == []


def test_시제_판정은_생산_미래표지_함수를_그대로_쓴다():
    """목록을 두 벌로 만들지 않았는지 «행동»으로 묶는다.

    6장 장 배치 관문이 통과시키는 어투는 이 단계에서도 미래로 읽혀야 한다.
    한쪽만 고쳐지면 「관문은 통과했는데 소유는 못 가져가는」 장이 생긴다.
    """
    from src.features.composer.future_plan_guard import (
        future_section_prose_problem,
        has_forward_marker,
    )

    assert has_forward_marker(_재구성_6장_계획어투) is True
    assert has_forward_marker(_실측_5장_진행어투) is False
    assert future_section_prose_problem(_재구성_6장_계획어투) == ""
    assert future_section_prose_problem(_실측_5장_진행어투) == (
        "future_section_no_forward_statement"
    )


# ══════════════════════════════════════════════════════════
# ⑧ 안내문과 화면이 어긋나지 않는다 — 표가 남으면 남는다고 적는다
# ══════════════════════════════════════════════════════════
#
# ★ 왜 필요한가 (실측 — 4차 유료 실행 멀티캠퍼스 6장) — 「이 장에 담겼던 내용이
#   … 그쪽으로 모았습니다」라고 적어 놓고 바로 아래에 「회사가 밝힌 성장 계획」
#   표(2026년 이후)를 그대로 실었다. 표는 문장과 별개 재료라 남는 것이 맞다.
#   틀린 것은 «표가 남았다는 말이 없는» 안내문이다.


def _표_남는_보고서(표_있는_장: str, 표) -> ComposedReport:
    return ComposedReport(
        sections=tuple(
            ComposedSection(
                section_id=section_id,
                sentences=(
                    (_sentence(_파트너_문장, ("12",)),)
                    if section_id == 표_있는_장
                    else (
                        _sentence(_파트너_문장_변형, ("12",)),
                        _sentence(_공연_문장, ("12",)),
                    )
                    if section_id == "identity"
                    else ()
                ),
                flow_rows=표 if section_id == 표_있는_장 else (),
            )
            for section_id in SECTION_IDS
        )
    )


def test_표가_남는_장은_안내문도_표가_남았다고_말한다():
    경로 = (FlowRow(cells=("수지", "가공", "가구사"), citations=("12",)),)

    새보고서, _ = drop_cross_section_duplicates(
        _표_남는_보고서("operations_partners", 경로)
    )

    비워진_장 = next(
        s for s in 새보고서.sections if s.section_id == "operations_partners"
    )
    assert 비워진_장.sentences == ()
    assert 비워진_장.flow_rows == 경로
    assert 비워진_장.notice == NOTICE_DUPLICATE_MOVED_TABLE_KEPT
    assert "표" in 비워진_장.notice, "표가 남았다는 말이 안내문에 없습니다"
    assert "자료가 없어서" in 비워진_장.notice


def test_표가_없는_장의_안내문은_그대로다():
    """표가 없으면 늘리지 않는다 — 화면에 없는 것을 말하면 그것도 거짓이다."""
    새보고서, _ = drop_cross_section_duplicates(_표_남는_보고서("portfolio", ()))

    비워진_장 = next(s for s in 새보고서.sections if s.section_id == "portfolio")
    assert 비워진_장.notice == NOTICE_DUPLICATE_MOVED
    assert "표" not in 비워진_장.notice


def test_보도표가_남아도_안내문이_표를_말한다():
    보도 = (
        NewsRow(
            cells=("2025-09-15", "mk.co.kr", "수도전기공고 OPIc 위탁 교육"),
            citations=("210",),
        ),
    )
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
                news_rows=보도 if section_id == "operations_partners" else (),
                news_decisions=(
                    (("209", "검수후미반영", "본문 검수를 통과하지 못했습니다."),)
                    if section_id == "operations_partners"
                    else ()
                ),
            )
            for section_id in SECTION_IDS
        )
    )

    새보고서, _ = drop_cross_section_duplicates(report)

    비워진_장 = next(
        s for s in 새보고서.sections if s.section_id == "operations_partners"
    )
    assert 비워진_장.news_rows == 보도, "문장을 옮기면서 보도표가 사라졌습니다"
    assert 비워진_장.news_decisions != (), "뉴스 제외 사유가 사라졌습니다"
    assert 비워진_장.notice == NOTICE_DUPLICATE_MOVED_TABLE_KEPT


# ══════════════════════════════════════════════════════════
# ⑨ 장이 «들고 있지 않은» 표도 안내문 판정에 넣는다
# ══════════════════════════════════════════════════════════
#
# ★ 왜 필요한가 (독립 검토 지적) — 실적표(4장)·매출 구성표(2·3장)는
#   `ComposedSection`의 칸이 아니라 `render_report`의 인자다. 그래서 ⑧의
#   안내문 분기가 그 장들에서는 아예 걸리지 않았고, 6장에서 고친 어긋남
#   (「그쪽으로 모았습니다」라고 적고 바로 아래 표를 싣는 것)이 2·3·4장에
#   그대로 남아 있었다.


def _네장이_비는_보고서() -> ComposedReport:
    """4장 문장이 1장으로 옮겨져 4장이 비는 보고서 (표는 안 들고 있다)."""
    return _report(
        past_changes=(_sentence(_파트너_문장, ("12",)),),
        identity=(
            _sentence(_파트너_문장_변형, ("12",)),
            _sentence(_공연_문장, ("12",)),
        ),
    )


def test_밖에서_들어오는_표가_있는_장도_안내문이_표를_말한다():
    새보고서, 뺀수 = drop_cross_section_duplicates(
        _네장이_비는_보고서(), sections_with_tables=("past_changes",)
    )

    assert 뺀수 == 1
    사장 = next(s for s in 새보고서.sections if s.section_id == "past_changes")
    assert 사장.sentences == ()
    assert 사장.flow_rows == () and 사장.news_rows == ()
    assert 사장.notice == NOTICE_DUPLICATE_MOVED_TABLE_KEPT


def test_표가_있는_장을_안_넘기면_종전_동작이다():
    """새 인자는 «더 알려 주는» 것이지 기존 동작을 바꾸지 않는다."""
    새보고서, _ = drop_cross_section_duplicates(_네장이_비는_보고서())

    사장 = next(s for s in 새보고서.sections if s.section_id == "past_changes")
    assert 사장.notice == NOTICE_DUPLICATE_MOVED


def test_다른_장의_표는_이_장_안내문에_영향을_주지_않는다():
    새보고서, _ = drop_cross_section_duplicates(
        _네장이_비는_보고서(), sections_with_tables=("business_model",)
    )

    사장 = next(s for s in 새보고서.sections if s.section_id == "past_changes")
    assert 사장.notice == NOTICE_DUPLICATE_MOVED


def test_표가_있는_장_판정이_실적표와_구성표를_모두_본다():
    """캡션은 리터럴로 적는다 — 생산 상수를 import해 맞추면 순환 검증이 된다."""
    실적표 = PerformanceTable(
        caption="3개년 주요 실적",
        headers=("구분", "2024", "2025"),
        rows=(("매출액", "1,100", "1,243"),),
    )
    빈_실적표 = PerformanceTable(caption="3개년 주요 실적", headers=(), rows=())
    제품_구성표 = PerformanceTable(
        caption="무엇을 팔아 번 돈인가 — 제품·서비스별 매출 비중",
        headers=("구분", "비중"),
        rows=(("교육서비스", "76%"),),
    )
    지역_구성표 = PerformanceTable(
        caption="어디서 번 돈인가 — 지역별 매출 비중",
        headers=("구분", "비중"),
        rows=(("국내", "88%"),),
    )

    assert sections_with_program_tables(실적표) == frozenset({"past_changes"})
    assert sections_with_program_tables(빈_실적표) == frozenset()
    assert sections_with_program_tables(None) == frozenset()
    # 구성표는 «축»에 따라 3장·2장으로 갈린다 — 이 갈래가 빠지면 그 장들에서
    # 안내문이 다시 어긋난다.
    assert sections_with_program_tables(None, (제품_구성표,)) == frozenset(
        {"portfolio"}
    )
    assert sections_with_program_tables(None, (지역_구성표,)) == frozenset(
        {"business_model"}
    )
    assert sections_with_program_tables(실적표, (제품_구성표, 지역_구성표)) == (
        frozenset({"past_changes", "portfolio", "business_model"})
    )


def test_캡션을_못_읽는_구성표는_보고서_생성을_멈추지_않는다():
    """안내문 한 줄 때문에 더 이른 자리에서 죽이지 않는다 — 렌더가 다시 본다."""
    이상한_표 = PerformanceTable(
        caption="알 수 없는 표", headers=("구분",), rows=(("값",),)
    )

    assert sections_with_program_tables(None, (이상한_표,)) == frozenset()


def test_표가_실리는_장_판정이_렌더_결과와_어긋나지_않는다():
    """장 배정 규칙을 두 벌로 만들지 않았는지 «렌더 결과»로 맞춰 본다.

    안내문이 「표가 남는다」고 적었는데 화면에 표가 없으면 그것도 거짓말이다.
    그래서 «내가 표가 있다고 본 장»은 렌더가 실제로 표를 실은 장에 들어 있어야
    한다(반대 방향은 아래 주석의 3장 이름표처럼 아직 못 보는 자리가 있다).
    """
    from src.features.composer.render import render_report

    실적표 = PerformanceTable(
        caption="3개년 주요 실적",
        headers=("구분", "2024", "2025"),
        rows=(("매출액", "1,100", "1,243"),),
        cite="[1]",
    )
    보고서 = _report(
        past_changes=(_sentence("회사의 2025년 매출액은 1,243억원이다.", ("1",)),),
    )
    조각 = (
        CollectedFragment(
            fragment_id="1", kind="사업내용", text="가나다전자는 검사 장비를 만든다."
        ),
    )

    rendered = render_report("가나다전자(주)", 보고서, 조각, 실적표)

    표가_실린_장 = {section.cell for section in rendered.sections if section.tables}
    assert 표가_실린_장, "이 시험의 픽스처가 표를 하나도 안 내면 아무것도 증명 못 한다"
    assert sections_with_program_tables(실적표) <= 표가_실린_장


def test_운영_경로가_표가_있는_장을_실제로_넘긴다(monkeypatch):
    """★ 배선 단정 — 넘기지 않으면 이 규칙은 운영에서 통째로 꺼진다.

    운영 진입점(run_v2)을 실제로 돌리고 «그 호출이 받은 인자»를 단정한 뒤,
    그 값 그대로 중복 제거를 돌려 안내문까지 확인한다. 시험 안에서 따로 만든
    값으로 검사하면 운영 배선이 빠져도 초록불이 된다.
    """
    from src.features.composer import dedupe as dedupe_module
    from src.features.composer import pipeline
    from src.features.composer.tests.test_pipeline import (
        _FakeReviewer,
        _FakeWriter,
        _raw_fragments,
        _structured_financial_table,
    )

    seen: list[dict] = []

    def spy(*args, **kwargs):
        seen.append(kwargs)
        return dedupe_module.drop_cross_section_duplicates(*args, **kwargs)

    monkeypatch.setattr(pipeline, "drop_cross_section_duplicates", spy)

    pipeline.run_v2(
        "가나다전자", _raw_fragments(), _structured_financial_table(),
        writer_ask=_FakeWriter(), reviewer_ask=_FakeReviewer(),
        corp_type="상장사", as_of_date="2026-08-24",
    )

    assert seen, "운영 경로가 중복 제거를 아예 부르지 않았습니다"
    for kwargs in seen:
        assert "sections_with_tables" in kwargs, (
            "표가 있는 장을 안 넘기는 drop_cross_section_duplicates 호출이 있습니다"
        )
        assert "past_changes" in kwargs["sections_with_tables"], (
            "실적표가 실리는 4장을 안내문 판정에 넘기지 않았습니다"
        )

    # ★ 여기부터가 핵심 — «운영이 실제로 넘긴 그 값»으로 안내문을 확인한다.
    새보고서, _ = dedupe_module.drop_cross_section_duplicates(
        _네장이_비는_보고서(), sections_with_tables=seen[0]["sections_with_tables"]
    )
    사장 = next(s for s in 새보고서.sections if s.section_id == "past_changes")
    assert 사장.sentences == ()
    assert 사장.notice == NOTICE_DUPLICATE_MOVED_TABLE_KEPT


def test_모든_호출부가_표가_있는_장을_넘긴다():
    """보충 경로처럼 위 시험이 못 도는 호출부까지 «구문»으로 전수 확인한다."""
    import ast
    import inspect

    from src.features.composer import pipeline

    tree = ast.parse(inspect.getsource(pipeline))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "drop_cross_section_duplicates"
    ]
    assert len(calls) >= 2, "호출부가 줄었습니다 — 배선 단정을 다시 맞추세요"
    for call in calls:
        keywords = {keyword.arg: keyword.value for keyword in call.keywords}
        assert "sections_with_tables" in keywords, (
            f"{call.lineno}행의 호출이 표가 있는 장을 넘기지 않습니다"
        )
        assert ast.unparse(keywords["sections_with_tables"]) == (
            "sections_with_program_tables(performance_table, composition_tables)"
        ), f"{call.lineno}행이 다른 값을 넘깁니다"
