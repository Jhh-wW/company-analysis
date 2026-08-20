"""연결 열기·표 만들기 시험."""

from __future__ import annotations

from pathlib import Path

from src.features.export_notion import store as notion_store
from src.features.storage import constants, db


def test_connect_creates_missing_parent_dir_and_file(tmp_path: Path) -> None:
    """DB 파일이 없어도, 폴더가 없어도 처음 연결하면 만들어진다."""
    target = tmp_path / "nested" / "storage.db"
    assert not target.exists()

    with db.connect(target) as conn:
        tables = {
            row["name"]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }

    assert target.exists()
    assert {
        constants.TABLE_REPORTS,
        constants.TABLE_LAYER1_CACHE,
        constants.TABLE_LAYER2_CACHE,
        constants.TABLE_ALIAS_CACHE,
        constants.TABLE_SESSIONS,
        notion_store.TABLE_NOTION_EXPORTS,
    } <= tables


def test_connect_reopen_keeps_data(tmp_path: Path) -> None:
    """서버를 껐다 켜는 것을 흉내 — 연결을 닫고 다시 열어도 표는 그대로다."""
    target = tmp_path / "storage.db"

    with db.connect(target) as conn:
        conn.execute(
            f"INSERT INTO {constants.TABLE_ALIAS_CACHE} (alias_key, corp_id, created_at) "
            "VALUES ('a', 'CORP1', '2026-08-15T00:00:00')"
        )

    with db.connect(target) as conn:
        row = conn.execute(
            f"SELECT corp_id FROM {constants.TABLE_ALIAS_CACHE} WHERE alias_key = 'a'"
        ).fetchone()

    assert row is not None
    assert row["corp_id"] == "CORP1"


def test_connect_twice_is_idempotent(tmp_path: Path) -> None:
    """표를 두 번 만들어도(멱등) 에러가 나지 않는다."""
    target = tmp_path / "storage.db"
    with db.connect(target):
        pass
    with db.connect(target):
        pass  # 두 번째도 예외 없이 지나가야 한다


def test_default_db_path_is_under_app_data_dir(monkeypatch) -> None:
    # ★ 환경변수를 «걷어내고» 진짜 기본값을 본다.
    #   (시험 전체가 임시 DB를 쓰도록 conftest.py가 이 변수를 걸어 둔다)
    monkeypatch.delenv(constants.ENV_DB_PATH, raising=False)
    path = db.default_db_path()
    assert path.name == "storage.db"
    assert path.parent.name == "data"
