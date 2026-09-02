"""진짜 알맹이가 «화면에 내보내기 직전» 거르는 세 가지를 못 박는다.

① 표 덩어리 · ② 회계기준 설명 문구 · ③ 알맹이 검사 결과

★ 특히 ③ — 전에는 AI 3회를 써서 3회 다수결까지 내고 **결과를 통째로 버렸다.**
  `_sections_from`이 판정을 인자로 받지도 않았고, 등급 계산은 dict의 «키»만
  검사해서 「알맹이 없음」으로 판정된 칸이 그대로 화면에 나갔다.

⚠️ ③에는 «문장이 실제로 있던 칸에만 건다»는 단서가 붙는다. 엔진 프롬프트가
   「문장이 없는 칸은 false」로 지시하기 때문에, 빈 칸의 false는 「내용이 나쁘다」가
   아니라 「비어 있었다」는 뜻이다. 빈 칸에까지 걸면 나중에 그 칸을 채워도
   통째로 다시 숨겨진다 (수정과 정면 충돌).
"""

from __future__ import annotations

from dataclasses import dataclass

from src.core.constants import SUBSTANCE_FAILED_REASON
from src.features.pipeline.real import _sections_from


@dataclass
class _가짜문장:
    """1판 엔진의 `DraftItem`이 `_sections_from`에 주는 최소한의 모양."""

    block: str
    sentence: str
    fragment_id: int = 1


class _가짜엔진:
    EMPTY_REASONS: dict[str, str] = {}


_조각 = {1: {"종류": "사업내용", "원문": ""}}
_사업문장 = "당사는 재생의학 기술을 기반으로 의약품과 의료기기를 제조·판매하고 있습니다."


def _칸(sections, cell: str):
    return next(s for s in sections if s.cell == cell)


# ══════════════════════════════════════════════════════════
# ③ 알맹이 검사 결과 반영
# ══════════════════════════════════════════════════════════


def test_알맹이_검사에서_떨어진_칸은_문장이_화면에_안_나간다():
    """★ 이 시험이 깨지면 AI 3회를 쓰고도 판정을 버리던 상태로 되돌아간 것이다."""
    kept = [_가짜문장(block="1", sentence=_사업문장)]

    sections, _ = _sections_from(kept, _조각, _가짜엔진(), {"1": False})

    칸1 = _칸(sections, "1")
    assert 칸1.lines == []
    assert 칸1.is_filled is False
    assert 칸1.empty_reason == SUBSTANCE_FAILED_REASON


def test_알맹이_검사를_통과한_칸은_그대로_나간다():
    kept = [_가짜문장(block="1", sentence=_사업문장)]

    sections, _ = _sections_from(kept, _조각, _가짜엔진(), {"1": True})

    칸1 = _칸(sections, "1")
    assert [문장 for 문장, _ in 칸1.lines] == [_사업문장]
    assert 칸1.is_filled is True


def test_판정을_안_넘기면_예전처럼_거르지_않는다():
    """`engine_cells`가 없는 호출(데모·시험)에서 문장이 사라지면 안 된다."""
    kept = [_가짜문장(block="1", sentence=_사업문장)]

    sections, _ = _sections_from(kept, _조각, _가짜엔진())

    assert _칸(sections, "1").is_filled is True


def test_비어_있던_칸의_탈락_판정은_사유를_덮어쓰지_않는다():
    """★★ 수정분을 도로 숨기지 않기 위한 안전핀.

    엔진은 「문장이 없는 칸은 false」로 판정한다. 그 false를 「내용이 나쁘다」로
    읽으면, 나중에 그 칸을 채워도 알맹이 판정이 계속 false라 통째로 숨겨진다.
    """
    kept = [_가짜문장(block="1", sentence=_사업문장)]
    빈칸사유 = "수집 자료에 해당 재료 없음"

    # 4-1은 애초에 문장이 없고, 엔진 판정도 false다.
    sections, _ = _sections_from(kept, _조각, _가짜엔진(), {"1": True, "4-1": False})

    칸41 = _칸(sections, "4-1")
    assert 칸41.is_filled is False
    assert 칸41.empty_reason == 빈칸사유, (
        "빈 칸에 「알맹이 미달」 사유를 붙이면 «왜 비었는지»가 거짓이 된다"
    )


# ══════════════════════════════════════════════════════════
# ② 회계기준 설명 문구
# ══════════════════════════════════════════════════════════


def test_회계기준_설명_문구는_화면에_안_나간다():
    회계문구 = (
        "수익인식모형 연결회사의 고객과의 계약에서 생기는 수익은 "
        "제품매출, 기타매출로 구성되어 있습니다."
    )
    kept = [_가짜문장(block="1", sentence=회계문구)]

    sections, _ = _sections_from(kept, _조각, _가짜엔진(), {"1": True})

    칸1 = _칸(sections, "1")
    assert 칸1.lines == []
    assert "회계기준" in 칸1.empty_reason, "왜 비었는지 사유가 있어야 한다"


def test_회계_낱말이_들어간_좋은_실적_문장은_살아남는다():
    """★ 오거부 안전핀 — 「매출·수익」을 낱말로 거르면 이 문장이 죽는다 (D12-b)."""
    실적문장 = (
        "2025년 연결기준 매출액 5,363억원, 영업이익 2,144억원, "
        "당기순이익 1,683억원의 실적을 달성하였습니다."
    )
    kept = [_가짜문장(block="3", sentence=실적문장)]

    sections, _ = _sections_from(kept, _조각, _가짜엔진(), {"3": True})

    assert [문장 for 문장, _ in _칸(sections, "3").lines] == [실적문장]
