"""이력 → 대시보드 집계(`build_dashboard`) 시험.

★ Dashboard는 「더하고 나누기만」 하는 값이다 — 여기서 검증하는 것도 그 산수뿐이다.
"""

from __future__ import annotations

import datetime as dt

from src.features.observability.constants import (
    CACHE_HIT_L1,
    CORP_TYPE_LISTED,
    CORP_TYPE_UNKNOWN,
    CORP_TYPE_UNLISTED_AUDITED,
    END_STEP_COMPLETE,
    END_STEP_GATE,
    END_STEP_GENERATE,
    END_STEP_IDENTIFY,
    END_STEP_IDENTIFY_ERROR,
    END_STEP_IMAGE_ERROR,
    END_STEP_OUTPUT,
    GRADE_COMPLETE,
    GRADE_INCOMPLETE,
    GRADE_NONE,
    GRADE_PARTIAL,
    METRIC_ANSWER_RELEVANCY,
    METRIC_CONTEXT_PRECISION,
    METRIC_CONTEXT_RECALL,
    METRIC_FAITHFULNESS,
    METRIC_JUDGE_AGREEMENT,
    METRIC_JUDGE_STABILITY,
    RECENT_LIMIT,
)
from src.features.observability.metrics import build_dashboard
from src.features.observability.records import RunRecord

오늘 = dt.date(2026, 8, 15)


def _record(
    *,
    run_id: str = "r1",
    at: str = "2026-08-15T10:00:00",
    corp_type: str = CORP_TYPE_LISTED,
    job: str = "영업",
    end_step: str = END_STEP_COMPLETE,
    cache_hit: str = CACHE_HIT_L1,
    fragments_collected: int = 0,
    fragments_cited: int = 0,
    sentences_made: int = 0,
    sentences_passed: int = 0,
    cells_filled: int = 0,
    cells_missing: list[str] | None = None,
    cells_suspect: list[str] | None = None,
    grade: str = GRADE_NONE,
    human_check: str = "",
    cost_krw: float = 0.0,
    elapsed_sec: float = 0.0,
    model: str = "claude-opus-5",
) -> RunRecord:
    return RunRecord(
        run_id=run_id,
        at=at,
        corp_type=corp_type,
        job=job,
        end_step=end_step,
        cache_hit=cache_hit,
        fragments_collected=fragments_collected,
        fragments_cited=fragments_cited,
        sentences_made=sentences_made,
        sentences_passed=sentences_passed,
        cells_filled=cells_filled,
        cells_missing=cells_missing if cells_missing is not None else [],
        cells_suspect=cells_suspect if cells_suspect is not None else [],
        grade=grade,
        human_check=human_check,
        cost_krw=cost_krw,
        elapsed_sec=elapsed_sec,
        model=model,
    )


# ══════════════════════════════════════════════════════════
# 빈 이력
# ══════════════════════════════════════════════════════════


def test_빈_이력이면_건수와_비용이_0이고_지표는_전부_못잰다():
    dashboard = build_dashboard([], today=오늘, model="claude-opus-5")

    assert dashboard.total == 0
    assert dashboard.today == 0
    assert dashboard.errors == 0
    assert dashboard.cost_month_krw == 0
    assert dashboard.cell_fill_rate == []
    assert all(v is None for v in dashboard.quality.values())
    assert dashboard.recent == []
    assert dashboard.grades == {
        GRADE_COMPLETE: 0, GRADE_PARTIAL: 0, GRADE_INCOMPLETE: 0,
    }


# ══════════════════════════════════════════════════════════
# ① 서비스 상태
# ══════════════════════════════════════════════════════════


def test_전체_건수와_오늘_건수를_센다():
    records = [
        _record(run_id="a", at="2026-08-15T09:00:00"),
        _record(run_id="b", at="2026-08-14T09:00:00"),  # 어제
        _record(run_id="c", at="2026-08-15T23:59:59"),
    ]

    dashboard = build_dashboard(records, today=오늘, model="claude-opus-5")

    assert dashboard.total == 3
    assert dashboard.today == 2


