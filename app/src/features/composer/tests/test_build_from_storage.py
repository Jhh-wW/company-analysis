# -*- coding: utf-8 -*-
"""픽스처 «만드는 도구»가 저장본 «세 갈래»를 빠짐없이 훑는지 지키는 시험.

★ 「세 갈래」라고 범위를 적은 이유 — 도구는 `<갈래>/<실행폴더>/storage.db` 모양만
  잡는다. `app/.local_demo/storage.db` 처럼 실행폴더 없이 바로 둔 것 4개는 지금도
  안 보인다(넷 다 v2 보고서 0건이라 실해는 없다). 「빠짐없이」라고만 적으면
  제품 주석과 이 제목이 서로 다른 말을 하게 된다.

★ 왜 이 파일이 생겼나
  ─────────────────────────────────────────────────────────
  `stored_reports/build_from_storage.py` 의 `_storage_paths()` 가 저장본을
  **두 갈래만** 훑고 `.local_evaluation_runs` 를 빠뜨리고 있었다. 그래서
  그 갈래의 v2 보고서 **9건**(제이와이피·삼성전자·현대자동차 포함)이
  도구에 아예 안 보였고, 「v2 모양 보고서의 회사는 하이브·진영 둘뿐」이라는
  **틀린 주장**이 `test_stored_reports_regression.py` 머리말에 적혔다.

  빠뜨린 것 자체보다 나쁜 것은 **그것을 지켜 주는 시험이 0건**이었다는 점이다.
  도구는 이름이 `test_` 로 시작하지 않아 pytest가 거두지 않고, 사람이 손으로만
  돌린다. 갈래를 하나 지워도 아무 데도 빨간불이 안 뜬다. 이 파일이 그 구멍을 막는다.

★ 왜 «진짜» 폴더를 안 보나
  ─────────────────────────────────────────────────────────
  `app/.local_*` 는 이 PC에만 있고 git이 추적하지 않는다 — CI에는 아예 없다.
  진짜 폴더를 세는 시험을 쓰면 CI에서는 셀 것이 0개라 **아무것도 안 지키면서
  초록불만 뜬다**. 그래서 임시 폴더에 가짜 저장본을 만들어 잰다.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest
from typing import Any

#: 도구는 패키지가 아니라 «혼자 도는 스크립트»다(`__init__.py` 없음).
#: 그래서 일반 import 가 아니라 파일 경로로 불러온다.
_TOOL_PATH = Path(__file__).with_name("stored_reports") / "build_from_storage.py"


def _load_tool() -> Any:
    spec = importlib.util.spec_from_file_location("build_from_storage_under_test", _TOOL_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _만든다(root: Path, 상대경로: str) -> str:
    """가짜 저장본 파일 하나를 만들고 절대경로를 돌려준다."""
    path = root / 상대경로
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    return str(path)


def test_저장본_세_갈래를_모두_훑는다(tmp_path: Path) -> None:
    """세 갈래 중 하나라도 빠지면 빨간불.

    ★ `.local_evaluation_runs` 가 빠져 있던 것이 이 시험이 생긴 이유다.
    """
    운영 = _만든다(tmp_path, "app/data/storage.db")
    리허설 = _만든다(tmp_path, "app/.local_deployment_rehearsal_runs/20260825_aaa/storage.db")
    평가 = _만든다(tmp_path, "app/.local_evaluation_runs/20260824_bbb/storage.db")

    찾은것 = _load_tool()._storage_paths(str(tmp_path))

    assert 운영 in 찾은것, "운영 저장소(app/data/storage.db)를 안 본다"
    assert 리허설 in 찾은것, "배포 리허설 실행 기록을 안 본다"
    assert 평가 in 찾은것, ".local_evaluation_runs 를 안 본다 — 이 갈래가 빠지면 "
    assert len(찾은것) == 3, f"세 갈래만 나와야 하는데 {len(찾은것)}개가 나왔다: {찾은것}"


def test_평가실행_기록은_우선순위_맨_뒤다(tmp_path: Path) -> None:
    """순서가 «우선순위»다 — `main()` 은 report_id마다 «먼저 만난» 저장본을 쓴다.

    ★ 평가 실행 기록을 앞에 끼우면 이미 굳어 있는 픽스처의 출처가 바뀔 수 있다.
      그래서 맨 뒤로 못 박는다. 순서를 바꾸면 이 시험이 빨간불이 된다.
    """
    운영 = _만든다(tmp_path, "app/data/storage.db")
    리허설 = _만든다(tmp_path, "app/.local_deployment_rehearsal_runs/20260825_aaa/storage.db")
    평가 = _만든다(tmp_path, "app/.local_evaluation_runs/20260824_bbb/storage.db")

    찾은것 = _load_tool()._storage_paths(str(tmp_path))

    assert 찾은것.index(운영) < 찾은것.index(리허설) < 찾은것.index(평가), (
        f"우선순위가 «운영 → 리허설 → 평가» 가 아니다: {찾은것}"
    )


def test_한_갈래_안에서는_이름순이다(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """같은 갈래 안 여러 실행 폴더는 «이름순»이라야 결과가 매번 같다.

    ★ glob 은 순서를 보장하지 않는다. 정렬을 빼면 PC마다 다른 저장본이 뽑혀
      픽스처가 «돌릴 때마다» 달라진다. 실제로 정렬이 일을 한다 — 같은 보고서가
      리허설 갈래 5곳에 중복 저장돼 있어, 정렬이 어느 사본을 뽑을지 정한다.

    ★ 왜 glob 을 «역순으로» 갈아끼우나 (적대 검수 D2)
      이 PC(Windows·NTFS)에서는 glob 이 «이미» 이름순으로 돌려준다. 그래서
      `sorted()` 를 통째로 지워도 이 시험이 초록불이었다 — 제목과 다른 것을
      재고 있었다. 정렬이 실제로 일하는지 재려면 «흐트러진 입력»을 줘야 한다.
    """
    _만든다(tmp_path, "app/data/storage.db")
    늦은것 = _만든다(tmp_path, "app/.local_evaluation_runs/20260826_zzz/storage.db")
    이른것 = _만든다(tmp_path, "app/.local_evaluation_runs/20260818_aaa/storage.db")

    tool = _load_tool()
    진짜_glob = tool.glob.glob

    class _역순으로_돌려주는_glob:
        """도구가 부르는 `glob.glob` 만 갈아끼운다 (표준 모듈은 안 건드린다)."""

        @staticmethod
        def glob(pattern: str) -> list[str]:
            return sorted(진짜_glob(pattern), reverse=True)

    monkeypatch.setattr(tool, "glob", _역순으로_돌려주는_glob)

    찾은것 = tool._storage_paths(str(tmp_path))

    assert 찾은것.index(이른것) < 찾은것.index(늦은것), (
        f"glob 이 역순으로 줘도 이름순으로 정리해야 한다: {찾은것}"
    )


def test_없는_저장본은_거른다(tmp_path: Path) -> None:
    """파일이 없으면 목록에 넣지 않는다 (sqlite3 가 나중에 죽지 않게)."""
    (tmp_path / "app" / "data").mkdir(parents=True)  # storage.db 는 «안» 만든다
    평가 = _만든다(tmp_path, "app/.local_evaluation_runs/20260824_bbb/storage.db")

    찾은것 = _load_tool()._storage_paths(str(tmp_path))

    assert 찾은것 == [평가], f"있는 것만 나와야 한다: {찾은것}"


def test_이름만_같은_폴더는_저장본이_아니다(tmp_path: Path) -> None:
    """`storage.db` 라는 «폴더»를 파일로 착각하면 안 된다.

    ★ 왜 이 시험이 생겼나 (적대 검수 D3)
      원래 마지막 단언이 `all(os.path.exists(...))` 였는데, 코드가 방금 한
      검사를 그대로 되풀이하는 «항등식»이라 어떤 변경에도 빨간불이 안 됐다.
      `exists` 는 폴더도 통과시키고, 그 뒤 sqlite3 가 OperationalError 로 죽는다.
      그래서 제품을 `isfile` 로 바꾸고, 이 시험이 그것을 지킨다.
    """
    진짜 = _만든다(tmp_path, "app/.local_evaluation_runs/20260818_aaa/storage.db")
    (tmp_path / "app" / ".local_evaluation_runs" / "20260826_zzz" / "storage.db").mkdir(
        parents=True
    )  # 파일이 아니라 «폴더»

    찾은것 = _load_tool()._storage_paths(str(tmp_path))

    assert 찾은것 == [진짜], f"폴더는 빠져야 한다: {찾은것}"
