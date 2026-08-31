"""``/healthz`` 가 «배포된 커밋»을 알려 주는지 지킨다.

★ 왜 이 파일이 생겼나 (2026-08-26)
  ─────────────────────────────────────────────────────────
  배포된 것이 «어느 커밋인지»를 밖에서 알 방법이 전혀 없었다. /healthz 는
  {"status":"ok"} 만 주고 응답 헤더에도 단서가 없다(rndr-id 는 요청마다 바뀐다).
  그래서 「Manual Deploy 를 눌렀는가」를 확인하려면 매번 사람이 Render 대시보드를
  열어야 했고, 세션을 넘길 때마다 「배포됐는지 모른다」를 인수인계에 적어야 했다.

★ 이 시험이 지키는 것
  ─────────────────────────────────────────────────────────
  ① 키가 «있다» — 키가 보이는 것 자체가 「새 코드가 돌고 있다」는 증거다
  ② 값이 걸러진다 — 이 경로는 로그인 없이 열리므로 환경변수를 그대로 흘리면 안 된다
  ③ liveness 를 망치지 않는다 — 커밋을 몰라도 status 는 ok 여야 한다
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from src.features.pipeline import engine_mode
from src.shared import engine_build_identity
from src.web import main
from src.web.routers import health


@pytest.fixture(name="client")
def _client() -> TestClient:
    return TestClient(main.app)


def _clear_commit_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in health._COMMIT_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)


def test_healthz가_배포된_커밋을_함께_알려_준다(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """★ 이 시험이 빨간불이면 배포 확인이 다시 «사람이 대시보드 보기»로 돌아간다."""
    _clear_commit_env(monkeypatch)
    monkeypatch.setenv("RENDER_GIT_COMMIT", "8541a53fedcba9876543210fedcba98765432100")

    payload = client.get("/healthz").json()

    assert payload["status"] == "ok"
    assert payload["commit"] == "8541a53", (
        "배포된 커밋이 짧은 형태로 나와야 합니다 — 사람이 대시보드·git log 와 "
        "눈으로 맞추는 값입니다"
    )


def test_render가_값을_안_주면_우리가_넣은_이름을_쓴다(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """이 서비스는 runtime 이 docker 라 RENDER_GIT_COMMIT 주입을 장담할 수 없다.

    그때 render.yaml 에서 APP_GIT_COMMIT 으로 직접 넣을 수 있어야 한다.
    """
    _clear_commit_env(monkeypatch)
    monkeypatch.setenv("APP_GIT_COMMIT", "abc1234" + "0" * 33)

    assert client.get("/healthz").json()["commit"] == "abc1234"


def test_healthz는_process가_실제로_동결한_commit만_표시한다(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """표시만 raw B로 바뀌어 실제 생성 epoch A와 갈라지는 거짓 상태를 막는다."""

    _clear_commit_env(monkeypatch)
    commit_a = "a" * 40
    commit_b = "b" * 40
    monkeypatch.setenv("RENDER_GIT_COMMIT", commit_a)
    frozen = engine_build_identity.process_engine_build_identity()

    monkeypatch.setenv("RENDER_GIT_COMMIT", commit_b)
    payload = client.get("/healthz").json()

    assert frozen.deployment_revision == commit_a
    assert payload["commit"] == commit_a[: health._COMMIT_SHORT_LEN]
    assert engine_build_identity.process_engine_build_identity() is frozen


def test_healthz는_process가_실제로_동결한_engine_mode만_표시한다(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(engine_mode.ENGINE_V2_ENV_NAME, engine_mode.ENGINE_V2_ENV_ON)
    frozen = engine_mode.process_engine_mode()

    monkeypatch.setenv(engine_mode.ENGINE_V2_ENV_NAME, "0")
    payload = client.get("/healthz").json()

    assert frozen is engine_mode.EngineMode.V2
    assert payload["engine_mode"] == "v2"
    assert engine_mode.process_engine_mode() is frozen


def test_커밋을_몰라도_상태는_ok다(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """커밋 표시는 «확인용»이지 liveness 판정이 아니다.

    여기서 503이 되면 Render 가 살아 있는 서버를 재시작 루프로 죽인다.
    """
    _clear_commit_env(monkeypatch)
    monkeypatch.delenv(engine_mode.ENGINE_V2_ENV_NAME, raising=False)

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "commit": "unknown",
        "engine_mode": "v1",
    }, (
        "커밋을 모를 때 키를 «빼면» 「옛 코드가 돈다」와 「환경변수가 안 들어왔다」를 "
        "구분할 수 없습니다"
    )


@pytest.mark.parametrize(
    "polluted",
    [
        "../../etc/passwd",
        "8541a53; rm -rf /",
        "<script>alert(1)</script>",
        "커밋",
        "  ",
        "zzzzzzz",
        "ABCDEF0123456789ABCDEF0123456789ABCDEF01",
        " 8541a53fedcba9876543210fedcba98765432100",
        "8541a53fedcba9876543210fedcba98765432100 ",
    ],
)
def test_환경변수가_오염돼도_그대로_흘리지_않는다(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, polluted: str
) -> None:
    """★ 이 경로는 «로그인 없이» 열린다 — 환경변수를 그대로 내보내면 안 된다."""
    _clear_commit_env(monkeypatch)
    monkeypatch.setenv("RENDER_GIT_COMMIT", polluted)

    commit = client.get("/healthz").json()["commit"]

    assert commit == "unknown", f"16진수가 아닌 값이 새어 나갔습니다: {commit!r}"
