"""배포 신원을 health와 생성기 캐시가 같은 규칙으로 읽는지 지킨다."""

from __future__ import annotations

from src.core import deployment_identity


def test_첫번째_정상_커밋을_전체_검사한뒤_돌려준다(monkeypatch) -> None:
    monkeypatch.setenv(
        "RENDER_GIT_COMMIT", "ABCDEF0123456789ABCDEF0123456789ABCDEF01"
    )
    monkeypatch.setenv("APP_GIT_COMMIT", "1111111")

    assert deployment_identity.deployed_commit() == (
        "abcdef0123456789abcdef0123456789abcdef01"
    )
    assert deployment_identity.short_deployed_commit() == "abcdef0"


def test_오염값과_너무긴값은_건너뛴다(monkeypatch) -> None:
    monkeypatch.setenv("RENDER_GIT_COMMIT", "abcdef0;polluted")
    monkeypatch.setenv("APP_GIT_COMMIT", "f" * 41)

    assert deployment_identity.deployed_commit() == ""
    assert deployment_identity.short_deployed_commit() == "unknown"
