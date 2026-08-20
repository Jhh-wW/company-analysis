"""이력 1행(`RunRecord`) 저장·읽기 시험.

★ 저장은 덧붙이기만 한다는 전제로 시험을 짠다 — 「고쳐 쓰기」 경로는 아예 없다.
"""

from __future__ import annotations

from dataclasses import fields
from pathlib import Path

import pytest

from src.features.observability.constants import (
    CACHE_HIT_NONE,
    COUNTED_CELLS,
    CORP_TYPE_LISTED,
    END_STEP_COMPLETE,
    GRADE_COMPLETE,
    TOTAL_CELLS,
)
from src.features.observability.records import (
    ReadResult,
    RunRecord,
    append_record,
    new_run_id,
    now_iso,
    read_records,
)

# ══════════════════════════════════════════════════════════
# 시험용 이력 1행 — 기본값은 「정상적으로 완주한 건」 하나
# ══════════════════════════════════════════════════════════


def _record(
    *,
    run_id: str = "r1",
    at: str = "2026-08-15T10:00:00",
    corp_type: str = CORP_TYPE_LISTED,
    job: str = "영업",
    end_step: str = END_STEP_COMPLETE,
    cache_hit: str = CACHE_HIT_NONE,
    fragments_collected: int = 24,
    fragments_cited: int = 16,
    sentences_made: int = 27,
    sentences_passed: int = 22,
    cells_filled: int = 9,
    cells_missing: list[str] | None = None,
    cells_suspect: list[str] | None = None,
    grade: str = GRADE_COMPLETE,
    human_check: str = "",
    cost_krw: float = 112.0,
    elapsed_sec: float = 131.0,
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
# 저장 · 읽기 왕복
# ══════════════════════════════════════════════════════════


def test_저장하고_읽으면_값이_그대로_돌아온다(tmp_path: Path):
    path = tmp_path / "runs.jsonl"
    record = _record()
    append_record(record, path)

    result = read_records(path)

    assert result.skipped == 0
    assert result.records == [record]


def test_여러_행을_덧붙이면_순서대로_쌓인다(tmp_path: Path):
    path = tmp_path / "runs.jsonl"
    first = _record(run_id="r1")
    second = _record(run_id="r2")
    append_record(first, path)
    append_record(second, path)

    result = read_records(path)

    assert [r.run_id for r in result.records] == ["r1", "r2"]


def test_저장_경로의_폴더가_없으면_만든다(tmp_path: Path):
    path = tmp_path / "새폴더" / "runs.jsonl"
    append_record(_record(), path)

    assert path.exists()


def test_없는_파일을_읽으면_빈_결과를_돌려준다(tmp_path: Path):
    result = read_records(tmp_path / "없음.jsonl")

    assert result == ReadResult(records=[], skipped=0)


# ══════════════════════════════════════════════════════════
# 깨진 줄 방어 — 죽지 않는다
# ══════════════════════════════════════════════════════════


def test_깨진_줄은_건너뛰고_개수를_알려준다(tmp_path: Path):
    path = tmp_path / "runs.jsonl"
    append_record(_record(run_id="ok1"), path)
    with path.open("a", encoding="utf-8") as f:
        f.write("이건 JSON이 아니다\n")
    append_record(_record(run_id="ok2"), path)

    result = read_records(path)

    assert [r.run_id for r in result.records] == ["ok1", "ok2"]
    assert result.skipped == 1


def test_빈_줄은_건너뛴_것으로_세지_않는다(tmp_path: Path):
    path = tmp_path / "runs.jsonl"
    append_record(_record(), path)
    with path.open("a", encoding="utf-8") as f:
        f.write("\n\n")  # 트레일링 개행류

    result = read_records(path)

    assert result.skipped == 0
    assert len(result.records) == 1


def test_필드가_다른_옛_스키마_줄은_건너뛴다(tmp_path: Path):
    path = tmp_path / "runs.jsonl"
    with path.open("a", encoding="utf-8") as f:
        f.write('{"run_id": "옛날", "at": "2026-01-01T00:00:00"}\n')  # 필드 대부분 없음
    append_record(_record(run_id="ok"), path)

    result = read_records(path)

    assert [r.run_id for r in result.records] == ["ok"]
    assert result.skipped == 1


def test_허용값을_벗어난_줄도_건너뛴다(tmp_path: Path):
    """파일에 직접 손으로 써넣은 경우처럼, 검증을 우회한 값이 섞여도 죽지 않는다."""
    path = tmp_path / "runs.jsonl"
    broken = _record(run_id="망가짐")
    import dataclasses
    import json

    bad = dataclasses.asdict(broken)
    bad["corp_type"] = "이상한값"
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(bad, ensure_ascii=False) + "\n")
    append_record(_record(run_id="ok"), path)

    result = read_records(path)

    assert [r.run_id for r in result.records] == ["ok"]
    assert result.skipped == 1


def test_옛_7칸_이력은_파일을_고치지_않고_6칸으로_재해석한다(tmp_path: Path):
    """장기 보관한 원본은 남기되 숨긴9번이 대시보드에 되살아나지 않아야 한다."""
    import dataclasses
    import json

    path = tmp_path / "runs.jsonl"
    legacy = dataclasses.asdict(_record())
    legacy["cells_filled"] = 7
    legacy["cells_missing"] = []
    path.write_text(json.dumps(legacy, ensure_ascii=False) + "\n", encoding="utf-8")
    raw_before = path.read_text(encoding="utf-8")

    result = read_records(path)

    assert result.skipped == 0
    assert result.records[0].cells_filled == 6
    assert result.records[0].cell_total == 6
    assert result.records[0].cells_missing == []
    assert path.read_text(encoding="utf-8") == raw_before


