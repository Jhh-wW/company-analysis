"""이력 → 대시보드가 그릴 값. **더하고 나누기만 한다** — 새로 계산할 것이 없어야 한다.

정본: 확정/08_관측/2_규칙/01_대시보드범위.md (화면 6영역) ·
      확정/08_관측/2_규칙/02_지표산출.md (지표 계산식·산출 주체)

★ 지표 6종 중 이 이력 13종만으로 «못 재는» 것이 둘 있다. 함수 `_build_quality`의
  주석에 왜 못 재는지 적어 두었다 — 지어내지 않고 `None`으로 둔다.
"""

from __future__ import annotations

import datetime as dt
import math
from collections import Counter
from dataclasses import dataclass, field, replace

from src.features.observability.constants import (
    COUNTED_CELLS,
    END_STEP_COMPLETE,
    ERROR_END_STEPS,
    FUNNEL_STAGES,
    GRADE_ORDER,
    HUMAN_CHECK_MATCH,
    METRIC_ANSWER_RELEVANCY,
    METRIC_CONTEXT_PRECISION,
    METRIC_CONTEXT_RECALL,
    METRIC_FAITHFULNESS,
    METRIC_JUDGE_AGREEMENT,
    METRIC_JUDGE_STABILITY,
    PERCENT_DECIMALS,
    RECENT_LIMIT,
)
from src.core.constants import REPLAY_MODEL_MARK
from src.features.observability.records import RunRecord


@dataclass(frozen=True)
class Dashboard:
    """화면이 그릴 값. **더하고 나누기만** 한다 — 새로 계산할 것이 없어야 한다."""

    total: int                                  # 전체 건수
    today: int                                  # 오늘 건수
    errors: int                                 # 오류로 끝난 건수
    cost_month_krw: float                       # 당월 비용 (원장 연결 전에는 이력 호환값)
    #: 당월 건수 중 «데모»가 몇 건인가. ★ 비용이 0원인 «이유»를 화면이 말하게 한다 —
    #: 이 값이 없으면 관리자가 「비용 집계가 고장 났다」고 오해한다.
    replay_month: int                           # 당월 데모 건수
    cost_from_ledger: bool                      # 비용이 SQLite 단계 원장에서 왔는가
    cost_ledger_error: bool                     # 원장을 못 읽어 금액을 표시하면 안 되는가
    unresolved_cost_runs: int                   # 당월 비용 미확정 요청 수(단계 수 아님)
    cost_ledger_since: str                      # 원장 최초 기록일(도입 전 기간 오해 방지)
    model: str                                  # 지금 쓰는 모델
    funnel: list[tuple[str, int, int]]          # (단계, 도달 건수, 여기서 이탈한 건수)
    grades: dict[str, int]                      # 등급 → 건수
    cell_fill_rate: list[tuple[str, float]]     # (칸 번호, 채움률 0~100)
    quality: dict[str, float | None]            # 지표 이름 → % (못 재면 None)
    recent: list[RunRecord] = field(default_factory=list)  # 최근 건 (개별 조회용)


def _latest_per_run(records: list[RunRecord]) -> list[RunRecord]:
    """같은 요청이 여러 줄이면 마지막 것만 남긴다 (사후 정정 대응).

    ★ 「지운다」가 아니라 「집계에서 옛 줄을 안 센다」이다.
      이력 파일 자체는 그대로 둔다 — 이력은 지우지 않는다.
    """
    latest: dict[str, RunRecord] = {}
    for record in records:
        latest[record.run_id] = record
    return list(latest.values())


