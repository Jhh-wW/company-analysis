"""비상장 회사의 조사 이력이 «마감»까지 남는지 본다 (운영 결함 — 2026-09-05).

★ 무슨 일이 있었나
  판정 엔진은 회사 유형을 「비상장외감」(띄어쓰기 없음)으로 냈는데 이력 정본은
  「비상장 외감」(띄어쓰기 있음)이었다. 두 글자가 달라서 비상장 회사(현대카드·
  우리은행)의 이력 1행이 허용값 검사에 걸려 **전부 거부**됐다.
  `record_run`은 기록 실패를 삼키므로(보고서를 못 보게 만들면 안 되니까)
  사용자에게는 보고서가 정상으로 나갔고, 실행 상태만 「진행 중」으로 남아
  대시보드·하루 집계·게이트 진단이 통째로 빠졌다. 상장사는 글자가 같아 멀쩡했다.

★ 이 파일이 지키는 두 겹
  1) 엔진이 내놓는 글자가 이력 정본과 같다 (`analysis_engine` 판정 상수)
  2) 그 글자가 어긋나도 파이프라인 경계에서 잡는다 (`real._canonical_corp_type`)
  겹마다 따로 못 박는다 — 한 겹만 되돌려도 빨간불이 나와야 한다.
"""

from __future__ import annotations

import ast
import logging
import sqlite3
from pathlib import Path

import pytest

from src.core import paths
from src.features.observability import constants as obs
from src.features.observability import lifecycle
from src.features.pipeline import real
from src.features.pipeline.port import Outcome, RunResult, UserInput
from src.features.storage import constants as storage_constants
from src.web import recording


RUN_ID = "fedcba98765432100123456789abcdef"

#: 엔진이 예전에 내놓던 글자. 이 값이 이력에 그대로 실려서 결함이 났다.
BROKEN_CORP_TYPE = "비상장외감"
#: 이력 정본 글자. 손으로 적어 둔다 — 상수끼리 비교하면 둘이 같이 바뀌어도 초록불이다.
CANONICAL_CORP_TYPE = "비상장 외감"


def _engine_corp_type_constants() -> dict[str, str]:
    """판정이 «실제로» 내놓는 유형 글자를 엔진 파일에서 직접 읽는다.

    손으로 적으면 엔진이 바뀔 때 이 시험이 같이 안 바뀐다. 값을 import하지 않고
    소스를 파싱하는 이유는 옛 바이트코드(`__pycache__`)를 타지 않기 위해서다.
    """
    엔진_판정 = (
        paths.PROJECT_ROOT
        / "analysis_engine"
        / "src"
        / "features"
        / "judgment"
        / "logic.py"
    )
    상수: dict[str, str] = {}
    for node in ast.parse(엔진_판정.read_text(encoding="utf-8")).body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if isinstance(node.value, ast.Constant) and isinstance(
                node.value.value, str
            ):
                상수[node.target.id] = node.value.value
        elif isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            for target in node.targets:
                if isinstance(target, ast.Name) and isinstance(node.value.value, str):
                    상수[target.id] = node.value.value
    return 상수


def _result(corp_type: str) -> RunResult:
    """보고서가 나간 요청 하나 — 회사 유형만 다르게 준다."""
    return RunResult(outcome=Outcome.REPORT, corp_type=corp_type)


def _isolate(tmp_path, monkeypatch) -> Path:
    """진짜 이력·저장소를 건드리지 않게 둘 다 임시 폴더로 돌린다."""
    db_path = tmp_path / "storage.db"
    monkeypatch.setenv(storage_constants.ENV_DB_PATH, str(db_path))
    monkeypatch.setenv(obs.ENV_RECORDS_PATH, str(tmp_path / "runs.jsonl"))
    return db_path


def _entry(db_path: Path) -> lifecycle.LifecycleEntry | None:
    """이 요청의 현재 상태. 기록이 아예 안 됐으면 ``None``이다.

    기록이 실패하면 DB 파일조차 없을 수 있어서, 읽기 «전»에 표를 만들어 둔다.
    (`ensure_schema`는 이미 있으면 아무것도 안 한다.)
    """
    conn = sqlite3.connect(db_path)
    try:
        lifecycle.ensure_schema(conn)
        return lifecycle.get_entry(conn, RUN_ID)
    finally:
        conn.close()


# ══ 겹 1 — 엔진이 내놓는 글자 ══════════════════════════════════


def test_엔진_판정_유형이_이력_허용값_안에_있다() -> None:
    """엔진과 앱이 «같은 말»을 쓰는지 본다. 이번 결함이 정확히 여기서 났다."""
    상수 = _engine_corp_type_constants()

    assert "TYPE_AUDITED" in 상수 and "TYPE_LISTED" in 상수, f"엔진 상수를 못 읽었다: {상수}"
    assert 상수["TYPE_AUDITED"] == CANONICAL_CORP_TYPE
    assert 상수["TYPE_AUDITED"] in obs.CORP_TYPE_VALUES
    assert 상수["TYPE_LISTED"] in obs.CORP_TYPE_VALUES


# ══ 겹 2 — 파이프라인 경계의 정규화 ════════════════════════════


def test_띄어쓰기가_빠진_옛_표기를_경계에서_정본으로_바꾼다() -> None:
    """엔진 글자가 «또» 어긋나도 이력이 통째로 빠지지는 않게 하는 그물이다."""
    assert real._canonical_corp_type(BROKEN_CORP_TYPE) == CANONICAL_CORP_TYPE
    assert real._canonical_corp_type("  비상장  외감 ") == CANONICAL_CORP_TYPE
    assert real._canonical_corp_type("상장 사") == "상장사"