def test_당월_비용만_합산한다():
    records = [
        _record(run_id="a", at="2026-08-01T00:00:00", cost_krw=100),
        _record(run_id="b", at="2026-08-31T00:00:00", cost_krw=50),
        _record(run_id="c", at="2026-07-31T23:59:59", cost_krw=9999),  # 전월 — 빠져야 한다
    ]

    dashboard = build_dashboard(records, today=오늘, model="claude-opus-5")

    assert dashboard.cost_month_krw == 150


def test_비용원장이_연결되면_관측행_비용을_합치지_않는다():
    records = [_record(run_id="a", cost_krw=9999)]

    dashboard = build_dashboard(
        records,
        today=오늘,
        model="m",
        cost_month_krw_override=321.5,
        unresolved_cost_runs=2,
        cost_ledger_since="2026-08-17",
    )

    assert dashboard.cost_month_krw == 321.5
    assert dashboard.cost_from_ledger is True
    assert dashboard.unresolved_cost_runs == 2
    assert dashboard.cost_ledger_since == "2026-08-17"


def test_오류_건수는_식별오류_이미지오류_생성실패_출력실패를_센다():
    records = [
        _record(run_id="z", end_step=END_STEP_IDENTIFY_ERROR),
        _record(run_id="y", end_step=END_STEP_IMAGE_ERROR),
        _record(run_id="a", end_step=END_STEP_GENERATE),   # 오류 — 0건이어야 하는 신호
        _record(run_id="b", end_step=END_STEP_OUTPUT),      # 오류
        _record(run_id="c", end_step=END_STEP_GATE),        # 정상 범위 — 오류 아님
        _record(run_id="d", end_step=END_STEP_IDENTIFY),    # 정상 범위 — 오류 아님
        _record(run_id="e", end_step=END_STEP_COMPLETE),
    ]

    dashboard = build_dashboard(records, today=오늘, model="claude-opus-5")

    assert dashboard.errors == 4


def test_지금_쓰는_모델은_인자_그대로_반영된다():
    dashboard = build_dashboard([], today=오늘, model="claude-fable-5")
    assert dashboard.model == "claude-fable-5"


# ══════════════════════════════════════════════════════════
# ③ 단계별 이탈
# ══════════════════════════════════════════════════════════


def test_단계별_이탈_퍼널이_순서대로_감소한다():
    records = (
        [_record(run_id=f"id{i}", end_step=END_STEP_IDENTIFY) for i in range(3)]
        + [_record(run_id=f"gate{i}", end_step=END_STEP_GATE) for i in range(2)]
        + [_record(run_id=f"done{i}", end_step=END_STEP_COMPLETE) for i in range(5)]
    )

    dashboard = build_dashboard(records, today=오늘, model="m")

    labels_reached = {label: reached for label, reached, _left in dashboard.funnel}
    labels_left = {label: left for label, _reached, left in dashboard.funnel}

    assert labels_reached["회사 식별 실패"] == 10  # 전체
    assert labels_left["회사 식별 실패"] == 3
    assert labels_reached["대상 제외"] == 7  # 식별 실패 3건 빠진 나머지
    assert labels_left["자료 부족 중단"] == 2
    assert labels_reached["보고서 제공"] == 5
    assert labels_left["보고서 제공"] == 5


def test_퍼널_이탈_합은_전체_건수와_같다():
    records = [
        _record(run_id="a", end_step=END_STEP_IDENTIFY),
        _record(run_id="b", end_step=END_STEP_GATE),
        _record(run_id="c", end_step=END_STEP_COMPLETE),
        _record(run_id="d", end_step=END_STEP_COMPLETE),
    ]

    dashboard = build_dashboard(records, today=오늘, model="m")

    assert sum(left for _label, _reached, left in dashboard.funnel) == len(records)


# ══════════════════════════════════════════════════════════
# ⑤ 완성도 분포 · 칸별 채움률
# ══════════════════════════════════════════════════════════


def test_등급_분포를_센다():
    records = [
        _record(run_id="a", end_step=END_STEP_COMPLETE, grade=GRADE_COMPLETE),
        _record(run_id="b", end_step=END_STEP_COMPLETE, grade=GRADE_COMPLETE),
        _record(run_id="c", end_step=END_STEP_COMPLETE, grade=GRADE_PARTIAL),
        _record(run_id="d", end_step=END_STEP_GATE, grade=GRADE_NONE),  # 등급 없음 — 세지 않는다
    ]

    dashboard = build_dashboard(records, today=오늘, model="m")

    assert dashboard.grades == {
        GRADE_COMPLETE: 2, GRADE_PARTIAL: 1, GRADE_INCOMPLETE: 0,
    }