def build_dashboard(
    records: list[RunRecord],
    *,
    today: dt.date,
    model: str,
    cost_month_krw_override: float | None = None,
    cost_ledger_error: bool = False,
    unresolved_cost_runs: int = 0,
    cost_ledger_since: str = "",
) -> Dashboard:
    """이력 전체를 한 번에 집계해 대시보드 값으로 바꾼다.

    Args:
        records: `records.read_records()`가 돌려준 이력 행 전체.
        today: 「오늘」로 볼 날짜. 서버 시각에 매번 의존하지 않도록 인자로 받는다
            (시험에서 특정 날짜를 고정해 넣을 수 있어야 한다).
        model: 지금 서비스가 쓰는 AI 모델 버전. 이력이 아니라 **지금 설정값**이다
            — 이력에도 모델 버전이 찍히지만(과거 기록용), 「지금 뭘 쓰나」는 별개다.

    Returns:
        화면이 그대로 그릴 수 있는 `Dashboard`.

    ★ 같은 `run_id`가 여러 줄이면 **마지막 줄만** 쓴다.
      이력은 덧붙이기 전용이라, 사람이 나중에 [일치]/[불일치]를 기록하면
      «같은 요청이 한 줄 더» 쌓인다. 그대로 합치면 그 요청이 두 번 세어져
      건수·비용·지표가 전부 부풀어 오른다.

    ★ **비용만은 데모 기록을 빼고 센다** (문제로그 P-84).
      데모는 저장된 결과를 되돌려 줄 뿐 AI를 안 부른다 — **0원이다.**
      옛 데모 기록에는 시제품이 «옛날에» 쓴 돈이 들어 있어, 그대로 합치면
      실측 **791건 34,222원** 대 **진짜 지출 약 750원**처럼 45배로 부풀었다.
      ⚠️ **건수(`total`·`today`)는 그대로 센다** — 데모도 «실행»은 실행이다.
      틀린 것은 「얼마 썼나」뿐이므로, 고치는 것도 그 한 줄뿐이다.
    """
    if cost_month_krw_override is not None and (
        not math.isfinite(float(cost_month_krw_override))
        or float(cost_month_krw_override) < 0
    ):
        raise ValueError("비용 원장 월 합계는 유한한 0 이상의 값이어야 합니다")
    if unresolved_cost_runs < 0:
        raise ValueError("비용 미확정 요청 수는 음수일 수 없습니다")
    records = [_with_current_cells(record) for record in _latest_per_run(records)]
    return Dashboard(
        total=len(records),
        today=sum(1 for r in records if _at_date(r) == today),
        errors=sum(1 for r in records if r.end_step in ERROR_END_STEPS),
        cost_month_krw=(
            float(cost_month_krw_override)
            if cost_month_krw_override is not None
            else sum(
                r.cost_krw
                for r in records
                if _same_month(_at_date(r), today) and not _is_replay(r)
            )
        ),
        replay_month=sum(
            1 for r in records if _same_month(_at_date(r), today) and _is_replay(r)
        ),
        cost_from_ledger=cost_month_krw_override is not None,
        cost_ledger_error=cost_ledger_error,
        unresolved_cost_runs=int(unresolved_cost_runs),
        cost_ledger_since=cost_ledger_since,
        model=model,
        funnel=_build_funnel(records),
        grades=_build_grades(records),
        cell_fill_rate=_build_cell_fill_rate(records),
        quality=_build_quality(records),
        recent=_build_recent(records),
    )


# ══════════════════════════════════════════════════════════
# ③ 단계별 이탈
# ══════════════════════════════════════════════════════════


def _build_funnel(records: list[RunRecord]) -> list[tuple[str, int, int]]:
    """실제 종료 이유별 이탈 — 도달 건수는 앞 종료값 이탈을 뺀 나머지다.

    ★ `RunRecord.__post_init__`이 `end_step`을 `FUNNEL_STAGES`와 같은 값 집합으로
      제한해 두었으므로, 여기서 센 이탈 건수의 합은 반드시 `len(records)`와 같다.
    """
    dropout = Counter(r.end_step for r in records)
    reached = len(records)
    funnel: list[tuple[str, int, int]] = []
    for internal_key, label in FUNNEL_STAGES:
        left_here = dropout.get(internal_key, 0)
        funnel.append((label, reached, left_here))
        reached -= left_here
    return funnel


# ══════════════════════════════════════════════════════════
# ⑤ 완성도 분포
# ══════════════════════════════════════════════════════════


def _build_grades(records: list[RunRecord]) -> dict[str, int]:
    """등급별 건수. 완주하지 못한 요청(`grade == ""`)은 등급이 없으므로 세지 않는다."""
    counts = Counter(r.grade for r in records if r.grade)
    return {grade: counts.get(grade, 0) for grade in GRADE_ORDER}


def _build_cell_fill_rate(records: list[RunRecord]) -> list[tuple[str, float]]:
    """세는 칸 6개가 각각 몇 %에서 채워지는지 — 완주한 건에서만 의미가 있다.

    ★ 완주하지 못한 요청은 칸 판정 자체가 없다(`cells_missing`이 그냥 빈 리스트인
      기본값일 뿐, 「다 채웠다」는 뜻이 아니다). 섞으면 채움률이 부풀어 오른다.
      완주 건이 하나도 없으면 잴 수 없으므로 빈 목록을 돌려준다(0%로 지어내지 않는다).
    """
    completed = [r for r in records if r.end_step == END_STEP_COMPLETE]
    if not completed:
        return []
    total = len(completed)
    return [
        (
            cell,
            _percent(sum(1 for r in completed if cell not in r.cells_missing), total),
        )
        for cell in COUNTED_CELLS
    ]


def _with_current_cells(record: RunRecord) -> RunRecord:
    """집계에 직접 넘어온 옛 이력도 현재 6칸 규칙으로 재해석한다.

    보통은 `read_records()`가 먼저 같은 일을 한다. 다만 집계 함수를
    다른 코드가 직접 부르거나 시험에서 옛 모양을 넘겨도 9번이
    대시보드에 되살아나지 않게 하는 두 번째 안전핀이다.
    """
    if not record.grade:
        return record
    missing_set = set(record.cells_missing)
    missing = [cell for cell in COUNTED_CELLS if cell in missing_set]
    suspect_set = set(record.cells_suspect)
    suspect = [cell for cell in missing if cell in suspect_set]
    return replace(
        record,
        cells_filled=len(COUNTED_CELLS) - len(missing),
        cells_missing=missing,
        cells_suspect=suspect,
    )


