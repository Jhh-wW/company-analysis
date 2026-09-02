r"""저장된 실제 보고서를 시험 픽스처(JSON)로 굳히는 도구.

★ 시험은 이 파일을 부르지 않는다. 이름이 `test_`로 시작하지 않으므로 pytest가
  거두지도 않는다. DB가 손에 있을 때 «사람이» 한 번 돌려 픽스처를 다시 만드는
  용도다 — 시험이 매번 DB를 읽으면 DB가 사라진 곳(CI·다른 PC)에서 시험이
  통째로 죽기 때문이다.

★ 왜 재구성이 필요한가 — 저장본에는 `ComposedReport`가 아니라 «렌더된 글»이
  들어 있다. 문장 뒤 `[3][6]`은 조각 id가 아니라 «부록 표시 번호»이고,
  «해석» 문장은 ` — 해석` 표지를 달고 나온다. 그래서 아래 세 가지를 되돌린다:

  ① 표지 되돌리기 — 끝의 ` — 해석`을 떼면 등급이 «해석», 없으면 «확인».
  ② 번호 → 조각 id — payload의 `citations[*]`가 `number`와
     `source_id="v2-frag-<조각id>"`를 함께 들고 있어 1:1로 되돌릴 수 있다.
  ③ 미뤄 둔 번호 되살리기 — 기본 인용 표기(merged)는 「다음 «확인» 문장이 같은
     출처 묶음이면 번호를 그쪽으로 미룬다」이다(render.py `_marker_visibility`).
     그래서 번호가 «안 보이는» 확인 문장은 뒤에 오는 확인 문장과 출처가 같다.
     verify가 「인용 없는 확인은 해석으로 강등」을 이미 강제하므로(verify.py
     ④-a) «인용이 정말 없는 확인 문장»은 남아 있을 수 없다 — 되돌림이 성립한다.

★ 되돌리지 못하는 것 (시험 docstring에도 같은 내용을 적어 둔다):
  «해석» 문장의 인용은 화면에 아예 안 나오므로 되살릴 수 없다. 빈 튜플로 둔다.
  그래서 픽스처의 해석 문장은 실제보다 인용이 «적다» — 정리 단계 ①(근거 공유)이
  덜 발동하는 쪽이므로, 기준값은 «덜 지우는» 쪽으로 치우쳐 있다.

사용법 (저장소 루트에서):
    .venv\Scripts\python.exe app\src\features\composer\tests\stored_reports\build_from_storage.py
"""

from __future__ import annotations

import glob
import json
import os
import re
import sqlite3
import sys
import unicodedata
from typing import Any, Final

#: 저장본에서 골라 굳힐 보고서 — (파일이름, report_id). 고른 이유는 시험 파일에.
TARGETS: Final[tuple[tuple[str, str], ...]] = (
    ("jinyoung_aa81160a.json", "aa81160aeeaa97fbe2219d4fc8039133"),
    ("hive_40c8cc92.json", "40c8cc925f8f1a79713975090258561e"),
    ("hive_034898b2.json", "034898b2e6a0ff18c203079e79dfcf8b"),
    ("hive_8d6ae287.json", "8d6ae287fd9d4e12b58922d73cc01e63"),
    ("hive_1b7620d0.json", "1b7620d0665c45fcb74c644342e6835f"),
)

#: «해석» 문장 뒤에 붙는 표지. constants.INTERPRETATION_MARKER와 같은 값이지만
#: 이 도구는 앱을 import 하지 않고 혼자 돈다(DB만 읽는다).
_MARKER: Final[str] = " — 해석"
#: 문장 끝에 붙은 부록 표시 번호 덩어리 (`[3][6]`).
_TAIL_RE: Final[re.Pattern[str]] = re.compile(r"((?:\[\d+\])+)\s*$")
_NUMBER_RE: Final[re.Pattern[str]] = re.compile(r"\[(\d+)\]")
#: 부록 source_id 접두어 (render.V2_SOURCE_ID_PREFIX와 같은 값).
_SOURCE_PREFIX: Final[str] = "v2-frag-"


def _repo_root() -> str:
    return os.path.abspath(os.path.join(os.path.dirname(__file__), *[".."] * 6))


#: 실행 기록이 쌓이는 갈래들. `app/data/storage.db` 와 함께 «세 갈래»가 되고,
#: 순서가 «우선순위»다 — main()이 report_id마다 먼저 만난 것을 쓰고 뒤에 나온
#: 같은 id는 버린다.
#:
#: ⚠️ 이 목록은 `<갈래>/<실행폴더>/storage.db` 모양만 잡는다. `app/.local_demo/`
#:   처럼 «실행폴더 없이» 바로 밑에 둔 저장본 4개는 지금도 안 보인다
#:   (실측 — 넷 다 v2 보고서 0건이라 실해는 없다).
#:
#: ★ 왜 `.local_evaluation_runs`가 «맨 뒤»인가 (나중에 더함)
#:   앞에 끼우면 이미 굳어 있는 픽스처 5건의 출처가 바뀔 수 있다. 맨 뒤에
#:   붙이면 기존 우선순위가 그대로 보존된다.
#:   (실측: 그 5건은 evaluation_runs에 «없으므로» 지금은 순서와 무관하다.
#:    그래도 앞으로 대상이 늘 때를 위해 안전한 쪽으로 못 박는다.)
#: ★ 왜 이 갈래가 빠져 있었나 — 두 갈래만 훑는 바람에 evaluation_runs의
#:   v2 보고서 9건(제이와이피·삼성전자·현대자동차 포함)을 통째로 못 봤다.
#:   그 결과 「v2 모양 보고서의 회사는 둘뿐」이라는 틀린 주장이 시험
#:   머리말에 적혔다 → `test_stored_reports_regression.py` 머리말에
#:   정정을 남겼다.
_RUN_DIRS: Final[tuple[str, ...]] = (
    ".local_deployment_rehearsal_runs",
    ".local_evaluation_runs",
)