def test_칸별_채움률은_완주_건만_본다():
    records = [
        _record(
            run_id="a",
            end_step=END_STEP_COMPLETE,
            cells_missing=["4-1", "4-3"],
        ),
        _record(
            run_id="b",
            end_step=END_STEP_COMPLETE,
            cells_missing=[],
        ),
        # 완주하지 못한 건 — cells_missing이 기본값(빈 리스트)이라도 채움률에 안 들어가야 한다
        _record(run_id="c", end_step=END_STEP_GATE, cells_missing=[]),
    ]

    dashboard = build_dashboard(records, today=오늘, model="m")
    rates = dict(dashboard.cell_fill_rate)

    assert rates["4-1"] == 50.0  # 완주 2건 중 1건만 채움
    assert rates["1"] == 100.0   # 두 완주 건 모두 미충족 목록에 없음
    assert "9" not in rates


def test_완주_건이_없으면_칸별_채움률은_빈_목록이다():
    records = [_record(run_id="a", end_step=END_STEP_GATE)]

    dashboard = build_dashboard(records, today=오늘, model="m")

    assert dashboard.cell_fill_rate == []


# ══════════════════════════════════════════════════════════
# ② 보고서 품질 — 잴 수 있는 지표
# ══════════════════════════════════════════════════════════


def test_원문_일치율은_통과_문장_합_나누기_생성_문장_합이다():
    records = [
        _record(run_id="a", sentences_made=10, sentences_passed=8),
        _record(run_id="b", sentences_made=10, sentences_passed=6),
    ]

    dashboard = build_dashboard(records, today=오늘, model="m")

    assert dashboard.quality[METRIC_FAITHFULNESS] == 70.0  # (8+6)/(10+10)


def test_생성_문장이_전혀_없으면_원문_일치율은_None이다():
    records = [_record(run_id="a", sentences_made=0, sentences_passed=0)]

    dashboard = build_dashboard(records, today=오늘, model="m")

    assert dashboard.quality[METRIC_FAITHFULNESS] is None


def test_수집_활용률은_인용_조각_합_나누기_수집_조각_합이다():
    records = [
        _record(run_id="a", fragments_collected=20, fragments_cited=10),
        _record(run_id="b", fragments_collected=10, fragments_cited=5),
    ]

    dashboard = build_dashboard(records, today=오늘, model="m")

    assert dashboard.quality[METRIC_CONTEXT_PRECISION] == 50.0  # (10+5)/(20+10)


def test_내용_고유성은_충족_항목_합_나누기_작성_항목_합이다():
    records = [
        # 옛 채움 수는 신뢰하지 않고 현재 6칸의 미충족 목록으로 재계산한다.
        _record(
            run_id="a", cells_filled=6,
            cells_missing=["4-1", "4-3", "9"], grade=GRADE_COMPLETE,
        ),  # 현재 규칙 4/6
        _record(
            run_id="b", cells_filled=1, cells_missing=[], grade=GRADE_COMPLETE,
        ),  # 현재 규칙 6/6
    ]

    dashboard = build_dashboard(records, today=오늘, model="m")

    assert dashboard.quality[METRIC_ANSWER_RELEVANCY] == round(10 / 12 * 100, 1)
    assert [record.cells_filled for record in dashboard.recent] == [4, 6]
    assert all("9" not in record.cells_missing for record in dashboard.recent)


def test_AI_판정_정합률은_사람이_검토한_건만_분모로_쓴다():
    records = [
        _record(run_id="a", human_check="일치"),
        _record(run_id="b", human_check="불일치"),
        _record(run_id="c", human_check=""),  # 아직 사람이 안 봄 — 분모에서 뺀다
    ]

    dashboard = build_dashboard(records, today=오늘, model="m")

    assert dashboard.quality[METRIC_JUDGE_AGREEMENT] == 50.0  # 1/2