# ══════════════════════════════════════════════════════════
# ② 보고서 품질 · ②-b AI 판정 검증 — 지표 6종
# ══════════════════════════════════════════════════════════


def _build_quality(records: list[RunRecord]) -> dict[str, float | None]:
    """지표 6종. 정본 계산식 그대로 «합을 나눈» 값이다(건별 비율의 평균이 아니다).

    잴 수 있는 4종:
      - 원문 일치율   = Σ검사 통과 문장 ÷ Σ생성 문장
      - 내용 고유성   = Σ충족 항목 ÷ Σ(충족 항목 + 미충족 항목)  ("작성 항목")
      - 수집 활용률   = Σ인용 조각 ÷ Σ수집 조각
      - AI 판정 정합률 = 사람이 [일치]를 누른 건 ÷ 사람이 검토한 건(human_check가 채워진 건)

    ★ 이 이력 13종만으로 «구조적으로» 못 재는 2종은 아래에서 항상 None이다.
      더 많은 이력을 쌓아도 못 잰다 — 다른 재료가 있어야 한다.
    """
    sentences_made = sum(r.sentences_made for r in records)
    sentences_passed = sum(r.sentences_passed for r in records)

    written_cells = sum(r.cells_filled + len(r.cells_missing) for r in records)
    filled_cells = sum(r.cells_filled for r in records)

    fragments_collected = sum(r.fragments_collected for r in records)
    fragments_cited = sum(r.fragments_cited for r in records)

    reviewed = [r for r in records if r.human_check]
    matched = sum(1 for r in reviewed if r.human_check == HUMAN_CHECK_MATCH)

    return {
        METRIC_FAITHFULNESS: _ratio_percent(sentences_passed, sentences_made),
        METRIC_ANSWER_RELEVANCY: _ratio_percent(filled_cells, written_cells),
        METRIC_CONTEXT_PRECISION: _ratio_percent(fragments_cited, fragments_collected),
        # 수집 완전성 — 분모(「존재 자료」)는 이 이력에 없다. 03_수집완전성측정.md
        # 「방법 3」의 고정 평가셋 20건(정답 데이터)이 있어야 절대 수치가 나온다.
        # 이력을 아무리 쌓아도 이 필드만으로는 계산식 자체가 성립하지 않는다.
        METRIC_CONTEXT_RECALL: None,
        METRIC_JUDGE_AGREEMENT: _ratio_percent(matched, len(reviewed)),
        # 판정 재현성 — "3회 다수결 «전»의 원본 판정을 비교"해야 하는데, 이력
        # 13종에는 다수결 «후» 값(cells_filled/cells_missing)만 남는다. 회차별
        # 원본 판정을 담을 필드가 스키마에 없어 이 이력만으로는 계산이 안 된다.
        METRIC_JUDGE_STABILITY: None,
    }


# ══════════════════════════════════════════════════════════
# ⑥ 개별 요청 조회
# ══════════════════════════════════════════════════════════


def _build_recent(records: list[RunRecord]) -> list[RunRecord]:
    """최신순으로 자른 목록. `at`이 ISO 8601이라 문자열 정렬 = 시간 정렬이다."""
    return sorted(records, key=lambda r: r.at, reverse=True)[:RECENT_LIMIT]


# ══════════════════════════════════════════════════════════
# 공용 도우미
# ══════════════════════════════════════════════════════════


def _at_date(record: RunRecord) -> dt.date:
    return dt.datetime.fromisoformat(record.at).date()


def _same_month(day: dt.date, today: dt.date) -> bool:
    return day.year == today.year and day.month == today.month


def _is_replay(record: RunRecord) -> bool:
    """저장된 기록을 되돌려 준 «데모» 실행인가 (문제로그 P-84).

    Args:
        record: 이력 한 줄.

    Returns:
        데모 실행이면 True — **AI를 안 불렀으므로 비용이 0이어야 한다.**

    ★ 모델 이름의 꼬리표로 가린다. 이력에 「데모였나」를 적는 칸이 따로 없고,
      **이미 쌓인 옛 기록에는 더더욱 없다.** 칸을 새로 만들면 옛 기록은 영영
      못 가리므로, 옛 기록에도 들어 있는 «모델 이름»을 쓴다.
    ⚠️ 지금 데모는 0원을 적으므로 이 함수는 **옛 기록을 위한 안전핀**이다.
      둘 다 있어야 한다 — 하나는 앞으로를, 하나는 이미 쌓인 것을 막는다.
    """
    return REPLAY_MODEL_MARK in (record.model or "")


def _percent(numerator: int, denominator: int) -> float:
    """분모가 0이면 0.0이 아니라 호출부가 이미 「잴 수 없음」을 판단한 뒤다."""
    return round(numerator / denominator * 100, PERCENT_DECIMALS)


def _ratio_percent(numerator: int, denominator: int) -> float | None:
    """분모가 0이면 못 재는 것이다 — 0%로 지어내지 않고 None을 돌려준다."""
    if denominator == 0:
        return None
    return _percent(numerator, denominator)
