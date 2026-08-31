"""요청이 끝날 때 이력 «1행»을 남긴다 (파이프라인 14번 · 기획서 08 관측).

★ 화면과 알맹이 사이에 두는 얇은 이음새다.
  알맹이(`RunResult`)는 「이번에 뭘 했나」만 알고, 이력의 «모양»은 08 관측이 정한다.
  둘을 직접 붙이면 한쪽을 고칠 때마다 다른 쪽이 깨진다.

★ 사람을 알아볼 수 있는 것은 담지 않는다 — 회사명·공고 원문·이메일 금지
  (정본 08_관측/1_흐름/01_지표수집.md §「이력에 담지 «않는» 것」).
  직무명(`job`)은 통계용이라 담는다.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from src.core import paths
from src.features.budget import state_machine as budget_state_machine
from src.features.final_gate_diagnostic import store as final_gate_diagnostic_store
from src.features.observability import constants as obs
from src.features.observability import lifecycle
from src.features.observability.records import (
    RunRecord,
    append_record,
    new_run_id,
    now_iso,
)
from src.features.pipeline.port import Outcome, RunResult, UserInput
from src.features.report_standard.constants import CANONICAL_SCHEMA_VERSION
from src.features.spanselect import diagnostic_store as span_diagnostic_store
from src.features.storage import db as storage_db

logger = logging.getLogger(__name__)

#: 어떻게 끝났는지 → 「어느 단계에서 멈췄나」.
#: 정본의 종료 단계 값과 맞춘다. 화면의 Outcome과 관측의 단계는 «다른 말»이라 여기서 옮긴다.
_END_STEP: dict[Outcome, str] = {
    Outcome.REPORT: obs.END_STEP_COMPLETE,
    Outcome.NOT_FOUND: obs.END_STEP_IDENTIFY,
    Outcome.REJECT_PUBLIC: obs.END_STEP_JUDGE,
    Outcome.REJECT_NO_DISCLOSURE: obs.END_STEP_JUDGE,
    Outcome.POSTING_DISCARDED: obs.END_STEP_POSTING,
    Outcome.GATE_STOPPED: obs.END_STEP_GATE,
    Outcome.FAILED: obs.END_STEP_GENERATE,
}


def records_path() -> Path:
    """이력 파일 위치. 없으면 만들어진다.

    Returns:
        이력 파일(`runs.jsonl`)의 절대 경로.

    ★ 환경변수 `OBSERVABILITY_RECORDS_PATH`가 있으면 그쪽을 쓴다 (문제로그 P-85).
      **시험이 진짜 이력을 더럽히지 않게 하려는 것**이다 — 이게 없던 동안
      시험을 돌릴 때마다 기록이 쌓여, 관리 화면이 «사용자가 한 적 없는 조사»를
      세고 있었다. 저장소(`STORAGE_DB_PATH`)는 이미 같은 방식으로 격리돼 있었다.
    ⚠️ 매번 환경변수를 읽는다. 한 번 읽어 상수로 두면 시험이 못 바꾼다.
    """
    override = os.environ.get(obs.ENV_RECORDS_PATH, "").strip()
    if override:
        return Path(override)
    return paths.APP_ROOT / obs.DEFAULT_RECORDS_RELATIVE_PATH


def record_run(
    user_input: UserInput,
    result: RunResult,
    elapsed_sec: float,
    *,
    run_id: str = "",
    end_step: str = "",
    expected_state: str | None = None,
) -> bool:
    """요청 하나를 이력 1행으로 남긴다.

    ★ 여기서 절대 예외를 밖으로 던지지 않는다. 이력 기록이 실패했다고
      **사용자의 보고서를 못 보게 만들면 안 된다** — 기록은 부차적이다.
    """
    try:
        gate_diagnostic_allowed = result.outcome is Outcome.GATE_STOPPED or (
            result.outcome is Outcome.FAILED and result.billing_uncertain is True
        )
        if result.final_gate_reason and not gate_diagnostic_allowed:
            raise ValueError(
                "최종 게이트 사유는 게이트 중단 또는 비용 미확정 실패에만 기록할 수 있습니다"
            )
        report = result.report
        cells_filled, cells_missing = _observed_cells(report)
        record = RunRecord(
            # 회사 식별·OCR·본조사를 한 요청으로 이어 적을 때는 같은 번호를 쓴다.
            # 비우면 예전 호출부와 똑같이 여기서 난수를 만든다.
            run_id=run_id or new_run_id(),
            at=now_iso(),
            corp_type=result.corp_type or obs.CORP_TYPE_UNKNOWN,
            job=safe_observation_job(user_input.job),
            end_step=end_step or _END_STEP.get(result.outcome, obs.END_STEP_GENERATE),
            # 파이프라인이 실은 값을 그대로 쓴다. 비어 있으면 「없음」.
            # ★ 예전에는 여기가 「없음」 고정이라 캐시를 붙여도 대시보드가
            #   영영 0건이었다. 기능이 붙으면 이 줄도 같이 살아야 한다 (P-63).
            cache_hit=result.cache_hit or obs.CACHE_HIT_NONE,
            fragments_collected=result.fragments_collected,
            fragments_cited=result.fragments_cited,
            sentences_made=result.sentences_made,
            sentences_passed=result.sentences_passed,
            cells_filled=cells_filled,
            cells_missing=cells_missing,
            # 「누락 의심」 자동 탐지는 아직 미구현 — 빈 목록이 정직한 값이다.
            cells_suspect=[],
            grade=report.grade.value if report is not None else "",
            # 사람 검토는 «나중에» 붙는다. 끝난 시점엔 항상 빈칸이 맞다.
            human_check="",
            cost_krw=result.cost_krw,
            elapsed_sec=round(elapsed_sec, 1),
            model=result.model,
        )
        # SQLite current 행이 요청당 최종값 하나를 보장하고 별도 audit 표가 상태
        # 전이를 변경 불가로 남긴다. JSONL은 새 비용 원장 전환 전 도구만 위한 호환 사본이다.
        with storage_db.connect() as conn:
            # lifecycle.finalize_once()는 독립 호출도 지원하려고 savepoint를 쓴다.
            # 여기서는 부속 span 진단과 crash-gap 없이 함께 남겨야 하므로 명시적
            # 바깥 transaction을 먼저 연다.
            if not conn.in_transaction:
                conn.execute("BEGIN IMMEDIATE")
            lifecycle.ensure_schema(conn)
            write_legacy_jsonl = not budget_state_machine.cutover_applied(conn)
            inserted = lifecycle.finalize_once(
                conn,
                record,
                expected_state=expected_state,
            )
            if result.span_selection_diagnostics:
                # inserted=False인 멱등 재호출에도 같은 값만 허용한다. 이는 진단
                # 표 도입 전 final 행을 안전하게 backfill하는 유일한 경로다.
                span_diagnostic_store.record_once(
                    conn,
                    run_id=record.run_id,
                    result_reason=result.span_selection_result_reason,
                    rounds=result.span_selection_diagnostics,
                    recorded_at=record.at,
                )
            if result.final_gate_reason:
                final_gate_diagnostic_store.record_once(
                    conn,
                    run_id=record.run_id,
                    reason_code=result.final_gate_reason,
                    recorded_at=record.at,
                )
        if inserted and write_legacy_jsonl:
            try:
                append_record(record, records_path())
            except Exception:  # noqa: BLE001 — SQLite 최종값은 이미 남았다
                logger.exception("JSONL 이력 사본 기록 실패 (SQLite 최종값은 정상)")
        return inserted
    except Exception:  # noqa: BLE001 — 기록 실패가 사용자를 막으면 안 된다
        logger.exception("이력 기록 실패 (보고서는 정상)")
        return False


def _observed_cells(report: object | None) -> tuple[int, list[str]]:
    """신규 canonical 9장과 구형 6칸을 섞지 않고 관측한다.

    공개 canonical 보고서는 의미 ID 9개만 기록한다. 하위 호환용
    비-canonical `Report`를 직접 넘긴 오래된 호출부는 과거 숫자 칸으로
    남겨, 기존 JSONL과 같은 계약으로 읽힌다.
    """
    if report is None:
        return 0, []

    cells = getattr(report, "cells", {})
    if not isinstance(cells, dict):
        cells = {}
    if getattr(report, "schema_version", "") == CANONICAL_SCHEMA_VERSION:
        section_values = {
            str(getattr(section, "cell", "")): bool(
                getattr(section, "is_filled", False)
            )
            for section in getattr(report, "sections", [])
        }
        values = section_values or cells
        missing = [cell for cell in obs.COUNTED_CELLS if not values.get(cell, False)]
        return len(obs.COUNTED_CELLS) - len(missing), missing

    missing = [
        cell for cell in obs.LEGACY_COUNTED_CELLS if not cells.get(cell, False)
    ]
    return len(obs.LEGACY_COUNTED_CELLS) - len(missing), missing


def safe_observation_job(job: str) -> str:
    """옛 ``job`` 관측 열에 회사분석 유형 또는 안전한 옛 직무값을 남긴다."""
    if not (job or "").strip():
        return "회사분석"
    try:
        return lifecycle.safe_job(job)
    except lifecycle.LifecycleError:
        logger.warning("관측 직무값이 저장 경계를 벗어나 비식별값으로 바꿨습니다")
        return "비공개"


def record_end(
    *,
    run_id: str,
    job: str,
    end_step: str,
    cost_krw: float,
    elapsed_sec: float,
    model: str,
    expected_state: str | None = None,
) -> bool:
    """보고서 전 종료를 기존 이력 모양의 최종 관측값으로 남긴다."""
    return record_run(
        UserInput(company="", job=job, region=""),
        RunResult(
            outcome=Outcome.FAILED,
            cost_krw=cost_krw,
            model=model,
        ),
        elapsed_sec,
        run_id=run_id,
        end_step=end_step,
        expected_state=expected_state,
    )
