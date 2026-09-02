"""서비스 핵심 상수와 서로 의존하는 값의 일관성을 검사한다."""

from __future__ import annotations

from src.core import constants as C


def test_비용과판정_안전상수가_승인된값이다():
    assert C.MAX_RETRY_INPUT == 3
    # ★ 숫자를 박지 않는다. «왜 그 숫자인지»를 지킨다.
    #   상한은 시간 규약이 정하는 천장을 넘을 수 없다(generation_singleflight.py:64).
    #   그리고 v2 가 한 보고서를 끝내는 데 필요한 최소 횟수보다 커야 한다.
    from src.features.budget.constants import PAID_PHASE_LEASE_SEC
    from src.features.pipeline.constants import ANTHROPIC_TIMEOUT_SEC

    하트비트 = 30.0  # generation_singleflight.HEARTBEAT_INTERVAL_SEC
    천장초 = PAID_PHASE_LEASE_SEC - (ANTHROPIC_TIMEOUT_SEC + 2 * 하트비트)
    assert C.MAX_AI_CALLS_PER_REQUEST * ANTHROPIC_TIMEOUT_SEC <= 천장초, (
        "★ 시간 규약을 넘었다 — generation_singleflight 가 import 부터 실패한다"
    )

    # v2 최소 필요 횟수: 9개 장 + 문장검증 + 도식 + 요약작성 + 요약검증 + 회사식별
    V2_최소_필요 = 9 + 1 + 1 + 1 + 1 + 1
    assert C.MAX_AI_CALLS_PER_REQUEST >= V2_최소_필요, (
        "★ 실측: 15 로는 요약 직전에 상한을 넘겨 보고서가 통째로 실패했다"
    )
    assert C.MIN_FILLED_CELLS == 4
    assert C.VOTE_ROUNDS == 2
    assert C.AUDIT_WINDOW_YEARS == 3


def test_세는_칸은_6개다():
    """공고 블록(5·8)과 숨긴 6·7·9·附는 등급·안내·관측에 세지 않는다."""
    assert len(C.COUNTED_CELLS) == 6
    assert "5" not in C.COUNTED_CELLS
    assert "9" not in C.COUNTED_CELLS
    assert "附" not in C.COUNTED_CELLS


def test_캐시_표시값이_이력_허용값과_같다():
    """표시값과 관측 이력의 허용값이 어긋나면 캐시 기록이 거부된다."""
    from src.features.observability import constants as obs

    assert C.CACHE_HIT_LAYER1 == obs.CACHE_HIT_L1
    assert C.CACHE_HIT_LAYER1 in obs.CACHE_HIT_VALUES


def test_사실선택은_최대두번이고_닫힌검증_통과한_한표를_유지한다():
    assert C.VOTE_ROUNDS == 2
    assert C.VOTE_MIN == 1


def test_부분_완성_문턱은_성립_문턱보다_낮다():
    assert C.PARTIAL_MIN_CELLS < C.MIN_FILLED_CELLS


def test_진행_단계는_회사분석_흐름_7단계다():
    assert len(C.PROGRESS_STEPS) == 7
    keys = [key for key, _ in C.PROGRESS_STEPS]
    assert len(set(keys)) == len(keys), "단계 키가 겹칩니다"
    assert "posting" not in keys


def test_모든_세는_칸에_이름이_있다():
    for cell in C.COUNTED_CELLS:
        assert cell in C.CELL_LABELS, f"{cell}번 칸의 이름이 없습니다"


def test_보고서_순서에_모든_칸이_들어_있다():
    for cell in C.CELL_LABELS:
        assert cell in C.REPORT_CELL_ORDER, f"{cell}번이 보고서 순서에서 빠졌습니다"


def test_레거시_참고_키는_제목을_중복하지_않는다():
    assert C.section_display_parts("附", "참고 지표") == ("참고", "지표")
    assert C.section_display_heading("附", "참고 지표") == "참고 지표"
    assert C.section_display_heading("1", "사업 구조") == "1. 사업 구조"
