"""이력 1행(`RunRecord`) — 저장(덧붙이기)·읽기.

★ 이력에는 사람을 알아볼 수 있는 것을 담지 않는다 (정본 §「이력에 담지 않는 것」).
  이메일·이름·회사명·공고 원문은 필드 자체에 없다. `job`(직무명)만 예외로 허용된다.

★ 저장소는 JSONL(줄마다 JSON 객체 하나) 파일이다. DB는 아직 없다 — 정본은
  "보고서 캐시와 같은 DB, 다른 표"라고 적어 두었지만, 그 DB가 아직 없으므로
  이 단계에서는 파일로 흉내 낸다. 나중에 DB로 옮겨도 `append_record`/`read_records`의
  타입 서명만 유지하면 부르는 쪽 코드는 안 바뀐다.

★ 덧붙이기만 한다. 기존 줄을 고치거나 지우지 않는다 — 이력은 「일어난 일의 기록」이라
  나중에 값을 바꾸면 그 자체가 거짓말이 된다.
"""

from __future__ import annotations

import datetime as dt
import json
import threading
import uuid
from dataclasses import asdict, dataclass, fields
from pathlib import Path
from typing import Any

from src.core import clock
from src.features.observability.constants import (
    CACHE_HIT_VALUES,
    COUNTED_CELLS,
    CORP_TYPE_VALUES,
    END_STEP_VALUES,
    GRADE_VALUES,
    HUMAN_CHECK_VALUES,
    LEGACY_COUNTED_CELLS,
    LEGACY_HIDDEN_CELLS,
    LEGACY_TOTAL_CELLS,
    MAX_RECORD_LINE_BYTES,
    MAX_RECORDS_FILE_BYTES,
    TOTAL_CELLS,
)


_APPEND_LOCK = threading.Lock()


class RecordsCapacityError(RuntimeError):
    """옛 JSONL 호환 사본이 운영 디스크 예약 상한에 도달했다."""


@dataclass(frozen=True)
class RunRecord:
    """이력 1행 — 정해진 「13종」.

    ★ 이 모양은 대시보드 화면 쪽과 합의된 고정값이다 — 필드를 늘리거나 줄이면
      화면 코드가 같이 깨진다. 바꿔야 한다면 화면 담당과 먼저 맞춘다.
    """

    run_id: str
    at: str                      # ISO 8601 (예: "2026-08-15T10:22:31")
    corp_type: str               # "상장사" | "비상장 외감" | "" (02_판정 전 종료)
    job: str
    #: 정상 종료·앞단 이탈·기술 오류를 constants.END_STEP_VALUES 중 하나로 기록한다.
    end_step: str
    cache_hit: str               # "1층" | "2층" | "없음"
    fragments_collected: int     # 수집 조각 수
    fragments_cited: int         # 인용 조각 수
    sentences_made: int          # 생성 문장 수
    sentences_passed: int        # 검사 통과 문장 수
    cells_filled: int            # 충족 항목 수
    cells_missing: list[str]     # 미충족 canonical 장 ID
    cells_suspect: list[str]     # 누락 의심 항목
    grade: str                   # "완성" | "부분 완성" | "미완성" | ""
    human_check: str             # "일치" | "불일치" | "" (사람 검토, 없으면 빈칸)
    cost_krw: float
    elapsed_sec: float
    model: str                   # AI 모델 버전

    def __post_init__(self) -> None:
        """구조적으로 말이 안 되는 값은 저장 «전»에 걸러낸다.

        ★ 여기서 막지 않으면 깨진 값이 파일에 그대로 쌓이고, JSONL은 나중에
          고칠 수 없다(덧붙이기 전용)는 원칙 때문에 영영 못 고친다.
        """
        _require(self.run_id.strip() != "", "run_id가 비어 있습니다")
        _require_iso8601(self.at)
        _require_in(self.corp_type, CORP_TYPE_VALUES, "corp_type")
        _require_in(self.end_step, END_STEP_VALUES, "end_step")
        _require_in(self.cache_hit, CACHE_HIT_VALUES, "cache_hit")
        _require_in(self.grade, GRADE_VALUES, "grade")
        _require_in(self.human_check, HUMAN_CHECK_VALUES, "human_check")

        count_fields = (
            "fragments_collected", "fragments_cited",
            "sentences_made", "sentences_passed",
        )
        for name in count_fields:
            _require(getattr(self, name) >= 0, f"{name}은(는) 음수일 수 없습니다")
        _require(
            self.fragments_cited <= self.fragments_collected,
            "인용 조각 수가 수집 조각 수보다 많습니다",
        )
        _require(
            self.sentences_passed <= self.sentences_made,
            "검사 통과 문장 수가 생성 문장 수보다 많습니다",
        )
        _require(
            0 <= self.cells_filled <= TOTAL_CELLS,
            f"cells_filled는 0~{TOTAL_CELLS} 사이여야 합니다",
        )
        cell_ids = [*self.cells_missing, *self.cells_suspect]
        _require(
            all(isinstance(cell, str) for cell in cell_ids),
            "칸 ID는 문자열이어야 합니다",
        )
        known_cell_ids = (
            set(COUNTED_CELLS)
            | set(LEGACY_COUNTED_CELLS)
            | set(LEGACY_HIDDEN_CELLS)
        )
        _require(
            set(cell_ids) <= known_cell_ids,
            "미충족·누락 의심에 모르는 칸 ID가 있습니다",
        )
        _require(
            not (
                set(cell_ids) & set(COUNTED_CELLS)
                and set(cell_ids)
                & (set(LEGACY_COUNTED_CELLS) | set(LEGACY_HIDDEN_CELLS))
            ),
            "canonical 장 ID와 구형 칸 ID를 한 행에 섞을 수 없습니다",
        )
        _require(
            set(self.cells_suspect) <= set(self.cells_missing),
            "누락 의심 항목은 미충족 항목의 부분집합이어야 합니다",
        )
        _require(self.cost_krw >= 0, "cost_krw는 음수일 수 없습니다")
        _require(self.elapsed_sec >= 0, "elapsed_sec는 음수일 수 없습니다")

    @property
    def cell_total(self) -> int:
        """개별 이력이 작성된 당시의 채움 분모.

        스키마 필드를 늘려 기존 JSONL을 깨뜨리지 않고, 구형 칸 ID와
        완성 수로 과거 6칸 기록을 식별한다. 신규 공개본은 게이트 상 항상
        canonical 9장을 모두 채운다.
        """
        return (
            LEGACY_TOTAL_CELLS
            if uses_legacy_cell_contract(self)
            else TOTAL_CELLS
        )


