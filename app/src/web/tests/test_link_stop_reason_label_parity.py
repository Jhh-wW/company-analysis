"""admin·dashboard 두 화면의 LINK 중단사유 라벨 사본이 같은 키 집합을 갖는지 못 박는다.

``routers/admin.py``(``admin_link``)와 ``routers/dashboard.py``(``admin_link_detail``)는
같은 ``share_link_run_history.stop_reason`` 값을 각자 독립된 딕셔너리 리터럴로
사람이 읽을 라벨로 바꾼다. 두 사본은 소스 코드 수준에서 서로를 모르므로,
한쪽에 새 stop_reason을 추가하고 다른 쪽을 빠뜨리면 그 화면만 조용히
"확인 필요"/원문 코드로 표시된다 — 시험 없이는 아무도 못 알아챈다.
"""

from __future__ import annotations

import inspect
import re

from src.web.routers import admin as admin_router
from src.web.routers import dashboard as dashboard_router

_DICT_KEY_RE = re.compile(r'"([a-z0-9_]+)"\s*:\s*"')


def _extract_dict_keys(source: str, *, variable_name: str) -> set[str]:
    """소스 텍스트에서 ``변수명={ ... }`` 리터럴의 키 집합만 뽑아낸다."""

    marker = f"{variable_name}={{"
    start = source.index(marker) + len(marker)
    depth = 1
    end = start
    while depth > 0:
        if source[end] == "{":
            depth += 1
        elif source[end] == "}":
            depth -= 1
        end += 1
    body = source[start : end - 1]
    keys = set(_DICT_KEY_RE.findall(body))
    assert keys, f"{variable_name} 딕셔너리에서 키를 하나도 못 찾았습니다 — 추출 정규식을 다시 확인하세요"
    return keys


def test_stop_reason_라벨_사본은_같은_키를_가진다() -> None:
    admin_source = inspect.getsource(admin_router)
    dashboard_source = inspect.getsource(dashboard_router)

    admin_keys = _extract_dict_keys(admin_source, variable_name="run_stop_reason_labels")
    dashboard_keys = _extract_dict_keys(
        dashboard_source, variable_name="dashboard_run_stop_reason_labels"
    )

    missing_from_dashboard = admin_keys - dashboard_keys
    missing_from_admin = dashboard_keys - admin_keys
    assert not missing_from_dashboard, (
        f"admin.py에만 있고 dashboard.py 사본에 없는 stop_reason: {sorted(missing_from_dashboard)}"
    )
    assert not missing_from_admin, (
        f"dashboard.py에만 있고 admin.py 사본에 없는 stop_reason: {sorted(missing_from_admin)}"
    )


def test_새로_추가한_두_stop_reason은_두_사본_모두_같은_한국어_라벨이다() -> None:
    admin_source = inspect.getsource(admin_router)
    dashboard_source = inspect.getsource(dashboard_router)

    for code, expected_label in (
        ("server_restart_delivery_incomplete", "서버 재시작으로 자동출고 확인 전 중단됨"),
        ("admin_manual_settled", "관리자가 수동으로 대사해 중단됨"),
    ):
        needle = f'"{code}": "{expected_label}"'
        assert needle in admin_source, f"admin.py에 {needle!r}가 없습니다"
        assert needle in dashboard_source, f"dashboard.py에 {needle!r}가 없습니다"