def _storage_paths(root: str) -> list[str]:
    paths = [os.path.join(root, "app", "data", "storage.db")]
    for run_dir in _RUN_DIRS:
        paths += sorted(
            glob.glob(os.path.join(root, "app", run_dir, "*", "storage.db"))
        )
    # ★ exists 가 아니라 isfile 이다 (적대 검수 D3)
    #   `storage.db` 라는 «폴더»가 있으면 exists 는 통과시키고, 그 뒤
    #   sqlite3 가 OperationalError 로 죽는다. 여기서 거르는 편이 낫다.
    return [path for path in paths if os.path.isfile(path)]


def _split_sentence(display: str) -> tuple[str, str, tuple[int, ...]]:
    """렌더된 한 줄을 (본문, 등급, 표시번호들)로 되돌린다."""
    text = display
    grade = "확인"
    if text.endswith(_MARKER):
        grade = "해석"
        text = text[: -len(_MARKER)]
    text = text.rstrip()
    numbers: tuple[int, ...] = ()
    match = _TAIL_RE.search(text)
    if match:
        numbers = tuple(int(one) for one in _NUMBER_RE.findall(match.group(1)))
        text = text[: match.start()].rstrip()
    return text, grade, numbers


def _rebuild(payload: dict[str, Any]) -> list[dict[str, Any]]:
    fragment_by_number: dict[int, str] = {}
    for citation in payload.get("citations") or ():
        source_id = str(citation.get("source_id") or "")
        if source_id.startswith(_SOURCE_PREFIX):
            fragment_by_number[int(citation["number"])] = source_id[len(_SOURCE_PREFIX) :]

    sections: list[dict[str, Any]] = []
    for section in payload.get("sections") or ():
        lines = [row[0] for row in (section.get("prose_lines") or ())]
        notice = str(section.get("empty_reason") or "")
        if notice and lines and lines[0] == notice:
            lines = lines[1:]  # 안내문은 문장이 아니다
        parsed = [_split_sentence(line) for line in lines]
        numbers = [item[2] for item in parsed]
        # ③ 미뤄 둔 번호 되살리기 — 뒤에서 앞으로 훑어야 연속으로 미룬 것도 잡힌다.
        for index in range(len(parsed) - 1, -1, -1):
            if parsed[index][1] == "확인" and not numbers[index]:
                for later in range(index + 1, len(parsed)):
                    if parsed[later][1] == "확인":
                        numbers[index] = numbers[later]
                        break
        sentences = [
            {
                "text": text,
                "citations": [
                    fragment_by_number[one] for one in shown if one in fragment_by_number
                ],
                "grade": grade,
            }
            for (text, grade, _), shown in zip(parsed, numbers)
        ]
        sections.append({"section_id": section.get("cell"), "sentences": sentences})
    return sections


_FORBIDDEN: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    ("전자우편", re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")),
    ("전화번호", re.compile(r"0\d{1,2}[-\s]?\d{3,4}[-\s]?\d{4}")),
    ("주소", re.compile(r"https?://")),
    ("비밀값", re.compile(r"(?i)api[_-]?key|secret|token|password|sk-")),
    ("주민등록번호", re.compile(r"\d{6}-\d{7}")),
)


def _reject_sensitive(sections: list[dict[str, Any]]) -> None:
    """개인정보·비밀값이 섞이면 파일을 만들지 않고 멈춘다."""
    for section in sections:
        for sentence in section["sentences"]:
            for name, pattern in _FORBIDDEN:
                if pattern.search(sentence["text"]):
                    raise SystemExit(f"{name}이(가) 섞여 픽스처를 만들지 않았다: {sentence['text'][:60]}")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    root = _repo_root()
    wanted = dict((report_id, name) for name, report_id in TARGETS)
    found: dict[str, tuple[str, dict[str, Any], str]] = {}
    for path in _storage_paths(root):
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        try:
            rows = connection.execute(
                "select report_id, payload_json, generated_at from reports"
            ).fetchall()
        finally:
            connection.close()
        for report_id, payload_json, generated_at in rows:
            if report_id in wanted and report_id not in found:
                found[report_id] = (
                    os.path.relpath(path, root).replace(os.sep, "/"),
                    json.loads(payload_json),
                    generated_at,
                )

    missing = sorted(set(wanted) - set(found))
    if missing:
        raise SystemExit(f"저장본을 못 찾았다: {missing}")

    out_dir = os.path.dirname(os.path.abspath(__file__))
    for report_id, (source_path, payload, generated_at) in found.items():
        sections = _rebuild(payload)
        _reject_sensitive(sections)
        document = {
            "출처": {
                "저장소": source_path,
                "report_id": report_id,
                "회사": payload.get("company", ""),
                "생성시각": generated_at,
            },
            "sections": sections,
        }
        name = wanted[report_id]
        with open(os.path.join(out_dir, name), "w", encoding="utf-8") as handle:
            json.dump(document, handle, ensure_ascii=False, indent=1)
            handle.write("\n")
        total = sum(len(one["sentences"]) for one in sections)
        print(f"{name}: 장 {len(sections)}개 · 문장 {total}개 ← {source_path}")


if __name__ == "__main__":
    main()