#: `RunRecord`가 실제로 갖는 필드 이름 집합 — JSONL에서 읽은 줄이 이 모양과
#: 정확히 같은지 확인할 때 쓴다(스키마 드리프트 검출).
_RECORD_FIELD_NAMES = frozenset(f.name for f in fields(RunRecord))


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(f"이력 1행 값이 규칙을 어겼습니다: {message}")


def _require_in(value: str, allowed: tuple[str, ...], field_name: str) -> None:
    if value not in allowed:
        raise ValueError(
            f"이력 1행 값이 규칙을 어겼습니다: {field_name}={value!r}는 "
            f"허용된 값 {allowed} 안에 있어야 합니다"
        )


def _require_iso8601(value: str) -> None:
    try:
        dt.datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(
            f"이력 1행 값이 규칙을 어겼습니다: at={value!r}는 ISO 8601 형식이 아닙니다"
        ) from exc


def new_run_id() -> str:
    """익명 요청 ID를 새로 만든다.

    ★ 사람을 알아볼 수 있는 값을 절대 섞지 않는다 — 순수 난수다.
    """
    return uuid.uuid4().hex


def now_iso() -> str:
    """지금 시각을 KST offset 포함 ISO 8601 초 단위로 돌려준다."""
    return clock.iso_now_kst()


def append_record(record: RunRecord, path: Path) -> None:
    """이력 1행을 파일 끝에 덧붙인다.

    ★ 덧붙이기만 한다. 기존 줄은 절대 열어서 고치지 않는다.

    Args:
        record: 저장할 이력 1행. 만들 때 이미 `__post_init__` 검증을 통과했다.
        path: 저장할 JSONL 파일 경로. 부모 폴더가 없으면 만든다.
    """
    line = (json.dumps(asdict(record), ensure_ascii=False) + "\n").encode("utf-8")
    if len(line) > MAX_RECORD_LINE_BYTES:
        raise RecordsCapacityError("관측 JSONL 한 행이 안전한 길이 상한을 넘었습니다")
    path.parent.mkdir(parents=True, exist_ok=True)
    # 배포 계약은 process/worker 1개지만 같은 process의 background task와 요청
    # thread는 동시에 마감될 수 있다. 크기 확인과 append를 한 경계로 묶어 상한을
    # 경합으로 넘지 않는다. SQLite 정본은 이 호출 전에 이미 commit된다.
    with _APPEND_LOCK:
        try:
            current_size = path.stat().st_size
        except FileNotFoundError:
            current_size = 0
        if current_size + len(line) > MAX_RECORDS_FILE_BYTES:
            raise RecordsCapacityError(
                "관측 JSONL 호환 사본이 디스크 예약 상한에 도달했습니다"
            )
        with path.open("ab") as file_pointer:
            file_pointer.write(line)


@dataclass(frozen=True)
class ReadResult:
    """이력 파일을 읽은 결과.

    ★ 깨진 줄이 있어도 전체를 죽이지 않는다 — 건너뛰고 몇 줄 건너뛰었는지만 알린다
      (성공 기준 M1 「이력 누락」을 사람이 알아채려면 건너뛴 줄 수가 보여야 한다).
    """

    records: list[RunRecord]
    skipped: int