@pytest.mark.parametrize("그대로", ["상장사", CANONICAL_CORP_TYPE, ""])
def test_이미_정본인_값은_손대지_않는다(그대로: str) -> None:
    assert real._canonical_corp_type(그대로) == 그대로


def test_유형을_모르면_빈칸이_되고_None도_빈칸이다() -> None:
    """판정 전에 끝난 요청의 「모름」은 빈칸이 정본이다."""
    assert real._canonical_corp_type(None) == ""
    assert real._canonical_corp_type("   ") == ""


def test_판정이_낸_유형은_정규화를_거치지_않고는_안_쓰인다() -> None:
    """★ 「한 곳에서만 맞춘다」를 사람 기억이 아니라 시험이 지킨다.

    파이프라인은 이 값을 스무 곳 넘게 싣는다. 다음 사람이 한 곳을 더하면서
    판정 결과를 «그대로» 갖다 쓰면 그 경로만 조용히 예전으로 돌아간다.
    그래서 판정이 낸 유형을 읽는 자리는 정규화 함수의 인자 자리 «하나»뿐이어야 한다.
    """
    실제파일 = paths.APP_ROOT / "src" / "features" / "pipeline" / "real.py"
    나무 = ast.parse(실제파일.read_text(encoding="utf-8"))

    정규화_인자 = {
        id(인자)
        for 마디 in ast.walk(나무)
        if isinstance(마디, ast.Call)
        and isinstance(마디.func, ast.Name)
        and 마디.func.id == "_canonical_corp_type"
        for 인자 in 마디.args
    }
    판정에서_읽는_자리 = [
        마디
        for 마디 in ast.walk(나무)
        if isinstance(마디, ast.Attribute)
        and 마디.attr == "corp_type"
        and isinstance(마디.value, ast.Name)
        and 마디.value.id == "judgment"
    ]

    assert 판정에서_읽는_자리, "판정 결과를 읽는 자리를 못 찾았다 — 시험이 헛돌고 있다"
    벗어난_줄 = [마디.lineno for 마디 in 판정에서_읽는_자리 if id(마디) not in 정규화_인자]
    assert not 벗어난_줄, (
        f"real.py {벗어난_줄} 줄이 판정 결과를 정규화 없이 쓴다 — "
        "_canonical_corp_type 을 거쳐라"
    )


def test_모르는_유형은_빈칸으로_뭉개지_않는다() -> None:
    """빈칸은 「02_판정에 이르지 못했다」는 «다른 뜻»이다.

    판정까지 갔는데 모르는 값을 빈칸으로 적으면 이력이 거짓말을 한다.
    그대로 흘려보내 허용값 검사에서 소리 나게 둔다.
    """
    assert real._canonical_corp_type("듣도보도못한유형") == "듣도보도못한유형"


# ══ 끝에서 끝까지 — 이력이 «마감»으로 남는가 ═══════════════════


def test_비상장_판정_결과가_이력에_마감으로_남는다(
    tmp_path, monkeypatch, caplog
) -> None:
    """엔진이 내놓는 값을 파이프라인 경계 그대로 통과시켜 기록해 본다.

    시험에 글자를 박지 않고 «엔진이 실제로 내놓는 값»을 읽어 쓰는 것이 핵심이다.
    엔진 글자가 어긋나고 경계 정규화까지 못 잡으면 여기가 빨간불이 된다.
    """
    db_path = _isolate(tmp_path, monkeypatch)
    엔진_값 = _engine_corp_type_constants()["TYPE_AUDITED"]

    with caplog.at_level(logging.ERROR, logger="src.web.recording"):
        남았나 = recording.record_run(
            UserInput(company="저장하지 않을 회사", job="회사분석", region=""),
            _result(real._canonical_corp_type(엔진_값)),
            1.0,
            run_id=RUN_ID,
        )

    assert 남았나 is True
    assert not [기록 for 기록 in caplog.records if 기록.levelno >= logging.ERROR], (
        "이력 기록에서 예외가 났다: "
        f"{[기록.getMessage() for 기록 in caplog.records]}"
    )

    남은것 = _entry(db_path)
    assert 남은것 is not None and 남은것.state == lifecycle.STATE_FINAL
    assert 남은것.final_record is not None
    assert 남은것.final_record.corp_type == CANONICAL_CORP_TYPE


def test_옛_표기를_그대로_실으면_이력이_거부된다(tmp_path, monkeypatch) -> None:
    """★ 이 시험이 위 두 겹의 «이유»다 — 정규화를 빼면 무슨 일이 나는지 못 박는다.

    이력 허용값 검사는 지금도 「비상장외감」을 거부한다. `record_run`은 그 예외를
    삼키므로 **사용자 화면은 멀쩡하고** 실행 상태만 마감되지 않는다. 조용해서
    운영 로그를 뒤지기 전까지 아무도 몰랐던 것이 이번 결함의 정체다.
    """
    db_path = _isolate(tmp_path, monkeypatch)

    남았나 = recording.record_run(
        UserInput(company="저장하지 않을 회사", job="회사분석", region=""),
        _result(BROKEN_CORP_TYPE),
        1.0,
        run_id=RUN_ID,
    )

    남은것 = _entry(db_path)
    assert 남았나 is False
    assert 남은것 is None or 남은것.state != lifecycle.STATE_FINAL
