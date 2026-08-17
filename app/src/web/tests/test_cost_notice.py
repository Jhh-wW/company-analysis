"""첫 화면에서 삭제한 비용 안내가 다시 나타나지 않는지 확인한다.

실측값과 운영 추정 범위의 자체 검증은 유지하지만, 그 값은 더 이상 첫 화면에
표시하지 않는다.
"""

from __future__ import annotations

import re

import pytest
from fastapi.testclient import TestClient

from src.core.constants import (
    REAL_COST_KRW_RANGE,
    REAL_COST_MEASURED_KRW,
    REMOVED_INPUT_COPY_MARKERS,
)
from src.features.pipeline.demo import DemoPipeline
from src.web import main
from src.web.tests._visible_text import visible_text


class _가짜진짜알맹이:
    """`DemoPipeline`이 아니기만 하면 된다 — `is_real` 판정이 그것으로 갈린다."""


@pytest.fixture
def client() -> TestClient:
    return TestClient(main.app)


def _진짜모드_첫화면(client: TestClient, monkeypatch) -> str:
    monkeypatch.setattr(main, "_PIPELINE", _가짜진짜알맹이())
    response = client.get("/")
    assert response.status_code == 200
    return response.text


# ══════════════════════════════════════════════════════════
# ① 삭제한 비용 안내가 화면에 없다
# ══════════════════════════════════════════════════════════


def test_삭제한_비용_안내가_진짜_모드에도_없다(client: TestClient, monkeypatch):
    최소, 최대 = REAL_COST_KRW_RANGE

    shown = visible_text(_진짜모드_첫화면(client, monkeypatch))

    for removed in REMOVED_INPUT_COPY_MARKERS:
        assert removed not in shown
    assert f"{최소}~{최대}원" not in shown


# ══════════════════════════════════════════════════════════
# ② 상수 자체의 모양 (실수 방지)
# ══════════════════════════════════════════════════════════


def test_금액_범위가_말이_된다():
    """최소 > 최대이거나 0원이면 운영 추정치로 쓸 수 없다."""
    최소, 최대 = REAL_COST_KRW_RANGE

    assert 0 < 최소 <= 최대


def test_실측이_안내_범위_안에_들어온다():
    """★ 안내가 «약속»이 되려면 재 본 것부터 그 안에 있어야 한다.

    실측값을 새로 넣었는데 범위를 안 고치면 여기가 깨진다.
    ⚠️ 깨졌을 때 **범위부터 넓히지 말 것** — 왜 벗어났는지가 먼저다
    (모델이 바뀌었나, 자료를 더 모으게 됐나).
    """
    최소, 최대 = REAL_COST_KRW_RANGE

    벗어난_것 = [c for c in REAL_COST_MEASURED_KRW if not 최소 <= c <= 최대]

    assert not 벗어난_것, (
        f"실측 {벗어난_것}원이 안내 범위 {최소}~{최대}원 밖입니다 — "
        "범위를 넓히기 전에 왜 벗어났는지 확인하세요"
    )


def test_안내_범위가_실측_양끝에서_물러서_있다():
    """★ 이번 검증에서 지적된 것 — 표본 3개로 만든 범위는 «네 번째»에서 깨진다.

    실측 최소가 82원인데 안내를 「82원부터」로 하면, 조금만 싼 회사가 나와도
    안내가 틀린 말이 된다. 반대로 최대 쪽이 빠듯하면 **사용자가 예상보다 많이 낸다** —
    이쪽이 더 나쁘다. 양끝에서 **최소 20%**는 물러서 있어야 한다.
    """
    최소, 최대 = REAL_COST_KRW_RANGE
    잰것최소, 잰것최대 = min(REAL_COST_MEASURED_KRW), max(REAL_COST_MEASURED_KRW)
    여유_최소 = 0.20

    assert 최소 <= 잰것최소 * (1 - 여유_최소), (
        f"안내 하한 {최소}원이 실측 최소 {잰것최소}원에 너무 붙어 있습니다"
    )
    assert 최대 >= 잰것최대 * (1 + 여유_최소), (
        f"안내 상한 {최대}원이 실측 최대 {잰것최대}원에 너무 붙어 있습니다 — "
        "사용자가 예상보다 많이 내게 됩니다"
    )


def test_실제로_잰_금액은_화면에_안_보인다(client: TestClient, monkeypatch):
    """실측 상수는 운영 검증에 남기되 화면에는 내보내지 않는다."""
    shown = visible_text(_진짜모드_첫화면(client, monkeypatch))

    for 금액 in REAL_COST_MEASURED_KRW:
        assert f"{금액}원" not in shown, (
            f"실측 {금액}원이 화면에 아직 있습니다 — 사용자가 빼라고 한 항목입니다"
        )


# ══════════════════════════════════════════════════════════
# ③ 데모 모드에도 삭제한 비용 안내가 없다
# ══════════════════════════════════════════════════════════


def test_데모_모드에는_금액_안내가_없다(client: TestClient, monkeypatch):
    """★ 공짜인데 돈 얘기를 하면 겁먹고 안 눌러본다 — P-79의 반대 방향."""
    monkeypatch.setattr(main, "_PIPELINE", DemoPipeline())

    shown = visible_text(client.get("/").text)

    for removed in REMOVED_INPUT_COPY_MARKERS:
        assert removed not in shown


# ══════════════════════════════════════════════════════════
# ④ 템플릿에 금액 범위가 다시 박히지 않았는지
# ══════════════════════════════════════════════════════════


def test_금액을_박아둔_템플릿이_없다():
    """삭제한 「숫자~숫자원」 안내가 다른 템플릿으로 돌아오는 것도 잡는다."""
    from src.core import paths

    금액패턴 = re.compile(r"\d{2,}\s*~\s*\d{2,}\s*원")
    templates = paths.PROJECT_ROOT / "app" / "src" / "web" / "templates"
    if not templates.exists():                       # 배포 형태가 달라도 안 깨지게
        pytest.skip("템플릿 폴더를 못 찾았습니다")

    범인 = [
        f"{path.name}: {m.group()}"
        for path in templates.glob("*.html")
        for m in 금액패턴.finditer(path.read_text(encoding="utf-8"))
    ]

    assert not 범인, (
        "삭제한 금액 안내가 템플릿에 다시 들어왔습니다: "
        + " / ".join(범인)
    )