def read_records(path: Path) -> ReadResult:
    """JSONL 이력 파일을 읽어 `RunRecord` 목록으로 돌려준다.

    Args:
        path: 읽을 JSONL 파일 경로.

    Returns:
        정상적으로 읽힌 행 목록 + 건너뛴(깨진) 줄 수. 파일이 아예 없으면
        빈 목록 + 0을 돌려준다(에러가 아니다 — 아직 요청이 한 건도 없었을 수 있다).
    """
    if not path.exists():
        return ReadResult(records=[], skipped=0)

    try:
        file_size = path.stat().st_size
    except OSError as exc:
        raise RecordsCapacityError("관측 JSONL 크기를 확인할 수 없습니다") from exc
    if file_size > MAX_RECORDS_FILE_BYTES:
        raise RecordsCapacityError(
            "관측 JSONL 호환 사본이 읽기 안전 상한을 넘었습니다"
        )

    records: list[RunRecord] = []
    skipped = 0
    # ``read_text().splitlines()``는 파일 전체 문자열과 줄 목록을 동시에 메모리에
    # 올린다. 호환 파일은 한 줄씩 제한 길이로 읽어 OOM 없이 SQLite 전환할 수 있게 한다.
    with path.open("rb") as file_pointer:
        while True:
            raw_line = file_pointer.readline(MAX_RECORD_LINE_BYTES + 1)
            if not raw_line:
                break
            if len(raw_line) > MAX_RECORD_LINE_BYTES:
                # 한 논리 행을 한 번만 손상으로 세고 다음 개행까지 bounded chunk로 버린다.
                while raw_line and not raw_line.endswith(b"\n"):
                    raw_line = file_pointer.readline(MAX_RECORD_LINE_BYTES + 1)
                skipped += 1
                continue
            try:
                line = raw_line.decode("utf-8").strip()
            except UnicodeDecodeError:
                skipped += 1
                continue
            if not line:
                continue  # 빈 줄(마지막 개행 등)은 손상이 아니다 — 세지 않는다
            record = _parse_line(line)
            if record is None:
                skipped += 1
                continue
            records.append(record)
    return ReadResult(records=records, skipped=skipped)


def _parse_line(line: str) -> RunRecord | None:
    """줄 하나를 `RunRecord`로 바꾼다. 모양이 안 맞으면 None(호출부가 건너뛴다)."""
    try:
        data: Any = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    if set(data.keys()) != _RECORD_FIELD_NAMES:
        return None  # 필드가 늘거나 준 옛 스키마 — 지어내 채우지 않고 건너뛴다
    data = normalize_persisted_cells(data)
    try:
        return RunRecord(**data)
    except (TypeError, ValueError):
        return None


def uses_legacy_cell_contract(record: RunRecord) -> bool:
    """구형 6칸 이력인지 스키마 필드 추가 없이 판별한다.

    구형과 canonical ID 집합은 겹치지 않는다. ID 목록이 빈 완성
    기록은 과거 분모의 최대값(6)으로 구분한다. 보고서 없이 멈춘
    기록(`grade == ""`)은 canonical 운영 흐름으로 다룬다.
    """
    if not record.grade:
        return False
    ids = set(record.cells_missing) | set(record.cells_suspect)
    if ids & (set(LEGACY_COUNTED_CELLS) | set(LEGACY_HIDDEN_CELLS)):
        return True
    if ids & set(COUNTED_CELLS):
        return False
    return record.cells_filled <= LEGACY_TOTAL_CELLS


def normalize_persisted_cells(data: dict[str, Any]) -> dict[str, Any]:
    """옛 6·7칸 이력을 원본 수정 없이 구형 6칸으로 읽는다.

    이력 JSONL은 덮어쓰지 않는다. 과거 `cells_filled`는 5·6·7·8번까지
    센 경우가 있고, `cells_missing`에는 숨긴 9번이 남아 있었다.
    파일을 읽을 때만 옛 여섯 칸의 미충족 목록으로 채움 수를
    다시 산출한다. canonical 9장으로 승격시키지 않으므로 과거 기록이
    새 장을 검증했다고 거짓 표시되지 않는다.
    """
    if not data.get("grade"):
        return data
    filled = data.get("cells_filled")
    missing = data.get("cells_missing")
    suspect = data.get("cells_suspect")
    canonical_cells = set(COUNTED_CELLS)
    legacy_counted = set(LEGACY_COUNTED_CELLS)
    legacy_cells = legacy_counted | set(LEGACY_HIDDEN_CELLS)
    if (
        not isinstance(filled, int)
        or isinstance(filled, bool)
        or not 0 <= filled <= len(legacy_cells)
        or not isinstance(missing, list)
        or not isinstance(suspect, list)
        or not all(isinstance(cell, str) and cell in legacy_cells for cell in missing)
        or not all(isinstance(cell, str) and cell in legacy_cells for cell in suspect)
    ):
        return data

    observed_ids = set(missing) | set(suspect)
    if observed_ids & canonical_cells:
        return data

    missing_set = set(missing)
    current_missing = [cell for cell in LEGACY_COUNTED_CELLS if cell in missing_set]
    suspect_set = set(suspect)
    current_suspect = [cell for cell in current_missing if cell in suspect_set]
    normalized = dict(data)
    normalized["cells_filled"] = LEGACY_TOTAL_CELLS - len(current_missing)
    normalized["cells_missing"] = current_missing
    normalized["cells_suspect"] = current_suspect
    return normalized