def test_아무도_검토하지_않았으면_AI_판정_정합률은_None이다():
    records = [_record(run_id="a", human_check="")]

    dashboard = build_dashboard(records, today=오늘, model="m")

    assert dashboard.quality[METRIC_JUDGE_AGREEMENT] is None


# ══════════════════════════════════════════════════════════
# ② 보고서 품질 — 구조적으로 못 재는 지표 (이력만으로는 불가능)
# ══════════════════════════════════════════════════════════


def test_수집_완전성은_이력이_아무리_쌓여도_항상_None이다():
    records = [
        _record(
            run_id=f"r{i}",
            fragments_collected=10,
            fragments_cited=5,
            sentences_made=5,
            sentences_passed=5,
        )
        for i in range(50)
    ]

    dashboard = build_dashboard(records, today=오늘, model="m")

    assert dashboard.quality[METRIC_CONTEXT_RECALL] is None


def test_판정_재현성은_이력이_아무리_쌓여도_항상_None이다():
    records = [_record(run_id=f"r{i}") for i in range(50)]

    dashboard = build_dashboard(records, today=오늘, model="m")

    assert dashboard.quality[METRIC_JUDGE_STABILITY] is None


# ══════════════════════════════════════════════════════════
# ⑥ 개별 요청 조회
# ══════════════════════════════════════════════════════════


def test_최근_목록은_최신순으로_상한만큼만_자른다():
    시작 = dt.datetime(2026, 8, 1, 0, 0, 0)
    records = [
        _record(run_id=f"r{i}", at=(시작 + dt.timedelta(minutes=i)).isoformat())
        for i in range(RECENT_LIMIT + 5)
    ]

    dashboard = build_dashboard(records, today=오늘, model="m")

    assert len(dashboard.recent) == RECENT_LIMIT
    assert dashboard.recent[0].run_id == f"r{RECENT_LIMIT + 4}"  # 가장 최근(날짜 가장 큰 것)이 맨 앞


# ══════════════════════════════════════════════════════════
# 회사 유형이 아직 없는 초기 이탈 건도 정상 집계된다
# ══════════════════════════════════════════════════════════


def test_회사_유형을_모르는_초기_이탈_건도_전체_건수에는_들어간다():
    records = [
        _record(run_id="a", end_step=END_STEP_IDENTIFY, corp_type=CORP_TYPE_UNKNOWN),
        _record(run_id="b", end_step=END_STEP_COMPLETE, corp_type=CORP_TYPE_LISTED),
        _record(
            run_id="c",
            end_step=END_STEP_COMPLETE,
            corp_type=CORP_TYPE_UNLISTED_AUDITED,
        ),
    ]

    dashboard = build_dashboard(records, today=오늘, model="m")

    assert dashboard.total == 3


# ── ★ 사후 정정 — 같은 요청이 두 번 세어지면 안 된다 ──

def test_같은_요청이_여러_줄이면_마지막_것만_센다():
    """사람이 나중에 [일치]/[불일치]를 기록하면 같은 run_id로 한 줄이 더 쌓인다.

    그대로 합치면 건수·비용·지표가 전부 두 배로 부풀어 오른다.
    """
    import datetime as dt

    from src.features.observability.metrics import build_dashboard
    from src.features.observability.records import RunRecord

    def row(human: str, cost: float) -> RunRecord:
        return RunRecord(
            run_id="같은요청",
            at="2026-08-15T10:00:00",
            corp_type="상장사",
            job="마케팅",
            end_step="완주",
            cache_hit="없음",
            fragments_collected=10,
            fragments_cited=5,
            sentences_made=10,
            sentences_passed=8,
            cells_filled=4,
            cells_missing=["2", "9"],
            cells_suspect=[],
            grade="완성",
            human_check=human,
            cost_krw=cost,
            elapsed_sec=30.0,
            model="claude-haiku-4-5",
        )

    board = build_dashboard(
        [row("", 40.0), row("일치", 40.0)],
        today=dt.date(2026, 8, 15),
        model="claude-haiku-4-5",
    )
    assert board.total == 1, "같은 요청이 두 번 세어졌습니다"
    assert board.cost_month_krw == 40.0, "비용이 두 번 더해졌습니다"
    assert board.quality["AI 판정 정합률"] == 100.0
