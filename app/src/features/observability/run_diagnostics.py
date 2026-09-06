"""실행 진단 기록(steps)을 운영 로그 한 줄과 관리자 화면까지 나른다.

★ 왜 있나 — 파이프라인은 실행 내내 `steps`라는 단계 기록을 쌓지만, 그 목록은
  함수가 끝나는 순간 사라졌다. 운영에서 「뉴스를 몇 건 걸렀나」·「이름 후보가
  몇 개였나」를 물어볼 곳이 어디에도 없어 원인 추적이 두 번 막혔다.

★ 로그에는 「개수·코드·라벨」만 싣는다. 주소(URL)와 원문 발췌는 지운다.
  - 담을 단계는 `constants.RUN_SUMMARY_STEP_NAMES` 허용 목록으로만 고른다.
  - 문자열은 주소를 지우고 길이를 자른다.
  - 줄 전체 길이가 상한을 넘으면 뒤쪽 단계부터 덜어내고 「생략단계」로 몇 개를
    덜었는지 남긴다.

★ steps 원본은 로그가 아니라 SQLite(`run_steps_store`)에만 저장하고, 로그인한
  관리자 화면에서만 펼쳐 본다.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator, Mapping, Sequence

from src.features.observability.constants import (
    RUN_GRADE_FULL,
    RUN_GRADE_PARTIAL,
    RUN_GRADE_UNKNOWN,
    RUN_SUMMARY_COMPOSED_STEP,
    RUN_SUMMARY_LOG_PREFIX,
    RUN_SUMMARY_MAX_CHARS,
    RUN_SUMMARY_MAX_DEPTH,
    RUN_SUMMARY_MAX_ITEMS,
    RUN_SUMMARY_MAX_LABEL_CHARS,
    RUN_SUMMARY_PARTIAL_STEP,
    RUN_SUMMARY_REDACTED_MARK,
    RUN_SUMMARY_STEP_NAMES,
    RUN_SUMMARY_TRUNCATED_MARK,
)

logger = logging.getLogger(__name__)

#: 요약 사전의 고정 열쇠. 화면·시험이 이 이름으로 찾는다.
KEY_GRADE = "등급"
KEY_STEP_TOTAL = "전체단계"
KEY_STEPS = "단계"
KEY_OMITTED = "생략단계"
KEY_STEP_NAME = "step"

#: 단계 사전에서 이름 자리로 쓰는 열쇠 — 파이프라인이 정한 이름이다.
_STEP_KEY = "step"

#: 주소로 보이는 토막. `http://`·`https://`·`www.` 뿐 아니라 임의 스킴도 지운다.
#: 로그에 주소가 실리면 그 자체로 「어디를 긁었나」가 새 나간다.
_URL_PATTERN = re.compile(r"(?:[a-zA-Z][a-zA-Z0-9+.\-]*://|www\.)\S*")


def _safe_label(value: str) -> str:
    """문자열 하나를 「라벨」로 다듬는다. 주소를 지우고 길이를 자른다."""

    cleaned = _URL_PATTERN.sub(RUN_SUMMARY_REDACTED_MARK, str(value))
    cleaned = " ".join(cleaned.split())
    if len(cleaned) > RUN_SUMMARY_MAX_LABEL_CHARS:
        return cleaned[:RUN_SUMMARY_MAX_LABEL_CHARS] + RUN_SUMMARY_TRUNCATED_MARK
    return cleaned


def _compact(value: Any, *, depth: int = 0) -> Any:
    """값 하나를 요약에 실을 수 있는 안전한 모양으로 줄인다.

    숫자·참거짓은 그대로 두고, 문자열은 라벨로 다듬고, 사전·목록은 항목 수와
    깊이를 잘라 따라간다. 모르는 종류는 종류 이름만 남긴다 — 지어내지 않는다.
    """

    if value is None or isinstance(value, bool) or isinstance(value, int):
        return value
    if isinstance(value, float):
        return round(value, 3)
    if isinstance(value, str):
        return _safe_label(value)
    if depth >= RUN_SUMMARY_MAX_DEPTH:
        return f"<{type(value).__name__}>"
    if isinstance(value, Mapping):
        reduced: dict[str, Any] = {}
        for index, (raw_key, raw_value) in enumerate(value.items()):
            if index >= RUN_SUMMARY_MAX_ITEMS:
                reduced[RUN_SUMMARY_TRUNCATED_MARK] = len(value) - RUN_SUMMARY_MAX_ITEMS
                break
            reduced[_safe_label(str(raw_key))] = _compact(raw_value, depth=depth + 1)
        return reduced
    if isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
        reduced_items = [
            _compact(item, depth=depth + 1) for item in items[:RUN_SUMMARY_MAX_ITEMS]
        ]
        if len(items) > RUN_SUMMARY_MAX_ITEMS:
            reduced_items.append(RUN_SUMMARY_TRUNCATED_MARK)
        return reduced_items
    return f"<{type(value).__name__}>"


def _selected_steps(steps: Sequence[Any]) -> list[dict[str, Any]]:
    """허용 목록에 있는 단계만, 나온 순서대로 고른다."""

    allowed = set(RUN_SUMMARY_STEP_NAMES)
    picked: list[dict[str, Any]] = []
    for raw in steps:
        if not isinstance(raw, Mapping):
            continue
        name = raw.get(_STEP_KEY)
        if not isinstance(name, str) or name not in allowed:
            continue
        compacted: dict[str, Any] = {KEY_STEP_NAME: name}
        for key, value in raw.items():
            if key == _STEP_KEY:
                continue
            compacted[_safe_label(str(key))] = _compact(value)
        picked.append(compacted)
    return picked


def run_grade(steps: Sequence[Any]) -> str:
    """단계 기록만 보고 이번 실행의 최종 등급을 말한다.

    본문 작성을 끝낸 흔적이 없으면 「미상」이다 — 실패한 실행을 「정식」으로
    적으면 그 기록 자체가 거짓말이 된다.
    """

    names = {
        raw.get(_STEP_KEY)
        for raw in steps
        if isinstance(raw, Mapping)
    }
    if RUN_SUMMARY_PARTIAL_STEP in names:
        return RUN_GRADE_PARTIAL
    if RUN_SUMMARY_COMPOSED_STEP in names:
        return RUN_GRADE_FULL
    return RUN_GRADE_UNKNOWN


def build_summary(steps: Sequence[Any]) -> dict[str, Any]:
    """요약 사전을 만든다. 길이 상한은 여기서 보지 않는다."""

    picked = _selected_steps(steps)
    return {
        KEY_GRADE: run_grade(steps),
        KEY_STEP_TOTAL: sum(1 for raw in steps if isinstance(raw, Mapping)),
        KEY_STEPS: picked,
    }


def _dumps(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def summary_json(steps: Sequence[Any]) -> str:
    """요약을 길이 상한 안에 드는 JSON 한 줄로 만든다.

    상한을 넘으면 뒤쪽 단계부터 덜어내고 몇 개를 덜었는지 남긴다. 그래도 넘으면
    마지막 수단으로 글자를 자르고 잘림 표시를 붙인다(JSON이 아니게 되지만, 아무
    것도 남기지 않는 것보다 낫다).
    """

    summary = build_summary(steps)
    picked = list(summary[KEY_STEPS])
    omitted = 0
    while True:
        payload = dict(summary)
        payload[KEY_STEPS] = picked
        if omitted:
            payload[KEY_OMITTED] = omitted
        line = _dumps(payload)
        if len(line) <= RUN_SUMMARY_MAX_CHARS or not picked:
            break
        picked = picked[:-1]
        omitted += 1
    if len(line) > RUN_SUMMARY_MAX_CHARS:
        return line[: RUN_SUMMARY_MAX_CHARS - len(RUN_SUMMARY_TRUNCATED_MARK)] + (
            RUN_SUMMARY_TRUNCATED_MARK
        )
    return line


@dataclass
class StepsCapture:
    """한 실행의 단계 기록을 담아 두는 칸.

    파이프라인은 이 칸의 존재를 모른다. 실행을 띄운 쪽(웹 Job)이 열어 두면
    `finish_run`이 채우고, 실행이 끝난 뒤 그 쪽이 꺼내 저장한다.
    """

    steps: list[dict[str, Any]] = field(default_factory=list)
    filled: bool = False


_CAPTURE: contextvars.ContextVar[StepsCapture | None] = contextvars.ContextVar(
    "observability_steps_capture", default=None
)

#: 지금 돌고 있는 실행이 단계를 쌓는 목록. 파이프라인 본체가 이걸 받아 쓴다.
#: ★ 인자로 넘기지 않는 이유 — 본체 함수의 서명을 바꾸면 그 함수를 대신 끼워
#:   넣는 기존 시험들이 한꺼번에 깨진다. 여기서는 같은 스레드 안의 「지금 실행」만
#:   가리키면 충분하다.
_STEPS: contextvars.ContextVar[list[dict[str, Any]] | None] = contextvars.ContextVar(
    "observability_run_steps", default=None
)


@dataclass
class RunCollector:
    """실행 하나가 쌓는 단계 기록과 그 자리 표식."""

    steps: list[dict[str, Any]]
    _token: contextvars.Token
    finished: bool = False

    def finish(self, *, corp_code: str = "") -> str:
        """자리를 닫고 요약 로그 한 줄을 남긴다. 두 번 불러도 한 번만 남는다."""

        if self.finished:
            return ""
        self.finished = True
        try:
            _STEPS.reset(self._token)
        except ValueError:  # pragma: no cover — 다른 문맥에서 닫으려 한 경우
            logger.warning("실행 진단 자리를 연 문맥이 아닙니다")
        return finish_run(self.steps, corp_code=corp_code)


def begin_run() -> RunCollector:
    """이 실행이 단계를 쌓을 자리를 연다. 끝나면 `finish`를 부른다."""

    steps: list[dict[str, Any]] = []
    return RunCollector(steps=steps, _token=_STEPS.set(steps))


def current_steps() -> list[dict[str, Any]]:
    """지금 실행이 쓸 단계 목록. 자리가 안 열려 있으면 새 목록을 준다.

    ★ 새 목록을 주는 쪽이 안전하다 — 자리가 없다고 파이프라인이 멈추면,
      진단이 본 기능을 망가뜨리는 셈이 된다.
    """

    steps = _STEPS.get()
    return [] if steps is None else steps


@contextlib.contextmanager
def capture() -> Iterator[StepsCapture]:
    """이 블록 안에서 끝난 실행의 단계 기록을 받아 둔다.

    ★ 파이프라인을 부르는 «같은 스레드»에서 열어야 한다. ContextVar는 스레드마다
      따로이므로, 다른 스레드에서 열면 채워지지 않고 조용히 빈 칸이 된다.
    """

    sink = StepsCapture()
    token = _CAPTURE.set(sink)
    try:
        yield sink
    finally:
        _CAPTURE.reset(token)


def finish_run(steps: Iterable[Any], *, corp_code: str = "") -> str:
    """실행이 끝났을 때 요약 로그 한 줄을 남기고 단계 기록을 넘겨준다.

    Args:
        steps: 파이프라인이 쌓은 단계 기록.
        corp_code: DART 고유번호. 회사 이름은 싣지 않는다.

    Returns:
        실제로 남긴 요약 JSON 한 줄. 실패하면 빈 문자열.

    ★ 여기서 예외를 밖으로 내보내지 않는다. 진단 기록 때문에 사용자의 보고서가
      막히면 안 된다 — 진단은 부차적이다.
    """

    try:
        items = [item for item in steps if isinstance(item, Mapping)]
        line = summary_json(items)
        logger.info(
            "%s corp_code=%s steps=%s",
            RUN_SUMMARY_LOG_PREFIX,
            _safe_label(corp_code),
            line,
        )
        sink = _CAPTURE.get()
        if sink is not None:
            sink.steps = [dict(item) for item in items]
            sink.filled = True
        return line
    except Exception:  # noqa: BLE001 — 진단 실패가 실행을 막으면 안 된다
        logger.exception("실행 진단 요약을 남기지 못했습니다")
        return ""