def test_기존_6칸_JSONL의_미충족_ID와_분모를_그대로_보존한다(tmp_path: Path):
    """과거 숫자 칸을 근거 없이 canonical 의미 ID로 승격시키지 않는다."""
    import dataclasses
    import json

    path = tmp_path / "runs.jsonl"
    legacy = dataclasses.asdict(_record())
    legacy["cells_filled"] = 4
    legacy["cells_missing"] = ["4-1", "4-3"]
    legacy["cells_suspect"] = ["4-3"]
    path.write_text(json.dumps(legacy, ensure_ascii=False) + "\n", encoding="utf-8")

    result = read_records(path)

    assert result.skipped == 0
    assert result.records[0].cells_filled == 4
    assert result.records[0].cell_total == 6
    assert result.records[0].cells_missing == ["4-1", "4-3"]
    assert result.records[0].cells_suspect == ["4-3"]


def test_신규_관측_칸은_canonical_1_9장_의미_ID와_순서가_같다():
    assert COUNTED_CELLS == (
        "identity",
        "business_model",
        "portfolio",
        "past_changes",
        "current_challenges",
        "future_strategy",
        "operations_partners",
        "culture",
        "competitive_position",
    )
    assert TOTAL_CELLS == 9
    assert _record().cell_total == 9


# ══════════════════════════════════════════════════════════
# 값 검증 — 저장 «전»에 막는다
# ══════════════════════════════════════════════════════════


def test_run_id가_비면_에러():
    with pytest.raises(ValueError, match="run_id"):
        _record(run_id="")


def test_at이_ISO8601이_아니면_에러():
    with pytest.raises(ValueError, match="at"):
        _record(at="2026년 8월 15일")


def test_corp_type이_허용값이_아니면_에러():
    with pytest.raises(ValueError, match="corp_type"):
        _record(corp_type="유한회사")


def test_end_step이_허용값이_아니면_에러():
    with pytest.raises(ValueError, match="end_step"):
        _record(end_step="03_수집")  # 03 수집은 이탈 지점이 아니다 — end_step이 될 수 없다


def test_cache_hit이_허용값이_아니면_에러():
    with pytest.raises(ValueError, match="cache_hit"):
        _record(cache_hit="3층")


def test_grade가_허용값이_아니면_에러():
    with pytest.raises(ValueError, match="grade"):
        _record(grade="우수")


def test_human_check이_허용값이_아니면_에러():
    with pytest.raises(ValueError, match="human_check"):
        _record(human_check="애매함")


def test_인용_조각이_수집_조각보다_많으면_에러():
    with pytest.raises(ValueError, match="인용 조각"):
        _record(fragments_collected=5, fragments_cited=6)


def test_검사_통과_문장이_생성_문장보다_많으면_에러():
    with pytest.raises(ValueError, match="검사 통과 문장"):
        _record(sentences_made=5, sentences_passed=6)


def test_누락_의심이_미충족의_부분집합이_아니면_에러():
    with pytest.raises(ValueError, match="누락 의심"):
        _record(
            cells_missing=["current_challenges"],
            cells_suspect=["current_challenges", "future_strategy"],
        )


def test_모르는_칸_ID는_저장_전에_막는다():
    with pytest.raises(ValueError, match="모르는 칸 ID"):
        _record(cells_missing=["made_up_section"])


def test_음수_비용은_에러():
    with pytest.raises(ValueError, match="cost_krw"):
        _record(cost_krw=-1)


def test_음수_소요시간은_에러():
    with pytest.raises(ValueError, match="elapsed_sec"):
        _record(elapsed_sec=-1)


def test_충족_항목_수가_전체_칸_수를_넘으면_에러():
    with pytest.raises(ValueError, match="cells_filled"):
        _record(cells_filled=10)  # canonical 필수 장은 9개다


# ══════════════════════════════════════════════════════════
# 개인정보 방어선 — 필드 자체에 사람을 알아볼 수 있는 항목이 없다
# ══════════════════════════════════════════════════════════

#: 이력 1행에 있으면 안 되는 개인정보·원문류 필드 이름의 조각.
#: 정본 §「이력에 담지 않는 것」— 이메일·이름·회사명·공고 원문·프롬프트·보고서 본문.
_금지_필드_조각 = (
    "email", "name", "company", "phone", "address",
    "posting", "prompt", "text", "본문", "원문",
)


def test_이력_1행에_개인정보성_필드가_없다():
    field_names = [f.name for f in fields(RunRecord)]

    for name in field_names:
        for banned in _금지_필드_조각:
            assert banned not in name.lower(), f"{name} 필드가 금지어 「{banned}」를 포함합니다"


def test_이력_1행은_정확히_13종_항목에_대응하는_18개_필드다():
    """13종을 화면 계약 그대로 세분화한 결과다 — 늘거나 줄면 화면과 어긋난다."""
    expected = {
        "run_id", "at", "corp_type", "job", "end_step", "cache_hit",
        "fragments_collected", "fragments_cited", "sentences_made", "sentences_passed",
        "cells_filled", "cells_missing", "cells_suspect", "grade", "human_check",
        "cost_krw", "elapsed_sec", "model",
    }
    actual = {f.name for f in fields(RunRecord)}
    assert actual == expected


# ══════════════════════════════════════════════════════════
# 보조 함수
# ══════════════════════════════════════════════════════════


def test_new_run_id는_매번_다르다():
    assert new_run_id() != new_run_id()


def test_now_iso는_ISO8601_형식이다():
    import datetime as dt

    parsed = dt.datetime.fromisoformat(now_iso())  # 예외 없이 파싱되면 통과
    assert parsed.utcoffset() == dt.timedelta(hours=9)
