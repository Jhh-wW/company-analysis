"""초대 링크가 닫혀 조사가 멈춘 사실이 «사람 말»로 보이는지 못 박는다.

앞부분은 admin·dashboard 두 화면의 중단사유 라벨 사본이 같은 키 집합을 갖는지,
뒷부분은 손님이 보는 중단 화면의 큰 제목이 링크 중단에도 맞는 문구인지 본다.

``routers/admin.py``(``admin_link``)와 ``routers/dashboard.py``(``admin_link_detail``)는
같은 ``share_link_run_history.stop_reason`` 값을 각자 독립된 딕셔너리 리터럴로
사람이 읽을 라벨로 바꾼다. 두 사본은 소스 코드 수준에서 서로를 모르므로,
한쪽에 새 stop_reason을 추가하고 다른 쪽을 빠뜨리면 그 화면만 조용히
"확인 필요"/원문 코드로 표시된다 — 시험 없이는 아무도 못 알아챈다.
"""

from __future__ import annotations

import inspect
import re

import uuid

from fastapi.testclient import TestClient

from src.features.pipeline.port import CompanyCard, Outcome, RunResult, UserInput
from src.web import job_runtime, main
from src.web.routers import admin as admin_router
from src.web.routers import dashboard as dashboard_router
from src.web.tests.report_route_support import bind_public_report_access

_DICT_KEY_RE = re.compile(r'"([a-z0-9_]+)"\s*:\s*"')
_DICT_ENTRY_RE = re.compile(r'"([a-z0-9_]+)"\s*:\s*"([^"]*)"')


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


def _멈춘_조사(message: str) -> tuple[str, job_runtime.Job]:
    """생성 갈래에서 멈춘 조사 하나. 사유 문구만 갈아 끼운다.

    ★ 링크가 닫혀 멈추면 `job_runtime`이 `Outcome.FAILED`로 끝내고 안내 문구만
      링크용으로 바꾼다. 그래서 화면은 «진짜 기술 오류»와 같은 갈래를
      탄다 — 큰 제목이 「오류가 났습니다」면 링크를 닫은 경우에 거짓말이 된다.
    """

    job_id = uuid.uuid4().hex
    job = job_runtime.Job(
        job_id=job_id,
        user_input=UserInput(company="우리엔", job="", region="서울 중구"),
        card=CompanyCard(
            legal_name="주식회사 우리엔",
            typed_name="우리엔",
            address="서울특별시 중구",
            ceo="",
            founded="",
        ),
        finished=True,
        result=RunResult(outcome=Outcome.FAILED, message=message),
    )
    return job_id, job


def _중단화면(message: str):
    job_id, job = _멈춘_조사(message)
    job_runtime._JOBS[job_id] = job
    try:
        with TestClient(main.app, base_url="https://testserver") as client:
            bind_public_report_access(client, job_id)
            return client.get(f"/result/{job_id}")
    finally:
        job_runtime._JOBS.pop(job_id, None)


def test_링크가_닫혀_멈춘_화면은_오류라고_부르지_않는다() -> None:
    화면 = _중단화면("이 링크의 사용이 중단되어 조사를 멈췄습니다.")

    assert 화면.status_code == 200
    assert "조사를 멈췄습니다" in 화면.text
    assert "오류가 났습니다" not in 화면.text
    assert "이 링크의 사용이 중단되어 조사를 멈췄습니다." in 화면.text


def test_진짜_기술오류에서도_무엇이_있었는지는_그대로_말한다() -> None:
    """★ 중립 제목으로 바꾸면서 «진짜 오류»를 숨기면 안 된다."""

    화면 = _중단화면("보고서를 만들다 오류가 났습니다. 잠시 후 다시 시도해주세요.")

    assert 화면.status_code == 200
    assert "조사를 멈췄습니다" in 화면.text
    assert "보고서를 만들다 오류가 났습니다" in 화면.text


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


def test_링크_중단_사유코드_세_개의_라벨이_두_사본에_같다() -> None:
    """조사 도중 초대 링크가 닫혀 멈춘 갈래 3개의 라벨을 못 박는다.

    ★ 이 세 코드는 `job_runtime`이 실행 이력에 적는 값이다. 라벨이 없으면
      관리자 화면이 원문 코드나 「확인 필요」로 보여 무슨 일이 있었는지 모른다.
    ★ 라벨에 내부 용어를 쓰지 않는다 — 「revoked」·「LINK」로는 읽는 사람이
      무엇이 멈췄는지 알 수 없다.
    """

    admin_source = inspect.getsource(admin_router)
    dashboard_source = inspect.getsource(dashboard_router)

    for code, expected_label in (
        ("link_revoked", "초대 링크의 사용이 중단됨"),
        ("link_expired", "초대 링크의 기간이 지남"),
        ("link_state_unknown", "초대 링크 상태를 확인하지 못함"),
    ):
        needle = f'"{code}": "{expected_label}"'
        assert needle in admin_source, f"admin.py에 {needle!r}가 없습니다"
        assert needle in dashboard_source, f"dashboard.py에 {needle!r}가 없습니다"


def test_링크_중단_라벨에는_내부_용어가_없다() -> None:
    """사람이 읽는 라벨이라 내부 자료 이름이 들어가면 안 된다."""

    admin_source = inspect.getsource(admin_router)
    라벨 = {
        code: label
        for code, label in _DICT_ENTRY_RE.findall(
            admin_source[admin_source.index("run_stop_reason_labels={") :]
        )
        if code.startswith("link_")
    }
    assert set(라벨) == {"link_revoked", "link_expired", "link_state_unknown"}
    for code, label in 라벨.items():
        for 용어 in ("LINK", "MEMBER", "revoke", "hash", "capability"):
            assert 용어 not in label, f"{code} 라벨에 내부 용어 {용어!r}가 있습니다"


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
